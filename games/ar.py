# games/ar.py
import random
import string
import threading
import time
from flask import Blueprint, jsonify, request, session

bp = Blueprint('ar', __name__, url_prefix='/ar')

GAMES = {}
LOCK = threading.RLock()

GAME_META = {
    'code': 'ar',
    'prefix': 'AR',
    'name': 'Arena Royale',
    'players': '2-8',
    'template': 'ar.html',
    'order': 60,
    'modes': [2, 3, 4, 5, 6, 7, 8],
    'realtime': False,
}

HAND_MAX = 8
REFRESH_COST = 1

UNITS = {
    'rat':    {'name': 'Крыса',   'tier': 1, 'atk': 1, 'hp': 2, 'cost': 3, 'ability': 'poison'},
    'guard':  {'name': 'Страж',   'tier': 1, 'atk': 1, 'hp': 4, 'cost': 3, 'ability': 'shield'},
    'archer': {'name': 'Лучник',  'tier': 1, 'atk': 2, 'hp': 1, 'cost': 3, 'ability': 'cleave'},
    'wolf':   {'name': 'Волк',    'tier': 2, 'atk': 3, 'hp': 2, 'cost': 3, 'ability': 'none'},
    'knight': {'name': 'Рыцарь',  'tier': 2, 'atk': 2, 'hp': 4, 'cost': 3, 'ability': 'shield'},
    'mage':   {'name': 'Маг',     'tier': 2, 'atk': 3, 'hp': 3, 'cost': 3, 'ability': 'regen'},
    'dragon': {'name': 'Дракон',  'tier': 3, 'atk': 5, 'hp': 5, 'cost': 3, 'ability': 'cleave'},
    'giant':  {'name': 'Гигант',  'tier': 3, 'atk': 4, 'hp': 7, 'cost': 3, 'ability': 'shield'},
    'demon':  {'name': 'Демон',   'tier': 3, 'atk': 6, 'hp': 4, 'cost': 3, 'ability': 'poison'},
}

SPELLS = {
    'sp_gold':   {'name': 'Золотая жила',  'icon': '💰', 'desc': '+3 золота', 'cost': 1},
    'sp_fire':   {'name': 'Огненный шар',  'icon': '🔥', 'desc': 'Случайному юниту на поле +2 ATK', 'cost': 2},
    'sp_heal':   {'name': 'Исцеление',     'icon': '💚', 'desc': 'Случайному юниту на поле +2 HP', 'cost': 2},
    'sp_bless':  {'name': 'Благословение', 'icon': '⚡', 'desc': 'Всем юнитам на поле +1 ATK', 'cost': 4},
    'sp_shield': {'name': 'Щит веры',      'icon': '🛡', 'desc': 'Всем юнитам на поле +1 HP', 'cost': 4},
    'sp_rally':  {'name': 'Клич войны',    'icon': '📯', 'desc': 'Всем юнитам на поле +1 ATK и +1 HP', 'cost': 6},
}

ABILITIES = {
    'none':   {'name': '',            'icon': '',  'desc': ''},
    'poison': {'name': 'Яд',          'icon': '☠', 'desc': 'После атаки цель теряет ещё 1 HP'},
    'shield': {'name': 'Щит',         'icon': '🛡', 'desc': 'Первый удар по юниту уменьшен вдвое'},
    'regen':  {'name': 'Регенерация', 'icon': '✚', 'desc': 'Перед своим ударом восстанавливает 1 HP'},
    'cleave': {'name': 'Рассечение',  'icon': '⚡', 'desc': 'Атака также задевает второго врага на 1'},
}

