"""Runtime: очередь команд + tick(). Без сети и Flask. Python 3.6."""
import threading
import time
from collections import deque
from typing import Dict, Optional

from .entity import Entity
from .order import MoveOrder, StopOrder
from .world import World


class GameRuntime(object):
    def __init__(self, rules, units_data, buildings_data=None, grid=None, world=None):
        # type: (dict, dict, dict, object, object) -> None
        self.rules = rules or {}
        self.units_data = units_data or {}
        self.buildings_data = buildings_data or {}
        if world is not None:
            self.world = world
        else:
            self.world = World(self.rules, self.units_data)
            self.world.buildings_data = self.buildings_data
            self.world.grid = grid
        self.tick_rate = int(self.rules.get("tick_rate", 10))
        self.started_at = time.time()

        self._commands = deque()
        self._lock = threading.RLock()
        self._alive_check = None

        self.grid = grid
        if grid is not None:
            from .pathfinder import Pathfinder
            from .flowfield import FlowFieldPool
            self.pathfinder = Pathfinder(grid)
            self.flowfield_pool = FlowFieldPool(grid)
        else:
            self.pathfinder = None
            self.flowfield_pool = None

    # ---------- внешний API ----------

    def enqueue_command(self, player, cmd):
        # type: (int, dict) -> None
        with self._lock:
            self._commands.append((int(player), cmd))

    def set_alive_check(self, fn):
        """fn() -> bool. Пока возвращает False, tick() — no-op (пауза)."""
        self._alive_check = fn

    def tick(self):
        # heartbeat-пауза: если нет живых игроков, мир не двигается
        if self._alive_check is not None and not self._alive_check():
            return

        with self._lock:
            cmds = list(self._commands)
            self._commands.clear()
        for player, cmd in cmds:
            try:
                self._apply(player, cmd)
            except Exception as e:
                print("[rts] command error: %r" % (e,))
        self.world.step()

    # ---------- команды ----------

    def _apply(self, player, cmd):
        if not isinstance(cmd, dict):
            return
        action = cmd.get("action")
        if action == "spawn":
            self._cmd_spawn(player, cmd)
        elif action == "move":
            self._cmd_move(player, cmd)
        elif action == "stop":
            self._cmd_stop(player, cmd)
        elif action == "gather":
            self._cmd_gather(player, cmd)
        elif action == "build":
            self._cmd_build(player, cmd)
        elif action == "train":
            self._cmd_train(player, cmd)
        elif action == "cancel_train":
            self._cmd_cancel_train(player, cmd)
        elif action == "attack":
            self._cmd_attack(player, cmd)
        elif action == "attack_move":
            self._cmd_attack_move(player, cmd)
        elif action == "hold":
            self._cmd_hold(player, cmd)
        elif action == "patrol":
            self._cmd_patrol(player, cmd)
        elif action == "repair":
            self._cmd_repair(player, cmd)
        elif action == "use_ability":
            self._cmd_use_ability(player, cmd)


    def _cmd_spawn(self, player, cmd):
        unit_id = cmd.get("unit")
        if not isinstance(unit_id, str):
            return
        ud = self.units_data.get(unit_id)
        if not ud:
            return
        pos = cmd.get("pos") or [0.0, 0.0]
        if not isinstance(pos, (list, tuple)) or len(pos) != 2:
            return

        cost = ud.get("cost") or {}
        gold = int(cost.get("gold", 0))
        wood = int(cost.get("wood", 0))
        res = self.world.resources.setdefault(player, {"gold": 0, "wood": 0})
        if res.get("gold", 0) < gold or res.get("wood", 0) < wood:
            return
        res["gold"] = res.get("gold", 0) - gold
        res["wood"] = res.get("wood", 0) - wood

        hp = int(ud.get("hp", 1))
        e = Entity(
            id=None,
            type_id=unit_id,
            owner=player,
            pos=[float(pos[0]), float(pos[1])],
            hp=hp,
            max_hp=hp,
            speed=float(ud.get("speed", 1.0)),
            vision=float(ud.get("vision", 5.0)),
            radius=float(ud.get("radius", 0.4)),
            attack=int(ud.get("attack", 0)),
            attack_speed=float(ud.get("attack_speed", 1.0)),
            attack_type=str(ud.get("attack_type", "normal")),
            attack_range=float(ud.get("range", 0.0)),
            armor=int(ud.get("armor", 0)),
            armor_type=str(ud.get("armor_type", "light")),
        )
        self.world.spawn(e)

    def _cmd_move(self, player, cmd):
        ids = cmd.get("ids") or []
        pos = cmd.get("pos")
        if not isinstance(ids, list) or not pos or len(pos) != 2:
            return
        target = [float(pos[0]), float(pos[1])]
        use_ff = len(ids) >= 5
        for raw in ids:
            try:
                eid = int(raw)
            except (TypeError, ValueError):
                continue
            e = self.world.entities.get(eid)
            if not e or e.owner != player or not e.alive:
                continue
            e.set_order(MoveOrder(
                target,
                pathfinder=self.pathfinder,
                flowfield_pool=self.flowfield_pool if use_ff else None,
                unit_type="ground",
            ))

    def _cmd_repair(self, player, cmd):
        from .order import RepairOrder
        ids = cmd.get("ids") or []
        target_id = cmd.get("target_id")
        if not isinstance(ids, list) or target_id is None:
            return
        try:
            target_id = int(target_id)
        except (TypeError, ValueError):
            return
        target = self.world.entities.get(target_id)
        if target is None or not target.alive:
            return
        if target.owner != player:
            return
        if not getattr(target, "is_building", False):
            return
        for raw in ids:
            try:
                eid = int(raw)
            except (TypeError, ValueError):
                continue
            e = self.world.entities.get(eid)
            if not e or e.owner != player or not e.alive:
                continue
            e.set_order(RepairOrder(
                target_id,
                pathfinder=self.pathfinder,
                unit_type=getattr(e, "unit_type", "worker"),
            ))

    def _cmd_stop(self, player, cmd):
        ids = cmd.get("ids") or []
        if not isinstance(ids, list):
            return
        for raw in ids:
            try:
                eid = int(raw)
            except (TypeError, ValueError):
                continue
            e = self.world.entities.get(eid)
            if not e or e.owner != player or not e.alive:
                continue
            e.set_order(StopOrder())

    def _cmd_gather(self, player, cmd):
        from .gather import GatherOrder
        ids = cmd.get("worker_ids") or []
        node_id = cmd.get("node_id")
        if not isinstance(ids, list) or node_id is None:
            return
        node = self.world.resource_nodes.get(int(node_id))
        if node is None or node.depleted:
            return
        for raw in ids:
            try:
                eid = int(raw)
            except (TypeError, ValueError):
                continue
            e = self.world.entities.get(eid)
            if not e or e.owner != player or not e.alive:
                continue
            e.set_order(GatherOrder(
                node.id, pathfinder=self.pathfinder,
                flowfield_pool=self.flowfield_pool, unit_type="worker",
            ))

    def _cmd_build(self, player, cmd):
        from .build import CreateBuildingOrder
        wid = cmd.get("worker_id")
        btype = cmd.get("building")
        pos = cmd.get("pos")
        if wid is None or not isinstance(btype, str) or not pos:
            return
        try:
            eid = int(wid)
        except (TypeError, ValueError):
            return
        e = self.world.entities.get(eid)
        if not e or e.owner != player or not e.alive:
            return
        if not isinstance(pos, (list, tuple)) or len(pos) != 2:
            return
        e.set_order(CreateBuildingOrder(
            btype, (float(pos[0]), float(pos[1])),
            pathfinder=self.pathfinder,
            flowfield_pool=self.flowfield_pool,
            unit_type="worker",
        ))

    def _cmd_train(self, player, cmd):
        from .production import enqueue
        bid = cmd.get("building_id")
        unit = cmd.get("unit")
        if bid is None or not isinstance(unit, str):
            return
        try:
            eid = int(bid)
        except (TypeError, ValueError):
            return
        b = self.world.entities.get(eid)
        if not b or b.owner != player or not b.alive:
            return
        if not getattr(b, "is_building", False):
            return
        enqueue(b, unit, self.world)

    def _cmd_cancel_train(self, player, cmd):
        from .production import cancel
        bid = cmd.get("building_id")
        idx = int(cmd.get("index", 0))
        if bid is None:
            return
        try:
            eid = int(bid)
        except (TypeError, ValueError):
            return
        b = self.world.entities.get(eid)
        if not b or b.owner != player or not b.alive:
            return
        cancel(b, idx, self.world)

    def _cmd_attack(self, player, cmd):
        from .order import AttackOrder
        ids = cmd.get("ids") or []
        target_id = cmd.get("target_id")
        if not isinstance(ids, list) or target_id is None:
            return
        try:
            target_id = int(target_id)
        except (TypeError, ValueError):
            return
        target = self.world.entities.get(target_id)
        if target is None or not target.alive:
            return
        for raw in ids:
            try:
                eid = int(raw)
            except (TypeError, ValueError):
                continue
            e = self.world.entities.get(eid)
            if not e or e.owner != player or not e.alive:
                continue
            # не приказываем атаковать своего
            if target.owner == player:
                continue
            e.set_order(AttackOrder(
                target_id,
                pathfinder=self.pathfinder,
                unit_type=getattr(e, "unit_type", "ground"),
            ))

    def _cmd_attack_move(self, player, cmd):
        from .order import AttackMoveOrder
        ids = cmd.get("ids") or []
        pos = cmd.get("pos")
        if not isinstance(ids, list) or not pos or len(pos) != 2:
            return
        target = [float(pos[0]), float(pos[1])]
        use_ff = len(ids) >= 5
        for raw in ids:
            try:
                eid = int(raw)
            except (TypeError, ValueError):
                continue
            e = self.world.entities.get(eid)
            if not e or e.owner != player or not e.alive:
                continue
            e.set_order(AttackMoveOrder(
                target,
                pathfinder=self.pathfinder,
                flowfield_pool=self.flowfield_pool if use_ff else None,
                unit_type=getattr(e, "unit_type", "ground"),
            ))


    def _cmd_hold(self, player, cmd):
        from .order import HoldOrder
        ids = cmd.get("ids") or []
        if not isinstance(ids, list):
            return
        for raw in ids:
            try:
                eid = int(raw)
            except (TypeError, ValueError):
                continue
            e = self.world.entities.get(eid)
            if not e or e.owner != player or not e.alive:
                continue
            e.set_order(HoldOrder())


    def _cmd_patrol(self, player, cmd):
        from .order import PatrolOrder
        ids = cmd.get("ids") or []
        p1 = cmd.get("p1")
        p2 = cmd.get("p2")
        if not isinstance(ids, list):
            return
        if not p1 or not p2 or len(p1) != 2 or len(p2) != 2:
            return
        a = [float(p1[0]), float(p1[1])]
        b = [float(p2[0]), float(p2[1])]
        use_ff = len(ids) >= 5
        for raw in ids:
            try:
                eid = int(raw)
            except (TypeError, ValueError):
                continue
            e = self.world.entities.get(eid)
            if not e or e.owner != player or not e.alive:
                continue
            e.set_order(PatrolOrder(
                a, b,
                pathfinder=self.pathfinder,
                flowfield_pool=self.flowfield_pool if use_ff else None,
                unit_type=getattr(e, "unit_type", "ground"),
            ))

    def _cmd_use_ability(self, player, cmd):
        """Универсальный диспетчер действий по abilities.json."""
        ability_id = cmd.get("ability")
        ids = cmd.get("ids") or []
        if not isinstance(ability_id, str) or not isinstance(ids, list):
            return

        abilities = self.world.abilities_data or {}
        ab = abilities.get(ability_id)
        if not ab:
            return

        kind = ab.get("kind")
        if kind == "order":
            self._dispatch_order(player, ids, ability_id, cmd)
        elif kind == "menu":
            self._dispatch_menu(player, ids, ability_id, ab, cmd)
        elif kind == "spell":
            return  # ещё не реализовано

    def _dispatch_order(self, player, ids, ability_id, cmd):
        from .order import (MoveOrder, StopOrder, AttackOrder, AttackMoveOrder,
                            HoldOrder, PatrolOrder, RepairOrder)
        from .gather import GatherOrder
        if ability_id == "move":
            self._cmd_move(player, {"ids": ids, "pos": cmd.get("pos")})
        elif ability_id == "stop":
            self._cmd_stop(player, {"ids": ids})
        elif ability_id == "hold":
            self._cmd_hold(player, {"ids": ids})
        elif ability_id == "attack":
            self._cmd_attack(player, {"ids": ids, "target_id": cmd.get("target_id")})
        elif ability_id == "attack_move":
            self._cmd_attack_move(player, {"ids": ids, "pos": cmd.get("pos")})
        elif ability_id == "patrol":
            self._cmd_patrol(player, {"ids": ids, "p1": cmd.get("p1"), "p2": cmd.get("p2")})
        elif ability_id == "gather":
            # у gather ids — это worker_ids, но приведём к единому
            self._cmd_gather(player, {"worker_ids": ids, "node_id": cmd.get("node_id")})
        elif ability_id == "repair":
            self._cmd_repair(player, {"ids": ids, "target_id": cmd.get("target_id")})

    def _dispatch_menu(self, player, ids, ability_id, ab, cmd):
        options = ab.get("options") or []
        opt_id = cmd.get("option")
        if not isinstance(opt_id, str):
            return
        opt = None
        for o in options:
            if o.get("id") == opt_id:
                opt = o
                break
        if opt is None:
            return

        target_kind = opt.get("target")
        cost = opt.get("cost") or {}
        build_time = float(opt.get("build_time", 10))

        if target_kind == "none":
            # найм: одно здание, которое сейчас выделено — ids[0]
            if not ids:
                return
            try:
                bid = int(ids[0])
            except (TypeError, ValueError):
                return
            b = self.world.entities.get(bid)
            if not b or b.owner != player or not b.alive:
                return
            from .production import enqueue
            enqueue(b, opt_id, cost, build_time, self.world)

        elif target_kind == "point":
            # стройка: у рабочего ids — это сам рабочий
            pos = cmd.get("pos")
            if not pos or len(pos) != 2:
                return
            from .build import CreateBuildingOrder
            for raw in ids:
                try:
                    wid = int(raw)
                except (TypeError, ValueError):
                    continue
                w = self.world.entities.get(wid)
                if not w or w.owner != player or not w.alive:
                    continue
                w.set_order(CreateBuildingOrder(
                    opt_id, (float(pos[0]), float(pos[1])),
                    cost=cost,
                    build_time=build_time,
                    pathfinder=self.pathfinder,
                    unit_type=getattr(w, "unit_type", "worker"),
                ))
