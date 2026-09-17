# -*- coding: utf-8 -*-
"""Мины (mines_coop) — 1-4 игрока, пошаговое разминирование.

Правила:
  • Поле 10×10, 15 мин.
  • Игроки ходят по очереди: открыть клетку или пометить её.
  • Открыл мину → взрыв, -1 HP, -3 очка. HP=3 у каждого.
  • Пометил мину правильно → +5 очков (снять метку → -5).
  • Пометил пустую → -2 очка (снять → +2).
  • Игра заканчивается когда все безопасные клетки открыты,
    ИЛИ когда в живых остался один игрок, ИЛИ все выбыли.
  • Победитель — наибольшее число очков.
"""
import random
import threading
import time

from flask import Blueprint, jsonify, request, session

bp = Blueprint('mines_coop', __name__, url_prefix='/mn')

GAME_META = {
    'code': 'mn', 'prefix': 'MN', 'name': 'Мины',
    'players': '1-4', 'template': 'mines.html',
    'modes': [1, 2, 3, 4], 'order': 40,
    'realtime': False,
}

BOARD_SIZE = 10
DEFAULT_MINES = 15
MIN_PLAYERS = 1
MAX_PLAYERS = 4
START_HP = 3
GAME_TTL = 6 * 3600

GAMES = {}
LOCK = threading.RLock()
CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'

CELL_HIDDEN = 'hidden'
CELL_OPENED = 'opened'
CELL_FLAGGED = 'flagged'
CELL_EXPLODED = 'exploded'
CELL_REVEALED = 'revealed_mine'   # только для отображения в конце


# --------------------------------------------------------------------------
# Утилиты
# --------------------------------------------------------------------------
def _gen_code():
    return 'MN-' + ''.join(random.choice(CODE_ALPHABET) for _ in range(4))


def _gen_token():
    return '%d-%d' % (random.randint(0, 10 ** 9), id(object()) % 10 ** 9)


def in_bounds(r, c):
    return 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE


def empty_cells():
    return [[{'state': CELL_HIDDEN, 'number': None, 'flagger': None}
             for _ in range(BOARD_SIZE)] for _ in range(BOARD_SIZE)]


def neighbors(r, c):
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == 0 and dc == 0:
                continue
            rr, cc = r + dr, c + dc
            if in_bounds(rr, cc):
                yield rr, cc


def place_mines(num_mines):
    cells = [(r, c) for r in range(BOARD_SIZE) for c in range(BOARD_SIZE)]
    random.shuffle(cells)
    return set(cells[:num_mines])


def compute_numbers(mines):
    nums = [[0] * BOARD_SIZE for _ in range(BOARD_SIZE)]
    for (r, c) in mines:
        for rr, cc in neighbors(r, c):
            nums[rr][cc] += 1
    return nums


def flood_open(g, r, c):
    """Открывает клетку и, если пусто, — всех соседей рекурсивно."""
    stack = [(r, c)]
    opened = []
    while stack:
        rr, cc = stack.pop()
        cell = g['cells'][rr][cc]
        if cell['state'] != CELL_HIDDEN:
            continue
        cell['state'] = CELL_OPENED
        cell['number'] = g['numbers'][rr][cc]
        opened.append((rr, cc))
        if cell['number'] == 0:
            for nr, nc in neighbors(rr, cc):
                if g['cells'][nr][nc]['state'] == CELL_HIDDEN:
                    stack.append((nr, nc))
    return opened


def add_log(g, text):
    g['log'].append(text)
    if len(g['log']) > 80:
        g['log'] = g['log'][-80:]


def record(g):
    g['move_no'] += 1
    g['history'].append({
        'n': g['move_no'],
        'score': list(g['score']),
        'hp': list(g['hp']),
    })
    if len(g['history']) > 300:
        g['history'] = g['history'][-300:]


