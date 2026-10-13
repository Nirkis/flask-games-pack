"""Приказы: движение, атака, патруль, удержание. Python 3.6."""
import math
from typing import List, Optional, Tuple

from . import combat, steering
from .entity import EntityState


# ---------- общий трекер пути ----------

class PathTracker(object):
    """
    Управляет A*/flow field и кулдауном перестроения пути для одной цели.
    """
    __slots__ = ("pathfinder", "flowfield_pool", "unit_type",
                 "path", "path_index", "use_flowfield",
                 "_last_pos", "_stuck_time", "_target")

    def __init__(self, pathfinder=None, flowfield_pool=None, unit_type="ground"):
        # type: (object, object, str) -> None
        self.pathfinder = pathfinder
        self.flowfield_pool = flowfield_pool
        self.unit_type = unit_type
        self.path = []                  # type: List[Tuple[float, float]]
        self.path_index = 0
        self.use_flowfield = False
        self._last_pos = None           # type: Optional[Tuple[float, float]]
        self._stuck_time = 0.0
        self._target = None             # type: Optional[List[float]]

    def set_target(self, target):
        # type: (Tuple[float, float]) -> None
        t = [float(target[0]), float(target[1])]
        if self._target is None:
            self._target = t
            self.path = []
            self.path_index = 0
            return
        dx = t[0] - self._target[0]
        dy = t[1] - self._target[1]
        moved_far = (dx * dx + dy * dy) > 0.25    # > 0.5 тайла
        self._target = t
        if moved_far:
            self.path = []
            self.path_index = 0


    def reset(self):
        self.path = []
        self.path_index = 0
        self._last_pos = None
        self._stuck_time = 0.0
        self._target = None
        self.use_flowfield = False


    def _compute_path(self, entity, world):
        target = self._target
        if target is None:
            return
        if self.flowfield_pool is not None:
            ff = self.flowfield_pool.get(target, self.unit_type)
            if ff.has_path(entity.pos):
                self.path = [(target[0], target[1])]
                self.path_index = 0
                self.use_flowfield = True
                return
        if self.pathfinder is not None:
            p = self.pathfinder.find_path(entity.pos, target, self.unit_type)
            if p:
                self.path = list(p)
                self.path_index = 0
                self.use_flowfield = False
                return
        self.path = [(target[0], target[1])]
        self.path_index = 0
        self.use_flowfield = False

    def step(self, entity, world, arrive_radius, dt):
        # type: (object, object, float, float) -> bool
        """Возвращает True, если достигли окрестности цели."""
        if self._target is None:
            return True
        if not self.path:
            self._compute_path(entity, world)

        # stuck-детект
        lp = self._last_pos
        if lp is not None:
            dx = entity.pos[0] - lp[0]
            dy = entity.pos[1] - lp[1]
            if dx * dx + dy * dy < 4e-4:
                self._stuck_time += dt
            else:
                self._stuck_time = 0.0
        self._last_pos = (entity.pos[0], entity.pos[1])

        if self._stuck_time > 1.0:
            self.path = []
            self.path_index = 0
            self._stuck_time = 0.0
            self._compute_path(entity, world)

        # проверка прибытия
        dx = self._target[0] - entity.pos[0]
        dy = self._target[1] - entity.pos[1]
        if dx * dx + dy * dy <= arrive_radius * arrive_radius:
            entity.vel[0] = 0.0
            entity.vel[1] = 0.0
            entity.target_pos = None
            entity.state = EntityState.IDLE
            return True

        grid = self.pathfinder.grid if self.pathfinder is not None else None

        if self.use_flowfield and self.flowfield_pool is not None:
            ff = self.flowfield_pool.get(self._target, self.unit_type)
            d = ff.direction_at(int(math.floor(entity.pos[0])),
                                int(math.floor(entity.pos[1])))
            if d[0] == 0.0 and d[1] == 0.0:
                dist = math.sqrt(dx * dx + dy * dy)
                if dist < 1e-6:
                    entity.state = EntityState.IDLE
                    return True
                d = (dx / dist, dy / dist)
            steering.follow_direction(entity, world, d, dt,
                                      self.unit_type, grid)
            entity.state = EntityState.MOVING
            entity.target_pos = list(self._target)
            return False

        new_idx, arrived = steering.follow_path(
            entity, world, self.path, self.path_index, dt,
            self.unit_type, grid,
        )
        self.path_index = new_idx
        if arrived:
            entity.vel[0] = 0.0
            entity.vel[1] = 0.0
            entity.target_pos = None
            entity.state = EntityState.IDLE
            return True
        entity.state = EntityState.MOVING
        entity.target_pos = list(self._target)
        return False


