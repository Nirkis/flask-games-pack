import random
import string
import threading
import time

from flask import Blueprint, jsonify, request, session

GAME_META = {
    'code': 'ms',
    'prefix': 'MS',
    'name': 'Морской бой: Четыре флота',
    'players': '2-4',
    'template': 'battleship.html',
    'order': 60,
    'modes': [2, 3, 4],
    'realtime': False,
}

bp = Blueprint('ms', __name__, url_prefix='/ms')

GAMES = {}
LOCK = threading.RLock()

FIELD = 10
FLEET = [4, 3, 3, 2, 2, 2, 1, 1, 1, 1]
COLS = 'ABCDEFGHIJ'

_tick_started = False


def _gen_code():
    return 'MS-' + ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))


def _gen_token():
    return ''.join(random.choices(string.ascii_letters + string.digits, k=24))


def _find_player(g, tok):
    if not tok:
        return None
    for i, t in enumerate(g['tokens']):
        if t and t == tok:
            return i
    return None


def _coord(r, c):
    return COLS[c] + str(r + 1)


def _log(g, text):
    g['log'].append(text)
    if len(g['log']) > 100:
        g['log'] = g['log'][-100:]


def _cells_for(size, r, c, horiz):
    if horiz:
        return [(r, c + i) for i in range(size)]
    return [(r + i, c) for i in range(size)]


def _can_place(placed, size, r, c, horiz, skip_idx=None):
    cells = _cells_for(size, r, c, horiz)
    for (rr, cc) in cells:
        if not (0 <= rr < FIELD and 0 <= cc < FIELD):
            return None
    occupied = set()
    for i, p in enumerate(placed):
        if p is None or i == skip_idx:
            continue
        for (rr, cc) in p['cells']:
            occupied.add((rr, cc))
    for (rr, cc) in cells:
        if (rr, cc) in occupied:
            return None
    for (rr, cc) in cells:
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                nr, nc = rr + dr, cc + dc
                if 0 <= nr < FIELD and 0 <= nc < FIELD and (nr, nc) in occupied:
                    return None
    return cells


def _random_placement():
    order = sorted(range(len(FLEET)), key=lambda i: -FLEET[i])
    for _ in range(300):
        placed = [None] * len(FLEET)
        ok = True
        for i in order:
            size = FLEET[i]
            done = False
            for _ in range(200):
                horiz = random.random() < 0.5
                r = random.randint(0, FIELD - 1)
                c = random.randint(0, FIELD - 1)
                cells = _can_place(placed, size, r, c, horiz)
                if cells:
                    placed[i] = {'cells': cells, 'horiz': horiz}
                    done = True
                    break
            if not done:
                ok = False
                break
        if ok:
            return placed
    return None


def _placement_to_board(placed):
    grid = [[None] * FIELD for _ in range(FIELD)]
    ships = []
    for i, p in enumerate(placed):
        if p is None:
            continue
        sid = len(ships)
        for (r, c) in p['cells']:
            grid[r][c] = sid
        ships.append({'size': FLEET[i], 'cells': list(p['cells'])})
    return {'ships': ships, 'grid': grid, 'shots': {}}


def _ship_hits(b, ship):
    n = 0
    for (r, c) in ship['cells']:
        s = b['shots'].get((r, c))
        if s and s.get('res') == 'hit':
            n += 1
    return n


def _alive_ships(b):
    if not b:
        return 0
    n = 0
    for s in b['ships']:
        if _ship_hits(b, s) < s['size']:
            n += 1
    return n


def _all_sunk(b):
    for s in b['ships']:
        if _ship_hits(b, s) < s['size']:
            return False
    return True


def _open_neighbors(board, cells, by):
    for (r, c) in cells:
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                nr, nc = r + dr, c + dc
                if 0 <= nr < FIELD and 0 <= nc < FIELD:
                    if (nr, nc) not in board['shots']:
                        board['shots'][(nr, nc)] = {'res': 'miss', 'by': by}


