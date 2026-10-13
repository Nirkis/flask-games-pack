# -*- coding: utf-8 -*-
"""Liveness игроков. Игры вызывают touch() в _current().

Конвенция: g['last_seen'] = {token: timestamp}.
"""
import time

PLAYER_TIMEOUT = 45       # сек без запроса → offline
LOBBY_KICK = 30           # сек в лобби без запроса → освободить слот
GAME_KEEP_ALIVE = 300     # сек, пока партия хранится без живых
SWEEP_INTERVAL = 10       # как часто портал дёргает sweep_games()


def touch(g, token):
    if not token:
        return
    g.setdefault('last_seen', {})[token] = time.time()


def is_alive(g, token, now=None, timeout=PLAYER_TIMEOUT):
    ls = g.get('last_seen', {})
    if token not in ls:
        return False
    now = now or time.time()
    return (now - ls[token]) < timeout


def alive_tokens(g, now=None, timeout=PLAYER_TIMEOUT):
    now = now or time.time()
    ls = g.get('last_seen', {})
    return [t for t, ts in ls.items() if (now - ts) < timeout]


def should_delete(g, now=None, keep_alive=GAME_KEEP_ALIVE):
    now = now or time.time()
    ls = g.get('last_seen', {})
    if not ls:
        created = g.get('created', now) if isinstance(g, dict) else getattr(g, 'created', now)
        return (now - created) > keep_alive
    return (now - max(ls.values())) > keep_alive


def generic_sweep(g, now, get_tokens, drop_player, is_lobby):
    """Типовая уборка. Возвращает True, если партию пора удалять."""
    ls = g.setdefault('last_seen', {})
    tokens = list(get_tokens(g) or [])

    if is_lobby(g):
        for t in list(ls.keys()):
            if t not in tokens:
                ls.pop(t, None)
                continue
            if (now - ls[t]) > LOBBY_KICK:
                drop_player(g, t)
                ls.pop(t, None)

    return should_delete(g, now)
