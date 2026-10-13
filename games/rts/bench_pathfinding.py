"""Бенчмарк: 200 юнитов одной цели. Python 3.6."""
import json
import time
from pathlib import Path

from games.rts.sim.entity import Entity
from games.rts.sim.flowfield import FlowFieldPool
from games.rts.sim.grid import Grid
from games.rts.sim.order import MoveOrder
from games.rts.sim.pathfinder import Pathfinder
from games.rts.sim.world import World

DATA_DIR = Path(__file__).resolve().parent / "data"


def main():
    # type: () -> int
    with open(str(DATA_DIR / "maps" / "example_map.json"), "r", encoding="utf-8") as f:
        map_data = json.load(f)
    grid = Grid.from_map(map_data)
    pf = Pathfinder(grid)
    ff_pool = FlowFieldPool(grid)

    rules = {"tick_rate": 10}
    units_data = {"footman": {"hp": 420, "speed": 2.4, "vision": 8,
                              "cost": {"gold": 100, "wood": 0, "food": 2}}}
    world = World(rules, units_data)
    world.resources[0] = {"gold": 0, "wood": 0}

    N = 200
    for i in range(N):
        x = 8.0 + (i % 20) * 0.6
        y = 8.0 + (i // 20) * 0.6
        e = Entity(None, "footman", 0, (x, y), hp=420,
                   speed=2.4, vision=8, radius=0.4)
        world.spawn(e)

    target = (54.5, 54.5)
    for e in world.entities.values():
        e.set_order(MoveOrder(target, pathfinder=pf, flowfield_pool=ff_pool))

    for _ in range(5):
        world.step()

    TICKS = 500
    times = []
    t_total = time.perf_counter()
    for _ in range(TICKS):
        t0 = time.perf_counter()
        world.step()
        times.append((time.perf_counter() - t0) * 1000.0)
    total_ms = (time.perf_counter() - t_total) * 1000.0

    times.sort()
    p50 = times[len(times) // 2]
    p95 = times[int(len(times) * 0.95)]
    p99 = times[int(len(times) * 0.99)]
    worst = times[-1]
    avg = total_ms / TICKS

    print("Units: %d, ticks: %d" % (N, TICKS))
    print("  avg:   %.3f ms/tick" % avg)
    print("  p50:   %.3f ms" % p50)
    print("  p95:   %.3f ms" % p95)
    print("  p99:   %.3f ms" % p99)
    print("  worst: %.3f ms" % worst)
    print("  budget: 30.000 ms/tick")

    arrived = 0
    for e in world.entities.values():
        dx = e.pos[0] - target[0]
        dy = e.pos[1] - target[1]
        if dx * dx + dy * dy < 4.0:
            arrived += 1
    print("  arrived: %d/%d" % (arrived, N))

    ok = avg < 30.0 and p99 < 30.0
    print("  RESULT: %s" % ("OK" if ok else "OVER BUDGET"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