def _advance_turn(g):
    n = g['num_players']
    if n == 0:
        g['turn'] = None
        return
    for _ in range(n):
        g['order_pos'] = (g['order_pos'] + 1) % n
        p = g['order'][g['order_pos']]
        if not g['eliminated'][p]:
            g['turn'] = p
            return
    g['turn'] = None


def _check_finish(g):
    if g['phase'] == 'lobby':
        return
    alive = [i for i in range(g['num_players']) if not g['eliminated'][i]]
    if len(alive) <= 1 and g['phase'] != 'finished':
        g['phase'] = 'finished'
        g['winner'] = alive[0] if alive else None
        if g['winner'] is not None:
            _log(g, 'Победа: ' + str(g['names'][g['winner']]))


def _ensure_tick_thread():
    global _tick_started
    with LOCK:
        if _tick_started:
            return
        _tick_started = True
    threading.Thread(target=_tick_loop, daemon=True).start()


def _tick_loop():
    while True:
        time.sleep(1.0)
        try:
            with LOCK:
                for code, g in list(GAMES.items()):
                    try:
                        _tick_one(g)
                    except Exception:
                        pass
        except Exception:
            pass


def _tick_one(g):
    if g['phase'] not in ('placing', 'battle'):
        return
    now = time.time()
    changed = False
    for i in range(g['num_players']):
        if g['eliminated'][i] or not g['occupied'][i]:
            continue
        ls = g['last_seen'][i]
        if ls and (now - ls) > 180:
            g['eliminated'][i] = True
            _log(g, str(g['names'][i]) + ' потерял связь и выбывает')
            if g['phase'] == 'placing':
                g['ready'][i] = True
            changed = True
    if not changed:
        return
    if g['phase'] == 'placing':
        _maybe_start_battle(g)
        return
    _check_finish(g)
    if g['phase'] == 'battle' and g['turn'] is not None and g['eliminated'][g['turn']]:
        _advance_turn(g)


def _start_battle(g):
    n = g['num_players']
    alive = [i for i in range(n) if not g['eliminated'][i]]
    for i in alive:
        if g['boards'][i] is None:
            pl = g['placement'][i]
            if pl and not any(p is None for p in pl):
                g['boards'][i] = _placement_to_board(pl)
            else:
                rp = _random_placement()
                g['boards'][i] = _placement_to_board(rp)
        g['ready'][i] = True

    if len(alive) <= 1:
        g['phase'] = 'finished'
        g['winner'] = alive[0] if alive else None
        if g['winner'] is not None:
            _log(g, 'Победа: ' + str(g['names'][g['winner']]))
        return

    order = list(range(n))
    random.shuffle(order)
    g['order'] = order
    g['order_pos'] = n - 1
    g['phase'] = 'battle'
    g['turn'] = None
    _advance_turn(g)
    if g['turn'] is None:
        g['phase'] = 'finished'
        g['winner'] = alive[0] if alive else None
        return
    _log(g, 'Бой начался! Первым стреляет ' + str(g['names'][g['turn']]))
    _ensure_tick_thread()
    _check_finish(g)


def _maybe_start_battle(g):
    if g['phase'] != 'placing':
        return
    alive = [i for i in range(g['num_players']) if not g['eliminated'][i]]
    if len(alive) <= 1:
        _start_battle(g)
        return
    for i in alive:
        if not g['ready'][i]:
            return
    _start_battle(g)


def list_active():
    with LOCK:
        out = []
        for code, g in GAMES.items():
            out.append({
                'id': code,
                'phase': g['phase'],
                'players': [p['name'] for p in g['players']],
                'occupied': len(g['players']),
                'started': g['phase'] != 'lobby',
                'created': g.get('created'),
            })
        return out


def _board_view(b):
    if not b:
        return {'ships': [], 'misses': [], 'alive_ships': 0, 'total_ships': 0}
    ships = []
    for s in b['ships']:
        hit_cells = []
        for (r, c) in s['cells']:
            sh = b['shots'].get((r, c))
            if sh and sh.get('res') == 'hit':
                hit_cells.append([r, c, sh.get('by')])
        ships.append({
            'size': s['size'],
            'cells': [[r, c] for (r, c) in s['cells']],
            'hit_cells': hit_cells,
            'sunk': len(hit_cells) >= s['size'],
        })
    misses = []
    for (r, c), v in b['shots'].items():
        if v.get('res') == 'miss':
            misses.append([r, c, v.get('by')])
    return {
        'ships': ships,
        'misses': misses,
        'alive_ships': _alive_ships(b),
        'total_ships': len(b['ships']),
    }


