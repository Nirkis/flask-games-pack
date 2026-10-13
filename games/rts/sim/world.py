"""Мир: контейнер сущностей и глобальных ресурсов. Python 3.6."""
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from .entity import Entity


class World(object):
    def __init__(self, rules, units_data):
        # type: (dict, dict) -> None
        self.rules = rules or {}
        self.units_data = units_data or {}
        self.projectiles = []
        # grid — заглушка, появится в pathfinding-промпте
        self.grid = None

        self.entities = {}          # type: Dict[int, Entity]
        self.next_id = 1            # type: int
        self.tick_count = 0         # type: int

        # resources[player_index] = {"gold": int, "wood": int}
        self.resources = {}         # type: Dict[int, Dict[str, int]]
        self.resource_nodes = {}  # type: Dict[int, object]
        self.buildings_data = {}  # type: Dict[str, dict]
        self._next_node_id = 1
        self.abilities_data = {}

    # ---------- жизненный цикл ----------

    def spawn(self, entity):
        # type: (Entity) -> int
        eid = self.next_id
        self.next_id += 1
        entity.id = eid
        self.entities[eid] = entity
        return eid

    def despawn(self, eid):
        # type: (int) -> None
        self.entities.pop(eid, None)

    # ---------- тик ----------

    def step(self):
        # type: () -> None
        from .combat import auto_attack_pass
        from .build import tick_construction
        from .production import tick_production, recalc_food

        dt = 1.0 / max(1, int(self.rules.get("tick_rate", 10)))
        self.tick_count += 1

        # сбросить регистрацию строителей — их проставят BuildOrder-ы
        for e in self.entities.values():
            e.build_target_id = None

        # приказы и обновление сущностей
        for e in list(self.entities.values()):
            e.update(self, dt)

        # строительство
        tick_construction(self)

        # производство
        tick_production(self, dt)

        # снаряды
        if self.projectiles:
            alive = []
            for p in self.projectiles:
                if p.update(self, dt):
                    alive.append(p)
            self.projectiles = alive

        # авто-атака IDLE-юнитов
        auto_attack_pass(self)

        # food cap/used
        recalc_food(self)

        # убрать мёртвых
        dead = [eid for eid, e in self.entities.items() if not e.alive]
        for eid in dead:
            del self.entities[eid]

        # истощённые узлы — исчезают
        depleted = [nid for nid, n in self.resource_nodes.items() if n.depleted]
        for nid in depleted:
            del self.resource_nodes[nid]

    # ---------- запросы ----------

    def get_entities_in_radius(self, pos, r):
        # type: (Tuple[float, float], float) -> List[Entity]
        r2 = float(r) * float(r)
        px, py = float(pos[0]), float(pos[1])
        out = []                    # type: List[Entity]
        for e in self.entities.values():
            dx = e.pos[0] - px
            dy = e.pos[1] - py
            if dx * dx + dy * dy <= r2:
                out.append(e)
        return out

    def find_nearest(self, pos, predicate=None, exclude=None):
        # type: (Tuple[float, float], Optional[Callable[[Entity], bool]], Optional[Iterable[int]]) -> Optional[Entity]
        px, py = float(pos[0]), float(pos[1])
        skip = set(exclude) if exclude else set()
        best = None                 # type: Optional[Entity]
        best_d2 = float("inf")
        for e in self.entities.values():
            if e.id in skip:
                continue
            if predicate is not None and not predicate(e):
                continue
            dx = e.pos[0] - px
            dy = e.pos[1] - py
            d2 = dx * dx + dy * dy
            if d2 < best_d2:
                best_d2 = d2
                best = e
        return best

    def load_resource_nodes(self, map_data):
        # type: (dict) -> None
        from .gather import ResourceNode
        for raw in map_data.get("resource_nodes", []):
            nid = self._next_node_id
            self._next_node_id += 1
            self.resource_nodes[nid] = ResourceNode(
                id=nid,
                type=raw.get("type", "gold_mine"),
                pos=raw.get("pos", [0, 0]),
                amount=int(raw.get("amount", 5000)),
            )
