# -*- coding: utf-8 -*-
"""Морской бой — многокомнатная версия."""
import random
import string
import threading
import time

from flask import Blueprint, jsonify, request, session

bp = Blueprint('battleship', __name__, url_prefix='/bs')

BOARD_SIZE = 10
FLEET = [4, 3, 3, 2, 2, 2, 1, 1, 1, 1]
MIN_PLAYERS = 2
MAX_PLAYERS = 3
GAME_TTL = 6 * 3600

GAMES = {}
LOCK = threading.RLock()
CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'

WATER, SHIP, MISS, HIT = 0, 1, 2, 3


def _gen_code():
    return 'BS-' + ''.join(random.choice(CODE_ALPHABET) for _ in range(4))


def gen_token():
    return ''.join(random.choice(string.ascii_letters + string.digits) for _ in range(24))


def in_bounds(r, c):
    return 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE


def empty_grid():
    return [[WATER] * BOARD_SIZE for _ in range(BOARD_SIZE)]


def cells_for(r, c, size, direction):
    if direction == 'h':
        return [(r, c + i) for i in range(size)]
    return [(r + i, c) for i in range(size)]


def can_place(grid, cells):
    for (r, c) in cells:
        if not in_bounds(r, c):
            return False
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                rr, cc = r + dr, c + dc
                if in_bounds(rr, cc) and grid[rr][cc] == SHIP:
                    return False
    return True


def random_ships():
    for _ in range(200):
        grid = empty_grid()
        ships = []
        ok = True
        for size in FLEET:
            placed = False
            for _ in range(500):
                d = random.choice(('h', 'v'))
                r = random.randrange(BOARD_SIZE)
                c = random.randrange(BOARD_SIZE)
                cells = cells_for(r, c, size, d)
                if can_place(grid, cells):
                    for (rr, cc) in cells:
                        grid[rr][cc] = SHIP
                    ships.append({'size': size, 'cells': cells, 'hits': 0})
                    placed = True
                    break
            if not placed:
                ok = False
                break
        if ok:
            return ships
    return []


def validate_ships(raw):
    if not isinstance(raw, list):
        return None, 'Некорректный формат расстановки'
    grid = empty_grid()
    ships = []
    sizes = []
    for item in raw:
        if not isinstance(item, dict):
            return None, 'Некорректный формат корабля'
        try:
            size = int(item.get('size'))
            r = int(item.get('r'))
            c = int(item.get('c'))
        except (TypeError, ValueError):
            return None, 'Некорректные координаты корабля'
        d = 'h' if item.get('dir') == 'h' else 'v'
        if size not in (1, 2, 3, 4):
            return None, 'Недопустимый размер корабля'
        cells = cells_for(r, c, size, d)
        if not can_place(grid, cells):
            return None, 'Корабли пересекаются, касаются или выходят за границы'
        for (rr, cc) in cells:
            grid[rr][cc] = SHIP
        ships.append({'size': size, 'cells': cells, 'hits': 0})
        sizes.append(size)
    if sorted(sizes, reverse=True) != sorted(FLEET, reverse=True):
        return None, 'Неверный состав флота'
    return ships, None


def find_player(game, token):
    for i, p in enumerate(game['players']):
        if p['token'] == token:
            return i, p
    return None, None


def find_ship(player, r, c):
    for s in player['ships']:
        if (r, c) in s['cells']:
            return s
    return None


def coord_str(r, c):
    return '%s%d' % (string.ascii_uppercase[c], r + 1)


def add_log(game, text):
    game['log'].append({'t': time.strftime('%H:%M:%S'), 'text': text})
    if len(game['log']) > 80:
        game['log'] = game['log'][-80:]


def cleanup_games():
    now = time.time()
    for k in [k for k, g in GAMES.items() if now - g['created'] > GAME_TTL]:
        GAMES.pop(k, None)


def new_player(name):
    return {
        'name': name, 'token': gen_token(), 'grid': empty_grid(),
        'ships': [], 'ready': False, 'alive': True,
        'knowledge': {},
        'stats': {'shots': 0, 'hits': 0, 'misses': 0, 'sunk': 0},
        'joined': time.time(),
    }


def alive_indices(game):
    return [i for i, p in enumerate(game['players']) if p['alive']]


def ships_left(player):
    return sum(1 for s in player['ships'] if s['hits'] < s['size'])


def advance_turn(game):
    n = len(game['players'])
    if n == 0:
        return
    for step in range(1, n + 1):
        cand = (game['turn'] + step) % n
        if game['players'][cand]['alive']:
            game['turn'] = cand
            return


