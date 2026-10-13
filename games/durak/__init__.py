# games/durak/__init__.py
# -*- coding: utf-8 -*-
"""Дурак подкидной, переводной — 2-3 игрока, пошаговый."""

import random
import time

from flask import Blueprint, jsonify, request

from games import game_base
from games.data_loader import load


GAME_META = {
    'code': 'dk',
    'prefix': 'DK',
    'name': 'Дурак',
    'players': '2-3',
    'template': 'durak/index.html',
    'modes': [2, 3],
    'order': 30,
    'realtime': False,
}

bp = Blueprint('durak', __name__, url_prefix='/dk', template_folder='templates')


_RULES = load('rules', schema={
    'suits': list,
    'suit_names': dict,
    'ranks': list,
    'hand_size': int,
    'max_table': int,
    'bite_grace': float,
    'bite_timeout': float,
})

SUITS = _RULES['suits']
RANKS = _RULES['ranks']
HAND_SIZE = _RULES['hand_size']
MAX_TABLE = _RULES['max_table']
BITE_GRACE = _RULES['bite_grace']
BITE_TIMEOUT = _RULES['bite_timeout']

RANK_VALUE = {r: i for i, r in enumerate(RANKS)}

MIN_PLAYERS = 2
MAX_PLAYERS = 3


# ---------- pure helpers ----------

def _make_deck():
    d = [{'r': r, 's': s} for s in SUITS for r in RANKS]
    random.shuffle(d)
    return d


def _card_text(c):
    return c['r'] + c['s']


def _find_card(hand, card):
    if not isinstance(card, dict):
        return -1
    for i, c in enumerate(hand):
        if c['r'] == card.get('r') and c['s'] == card.get('s'):
            return i
    return -1


def _beats(attack, defend, trump):
    if defend['s'] == attack['s']:
        return RANK_VALUE[defend['r']] > RANK_VALUE[attack['r']]
    if defend['s'] == trump and attack['s'] != trump:
        return True
    return False


def _log(g, msg):
    g['log'].append(msg)
    if len(g['log']) > 200:
        g['log'] = g['log'][-200:]


def _record(g):
    g['move_no'] += 1
    g['history'].append({
        'n': g['move_no'],
        'hands': [len(h) for h in g['hands']],
        'deck': len(g['deck']),
    })
    if len(g['history']) > 400:
        g['history'] = g['history'][-400:]


def _undefended(g):
    return sum(1 for t in g['table'] if t['defend'] is None)


def _table_ranks(g):
    ranks = set()
    for t in g['table']:
        ranks.add(t['attack']['r'])
        if t['defend']:
            ranks.add(t['defend']['r'])
    return ranks


def _is_out(g, idx):
    return not g['hands'][idx]


def _next_active_after(g, idx):
    n = g['num_players']
    for k in range(1, n + 1):
        cand = (idx + k) % n
        if not _is_out(g, cand):
            return cand
    return None


# ---------- GameServer hooks ----------

def _new_game(code, mode=2):
    try:
        mode = int(mode)
    except (TypeError, ValueError):
        mode = MIN_PLAYERS
    mode = max(MIN_PLAYERS, min(MAX_PLAYERS, mode))
    deck = _make_deck()
    trump = deck[-1]
    return {
        'id': code,
        'num_players': mode,
        'names': ['Игрок %d' % (i + 1) for i in range(mode)],
        'tokens': [None] * mode,
        'occupied': [False] * mode,
        'hands': [[] for _ in range(mode)],
        'deck': deck,
        'trump_card': trump,
        'trump_suit': trump['s'],
        'table': [],
        'attacker_idx': 0,
        'defender_idx': 1 % mode,
        'phase': 'lobby',
        'loser': None,
        'discard_count': 0,
        'bite_start': None,
        'log': [],
        'history': [],
        'move_no': 0,
        'created': time.time(),
    }


def _attach_player(g, slot, name, tok):
    g['tokens'][slot] = tok
    g['occupied'][slot] = True
    g['names'][slot] = name
    _log(g, '%s садится за стол' % name)


def _get_tokens(g):
    return list(g['tokens'])


def _drop_player(g, tok):
    if g['phase'] != 'lobby':
        return
    for i, t in enumerate(g['tokens']):
        if t == tok:
            g['tokens'][i] = None
            g['occupied'][i] = False
            break


def _player_names(g):
    return [g['names'][i] for i, occ in enumerate(g['occupied']) if occ]


def _phase_of(g):
    return g['phase']


