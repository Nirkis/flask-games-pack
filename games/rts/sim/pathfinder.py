"""A* по 8 направлениям + LRU-кэш путей. Python 3.6."""
import heapq
import math
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

SQRT2 = math.sqrt(2.0)

# (dx, dy, base_distance)
NEIGHBORS = (
    (-1, -1, SQRT2), (-1, 0, 1.0), (-1, 1, SQRT2),
    ( 0, -1, 1.0),                 ( 0, 1, 1.0),
    ( 1, -1, SQRT2), ( 1, 0, 1.0), ( 1, 1, SQRT2),
)


class PathCache(object):
    __slots__ = ("maxsize", "_d")

    def __init__(self, maxsize=1000):
        # type: (int) -> None
        self.maxsize = int(maxsize)
        self._d = OrderedDict()

    def get(self, key):
        v = self._d.get(key)
        if v is not None:
            self._d.move_to_end(key)
        return v

    def put(self, key, value):
        self._d[key] = value
        self._d.move_to_end(key)
        while len(self._d) > self.maxsize:
            self._d.popitem(last=False)

    def clear(self):
        self._d.clear()

    def __len__(self):
        return len(self._d)


class Pathfinder(object):
    def __init__(self, grid, cache_size=1000, max_iterations=5000):
        # type: (object, int, int) -> None
        self.grid = grid
        self.cache = PathCache(cache_size)
        self.max_iterations = int(max_iterations)
        self._cached_grid_version = grid.version
        self._min_cost = self._compute_min_cost("ground") or 1.0

    # ---------- версия/инвалидация ----------

    def _check_version(self):
        if self.grid.version != self._cached_grid_version:
            self.cache.clear()
            self._cached_grid_version = self.grid.version

    def invalidate_cache(self, region=None):
        self.cache.clear()
        self._min_cost = self._compute_min_cost("ground") or 1.0

    def _compute_min_cost(self, unit_type):
        # type: (str) -> float
        mn = float("inf")
        g = self.grid
        for y in range(g.height):
            for x in range(g.width):
                c = g.cost(x, y, unit_type)
                if c is not None and c < mn:
                    mn = c
        return mn if mn != float("inf") else 1.0

    # ---------- публичный API ----------

    def find_path(self, start, end, unit_type="ground"):
        # type: (Tuple[float, float], Tuple[float, float], str) -> Optional[List[Tuple[float, float]]]
        """
        start, end — мировые координаты (float-тайлы).
        Возвращает список waypoints [(x, y), ...] или None.
        """
        self._check_version()

        sx = int(math.floor(start[0]))
        sy = int(math.floor(start[1]))
        ex = int(math.floor(end[0]))
        ey = int(math.floor(end[1]))
        end_exact = (float(end[0]), float(end[1]))

        if not self.grid.in_bounds(sx, sy):
            return [end_exact]

        if not self.grid.is_passable(ex, ey, unit_type):
            found = self._nearest_passable(ex, ey, unit_type, r_max=3)
            if not found:
                return None
            ex, ey = found

        if (sx, sy) == (ex, ey):
            return [end_exact]

        key = (sx, sy, ex, ey, unit_type)
        cached = self.cache.get(key)
        if cached is not None:
            result = list(cached)
            result[-1] = end_exact
            return result

        tile_path = self._astar((sx, sy), (ex, ey), unit_type)
        if tile_path is None:
            world_path = [end_exact]
        else:
            world_path = []   # type: List[Tuple[float, float]]
            last = len(tile_path) - 1
            for i, (tx, ty) in enumerate(tile_path):
                if i == 0:
                    continue
                if i == last:
                    world_path.append(end_exact)
                else:
                    world_path.append((tx + 0.5, ty + 0.5))
            if not world_path:
                world_path = [end_exact]

        self.cache.put(key, world_path)
        return list(world_path)

    # ---------- внутренние ----------

    def _nearest_passable(self, cx, cy, unit_type, r_max=3):
        # type: (int, int, str, int) -> Optional[Tuple[int, int]]
        g = self.grid
        if g.is_passable(cx, cy, unit_type):
            return (cx, cy)
        for r in range(1, r_max + 1):
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    if abs(dx) != r and abs(dy) != r:
                        continue
                    if g.is_passable(cx + dx, cy + dy, unit_type):
                        return (cx + dx, cy + dy)
        return None

    def _astar(self, start, end, unit_type):
        # type: (Tuple[int, int], Tuple[int, int], str) -> Optional[List[Tuple[int, int]]]
        g = self.grid
        if not g.is_passable(start[0], start[1], unit_type):
            return None
        if not g.is_passable(end[0], end[1], unit_type):
            return None

        hf = self._min_cost

        def heuristic(a, b):
            dx = abs(a[0] - b[0])
            dy = abs(a[1] - b[1])
            return (dx + dy) + (SQRT2 - 2.0) * (dx if dx < dy else dy)

        counter = 0
        h0 = heuristic(start, end) * hf
        open_heap = [(h0, counter, start, 0.0)]
        counter += 1

        came_from = {}    # type: Dict[Tuple[int, int], Tuple[int, int]]
        g_score = {start: 0.0}   # type: Dict[Tuple[int, int], float]
        closed = set()    # type: set

        max_iter = self.max_iterations
        iterations = 0

        while open_heap:
            iterations += 1
            if iterations > max_iter:
                return None
            f, _, node, gcur = heapq.heappop(open_heap)
            if node in closed:
                continue
            if node == end:
                path = [node]
                cur = node
                while cur in came_from:
                    cur = came_from[cur]
                    path.append(cur)
                path.reverse()
                return path
            closed.add(node)
            x, y = node
            for dx, dy, dlen in NEIGHBORS:
                nx, ny = x + dx, y + dy
                if not g.is_passable(nx, ny, unit_type):
                    continue
                if dx != 0 and dy != 0:
                    if not g.is_passable(x + dx, y, unit_type):
                        continue
                    if not g.is_passable(x, y + dy, unit_type):
                        continue
                n = (nx, ny)
                if n in closed:
                    continue
                step = dlen * g.cost(nx, ny, unit_type)
                tg = gcur + step
                if tg < g_score.get(n, float("inf")):
                    g_score[n] = tg
                    hn = heuristic(n, end) * hf
                    heapq.heappush(open_heap, (tg + hn, counter, n, tg))
                    counter += 1
                    came_from[n] = node
        return None
