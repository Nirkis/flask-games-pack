# -*- coding: utf-8 -*-
"""Дурак подкидной, переводной — многокомнатный."""
import random
import threading
import time

from flask import Blueprint, jsonify, request, session

bp = Blueprint('durak', __name__, url_prefix='/dk')

SUITS = ['\u2660', '\u2665', '\u2666', '\u2663']
SUIT_NAMES = {'\u2660': 'пики', '\u2665': 'черви', '\u2666': 'бубны', '\u2663': 'трефы'}
RANKS = ['6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
RANK_VALUE = {r: i for i, r in enumerate(RANKS)}

HAND_SIZE = 6
MAX_TABLE = 12
MIN_PLAYERS = 2
MAX_PLAYERS = 3
BITE_GRACE = 10.0
BITE_TIMEOUT = 3.0

GAMES = {}
LOCK = threading.RLock()
CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'


def _gen_code():
    return 'DK-' + ''.join(random.choice(CODE_ALPHABET) for _ in range(4))


def _gen_token():
    return '%d-%d' % (random.randint(0, 10 ** 9), id(object()) % 10 ** 9)


def make_deck():
    d = [{'r': r, 's': s} for s in SUITS for r in RANKS]
    random.shuffle(d)
    return d


def card_text(c):
    return c['r'] + c['s']


def find_card(hand, card):
    if not isinstance(card, dict):
        return -1
    for i, c in enumerate(hand):
        if c['r'] == card.get('r') and c['s'] == card.get('s'):
            return i
    return -1


def beats(attack, defend, trump):
    if defend['s'] == attack['s']:
        return RANK_VALUE[defend['r']] > RANK_VALUE[attack['r']]
    if defend['s'] == trump and attack['s'] != trump:
        return True
    return False


def new_game(num_players, code):
    deck = make_deck()
    trump = deck[-1]
    return {
        'id': code, 'num_players': num_players,
        'names': ['Игрок %d' % (i + 1) for i in range(num_players)],
        'tokens': [None] * num_players,
        'occupied': [False] * num_players,
        'hands': [[] for _ in range(num_players)],
        'deck': deck, 'trump_card': trump, 'trump_suit': trump['s'],
        'table': [], 'attacker_idx': 0, 'defender_idx': 1 % num_players,
        'phase': 'lobby', 'loser': None, 'discard_count': 0,
        'bite_start': None, 'log': [], 'history': [], 'move_no': 0,
        'created': time.time(),
    }


def deal_initial(g):
    for _ in range(HAND_SIZE):
        for p in range(g['num_players']):
            if g['deck']:
                g['hands'][p].append(g['deck'].pop(0))


def choose_first_attacker(g):
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


def maybe_start(g):
    if g['phase'] != 'lobby' or not all(g['occupied']):
        return
    deal_initial(g)
    g['attacker_idx'] = choose_first_attacker(g)
    g['defender_idx'] = (g['attacker_idx'] + 1) % g['num_players']
    g['phase'] = 'battle'
    g['log'].append('Игра началась. Козырь — %s. Первым ходит %s.' % (
        g['trump_suit'], g['names'][g['attacker_idx']]))
    record(g)


def undefended_count(g):
    return sum(1 for t in g['table'] if t['defend'] is None)


def table_ranks(g):
    ranks = set()
    for t in g['table']:
        ranks.add(t['attack']['r'])
        if t['defend']:
            ranks.add(t['defend']['r'])
    return ranks


def is_out(g, idx):
    return not g['hands'][idx]


def next_active_after(g, idx):
    n = g['num_players']
    for k in range(1, n + 1):
        cand = (idx + k) % n
        if not is_out(g, cand):
            return cand
    return None


def refill(g):
    order = [g['attacker_idx'], g['defender_idx']]
    for i in range(g['num_players']):
        if i not in order:
            order.append(i)
    for p in order:
        while len(g['hands'][p]) < HAND_SIZE and g['deck']:
            g['hands'][p].append(g['deck'].pop(0))


def check_winner(g):
    if g['deck']:
        return False
    active = [i for i in range(g['num_players']) if g['hands'][i]]
    if len(active) == 0:
        g['phase'] = 'over'; g['loser'] = -1
        g['log'].append('Ничья — все вышли.')
        return True
    if len(active) == 1:
        loser = active[0]
        g['phase'] = 'over'; g['loser'] = loser
        g['log'].append('%s — дурак!' % g['names'][loser])
        return True
    return False


def end_battle(g, success):
    g['bite_start'] = None
    refill(g)
    if check_winner(g):
        return
    if success:
        next_att = g['defender_idx']
        if is_out(g, next_att):
            next_att = next_active_after(g, next_att)
    else:
        next_att = next_active_after(g, g['defender_idx'])
    if next_att is None:
        check_winner(g); return
    g['attacker_idx'] = next_att
    g['defender_idx'] = next_active_after(g, next_att)
    if g['defender_idx'] is None:
        check_winner(g)


def record(g):
    g['move_no'] += 1
    g['history'].append({
        'n': g['move_no'],
        'hands': [len(h) for h in g['hands']],
        'deck': len(g['deck']),
    })
    if len(g['history']) > 400:
        g['history'] = g['history'][-400:]


def do_attack(g, pid, card):
    if g['phase'] != 'battle':
        return 'Сейчас нельзя ходить'
    if pid == g['defender_idx']:
        return 'Защищающийся не подкидывает карты'
    if not g['table'] and pid != g['attacker_idx']:
        return 'Первую карту кладёт атакующий'
    idx = find_card(g['hands'][pid], card)
    if idx < 0:
        return 'У вас нет такой карты'
    if len(g['table']) >= MAX_TABLE:
        return 'На столе уже максимум карт'
    undef = undefended_count(g)
    if len(g['hands'][g['defender_idx']]) <= undef:
        return 'У защищающегося мало карт'
    if g['table'] and card['r'] not in table_ranks(g):
        return 'Подкидывать можно только карты того же достоинства'
    c = g['hands'][pid].pop(idx)
    g['table'].append({'attack': c, 'defend': None})
    g['bite_start'] = None
    g['log'].append('%s подкидывает: %s' % (g['names'][pid], card_text(c)))
    return None


def do_defend(g, pid, card):
    if g['phase'] != 'battle':
        return 'Сейчас нечего отбивать'
    if pid != g['defender_idx']:
        return 'Сейчас отбивается соперник'
    target = next((t for t in g['table'] if t['defend'] is None), None)
    if target is None:
        return 'Все карты отбиты'
    idx = find_card(g['hands'][pid], card)
    if idx < 0:
        return 'У вас нет такой карты'
    if not beats(target['attack'], card, g['trump_suit']):
        return 'Карта %s не бьёт %s' % (card_text(card), card_text(target['attack']))
    c = g['hands'][pid].pop(idx)
    target['defend'] = c
    g['log'].append('%s отбивается: %s' % (g['names'][pid], card_text(c)))
    if undefended_count(g) == 0 and g['table']:
        g['bite_start'] = time.time()
    return None


def do_transfer(g, pid, card):
    if g['phase'] != 'battle':
        return 'Сейчас нельзя переводить'
    if pid != g['defender_idx']:
        return 'Переводить может только защищающийся'
    if any(t['defend'] is not None for t in g['table']):
        return 'Нельзя переводить, когда есть отбитые карты'
    idx = find_card(g['hands'][pid], card)
    if idx < 0:
        return 'У вас нет такой карты'
    if len(g['table']) >= MAX_TABLE:
        return 'На столе уже максимум карт'
    if len(g['hands'][g['attacker_idx']]) <= undefended_count(g):
        return 'У соперника мало карт'
    if card['r'] not in table_ranks(g):
        return 'Переводить можно только картой того же достоинства'
    c = g['hands'][pid].pop(idx)
    g['table'].append({'attack': c, 'defend': None})
    g['bite_start'] = None
    g['attacker_idx'], g['defender_idx'] = g['defender_idx'], g['attacker_idx']
    g['log'].append('%s переводит: %s.' % (g['names'][pid], card_text(c)))
    return None


def do_take(g, pid):
    if g['phase'] != 'battle':
        return 'Сейчас нечего брать'
    if pid != g['defender_idx']:
        return 'Вы не защищаетесь'
    if not g['table']:
        return 'Стол пуст'
    if undefended_count(g) == 0:
        return 'Все карты уже отбиты'
    taken = 0
    for t in g['table']:
        g['hands'][pid].append(t['attack']); taken += 1
        if t['defend']:
            g['hands'][pid].append(t['defend']); taken += 1
    g['table'] = []
    g['bite_start'] = None
    g['log'].append('%s берёт %d карт(ы)' % (g['names'][pid], taken))
    end_battle(g, success=False)
    return None


def do_pass(g, pid):
    if g['phase'] != 'battle':
        return 'Сейчас нельзя'
    if pid != g['attacker_idx']:
        return 'Только атакующий может сказать Бито'
    if not g['table']:
        return 'Стол пуст'
    if undefended_count(g) > 0:
        return 'Есть неотбитые карты'
    if g['bite_start'] is not None:
        left = BITE_GRACE - (time.time() - g['bite_start'])
        if left > 0:
            return 'Подождите ещё %.1f сек' % left
    g['discard_count'] += len(g['table'])
    g['table'] = []
    g['bite_start'] = None
    g['log'].append('%s: бито' % g['names'][pid])
    end_battle(g, success=True)
    return None


def legal_attacks(g, pid):
    if g['phase'] != 'battle' or pid == g['defender_idx']:
        return []
    if len(g['table']) >= MAX_TABLE:
        return []
    if len(g['hands'][g['defender_idx']]) <= undefended_count(g):
        return []
    if not g['table']:
        return list(g['hands'][pid]) if pid == g['attacker_idx'] else []
    ranks = table_ranks(g)
    return [c for c in g['hands'][pid] if c['r'] in ranks]


def legal_defends(g, pid):
    if g['phase'] != 'battle' or pid != g['defender_idx']:
        return []
    target = next((t for t in g['table'] if t['defend'] is None), None)
    if target is None:
        return []
    return [c for c in g['hands'][pid] if beats(target['attack'], c, g['trump_suit'])]


def legal_transfers(g, pid):
    if g['phase'] != 'battle' or pid != g['defender_idx']:
        return []
    if any(t['defend'] is not None for t in g['table']):
        return []
    if len(g['table']) >= MAX_TABLE:
        return []
    if len(g['hands'][g['attacker_idx']]) <= undefended_count(g):
        return []
    ranks = table_ranks(g)
    if not ranks:
        return []
    return [c for c in g['hands'][pid] if c['r'] in ranks]


def force_pass_if_timeout(g):
    if g is None or g['phase'] != 'battle' or g['bite_start'] is None:
        return
    if undefended_count(g) > 0 or not g['table']:
        g['bite_start'] = None; return
    if time.time() - g['bite_start'] < BITE_TIMEOUT:
        return
    att = g['attacker_idx']
    g['discard_count'] += len(g['table'])
    g['table'] = []
    g['bite_start'] = None
    g['log'].append('%s: бито (авто)' % g['names'][att])
    end_battle(g, success=True)


def _find_player(g, token):
    for i, t in enumerate(g['tokens']):
        if t == token:
            return i
    return None


def create_game(name, mode):
    mode = max(MIN_PLAYERS, min(MAX_PLAYERS, mode))
    with LOCK:
        code = _gen_code()
        while code in GAMES:
            code = _gen_code()
        g = new_game(mode, code)
        GAMES[code] = g
        tok = _gen_token()
        g['occupied'][0] = True
        g['tokens'][0] = tok
        g['names'][0] = name
        g['log'].append('%s садится за стол' % name)
    return code, tok, None


def join_game(code, name):
    with LOCK:
        g = GAMES.get(code)
        if g is None:
            return None, 'Игра не найдена'
        if g['phase'] == 'over':
            return None, 'Партия окончена'
        for slot in range(g['num_players']):
            if not g['occupied'][slot]:
                tok = _gen_token()
                g['occupied'][slot] = True
                g['tokens'][slot] = tok
                g['names'][slot] = name
                g['log'].append('%s садится за стол' % name)
                maybe_start(g)
                return tok, None
        return None, 'Мест нет'


def leave(code, token):
    with LOCK:
        g = GAMES.get(code)
        if g is None:
            return
        if g['phase'] == 'lobby':
            for i in range(g['num_players']):
                if g['tokens'][i] == token:
                    g['tokens'][i] = None
                    g['occupied'][i] = False
                    break
            if not any(g['occupied']):
                GAMES.pop(code, None)


def _current():
    code = session.get('code')
    tok = session.get('player_token')
    if not code or not tok:
        return None, None
    g = GAMES.get(code)
    if g is None:
        return None, None
    idx = _find_player(g, tok)
    if idx is None:
        return None, None
    return g, idx


def build_state(g, pid):
    if g is None:
        return {'joined': False, 'phase': 'no_game'}
    if pid is None:
        return {
            'joined': False, 'phase': 'lobby',
            'num_players': g['num_players'],
            'names': g['names'], 'occupied': g['occupied'],
        }
    others = [i for i in range(g['num_players']) if i != pid]
    bite_grace_left = None
    bite_seconds_left = None
    if g['bite_start'] is not None and undefended_count(g) == 0 and g['table']:
        elapsed = time.time() - g['bite_start']
        bite_grace_left = max(0.0, BITE_GRACE - elapsed)
        bite_seconds_left = max(0.0, BITE_TIMEOUT - elapsed)
    return {
        'joined': True, 'slot': pid,
        'num_players': g['num_players'],
        'names': g['names'], 'occupied': g['occupied'],
        'phase': g['phase'], 'my_name': g['names'][pid],
        'others': [{
            'idx': i, 'name': g['names'][i],
            'hand_count': len(g['hands'][i]),
            'is_attacker': i == g['attacker_idx'],
            'is_defender': i == g['defender_idx'],
        } for i in others],
        'trump_card': g['trump_card'], 'trump_suit': g['trump_suit'],
        'deck_count': len(g['deck']), 'discard_count': g['discard_count'],
        'table': [{'attack': t['attack'], 'defend': t['defend']} for t in g['table']],
        'my_hand': g['hands'][pid],
        'attacker_idx': g['attacker_idx'], 'defender_idx': g['defender_idx'],
        'i_am_attacker': pid == g['attacker_idx'],
        'i_am_defender': pid == g['defender_idx'],
        'loser': g['loser'],
        'i_lost': (g['loser'] == pid) if g['loser'] is not None else False,
        'draw': g['loser'] == -1,
        'legal_attacks': legal_attacks(g, pid),
        'legal_defends': legal_defends(g, pid),
        'legal_transfers': legal_transfers(g, pid),
        'can_take': (g['phase'] == 'battle' and pid == g['defender_idx']
                     and undefended_count(g) > 0),
        'can_pass': (g['phase'] == 'battle' and pid == g['attacker_idx']
                     and bool(g['table']) and undefended_count(g) == 0
                     and (g['bite_start'] is None
                          or time.time() - g['bite_start'] >= BITE_GRACE)),
        'can_transfer': bool(legal_transfers(g, pid)),
        'bite_grace_left': bite_grace_left,
        'bite_seconds_left': bite_seconds_left,
        'waiting_opponents': not all(g['occupied']),
        'log': g['log'][-20:], 'history': g['history'],
    }


@bp.before_request
def _tick():
    g, _ = _current()
    if g is None:
        return
    with LOCK:
        force_pass_if_timeout(g)


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
        return jsonify({'ok': True, 'state': build_state(g, pid)})


@bp.route('/api/action', methods=['POST'])
def api_action():
    with LOCK:
        g, pid = _current()
        if g is None:
            return jsonify({'ok': False, 'error': 'Нет активной игры'}), 404
        if g['phase'] == 'lobby':
            return jsonify({'ok': False, 'error': 'Ждём остальных игроков'}), 400
        if g['phase'] == 'over':
            return jsonify({'ok': False, 'error': 'Партия окончена'}), 400
        data = request.get_json(silent=True) or {}
        action = data.get('action')
        card = data.get('card')
        if action == 'attack':
            err = do_attack(g, pid, card)
        elif action == 'defend':
            err = do_defend(g, pid, card)
        elif action == 'transfer':
            err = do_transfer(g, pid, card)
        elif action == 'take':
            err = do_take(g, pid)
        elif action == 'pass':
            err = do_pass(g, pid)
        else:
            err = 'Неизвестное действие'
        if err:
            return jsonify({'ok': False, 'error': err}), 400
        record(g)
        resp = build_state(g, pid)
        resp['ok'] = True
        return jsonify(resp)


@bp.route('/api/reset', methods=['POST'])
def api_reset():
    with LOCK:
        g, pid = _current()
        if g is None:
            return jsonify({'ok': False, 'error': 'Нет игры'}), 404
        old_tokens = g['tokens']
        old_names = g['names']
        num = g['num_players']
        code = g['id']
        newg = new_game(num, code)
        newg['tokens'] = old_tokens
        newg['names'] = old_names
        newg['occupied'] = [t is not None for t in old_tokens]
        newg['log'].append('Новая партия')
        GAMES[code] = newg
        maybe_start(newg)
        return jsonify({'ok': True})

def has_player(code, token):
    with LOCK:
        g = GAMES.get(code)
        if not g:
            return False
        return _find_player(g, token) is not None
