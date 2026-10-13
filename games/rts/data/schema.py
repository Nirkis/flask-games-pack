#!/usr/bin/env python3
"""
Валидатор данных для браузерной RTS «Наследие Азерота».

Проверяет консистентность всех JSON-файлов в games/rts/data/:
  - races.json, units.json, buildings.json, abilities.json, rules.json
  - maps/*.json

Запуск:
    python schema.py
    python schema.py --data-dir /path/to/data
"""


import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SPRITE_RE = re.compile(r"^[a-z_]+\.png$")
HOTKEY_RE = re.compile(r"^[A-Z]$")

VALID_ARMOR_TYPES = {"light", "medium", "heavy", "fortified", "hero"}
VALID_ATTACK_TYPES = {"normal", "pierce", "siege", "magic", "chaos"}
VALID_TARGETS = {"friendly_unit", "enemy_unit", "any_unit", "point", "none", "self"}
VALID_EFFECT_TYPES = {"heal", "damage", "summon", "buff", "aoe"}
VALID_RESOURCES = {"gold", "wood", "food"}
VALID_TILE_IDS = set(range(8))


class Validator:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.races: dict = {}
        self.units: dict = {}
        self.buildings: dict = {}
        self.abilities: dict = {}
        self.rules: dict = {}
        self.maps: dict[str, dict] = {}

    # ---------- reporting ----------

    def err(self, msg: str) -> None:
        self.errors.append(msg)
        print(f"  ERROR: {msg}", file=sys.stderr)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)
        print(f"  WARN:  {msg}", file=sys.stderr)

    def section(self, title: str) -> None:
        print(f"\n[{title}]")

    # ---------- loading ----------

    def load_json(self, rel_path: str) -> Any:
        path = self.data_dir / rel_path
        if not path.exists():
            self.err(f"file not found: {path}")
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            self.err(f"invalid JSON in {rel_path}: {e}")
            return None

    def load_all(self) -> None:
        self.section("Loading JSON")
        self.races = self.load_json("races.json") or {}
        self.units = self.load_json("units.json") or {}
        self.buildings = self.load_json("buildings.json") or {}
        self.abilities = self.load_json("abilities.json") or {}
        self.rules = self.load_json("rules.json") or {}

        maps_dir = self.data_dir / "maps"
        if maps_dir.is_dir():
            for m in sorted(maps_dir.glob("*.json")):
                data = self.load_json(f"maps/{m.name}")
                if data is not None:
                    self.maps[m.stem] = data

        print(f"  races={len(self.races)} units={len(self.units)} "
              f"buildings={len(self.buildings)} abilities={len(self.abilities)} "
              f"maps={len(self.maps)}")

    # ---------- helpers ----------

    def check_positive_number(self, value: Any, path: str) -> None:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            self.err(f"{path}: must be a number, got {type(value).__name__}")
            return
        if value <= 0:
            self.err(f"{path}: must be > 0, got {value}")

    def check_non_negative_number(self, value: Any, path: str) -> None:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            self.err(f"{path}: must be a number, got {type(value).__name__}")
            return
        if value < 0:
            self.err(f"{path}: must be >= 0, got {value}")

    def check_string(self, value: Any, path: str) -> None:
        if not isinstance(value, str) or not value:
            self.err(f"{path}: must be non-empty string")

    def check_cost(self, cost: Any, path: str) -> None:
        if not isinstance(cost, dict):
            self.err(f"{path}: must be an object")
            return
        for key, val in cost.items():
            if key not in VALID_RESOURCES:
                self.warn(f"{path}.{key}: unknown resource name")
            if not isinstance(val, int) or isinstance(val, bool) or val < 0:
                self.err(f"{path}.{key}: must be non-negative integer, got {val!r}")

    def check_list_of_strings(self, lst: Any, path: str, known: set[str] | None = None) -> None:
        if not isinstance(lst, list):
            self.err(f"{path}: must be a list")
            return
        for i, item in enumerate(lst):
            if not isinstance(item, str):
                self.err(f"{path}[{i}]: must be a string")
            elif known is not None and item not in known:
                self.err(f"{path}[{i}]: unknown id '{item}'")

    # ---------- races ----------

    def validate_races(self) -> None:
        self.section("races.json")
        for race_id, race in self.races.items():
            p = f"races.{race_id}"
            self.check_string(race.get("name"), f"{p}.name")
            self.check_string(race.get("description"), f"{p}.description")
            color = race.get("color", "")
            if not isinstance(color, str) or not re.match(r"^#[0-9a-fA-F]{6}$", color):
                self.err(f"{p}.color: expected #RRGGBB, got {color!r}")
            rb = race.get("resource_bonus", {})
            if not isinstance(rb, dict):
                self.err(f"{p}.resource_bonus: must be object")
            else:
                for k, v in rb.items():
                    if k not in VALID_RESOURCES:
                        self.err(f"{p}.resource_bonus.{k}: unknown resource")
                    if not isinstance(v, (int, float)) or isinstance(v, bool):
                        self.err(f"{p}.resource_bonus.{k}: must be a number")
            self.check_list_of_strings(race.get("heroes", []), f"{p}.heroes", set(self.units))
            self.check_list_of_strings(
                race.get("starting_units", []), f"{p}.starting_units", set(self.units)
            )

    # ---------- units ----------

    def validate_units(self) -> None:
        self.section("units.json")
        for uid, u in self.units.items():
            p = f"units.{uid}"
            self.check_string(u.get("name"), f"{p}.name")

            race = u.get("race")
            if race not in self.races:
                self.err(f"{p}.race: '{race}' not in races.json")

            self.check_positive_number(u.get("hp"), f"{p}.hp")
            self.check_positive_number(u.get("armor"), f"{p}.armor") if u.get("armor", 0) > 0 \
                else self.check_non_negative_number(u.get("armor", 0), f"{p}.armor")
            at = u.get("armor_type")
            if at not in VALID_ARMOR_TYPES:
                self.err(f"{p}.armor_type: '{at}' not in {sorted(VALID_ARMOR_TYPES)}")
            self.check_positive_number(u.get("attack"), f"{p}.attack")
            atk_t = u.get("attack_type")
            if atk_t not in VALID_ATTACK_TYPES:
                self.err(f"{p}.attack_type: '{atk_t}' not in {sorted(VALID_ATTACK_TYPES)}")

            self.check_positive_number(u.get("attack_speed"), f"{p}.attack_speed")
            self.check_non_negative_number(u.get("range"), f"{p}.range")
            self.check_positive_number(u.get("speed"), f"{p}.speed")
            self.check_positive_number(u.get("vision"), f"{p}.vision")

            self.check_cost(u.get("cost"), f"{p}.cost")
            self.check_positive_number(u.get("build_time"), f"{p}.build_time")

            pb = u.get("produced_by")
            if pb not in self.buildings:
                self.err(f"{p}.produced_by: '{pb}' not in buildings.json")

            self.check_list_of_strings(u.get("requires", []), f"{p}.requires",
                                       set(self.units) | set(self.buildings))
            self.check_list_of_strings(u.get("abilities", []), f"{p}.abilities",
                                       set(self.abilities))

            sprite = u.get("sprite")
            if not isinstance(sprite, str) or not SPRITE_RE.match(sprite):
                self.err(f"{p}.sprite: expected [a-z_]+.png, got {sprite!r}")

            hk = u.get("hotkey")
            if not isinstance(hk, str) or not HOTKEY_RE.match(hk):
                self.err(f"{p}.hotkey: expected single A-Z letter, got {hk!r}")

            # sanity: melee vs ranged
            rng = u.get("range", 0)
            if atk_t == "siege" and rng < 4:
                self.warn(f"{p}: siege unit with range {rng}, expected >= 4")
            if atk_t in ("pierce", "magic") and rng < 2:
                self.warn(f"{p}: ranged unit with range {rng}, expected >= 2")

    # ---------- buildings ----------

    def validate_buildings(self) -> None:
        self.section("buildings.json")
        for bid, b in self.buildings.items():
            p = f"buildings.{bid}"
            self.check_string(b.get("name"), f"{p}.name")

            race = b.get("race")
            if race not in self.races:
                self.err(f"{p}.race: '{race}' not in races.json")

            self.check_positive_number(b.get("hp"), f"{p}.hp")
            self.check_non_negative_number(b.get("armor", 0), f"{p}.armor")
            at = b.get("armor_type")
            if at not in VALID_ARMOR_TYPES:
                self.err(f"{p}.armor_type: '{at}' not in {sorted(VALID_ARMOR_TYPES)}")

            self.check_cost(b.get("cost"), f"{p}.cost")
            self.check_positive_number(b.get("build_time"), f"{p}.build_time")

            fp = b.get("footprint")
            if (not isinstance(fp, list) or len(fp) != 2
                    or not all(isinstance(x, int) and x > 0 for x in fp)):
                self.err(f"{p}.footprint: expected [w, h] positive ints, got {fp!r}")

            self.check_positive_number(b.get("vision"), f"{p}.vision")
            self.check_list_of_strings(b.get("produces", []), f"{p}.produces", set(self.units))
            self.check_list_of_strings(b.get("researches", []), f"{p}.researches", None)

            sprite = b.get("sprite")
            if not isinstance(sprite, str) or not SPRITE_RE.match(sprite):
                self.err(f"{p}.sprite: expected [a-z_]+.png, got {sprite!r}")

            hk = b.get("hotkey")
            if not isinstance(hk, str) or not HOTKEY_RE.match(hk):
                self.err(f"{p}.hotkey: expected single A-Z letter, got {hk!r}")

            fp_val = b.get("food_provided", 0)
            self.check_non_negative_number(fp_val, f"{p}.food_provided")

            # main building должен предоставлять еду
            if b.get("is_main") and fp_val < 1:
                self.warn(f"{p}: main building provides {fp_val} food")

    # ---------- abilities ----------

    def validate_abilities(self) -> None:
        self.section("abilities.json")
        for aid, a in self.abilities.items():
            p = f"abilities.{aid}"
            self.check_string(a.get("name"), f"{p}.name")

            tgt = a.get("target")
            if tgt not in VALID_TARGETS:
                self.err(f"{p}.target: '{tgt}' not in {sorted(VALID_TARGETS)}")

            cd = a.get("cooldown")
            self.check_non_negative_number(cd, f"{p}.cooldown")
            mana = a.get("mana_cost")
            self.check_non_negative_number(mana, f"{p}.mana_cost")
            cr = a.get("cast_range")
            self.check_non_negative_number(cr, f"{p}.cast_range")

            eff = a.get("effect")
            if not isinstance(eff, dict):
                self.err(f"{p}.effect: must be an object")
                continue
            et = eff.get("type")
            if et not in VALID_EFFECT_TYPES:
                self.err(f"{p}.effect.type: '{et}' not in {sorted(VALID_EFFECT_TYPES)}")

            for num_field in ("amount", "duration"):
                if num_field in eff:
                    self.check_non_negative_number(eff[num_field], f"{p}.effect.{num_field}")

            # логика target vs effect
            if tgt == "none" and et in ("heal", "damage"):
                self.err(f"{p}: target=none but effect={et} (need a unit target)")
            if tgt in ("friendly_unit", "enemy_unit", "any_unit") and cr <= 0:
                self.err(f"{p}: unit-targeted ability must have cast_range > 0")

    # ---------- rules ----------

    def validate_rules(self) -> None:
        self.section("rules.json")
        r = self.rules
        for key in ("tick_rate", "max_population", "starting_gold", "starting_wood",
                    "match_time_limit_sec", "max_units_per_player", "hero_max_level"):
            if key not in r:
                self.err(f"rules.{key}: missing")
            else:
                self.check_positive_number(r[key], f"rules.{key}")

        self.check_string(r.get("victory_condition"), "rules.victory_condition")

        rg = r.get("resource_gather", {})
        if isinstance(rg, dict):
            for k in ("gold_per_trip", "wood_per_trip", "trip_time_sec",
                      "mine_total_gold", "tree_total_wood"):
                if k in rg:
                    self.check_positive_number(rg[k], f"rules.resource_gather.{k}")

        sv = r.get("score_values", {})
        if isinstance(sv, dict):
            for k, v in sv.items():
                if not isinstance(v, int) or v < 0:
                    self.err(f"rules.score_values.{k}: must be non-negative int")

        dt = r.get("damage_table")
        if not isinstance(dt, dict):
            self.err("rules.damage_table: missing or not an object")
        else:
            expected = {"melee", "ranged", "flying", "armored", "siege"}
            for k in expected:
                if k not in dt:
                    self.err(f"rules.damage_table.{k}: missing row")
            for k, row in dt.items():
                if not isinstance(row, dict):
                    self.err(f"rules.damage_table.{k}: must be object")
                    continue
                for kk, vv in row.items():
                    if not isinstance(vv, (int, float)) or isinstance(vv, bool) or vv < 0:
                        self.err(f"rules.damage_table.{k}.{kk}: must be non-negative number")

        for map_key in ("armor_type_to_category", "attack_type_to_category"):
            m = r.get(map_key)
            if not isinstance(m, dict):
                self.err(f"rules.{map_key}: missing or not object")
                continue
            for src, cat in m.items():
                if cat not in (dt or {}):
                    self.err(f"rules.{map_key}.{src}: category '{cat}' not in damage_table")

    # ---------- maps ----------

    def validate_maps(self) -> None:
        self.section("maps/")
        for map_id, m in self.maps.items():
            p = f"maps.{map_id}"
            self.check_string(m.get("name"), f"{p}.name")

            size = m.get("size")
            if (not isinstance(size, list) or len(size) != 2
                    or not all(isinstance(x, int) and x > 0 for x in size)):
                self.err(f"{p}.size: expected [w, h] positive ints, got {size!r}")
                continue
            w, h = size

            ts = m.get("tile_size")
            self.check_positive_number(ts, f"{p}.tile_size")

            tiles = m.get("tiles")
            if not isinstance(tiles, list):
                self.err(f"{p}.tiles: must be a list")
                continue
            # permit nested (list of rows) for readability
            if tiles and isinstance(tiles[0], list):
                flat: list[int] = []
                for row in tiles:
                    if not isinstance(row, list):
                        self.err(f"{p}.tiles: mixed nesting")
                        break
                    flat.extend(row)
                tiles = flat
            if len(tiles) != w * h:
                self.err(f"{p}.tiles: length {len(tiles)} != {w}*{h} = {w*h}")
            bad = [t for t in tiles if not isinstance(t, int) or t not in VALID_TILE_IDS]
            if bad:
                self.err(f"{p}.tiles: {len(bad)} invalid tile ids (first: {bad[0]!r})")

            sp = m.get("start_positions", [])
            if not isinstance(sp, list) or len(sp) != 2:
                self.err(f"{p}.start_positions: expected exactly 2 positions")
            else:
                for i, pos in enumerate(sp):
                    if (not isinstance(pos, list) or len(pos) != 2
                            or not all(isinstance(c, int) for c in pos)):
                        self.err(f"{p}.start_positions[{i}]: expected [x, y] ints")
                        continue
                    x, y = pos
                    if not (0 <= x < w and 0 <= y < h):
                        self.err(f"{p}.start_positions[{i}]: [{x},{y}] out of bounds")

            rn = m.get("resource_nodes", [])
            if not isinstance(rn, list):
                self.err(f"{p}.resource_nodes: must be a list")
            else:
                for i, node in enumerate(rn):
                    np = f"{p}.resource_nodes[{i}]"
                    if not isinstance(node, dict):
                        self.err(f"{np}: must be object")
                        continue
                    self.check_string(node.get("type"), f"{np}.type")
                    pos = node.get("pos")
                    if (not isinstance(pos, list) or len(pos) != 2
                            or not all(isinstance(c, int) for c in pos)):
                        self.err(f"{np}.pos: expected [x, y] ints")
                    else:
                        x, y = pos
                        if not (0 <= x < w and 0 <= y < h):
                            self.err(f"{np}.pos: [{x},{y}] out of bounds")
                    amt = node.get("amount")
                    if amt is not None:
                        self.check_positive_number(amt, f"{np}.amount")

            # Не должно быть ресурсов на клетках воды/скал
            if isinstance(tiles, list) and len(tiles) == w * h:
                for i, node in enumerate(rn if isinstance(rn, list) else []):
                    pos = node.get("pos") if isinstance(node, dict) else None
                    if isinstance(pos, list) and len(pos) == 2:
                        x, y = pos
                        if 0 <= x < w and 0 <= y < h:
                            tile = tiles[y * w + x]
                            if tile in (1, 4):
                                self.warn(f"{p}.resource_nodes[{i}]: placed on water/rock (tile={tile})")

    # ---------- cross-cutting ----------

    def validate_cross_references(self) -> None:
        self.section("Cross-references")

        # Каждая фракция имеет хотя бы один основной юнит-рабочий
        for race_id, race in self.races.items():
            workers = [uid for uid, u in self.units.items()
                       if u.get("race") == race_id and u.get("cost", {}).get("food", 0) == 1
                       and u.get("attack", 0) <= 10]
            if not workers:
                self.warn(f"race '{race_id}' has no obvious worker unit")

            # Фракция должна иметь основной и военный юниты
            unit_count = sum(1 for u in self.units.values() if u.get("race") == race_id)
            if unit_count < 2:
                self.warn(f"race '{race_id}' has only {unit_count} unit(s)")

            bld_count = sum(1 for b in self.buildings.values() if b.get("race") == race_id)
            if bld_count < 2:
                self.warn(f"race '{race_id}' has only {bld_count} building(s)")

        # Каждый юнит должен кем-то производиться
        producers: dict[str, list[str]] = {}
        for bid, b in self.buildings.items():
            for uid in b.get("produces", []):
                producers.setdefault(uid, []).append(bid)
        for uid in self.units:
            if uid not in producers:
                self.warn(f"unit '{uid}' is not produced by any building")

        # Каждая способность героя должна быть у кого-то
        used_abilities: set[str] = set()
        for u in self.units.values():
            used_abilities.update(u.get("abilities", []))
        for aid in self.abilities:
            if aid not in used_abilities:
                self.warn(f"ability '{aid}' is not used by any unit")

        # Все спрайты юнитов/зданий должны быть уникальны
        sprites: dict[str, list[str]] = {}
        for uid, u in self.units.items():
            sprites.setdefault(u.get("sprite", ""), []).append(f"unit:{uid}")
        for bid, b in self.buildings.items():
            sprites.setdefault(b.get("sprite", ""), []).append(f"building:{bid}")
        for sprite, owners in sprites.items():
            if sprite and len(owners) > 1:
                self.warn(f"sprite '{sprite}' shared by {owners}")

    # ---------- main ----------

    def run(self) -> int:
        self.load_all()
        self.validate_races()
        self.validate_units()
        self.validate_buildings()
        self.validate_abilities()
        self.validate_rules()
        self.validate_maps()
        self.validate_cross_references()

        print()
        print("=" * 60)
        if self.errors:
            print(f"FAILED: {len(self.errors)} error(s), {len(self.warnings)} warning(s)")
            return 1
        print(f"OK: 0 errors, {len(self.warnings)} warning(s)")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate RTS data files.")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Path to the data directory (default: directory of this script)",
    )
    args = parser.parse_args()
    return Validator(args.data_dir).run()


if __name__ == "__main__":
    sys.exit(main())
