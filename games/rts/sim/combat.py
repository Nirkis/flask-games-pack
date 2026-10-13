"""Боевая система: урон, контр-типы, поиск цели. Python 3.6."""
import math
import random
from typing import Optional

# Таблица контр-типов по умолчанию (attack_type -> armor_type -> множитель).
# В rules.json можно переопределить. Если в rules лежит старая матрица
# категорий (melee/ranged/flying/armored/siege) — сработает fallback через
# attack_type_to_category / armor_type_to_category.
DEFAULT_DAMAGE_TABLE = {
    "normal": {"light": 1.0, "medium": 1.0,  "heavy": 1.0, "fortified": 1.0},
    "pierce": {"light": 2.0, "medium": 0.75, "heavy": 1.0, "fortified": 0.35},
    "siege":  {"light": 1.0, "medium": 0.5,  "heavy": 1.0, "fortified": 1.5},
    "magic":  {"light": 1.25, "medium": 0.75, "heavy": 1.0, "fortified": 1.5},
    "chaos":  {"light": 1.0, "medium": 1.0,  "heavy": 1.0, "fortified": 1.0},
}

DEFAULT_CRIT_CHANCE = 0.15
DEFAULT_CRIT_MULT = 1.5
ARMOR_REDUCTION_PER_POINT = 0.06

_rng = random.Random()


# ---------- таблица контр-типов ----------

def attack_multiplier(attack_type, armor_type, rules):
    # type: (str, str, dict) -> float
    tbl = (rules or {}).get("damage_table")
    if not tbl:
        return DEFAULT_DAMAGE_TABLE.get(attack_type, {}).get(armor_type, 1.0)

    # Новый формат: [attack_type][armor_type]
    row = tbl.get(attack_type)
    if isinstance(row, dict) and armor_type in row:
        return float(row[armor_type])

    # Старый формат: [attacker_category][defender_category]
    atc = (rules or {}).get("attack_type_to_category", {})
    aac = (rules or {}).get("armor_type_to_category", {})
    a_cat = atc.get(attack_type)
    b_cat = aac.get(armor_type)
    if a_cat and b_cat and a_cat in tbl:
        row = tbl.get(a_cat, {})
        return float(row.get(b_cat, 1.0))
    return 1.0


def compute_damage(base_attack, attack_type, armor, armor_type, rules):
    # type: (float, str, int, str, dict) -> float
    mult = attack_multiplier(attack_type, armor_type, rules)
    dmg = float(base_attack) * mult
    reduced = dmg * (1.0 - float(armor) * ARMOR_REDUCTION_PER_POINT)
    return max(1.0, reduced)


# ---------- критические удары ----------

def roll_crit(rules, rng=None):
    # type: (dict, Optional[object]) -> float
    if rules and rules.get("crit_enabled") is False:
        return 1.0
    ch = float((rules or {}).get("crit_chance", DEFAULT_CRIT_CHANCE))
    mult = float((rules or {}).get("crit_multiplier", DEFAULT_CRIT_MULT))
    r = (rng or _rng).random()
    return mult if r < ch else 1.0


# ---------- цели ----------

def is_enemy(a, b):
    # type: (object, object) -> bool
    if a is b:
        return False
    if b.owner == -1:
        return True
    if a.owner == -1:
        return True
    return a.owner != b.owner


def _dist2(a_pos, b_pos):
    dx = b_pos[0] - a_pos[0]
    dy = b_pos[1] - a_pos[1]
    return dx * dx + dy * dy


def in_attack_range(attacker, target):
    # type: (object, object) -> bool
    rng = float(getattr(attacker, "attack_range", 0.0))
    dx = target.pos[0] - attacker.pos[0]
    dy = target.pos[1] - attacker.pos[1]
    d = math.sqrt(dx * dx + dy * dy)
    return d <= rng + float(getattr(target, "radius", 0.4))


def in_vision(attacker, target):
    # type: (object, object) -> bool
    v = float(getattr(attacker, "vision", 0.0))
    dx = target.pos[0] - attacker.pos[0]
    dy = target.pos[1] - attacker.pos[1]
    return dx * dx + dy * dy <= v * v


def find_target(entity, world, radius=None):
    # type: (object, object, Optional[float]) -> Optional[object]
    """Ближайший враг; тай-брейк — меньше HP."""
    if radius is None:
        radius = float(getattr(entity, "attack_range", 0.0))
    r2 = float(radius) * float(radius)
    px, py = entity.pos
    best = None
    best_key = None
    for e in world.entities.values():
        if e is entity or not e.alive:
            continue
        if not is_enemy(entity, e):
            continue
        dx = e.pos[0] - px
        dy = e.pos[1] - py
        d2 = dx * dx + dy * dy
        if d2 > r2:
            continue
        key = (d2, e.hp, e.id)
        if best_key is None or key < best_key:
            best_key = key
            best = e
    return best


# ---------- нанесение удара ----------

def can_attack(entity):
    if getattr(entity, "attack", 0) <= 0:
        return False
    return entity.cooldowns.get("attack", 0.0) <= 0.0


def trigger_attack_cooldown(entity):
    speed = float(getattr(entity, "attack_speed", 1.0))
    if speed <= 0.0:
        speed = 1.0
    entity.cooldowns["attack"] = 1.0 / speed


def apply_attack(attacker, target, world):
    # type: (object, object, object) -> bool
    """
    Нанести удар. Возвращает True, если удар состоялся.
    range > 2 → создаётся Projectile, дальше урон наносит он.
    """
    if not can_attack(attacker):
        return False
    if target is None or not target.alive:
        return False
    if not in_attack_range(attacker, target):
        return False

    trigger_attack_cooldown(attacker)

    base = float(attacker.attack)
    atk_type = getattr(attacker, "attack_type", "normal")
    base *= roll_crit(world.rules)

    atk_range = float(getattr(attacker, "attack_range", 0.0))
    if atk_range > 2.0:
        from .projectile import Projectile
        dist = math.sqrt(_dist2(attacker.pos, target.pos))
        speed = max(dist / 0.3, 6.0)
        world.projectiles.append(Projectile(
            owner=attacker.owner,
            source_id=attacker.id,
            target_id=target.id,
            pos=(attacker.pos[0], attacker.pos[1]),
            base_damage=base,
            attack_type=atk_type,
            speed=speed,
        ))
    else:
        dmg = compute_damage(base, atk_type,
                             getattr(target, "armor", 0),
                             getattr(target, "armor_type", "light"),
                             world.rules)
        target.hp -= int(round(dmg))
        if target.hp <= 0:
            target.hp = 0
            target.kill()
    return True


def auto_attack_pass(world):
    """
    Юниты в IDLE без приказов атакуют врага в радиусе атаки.
    Юниты в MOVING/ATTACKING не трогаем — этим занимаются их приказы.
    """
    from .entity import EntityState
    for e in list(world.entities.values()):
        if not e.alive:
            continue
        if e.state is not EntityState.IDLE:
            continue
        if e.orders:
            continue
        if getattr(e, "attack", 0) <= 0:
            continue
        tgt = find_target(e, world)
        if tgt is not None:
            apply_attack(e, tgt, world)
