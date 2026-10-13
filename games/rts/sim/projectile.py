"""Снаряды для дальних атак. Python 3.6."""
import math


class Projectile(object):
    __slots__ = ("owner", "source_id", "target_id", "pos",
                 "base_damage", "attack_type", "speed", "life", "max_life")

    def __init__(self, owner, source_id, target_id, pos,
                 base_damage, attack_type, speed=15.0, max_life=2.0):
        # type: (int, int, int, tuple, float, str, float, float) -> None
        self.owner = owner
        self.source_id = source_id
        self.target_id = target_id
        self.pos = [float(pos[0]), float(pos[1])]
        self.base_damage = float(base_damage)
        self.attack_type = attack_type
        self.speed = float(speed)
        self.life = 0.0
        self.max_life = float(max_life)

    def update(self, world, dt):
        # type: (object, float) -> bool
        """Возвращает False, когда снаряд израсходован (попадание/промах)."""
        self.life += dt
        if self.life > self.max_life:
            return False

        target = world.entities.get(self.target_id)
        if target is None or not target.alive:
            return False

        dx = target.pos[0] - self.pos[0]
        dy = target.pos[1] - self.pos[1]
        dist = math.sqrt(dx * dx + dy * dy)
        step = self.speed * dt

        if dist < 1e-6 or step >= dist:
            self._hit(target, world)
            return False

        self.pos[0] += dx / dist * step
        self.pos[1] += dy / dist * step
        return True

    def _hit(self, target, world):
        from .combat import compute_damage
        dmg = compute_damage(
            self.base_damage, self.attack_type,
            getattr(target, "armor", 0),
            getattr(target, "armor_type", "light"),
            world.rules,
        )
        target.hp -= int(round(dmg))
        if target.hp <= 0:
            target.hp = 0
            target.kill()
