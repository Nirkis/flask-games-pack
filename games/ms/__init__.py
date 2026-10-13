import random
import string
import time

from flask import Blueprint, jsonify, request

from games import game_base
from games.data_loader import load

GAME_META = {
    'code': 'ms',
    'prefix': 'MS',
    'name': 'Морской бой: Четыре флота',
    'players': '2-4',
    'template': 'ms/index.html',
    'order': 60,
    'modes': [2, 3, 4],
    'realtime': False,
}

bp = Blueprint('ms', __name__, url_prefix='/ms', template_folder='templates')

RULES = load('rules', schema={
    'field': int,
    'cols': str,
    'fleet': list,
    'player_colors': list,
})

FIELD = RULES['field']
COLS = RULES['cols']
FLEET = RULES['fleet']
PLAYER_COLORS = RULES['player_colors']


def _gen_token():
    return ''.join(random.choices(string.ascii_letters + string.digits, k=24))


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
    if not g['order']:
        g['turn'] = None
        return
    n = len(g['order'])
    for _ in range(n):
        g['order_pos'] = (g['order_pos'] + 1) % n
        p = g['order'][g['order_pos']]
        if not g['eliminated'][p]:
            g['turn'] = p
            return
    g['turn'] = None


def _check_finish(g):
    if g['phase'] in ('lobby', 'placing'):
        return
    alive = [i for i in range(g['num_players'])
             if not g['eliminated'][i] and g['occupied'][i]]
    if len(alive) <= 1 and g['phase'] != 'finished':
        g['phase'] = 'finished'
        g['winner'] = alive[0] if alive else None
        if g['winner'] is not None:
            _log(g, 'Победа: ' + str(g['names'][g['winner']]))


def _start_battle(g):
    n = g['num_players']
    alive = [i for i in range(n) if not g['eliminated'][i] and g['occupied'][i]]
    for i in alive:
        if g['boards'][i] is None:
            pl = g['placement'][i]
            if pl and not any(p is None for p in pl):
                g['boards'][i] = _placement_to_board(pl)
            else:
                rp = _random_placement()
                if rp is None:
                    rp = [None] * len(FLEET)
                g['boards'][i] = _placement_to_board(rp)
        g['ready'][i] = True

    if len(alive) <= 1:
        g['phase'] = 'finished'
        g['winner'] = alive[0] if alive else None
        if g['winner'] is not None:
            _log(g, 'Победа: ' + str(g['names'][g['winner']]))
        return

    order = list(alive)
    random.shuffle(order)
    g['order'] = order
    g['order_pos'] = -1
    g['phase'] = 'battle'
    g['turn'] = None
    _advance_turn(g)
    if g['turn'] is None:
        g['phase'] = 'finished'
        g['winner'] = alive[0] if alive else None
        return
    _log(g, 'Бой начался! Первым стреляет ' + str(g['names'][g['turn']]))
    _check_finish(g)


def _maybe_start(g):
    if g['phase'] == 'lobby':
        if not all(g['occupied']):
            return
        for i in range(g['num_players']):
            if g['placement'][i] is None:
                g['placement'][i] = [None] * len(FLEET)
        g['phase'] = 'placing'
        _log(g, 'Все на месте. Расставьте флот.')
        return
    if g['phase'] == 'placing':
        alive = [i for i in range(g['num_players'])
                 if not g['eliminated'][i] and g['occupied'][i]]
        if len(alive) <= 1:
            _start_battle(g)
            return
        for i in alive:
            if not g['ready'][i]:
                return
        _start_battle(g)


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


def _build_state(g, pid):
    if g is None or pid is None:
        return {'joined': False, 'phase': 'no_game'}

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
        'colors': PLAYER_COLORS,
        'log': g['log'][-40:],
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
        pl = g['placement'][pid]
        if pl is None:
            pl = [None] * len(FLEET)
        base.update({
            'joined': True,
            'can_act': not g['ready'][pid],
            'ready': g['ready'][:],
            'my_ready': g['ready'][pid],
            'eliminated': g['eliminated'][:],
            'placement': _placement_view(pl),
        })
        return base

    enemies = []
    for i in range(g['num_players']):
        if i == pid or not g['occupied'][i]:
            continue
        enemies.append(_enemy_view(g, i))

    base.update({
        'joined': True,
        'can_act': g['phase'] == 'battle' and g['turn'] == pid and not g['eliminated'][pid],
        'eliminated': g['eliminated'][:],
        'turn': g['turn'],
        'is_my_turn': g['turn'] == pid,
        'winner': g['winner'],
        'my_board': _board_view(g['boards'][pid]),
        'enemies': enemies,
    })
    return base


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
    if not g['occupied'][target] or g['eliminated'][target]:
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

    if sid is None:
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
    if g['phase'] == 'battle' and g['turn'] is not None and g['eliminated'][g['turn']]:
        _advance_turn(g)
    return None