def _deal_initial(g):
    for _ in range(HAND_SIZE):
        for p in range(g['num_players']):
            if g['deck']:
                g['hands'][p].append(g['deck'].pop(0))


def _choose_first_attacker(g):
    best_idx, best_val = None, None
    for p in range(g['num_players']):
        for c in g['hands'][p]:
            if c['s'] == g['trump_suit']:
                v = RANK_VALUE[c['r']]
                if best_val is None or v < best_val:
                    best_val, best_idx = v, p
    if best_idx is None:
        best_idx = random.randint(0, g['num_players'] - 1)
    return best_idx


def _maybe_start(g):
    if g['phase'] != 'lobby':
        return
    if not all(g['occupied']):
        return
    _deal_initial(g)
    g['attacker_idx'] = _choose_first_attacker(g)
    g['defender_idx'] = (g['attacker_idx'] + 1) % g['num_players']
    g['phase'] = 'battle'
    _log(g, 'Игра началась. Козырь — %s. Первым ходит %s.' % (
        g['trump_suit'], g['names'][g['attacker_idx']]))
    _record(g)


def _refill(g):
    order = [g['attacker_idx'], g['defender_idx']]
    for i in range(g['num_players']):
        if i not in order:
            order.append(i)
    for p in order:
        while len(g['hands'][p]) < HAND_SIZE and g['deck']:
            g['hands'][p].append(g['deck'].pop(0))


def _check_winner(g):
    if g['deck']:
        return False
    active = [i for i in range(g['num_players']) if g['hands'][i]]
    if len(active) == 0:
        g['phase'] = 'over'
        g['loser'] = -1
        _log(g, 'Ничья — все вышли.')
        return True
    if len(active) == 1:
        loser = active[0]
        g['phase'] = 'over'
        g['loser'] = loser
        _log(g, '%s — дурак!' % g['names'][loser])
        return True
    return False


def _end_battle(g, success):
    g['bite_start'] = None
    _refill(g)
    if _check_winner(g):
        return
    if success:
        next_att = g['defender_idx']
        if _is_out(g, next_att):
            next_att = _next_active_after(g, next_att)
    else:
        next_att = _next_active_after(g, g['defender_idx'])
    if next_att is None:
        _check_winner(g)
        return
    g['attacker_idx'] = next_att
    g['defender_idx'] = _next_active_after(g, next_att)
    if g['defender_idx'] is None:
        _check_winner(g)


# ---------- actions ----------

def _do_attack(g, pid, card):
    if pid == g['defender_idx']:
        return 'Защищающийся не подкидывает карты'
    if not g['table'] and pid != g['attacker_idx']:
        return 'Первую карту кладёт атакующий'
    idx = _find_card(g['hands'][pid], card)
    if idx < 0:
        return 'У вас нет такой карты'
    if len(g['table']) >= MAX_TABLE:
        return 'На столе уже максимум карт'
    undef = _undefended(g)
    if len(g['hands'][g['defender_idx']]) <= undef:
        return 'У защищающегося мало карт'
    if g['table'] and card['r'] not in _table_ranks(g):
        return 'Подкидывать можно только карты того же достоинства'
    c = g['hands'][pid].pop(idx)
    g['table'].append({'attack': c, 'defend': None})
    g['bite_start'] = None
    _log(g, '%s подкидывает: %s' % (g['names'][pid], _card_text(c)))
    return None


def _do_defend(g, pid, card):
    if pid != g['defender_idx']:
        return 'Сейчас отбивается соперник'
    target = next((t for t in g['table'] if t['defend'] is None), None)
    if target is None:
        return 'Все карты отбиты'
    idx = _find_card(g['hands'][pid], card)
    if idx < 0:
        return 'У вас нет такой карты'
    if not _beats(target['attack'], card, g['trump_suit']):
        return 'Карта %s не бьёт %s' % (_card_text(card), _card_text(target['attack']))
    c = g['hands'][pid].pop(idx)
    target['defend'] = c
    _log(g, '%s отбивается: %s' % (g['names'][pid], _card_text(c)))
    if _undefended(g) == 0 and g['table']:
        g['bite_start'] = time.time()
    return None