# --------------------------------------------------------------------------
# Модель игры
# --------------------------------------------------------------------------
def new_game(code, num_players):
    return {
        'id': code,
        'num_players': num_players,
        'names': ['Игрок %d' % (i + 1) for i in range(num_players)],
        'tokens': [None] * num_players,
        'occupied': [False] * num_players,
        'hp': [START_HP] * num_players,
        'score': [0] * num_players,
        'phase': 'lobby',           # lobby | battle | over
        'turn': 0,
        'winner': None,             # None | -1 (ничья) | -2 (solo loss) | idx
        'mines': set(),
        'numbers': [[0] * BOARD_SIZE for _ in range(BOARD_SIZE)],
        'cells': empty_cells(),
        'num_mines': DEFAULT_MINES,
        'log': [],
        'history': [],
        'move_no': 0,
        'created': time.time(),
    }


def start_game(g):
    g['mines'] = place_mines(g['num_mines'])
    g['numbers'] = compute_numbers(g['mines'])
    g['cells'] = empty_cells()
    g['hp'] = [START_HP] * g['num_players']
    g['score'] = [0] * g['num_players']
    g['turn'] = 0
    g['winner'] = None
    g['phase'] = 'battle'
    g['move_no'] = 0
    g['history'] = []
    add_log(g, '🎲 Игра началась. Мин на поле: %d.' % g['num_mines'])
    record(g)


def maybe_start(g):
    if g['phase'] != 'lobby':
        return
    if not all(g['occupied']):
        return
    start_game(g)


def next_turn(g):
    n = g['num_players']
    for step in range(1, n + 1):
        cand = (g['turn'] + step) % n
        if g['hp'][cand] > 0:
            g['turn'] = cand
            return


def _finalize_score(g):
    """Кто победил по очкам (при завершении по «всё открыто» / «все мертвы»)."""
    max_score = max(g['score'])
    winners = [i for i in range(g['num_players']) if g['score'][i] == max_score]
    if len(winners) == 1:
        g['winner'] = winners[0]
        add_log(g, '🏆 %s побеждает с %d очками' % (g['names'][winners[0]], max_score))
    else:
        g['winner'] = -1
        add_log(g, '🤝 Ничья: несколько игроков набрали по %d очков' % max_score)


def check_over(g):
    if g['phase'] != 'battle':
        return

    n = g['num_players']
    alive = [i for i in range(n) if g['hp'][i] > 0]

    # 1. Solo: всё открыто или HP=0
    if n == 1:
        if g['hp'][0] <= 0:
            g['phase'] = 'over'
            g['winner'] = -2
            add_log(g, '💀 Вы проиграли — HP закончилось')
            return
        total_safe = BOARD_SIZE * BOARD_SIZE - len(g['mines'])
        opened_safe = sum(
            1 for r in range(BOARD_SIZE) for c in range(BOARD_SIZE)
            if g['cells'][r][c]['state'] == CELL_OPENED
        )
        if opened_safe >= total_safe:
            g['phase'] = 'over'
            _finalize_score(g)
        return

    # 2. Мульти: все мертвы
    if len(alive) == 0:
        g['phase'] = 'over'
        _finalize_score(g)
        return

    # 3. Мульти: один выживший
    if len(alive) == 1:
        g['phase'] = 'over'
        g['winner'] = alive[0]
        add_log(g, '🏆 Последний выживший: %s' % g['names'][alive[0]])
        return

    # 4. Мульти: все безопасные клетки открыты
    total_safe = BOARD_SIZE * BOARD_SIZE - len(g['mines'])
    opened_safe = sum(
        1 for r in range(BOARD_SIZE) for c in range(BOARD_SIZE)
        if g['cells'][r][c]['state'] == CELL_OPENED
    )
    if opened_safe >= total_safe:
        g['phase'] = 'over'
        _finalize_score(g)


