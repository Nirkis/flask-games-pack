"""Тесты боевой системы. Запуск: pytest -q tests/test_combat.py."""
import math

from games.rts.sim import combat
from games.rts.sim.entity import Entity
from games.rts.sim.grid import Grid
from games.rts.sim.order import (AttackOrder, AttackMoveOrder,
                                 HoldOrder, MoveOrder, PatrolOrder)
from games.rts.sim.pathfinder import Pathfinder
from games.rts.sim.world import World


def _rules():
    return {"tick_rate": 10, "crit_enabled": False}


def _world(w=20, h=20):
    grid = Grid(w, h, [0] * (w * h))
    wd = World(_rules(), {})
    wd.grid = grid
    wd.resources[0] = {"gold": 0, "wood": 0}
    wd.resources[1] = {"gold": 0, "wood": 0}
    return wd, Pathfinder(grid)


def _unit(world, owner, pos, **kw):
    params = {
        "hp": 100, "speed": 0.0, "vision": 8.0, "radius": 0.4,
        "attack": 0, "attack_speed": 1.0, "attack_type": "normal",
        "attack_range": 0.0, "armor": 0, "armor_type": "light",
    }
    params.update(kw)
    e = Entity(None, kw.get("type_id", "unit"), owner, pos, **params)
    world.spawn(e)
    return e


# ---------- 1. 12 dmg × 35 ударов = 420 HP ----------

def test_twelve_damage_kills_420hp_in_35_hits():
    world, pf = _world()
    atk = _unit(world, 0, (0.5, 0.5),
                attack=12, attack_speed=10.0, attack_range=1.5,
                armor=0, armor_type="medium")
    tgt = _unit(world, 1, (1.0, 0.5), hp=420,
                armor=0, armor_type="medium")
    atk.set_order(AttackOrder(tgt.id, pathfinder=pf))

    for _ in range(34):
        world.step()
    assert tgt.hp == 12, "expected 12 hp after 34 hits, got %d" % tgt.hp

    world.step()
    assert tgt.hp == 0, "expected dead after 35th hit, got %d" % tgt.hp
    assert not tgt.alive


# ---------- 2. pierce vs light ×2 ----------

def test_pierce_vs_light_multiplier():
    rules = _rules()
    assert combat.compute_damage(10, "pierce", 0, "light", rules) == 20.0
    assert combat.compute_damage(10, "pierce", 0, "heavy", rules) == 10.0
    assert combat.compute_damage(10, "pierce", 0, "fortified", rules) == 3.5
    assert combat.compute_damage(10, "siege", 0, "fortified", rules) == 15.0
    # броня снижает
    assert combat.compute_damage(10, "normal", 5, "medium", rules) == 7.0


# ---------- 3. снаряд догоняет движущуюся цель ----------

def test_projectile_hits_moving_target():
    world, pf = _world(30, 30)
    archer = _unit(world, 0, (1.0, 5.5),
                   attack=30, attack_speed=10.0, attack_range=6.0,
                   attack_type="normal", armor_type="light")
    runner = _unit(world, 1, (5.0, 5.5), hp=200, speed=2.0,
                   armor=0, armor_type="light")
    archer.set_order(AttackOrder(runner.id, pathfinder=pf))
    runner.set_order(MoveOrder((25.0, 5.5), pathfinder=pf))

    # первый тик должен породить снаряд
    world.step()
    assert len(world.projectiles) >= 1, "no projectile spawned"

    initial = runner.hp
    hit = False
    for _ in range(60):
        world.step()
        if runner.hp < initial:
            hit = True
            break
    assert hit, "projectile never damaged the moving target"
    # урон 30, множитель normal vs light = 1.0, armor 0
    assert runner.hp == initial - 30


# ---------- 4. AttackMove: атакует, потом идёт к точке ----------

def test_attack_move_attacks_then_continues():
    world, pf = _world(30, 30)
    runner = _unit(world, 0, (0.5, 5.5),
                   attack=30, attack_speed=10.0, attack_range=1.5,
                   speed=3.0, vision=8.0, armor_type="medium")
    dummy = _unit(world, 1, (5.5, 5.5), hp=50, speed=0.0,
                  vision=4.0, armor_type="medium")
    point = (15.0, 5.5)
    runner.set_order(AttackMoveOrder(point, pathfinder=pf))

    for _ in range(400):
        world.step()
        if not dummy.alive:
            break
    assert not dummy.alive, "dummy survived AttackMove"

    # продолжаем идти к точке
    for _ in range(400):
        world.step()
        if not runner.orders:
            break
    dx = runner.pos[0] - point[0]
    dy = runner.pos[1] - point[1]
    assert dx * dx + dy * dy < 1.0, "runner never reached point: %r" % (runner.pos,)


# ---------- 5. Hold: не двигается, бьёт только в радиусе ----------

def test_hold_stays_and_attacks_in_range_only():
    world, pf = _world(30, 30)
    holder = _unit(world, 0, (5.5, 5.5),
                   attack=20, attack_speed=10.0, attack_range=1.5,
                   speed=3.0, vision=8.0, armor_type="medium")
    enemy = _unit(world, 1, (15.5, 5.5), hp=100, speed=0.0,
                  armor_type="medium")
    holder.set_order(HoldOrder())
    start = list(holder.pos)

    for _ in range(30):
        world.step()
    assert holder.pos == start, "holder moved: %r -> %r" % (start, holder.pos)
    assert enemy.hp == 100, "enemy damaged outside range"

    # втолкнуть врага в радиус
    enemy.pos = [6.5, 5.5]
    for _ in range(5):
        world.step()
    assert enemy.hp < 100, "enemy not damaged inside range"


# ---------- 6. Patrol: юнит ходит туда-обратно ----------

def test_patrol_alternates_between_points():
    world, pf = _world(20, 20)
    runner = _unit(world, 0, (0.5, 0.5),
                   speed=3.0, vision=2.0, attack=0, attack_range=0.0)
    runner.set_order(PatrolOrder((0.5, 0.5), (6.5, 0.5), pathfinder=pf))

    reached_right = False
    reached_left_again = False
    for _ in range(500):
        world.step()
        if runner.pos[0] > 6.0:
            reached_right = True
        if reached_right and runner.pos[0] < 1.0:
            reached_left_again = True
            break
    assert reached_right, "patrol never reached p2"
    assert reached_left_again, "patrol never returned to p1"