def _enemy_view(g, eid):
    b = g['boards'][eid]
    cells = []
    sunk = []
    if b:
        for (r, c), v in b['shots'].items():
            cells.append([r, c, v.get('res'), v.get('by')])
        for s in b['ships']:
            if _ship_hits(b, s) >= s['size']:
                for (r, c) in s['cells']:
                    sunk.append([r, c])
    return {
        'slot': eid,
        'name': g['names'][eid],
        'eliminated': g['eliminated'][eid],
        'cells': cells,
        'sunk': sunk,
        'alive_ships': _alive_ships(b),
        'total_ships': len(b['ships']) if b else 0,
    }


def _placement_view(placed):
    items = []
    for i, size in enumerate(FLEET):
        p = placed[i] if placed else None
        if p is None:
            items.append({'idx': i, 'size': size, 'placed': False})
        else:
            items.append({
                'idx': i, 'size': size, 'placed': True,
                'cells': [[r, c] for (r, c) in p['cells']],
                'horiz': p['horiz'],
            })
    return items


def build_state(g, pid):
    if g is None:
        return {'joined': False, 'phase': 'no_game'}
    if pid is None:
        return {'joined': False, 'phase': g['phase']}

    base = {
        'game_id': g['id'],
        'num_players': g['num_players'],
        'names': g['names'][:],
        'occupied': g['occupied'][:],
        'phase': g['phase'],
        'slot': pid,
        'my_name': g['names'][pid],
        'fleet_sizes': FLEET,
        'field': FIELD,
        'cols': COLS,
    }

    if g['phase'] == 'lobby':
        base.update({
            'joined': True,
            'can_act': False,
            'waiting_opponents': not all(g['occupied']),
            'is_host': pid == 0,
        })
        return base

    if g['phase'] == 'placing':
        base.update({
            'joined': True,
            'can_act': not g['ready'][pid],
            'ready': g['ready'][:],
            'my_ready': g['ready'][pid],
            'eliminated': g['eliminated'][:],
            'placement': _placement_view(g['placement'][pid]),
        })
        return base

    enemies = []
    for i in range(g['num_players']):
        if i == pid:
            continue
        enemies.append(_enemy_view(g, i))

    base.update({
        'joined': True,
        'can_act': g['phase'] == 'battle' and g['turn'] == pid and not g['eliminated'][pid],
        'eliminated': g['eliminated'][:],
        'turn': g['turn'],
        'is_my_turn': g['turn'] == pid,
        'winner': g['winner'],
        'log': g['log'][-40:],
        'my_board': _board_view(g['boards'][pid]),
        'enemies': enemies,
    })
    return base


def create_game(name, mode=2):
    try:
        n = int(mode)
    except Exception:
        n = 2
    if n not in (2, 3, 4):
        n = 2
    with LOCK:
        code = _gen_code()
        while code in GAMES:
            code = _gen_code()
        g = {
            'id': code,
            'num_players': n,
            'occupied': [False] * n,
            'tokens': [None] * n,
            'names': [None] * n,
            'phase': 'lobby',
            'ready': [False] * n,
            'placement': [None] * n,
            'boards': [None] * n,
            'eliminated': [False] * n,
            'order': [],
            'order_pos': 0,
            'turn': None,
            'log': [],
            'winner': None,
            'last_seen': [0] * n,
        }
        tok = _gen_token()
        g['occupied'][0] = True
        g['tokens'][0] = tok
        g['names'][0] = name or 'Игрок 1'
        g['last_seen'][0] = time.time()
        GAMES[code] = g
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
                tok = _gen_token()
                g['occupied'][i] = True
                g['tokens'][i] = tok
                g['names'][i] = name or ('Игрок ' + str(i + 1))
                g['last_seen'][i] = time.time()
                if all(g['occupied']):
                    for j in range(g['num_players']):
                        if g['placement'][j] is None:
                            g['placement'][j] = [None] * len(FLEET)
                    g['phase'] = 'placing'
                    _log(g, 'Все на месте. Расставьте флот.')
                    _ensure_tick_thread()
                return tok, None
        return None, 'Мест нет'


