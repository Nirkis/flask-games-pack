# -*- coding: utf-8 -*-
"""LIDAR — тактическая игра 2-4: конусное сканирование, ракеты, ложные цели."""
import math
import random
import threading
import time

from flask import Blueprint, jsonify, request, session

bp = Blueprint('lidar', __name__, url_prefix='/ld')


GAME_META = {
    'code': 'ld', 'prefix': 'LD', 'name': 'LIDAR',
    'players': '2-4', 'template': 'lidar.html',
    'modes': [2, 3, 4], 'order': 60,
    'realtime': False,
}

BOARD_SIZE = 20
MIN_PLAYERS = 2
MAX_PLAYERS = 4
START_HP = 100
ROCKET_DAMAGE = 34
MOVE_COOLDOWN = 0.6
LIDAR_COOLDOWN = 3.0
LIDAR_RANGE = 10.0
LIDAR_HALF_ANGLE_DEG = 22.5
ROCKET_COOLDOWN = 2.0
DECOY_COUNT = 30
EVENT_TTL = 2.5
KNOWN_TTL = 25.0
MAX_EVENTS = 120
MAX_LOG = 60

GAMES = {}
LOCK = threading.RLock()
CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'

DIRECTIONS = {
    'N':  (0, -1), 'NE': (1, -1), 'E': (1, 0), 'SE': (1, 1),
    'S':  (0, 1),  'SW': (-1, 1), 'W': (-1, 0), 'NW': (-1, -1),
}
LIDAR_HALF_ANGLE_RAD = math.radians(LIDAR_HALF_ANGLE_DEG)


def _gen_code():
    return 'LD-' + ''.join(random.choice(CODE_ALPHABET) for _ in range(4))


def _gen_token():
    return '%d-%d' % (random.randint(0, 10**9), id(object()) % 10**9)


def _in_bounds(x, y):
    return 0 <= x < BOARD_SIZE and 0 <= y < BOARD_SIZE


def _spawn_positions(n):
    corners = [(2, 2), (BOARD_SIZE - 3, BOARD_SIZE - 3),
               (BOARD_SIZE - 3, 2), (2, BOARD_SIZE - 3)]
    random.shuffle(corners)
    return corners[:n]


def _spawn_decoys(exclude, count):
    used = set(exclude)
    cells = [(x, y) for x in range(BOARD_SIZE) for y in range(BOARD_SIZE)
             if (x, y) not in used]
    random.shuffle(cells)
    return [{'x': x, 'y': y, 'alive': True} for (x, y) in cells[:count]]


def new_game(num_players, code):
    return {
        'id': code,
        'num_players': num_players,
        'names': ['Игрок %d' % (i + 1) for i in range(num_players)],
        'tokens': [None] * num_players,
        'occupied': [False] * num_players,
        'positions': [None] * num_players,
        'hp': [START_HP] * num_players,
        'alive': [False] * num_players,
        'kills': [0] * num_players,
        'cooldowns': [{'move': 0.0, 'lidar': 0.0, 'rocket': 0.0}
                      for _ in range(num_players)],
        'known': [{} for _ in range(num_players)],   # "x,y" -> timestamp
        'decoys': [],
        'events': [],
        'phase': 'lobby',
        'winner': None,
        'log': [],
        'event_id': 0,
        'created': time.time(),
    }


def add_log(g, text, visible_to=None):
    g['log'].append({'text': text, 'visible_to': visible_to})
    if len(g['log']) > MAX_LOG:
        g['log'] = g['log'][-MAX_LOG:]


def _push_event(g, ev):
    g['event_id'] += 1
    ev['id'] = g['event_id']
    ev['t'] = time.time()
    g['events'].append(ev)
    if len(g['events']) > MAX_EVENTS:
        g['events'] = g['events'][-MAX_EVENTS:]


def _raycast_cells(x, y, dx, dy):
    out = []
    cx, cy = x + dx, y + dy
    while _in_bounds(cx, cy):
        out.append((cx, cy))
        cx += dx
        cy += dy
    return out


def _cone_cells(px, py, dx, dy):
    """Все клетки внутри конуса: направление (dx,dy), полуугол, дальность."""
    base = math.atan2(dy, dx)
    cells = []
    for cy in range(BOARD_SIZE):
        for cx in range(BOARD_SIZE):
            if cx == px and cy == py:
                continue
            vx, vy = cx - px, cy - py
            dist = math.hypot(vx, vy)
            if dist > LIDAR_RANGE:
                continue
            ang = math.atan2(vy, vx)
            diff = ang - base
            while diff > math.pi:
                diff -= 2 * math.pi
            while diff < -math.pi:
                diff += 2 * math.pi
            if abs(diff) <= LIDAR_HALF_ANGLE_RAD:
                cells.append((cx, cy, dist))
    cells.sort(key=lambda t: t[2])
    return [(x, y) for (x, y, _) in cells]


