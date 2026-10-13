"""Очереди производства юнитов в зданиях. Python 3.6."""
from .entity import Entity, EntityState


MAX_QUEUE = 5


class ProductionItem(object):
    __slots__ = ("unit_type", "time_left", "time_total", "cost")

    def __init__(self, unit_type, time_total, cost=None):
        self.unit_type = unit_type
        self.time_left = float(time_total)
        self.time_total = float(time_total)
        self.cost = dict(cost or {})


def _unit_data(world, unit_type):
    return world.units_data.get(unit_type)


def _spawn_offset(building):
    fp = getattr(building, "footprint", [1, 1])
    return (building.pos[0] + fp[0] * 0.5 + 0.5, building.pos[1])


def enqueue(building, unit_type, cost, build_time, world):
    # type: (object, str, dict, float, object) -> bool
    if not building.alive:
        return False
    if getattr(building, "under_construction", False):
        return False
    if len(building.production_queue) >= MAX_QUEUE:
        return False

    ud = world.units_data.get(unit_type)
    if ud is None:
        return False

    gold = int((cost or {}).get("gold", 0))
    wood = int((cost or {}).get("wood", 0))
    food = int((cost or {}).get("food", 0))

    st = world.resources.setdefault(building.owner, {"gold": 0, "wood": 0})
    if st.get("gold", 0) < gold or st.get("wood", 0) < wood:
        return False

    food_used = _compute_food_used(world, building.owner)
    food_cap = _compute_food_cap(world, building.owner)
    if food_used + food > food_cap:
        return False

    st["gold"] = int(st.get("gold", 0)) - gold
    st["wood"] = int(st.get("wood", 0)) - wood

    building.production_queue.append(ProductionItem(unit_type, float(build_time)))
    return True


def cancel(building, index, world):
    q = building.production_queue
    if index < 0 or index >= len(q):
        return False
    item = q[index]
    ratio = 1.0 if index > 0 else 0.5
    cost = item.cost or {}
    rg = int(int(cost.get("gold", 0)) * ratio)
    rw = int(int(cost.get("wood", 0)) * ratio)
    st = world.resources.setdefault(building.owner, {"gold": 0, "wood": 0})
    st["gold"] = int(st.get("gold", 0)) + rg
    st["wood"] = int(st.get("wood", 0)) + rw
    q.pop(index)
    return True


def _new_unit_entity(unit_type, owner, pos, ud, cost_food=0):
    hp = int(ud.get("hp", 1))
    e = Entity(
        id=None, type_id=unit_type, owner=owner, pos=pos,
        hp=hp, max_hp=hp,
        speed=float(ud.get("speed", 1.0)),
        vision=float(ud.get("vision", 5.0)),
        radius=float(ud.get("radius", 0.4)),
        attack=int(ud.get("attack", 0)),
        attack_speed=float(ud.get("attack_speed", 1.0)),
        attack_type=str(ud.get("attack_type", "normal")),
        attack_range=float(ud.get("range", 0.0)),
        armor=int(ud.get("armor", 0)),
        armor_type=str(ud.get("armor_type", "light")),
        unit_type=str(ud.get("unit_type", "ground")),
    )
    e.cost_food = int(cost_food)
    return e


def tick_production(world, dt):
    """Обработка очередей всех зданий. Вызывается из World.step()."""
    for b in list(world.entities.values()):
        if not b.alive:
            continue
        if not getattr(b, "is_building", False):
            continue
        if getattr(b, "under_construction", False):
            continue
        q = b.production_queue
        if not q:
            continue
        item = q[0]
        item.time_left -= dt
        if item.time_left <= 0.0:
            ud = _unit_data(world, item.unit_type)
            if ud is None:
                q.pop(0)
                continue
            spawn_pos = _spawn_offset(b)
            u = _new_unit_entity(item.unit_type, b.owner, spawn_pos, ud,
                                 cost_food=int((item.cost or {}).get("food", 0)))
            world.spawn(u)
            q.pop(0)


# ---------- food cap / food used ----------

def _compute_food_cap(world, player):
    cap = 10                                     # база
    for e in world.entities.values():
        if not e.alive or e.owner != player:
            continue
        if not getattr(e, "is_building", False):
            continue
        if getattr(e, "under_construction", False):
            continue
        cap += int(getattr(e, "food_provided", 0))
    return cap


def _compute_food_used(world, player):
    used = 0
    for e in world.entities.values():
        if not e.alive or e.owner != player:
            continue
        if getattr(e, "is_building", False):
            continue
        used += int(getattr(e, "cost_food", 0))
    # ещё в очередях
    for e in world.entities.values():
        if not e.alive or e.owner != player:
            continue
        if not getattr(e, "is_building", False):
            continue
        for item in e.production_queue:
            ud = world.units_data.get(item.unit_type)
            if ud:
                used += int((ud.get("cost") or {}).get("food", 0))
    return used


def recalc_food(world):
    """Обновить food_cap / food_used у всех игроков."""
    players = set(world.resources.keys())
    for e in world.entities.values():
        if e.owner >= 0:
            players.add(e.owner)
    for p in players:
        st = world.resources.setdefault(p, {"gold": 0, "wood": 0})
        st["food_cap"] = _compute_food_cap(world, p)
        st["food_used"] = _compute_food_used(world, p)