# ---------- базовый класс ----------

class Order(object):
    __slots__ = ()

    def tick(self, entity, world, dt):
        # type: (object, object, float) -> bool
        raise NotImplementedError


# ---------- движение ----------

class MoveOrder(Order):
    __slots__ = ("_tracker",)

    def __init__(self, target, pathfinder=None, flowfield_pool=None,
                 unit_type="ground"):
        # type: (Tuple[float, float], object, object, str) -> None
        self._tracker = PathTracker(pathfinder, flowfield_pool, unit_type)
        self._tracker.set_target(target)

    def tick(self, entity, world, dt):
        if not entity.alive:
            return True
        entity.target_entity_id = None
        return self._tracker.step(entity, world, 0.4, dt)


# ---------- атака ----------

class AttackOrder(Order):
    __slots__ = ("target_id", "_tracker", "_elapsed")

    def __init__(self, target_id, pathfinder=None, flowfield_pool=None,
                 unit_type="ground"):
        # type: (int, object, object, str) -> None
        self.target_id = int(target_id)
        self._tracker = PathTracker(pathfinder, flowfield_pool, unit_type)
        self._elapsed = 0.0

    def tick(self, entity, world, dt):
        if not entity.alive:
            return True
        self._elapsed += dt
        if self._elapsed > 60.0:
            entity.target_entity_id = None
            return True

        target = world.entities.get(self.target_id)
        if target is None or not target.alive:
            entity.target_entity_id = None
            return True

        if combat.in_attack_range(entity, target):
            entity.state = EntityState.ATTACKING
            entity.target_entity_id = self.target_id
            entity.vel[0] = 0.0
            entity.vel[1] = 0.0
            combat.apply_attack(entity, target, world)
            return not target.alive

        # подойти на дистанцию атаки
        rng = float(getattr(entity, "attack_range", 0.0))
        arrive = max(0.05, rng * 0.9)
        self._tracker.set_target((target.pos[0], target.pos[1]))
        arrived = self._tracker.step(entity, world, arrive, dt)
        entity.state = EntityState.MOVING
        entity.target_entity_id = self.target_id
        if arrived and not combat.in_attack_range(entity, target):
            # не смогли подойти (цель за стеной и т.п.)
            return True
        return False


class AttackMoveOrder(Order):
    __slots__ = ("point", "_tracker", "unit_type")

    def __init__(self, point, pathfinder=None, flowfield_pool=None,
                 unit_type="ground"):
        self.point = [float(point[0]), float(point[1])]
        self._tracker = PathTracker(pathfinder, flowfield_pool, unit_type)
        self._tracker.set_target(self.point)
        self.unit_type = unit_type

    def tick(self, entity, world, dt):
        if not entity.alive:
            return True

        vision = float(getattr(entity, "vision", 0.0))
        target = combat.find_target(entity, world, radius=vision)
        if target is not None:
            if combat.in_attack_range(entity, target):
                entity.state = EntityState.ATTACKING
                entity.target_entity_id = target.id
                entity.vel[0] = 0.0
                entity.vel[1] = 0.0
                combat.apply_attack(entity, target, world)
                return False
            rng = float(getattr(entity, "attack_range", 0.0))
            self._tracker.set_target((target.pos[0], target.pos[1]))
            entity.state = EntityState.MOVING
            entity.target_entity_id = target.id
            self._tracker.step(entity, world, max(0.05, rng * 0.9), dt)
            return False

        entity.target_entity_id = None
        self._tracker.set_target(self.point)
        return self._tracker.step(entity, world, 0.4, dt)