def has_player(code, token):
    with LOCK:
        g = GAMES.get(code)
        return bool(g) and _find_player(g, token) is not None


def leave(code, token):
    with LOCK:
        g = GAMES.get(code)
        if not g:
            return
        i = _find_player(g, token)
        if i is None:
            return
        if g['phase'] == 'lobby':
            g['occupied'][i] = False
            g['tokens'][i] = None
            g['names'][i] = None
            if not any(g['occupied']):
                GAMES.pop(code, None)
            return
        if g['eliminated'][i]:
            return
        g['eliminated'][i] = True
        if g['phase'] == 'placing':
            g['ready'][i] = True
            _log(g, str(g['names'][i]) + ' покинул партию')
            _maybe_start_battle(g)
            return
        _log(g, str(g['names'][i]) + ' покинул партию')
        _check_finish(g)
        if g['phase'] == 'battle' and g['turn'] == i:
            _advance_turn(g)


def _current():
    with LOCK:
        code = session.get('code')
        tok = session.get('player_token')
        if not code or not tok:
            return None, None
        g = GAMES.get(code)
        if not g:
            return None, None
        i = _find_player(g, tok)
        if i is None:
            return None, None
        return g, i


def _do_shoot(g, pid, data):
    if g['phase'] != 'battle':
        return 'Бой не идёт'
    if g['eliminated'][pid]:
        return 'Вы выбыли'
    if g['turn'] != pid:
        return 'Не ваш ход'
    try:
        target = int(data.get('target'))
        r = int(data.get('r'))
        c = int(data.get('c'))
    except (TypeError, ValueError):
        return 'Неверные координаты'
    if not (0 <= r < FIELD and 0 <= c < FIELD):
        return 'Неверные координаты'
    if target == pid or not (0 <= target < g['num_players']):
        return 'Неверная цель'
    if g['eliminated'][target]:
        return 'Цель уже выбыла'
    tb = g['boards'][target]
    if tb is None:
        return 'Нет поля цели'
    if (r, c) in tb['shots']:
        return 'Сюда уже стреляли'

    my_name = str(g['names'][pid])
    tgt_name = str(g['names'][target])
    coord = _coord(r, c)
    sid = tb['grid'][r][c]
    is_miss = sid is None

    if is_miss:
        tb['shots'][(r, c)] = {'res': 'miss', 'by': pid}
        _log(g, my_name + ' → ' + tgt_name + ' ' + coord + ': мимо')
        _check_finish(g)
        if g['phase'] == 'battle':
            _advance_turn(g)
        return None

    tb['shots'][(r, c)] = {'res': 'hit', 'by': pid}
    ship = tb['ships'][sid]
    if _ship_hits(tb, ship) >= ship['size']:
        _log(g, my_name + ' → ' + tgt_name + ' ' + coord + ': корабль уничтожен!')
        _open_neighbors(tb, ship['cells'], pid)
        if _all_sunk(tb):
            g['eliminated'][target] = True
            _log(g, tgt_name + ' выбывает из боя')
    else:
        _log(g, my_name + ' → ' + tgt_name + ' ' + coord + ': попал')

    _check_finish(g)
    return None


@bp.route('/api/state', methods=['GET'])
def api_state():
    g, pid = _current()
    if g is None:
        return jsonify({'ok': False, 'error': 'Партия не найдена'}), 404
    with LOCK:
        g['last_seen'][pid] = time.time()
        st = build_state(g, pid)
    return jsonify({'ok': True, 'state': st})