def _host_idx(g):
    for i in range(g['num_players']):
        if g['occupied'][i]:
            return i
    return None


def _check_winner(g):
    alive = [i for i in range(g['num_players']) if g['alive'][i]]
    if len(alive) <= 1:
        g['phase'] = 'over'
        g['winner'] = alive[0] if alive else None
        if g['winner'] is not None:
            add_log(g, '🏆 Победитель: %s' % g['names'][g['winner']])
        return True
    return False


# ---------- действия ----------

def _do_move(g, idx, dx, dy):
    now = time.time()
    if g['cooldowns'][idx]['move'] > now:
        return 'Перезарядка движения'
    px, py = g['positions'][idx]
    nx, ny = px + dx, py + dy
    if not _in_bounds(nx, ny):
        return 'За границей поля'
    for j in range(g['num_players']):
        if j != idx and g['alive'][j] and g['positions'][j] == (nx, ny):
            return 'Клетка занята'
    g['positions'][idx] = (nx, ny)
    g['cooldowns'][idx]['move'] = now + MOVE_COOLDOWN
    return None


def _do_lidar(g, idx, dx, dy):
    now = time.time()
    if g['cooldowns'][idx]['lidar'] > now:
        return 'Перезарядка сканера'
    px, py = g['positions'][idx]
    cells = _cone_cells(px, py, dx, dy)
    cell_set = set(cells)

    detected = set()

    # игроки в конусе
    for j in range(g['num_players']):
        if j == idx or not g['alive'][j]:
            continue
        if g['positions'][j] in cell_set:
            detected.add(g['positions'][j])

    # мусор в конусе
    for d in g['decoys']:
        if d['alive'] and (d['x'], d['y']) in cell_set:
            detected.add((d['x'], d['y']))

    for (x, y) in detected:
        g['known'][idx]['%d,%d' % (x, y)] = now

    g['cooldowns'][idx]['lidar'] = now + LIDAR_COOLDOWN
    _push_event(g, {
        'type': 'lidar',
        'by': idx,
        'from': [px, py],
        'dir': [dx, dy],
        'range': LIDAR_RANGE,
        'half_angle_deg': LIDAR_HALF_ANGLE_DEG,
        'targets': [{'x': x, 'y': y} for (x, y) in detected],
    })
    add_log(g, '📡 Сканирование: объектов — %d' % len(detected), visible_to=[idx])
    return None


def _do_rocket(g, idx, dx, dy):
    now = time.time()
    if g['cooldowns'][idx]['rocket'] > now:
        return 'Перезарядка ракет'
    px, py = g['positions'][idx]
    cells = _raycast_cells(px, py, dx, dy)

    hit_player = None
    hit_decoy = None
    hit_pos = None

    for (cx, cy) in cells:
        for j in range(g['num_players']):
            if j == idx or not g['alive'][j]:
                continue
            if g['positions'][j] == (cx, cy):
                hit_player = j
                hit_pos = (cx, cy)
                break
        for d in g['decoys']:
            if d['alive'] and d['x'] == cx and d['y'] == cy:
                hit_decoy = d
                if hit_pos is None:
                    hit_pos = (cx, cy)
                break
        if hit_player is not None or hit_decoy is not None:
            break

    end_pos = hit_pos or (cells[-1] if cells else (px, py))
    g['cooldowns'][idx]['rocket'] = now + ROCKET_COOLDOWN

    _push_event(g, {
        'type': 'rocket',
        'by': idx,
        'from': [px, py],
        'to': list(end_pos),
        'hit': bool(hit_player is not None or hit_decoy is not None),
    })

    if hit_decoy is not None:
        hit_decoy['alive'] = False
        g['known'][idx].pop('%d,%d' % (hit_decoy['x'], hit_decoy['y']), None)
        _push_event(g, {
            'type': 'decoy_broken',
            'by': idx,
            'at': [hit_decoy['x'], hit_decoy['y']],
        })
        add_log(g, '🚀 Ракета разнесла объект — это был мусор', visible_to=[idx])

    if hit_player is not None:
        g['hp'][hit_player] -= ROCKET_DAMAGE
        killed = False
        if g['hp'][hit_player] <= 0:
            g['hp'][hit_player] = 0
            g['alive'][hit_player] = False
            g['kills'][idx] += 1
            killed = True
        _push_event(g, {
            'type': 'hit', 'by': idx,
            'target': hit_player, 'at': list(hit_pos),
        })
        if killed:
            _push_event(g, {
                'type': 'death', 'public': True,
                'target': hit_player, 'at': list(hit_pos),
            })
            add_log(g, '💥 %s уничтожен' % g['names'][hit_player])
        else:
            add_log(g, '💢 Попадание в %s (HP %d)' % (
                g['names'][hit_player], g['hp'][hit_player]),
                    visible_to=[idx, hit_player])
        _check_winner(g)
    elif hit_decoy is None:
        add_log(g, '🚀 Промах', visible_to=[idx])

    return None


