"""Пять тестов pathfinding. Запуск: pytest -q tests/test_pathfinding.py."""
import math
import random
import time

from games.rts.sim.entity import Entity
from games.rts.sim.flowfield import FlowField, FlowFieldPool
from games.rts.sim.grid import Grid
from games.rts.sim.order import MoveOrder
from games.rts.sim.pathfinder import PathCache, Pathfinder
from games.rts.sim.world import World


def _grid(w, h, blocked=()):
    tiles = [0] * (w * h)
    for x, y in blocked:
        tiles[y * w + x] = 1  # water
    return Grid(w, h, tiles, tile_size=32)


def _rules():
    return {"tick_rate": 10}


def _units():
    return {"soldier": {"hp": 100, "speed": 3.0, "vision": 8,
                        "cost": {"gold": 0, "wood": 0, "food": 1}}}


def _world(grid):
    w = World(_rules(), _units())
    w.resources[0] = {"gold": 0, "wood": 0}
    return w


def test_astar_detours_around_water():
    blocked = [(32, y) for y in range(10, 51)]
    grid = _grid(64, 64, blocked)
    pf = Pathfinder(grid)

    start = (10.5, 30.5)
    end = (54.5, 30.5)
    path = pf.find_path(start, end, "ground")

    assert path is not None, "path not found"
    assert len(path) > 3, "path too short — likely straight through wall"

    for i, wp in enumerate(path):
        tx, ty = int(math.floor(wp[0])), int(math.floor(wp[1]))
        is_last = (i == len(path) - 1)
        if is_last and not grid.is_passable(tx, ty, "ground"):
            continue
        assert grid.is_passable(tx, ty, "ground"), \
            "waypoint #%d at blocked tile (%d, %d)" % (i, tx, ty)


def test_astar_100_pairs_under_100ms():
    blocked = [(32, y) for y in range(10, 51)]
    blocked += [(x, 32) for x in range(10, 51)]
    grid = _grid(64, 64, blocked)
    pf = Pathfinder(grid)

    rng = random.Random(12345)
    pairs = []
    while len(pairs) < 100:
        sx, sy = rng.randint(1, 62), rng.randint(1, 62)
        ex, ey = rng.randint(1, 62), rng.randint(1, 62)
        if not grid.is_passable(sx, sy, "ground"):
            continue
        if not grid.is_passable(ex, ey, "ground"):
            continue
        pairs.append(((sx + 0.5, sy + 0.5), (ex + 0.5, ey + 0.5)))

    t0 = time.perf_counter()
    found = 0
    for s, e in pairs:
        p = pf.find_path(s, e, "ground")
        if p:
            found += 1
    dt_ms = (time.perf_counter() - t0) * 1000.0

    assert found >= 95, "only %d/100 paths found" % found
    assert dt_ms < 100.0, "100 A* took %.1f ms (budget 100)" % dt_ms


def test_flow_field_50_units_converge():
    blocked = [(32, y) for y in range(10, 51)]
    grid = _grid(64, 64, blocked)
    pf = Pathfinder(grid)
    ff_pool = FlowFieldPool(grid)

    world = _world(grid)
    target = (50.5, 30.5)
    for i in range(50):
        x = 8.5 + (i % 10) * 0.7
        y = 25.0 + (i // 10) * 0.7
        e = Entity(None, "soldier", 0, (x, y), hp=100,
                   speed=3.0, vision=8, radius=0.4)
        world.spawn(e)
        e.set_order(MoveOrder(target, pathfinder=pf, flowfield_pool=ff_pool))

    for _ in range(1000):
        world.step()

    arrived = 0
    for e in world.entities.values():
        dx = e.pos[0] - target[0]
        dy = e.pos[1] - target[1]
        if dx * dx + dy * dy < 4.0:
            arrived += 1
    assert arrived >= 45, "only %d/50 converged" % arrived


def test_units_dont_clip_through_walls():
    blocked = [(16, y) for y in range(5, 27)]
    grid = _grid(32, 32, blocked)
    pf = Pathfinder(grid)
    ff_pool = FlowFieldPool(grid)

    world = _world(grid)
    for i in range(6):
        e = Entity(None, "soldier", 0, (5.5, 14.5 + i * 0.5), hp=100,
                   speed=3.0, vision=8, radius=0.4)
        world.spawn(e)
        e.set_order(MoveOrder((25.5, 16.0), pathfinder=pf, flowfield_pool=ff_pool))

    for tick in range(500):
        world.step()
        for e in world.entities.values():
            tx = int(math.floor(e.pos[0]))
            ty = int(math.floor(e.pos[1]))
            assert grid.is_passable(tx, ty, "ground"), \
                "tick %d: unit %s at blocked tile (%d, %d) pos=%s" % (
                    tick, e.id, tx, ty, e.pos)


def test_path_cache_lru_eviction():
    c = PathCache(maxsize=3)
    c.put("a", 1)
    c.put("b", 2)
    c.put("c", 3)
    assert len(c) == 3
    assert c.get("a") == 1
    c.put("d", 4)
    assert len(c) == 3
    assert c.get("b") is None
    assert c.get("a") == 1
    assert c.get("c") == 3
    assert c.get("d") == 4
    c.clear()
    assert len(c) == 0