HEROES = [
    {'id': 'econ',    'name': 'Крез',     'desc': '+2 золота в начале раунда',
     'active': {'name': 'Золотая жила', 'desc': '+5 золота', 'cost': 2}},
    {'id': 'smith',   'name': 'Кузнец',   'desc': 'Все ваши юниты +1 HP в бою',
     'active': {'name': 'Закалка', 'desc': 'Всем юнитам на поле +1 HP навсегда', 'cost': 3}},
    {'id': 'warlord', 'name': 'Воевода',  'desc': 'Все ваши юниты +1 ATK в бою',
     'active': {'name': 'Приказ', 'desc': 'Всем юнитам на поле +1 ATK навсегда', 'cost': 3}},
    {'id': 'trader',  'name': 'Торговец', 'desc': 'Апгрейд таверны -2 золота',
     'active': {'name': 'Скидка', 'desc': 'Следующий апгрейд -3 золота', 'cost': 2}},
    {'id': 'alchem',  'name': 'Алхимик',  'desc': 'Первая покупка в раунде -1 золото',
     'active': {'name': 'Трансмутация', 'desc': 'Первый обычный юнит в руке становится золотым', 'cost': 4}},
    {'id': 'master',  'name': 'Мастер',   'desc': 'Поле +1 слот',
     'active': {'name': 'Набор', 'desc': 'Ещё +1 слот поля навсегда', 'cost': 4}},
    {'id': 'bard',    'name': 'Бард',     'desc': '+2 золота за победу в бою',
     'active': {'name': 'Овация', 'desc': '+5 золота', 'cost': 2}},
    {'id': 'warden',  'name': 'Страж',    'desc': 'Стартовое HP +5',
     'active': {'name': 'Стойкость', 'desc': 'Восстановить 4 HP', 'cost': 3}},
]


def _gen_code():
    return 'AR-' + ''.join(random.choices(string.ascii_uppercase, k=4))


def _gen_token():
    return ''.join(random.choices(string.ascii_letters + string.digits, k=24))


def _find_player(g, tok):
    if not tok:
        return None
    toks = g.get('tokens') or []
    for i, t in enumerate(toks):
        if t == tok:
            return i
    return None


def _current():
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


def _init_pool():
    p = {}
    for uid, u in UNITS.items():
        if u['tier'] == 1:
            p[uid] = 14
        elif u['tier'] == 2:
            p[uid] = 10
        else:
            p[uid] = 6
    return p


def _new_unit(uid, golden=False):
    u = UNITS[uid]
    mult = 2 if golden else 1
    return {
        'uid': uid,
        'atk': u['atk'] * mult,
        'hp': u['hp'] * mult,
        'max_hp': u['hp'] * mult,
        'gold': golden,
        'ability': u.get('ability', 'none'),
    }


def _max_field(p):
    return 5 + (1 if p['hero'] == 'master' else 0) + p.get('extra_slots', 0)


def _roll_shop_item(g, p):
    if random.random() < 0.25:
        return random.choice(list(SPELLS.keys()))
    tier = p['tavern']
    pool_ids = [uid for uid, u in UNITS.items()
                if u['tier'] <= tier and g['pool'].get(uid, 0) > 0]
    if not pool_ids:
        return None
    weights = [(4 - UNITS[uid]['tier']) for uid in pool_ids]
    return random.choices(pool_ids, weights=weights, k=1)[0]


def _gen_shop(g, p):
    shop_size = 4 + p['tavern']
    return [_roll_shop_item(g, p) for _ in range(shop_size)]


def _refill_shop_slot(g, p, idx):
    p['shop'][idx] = _roll_shop_item(g, p)


def new_game(code, num_players):
    return {
        'id': code,
        'num_players': num_players,
        'phase': 'lobby',
        'occupied': [False] * num_players,
        'tokens': [None] * num_players,
        'names': [''] * num_players,
        'pool': {},
        'players': [],
        'round': 0,
        'timer': 0,
        'ready': [False] * num_players,
        'combats': [],
        'last_result': [],
        'winner': None,
    }