def _find_idx(g, token):
    if token is None:
        return None
    try:
        return g['tokens'].index(token)
    except ValueError:
        return None


def _current():
    code = session.get('code')
    tok = session.get('player_token')
    if not code or not tok:
        return None, None
    g = GAMES.get(code)
    if not g:
        return None, None
    idx = _find_idx(g, tok)
    if idx is None:
        return None, None
    return g, idx


def build_state(g, viewer_idx):
    if g is None:
        return {'joined': False, 'phase': 'no_game'}
    if viewer_idx is None:
        return {'joined': False, 'phase': 'lobby',
                'num_players': g['num_players']}

    now = time.time()
    g['events'] = [e for e in g['events'] if now - e['t'] < EVENT_TTL]

    known = g['known'][viewer_idx]
    for k in list(known.keys()):
        if now - known[k] > KNOWN_TTL:
            del known[k]

    over = g['phase'] == 'over'

    players = []
    for i in range(g['num_players']):
        p = {
            'index': i,
            'name': g['names'][i],
            'alive': g['alive'][i] if g['phase'] != 'lobby' else True,
            'hp': g['hp'][i] if g['phase'] != 'lobby' else START_HP,
            'max_hp': START_HP,
            'kills': g['kills'][i],
            'is_me': i == viewer_idx,
            'occupied': g['occupied'][i],
        }
        if over and g['positions'][i]:
            p['x'] = g['positions'][i][0]
            p['y'] = g['positions'][i][1]
        players.append(p)

    my_pos = g['positions'][viewer_idx] if g['phase'] != 'lobby' else None
    cds = g['cooldowns'][viewer_idx]
    cooldowns = {
        'move': max(0.0, cds['move'] - now),
        'lidar': max(0.0, cds['lidar'] - now),
        'rocket': max(0.0, cds['rocket'] - now),
    }

    known_list = []
    for k, t in known.items():
        try:
            x, y = k.split(',')
            known_list.append({'x': int(x), 'y': int(y), 'age': now - t})
        except Exception:
            pass

    visible_events = []
    for ev in g['events']:
        by = ev.get('by')
        tgt = ev.get('target')
        if by == viewer_idx or ev.get('public') or tgt == viewer_idx:
            e2 = dict(ev)
            e2['age'] = now - ev['t']
            visible_events.append(e2)

    log_view = []
    for e in g['log'][-20:]:
        vt = e.get('visible_to')
        if vt is None or viewer_idx in vt:
            log_view.append(e['text'])

    decoys_view = []
    if over:
        decoys_view = [{'x': d['x'], 'y': d['y'], 'alive': d['alive']}
                       for d in g['decoys']]

    return {
        'joined': True,
        'slot': viewer_idx,
        'game_id': g['id'],
        'phase': g['phase'],
        'num_players': g['num_players'],
        'board_size': BOARD_SIZE,
        'min_players': MIN_PLAYERS,
        'max_players': MAX_PLAYERS,
        'players': players,
        'my_position': {'x': my_pos[0], 'y': my_pos[1]} if my_pos else None,
        'cooldowns': cooldowns,
        'known': known_list,
        'events': visible_events,
        'log': log_view,
        'winner': g['winner'],
        'decoys': decoys_view,
        'lidar_range': LIDAR_RANGE,
        'lidar_half_angle_deg': LIDAR_HALF_ANGLE_DEG,
        'i_am_host': _host_idx(g) == viewer_idx,
        'can_start': (g['phase'] == 'lobby'
                      and sum(1 for x in g['occupied'] if x) >= MIN_PLAYERS),
    }


# ---------- публичное API для app.py ----------

def create_game(name):
    with LOCK:
        code = _gen_code()
        while code in GAMES:
            code = _gen_code()
        g = new_game(MAX_PLAYERS, code)
        g['names'][0] = name
        g['occupied'][0] = True
        tok = _gen_token()
        g['tokens'][0] = tok
        GAMES[code] = g
        add_log(g, '%s создал игру' % name)
    return code, tok, None


def join_game(code, name):
    with LOCK:
        g = GAMES.get(code)
        if not g:
            return None, 'Игра не найдена'
        if g['phase'] != 'lobby':
            return None, 'Игра уже началась'
        for i in range(g['num_players']):
            if not g['occupied'][i]:
                g['occupied'][i] = True
                tok = _gen_token()
                g['tokens'][i] = tok
                g['names'][i] = name
                add_log(g, '%s присоединился' % name)
                return tok, None
        return None, 'Мест нет'