def _do_action(g, pid, data):
    action = data.get('action')

    if action == 'place_ship':
        if g['phase'] != 'placing':
            return 'Сейчас нельзя расставлять'
        if g['ready'][pid]:
            return 'Вы уже готовы'
        try:
            idx = int(data.get('idx'))
            r = int(data.get('r'))
            c = int(data.get('c'))
        except (TypeError, ValueError):
            return 'Неверные данные'
        horiz = bool(data.get('horiz'))
        if not (0 <= idx < len(FLEET)):
            return 'Неверный корабль'
        if not (0 <= r < FIELD and 0 <= c < FIELD):
            return 'Неверные координаты'
        placed = g['placement'][pid]
        if placed is None:
            placed = [None] * len(FLEET)
            g['placement'][pid] = placed
        if placed[idx] is not None:
            return 'Этот корабль уже стоит'
        size = FLEET[idx]
        cells = _can_place(placed, size, r, c, horiz, skip_idx=idx)
        if not cells:
            return 'Здесь нельзя поставить корабль'
        placed[idx] = {'cells': cells, 'horiz': horiz}
        return None

    if action == 'remove_ship':
        if g['phase'] != 'placing' or g['ready'][pid]:
            return 'Сейчас нельзя'
        try:
            idx = int(data.get('idx'))
        except (TypeError, ValueError):
            return 'Неверный корабль'
        placed = g['placement'][pid]
        if not placed or not (0 <= idx < len(FLEET)):
            return 'Неверный корабль'
        if placed[idx] is None:
            return 'Корабль не стоит'
        placed[idx] = None
        return None

    if action == 'clear':
        if g['phase'] != 'placing' or g['ready'][pid]:
            return 'Сейчас нельзя'
        g['placement'][pid] = [None] * len(FLEET)
        return None

    if action == 'randomize':
        if g['phase'] != 'placing' or g['ready'][pid]:
            return 'Сейчас нельзя'
        rp = _random_placement()
        if rp is None:
            return 'Не удалось расставить'
        g['placement'][pid] = rp
        return None

    if action == 'ready':
        if g['phase'] != 'placing' or g['ready'][pid]:
            return 'Сейчас нельзя'
        placed = g['placement'][pid]
        if placed is None or any(p is None for p in placed):
            return 'Сначала расставьте все корабли'
        g['ready'][pid] = True
        g['boards'][pid] = _placement_to_board(placed)
        _maybe_start(g)
        return None

    if action == 'shoot':
        return _do_shoot(g, pid, data)

    if action == 'reset':
        if g['phase'] not in ('battle', 'finished'):
            return 'Нельзя перезапустить'
        n = g['num_players']
        g['phase'] = 'placing'
        g['ready'] = [False] * n
        g['placement'] = [None] * n
        g['boards'] = [None] * n
        g['eliminated'] = [False] * n
        g['order'] = []
        g['order_pos'] = -1
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
        return None

    return 'Неизвестное действие'


@bp.route('/api/state', methods=['GET'])
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
        return jsonify({'ok': True, 'state': _build_state(g, pid)})


def _new_game(code, mode=2):
    try:
        n = int(mode)
    except (TypeError, ValueError):
        n = 2
    if n not in (2, 3, 4):
        n = 2
    return {
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
        'order_pos': -1,
        'turn': None,
        'log': [],
        'winner': None,
        'created': time.time(),
    }


def _attach_player(g, slot, name, tok):
    g['occupied'][slot] = True
    g['tokens'][slot] = tok
    g['names'][slot] = name or ('Игрок ' + str(slot + 1))


def _get_tokens(g):
    return g['tokens']


def _drop_player(g, tok):
    if tok is None:
        return
    try:
        i = g['tokens'].index(tok)
    except ValueError:
        return
    if g['phase'] == 'lobby':
        g['occupied'][i] = False
        g['tokens'][i] = None
        g['names'][i] = None
        return
    if g['eliminated'][i]:
        return
    g['eliminated'][i] = True
    if g['phase'] == 'placing':
        g['ready'][i] = True
        _log(g, str(g['names'][i]) + ' покинул партию')
        _maybe_start(g)
        return
    _log(g, str(g['names'][i]) + ' покинул партию')
    _check_finish(g)
    if g['phase'] == 'battle' and g['turn'] == i:
        _advance_turn(g)


def _player_names(g):
    return [g['names'][i] for i in range(g['num_players']) if g['occupied'][i]]


def _phase_of(g):
    return g['phase']


server = game_base.GameServer(
    prefix='MS',
    max_players=4,
    min_players=2,
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