def _start_recruit(g):
    g['phase'] = 'recruit'
    g['round'] += 1
    g['timer'] = 20
    g['ready'] = [False] * g['num_players']
    g['combats'] = []
    g['last_result'] = []
    for p in g['players']:
        if not p['alive']:
            continue
        gain = 6
        if p['hero'] == 'econ':
            gain += 2
        p['gold'] += gain
        p['bought_this_round'] = 0
        p['used_active'] = False
        p['trade_discount'] = 0
        p['refreshes_this_round'] = 0
        p['shop'] = _gen_shop(g, p)


def _start_game(g):
    n = g['num_players']
    hero_ids = random.sample([h['id'] for h in HEROES], n)
    g['players'] = []
    for i in range(n):
        hp = 15
        if hero_ids[i] == 'warden':
            hp += 5
        g['players'].append({
            'hp': hp,
            'gold': 0,
            'tavern': 1,
            'hero': hero_ids[i],
            'field': [],
            'hand': [],
            'shop': [],
            'alive': True,
            'bought_this_round': 0,
            'refreshes_this_round': 0,
            'used_active': False,
            'trade_discount': 0,
            'extra_slots': 0,
            'snapshot': None,
        })
    g['pool'] = _init_pool()
    _ensure_tick_thread()
    _start_recruit(g)


def maybe_start(g):
    if g['phase'] != 'lobby':
        return
    if not all(g['occupied']):
        return
    _start_game(g)


def _all_ready(g):
    alive = [i for i, p in enumerate(g['players']) if p['alive']]
    if not alive:
        return False
    return all(g['ready'][i] for i in alive)


def _make_army(g, pid):
    p = g['players'][pid]
    army = []
    for u in p['field']:
        atk = u['atk']
        hp = u['hp']
        if p['hero'] == 'smith':
            hp += 1
        if p['hero'] == 'warlord':
            atk += 1
        army.append({'uid': u['uid'], 'atk': atk, 'hp': hp,
                     'max_hp': hp, 'gold': u['gold'],
                     'ability': u.get('ability', 'none')})
    return army


def _snapshot(g, pid):
    g['players'][pid]['snapshot'] = _make_army(g, pid)


def _make_ghost_army(g, pid):
    snap = g['players'][pid].get('snapshot')
    if not snap:
        return []
    return [dict(u) for u in snap]


def _return_field_units(g, pid):
    p = g['players'][pid]
    for u in p['field']:
        if not u['gold']:
            g['pool'][u['uid']] = g['pool'].get(u['uid'], 0) + 1
    p['field'] = []


def _start_combat(g):
    alive = [i for i, p in enumerate(g['players']) if p['alive']]
    if len(alive) <= 1:
        g['phase'] = 'end'
        g['winner'] = alive[0] if alive else None
        return
    order = alive[:]
    random.shuffle(order)
    pairs = []
    i = 0
    while i + 1 < len(order):
        pairs.append({'a': order[i], 'b': order[i + 1], 'ghost': False})
        i += 2
    if i < len(order):
        extra = order[i]
        dead = [j for j, p in enumerate(g['players']) if not p['alive']]
        if dead:
            pairs.append({'a': extra, 'b': random.choice(dead), 'ghost': True})
        else:
            pairs.append({'a': extra, 'b': None, 'ghost': True})

    combats = []
    for pr in pairs:
        ca = _make_army(g, pr['a'])
        if pr['b'] is None:
            cb = []
        elif pr['ghost']:
            cb = _make_ghost_army(g, pr['b'])
        else:
            cb = _make_army(g, pr['b'])
        combats.append({
            'a': pr['a'], 'b': pr['b'], 'ghost': pr['ghost'],
            'army_a': ca, 'army_b': cb,
            'step': 0, 'done': False, 'winner': None,
        })
    g['combats'] = combats
    g['phase'] = 'combat'
    g['timer'] = 0


def _strike(attacker, target):
    dmg = attacker['atk']
    if target.get('ability') == 'shield' and not target.get('shield_used'):
        dmg = (dmg + 1) // 2
        target['shield_used'] = True
    target['hp'] -= dmg