def _do_transfer(g, pid, card):
    if pid != g['defender_idx']:
        return 'Переводить может только защищающийся'
    if any(t['defend'] is not None for t in g['table']):
        return 'Нельзя переводить, когда есть отбитые карты'
    idx = _find_card(g['hands'][pid], card)
    if idx < 0:
        return 'У вас нет такой карты'
    if len(g['table']) >= MAX_TABLE:
        return 'На столе уже максимум карт'
    if len(g['hands'][g['attacker_idx']]) <= _undefended(g):
        return 'У соперника мало карт'
    if card['r'] not in _table_ranks(g):
        return 'Переводить можно только картой того же достоинства'
    c = g['hands'][pid].pop(idx)
    g['table'].append({'attack': c, 'defend': None})
    g['bite_start'] = None
    g['attacker_idx'], g['defender_idx'] = g['defender_idx'], g['attacker_idx']
    _log(g, '%s переводит: %s.' % (g['names'][pid], _card_text(c)))
    return None


def _do_take(g, pid):
    if pid != g['defender_idx']:
        return 'Вы не защищаетесь'
    if not g['table']:
        return 'Стол пуст'
    if _undefended(g) == 0:
        return 'Все карты уже отбиты'
    taken = 0
    for t in g['table']:
        g['hands'][pid].append(t['attack'])
        taken += 1
        if t['defend']:
            g['hands'][pid].append(t['defend'])
            taken += 1
    g['table'] = []
    g['bite_start'] = None
    _log(g, '%s берёт %d карт(ы)' % (g['names'][pid], taken))
    _end_battle(g, success=False)
    return None


def _do_pass(g, pid):
    if pid != g['attacker_idx']:
        return 'Только атакующий может сказать Бито'
    if not g['table']:
        return 'Стол пуст'
    if _undefended(g) > 0:
        return 'Есть неотбитые карты'
    if g['bite_start'] is not None:
        left = BITE_GRACE - (time.time() - g['bite_start'])
        if left > 0:
            return 'Подождите ещё %.1f сек' % left
    g['discard_count'] += len(g['table'])
    g['table'] = []
    g['bite_start'] = None
    _log(g, '%s: бито' % g['names'][pid])
    _end_battle(g, success=True)
    return None


def _do_reset(g, pid):
    old_tokens = list(g['tokens'])
    old_names = list(g['names'])
    num = g['num_players']
    code = g['id']
    fresh = _new_game(code, num)
    g.clear()
    g.update(fresh)
    g['tokens'] = old_tokens
    g['names'] = old_names
    g['occupied'] = [t is not None for t in old_tokens]
    _log(g, 'Новая партия')
    _maybe_start(g)
    return None


def _do_action(g, pid, data):
    action = data.get('action')
    if action == 'reset':
        return _do_reset(g, pid)
    if g['phase'] != 'battle':
        return 'Сейчас не боевая фаза'
    if action == 'attack':
        return _do_attack(g, pid, data.get('card'))
    if action == 'defend':
        return _do_defend(g, pid, data.get('card'))
    if action == 'transfer':
        return _do_transfer(g, pid, data.get('card'))
    if action == 'take':
        return _do_take(g, pid)
    if action == 'pass':
        return _do_pass(g, pid)
    return 'Неизвестное действие'


# ---------- legal moves ----------

def _legal_attacks(g, pid):
    if g['phase'] != 'battle' or pid == g['defender_idx']:
        return []
    if len(g['table']) >= MAX_TABLE:
        return []
    if len(g['hands'][g['defender_idx']]) <= _undefended(g):
        return []
    if not g['table']:
        return list(g['hands'][pid]) if pid == g['attacker_idx'] else []
    ranks = _table_ranks(g)
    return [c for c in g['hands'][pid] if c['r'] in ranks]


def _legal_defends(g, pid):
    if g['phase'] != 'battle' or pid != g['defender_idx']:
        return []
    target = next((t for t in g['table'] if t['defend'] is None), None)
    if target is None:
        return []
    return [c for c in g['hands'][pid] if _beats(target['attack'], c, g['trump_suit'])]


def _legal_transfers(g, pid):
    if g['phase'] != 'battle' or pid != g['defender_idx']:
        return []
    if any(t['defend'] is not None for t in g['table']):
        return []
    if len(g['table']) >= MAX_TABLE:
        return []
    if len(g['hands'][g['attacker_idx']]) <= _undefended(g):
        return []
    ranks = _table_ranks(g)
    if not ranks:
        return []
    return [c for c in g['hands'][pid] if c['r'] in ranks]


def _force_pass_if_timeout(g):
    if g is None or g['phase'] != 'battle' or g['bite_start'] is None:
        return
    if _undefended(g) > 0 or not g['table']:
        g['bite_start'] = None
        return
    if time.time() - g['bite_start'] < BITE_TIMEOUT:
        return
    att = g['attacker_idx']
    g['discard_count'] += len(g['table'])
    g['table'] = []
    g['bite_start'] = None
    _log(g, '%s: бито (авто)' % g['names'][att])
    _end_battle(g, success=True)


