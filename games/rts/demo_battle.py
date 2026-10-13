"""
Демо: 5 vs 5, юниты автоматически находят врага (AttackMove) и дерутся.
Запуск: python -m games.rts.demo_battle
"""
from games.rts.sim import combat
from games.rts.sim.entity import Entity
from games.rts.sim.grid import Grid
from games.rts.sim.order import AttackMoveOrder
from games.rts.sim.pathfinder import Pathfinder
from games.rts.sim.world import World


RULES = {
    "tick_rate": 10,
    "crit_enabled": False,
    "damage_table": {
        "normal": {"light": 1.0, "medium": 1.0, "heavy": 1.0, "fortified": 1.0},
        "pierce": {"light": 2.0, "medium": 0.75, "heavy": 1.0, "fortified": 0.35},
        "siege":  {"light": 1.0, "medium": 0.5, "heavy": 1.0, "fortified": 1.5},
        "magic":  {"light": 1.25, "medium": 0.75, "heavy": 1.0, "fortified": 1.5},
        "chaos":  {"light": 1.0, "medium": 1.0, "heavy": 1.0, "fortified": 1.0},
    },
}

UNIT_TYPES = {
    "footman": {
        "hp": 120, "armor": 2, "armor_type": "heavy",
        "attack": 12, "attack_type": "normal", "attack_speed": 1.5,
        "range": 1.0, "speed": 2.4, "vision": 8.0, "radius": 0.4,
    },
    "rifleman": {
        "hp": 80, "armor": 1, "armor_type": "medium",
        "attack": 15, "attack_type": "pierce", "attack_speed": 1.2,
        "range": 5.0, "speed": 2.2, "vision": 9.0, "radius": 0.35,
    },
}


def make(world, uid, owner, pos):
    d = UNIT_TYPES[uid]
    e = Entity(None, uid, owner, pos, hp=d["hp"],
               speed=d["speed"], vision=d["vision"], radius=d["radius"],
               attack=d["attack"], attack_speed=d["attack_speed"],
               attack_type=d["attack_type"], attack_range=d["range"],
               armor=d["armor"], armor_type=d["armor_type"])
    world.spawn(e)
    return e


def main():
    grid = Grid(30, 30, [0] * 900)
    pf = Pathfinder(grid)
    world = World(RULES, UNIT_TYPES)
    world.grid = grid
    world.resources[0] = {"gold": 0, "wood": 0}
    world.resources[1] = {"gold": 0, "wood": 0}

    blue = [
        make(world, "footman", 0, (6.0, 13.0)),
        make(world, "footman", 0, (6.0, 14.0)),
        make(world, "footman", 0, (6.0, 15.0)),
        make(world, "rifleman", 0, (5.0, 13.5)),
        make(world, "rifleman", 0, (5.0, 14.5)),
    ]
    red = [
        make(world, "footman", 1, (24.0, 13.0)),
        make(world, "footman", 1, (24.0, 14.0)),
        make(world, "footman", 1, (24.0, 15.0)),
        make(world, "rifleman", 1, (25.0, 13.5)),
        make(world, "rifleman", 1, (25.0, 14.5)),
    ]

    for e in blue:
        e.set_order(AttackMoveOrder((24.0, 14.0), pathfinder=pf))
    for e in red:
        e.set_order(AttackMoveOrder((6.0, 14.0), pathfinder=pf))

    prev_alive = {e.id: (e.type_id, e.owner) for e in (blue + red)}
    print("=== BATTLE START (5 vs 5) ===")
    print("Blue (Alliance): 3x footman, 2x rifleman")
    print("Red  (Horde):    3x footman, 2x rifleman")
    print()

    MAX_TICKS = 800
    for tick in range(MAX_TICKS):
        world.step()

        alive_now = {e.id: e for e in world.entities.values() if e.alive}

        # репортим смерть ровно один раз на юнита
        for eid in list(prev_alive.keys()):
            if eid not in alive_now:
                uid, owner = prev_alive.pop(eid)
                side = "Blue" if owner == 0 else "Red"
                print("t=%3d  %s %s #%d falls" % (tick, side, uid, eid))

        if tick % 20 == 0 or tick == 0:
            b_alive = [e for e in alive_now.values() if e.owner == 0]
            r_alive = [e for e in alive_now.values() if e.owner == 1]
            b_hp = sum(e.hp for e in b_alive)
            r_hp = sum(e.hp for e in r_alive)
            print("t=%3d  Blue: %d/5 (%4d hp)   Red: %d/5 (%4d hp)   proj=%d"
                  % (tick, len(b_alive), b_hp, len(r_alive), r_hp,
                     len(world.projectiles)))

        b_alive = [e for e in world.entities.values()
                   if e.alive and e.owner == 0]
        r_alive = [e for e in world.entities.values()
                   if e.alive and e.owner == 1]
        if not b_alive or not r_alive:
            print()
            if b_alive and not r_alive:
                print("=== BLUE WINS at t=%d (%d survivor(s)) ===" % (tick, len(b_alive)))
            elif r_alive and not b_alive:
                print("=== RED WINS at t=%d (%d survivor(s)) ===" % (tick, len(r_alive)))
            else:
                print("=== DRAW at t=%d ===" % tick)
            break
    else:
        print("=== TIMEOUT at t=%d ===" % MAX_TICKS)

    print()
    print("Survivors:")
    for e in world.entities.values():
        side = "Blue" if e.owner == 0 else "Red"
        print("  %s %-10s hp=%3d pos=(%.1f, %.1f)"
              % (side, e.type_id, e.hp, e.pos[0], e.pos[1]))


if __name__ == "__main__":
    main()
