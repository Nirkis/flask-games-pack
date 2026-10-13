# -*- coding: utf-8 -*-
"""GameServer — базовый движок, избавляющий игру от boilerplate.

Использование в games/<game>/__init__.py:

    server = GameServer(
        prefix='XX',
        max_players=4, min_players=2,
        new_game=lambda code, **opts: _new_game(code, **opts),
        attach_player=lambda g, slot, name, tok: ...,
        get_tokens=lambda g: g['tokens'],
        drop_player=lambda g, tok: ...,
        is_lobby=lambda g: g['phase'] == 'lobby',
        build_state=lambda g, i: _build_state(g, i),
        player_names=lambda g: [...],
        phase_of=lambda g: g['phase'],
        maybe_start=lambda g: _maybe_start(g),
    )
    server.install(globals())
"""
import random
import threading
import time

from flask import session

from games import heartbeat


def _default_token():
    return '%d-%d' % (random.randint(0, 10 ** 9), id(object()) % 10 ** 9)


class GameServer(object):
    def __init__(self, *, prefix, max_players, min_players=1,
                 new_game, attach_player, get_tokens, drop_player,
                 is_lobby, build_state, player_names, phase_of,
                 maybe_start=None,
                 code_len=4,
                 code_alphabet='ABCDEFGHJKLMNPQRSTUVWXYZ23456789',
                 token_gen=None):
        self.prefix = prefix.upper()
        self.max_players = max_players
        self.min_players = min_players
        self.new_game = new_game
        self.attach_player = attach_player
        self.get_tokens = get_tokens
        self.drop_player = drop_player
        self.is_lobby = is_lobby
        self.build_state = build_state
        self.player_names = player_names
        self.phase_of = phase_of
        self.maybe_start = maybe_start
        self.code_len = code_len
        self.code_alphabet = code_alphabet
        self.token_gen = token_gen or _default_token
        self.GAMES = {}
        self.LOCK = threading.RLock()

    # ---------- внутреннее ----------
    def _gen_code(self):
        return '%s-%s' % (self.prefix,
                          ''.join(random.choice(self.code_alphabet)
                                  for _ in range(self.code_len)))

    def _find_slot(self, g, token):
        for i, t in enumerate(self.get_tokens(g)):
            if t == token:
                return i
        return None

    def install(self):
        """Возвращает кортеж для явного присваивания в модуле игры.

        Использование:

            (GAMES, LOCK,
             create_game, join_game, leave, has_player,
             sweep, list_active, is_joinable, build_state_for_token,
             _current) = server.install()
        """
        return (
            self.GAMES,
            self.LOCK,
            self.create_game,
            self.join_game,
            self.leave,
            self.has_player,
            self.sweep,
            self.list_active,
            self.is_joinable,
            self.build_state_for_token,
            self.current,
        )

    # ---------- хуки портала ----------
    def create_game(self, name, **opts):
        with self.LOCK:
            code = self._gen_code()
            while code in self.GAMES:
                code = self._gen_code()
            g = self.new_game(code, **opts)
            self.GAMES[code] = g
            tok = self.token_gen()
            self.attach_player(g, 0, name, tok)
            heartbeat.touch(g, tok)
            if callable(self.maybe_start):
                self.maybe_start(g)
        return code, tok, None

    def join_game(self, code, name, **opts):
        with self.LOCK:
            g = self.GAMES.get(code)
            if not g:
                return None, 'Игра не найдена'
            if not self.is_lobby(g):
                return None, 'Игра уже началась'
            tokens = self.get_tokens(g)
            slot = next((i for i, t in enumerate(tokens) if t is None), None)
            if slot is None:
                return None, 'Мест нет'
            tok = self.token_gen()
            self.attach_player(g, slot, name, tok)
            heartbeat.touch(g, tok)
            if callable(self.maybe_start):
                self.maybe_start(g)
            return tok, None

    def leave(self, code, token):
        with self.LOCK:
            g = self.GAMES.get(code)
            if not g:
                return
            if self.is_lobby(g):
                self.drop_player(g, token)
                if not any(t for t in self.get_tokens(g)):
                    self.GAMES.pop(code, None)

    def has_player(self, code, token):
        with self.LOCK:
            g = self.GAMES.get(code)
            if not g:
                return False
            return self._find_slot(g, token) is not None

    def current(self):
        code = session.get('code')
        tok = session.get('player_token')
        if not code or not tok:
            return None, None
        g = self.GAMES.get(code)
        if not g:
            return None, None
        i = self._find_slot(g, tok)
        if i is None:
            return None, None
        heartbeat.touch(g, tok)
        return g, i

    # ---------- уборка и статистика ----------
    def sweep(self, now):
        with self.LOCK:
            for code, g in list(self.GAMES.items()):
                tokens = [t for t in self.get_tokens(g) if t]
                delete = heartbeat.generic_sweep(
                    g, now,
                    get_tokens=lambda gg: tokens,
                    drop_player=self.drop_player,
                    is_lobby=self.is_lobby,
                )
                if delete:
                    self.GAMES.pop(code, None)

    def _entry_created(self, g, default):
        if isinstance(g, dict):
            return g.get('created', default)
        return getattr(g, 'created', default)

    def list_active(self):
        with self.LOCK:
            now = time.time()
            out = []
            for code, g in self.GAMES.items():
                tokens = self.get_tokens(g)
                occ = sum(1 for t in tokens if t)
                active = len(heartbeat.alive_tokens(g, now))
                out.append({
                    'id': code,
                    'phase': self.phase_of(g),
                    'players': self.player_names(g),
                    'occupied': occ,
                    'active': active,
                    'max_players': self.max_players,
                    'started': not self.is_lobby(g),
                    'created': self._entry_created(g, now),
                    'joinable': self.is_lobby(g) and occ < self.max_players,
                })
            return out

    def is_joinable(self, code):
        with self.LOCK:
            g = self.GAMES.get(code)
            if not g:
                return None
            tokens = self.get_tokens(g)
            occ = sum(1 for t in tokens if t)
            return {
                'id': code,
                'phase': self.phase_of(g),
                'players': self.player_names(g),
                'occupied': occ,
                'max_players': self.max_players,
                'can_join': self.is_lobby(g) and occ < self.max_players,
            }

    def build_state_for_token(self, code, token):
        with self.LOCK:
            g = self.GAMES.get(code)
            if not g:
                return None
            i = self._find_slot(g, token)
            if i is None:
                return None
            return self.build_state(g, i)