def _do_combat_round(c):
    a = c['army_a']
    b = c['army_b']
    if not a and not b:
        c['winner'] = 'draw'
        return
    if not a:
        c['winner'] = 'b'
        return
    if not b:
        c['winner'] = 'a'
        return

    ua = a[0]
    ub = b[0]

    if ua.get('ability') == 'regen' and ua['hp'] < ua['max_hp']:
        ua['hp'] = min(ua['max_hp'], ua['hp'] + 1)

    _strike(ua, ub)

    if ua.get('ability') == 'poison' and ub['hp'] > 0:
        ub['hp'] -= 1
    if ua.get('ability') == 'cleave' and len(b) >= 2 and b[1]['hp'] > 0:
        b[1]['hp'] -= 1

    if ub['hp'] > 0:
        if ub.get('ability') == 'regen' and ub['hp'] < ub['max_hp']:
            ub['hp'] = min(ub['max_hp'], ub['hp'] + 1)
        _strike(ub, ua)
        if ub.get('ability') == 'poison' and ua['hp'] > 0:
            ua['hp'] -= 1
        if ub.get('ability') == 'cleave' and len(a) >= 2 and a[1]['hp'] > 0:
            a[1]['hp'] -= 1

    a[:] = [u for u in a if u['hp'] > 0]
    b[:] = [u for u in b if u['hp'] > 0]

    if not a and not b:
        c['winner'] = 'draw'
    elif not a:
        c['winner'] = 'b'
    elif not b:
        c['winner'] = 'a'


def _combat_step(g):
    for c in g['combats']:
        if c['done']:
            continue
        c['step'] += 1
        _do_combat_round(c)
        if c['winner'] is not None or c['step'] >= 30:
            if c['winner'] is None:
                ta = sum(u['atk'] for u in c['army_a'])
                tb = sum(u['atk'] for u in c['army_b'])
                if ta > tb:
                    c['winner'] = 'a'
                elif tb > ta:
                    c['winner'] = 'b'
                else:
                    c['winner'] = 'draw'
            c['done'] = True
    if all(c['done'] for c in g['combats']):
        _finish_combat(g)


def _finish_combat(g):
    results = []
    for c in g['combats']:
        if c['b'] is None:
            results.append({'a': c['a'], 'b': None, 'ghost': True,
                            'winner': 'a', 'damage': 0})
            continue
        w = c['winner']
        if w == 'draw':
            results.append({'a': c['a'], 'b': c['b'], 'ghost': c['ghost'],
                            'winner': 'draw', 'damage': 0})
            continue
        if w == 'a':
            dmg = len(c['army_a'])
            if not c['ghost']:
                g['players'][c['b']]['hp'] -= dmg
        else:
            dmg = len(c['army_b'])
            g['players'][c['a']]['hp'] -= dmg
        results.append({'a': c['a'], 'b': c['b'], 'ghost': c['ghost'],
                        'winner': w, 'damage': dmg})

    for c in g['combats']:
        if c['b'] is None:
            continue
        winner = None
        if c['winner'] == 'a':
            winner = c['a']
        elif c['winner'] == 'b' and not c['ghost']:
            winner = c['b']
        if winner is not None and g['players'][winner]['hero'] == 'bard':
            g['players'][winner]['gold'] += 2

    for i, p in enumerate(g['players']):
        if p['alive'] and p['hp'] <= 0:
            p['hp'] = 0
            p['alive'] = False
            _snapshot(g, i)
            _return_field_units(g, i)

    g['last_result'] = results
    alive = [i for i, p in enumerate(g['players']) if p['alive']]
    if len(alive) <= 1:
        g['phase'] = 'end'
        g['winner'] = alive[0] if alive else None
    else:
        g['phase'] = 'result'
        g['timer'] = 4


