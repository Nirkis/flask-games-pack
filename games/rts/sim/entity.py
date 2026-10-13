"""Сущность мира: юнит или здание. Python 3.6."""
from collections import deque
from enum import Enum
from typing import Dict, List, Optional


class EntityState(Enum):
    IDLE = "idle"
    MOVING = "moving"
    ATTACKING = "attacking"
    GATHERING = "gathering"
    BUILDING = "building"
    DEAD = "dead"


class Entity(object):
    __slots__ = (
        "id", "type_id", "owner",
        "pos", "vel",
        "hp", "max_hp",
        "state", "orders",
        "target_pos", "target_entity_id",
        "cooldowns",
        "speed", "vision", "radius",
        "attack", "attack_speed", "attack_type", "attack_range",
        "armor", "armor_type", "unit_type",
        # ---- экономика ----
        "is_building", "under_construction",
        "food_provided", "accepts_resources",
        "cost_food", "produces",
        "production_queue", "build_target_id",
        "footprint",
        "gather_deposits",
    )

    def __init__(self, id, type_id, owner, pos, hp,
                 max_hp=None, speed=1.0, vision=5.0, radius=0.4,
                 attack=0, attack_speed=1.0, attack_type="normal",
                 attack_range=0.0, armor=0, armor_type="light",
                 unit_type="ground"):
        # type: (...) -> None
        self.id = id
        self.type_id = type_id
        self.owner = owner
        self.pos = [float(pos[0]), float(pos[1])]
        self.vel = [0.0, 0.0]
        self.hp = int(hp)
        self.max_hp = int(max_hp if max_hp is not None else hp)
        self.state = EntityState.IDLE
        self.orders = deque()
        self.target_pos = None
        self.target_entity_id = None
        self.cooldowns = {}
        self.speed = float(speed)
        self.vision = float(vision)
        self.radius = float(radius)
        self.attack = int(attack)
        self.attack_speed = float(attack_speed)
        self.attack_type = attack_type
        self.attack_range = float(attack_range)
        self.armor = int(armor)
        self.armor_type = armor_type
        self.unit_type = unit_type
        # экономика
        self.is_building = False
        self.under_construction = False
        self.food_provided = 0
        self.accepts_resources = False
        self.cost_food = 0
        self.produces = []               # type: list
        self.production_queue = []       # type: list
        self.build_target_id = None      # type: object
        self.footprint = [1, 1]
        self.gather_deposits = 0

    # kill(): добавь forward-compat очистку очереди производства
    def kill(self):
        self.hp = 0
        self.state = EntityState.DEAD
        self.orders.clear()
        self.target_pos = None
        self.target_entity_id = None
        self.vel[0] = 0.0
        self.vel[1] = 0.0
        q = getattr(self, "production_queue", None)
        if q is not None:
            q.clear()


    # ---------- статус ----------

    @property
    def alive(self):
        return self.hp > 0 and self.state is not EntityState.DEAD

    def kill(self):
        self.hp = 0
        self.state = EntityState.DEAD
        self.orders.clear()
        self.target_pos = None
        self.target_entity_id = None
        self.vel[0] = 0.0
        self.vel[1] = 0.0

    # ---------- приказы ----------

    def set_order(self, order):
        self.orders.clear()
        self.orders.append(order)

    def push_order(self, order):
        self.orders.append(order)

    def clear_orders(self):
        self.orders.clear()
        self.target_pos = None
        self.target_entity_id = None
        self.vel[0] = 0.0
        self.vel[1] = 0.0
        if self.alive:
            self.state = EntityState.IDLE

    # ---------- тик ----------

    def update(self, world, dt):
        # type: (object, float) -> None
        if not self.alive:
            return

        for k in list(self.cooldowns):
            self.cooldowns[k] -= dt
            if self.cooldowns[k] <= 0.0:
                del self.cooldowns[k]

        while self.orders:
            order = self.orders[0]
            done = order.tick(self, world, dt)
            if done:
                self.orders.popleft()
            else:
                break

        if not self.orders:
            if self.state is EntityState.MOVING:
                self.state = EntityState.IDLE
            if self.state is EntityState.IDLE:
                self.target_pos = None
                self.vel[0] = 0.0
                self.vel[1] = 0.0

    # ---------- сериализация ----------

    def to_dict(self):
        d = {
            "id": self.id,
            "type_id": self.type_id,
            "owner": self.owner,
            "pos": [round(self.pos[0], 3), round(self.pos[1], 3)],
            "vel": [round(self.vel[0], 3), round(self.vel[1], 3)],
            "hp": self.hp,
            "max_hp": self.max_hp,
            "state": self.state.value,
            "target_pos": (
                [round(self.target_pos[0], 3), round(self.target_pos[1], 3)]
                if self.target_pos else None
            ),
        }
        if self.is_building:
            d["is_building"] = True
            d["under_construction"] = bool(self.under_construction)
            d["footprint"] = list(self.footprint) if self.footprint else [1, 1]
            d["food_provided"] = int(self.food_provided)
            d["accepts_resources"] = bool(self.accepts_resources)
            d["produces"] = list(self.produces)
            d["production_queue"] = [
                {"unit_type": it.unit_type,
                 "time_left": round(it.time_left, 2),
                 "time_total": round(it.time_total, 2)}
                for it in self.production_queue
            ]
        return d

    def __repr__(self):
        return "<Entity id=%s %s owner=%s hp=%s>" % (
            self.id, self.type_id, self.owner, self.hp
        )
