# -*- coding: utf-8 -*-
"""
Дурак подкидной, переводной. Поддерживает 2 и 3 игроков.

Backend  : Python 3.6.8 + Flask 2.0.3
Frontend : Chart.js 4.5.1 (локально: static/chart.umd.min.js)

Запуск:
    pip install flask==2.0.3
    python main.py
"""
import random
import threading
import time

from flask import Flask, jsonify, render_template, request, session

# =====================================================================
# Конфигурация Flask
# =====================================================================
app = Flask(__name__)
app.secret_key = 'durak-final-secret-change-me'
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# =====================================================================
# Игровые константы
# =====================================================================
SUITS = ['\u2660', '\u2665', '\u2666', '\u2663']
SUIT_NAMES = {
    '\u2660': 'пики',
    '\u2665': 'черви',
    '\u2666': 'бубны',
    '\u2663': 'трефы',
}
RANKS = ['6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
RANK_VALUE = {r: i for i, r in enumerate(RANKS)}

HAND_SIZE = 6       # карт в руке
MAX_TABLE = 12       # максимум атакующих карт за бой
MIN_PLAYERS = 2
MAX_PLAYERS = 3

BITE_GRACE = 10.0    # пауза перед активацией «Бито», сек
BITE_TIMEOUT = 3.0  # авто-«Бито», сек

# =====================================================================
# Глобальное состояние (одна партия на сервер)
# =====================================================================
LOCK = threading.RLock()
GAME = None


# =====================================================================
# Утилиты для карт
# =====================================================================
def make_deck():
    deck = [{'r': r, 's': s} for s in SUITS for r in RANKS]
    random.shuffle(deck)
    return deck


def card_text(card):
    return card['r'] + card['s']


def find_card(hand, card):
    if not isinstance(card, dict):
        return -1
    for i, c in enumerate(hand):
        if c['r'] == card.get('r') and c['s'] == card.get('s'):
            return i
    return -1


def beats(attack, defend, trump_suit):
    """Бьёт ли карта defend карту attack при данном козыре."""
    if defend['s'] == attack['s']:
        return RANK_VALUE[defend['r']] > RANK_VALUE[attack['r']]
    if defend['s'] == trump_suit and attack['s'] != trump_suit:
        return True
    return False


# =====================================================================
# Создание и старт партии
# =====================================================================
def new_game(num_players):
    deck = make_deck()
    trump_card = deck[-1]   # нижняя карта колоды — козырь
    return {
        'num_players': num_players,
        'names': ['Игрок %d' % (i + 1) for i in range(num_players)],
        'tokens': [None] * num_players,
        'occupied': [False] * num_players,
        'hands': [[] for _ in range(num_players)],
        'deck': deck,
        'trump_card': trump_card,
        'trump_suit': trump_card['s'],
        'table': [],             # [{'attack': card, 'defend': card|None}]
        'attacker_idx': 0,
        'defender_idx': 1 % num_players,
        'phase': 'lobby',        # lobby | battle | over
        'loser': None,           # None | -1 (ничья) | индекс дурака
        'discard_count': 0,
        'bite_start': None,      # метка времени начала окна «Бито»
        'log': [],
        'history': [],           # для графика Chart.js
        'move_no': 0,
    }


def deal_initial():
    for _ in range(HAND_SIZE):
        for p in range(GAME['num_players']):
            if GAME['deck']:
                GAME['hands'][p].append(GAME['deck'].pop(0))


def choose_first_attacker():
    """Первым ходит игрок с младшим козырем; если козырей нет — случайный."""
    g = GAME
    best_idx, best_val = None, None
    for p in range(g['num_players']):
        for c in g['hands'][p]:
            if c['s'] == g['trump_suit']:
                v = RANK_VALUE[c['r']]
                if best_val is None or v < best_val:
                    best_val = v
                    best_idx = p
    if best_idx is None:
        best_idx = random.randint(0, g['num_players'] - 1)
    return best_idx


def maybe_start():
    """Старт партии, если все места за столом заняты."""
    g = GAME
    if g['phase'] != 'lobby':
        return
    if not all(g['occupied']):
        return
    deal_initial()
    g['attacker_idx'] = choose_first_attacker()
    g['defender_idx'] = (g['attacker_idx'] + 1) % g['num_players']
    g['phase'] = 'battle'
    g['log'].append('Игра началась. Козырь — %s (%s). Первым ходит %s.' % (
        g['trump_suit'], SUIT_NAMES.get(g['trump_suit'], ''),
        g['names'][g['attacker_idx']],
    ))
    record()


# =====================================================================
# Снимки состояния стола
# =====================================================================
def undefended_count():
    return sum(1 for t in GAME['table'] if t['defend'] is None)


def table_ranks():
    """Достоинства всех атакующих карт на столе (и битых, и неотбитых)."""
    ranks = set()
    for t in GAME['table']:
        ranks.add(t['attack']['r'])
        if t['defend']:
            ranks.add(t['defend']['r'])
    return ranks


def is_out(idx):
    return not GAME['hands'][idx]


def next_active_after(idx):
    """Следующий игрок по кругу с непустой рукой."""
    n = GAME['num_players']
    for k in range(1, n + 1):
        cand = (idx + k) % n
        if not is_out(cand):
            return cand
    return None


# =====================================================================
# Добор карт и завершение боя
# =====================================================================
def refill():
    """Добор: сначала атакующий, затем защищающийся, затем остальные."""
    g = GAME
    order = [g['attacker_idx'], g['defender_idx']]
    for i in range(g['num_players']):
        if i not in order:
            order.append(i)
    for p in order:
        while len(g['hands'][p]) < HAND_SIZE and g['deck']:
            g['hands'][p].append(g['deck'].pop(0))


def check_winner():
    """Если колода пуста — фиксируем результат партии."""
    g = GAME
    if g['deck']:
        return False

    active = [i for i in range(g['num_players']) if g['hands'][i]]
    if len(active) == 0:
        g['phase'] = 'over'
        g['loser'] = -1
        g['log'].append('Ничья — все вышли.')
        return True
    if len(active) == 1:
        loser = active[0]
        g['phase'] = 'over'
        g['loser'] = loser
        g['log'].append('%s — дурак!' % g['names'][loser])
        return True
    return False


def end_battle(success):
    """success=True — защитился (бито), False — взял."""
    g = GAME
    g['bite_start'] = None
    refill()
    if check_winner():
        return

    if success:
        # Успешный отбой: защищающийся становится атакующим.
        next_att = g['defender_idx']
        if is_out(next_att):
            next_att = next_active_after(next_att)
    else:
        # Взял карты — ход переходит к следующему после защищающегося.
        next_att = next_active_after(g['defender_idx'])

    if next_att is None:
        check_winner()
        return

    g['attacker_idx'] = next_att
    g['defender_idx'] = next_active_after(next_att)
    if g['defender_idx'] is None:
        check_winner()


# =====================================================================
# Лог и статистика
# =====================================================================
def record():
    g = GAME
    g['move_no'] += 1
    g['history'].append({
        'n': g['move_no'],
        'hands': [len(h) for h in g['hands']],
        'deck': len(g['deck']),
    })
    if len(g['history']) > 400:
        g['history'] = g['history'][-400:]


# =====================================================================
# Действия игроков
# =====================================================================
def do_attack(pid, card):
    g = GAME
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
        return 'На столе уже максимум карт (%d)' % MAX_TABLE

    # У защищающегося должно хватать карт, чтобы теоретически отбиться.
    undef = undefended_count()
    def_hand = len(g['hands'][g['defender_idx']])
    if def_hand <= undef:
        return ('У защищающегося %d карт(а), неотбитых уже %d. '
                'Подкидывать нельзя.' % (def_hand, undef))

    # Разрешены карты того же достоинства, что любая карта атаки на столе.
    if g['table']:
        ranks = table_ranks()
        if card['r'] not in ranks:
            return ('Подкидывать можно только карты того же достоинства, '
                    'что уже есть на столе')

    c = g['hands'][pid].pop(idx)
    g['table'].append({'attack': c, 'defend': None})
    g['bite_start'] = None
    g['log'].append('%s подкидывает: %s' % (g['names'][pid], card_text(c)))
    return None


def do_defend(pid, card):
    g = GAME
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
        return 'Карта %s не бьёт %s' % (
            card_text(card), card_text(target['attack']))

    c = g['hands'][pid].pop(idx)
    target['defend'] = c
    g['log'].append('%s отбивается: %s' % (g['names'][pid], card_text(c)))

    # Все карты отбиты — открываем окно «Бито».
    if undefended_count() == 0 and g['table']:
        g['bite_start'] = time.time()
    return None


def do_transfer(pid, card):
    """Переводной дурак: защищающийся переводит картой того же ранга."""
    g = GAME
    if g['phase'] != 'battle':
        return 'Сейчас нельзя переводить'
    if pid != g['defender_idx']:
        return 'Переводить может только защищающийся'
    if any(t['defend'] is not None for t in g['table']):
        return 'Нельзя переводить, когда уже есть отбитые карты'

    idx = find_card(g['hands'][pid], card)
    if idx < 0:
        return 'У вас нет такой карты'
    if len(g['table']) >= MAX_TABLE:
        return 'На столе уже максимум карт (%d)' % MAX_TABLE

    # Новый защищающийся — это прежний атакующий: должен смочь отбиться.
    new_def_hand = len(g['hands'][g['attacker_idx']])
    undef = undefended_count()
    if new_def_hand <= undef:
        return ('У соперника %d карт(а), неотбитых уже %d. '
                'Переводить нельзя.' % (new_def_hand, undef))

    ranks = table_ranks()
    if card['r'] not in ranks:
        return 'Переводить можно только картой того же достоинства'

    c = g['hands'][pid].pop(idx)
    g['table'].append({'attack': c, 'defend': None})
    g['bite_start'] = None

    # Роли меняются местами.
    g['attacker_idx'], g['defender_idx'] = g['defender_idx'], g['attacker_idx']
    g['log'].append('%s переводит: %s. Теперь атакует %s.' % (
        g['names'][pid], card_text(c), g['names'][pid],
    ))
    return None


def do_take(pid):
    g = GAME
    if g['phase'] != 'battle':
        return 'Сейчас нечего брать'
    if pid != g['defender_idx']:
        return 'Вы не защищаетесь'
    if not g['table']:
        return 'Стол пуст'
    if undefended_count() == 0:
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
    g['log'].append('%s берёт %d карт(ы)' % (g['names'][pid], taken))
    end_battle(success=False)
    return None


def do_pass(pid):
    g = GAME
    if g['phase'] != 'battle':
        return 'Сейчас нельзя сказать «Бито»'
    if pid != g['attacker_idx']:
        return 'Только атакующий может сказать «Бито»'
    if not g['table']:
        return 'Стол пуст'
    if undefended_count() > 0:
        return 'Есть неотбитые карты'

    # Выдержка перед «Бито»: даём соперникам время подкинуть.
    if g['bite_start'] is not None:
        elapsed = time.time() - g['bite_start']
        if elapsed < BITE_GRACE:
            return 'Подождите ещё %.1f сек — соперники могут подкинуть карты' % (
                BITE_GRACE - elapsed)

    g['discard_count'] += len(g['table'])
    g['table'] = []
    g['bite_start'] = None
    g['log'].append('%s: бито' % g['names'][pid])
    end_battle(success=True)
    return None


# =====================================================================
# Подсказки допустимых ходов
# =====================================================================
def legal_attacks(pid):
    g = GAME
    if g['phase'] != 'battle' or pid == g['defender_idx']:
        return []
    if len(g['table']) >= MAX_TABLE:
        return []

    undef = undefended_count()
    if len(g['hands'][g['defender_idx']]) <= undef:
        return []

    if not g['table']:
        return list(g['hands'][pid]) if pid == g['attacker_idx'] else []

    ranks = table_ranks()
    return [c for c in g['hands'][pid] if c['r'] in ranks]


def legal_defends(pid):
    g = GAME
    if g['phase'] != 'battle' or pid != g['defender_idx']:
        return []
    target = next((t for t in g['table'] if t['defend'] is None), None)
    if target is None:
        return []
    return [c for c in g['hands'][pid]
            if beats(target['attack'], c, g['trump_suit'])]


def legal_transfers(pid):
    g = GAME
    if g['phase'] != 'battle' or pid != g['defender_idx']:
        return []
    if any(t['defend'] is not None for t in g['table']):
        return []
    if len(g['table']) >= MAX_TABLE:
        return []
    if len(g['hands'][g['attacker_idx']]) <= undefended_count():
        return []

    ranks = table_ranks()
    if not ranks:
        return []
    return [c for c in g['hands'][pid] if c['r'] in ranks]


# =====================================================================
# Авто-«Бито» по таймауту
# =====================================================================
def force_pass_if_timeout():
    g = GAME
    if g is None or g['phase'] != 'battle':
        return
    if g['bite_start'] is None:
        return
    if undefended_count() > 0 or not g['table']:
        g['bite_start'] = None
        return
    if time.time() - g['bite_start'] < BITE_TIMEOUT:
        return

    att = g['attacker_idx']
    g['discard_count'] += len(g['table'])
    g['table'] = []
    g['bite_start'] = None
    g['log'].append('%s: бито (авто)' % g['names'][att])
    end_battle(success=True)


# =====================================================================
# Состояние для клиента
# =====================================================================
def current_pid():
    """Индекс игрока по токену сессии, либо None."""
    token = session.get('token')
    if not token or GAME is None:
        return None
    for i, t in enumerate(GAME['tokens']):
        if t == token:
            return i
    return None


def build_state(pid):
    g = GAME
    if g is None:
        return {'joined': False, 'phase': 'no_game',
                'num_players': 0, 'occupied': [], 'names': []}

    if pid is None:
        return {
            'joined': False,
            'phase': 'lobby',
            'num_players': g['num_players'],
            'names': g['names'],
            'occupied': g['occupied'],
        }

    others = [i for i in range(g['num_players']) if i != pid]

    # Окно «Бито»: сколько осталось ждать и когда сработает авто-«Бито».
    bite_grace_left = None
    bite_seconds_left = None
    if (g['bite_start'] is not None
            and undefended_count() == 0
            and g['table']):
        elapsed = time.time() - g['bite_start']
        bite_grace_left = max(0.0, BITE_GRACE - elapsed)
        bite_seconds_left = max(0.0, BITE_TIMEOUT - elapsed)

    return {
        'joined': True,
        'slot': pid,
        'num_players': g['num_players'],
        'names': g['names'],
        'occupied': g['occupied'],
        'phase': g['phase'],
        'my_name': g['names'][pid],
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
        'table': [{'attack': t['attack'], 'defend': t['defend']}
                  for t in g['table']],
        'my_hand': g['hands'][pid],
        'attacker_idx': g['attacker_idx'],
        'defender_idx': g['defender_idx'],
        'i_am_attacker': pid == g['attacker_idx'],
        'i_am_defender': pid == g['defender_idx'],
        'loser': g['loser'],
        'i_lost': (g['loser'] == pid) if g['loser'] is not None else False,
        'draw': g['loser'] == -1,
        'legal_attacks': legal_attacks(pid),
        'legal_defends': legal_defends(pid),
        'legal_transfers': legal_transfers(pid),
        'can_take': (g['phase'] == 'battle' and pid == g['defender_idx']
                     and undefended_count() > 0),
        'can_pass': (
            g['phase'] == 'battle' and pid == g['attacker_idx']
            and bool(g['table']) and undefended_count() == 0
            and (g['bite_start'] is None
                 or time.time() - g['bite_start'] >= BITE_GRACE)
        ),
        'can_transfer': bool(legal_transfers(pid)),
        'bite_grace_left': bite_grace_left,
        'bite_seconds_left': bite_seconds_left,
        'waiting_opponents': not all(g['occupied']),
        'log': g['log'][-20:],
        'history': g['history'],
    }


# =====================================================================
# Flask-хуки
# =====================================================================
@app.before_request
def _tick():
    if GAME is None or not request.path.startswith('/api/'):
        return
    with LOCK:
        force_pass_if_timeout()


@app.after_request
def _no_cache(response):
    if request.path.startswith('/api/'):
        response.headers['Cache-Control'] = \
            'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response


# =====================================================================
# Маршруты
# =====================================================================
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/state')
def api_state():
    with LOCK:
        return jsonify(build_state(current_pid()))


@app.route('/api/join', methods=['POST'])
def api_join():
    global GAME
    with LOCK:
        data = request.get_json(silent=True) or {}
        name = (data.get('name') or '').strip()[:16]
        try:
            mode = int(data.get('mode') or 2)
        except (TypeError, ValueError):
            mode = 2
        mode = max(MIN_PLAYERS, min(MAX_PLAYERS, mode))

        existing = current_pid()
        if existing is not None:
            if name:
                GAME['names'][existing] = name
            return jsonify({'ok': True, 'slot': existing})

        if GAME is None or GAME['phase'] == 'over':
            GAME = new_game(mode)

        for slot in range(GAME['num_players']):
            if not GAME['occupied'][slot]:
                token = '%d-%d-%d' % (
                    random.randint(0, 10 ** 9), slot,
                    id(object()) % 10 ** 9,
                )
                GAME['occupied'][slot] = True
                GAME['tokens'][slot] = token
                session['token'] = token
                if name:
                    GAME['names'][slot] = name
                GAME['log'].append('%s садится за стол' % GAME['names'][slot])
                maybe_start()
                return jsonify({'ok': True, 'slot': slot})

        return jsonify({
            'ok': False,
            'error': 'Мест нет. Дождитесь окончания партии.',
        }), 409


@app.route('/api/action', methods=['POST'])
def api_action():
    with LOCK:
        pid = current_pid()
        if pid is None:
            return jsonify({'ok': False, 'error': 'Вы не за столом'}), 403

        g = GAME
        if g['phase'] == 'lobby':
            return jsonify({'ok': False,
                            'error': 'Ждём остальных игроков'}), 400
        if g['phase'] == 'over':
            return jsonify({'ok': False,
                            'error': 'Партия окончена'}), 400

        data = request.get_json(silent=True) or {}
        action = data.get('action')
        card = data.get('card')

        card_actions = {
            'attack':   do_attack,
            'defend':   do_defend,
            'transfer': do_transfer,
        }
        if action in card_actions:
            err = card_actions[action](pid, card)
        elif action == 'take':
            err = do_take(pid)
        elif action == 'pass':
            err = do_pass(pid)
        else:
            err = 'Неизвестное действие'

        if err:
            return jsonify({'ok': False, 'error': err}), 400

        record()
        resp = build_state(pid)
        resp['ok'] = True
        return jsonify(resp)


@app.route('/api/reset', methods=['POST'])
def api_reset():
    global GAME
    with LOCK:
        pid = current_pid()
        if pid is None:
            return jsonify({'ok': False, 'error': 'Вы не за столом'}), 403

        old_tokens = GAME['tokens']
        old_names = GAME['names']
        num_players = GAME['num_players']

        GAME = new_game(num_players)
        GAME['tokens'] = old_tokens
        GAME['names'] = old_names
        GAME['occupied'] = [t is not None for t in old_tokens]
        GAME['log'].append('Новая партия')
        maybe_start()
        return jsonify({'ok': True})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