@bp.route('/api/action', methods=['POST'])
def api_action():
    g, pid = _current()
    if g is None:
        return jsonify({'ok': False, 'error': 'Партия не найдена'}), 404
    data = request.get_json(silent=True) or {}
    action = data.get('action')
    with LOCK:
        g['last_seen'][pid] = time.time()
        err = None

        if action == 'place_ship':
            if g['phase'] != 'placing':
                err = 'Сейчас нельзя расставлять'
            elif g['ready'][pid]:
                err = 'Вы уже готовы'
            else:
                try:
                    idx = int(data.get('idx'))
                    r = int(data.get('r'))
                    c = int(data.get('c'))
                except (TypeError, ValueError):
                    idx = None
                horiz = bool(data.get('horiz'))
                if idx is None or not (0 <= idx < len(FLEET)):
                    err = 'Неверный корабль'
                elif not (0 <= r < FIELD and 0 <= c < FIELD):
                    err = 'Неверные координаты'
                else:
                    placed = g['placement'][pid]
                    if placed[idx] is not None:
                        err = 'Этот корабль уже стоит'
                    else:
                        size = FLEET[idx]
                        cells = _can_place(placed, size, r, c, horiz, skip_idx=idx)
                        if not cells:
                            err = 'Здесь нельзя поставить корабль'
                        else:
                            placed[idx] = {'cells': cells, 'horiz': horiz}

        elif action == 'remove_ship':
            if g['phase'] != 'placing':
                err = 'Сейчас нельзя'
            elif g['ready'][pid]:
                err = 'Вы уже готовы'
            else:
                try:
                    idx = int(data.get('idx'))
                except (TypeError, ValueError):
                    idx = None
                if idx is None or not (0 <= idx < len(FLEET)):
                    err = 'Неверный корабль'
                else:
                    placed = g['placement'][pid]
                    if placed[idx] is None:
                        err = 'Корабль не стоит'
                    else:
                        placed[idx] = None

        elif action == 'clear':
            if g['phase'] != 'placing':
                err = 'Сейчас нельзя'
            elif g['ready'][pid]:
                err = 'Вы уже готовы'
            else:
                g['placement'][pid] = [None] * len(FLEET)

        elif action == 'randomize':
            if g['phase'] != 'placing':
                err = 'Сейчас нельзя'
            elif g['ready'][pid]:
                err = 'Вы уже готовы'
            else:
                rp = _random_placement()
                if rp is None:
                    err = 'Не удалось расставить'
                else:
                    g['placement'][pid] = rp

        elif action == 'ready':
            if g['phase'] != 'placing':
                err = 'Сейчас нельзя'
            elif g['ready'][pid]:
                err = 'Вы уже готовы'
            else:
                placed = g['placement'][pid]
                if placed is None or any(p is None for p in placed):
                    err = 'Сначала расставьте все корабли'
                else:
                    g['ready'][pid] = True
                    g['boards'][pid] = _placement_to_board(placed)
                    _maybe_start_battle(g)

        elif action == 'shoot':
            err = _do_shoot(g, pid, data)

        else:
            err = 'Неизвестное действие'

        if err:
            return jsonify({'ok': False, 'error': err}), 400
        st = build_state(g, pid)
    return jsonify({'ok': True, 'state': st})


@bp.route('/api/reset', methods=['POST'])
def api_reset():
    g, pid = _current()
    if g is None:
        return jsonify({'ok': False, 'error': 'Партия не найдена'}), 404
    with LOCK:
        if g['phase'] not in ('battle', 'finished'):
            return jsonify({'ok': False, 'error': 'Нельзя перезапустить'}), 400
        n = g['num_players']
        g['phase'] = 'placing'
        g['ready'] = [False] * n
        g['placement'] = [None] * n
        g['boards'] = [None] * n
        g['eliminated'] = [False] * n
        g['order'] = []
        g['order_pos'] = 0
        g['turn'] = None
        g['winner'] = None
        g['log'] = []
        for i in range(n):
            if not g['occupied'][i]:
                g['eliminated'][i] = True
                g['ready'][i] = True
            else:
                g['placement'][i] = [None] * len(FLEET)
        _log(g, 'Новая партия. Расставьте флот.')
        st = build_state(g, pid)
    return jsonify({'ok': True, 'state': st})
