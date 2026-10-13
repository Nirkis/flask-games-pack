# -*- coding: utf-8 -*-
"""
Перегенерирует games/rts/data/maps/example_map.json в виде валидной
симметричной карты 64x64. Запуск:

    python tools/regen_example_map.py
"""
import json
import os

W, H = 64, 64

# tile ids (совпадают с legend в example_map.json и GROUND_COST в grid.py)
GRASS = 0
WATER = 1
FOREST = 2
STONE = 3
ROCK = 4
GOLD = 5
BUILDABLE = 6
DIRT = 7


def blank_map():
    # всё — трава
    return [GRASS] * (W * H)


def put(tiles, x, y, t):
    if 0 <= x < W and 0 <= y < H:
        tiles[y * W + x] = t


def fill_rect(tiles, x0, y0, w, h, t):
    for yy in range(y0, y0 + h):
        for xx in range(x0, x0 + w):
            put(tiles, xx, yy, t)


def water_border(tiles):
    for x in range(W):
        put(tiles, x, 0, WATER)
        put(tiles, x, H - 1, WATER)
    for y in range(H):
        put(tiles, 0, y, WATER)
        put(tiles, W - 1, y, WATER)


def forest(tiles):
    # симметричные пятна леса
    fill_rect(tiles, 11, 11, 4, 4, FOREST)
    fill_rect(tiles, 49, 49, 4, 4, FOREST)
    fill_rect(tiles, 11, 49, 4, 4, FOREST)
    fill_rect(tiles, 49, 11, 4, 4, FOREST)
    fill_rect(tiles, 6, 12, 3, 6, FOREST)
    fill_rect(tiles, 55, 12, 3, 6, FOREST)
    fill_rect(tiles, 6, 46, 3, 6, FOREST)
    fill_rect(tiles, 55, 46, 3, 6, FOREST)


def stone(tiles):
    fill_rect(tiles, 20, 20, 3, 3, STONE)
    fill_rect(tiles, 41, 41, 3, 3, STONE)
    fill_rect(tiles, 20, 41, 3, 3, STONE)
    fill_rect(tiles, 41, 20, 3, 3, STONE)


def rocks(tiles):
    # стенка в центре с зазорами, симметричная по диагонали
    for y in range(28, 36):
        put(tiles, 30, y, ROCK)
        put(tiles, 33, y, ROCK)
    # угловые скалы
    fill_rect(tiles, 2, 30, 2, 4, ROCK)
    fill_rect(tiles, 60, 30, 2, 4, ROCK)
    fill_rect(tiles, 30, 2, 4, 2, ROCK)
    fill_rect(tiles, 30, 60, 4, 2, ROCK)


def gold_mines_on_tiles(tiles):
    # тайлы-шахты (визуальный маркер; сами шахты — в resource_nodes)
    for (x, y) in [(13, 13), (51, 51), (12, 51), (51, 12)]:
        put(tiles, x, y, GOLD)


def build():
    tiles = blank_map()
    water_border(tiles)
    forest(tiles)
    stone(tiles)
    rocks(tiles)
    gold_mines_on_tiles(tiles)
    return tiles


def main():
    tiles = build()
    assert len(tiles) == W * H, "internal: %d != %d" % (len(tiles), W * H)

    data = {
        "name": "Долина",
        "size": [W, H],
        "tile_size": 32,
        "tile_legend": {
            "0": "grass",
            "1": "water",
            "2": "forest",
            "3": "stone",
            "4": "rock",
            "5": "gold_mine",
            "6": "buildable",
            "7": "dirt",
        },
        "tiles": tiles,
        "start_positions": [[10, 10], [54, 54]],
        "resource_nodes": [
            {"type": "gold_mine", "pos": [13, 13], "amount": 5000},
            {"type": "gold_mine", "pos": [51, 51], "amount": 5000},
            {"type": "gold_mine", "pos": [12, 51], "amount": 5000},
            {"type": "gold_mine", "pos": [51, 12], "amount": 5000},
            {"type": "wood_patch", "pos": [6, 14], "amount": 100},
            {"type": "wood_patch", "pos": [14, 6], "amount": 100},
            {"type": "wood_patch", "pos": [57, 50], "amount": 100},
            {"type": "wood_patch", "pos": [50, 57], "amount": 100},
        ],
        "neutral_creeps": [],
    }

    here = os.path.abspath(os.path.dirname(__file__))
    out = os.path.join(here, "..", "games", "rts", "data", "maps", "example_map.json")
    out = os.path.normpath(out)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("wrote %s (%d tiles)" % (out, len(tiles)))


if __name__ == "__main__":
    main()
