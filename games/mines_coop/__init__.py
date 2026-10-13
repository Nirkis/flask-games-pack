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
import time

from flask import Blueprint, jsonify, request

from games import game_base
from games.data_loader import load

bp = Blueprint('mines_coop', __name__, url_prefix='/mn',
               template_folder='templates')

GAME_META = {
    'code': 'mn',
    'prefix': 'MN',
    'name': 'Мины',
    'players': '1-4',
    'template': 'mines_coop/index.html',
    'modes': [1, 2, 3, 4],
    'order': 40,
    'realtime': False,
}

_cfg = load('rules', schema={
    'board_size': int,
    'default_mines': int,
    'start_hp': int,
    'max_players': int,
    'min_players': int,
})

BOARD_SIZE = _cfg['board_size']
DEFAULT_MINES = _cfg['default_mines']
START_HP = _cfg['start_hp']
MAX_PLAYERS = _cfg['max_players']
MIN_PLAYERS = _cfg['min_players']

CELL_HIDDEN = 'hidden'
CELL_OPENED = 'opened'
CELL_FLAGGED = 'flagged'
CELL_EXPLODED = 'exploded'
CELL_REVEALED = 'revealed_mine'


# --------------------------------------------------------------------------
# Утилиты поля
# --------------------------------------------------------------------------
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
    """Открывает клетку и, если пусто, — соседей рекурсивно."""
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
# GameServer hooks
# --------------------------------------------------------------------------
def _new_game(code, mode=2):
    mode = max(MIN_PLAYERS, min(MAX_PLAYERS, int(mode)))
    return {
        'id': code,
        'num_players': mode,
        'names': ['Игрок %d' % (i + 1) for i in range(mode)],
        'tokens': [None] * mode,
        'occupied': [False] * mode,
        'hp': [START_HP] * mode,
        'score': [0] * mode,
        'phase': 'lobby',
        'turn': 0,
        'winner': None,
        'mines': set(),
        'numbers': [[0] * BOARD_SIZE for _ in range(BOARD_SIZE)],
        'cells': empty_cells(),
        'num_mines': DEFAULT_MINES,
        'log': [],
        'history': [],
        'move_no': 0,
        'created': time.time(),
    }


def _attach_player(g, slot, name, tok):
    g['tokens'][slot] = tok
    g['occupied'][slot] = True
    g['names'][slot] = name
    add_log(g, '%s садится за стол' % name)


def _get_tokens(g):
    return list(g['tokens'])


def _drop_player(g, tok):
    if g['phase'] != 'lobby':
        return
    for i, t in enumerate(g['tokens']):
        if t == tok:
            g['tokens'][i] = None
            g['occupied'][i] = False
            g['names'][i] = 'Игрок %d' % (i + 1)
            return


def _player_names(g):
    return [g['names'][i] for i, occ in enumerate(g['occupied']) if occ]


def _phase_of(g):
    return g['phase']


def _maybe_start(g):
    if g['phase'] != 'lobby':
        return
    if not all(g['occupied']):
        return
    _start_game(g)


# --------------------------------------------------------------------------
# Игровой цикл
# --------------------------------------------------------------------------
def _start_game(g):
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


def _next_turn(g):
    n = g['num_players']
    for step in range(1, n + 1):
        cand = (g['turn'] + step) % n
        if g['hp'][cand] > 0:
            g['turn'] = cand
            return


def _finalize_score(g):
    max_score = max(g['score'])
    winners = [i for i in range(g['num_players']) if g['score'][i] == max_score]
    if len(winners) == 1:
        g['winner'] = winners[0]
        add_log(g, '🏆 %s побеждает с %d очками'
                % (g['names'][winners[0]], max_score))
    else:
        g['winner'] = -1
        add_log(g, '🤝 Ничья: несколько игроков набрали по %d очков' % max_score)