def _tick_one(g):
    if g['phase'] == 'lobby' or g['phase'] == 'end':
        return
    if g['phase'] == 'recruit':
        g['timer'] -= 1
        if g['timer'] <= 0 or _all_ready(g):
            _start_combat(g)
    elif g['phase'] == 'combat':
        _combat_step(g)
    elif g['phase'] == 'result':
        g['timer'] -= 1
        if g['timer'] <= 0:
            _start_recruit(g)


_tick_started = False
_tick_lock = threading.Lock()


def _ensure_tick_thread():
    global _tick_started
    with _tick_lock:
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


def _award_bonus_card(g, p, uid):
    if len(p['hand']) >= HAND_MAX:
        return
    tier = UNITS[uid]['tier']
    cands = [x for x, u in UNITS.items()
             if u['tier'] == tier and g['pool'].get(x, 0) > 0]
    if not cands:
        return
    cid = random.choice(cands)
    g['pool'][cid] -= 1
    p['hand'].append(_new_unit(cid))


def _collect_triple_sources(p, uid):
    sources = []
    for i, u in enumerate(p['field']):
        if u['uid'] == uid and not u['gold']:
            sources.append(('field', i))
            if len(sources) == 3:
                return sources
    for i, u in enumerate(p['hand']):
        if u['uid'] == uid and not u['gold']:
            sources.append(('hand', i))
            if len(sources) == 3:
                return sources
    return sources


def _check_triples(g, pid):
    p = g['players'][pid]
    while True:
        counts = {}
        for u in p['field']:
            if not u['gold']:
                counts[u['uid']] = counts.get(u['uid'], 0) + 1
        for u in p['hand']:
            if not u['gold']:
                counts[u['uid']] = counts.get(u['uid'], 0) + 1

        target = None
        for uid, c in counts.items():
            if c >= 3:
                target = uid
                break
        if target is None:
            return

        sources = _collect_triple_sources(p, target)
        if len(sources) < 3:
            return

        on_field = any(s[0] == 'field' for s in sources)
        field_idx = sorted([s[1] for s in sources if s[0] == 'field'], reverse=True)
        hand_idx = sorted([s[1] for s in sources if s[0] == 'hand'], reverse=True)

        cards = []
        for i in field_idx:
            cards.append(p['field'].pop(i))
        for i in hand_idx:
            cards.append(p['hand'].pop(i))

        base = UNITS[target]
        base_atk = base['atk']
        base_hp = base['hp']
        bonus_atk = sum(c['atk'] - base_atk for c in cards)
        bonus_hp = sum(c['hp'] - base_hp for c in cards)
        g_atk = base_atk * 2 + bonus_atk
        g_hp = base_hp * 2 + bonus_hp

        golden = {
            'uid': target,
            'atk': g_atk,
            'hp': g_hp,
            'max_hp': g_hp,
            'gold': True,
            'ability': base.get('ability', 'none'),
        }

        if on_field:
            p['field'].append(golden)
        else:
            p['hand'].append(golden)

        _award_bonus_card(g, p, target)


def _apply_spell(g, p, sid):
    if sid == 'sp_gold':
        p['gold'] += 3
    elif sid == 'sp_fire':
        if p['field']:
            u = random.choice(p['field'])
            u['atk'] += 2
    elif sid == 'sp_heal':
        if p['field']:
            u = random.choice(p['field'])
            u['hp'] += 2
            u['max_hp'] += 2
    elif sid == 'sp_bless':
        for u in p['field']:
            u['atk'] += 1
    elif sid == 'sp_shield':
        for u in p['field']:
            u['hp'] += 1
            u['max_hp'] += 1
    elif sid == 'sp_rally':
        for u in p['field']:
            u['atk'] += 1
            u['hp'] += 1
            u['max_hp'] += 1