# ---------- патруль ----------

class PatrolOrder(Order):
    __slots__ = ("p1", "p2", "_tracker", "_leg", "unit_type")

    def __init__(self, p1, p2, pathfinder=None, flowfield_pool=None,
                 unit_type="ground"):
        self.p1 = [float(p1[0]), float(p1[1])]
        self.p2 = [float(p2[0]), float(p2[1])]
        self._tracker = PathTracker(pathfinder, flowfield_pool, unit_type)
        self._tracker.set_target(self.p2)
        self._leg = 0        # 0 → идём к p2, 1 → идём к p1
        self.unit_type = unit_type

    def tick(self, entity, world, dt):
        if not entity.alive:
            return False    # патруль бесконечен, пока его не отменят

        vision = float(getattr(entity, "vision", 0.0))
        target = combat.find_target(entity, world, radius=vision)
        if target is not None:
            if combat.in_attack_range(entity, target):
                entity.state = EntityState.ATTACKING
                entity.target_entity_id = target.id
                combat.apply_attack(entity, target, world)
                return False
            rng = float(getattr(entity, "attack_range", 0.0))
            self._tracker.set_target((target.pos[0], target.pos[1]))
            entity.state = EntityState.MOVING
            entity.target_entity_id = target.id
            self._tracker.step(entity, world, max(0.05, rng * 0.9), dt)
            return False

        entity.target_entity_id = None
        dest = self.p2 if self._leg == 0 else self.p1
        self._tracker.set_target(dest)
        arrived = self._tracker.step(entity, world, 0.4, dt)
        if arrived:
            self._leg ^= 1
        return False


# ---------- удержание ----------

class HoldOrder(Order):
    __slots__ = ()

    def tick(self, entity, world, dt):
        if not entity.alive:
            return True
        entity.vel[0] = 0.0
        entity.vel[1] = 0.0
        entity.target_pos = None
        entity.target_entity_id = None

        tgt = combat.find_target(entity, world)
        if tgt is not None:
            entity.state = EntityState.ATTACKING
            combat.apply_attack(entity, tgt, world)
        else:
            entity.state = EntityState.IDLE
        return False        # держимся, пока не отменят


# ---------- стоп ----------

class StopOrder(Order):
    __slots__ = ()

    def tick(self, entity, world, dt):
        entity.vel[0] = 0.0
        entity.vel[1] = 0.0
        entity.target_pos = None
        entity.target_entity_id = None
        entity.state = EntityState.IDLE
        return True


class RepairOrder(Order):
    """
    Ремонт/достройка своего здания. Работает для:
      - under_construction=True → присоединяется как builder
      - hp < max_hp → восстанавливает 5% max_hp/сек
      - всё в порядке → завершается
    """
    __slots__ = ("target_id", "_tracker", "unit_type")

    def __init__(self, target_id, pathfinder=None, flowfield_pool=None,
                 unit_type="worker"):
        self.target_id = int(target_id)
        self._tracker = PathTracker(pathfinder, flowfield_pool, unit_type)
        self.unit_type = unit_type

    def tick(self, entity, world, dt):
        if not entity.alive:
            return True

        target = world.entities.get(self.target_id)
        if target is None or not target.alive:
            entity.state = EntityState.IDLE
            return True

        dx = target.pos[0] - entity.pos[0]
        dy = target.pos[1] - entity.pos[1]
        d2 = dx * dx + dy * dy
        if d2 > 2.5 * 2.5:
            self._tracker.set_target((target.pos[0], target.pos[1]))
            self._tracker.step(entity, world, 2.0, dt)
            entity.state = EntityState.MOVING
            return False

        entity.vel[0] = 0.0
        entity.vel[1] = 0.0

        if getattr(target, "under_construction", False):
            entity.state = EntityState.BUILDING
            entity.build_target_id = target.id
            return False

        if target.hp < target.max_hp:
            entity.state = EntityState.BUILDING
            heal = max(1, int(target.max_hp * 0.05 * dt))
            target.hp = min(target.max_hp, target.hp + heal)
            return False

        entity.state = EntityState.IDLE
        return True
