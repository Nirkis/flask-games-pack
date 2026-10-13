"""Separation + следование по waypoint/направлению."""
import math
from typing import List, Optional, Tuple

SEPARATION_WEIGHT = 0.6
ARRIVE_RADIUS = 0.4
WAYPOINT_ADVANCE_RADIUS = 0.6


class SpatialHash(object):
    __slots__ = ("cell_size", "cells")

    def __init__(self, cell_size=2.0):
        # type: (float) -> None
        self.cell_size = float(cell_size)
        self.cells = {}    # type: dict

    def insert(self, entity):
        cs = self.cell_size
        cx = int(math.floor(entity.pos[0] / cs))
        cy = int(math.floor(entity.pos[1] / cs))
        b = self.cells.get((cx, cy))
        if b is None:
            self.cells[(cx, cy)] = [entity]
        else:
            b.append(entity)

    def query(self, pos, radius):
        # type: (Tuple[float, float], float) -> List[object]
        cs = self.cell_size
        min_x = int(math.floor((pos[0] - radius) / cs))
        max_x = int(math.floor((pos[0] + radius) / cs))
        min_y = int(math.floor((pos[1] - radius) / cs))
        max_y = int(math.floor((pos[1] + radius) / cs))
        out = []
        cells = self.cells
        for cx in range(min_x, max_x + 1):
            for cy in range(min_y, max_y + 1):
                b = cells.get((cx, cy))
                if b:
                    out.extend(b)
        return out


def _get_world_hash(world):
    h = getattr(world, "_steer_hash", None)
    tick = getattr(world, "_steer_hash_tick", -1)
    if h is None or tick != world.tick_count:
        h = SpatialHash(cell_size=2.0)
        for e in world.entities.values():
            h.insert(e)
        world._steer_hash = h
        world._steer_hash_tick = world.tick_count
    return h


def compute_separation(entity, world):
    # type: (object, object) -> Tuple[float, float]
    h = _get_world_hash(world)
    r = 1.5 * entity.radius
    neighbors = h.query(entity.pos, r + 1.0)
    sx = 0.0
    sy = 0.0
    ex, ey = entity.pos
    er = entity.radius
    for other in neighbors:
        if other is entity or not other.alive:
            continue
        ox = ex - other.pos[0]
        oy = ey - other.pos[1]
        d2 = ox * ox + oy * oy
        min_d = 1.5 * (er + other.radius)
        if d2 <= 1e-9 or d2 >= min_d * min_d:
            continue
        d = math.sqrt(d2)
        strength = (min_d - d) / min_d
        sx += (ox / d) * strength
        sy += (oy / d) * strength
    mag = math.hypot(sx, sy)
    if mag > 1.0:
        sx /= mag
        sy /= mag
    return sx, sy


def _passable(grid, x, y, unit_type):
    if grid is None:
        return True
    return grid.is_passable(int(math.floor(x)), int(math.floor(y)), unit_type)


def _integrate(entity, world, vx, vy, dt, grid, unit_type):
    sep_x, sep_y = compute_separation(entity, world)
    vx += sep_x * SEPARATION_WEIGHT * entity.speed
    vy += sep_y * SEPARATION_WEIGHT * entity.speed

    v_mag = math.hypot(vx, vy)
    max_v = entity.speed
    if v_mag > max_v and v_mag > 1e-9:
        k = max_v / v_mag
        vx *= k
        vy *= k

    nx = entity.pos[0] + vx * dt
    ny = entity.pos[1] + vy * dt

    if grid is not None and not _passable(grid, nx, ny, unit_type):
        if _passable(grid, nx, entity.pos[1], unit_type):
            ny = entity.pos[1]
            vy = 0.0
        elif _passable(grid, entity.pos[0], ny, unit_type):
            nx = entity.pos[0]
            vx = 0.0
        else:
            nx = entity.pos[0]
            ny = entity.pos[1]
            vx = 0.0
            vy = 0.0

    entity.pos[0] = nx
    entity.pos[1] = ny
    entity.vel[0] = vx
    entity.vel[1] = vy


def follow_direction(entity, world, direction, dt, unit_type="ground", grid=None):
    dx, dy = direction
    _integrate(entity, world, dx * entity.speed, dy * entity.speed,
               dt, grid, unit_type)
    return 0, False


def follow_path(entity, world, path, path_index, dt, unit_type="ground", grid=None):
    # type: (object, object, List[Tuple[float, float]], int, float, str, object) -> Tuple[int, bool]
    if path_index >= len(path):
        return path_index, True

    last_i = len(path) - 1
    while path_index < last_i:
        wp = path[path_index]
        dx = wp[0] - entity.pos[0]
        dy = wp[1] - entity.pos[1]
        if dx * dx + dy * dy < WAYPOINT_ADVANCE_RADIUS * WAYPOINT_ADVANCE_RADIUS:
            path_index += 1
        else:
            break

    wp = path[path_index]
    dx = wp[0] - entity.pos[0]
    dy = wp[1] - entity.pos[1]
    d2 = dx * dx + dy * dy

    if path_index == last_i and d2 < ARRIVE_RADIUS * ARRIVE_RADIUS:
        entity.vel[0] = 0.0
        entity.vel[1] = 0.0
        return path_index, True
    if d2 < 1e-9:
        return path_index, path_index >= last_i

    d = math.sqrt(d2)
    vx = dx / d * entity.speed
    vy = dy / d * entity.speed
    _integrate(entity, world, vx, vy, dt, grid, unit_type)
    return path_index, False