def _buy(g, pid, idx):
    p = g['players'][pid]
    if not p['alive']:
        return 'Вы выбыли'
    if g['phase'] != 'recruit':
        return 'Сейчас не фаза найма'
    shop = p.get('shop') or []
    if idx < 0 or idx >= len(shop):
        return 'Неверный слот'
    sid = shop[idx]
    if sid is None:
        return 'Пустой слот'

    if sid in SPELLS:
        cost = SPELLS[sid]['cost']
        if p['gold'] < cost:
            return 'Недостаточно золота'
        p['gold'] -= cost
        _apply_spell(g, p, sid)
        _refill_shop_slot(g, p, idx)
        return None

    uid = sid
    if g['pool'].get(uid, 0) <= 0:
        return 'Этого юнита больше нет в пуле'
    if len(p['hand']) >= HAND_MAX:
        return 'Рука полна'
    cost = UNITS[uid]['cost']
    if p['hero'] == 'alchem' and p['bought_this_round'] == 0:
        cost = max(1, cost - 1)
    if p['gold'] < cost:
        return 'Недостаточно золота'
    p['gold'] -= cost
    p['bought_this_round'] += 1
    g['pool'][uid] -= 1
    p['hand'].append(_new_unit(uid))
    _refill_shop_slot(g, p, idx)
    _check_triples(g, pid)
    return None


def _play(g, pid, idx):
    p = g['players'][pid]
    if not p['alive']:
        return 'Вы выбыли'
    if g['phase'] != 'recruit':
        return 'Сейчас не фаза найма'
    if idx < 0 or idx >= len(p['hand']):
        return 'Неверный индекс'
    if len(p['field']) >= _max_field(p):
        return 'Поле заполнено'
    u = p['hand'].pop(idx)
    p['field'].append(u)
    _check_triples(g, pid)
    return None


