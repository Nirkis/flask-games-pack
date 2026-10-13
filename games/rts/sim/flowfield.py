"""Flow field для групп: Dijkstra от цели + вектор направления на каждом тайле."""
import heapq
import math
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

SQRT2 = math.sqrt(2.0)

NEIGHBORS = (
    (-1, -1, SQRT2), (-1, 0, 1.0), (-1, 1, SQRT2),
    ( 0, -1, 1.0),                 ( 0, 1, 1.0),
    ( 1, -1, SQRT2), ( 1, 0, 1.0), ( 1, 1, SQRT2),
)


class FlowField(object):
    __slots__ = ("grid", "target_tile", "unit_type", "width", "height",
                 "_dist", "_dir", "max_dist")

    def __init__(self, grid, target_tile, unit_type="ground", max_dist=200.0):
        # type: (object, Tuple[int, int], str, float) -> None
        self.grid = grid
        self.target_tile = (int(target_tile[0]), int(target_tile[1]))
        self.unit_type = unit_type
        self.width = grid.width
        self.height = grid.height
        self.max_dist = float(max_dist)
        n = self.width * self.height
        self._dist = [float("inf")] * n     # type: List[float]
        # list of Optional[Tuple[float, float]]; без подписки built-ins
        self._dir = [None] * n
        self._build()

    def _idx(self, x, y):
        return y * self.width + x

    def _build(self):
        g = self.grid
        ut = self.unit_type
        tx, ty = self.target_tile
        if not g.is_passable(tx, ty, ut):
            return

        dist = self._dist
        dist[self._idx(tx, ty)] = 0.0
        heap = [(0.0, tx, ty)]
        max_d = self.max_dist

        while heap:
            d, x, y = heapq.heappop(heap)
            if d > dist[self._idx(x, y)]:
                continue
            if d > max_d:
                break
            for dx, dy, dlen in NEIGHBORS:
                nx, ny = x + dx, y + dy
                if not g.is_passable(nx, ny, ut):
                    continue
                if dx != 0 and dy != 0:
                    if not g.is_passable(x + dx, y, ut):
                        continue
                    if not g.is_passable(x, y + dy, ut):
                        continue
                c = g.cost(nx, ny, ut)
                if c is None:
                    continue
                nd = d + dlen * c
                idx = self._idx(nx, ny)
                if nd < dist[idx]:
                    dist[idx] = nd
                    heapq.heappush(heap, (nd, nx, ny))

        dirs = self._dir
        for y in range(self.height):
            for x in range(self.width):
                idx = self._idx(x, y)
                if dist[idx] == float("inf"):
                    continue
                if (x, y) == self.target_tile:
                    dirs[idx] = (0.0, 0.0)
                    continue
                best = None
                best_d = dist[idx]
                for dx, dy, _ in NEIGHBORS:
                    nx, ny = x + dx, y + dy
                    if not g.is_passable(nx, ny, ut):
                        continue
                    if dx != 0 and dy != 0:
                        if not g.is_passable(x + dx, y, ut):
                            continue
                        if not g.is_passable(x, y + dy, ut):
                            continue
                    nd = dist[self._idx(nx, ny)]
                    if nd < best_d:
                        best_d = nd
                        best = (dx, dy)
                if best is None:
                    continue
                bl = math.hypot(best[0], best[1])
                dirs[idx] = (best[0] / bl, best[1] / bl)

    def has_path(self, world_pos):
        # type: (Tuple[float, float]) -> bool
        tx = int(math.floor(world_pos[0]))
        ty = int(math.floor(world_pos[1]))
        if not (0 <= tx < self.width and 0 <= ty < self.height):
            return False
        return self._dist[self._idx(tx, ty)] != float("inf")

    def direction_at(self, tile_x, tile_y):
        # type: (int, int) -> Tuple[float, float]
        tx, ty = int(tile_x), int(tile_y)
        if not (0 <= tx < self.width and 0 <= ty < self.height):
            return (0.0, 0.0)
        d = self._dir[self._idx(tx, ty)]
        return d if d is not None else (0.0, 0.0)

    def dist_at(self, tile_x, tile_y):
        # type: (int, int) -> float
        tx, ty = int(tile_x), int(tile_y)
        if not (0 <= tx < self.width and 0 <= ty < self.height):
            return float("inf")
        return self._dist[self._idx(tx, ty)]


class FlowFieldPool(object):
    def __init__(self, grid, max_entries=8):
        # type: (object, int) -> None
        self.grid = grid
        self.max_entries = int(max_entries)
        self._cache = OrderedDict()
        self._version = grid.version

    def _check_version(self):
        if self.grid.version != self._version:
            self._cache.clear()
            self._version = self.grid.version

    def get(self, target, unit_type="ground"):
        # type: (Tuple[float, float], str) -> FlowField
        self._check_version()
        tx = int(math.floor(target[0]))
        ty = int(math.floor(target[1]))
        key = (tx, ty, unit_type)
        entry = self._cache.get(key)
        if entry is not None:
            self._cache.move_to_end(key)
            return entry
        ff = FlowField(self.grid, (tx, ty), unit_type)
        self._cache[key] = ff
        self._cache.move_to_end(key)
        while len(self._cache) > self.max_entries:
            self._cache.popitem(last=False)
        return ff

    def invalidate(self):
        self._cache.clear()