def leave(code, token):
    with LOCK:
        g = GAMES.get(code)
        if not g:
            return
        i = _find_idx(g, token)
        if i is None:
            return
        if g['phase'] == 'lobby':
            g['occupied'][i] = False
            g['tokens'][i] = None
            if not any(g['occupied']):
                GAMES.pop(code, None)
        elif g['phase'] == 'battle':
            if g['alive'][i]:
                g['alive'][i] = False
                g['hp'][i] = 0
                add_log(g, '%s вышел из боя' % g['names'][i])
                _push_event(g, {
                    'type': 'death', 'public': True,
                    'target': i,
                    'at': list(g['positions'][i]) if g['positions'][i] else None,
                })
                _check_winner(g)


def has_player(code, token):
    with LOCK:
        g = GAMES.get(code)
        if not g:
            return False
        return _find_idx(g, token) is not None


# ---------- endpoints ----------

@bp.route('/api/state')
def api_state():
    with LOCK:
        g, idx = _current()
        if g is None:
            return jsonify({'ok': False, 'error': 'no_game'}), 404
        return jsonify({'ok': True, 'state': build_state(g, idx)})


@bp.route('/api/start', methods=['POST'])
def api_start():
    with LOCK:
        g, idx = _current()
        if g is None:
            return jsonify({'ok': False, 'error': 'Нет игры'}), 404
        if g['phase'] != 'lobby':
            return jsonify({'ok': False, 'error': 'Уже началось'}), 409
        occupied = [i for i in range(g['num_players']) if g['occupied'][i]]
        if len(occupied) < MIN_PLAYERS:
            return jsonify({'ok': False,
                            'error': 'Нужно минимум %d игрока' % MIN_PLAYERS}), 409
        if _host_idx(g) != idx:
            return jsonify({'ok': False, 'error': 'Только хост может начать'}), 403

        new_names = [g['names'][i] for i in occupied]
        new_tokens = [g['tokens'][i] for i in occupied]
        n = len(occupied)
        positions = _spawn_positions(n)

        g['num_players'] = n
        g['names'] = new_names
        g['tokens'] = new_tokens
        g['occupied'] = [True] * n
        g['positions'] = positions
        g['decoys'] = _spawn_decoys(positions, DECOY_COUNT)
        g['hp'] = [START_HP] * n
        g['alive'] = [True] * n
        g['kills'] = [0] * n
        g['cooldowns'] = [{'move': 0.0, 'lidar': 0.0, 'rocket': 0.0}
                          for _ in range(n)]
        g['known'] = [{} for _ in range(n)]
        g['events'] = []
        g['phase'] = 'battle'
        add_log(g, '🎮 Бой начался! Игроков: %d, мусора: %d' % (n, DECOY_COUNT))
        return jsonify({'ok': True})


def _do_action(kind):
    g, idx = _current()
    if g is None:
        return jsonify({'ok': False, 'error': 'Нет игры'}), 404
    if g['phase'] != 'battle':
        return jsonify({'ok': False, 'error': 'Не бой'}), 409
    data = request.get_json(silent=True) or {}
    d = (data.get('dir') or '').upper()
    if d not in DIRECTIONS:
        return jsonify({'ok': False, 'error': 'Неверное направление'}), 400
    dx, dy = DIRECTIONS[d]
    fn = {'move': _do_move, 'lidar': _do_lidar, 'rocket': _do_rocket}[kind]
    err = fn(g, idx, dx, dy)
    if err:
        return jsonify({'ok': False, 'error': err}), 400
    return jsonify({'ok': True, 'state': build_state(g, idx)})


@bp.route('/api/move', methods=['POST'])
def api_move():
    with LOCK:
        return _do_action('move')


@bp.route('/api/lidar', methods=['POST'])
def api_lidar():
    with LOCK:
        return _do_action('lidar')


@bp.route('/api/rocket', methods=['POST'])
def api_rocket():
    with LOCK:
        return _do_action('rocket')


@bp.route('/api/reset', methods=['POST'])
def api_reset():
    with LOCK:
        g, idx = _current()
        if g is None:
            return jsonify({'ok': False, 'error': 'Нет игры'}), 404
        old_names = list(g['names'])
        old_tokens = list(g['tokens'])
        n = g['num_players']
        code = g['id']
        newg = new_game(n, code)
        newg['names'] = old_names
        newg['tokens'] = old_tokens
        newg['occupied'] = [t is not None for t in old_tokens]
        newg['log'].append({'text': 'Новая партия', 'visible_to': None})
        GAMES[code] = newg
        return jsonify({'ok': True})