# --------------------------------------------------------------------------
# Действия игрока
# --------------------------------------------------------------------------
def do_open(g, pid, r, c):
    if g['phase'] != 'battle':
        return 'Игра не активна'
    if g['hp'][pid] <= 0:
        return 'Вы выбыли'
    if g['turn'] != pid:
        return 'Сейчас не ваш ход'
    if not in_bounds(r, c):
        return 'Вне поля'
    cell = g['cells'][r][c]
    if cell['state'] != CELL_HIDDEN:
        return 'Эту клетку уже нельзя открыть'

    if (r, c) in g['mines']:
        cell['state'] = CELL_EXPLODED
        g['hp'][pid] -= 1
        g['score'][pid] -= 3
        add_log(g, '💥 %s подорвался на (%d,%d). HP: %d, очки: %d'
                % (g['names'][pid], r + 1, c + 1,
                   max(0, g['hp'][pid]), g['score'][pid]))
        if g['hp'][pid] <= 0:
            add_log(g, '☠ %s выбывает!' % g['names'][pid])
    else:
        opened = flood_open(g, r, c)
        add_log(g, '🔎 %s открывает (%d,%d) — %d клеток'
                % (g['names'][pid], r + 1, c + 1, len(opened)))

    record(g)
    check_over(g)
    if g['phase'] == 'battle':
        next_turn(g)
    return None


def do_flag(g, pid, r, c):
    if g['phase'] != 'battle':
        return 'Игра не активна'
    if g['hp'][pid] <= 0:
        return 'Вы выбыли'
    if g['turn'] != pid:
        return 'Сейчас не ваш ход'
    if not in_bounds(r, c):
        return 'Вне поля'
    cell = g['cells'][r][c]

    if cell['state'] == CELL_FLAGGED:
        if cell['flagger'] != pid:
            return 'Эту метку поставил другой игрок'
        # Снимаем метку — откатываем очки
        if (r, c) in g['mines']:
            g['score'][pid] -= 5
        else:
            g['score'][pid] += 2
        cell['state'] = CELL_HIDDEN
        cell['flagger'] = None
        add_log(g, '🚫 %s снимает метку с (%d,%d)'
                % (g['names'][pid], r + 1, c + 1))

    elif cell['state'] == CELL_HIDDEN:
        cell['state'] = CELL_FLAGGED
        cell['flagger'] = pid
        if (r, c) in g['mines']:
            g['score'][pid] += 5
            add_log(g, '🚩 %s помечает МИНУ (%d,%d)! +5'
                    % (g['names'][pid], r + 1, c + 1))
        else:
            g['score'][pid] -= 2
            add_log(g, '❌ %s ошибочно помечает (%d,%d). -2'
                    % (g['names'][pid], r + 1, c + 1))
    else:
        return 'Эту клетку нельзя пометить'

    record(g)
    check_over(g)
    if g['phase'] == 'battle':
        next_turn(g)
    return None


# --------------------------------------------------------------------------
# Реестр игр и хуки для портала
# --------------------------------------------------------------------------
def _find_player(g, token):
    for i, t in enumerate(g['tokens']):
        if t == token:
            return i
    return None


def create_game(name, mode=2):
    mode = max(MIN_PLAYERS, min(MAX_PLAYERS, mode))
    with LOCK:
        code = _gen_code()
        while code in GAMES:
            code = _gen_code()
        g = new_game(code, mode)
        GAMES[code] = g
        tok = _gen_token()
        g['occupied'][0] = True
        g['tokens'][0] = tok
        g['names'][0] = name
        add_log(g, '%s садится за стол' % name)
        if mode == 1:               # соло стартует сразу
            maybe_start(g)
    return code, tok, None


def list_active():
    with LOCK:
        out = []
        for code, g in GAMES.items():
            players = [g['names'][i] for i, occ in enumerate(g['occupied']) if occ]
            out.append({
                'id': code,
                'phase': g['phase'],
                'players': players,
                'occupied': len(players),
                'max_players': g['num_players'],
                'started': g['phase'] != 'lobby',
                'created': g.get('created'),
            })
        return out


def join_game(code, name):
    with LOCK:
        g = GAMES.get(code)
        if g is None:
            return None, 'Игра не найдена'
        if g['phase'] != 'lobby':
            return None, 'Игра уже началась'
        for slot in range(g['num_players']):
            if not g['occupied'][slot]:
                tok = _gen_token()
                g['occupied'][slot] = True
                g['tokens'][slot] = tok
                g['names'][slot] = name
                add_log(g, '%s садится за стол' % name)
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


