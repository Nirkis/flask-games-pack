"""Добыча ресурсов рабочими. Python 3.6."""
import math

from .entity import EntityState
from .order import PathTracker


class ResourceNode(object):
    __slots__ = ("id", "type", "pos", "amount", "max_amount", "radius")

    def __init__(self, id, type, pos, amount, radius=1.2):
        # type: (int, str, tuple, int, float) -> None
        self.id = id
        self.type = type
        self.pos = [float(pos[0]), float(pos[1])]
        self.amount = int(amount)
        self.max_amount = int(amount)
        self.radius = float(radius)

    @property
    def depleted(self):
        return self.amount <= 0

    def to_dict(self):
        return {
            "id": self.id,
            "type": self.type,
            "pos": [self.pos[0], self.pos[1]],
            "amount": self.amount,
            "max_amount": self.max_amount,
        }


# сколько ресурса за одну «ходку»
def _per_trip(world, resource):
    # type: (object, str) -> int
    rg = world.rules.get("resource_gather", {}) or {}
    if resource == "wood":
        return int(rg.get("wood_per_trip", 10))
    return int(rg.get("gold_per_trip", 10))


def _resource_for_node(node):
    # type: (ResourceNode) -> str
    if node.type in ("gold_mine", "gold"):
        return "gold"
    return "wood"


def _harvest_seconds(world):
    # «копает 3 сек» из ТЗ. В rules.json лежит trip_time_sec=2.5 — не используем,
    # чтобы не расходиться с ТЗ (это таймаут 'ходки', а не такт добычи).
    return 3.0


def find_nearest_depot(world, owner, pos):
    """Ближайшее готовое здание, принимающее ресурсы."""
    best = None
    best_d2 = float("inf")
    px, py = pos[0], pos[1]
    for e in world.entities.values():
        if not e.alive:
            continue
        if e.owner != owner:
            continue
        if not getattr(e, "is_building", False):
            continue
        if getattr(e, "under_construction", False):
            continue
        if not getattr(e, "accepts_resources", False):
            continue
        dx = e.pos[0] - px
        dy = e.pos[1] - py
        d2 = dx * dx + dy * dy
        if d2 < best_d2:
            best_d2 = d2
            best = e
    return best


class GatherOrder(object):
    """
    Циклический приказ: рабочего — между шахтой и базой.
    Фазы: MOVE_TO_NODE → HARVEST → MOVE_TO_DEPOT → DEPOSIT → MOVE_TO_NODE ...
    """
    PHASE_TO_NODE = 0
    PHASE_HARVEST = 1
    PHASE_TO_DEPOT = 2
    PHASE_DEPOSIT = 3

    def __init__(self, node_id, pathfinder=None, flowfield_pool=None,
                 unit_type="worker"):
        # type: (int, object, object, str) -> None
        self.node_id = int(node_id)
        self.pathfinder = pathfinder
        self.flowfield_pool = flowfield_pool
        self.unit_type = unit_type
        self.phase = self.PHASE_TO_NODE
        self.timer = 0.0
        self.carrying = 0
        self.carrying_resource = "gold"
        self.deposits = 0                # сколько раз успешно отдал ресурсы
        self._tracker = PathTracker(pathfinder, flowfield_pool, unit_type)

    # ---------- внутреннее ----------

    def _fresh_tracker(self, target):
        self._tracker.reset()
        self._tracker.set_target(target)
        return self._tracker

    # ---------- основной цикл ----------

    def tick(self, entity, world, dt):
        if not entity.alive:
            return True

        node = world.resource_nodes.get(self.node_id)
        if node is None or node.depleted:
            entity.state = EntityState.IDLE
            return True

        if self.phase == self.PHASE_TO_NODE:
            entity.state = EntityState.MOVING
            t = self._fresh_tracker(node.pos) if not self._tracker._target \
                else self._tracker
            t.set_target(node.pos)
            arrived = t.step(entity, world, 1.5, dt)
            if arrived:
                self.phase = self.PHASE_HARVEST
                self.timer = _harvest_seconds(world)
                entity.state = EntityState.GATHERING
                entity.vel[0] = 0.0
                entity.vel[1] = 0.0
            return False

        if self.phase == self.PHASE_HARVEST:
            entity.state = EntityState.GATHERING
            entity.vel[0] = 0.0
            entity.vel[1] = 0.0
            self.timer -= dt
            if self.timer <= 0.0:
                res_kind = _resource_for_node(node)
                self.carrying_resource = res_kind
                got = min(_per_trip(world, res_kind), node.amount)
                node.amount -= got
                self.carrying = got
                self.phase = self.PHASE_TO_DEPOT
            return False

        if self.phase == self.PHASE_TO_DEPOT:
            depot = find_nearest_depot(world, entity.owner, entity.pos)
            if depot is None:
                entity.state = EntityState.IDLE
                return True
            entity.state = EntityState.MOVING
            t = self._fresh_tracker(depot.pos)
            arrived = t.step(entity, world, 1.5, dt)
            if arrived:
                self.phase = self.PHASE_DEPOSIT
            return False

        if self.phase == self.PHASE_DEPOSIT:
            res = world.resources.setdefault(entity.owner,
                                             {"gold": 0, "wood": 0})
            key = self.carrying_resource or "gold"
            res[key] = int(res.get(key, 0)) + int(self.carrying)
            self.carrying = 0
            self.deposits += 1
            entity.gather_deposits = getattr(entity, "gather_deposits", 0) + 1
            self.phase = self.PHASE_TO_NODE
            self._tracker.reset()
            return False

        return True