def check_over(game):
    if game['phase'] != 'battle':
        return
    alive = alive_indices(game)
    if len(alive) <= 1:
        game['phase'] = 'over'
        game['winner'] = alive[0] if alive else None
        if game['winner'] is not None:
            add_log(game, '🏆 Победитель — %s!' % game['players'][game['winner']]['name'])
        else:
            add_log(game, 'Ничья: все игроки выбиты.')


def mark_surroundings(target, shooter, target_idx, ship):
    know = shooter['knowledge'].setdefault(str(target_idx), {})
    for (r, c) in ship['cells']:
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                rr, cc = r + dr, c + dc
                if not in_bounds(rr, cc):
                    continue
                key = '%d,%d' % (rr, cc)
                if key in know:
                    continue
                val = target['grid'][rr][cc]
                if val in (SHIP, HIT):
                    continue
                know[key] = 'miss'
                if val == WATER:
                    target['grid'][rr][cc] = MISS


def build_state(game, me, idx):
    over = game['phase'] == 'over'
    players = [{
        'index': i, 'name': p['name'], 'ready': p['ready'],
        'alive': p['alive'], 'is_me': i == idx,
        'ships_total': len(p['ships']), 'ships_left': ships_left(p),
        'stats': dict(p['stats']),
    } for i, p in enumerate(game['players'])]

    enemies = []
    for i, p in enumerate(game['players']):
        if i == idx:
            continue
        known = me['knowledge'].get(str(i), {})
        cells = dict(known)
        if over:
            for s in p['ships']:
                for (r, c) in s['cells']:
                    key = '%d,%d' % (r, c)
                    if cells.get(key) != 'hit':
                        cells[key] = 'ship'
        enemies.append({
            'index': i, 'name': p['name'], 'alive': p['alive'],
            'ships_left': ships_left(p), 'ships_total': len(p['ships']),
            'cells': cells,
        })

    my_ships = [{'size': s['size'], 'cells': s['cells'], 'hits': s['hits']} for s in me['ships']]
    return {
        'game_id': game['id'], 'phase': game['phase'], 'turn': game['turn'],
        'you': idx, 'winner': game['winner'],
        'players': players, 'enemies': enemies,
        'my_grid': me['grid'], 'my_ships': my_ships,
        'log': game['log'][-30:],
        'fleet': FLEET, 'board_size': BOARD_SIZE,
        'min_players': MIN_PLAYERS, 'max_players': MAX_PLAYERS,
    }


# ---------- Функции для app.py ----------

def create_game(name):
    with LOCK:
        cleanup_games()
        code = _gen_code()
        while code in GAMES:
            code = _gen_code()
        game = {
            'id': code, 'created': time.time(), 'players': [],
            'phase': 'lobby', 'turn': 0, 'winner': None, 'log': [],
        }
        player = new_player(name)
        game['players'].append(player)
        GAMES[code] = game
        add_log(game, '%s создал игру' % name)
    return code, player['token'], None


def join_game(code, name):
    with LOCK:
        game = GAMES.get(code)
        if not game:
            return None, 'Игра с таким кодом не найдена'
        if game['phase'] != 'lobby':
            return None, 'Игра уже началась'
        if len(game['players']) >= MAX_PLAYERS:
            return None, 'В игре уже максимум игроков'
        names = [p['name'] for p in game['players']]
        base = name
        n = 2
        while name in names:
            name = '%s%d' % (base[:14], n)
            n += 1
        player = new_player(name)
        game['players'].append(player)
        add_log(game, '%s присоединился к игре' % name)
    return player['token'], None


def leave(code, token):
    with LOCK:
        game = GAMES.get(code)
        if not game:
            return
        if game['phase'] == 'lobby':
            game['players'] = [p for p in game['players'] if p['token'] != token]
            if not game['players']:
                GAMES.pop(code, None)


def _current():
    code = session.get('code')
    tok = session.get('player_token')
    if not code or not tok:
        return None, None, None
    game = GAMES.get(code)
    if not game:
        return None, None, None
    idx, player = find_player(game, tok)
    if player is None:
        return None, None, None
    return game, player, idx


# ---------- API ----------

@bp.route('/api/state')
def api_state():
    game, me, idx = _current()
    if not game:
        return jsonify(ok=False, error='no_game')
    with LOCK:
        return jsonify(ok=True, state=build_state(game, me, idx))


