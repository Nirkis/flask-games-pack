# -*- coding: utf-8 -*-
"""Новая игра. Скопируй и адаптируй."""
import random
import threading
import time

from flask import Blueprint, jsonify, request, session

bp = Blueprint('mygame', __name__, url_prefix='/mg')

GAME_META = {
    'code': 'mg',
    'prefix': 'MG',
    'name': 'Моя игра',
    'players': '2',
    'template': 'mygame.html',
    # 'modes': [2, 3],                     # ← если нужен селектор режима
    # 'extra_options': ['disease_type'],   # ← если нужен доп. селектор
    'order': 60,
}

GAMES = {}
LOCK = threading.RLock()
CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'


def _gen_code():
    return 'MG-' + ''.join(random.choice(CODE_ALPHABET) for _ in range(4))


def _gen_token():
    return '%d-%d' % (random.randint(0, 10 ** 9), id(object()) % 10 ** 9)


def _find(g, token):
    for i, t in enumerate(g['tokens']):
        if t == token:
            return i
    return None


# ---------- Портальные хуки ----------

def create_game(name, mode=2):
    with LOCK:
        code = _gen_code()
        while code in GAMES:
            code = _gen_code()
        g = {
            'id': code, 'num_players': 2,
            'names': [name, 'Игрок 2'],
            'tokens': [_gen_token(), None],
            'occupied': [True, False],
            'phase': 'lobby',
            'state': {},          # твоё игровое состояние
            'created': time.time(),
        }
        GAMES[code] = g
    return code, g['tokens'][0], None


def join_game(code, name):
    with LOCK:
        g = GAMES.get(code)
        if not g:
            return None, 'Игра не найдена'
        if g['phase'] != 'lobby':
            return None, 'Игра уже началась'
        for i in range(g['num_players']):
            if not g['occupied'][i]:
                tok = _gen_token()
                g['occupied'][i] = True
                g['tokens'][i] = tok
                g['names'][i] = name
                if all(g['occupied']):
                    g['phase'] = 'battle'
                return tok, None
        return None, 'Мест нет'


def leave(code, token):
    with LOCK:
        g = GAMES.get(code)
        if not g:
            return
        if g['phase'] == 'lobby':
            for i in range(g['num_players']):
                if g['tokens'][i] == token:
                    g['tokens'][i] = None
                    g['occupied'][i] = False
                    break
            if not any(g['occupied']):
                GAMES.pop(code, None)


def has_player(code, token):
    with LOCK:
        g = GAMES.get(code)
        return bool(g) and _find(g, token) is not None


def _current():
    code = session.get('code')
    tok = session.get('player_token')
    if not code or not tok:
        return None, None
    g = GAMES.get(code)
    if not g:
        return None, None
    i = _find(g, tok)
    if i is None:
        return None, None
    return g, i


# ---------- API ----------

@bp.route('/api/state')
def api_state():
    with LOCK:
        g, pid = _current()
        if g is None:
            return jsonify({'ok': False, 'error': 'Нет игры'}), 404
        return jsonify({'ok': True, 'state': {
            'game_id': g['id'],
            'phase': g['phase'],
            'slot': pid,
            'names': g['names'],
            'occupied': g['occupied'],
            'my_turn': True,
        }})


@bp.route('/api/action', methods=['POST'])
def api_action():
    with LOCK:
        g, pid = _current()
        if g is None:
            return jsonify({'ok': False, 'error': 'Нет игры'}), 404
        data = request.get_json(silent=True) or {}
        # ... тут твоя логика ...
        return jsonify({'ok': True, 'state': {
            'game_id': g['id'], 'phase': g['phase'],
            'slot': pid, 'names': g['names'], 'occupied': g['occupied'],
        }})
