"""Строительство зданий. Python 3.6."""
from .entity import Entity, EntityState
from .order import PathTracker


# HP фундамента в долях от финального
FOUNDATION_HP_FRACTION = 0.10
# Прирост HP за такт (1 % максимума) с учётом числа рабочих
CONSTRUCTION_RATE_PER_TICK = 0.01
# Бонус каждого дополнительного рабочего
EXTRA_WORKER_BONUS = 0.50


class CreateBuildingOrder(object):
    """
    Заказ на постройку. Ставится рабочему как order.

    Фазы:
        INIT   — валидация + списание ресурсов + спавн фундамента
        MOVE   — рабочий идёт к площадке
        BUILD  — рабочий стоит у фундамента и «строит»
    """
    INIT = 0
    MOVE = 1
    BUILD = 2

    def __init__(self, building_type, pos, cost=None, build_time=None,
                 pathfinder=None, flowfield_pool=None, unit_type="worker"):
        self.building_type = building_type
        self.pos = [float(pos[0]), float(pos[1])]
        self.cost = dict(cost or {})
        self.build_time = float(build_time or 40.0)
        self.pathfinder = pathfinder
        self.flowfield_pool = flowfield_pool
        self.unit_type = unit_type

        self.phase = self.INIT
        self.foundation_id = None       # type: object
        self._tracker = PathTracker(pathfinder, flowfield_pool, unit_type)

    # ---------- валидация места ----------

    def _footprint_tiles(self, fp, cx, cy):
        # type: (list, int, int) -> list
        w, h = int(fp[0]), int(fp[1])
        x0 = cx - w // 2
        y0 = cy - h // 2
        tiles = []
        for y in range(y0, y0 + h):
            for x in range(x0, x0 + w):
                tiles.append((x, y))
        return tiles

    def _can_place(self, world, entity):
        bdata = world.buildings_data.get(self.building_type)
        if not bdata:
            return False, "unknown building"
        grid = world.grid
        fp = bdata.get("footprint", [1, 1])
        cx = int(self.pos[0])
        cy = int(self.pos[1])
        tiles = self._footprint_tiles(fp, cx, cy)

        # все тайлы должны быть проходимы (не вода, не скала, не занято)
        for (tx, ty) in tiles:
            if grid is not None and not grid.is_passable(tx, ty, "ground"):
                return False, "blocked tile"
            # занято другим зданием?
            for e in world.entities.values():
                if not e.alive or not getattr(e, "is_building", False):
                    continue
                ex, ey = int(e.pos[0]), int(e.pos[1])
                efp = getattr(e, "footprint", [1, 1])
                etiles = self._footprint_tiles(efp, ex, ey)
                if (tx, ty) in etiles:
                    return False, "occupied"
        return True, tiles

    def _try_place(self, entity, world):
        bdata = world.buildings_data.get(self.building_type)
        if not bdata:
            return False

        ok, tiles_or_reason = self._can_place(world, entity)
        if not ok:
            return False

        # ресурсы
        res = world.resources.setdefault(entity.owner, {"gold": 0, "wood": 0})
        gold = int(self.cost.get("gold", 0))
        wood = int(self.cost.get("wood", 0))
        if res.get("gold", 0) < gold or res.get("wood", 0) < wood:
            return False
        res["gold"] = int(res.get("gold", 0)) - gold
        res["wood"] = int(res.get("wood", 0)) - wood

        # спавн фундамента
        max_hp = int(bdata.get("hp", 100))
        fp = bdata.get("footprint", [1, 1])
        cx = int(self.pos[0])
        cy = int(self.pos[1])
        building = Entity(
            id=None,
            type_id=self.building_type,
            owner=entity.owner,
            pos=[float(cx), float(cy)],
            hp=max(1, int(max_hp * FOUNDATION_HP_FRACTION)),
            max_hp=max_hp,
            speed=0.0,
            vision=float(bdata.get("vision", 0)),
            radius=max(int(fp[0]), int(fp[1])) * 0.5,
            armor=int(bdata.get("armor", 0)),
            armor_type=str(bdata.get("armor_type", "fortified")),
        )
        building.is_building = True
        building.under_construction = True
        building.food_provided = int(bdata.get("food_provided", 0))
        building.accepts_resources = bool(bdata.get("accepts_resources", False))
        building.produces = list(bdata.get("produces", []))
        building.footprint = [int(fp[0]), int(fp[1])]
        self.foundation_id = world.spawn(building)

        # освобождаем тайлы на grid — здание всегда проходимо вокруг,
        # но сами тайлы — заняты (для pathfinding-а это блок).
        # Пока не блокируем: пусть другие здания не пересекаются,
        # но юниты могут проходить (иначе ворота станут лабиринтом).
        return True

    # ---------- основной цикл ----------

    def tick(self, entity, world, dt):
        if not entity.alive:
            return True

        if self.phase == self.INIT:
            ok = self._try_place(entity, world)
            if not ok:
                entity.state = EntityState.IDLE
                return True
            self.phase = self.MOVE
            return False

        foundation = world.entities.get(self.foundation_id) if self.foundation_id else None
        if foundation is None or not foundation.alive:
            entity.state = EntityState.IDLE
            return True

        if self.phase == self.MOVE:
            entity.state = EntityState.MOVING
            self._tracker.set_target(foundation.pos)
            arrived = self._tracker.step(entity, world, 1.5, dt)
            if arrived:
                self.phase = self.BUILD
                entity.state = EntityState.BUILDING
                entity.vel[0] = 0.0
                entity.vel[1] = 0.0
            return False

        if self.phase == self.BUILD:
            if not foundation.under_construction:
                entity.state = EntityState.IDLE
                return True
            entity.state = EntityState.BUILDING
            entity.vel[0] = 0.0
            entity.vel[1] = 0.0
            entity.build_target_id = foundation.id
            return False

        return True


# ---------- глобальный проход: прирост HP фундаментам ----------

def tick_construction(world):
    """Вызывается из World.step() каждый такт."""
    # группируем строителей по фундаменту
    by_foundation = {}
    for e in world.entities.values():
        if not e.alive:
            continue
        fid = getattr(e, "build_target_id", None)
        if fid is None:
            continue
        by_foundation.setdefault(fid, []).append(e)

    for fid, builders in by_foundation.items():
        b = world.entities.get(fid)
        if b is None or not b.alive:
            continue
        if not getattr(b, "under_construction", False):
            continue

        n = len(builders)
        rate = CONSTRUCTION_RATE_PER_TICK * (1.0 + EXTRA_WORKER_BONUS * (n - 1))
        inc = int(b.max_hp * rate)
        if inc < 1:
            inc = 1
        b.hp = min(b.max_hp, b.hp + inc)
        if b.hp >= b.max_hp:
            b.hp = b.max_hp
            b.under_construction = False
            b.state = EntityState.IDLE