def has_player(code, token):
    with LOCK:
        g = GAMES.get(code)
        if not g:
            return False
        return _find_player(g, token) is not None


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


# --------------------------------------------------------------------------
# Сериализация состояния
# --------------------------------------------------------------------------
def build_state(g, pid):
    if g is None:
        return {'joined': False, 'phase': 'no_game'}
    if pid is None:
        return {
            'joined': False,
            'phase': g['phase'],
            'num_players': g['num_players'],
            'names': g['names'],
            'occupied': g['occupied'],
        }

    view_cells = []
    for r in range(BOARD_SIZE):
        row = []
        for c in range(BOARD_SIZE):
            cell = g['cells'][r][c]
            view = {
                'state': cell['state'],
                'number': cell['number'],
                'flagger': cell['flagger'],
            }
            if (g['phase'] == 'over'
                    and cell['state'] == CELL_HIDDEN
                    and (r, c) in g['mines']):
                view['state'] = CELL_REVEALED
            row.append(view)
        view_cells.append(row)

    mines_left = sum(
        1 for (r, c) in g['mines']
        if g['cells'][r][c]['state'] not in (CELL_FLAGGED, CELL_EXPLODED)
    )

    return {
        'joined': True,
        'slot': pid,
        'game_id': g['id'],
        'num_players': g['num_players'],
        'names': g['names'],
        'occupied': g['occupied'],
        'phase': g['phase'],
        'turn': g['turn'],
        'my_turn': g['phase'] == 'battle' and g['turn'] == pid and g['hp'][pid] > 0,
        'my_name': g['names'][pid],
        'hp': list(g['hp']),
        'score': list(g['score']),
        'my_hp': g['hp'][pid],
        'my_score': g['score'][pid],
        'cells': view_cells,
        'board_size': BOARD_SIZE,
        'num_mines': g['num_mines'],
        'mines_left': mines_left,
        'winner': g['winner'],
        'i_won': g['winner'] == pid,
        'draw': g['winner'] == -1,
        'i_lost': (g['phase'] == 'over'
                   and g['winner'] != pid
                   and g['winner'] != -1),
        'log': g['log'][-30:],
        'history': g['history'],
        'others': [{
            'idx': i,
            'name': g['names'][i],
            'hp': g['hp'][i],
            'score': g['score'][i],
            'alive': g['hp'][i] > 0,
        } for i in range(g['num_players']) if i != pid],
        'waiting_opponents': not all(g['occupied']),
    }


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
@bp.route('/api/state')
def api_state():
    with LOCK:
        g, pid = _current()
        return jsonify({'ok': True, 'state': build_state(g, pid)})


@bp.route('/api/open', methods=['POST'])
def api_open():
    with LOCK:
        g, pid = _current()
        if g is None:
            return jsonify({'ok': False, 'error': 'Нет активной игры'}), 404
        data = request.get_json(silent=True) or {}
        try:
            r = int(data.get('r'))
            c = int(data.get('c'))
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'error': 'Некорректные координаты'}), 400
        err = do_open(g, pid, r, c)
        if err:
            return jsonify({'ok': False, 'error': err}), 400
        return jsonify({'ok': True, 'state': build_state(g, pid)})


@bp.route('/api/flag', methods=['POST'])
def api_flag():
    with LOCK:
        g, pid = _current()
        if g is None:
            return jsonify({'ok': False, 'error': 'Нет активной игры'}), 404
        data = request.get_json(silent=True) or {}
        try:
            r = int(data.get('r'))
            c = int(data.get('c'))
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'error': 'Некорректные координаты'}), 400
        err = do_flag(g, pid, r, c)
        if err:
            return jsonify({'ok': False, 'error': err}), 400
        return jsonify({'ok': True, 'state': build_state(g, pid)})


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
        newg = new_game(code, num)
        newg['tokens'] = old_tokens
        newg['names'] = old_names
        newg['occupied'] = [t is not None for t in old_tokens]
        add_log(newg, 'Новая партия')
        GAMES[code] = newg
        maybe_start(newg)
        return jsonify({'ok': True, 'state': build_state(newg, pid)})