@bp.route('/api/start', methods=['POST'])
def api_start():
    game, me, idx = _current()
    if not game:
        return jsonify(ok=False, error='Нет активной игры'), 400
    with LOCK:
        if game['phase'] != 'lobby':
            return jsonify(ok=False, error='Игра уже началась'), 409
        if len(game['players']) < MIN_PLAYERS:
            return jsonify(ok=False, error='Нужно минимум %d игрока' % MIN_PLAYERS), 409
        game['phase'] = 'placement'
        add_log(game, 'Началась расстановка кораблей')
    return jsonify(ok=True)


@bp.route('/api/place', methods=['POST'])
def api_place():
    game, me, idx = _current()
    if not game:
        return jsonify(ok=False, error='Нет активной игры'), 400
    data = request.get_json(silent=True) or {}
    with LOCK:
        if game['phase'] != 'placement':
            return jsonify(ok=False, error='Сейчас не фаза расстановки'), 409
        if data.get('random'):
            ships = random_ships()
            if not ships:
                return jsonify(ok=False, error='Не удалось расставить корабли'), 500
        else:
            ships, err = validate_ships(data.get('ships'))
            if err:
                return jsonify(ok=False, error=err), 400
        grid = empty_grid()
        for s in ships:
            for (r, c) in s['cells']:
                grid[r][c] = SHIP
        me['ships'] = ships
        me['grid'] = grid
        me['ready'] = True
        me['knowledge'] = {}
        add_log(game, '%s расставил корабли' % me['name'])
        if all(p['ready'] for p in game['players']) and len(game['players']) >= MIN_PLAYERS:
            game['phase'] = 'battle'
            first = alive_indices(game)[0]
            game['turn'] = first
            add_log(game, '⚔️ Бой начался! Первым ходит %s' % game['players'][first]['name'])
        state = build_state(game, me, idx)
    return jsonify(ok=True, state=state)


@bp.route('/api/shoot', methods=['POST'])
def api_shoot():
    game, me, idx = _current()
    if not game:
        return jsonify(ok=False, error='Нет активной игры'), 400
    data = request.get_json(silent=True) or {}
    with LOCK:
        if game['phase'] != 'battle':
            return jsonify(ok=False, error='Сейчас не фаза боя'), 409
        if game['turn'] != idx:
            return jsonify(ok=False, error='Сейчас не ваш ход'), 409
        try:
            target = int(data.get('target'))
            r = int(data.get('r'))
            c = int(data.get('c'))
        except (TypeError, ValueError):
            return jsonify(ok=False, error='Некорректные координаты'), 400
        if not in_bounds(r, c):
            return jsonify(ok=False, error='Координаты вне поля'), 400
        if target == idx or target < 0 or target >= len(game['players']):
            return jsonify(ok=False, error='Неверная цель'), 400
        tgt = game['players'][target]
        if not tgt['alive']:
            return jsonify(ok=False, error='Этот игрок уже выбит'), 409
        key = '%d,%d' % (r, c)
        know = me['knowledge'].setdefault(str(target), {})
        if key in know:
            return jsonify(ok=False, error='Вы уже стреляли в эту клетку'), 409
        me['stats']['shots'] += 1
        cell = tgt['grid'][r][c]
        result = 'miss'
        coord = coord_str(r, c)
        if cell == SHIP:
            result = 'hit'
            tgt['grid'][r][c] = HIT
            know[key] = 'hit'
            me['stats']['hits'] += 1
            ship = find_ship(tgt, r, c)
            sunk = False
            if ship is not None:
                ship['hits'] += 1
                if ship['hits'] >= ship['size']:
                    sunk = True
                    me['stats']['sunk'] += 1
                    mark_surroundings(tgt, me, target, ship)
                    add_log(game, '💥 %s потопил %d-палубный (%s) у %s'
                            % (me['name'], ship['size'], coord, tgt['name']))
                    if ships_left(tgt) == 0:
                        tgt['alive'] = False
                        add_log(game, '☠️ %s выбит!' % tgt['name'])
            if not sunk:
                add_log(game, '%s → %s %s: попадание' % (me['name'], tgt['name'], coord))
        else:
            if cell == WATER:
                tgt['grid'][r][c] = MISS
            know[key] = 'miss'
            me['stats']['misses'] += 1
            add_log(game, '%s → %s %s: мимо' % (me['name'], tgt['name'], coord))
        if result == 'miss':
            advance_turn(game)
        check_over(game)
        state = build_state(game, me, idx)
    return jsonify(ok=True, result=result, state=state)

def has_player(code, token):
    with LOCK:
        game = GAMES.get(code)
        if not game:
            return False
        idx, player = find_player(game, token)
        return player is not None