def _count_opened_safe(g):
    return sum(
        1 for r in range(BOARD_SIZE) for c in range(BOARD_SIZE)
        if g['cells'][r][c]['state'] == CELL_OPENED
    )


def _check_over(g):
    if g['phase'] != 'battle':
        return

    n = g['num_players']
    alive = [i for i in range(n) if g['hp'][i] > 0]

    if n == 1:
        if g['hp'][0] <= 0:
            g['phase'] = 'over'
            g['winner'] = -2
            add_log(g, '💀 Вы проиграли — HP закончилось')
            return
        total_safe = BOARD_SIZE * BOARD_SIZE - len(g['mines'])
        if _count_opened_safe(g) >= total_safe:
            g['phase'] = 'over'
            _finalize_score(g)
        return

    if len(alive) == 0:
        g['phase'] = 'over'
        _finalize_score(g)
        return

    if len(alive) == 1:
        g['phase'] = 'over'
        g['winner'] = alive[0]
        add_log(g, '🏆 Последний выживший: %s' % g['names'][alive[0]])
        return

    total_safe = BOARD_SIZE * BOARD_SIZE - len(g['mines'])
    if _count_opened_safe(g) >= total_safe:
        g['phase'] = 'over'
        _finalize_score(g)


def _do_open(g, pid, r, c):
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
    _check_over(g)
    if g['phase'] == 'battle':
        _next_turn(g)
    return None


def _do_flag(g, pid, r, c):
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
    _check_over(g)
    if g['phase'] == 'battle':
        _next_turn(g)
    return None


def _do_reset(g, pid):
    old_tokens = list(g['tokens'])
    old_names = list(g['names'])
    old_occupied = list(g['occupied'])
    num = g['num_players']
    code = g['id']

    newg = _new_game(code, num)
    newg['tokens'] = old_tokens
    newg['names'] = old_names
    newg['occupied'] = old_occupied
    add_log(newg, '🔄 Новая партия')

    g.clear()
    g.update(newg)
    _maybe_start(g)
    return None


def _do_action(g, pid, data):
    name = data.get('action') or data.get('name') or ''
    if name == 'reset':
        return _do_reset(g, pid)
    if name in ('open', 'flag'):
        try:
            r = int(data.get('r'))
            c = int(data.get('c'))
        except (TypeError, ValueError):
            return 'Некорректные координаты'
        if name == 'open':
            return _do_open(g, pid, r, c)
        return _do_flag(g, pid, r, c)
    return 'Неизвестное действие'


# --------------------------------------------------------------------------
# build_state
# --------------------------------------------------------------------------
def _build_state(g, pid):
    state = {
        'game_id': g['id'],
        'num_players': g['num_players'],
        'names': list(g['names']),
        'occupied': list(g['occupied']),
        'phase': g['phase'],
        'log': g['log'][-30:],
    }

    if g['phase'] == 'lobby':
        state['slot'] = pid if pid is not None else -1
        state['my_name'] = g['names'][pid] if pid is not None else ''
        state['waiting_opponents'] = not all(g['occupied'])
        return state

    if pid is None:
        state['slot'] = -1
        state['my_name'] = ''
        return state

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

    state.update({
        'slot': pid,
        'my_name': g['names'][pid],
        'turn': g['turn'],
        'my_turn': (g['phase'] == 'battle'
                    and g['turn'] == pid
                    and g['hp'][pid] > 0),
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
        'history': g['history'],
        'others': [{
            'idx': i,
            'name': g['names'][i],
            'hp': g['hp'][i],
            'score': g['score'][i],
            'alive': g['hp'][i] > 0,
        } for i in range(g['num_players']) if i != pid],
        'waiting_opponents': not all(g['occupied']),
    })
    return state


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
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
        return jsonify({'ok': True, 'state': _build_state(g, pid)})


# --------------------------------------------------------------------------
# GameServer — сборка хуков
# --------------------------------------------------------------------------
server = game_base.GameServer(
    prefix=GAME_META['prefix'],
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
