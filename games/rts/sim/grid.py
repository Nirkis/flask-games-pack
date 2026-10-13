"""Сетка навигации: тайлы, проходимость, стоимость."""
import sys
from typing import Dict, List, Optional, Set, Tuple


GROUND_COST = {
    0: 1.0,    # grass
    1: None,   # water (blocked)
    2: None,   # forest (blocked for ground)
    3: 3.0,    # stone
    4: 0.5,    # road
    5: 1.0,    # gold_mine
    6: 1.0,    # buildable
    7: 1.0,    # dirt
}

WORKER_EXTRA = {
    2: 2.0,    # forest: cost 2
}


class Grid(object):
    __slots__ = ("width", "height", "tiles", "tile_size", "blocked", "version")

    def __init__(self, width, height, tiles, tile_size=32):
        # type: (int, int, List[int], int) -> None
        self.width = int(width)
        self.height = int(height)
        self.tiles = list(tiles)
        self.tile_size = int(tile_size)
        self.blocked = set()    # type: Set[Tuple[int, int]]
        self.version = 0

    @classmethod
    def from_map(cls, map_data, auto_fix=True):
        # type: (dict, bool) -> "Grid"
        size = map_data["size"]
        w, h = int(size[0]), int(size[1])
        tiles = map_data["tiles"]
        if tiles and isinstance(tiles[0], list):
            flat = []   # type: List[int]
            for row in tiles:
                flat.extend(row)
            tiles = flat

        expected = w * h
        if len(tiles) != expected:
            if not auto_fix:
                raise ValueError(
                    "tiles length %d != %d" % (len(tiles), expected)
                )
            print(
                "[Grid] warning: tiles length %d != %d, auto-fixing"
                % (len(tiles), expected),
                file=sys.stderr,
            )
            tiles = list(tiles)
            if len(tiles) < expected:
                # Паддинг. Если не хватает на полную строку — оставшиеся
                # добираем водой (1), чтобы не появились «дырявые» края.
                missing = expected - len(tiles)
                full_rows = missing // w
                tail = missing % w
                tiles.extend([0] * (full_rows * w))
                if tail:
                    tiles.extend([1] * tail)
            else:
                tiles = tiles[:expected]

        return cls(w, h, list(tiles), int(map_data.get("tile_size", 32)))

    @classmethod
    def from_file(cls, path):
        # type: (str) -> "Grid"
        import json
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_map(json.load(f))

    # ---------- запросы ----------

    def in_bounds(self, x, y):
        # type: (int, int) -> bool
        return 0 <= x < self.width and 0 <= y < self.height

    def is_passable(self, x, y, unit_type="ground"):
        # type: (int, int, str) -> bool
        return self.cost(x, y, unit_type) is not None

    def cost(self, x, y, unit_type="ground"):
        # type: (int, int, str) -> Optional[float]
        if not self.in_bounds(x, y):
            return None
        if (x, y) in self.blocked:
            return None
        tid = self.tiles[y * self.width + x]
        if unit_type == "worker" and tid in WORKER_EXTRA:
            return WORKER_EXTRA[tid]
        return GROUND_COST.get(tid, 1.0)

    def world_to_tile(self, wx, wy):
        # type: (float, float) -> Tuple[int, int]
        import math
        return int(math.floor(wx)), int(math.floor(wy))

    # ---------- динамические препятствия (здания) ----------

    def block_tiles(self, coords):
        # type: (List[Tuple[int, int]]) -> None
        for c in coords:
            self.blocked.add((int(c[0]), int(c[1])))
        self.version += 1

    def unblock_tiles(self, coords):
        # type: (List[Tuple[int, int]]) -> None
        for c in coords:
            self.blocked.discard((int(c[0]), int(c[1])))
        self.version += 1
