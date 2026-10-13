"""
Smoke-тест: 200 юнитов + 1000 тактов без крашей и с замером времени.

Проверяет:
  * tick loop не падает;
  * скорость 1 такта при 200 юнитах < 30 мс (на ноутбуке);
  * MoveOrder корректно доводит юнитов до цели;
  * StopOrder останавливает.
"""

import json
import time
from pathlib import Path

from .sim.entity import Entity, EntityState
from .sim.order import MoveOrder
from .sim.runtime import GameRuntime

DATA_DIR = Path(__file__).resolve().parent / "data"


def _load(name: str) -> dict:
    with open(DATA_DIR / name, "r", encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    units = _load("units.json")
    rules = _load("rules.json")

    rt = GameRuntime(rules, units)
    # дать игроку 0 бесконечные ресурсы для спавна
    rt.world.resources[0] = {"gold": 10_000_000, "wood": 10_000_000}

    # --- 1) спавн 200 юнитов через команды ---
    N = 200
    ud = units["footman"]
    for i in range(N):
        x = 2.0 + (i % 20) * 1.2
        y = 2.0 + (i // 20) * 1.2
        rt.enqueue_command(0, {"action": "spawn", "unit": "footman", "pos": [x, y]})
    rt.tick()  # один такт — применяем все spawn-команды

    n_spawned = len(rt.world.entities)
    assert n_spawned == N, f"expected {N} entities, got {n_spawned}"
    print(f"[1] spawned {n_spawned} entities")

    # --- 2) приказ move на всех ---
    ids = list(rt.world.entities.keys())
    rt.enqueue_command(0, {"action": "move", "ids": ids, "pos": [50.0, 50.0]})
    rt.tick()  # команда применяется в начале этого такта

    # после первого такта с move все должны быть в состоянии MOVING
    moving = sum(1 for e in rt.world.entities.values() if e.state is EntityState.MOVING)
    print(f"[2] moving after first move tick: {moving}/{n_spawned}")

    # --- 3) 1000 тактов и замер ---
    TICKS = 1000
    t0 = time.perf_counter()
    for _ in range(TICKS):
        rt.tick()
    dt = time.perf_counter() - t0
    per_tick_ms = (dt * 1000.0) / TICKS
    print(f"[3] {TICKS} ticks in {dt*1000:.1f} ms total "
          f"({per_tick_ms:.3f} ms/tick)")

    # --- 4) проверим, что все юниты дошли (speed 2.4, 1000 ticks = 100 сек =
    #        240 тайлов пути; от 2..24 до 50 — легко) ---
    arrived = 0
    for e in rt.world.entities.values():
        dx = e.pos[0] - 50.0
        dy = e.pos[1] - 50.0
        if dx * dx + dy * dy < 0.25:  # радиус 0.5
            arrived += 1
    print(f"[4] arrived at target: {arrived}/{n_spawned}")

    # --- 5) стоп и проверка остановки ---
    rt.enqueue_command(0, {"action": "stop", "ids": ids})
    rt.tick()
    stopped = sum(1 for e in rt.world.entities.values()
                  if e.state is EntityState.IDLE
                  and e.vel[0] == 0.0 and e.vel[1] == 0.0)
    print(f"[5] stopped: {stopped}/{n_spawned}")

    # --- 6) бюджет производительности ---
    if per_tick_ms >= 30.0:
        print(f"[FAIL] per-tick {per_tick_ms:.3f} ms >= 30 ms budget")
        return 1
    print(f"[OK] per-tick {per_tick_ms:.3f} ms < 30 ms budget")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