# ---------- state ----------

def _build_state(g, pid):
    base = {
        'game_id': g['id'],
        'num_players': g['num_players'],
        'names': list(g['names']),
        'occupied': list(g['occupied']),
        'phase': g['phase'],
        'slot': pid,
        'my_name': (g['names'][pid]
                    if pid is not None and 0 <= pid < len(g['names']) else ''),
        'log': list(g['log'][-30:]),
        'history': list(g['history']),
    }
    if g['phase'] == 'lobby' or pid is None:
        base['joined'] = pid is not None
        base['waiting_opponents'] = not all(g['occupied'])
        return base

    others = [i for i in range(g['num_players']) if i != pid]
    bite_grace_left = None
    bite_seconds_left = None
    if g['bite_start'] is not None and _undefended(g) == 0 and g['table']:
        elapsed = time.time() - g['bite_start']
        bite_grace_left = max(0.0, BITE_GRACE - elapsed)
        bite_seconds_left = max(0.0, BITE_TIMEOUT - elapsed)

    base.update({
        'joined': True,
        'others': [{
            'idx': i,
            'name': g['names'][i],
            'hand_count': len(g['hands'][i]),
            'is_attacker': i == g['attacker_idx'],
            'is_defender': i == g['defender_idx'],
        } for i in others],
        'trump_card': g['trump_card'],
        'trump_suit': g['trump_suit'],
        'deck_count': len(g['deck']),
        'discard_count': g['discard_count'],
        'table': [{'attack': t['attack'], 'defend': t['defend']} for t in g['table']],
        'my_hand': list(g['hands'][pid]),
        'attacker_idx': g['attacker_idx'],
        'defender_idx': g['defender_idx'],
        'i_am_attacker': pid == g['attacker_idx'],
        'i_am_defender': pid == g['defender_idx'],
        'loser': g['loser'],
        'i_lost': (g['loser'] == pid) if g['loser'] is not None else False,
        'draw': g['loser'] == -1,
        'legal_attacks': _legal_attacks(g, pid),
        'legal_defends': _legal_defends(g, pid),
        'legal_transfers': _legal_transfers(g, pid),
        'can_take': (g['phase'] == 'battle' and pid == g['defender_idx']
                     and _undefended(g) > 0),
        'can_pass': (g['phase'] == 'battle' and pid == g['attacker_idx']
                     and bool(g['table']) and _undefended(g) == 0
                     and (g['bite_start'] is None
                          or time.time() - g['bite_start'] >= BITE_GRACE)),
        'can_transfer': bool(_legal_transfers(g, pid)),
        'bite_grace_left': bite_grace_left,
        'bite_seconds_left': bite_seconds_left,
        'waiting_opponents': False,
    })
    return base


# ---------- API ----------

@bp.before_request
def _tick():
    with LOCK:
        g, _ = _current()
        if g is None:
            return
        _force_pass_if_timeout(g)


@bp.after_request
def _no_cache(response):
    if request.path.startswith('/dk/api/'):
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response


@bp.route('/api/state')
def api_state():
    with LOCK:
        g, pid = _current()
        if g is None:
            return jsonify({'ok': False, 'error': 'Нет игры'}), 404
        return jsonify({'ok': True, 'state': _build_state(g, pid)})


@bp.route('/api/action', methods=['POST'])
def api_action():
    with LOCK:
        g, pid = _current()
        if g is None:
            return jsonify({'ok': False, 'error': 'Нет игры'}), 404
        data = request.get_json(silent=True) or {}
        err = _do_action(g, pid, data)
        if err:
            return jsonify({'ok': False, 'error': err}), 400
        _record(g)
        return jsonify({'ok': True, 'state': _build_state(g, pid)})


# ---------- install ----------

server = game_base.GameServer(
    prefix='DK',
    max_players=MAX_PLAYERS,
    min_players=MIN_PLAYERS,
    new_game=_new_game,
    attach_player=_attach_player,
    get_tokens=_get_tokens,
    drop_player=_drop_player,
    is_lobby=lambda g: g['phase'] == 'lobby',
    build_state=_build_state,
    player_names=_player_names,
    phase_of=_phase_of,
    maybe_start=_maybe_start,
)

(GAMES, LOCK,
 create_game, join_game, leave, has_player,
 sweep, list_active, is_joinable, build_state_for_token,
 _current) = server.install()