def _sell_field(g, pid, idx):
    p = g['players'][pid]
    if g['phase'] != 'recruit':
        return 'Сейчас не фаза найма'
    if idx < 0 or idx >= len(p['field']):
        return 'Неверный индекс'
    u = p['field'].pop(idx)
    base = UNITS[u['uid']]['cost']
    refund = base if u['gold'] else max(1, base // 2)
    p['gold'] += refund
    if not u['gold']:
        g['pool'][u['uid']] = g['pool'].get(u['uid'], 0) + 1
    return None


def _sell_hand(g, pid, idx):
    p = g['players'][pid]
    if g['phase'] != 'recruit':
        return 'Сейчас не фаза найма'
    if idx < 0 or idx >= len(p['hand']):
        return 'Неверный индекс'
    u = p['hand'].pop(idx)
    base = UNITS[u['uid']]['cost']
    refund = base if u['gold'] else max(1, base // 2)
    p['gold'] += refund
    if not u['gold']:
        g['pool'][u['uid']] = g['pool'].get(u['uid'], 0) + 1
    return None


def _refresh_shop(g, pid):
    p = g['players'][pid]
    if not p['alive']:
        return 'Вы выбыли'
    if g['phase'] != 'recruit':
        return 'Сейчас не фаза найма'
    cost = REFRESH_COST
    if p['hero'] == 'econ' and p.get('refreshes_this_round', 0) == 0:
        cost = 0
    if p['gold'] < cost:
        return 'Недостаточно золота'
    p['gold'] -= cost
    p['refreshes_this_round'] = p.get('refreshes_this_round', 0) + 1
    p['shop'] = _gen_shop(g, p)
    return None


def _upgrade(g, pid):
    p = g['players'][pid]
    if g['phase'] != 'recruit':
        return 'Сейчас не фаза найма'
    if not p['alive']:
        return 'Вы выбыли'
    if p['tavern'] >= 3:
        return 'Максимальный уровень таверны'
    cost = 5 if p['tavern'] == 1 else 8
    if p['hero'] == 'trader':
        cost = max(1, cost - 2)
    if p.get('trade_discount'):
        cost = max(1, cost - p['trade_discount'])
    if p['gold'] < cost:
        return 'Недостаточно золота'
    p['gold'] -= cost
    p['trade_discount'] = 0
    p['tavern'] += 1
    p['shop'] = _gen_shop(g, p)
    return None


def _set_ready(g, pid):
    if g['phase'] != 'recruit':
        return 'Сейчас не фаза найма'
    g['ready'][pid] = True
    return None


def _use_active(g, pid):
    p = g['players'][pid]
    if g['phase'] != 'recruit':
        return 'Только в фазе найма'
    if not p['alive']:
        return 'Вы выбыли'
    if p.get('used_active'):
        return 'Способность уже использована в этом раунде'
    h = p['hero']
    info = next(x for x in HEROES if x['id'] == h)
    cost = info['active']['cost']
    if p['gold'] < cost:
        return 'Недостаточно золота'
    if h == 'alchem':
        has_target = any(not u['gold'] for u in p['hand'])
        if not has_target:
            return 'Нет обычного юнита в руке'
    p['gold'] -= cost
    if h == 'econ':
        p['gold'] += 5
    elif h == 'smith':
        for u in p['field']:
            u['hp'] += 1
            u['max_hp'] += 1
    elif h == 'warlord':
        for u in p['field']:
            u['atk'] += 1
    elif h == 'trader':
        p['trade_discount'] = 3
    elif h == 'alchem':
        for u in p['hand']:
            if not u['gold']:
                u['gold'] = True
                u['atk'] *= 2
                u['hp'] *= 2
                u['max_hp'] *= 2
                _award_bonus_card(g, p, u['uid'])
                break
        _check_triples(g, pid)
    elif h == 'master':
        p['extra_slots'] = p.get('extra_slots', 0) + 1
    elif h == 'bard':
        p['gold'] += 5
    elif h == 'warden':
        p['hp'] += 4
    p['used_active'] = True
    return None


def _combat_view(c):
    return {
        'a': c['a'],
        'b': c['b'],
        'ghost': c['ghost'],
        'army_a': [dict(u) for u in c['army_a']],
        'army_b': [dict(u) for u in c['army_b']],
        'step': c['step'],
        'done': c['done'],
        'winner': c['winner'],
    }


def _shop_view(g, p):
    items = []
    for i, sid in enumerate(p.get('shop') or []):
        if sid is None:
            items.append(None)
            continue
        if sid in SPELLS:
            s = SPELLS[sid]
            items.append({
                'slot': i, 'kind': 'spell', 'id': sid,
                'name': s['name'], 'icon': s['icon'],
                'desc': s['desc'], 'cost': s['cost'],
            })
        else:
            u = UNITS[sid]
            eff = u['cost']
            if p['hero'] == 'alchem' and p['bought_this_round'] == 0:
                eff = max(1, eff - 1)
            items.append({
                'slot': i, 'kind': 'unit', 'id': sid,
                'name': u['name'], 'tier': u['tier'],
                'atk': u['atk'], 'hp': u['hp'],
                'cost': eff, 'ability': u.get('ability', 'none'),
                'count': g['pool'].get(sid, 0),
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
        'names': g['names'],
        'occupied': g['occupied'],
        'phase': g['phase'],
        'slot': pid,
        'my_name': g['names'][pid],
        'joined': True,
        'unit_names': {uid: u['name'] for uid, u in UNITS.items()},
        'unit_abilities': {uid: u.get('ability', 'none') for uid, u in UNITS.items()},
        'ability_info': ABILITIES,
        'hero_names': {h['id']: h['name'] for h in HEROES},
        'hand_max': HAND_MAX,
        'refresh_cost': REFRESH_COST,
    }

    if g['phase'] == 'lobby':
        base['waiting_opponents'] = not all(g['occupied'])
        base['can_act'] = False
        return base

    p = g['players'][pid]
    base['round'] = g['round']
    base['timer'] = g['timer']
    base['hero'] = p['hero']
    base['hero_info'] = next(h for h in HEROES if h['id'] == p['hero'])
    base['my_hp'] = p['hp']
    base['my_gold'] = p['gold']
    base['my_tavern'] = p['tavern']
    base['my_alive'] = p['alive']
    base['my_field'] = p['field']
    base['my_hand'] = p['hand']
    base['my_ready'] = g['ready'][pid] if g['phase'] == 'recruit' else False
    base['my_max_field'] = _max_field(p)
    base['my_active_used'] = bool(p.get('used_active'))
    base['my_trade_discount'] = p.get('trade_discount', 0)
    base['my_shop'] = _shop_view(g, p)

    others = []
    for i in range(g['num_players']):
        op = g['players'][i]
        others.append({
            'slot': i,
            'name': g['names'][i],
            'hp': op['hp'],
            'alive': op['alive'],
            'tavern': op['tavern'],
            'hero': op['hero'],
        })
    base['players'] = others

    base['pool'] = dict(g['pool'])

    up_cost = None
    if p['tavern'] < 3:
        up_cost = 5 if p['tavern'] == 1 else 8
        if p['hero'] == 'trader':
            up_cost = max(1, up_cost - 2)
        if p.get('trade_discount'):
            up_cost = max(1, up_cost - p['trade_discount'])
    base['upgrade_cost'] = up_cost

    base['can_act'] = (g['phase'] == 'recruit' and p['alive'])

    if g['phase'] == 'combat':
        vis = []
        for c in g['combats']:
            if pid == c['a'] or pid == c['b']:
                vis.append(_combat_view(c))
        base['combats'] = vis
    else:
        base['combats'] = []

    if g['phase'] == 'result':
        base['last_result'] = g['last_result']
    else:
        base['last_result'] = []

    if g['phase'] == 'end':
        base['winner'] = g.get('winner')

    return base


@bp.route('/api/state')
def api_state():
    with LOCK:
        g, pid = _current()
        if g is None:
            return jsonify({'ok': False, 'error': 'not in game'}), 404
        return jsonify({'ok': True, 'state': build_state(g, pid)})


@bp.route('/api/action', methods=['POST'])
def api_action():
    data = request.get_json(silent=True) or {}
    action = data.get('action')
    with LOCK:
        g, pid = _current()
        if g is None:
            return jsonify({'ok': False, 'error': 'not in game'}), 404
        err = None
        if action == 'buy':
            try:
                idx = int(data.get('idx'))
            except (TypeError, ValueError):
                idx = -1
            err = _buy(g, pid, idx)
        elif action == 'play':
            try:
                idx = int(data.get('idx'))
            except (TypeError, ValueError):
                idx = -1
            err = _play(g, pid, idx)
        elif action == 'sell':
            try:
                idx = int(data.get('idx'))
            except (TypeError, ValueError):
                idx = -1
            err = _sell_field(g, pid, idx)
        elif action == 'sell_hand':
            try:
                idx = int(data.get('idx'))
            except (TypeError, ValueError):
                idx = -1
            err = _sell_hand(g, pid, idx)
        elif action == 'refresh':
            err = _refresh_shop(g, pid)
        elif action == 'upgrade':
            err = _upgrade(g, pid)
        elif action == 'ready':
            err = _set_ready(g, pid)
        elif action == 'use_active':
            err = _use_active(g, pid)
        else:
            err = 'Неизвестное действие'
        if err:
            return jsonify({'ok': False, 'error': err}), 400
        return jsonify({'ok': True, 'state': build_state(g, pid)})


def create_game(name, mode=2):
    with LOCK:
        try:
            n = int(mode)
        except (TypeError, ValueError):
            n = 2
        if n < 2:
            n = 2
        if n > 8:
            n = 8
        code = _gen_code()
        while code in GAMES:
            code = _gen_code()
        g = new_game(code, n)
        GAMES[code] = g
        tok = _gen_token()
        g['occupied'][0] = True
        g['tokens'][0] = tok
        g['names'][0] = name
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
                g['names'][i] = name
                maybe_start(g)
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
            g['names'][i] = ''
