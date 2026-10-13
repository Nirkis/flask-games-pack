"""Демо: рабочий добывает золото → строится казарма → выходит юнит.
Запуск: python -m games.rts.demo_economy
"""
from games.rts.sim.build import CreateBuildingOrder
from games.rts.sim.entity import Entity
from games.rts.sim.gather import GatherOrder, ResourceNode
from games.rts.sim.grid import Grid
from games.rts.sim.pathfinder import Pathfinder
from games.rts.sim.production import enqueue
from games.rts.sim.world import World


UNITS = {
    "worker": {
        "hp": 220, "speed": 2.4, "vision": 7, "radius": 0.4,
        "attack": 8, "attack_speed": 1.5, "attack_type": "normal",
        "range": 0.8, "armor": 0, "armor_type": "light",
        "cost": {"gold": 60, "wood": 0, "food": 1}, "build_time": 12.0,
    },
    "footman": {
        "hp": 420, "speed": 2.4, "vision": 8, "radius": 0.4,
        "attack": 18, "attack_speed": 1.2, "attack_type": "normal",
        "range": 1.0, "armor": 2, "armor_type": "heavy",
        "cost": {"gold": 100, "wood": 0, "food": 2}, "build_time": 8.0,
    },
}

BUILDINGS = {
    "town_hall": {
        "hp": 1500, "armor": 5, "armor_type": "fortified",
        "cost": {"gold": 300, "wood": 100}, "build_time": 60.0,
        "footprint": [2, 2], "vision": 10,
        "produces": ["worker"], "food_provided": 10,
        "accepts_resources": True, "is_main": True,
    },
    "barracks": {
        "hp": 1200, "armor": 4, "armor_type": "fortified",
        "cost": {"gold": 150, "wood": 50}, "build_time": 40.0,
        "footprint": [2, 2], "vision": 8,
        "produces": ["footman"], "food_provided": 0,
        "accepts_resources": False, "is_main": False,
    },
}


def _spawn_building(world, btype, owner, pos):
    d = BUILDINGS[btype]
    b = Entity(None, btype, owner, pos, hp=int(d["hp"]),
               speed=0.0, vision=float(d["vision"]),
               radius=float(d["footprint"][0]) * 0.5,
               armor=int(d["armor"]), armor_type=str(d["armor_type"]))
    b.is_building = True
    b.under_construction = False
    b.food_provided = int(d.get("food_provided", 0))
    b.accepts_resources = bool(d.get("accepts_resources", False))
    b.produces = list(d.get("produces", []))
    b.footprint = list(d["footprint"])
    return world.spawn(b)


def _spawn_unit(world, uid, owner, pos):
    d = UNITS[uid]
    e = Entity(None, uid, owner, pos, hp=int(d["hp"]),
               speed=float(d["speed"]), vision=float(d["vision"]),
               radius=float(d["radius"]),
               attack=int(d["attack"]), attack_speed=float(d["attack_speed"]),
               attack_type=str(d["attack_type"]),
               attack_range=float(d["range"]),
               armor=int(d["armor"]), armor_type=str(d["armor_type"]))
    e.cost_food = int(d["cost"]["food"])
    return world.spawn(e)


def main():
    grid = Grid(32, 32, [0] * (32 * 32))
    pf = Pathfinder(grid)

    world = World({"tick_rate": 10}, UNITS)
    world.grid = grid
    world.buildings_data = BUILDINGS
    world.resources[0] = {"gold": 500, "wood": 200,
                          "food_used": 0, "food_cap": 10}
    world.load_resource_nodes({
        "resource_nodes": [
            {"type": "gold_mine", "pos": [12, 4], "amount": 5000},
        ],
    })

    hall_id = _spawn_building(world, "town_hall", 0, (2.0, 2.0))
    worker_id = _spawn_unit(world, "worker", 0, (6.0, 6.0))
    worker = world.entities[worker_id]

    print("=== ECONOMY DEMO ===")
    print("Town Hall #%d at (2,2), Worker #%d at (6,6)"
          % (hall_id, worker_id))
    print("Gold mine node #1 at (12,4), 5000 gold")
    print()

    # --- 1. Send worker to mine ---
    worker.set_order(GatherOrder(1, pathfinder=pf))
    print("Order: GatherOrder(node=1)")
    for tick in range(200):
        world.step()
        if worker.gather_deposits >= 2:
            break
    print("After %d ticks: gold=%d wood=%d food=%d/%d  worker deposits=%d"
          % (tick, world.resources[0]["gold"], world.resources[0]["wood"],
             world.resources[0]["food_used"], world.resources[0]["food_cap"],
             worker.gather_deposits))
    print()

    # --- 2. Build barracks ---
    print("Order: CreateBuildingOrder(barracks, (10,10))")
    worker.set_order(CreateBuildingOrder("barracks", (10.0, 10.0),
                                          pathfinder=pf))
    for tick in range(500):
        world.step()
        # приказ завершён (или провален) — выходим
        if not worker.orders:
            break
        # или стройка уже дошла до 100 %
        barracks = next((e for e in world.entities.values()
                         if e.type_id == "barracks"), None)
        if barracks is not None and not barracks.under_construction:
            break

    barracks = next((e for e in world.entities.values()
                     if e.type_id == "barracks"), None)
    if barracks is None:
        print("BUILD FAILED: no barracks.")
        return 1
    print("Barracks #%d hp=%d/%d under_construction=%s (built in ~%d ticks)"
          % (barracks.id, barracks.hp, barracks.max_hp,
             barracks.under_construction, tick))

    # --- 3. Train a footman ---
    print("Order: train(footman) at barracks #%d" % barracks.id)
    ok = enqueue(barracks, "footman", world)
    print("  enqueue -> %s ; queue len=%d"
          % (ok, len(barracks.production_queue)))
    for tick in range(200):
        world.step()
        n_footmen = sum(1 for e in world.entities.values()
                        if e.type_id == "footman" and e.owner == 0)
        if n_footmen >= 1:
            break
    n_footmen = sum(1 for e in world.entities.values()
                    if e.type_id == "footman" and e.owner == 0)
    print("After %d ticks: footmen=%d food_used=%d/%d gold=%d"
          % (tick, n_footmen,
             world.resources[0]["food_used"], world.resources[0]["food_cap"],
             world.resources[0]["gold"]))
    print()
    print("=== DONE ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
