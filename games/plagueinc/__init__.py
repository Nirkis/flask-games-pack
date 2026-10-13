# -*- coding: utf-8 -*-
"""Plague Inc — realtime 1-4 players, shared population pool."""
import random
import threading
import time

from flask import Blueprint, jsonify, request, Response, stream_with_context

from games import events as events_bus
from games import game_base
from games import heartbeat
from games.data_loader import load

GAME_META = {
    'code': 'pl',
    'prefix': 'PL',
    'name': 'Plague Inc',
    'players': '1-4',
    'template': 'plagueinc/index.html',
    'modes': [1, 2, 3, 4],
    'realtime': True,
    'order': 50,
}

bp = Blueprint('plague_inc', __name__, url_prefix='/pl',
               template_folder='templates')

# ---------- Данные ----------
DISEASE_TYPES = load('diseases', schema={'*': {
    'name': str, 'desc': str,
    'spread': float, 'kill': float, 'vis': float, 'vaccine': float,
    'organ_mult': {'*': float},
    'cost_mult': float, 'passive': str,
}})

SKILLS_DATA = load('skills', schema={
    'nodes': {'*': {
        'name': str, 'emoji': str, 'x': int, 'y': int, 'max': int,
        'requires': list, 'system': str,
        'effect': {'*': float}, 'desc': str, 'cost_tier': str,
    }},
    'costs': {'*': {'base': int, 'per_level': int}},
})

RULES = load('rules', schema={
    'world_pop': int, 'start_infected': int, 'start_vis': float,
    'detect_threshold': float, 'extinct_threshold': int,
    'tick_interval': float,
    'base_spread': float, 'base_kill': float, 'base_vis': float,
    'vis_per_ratio': float, 'cure_max': float, 'cure_detected': float,
    'cure_removal': float, 'neut_mu_zero_tick': int,
    'start_skill_points': int,
})

SKILL_NODES = SKILLS_DATA['nodes']
COST_BY_TIER = SKILLS_DATA['costs']

WORLD_POP = RULES['world_pop']
START_INFECTED = RULES['start_infected']
START_VIS = RULES['start_vis']
DETECT_THRESHOLD = RULES['detect_threshold']
EXTINCT_THRESHOLD = RULES['extinct_threshold']
TICK_INTERVAL = RULES['tick_interval']
BASE_SPREAD = RULES['base_spread']
BASE_KILL = RULES['base_kill']
BASE_VIS = RULES['base_vis']
VIS_PER_RATIO = RULES['vis_per_ratio']
CURE_MAX = RULES['cure_max']
CURE_DETECTED = RULES['cure_detected']
CURE_REMOVAL = RULES['cure_removal']
NEUT_MU_ZERO_TICK = RULES['neut_mu_zero_tick']
START_SKILL_POINTS = RULES['start_skill_points']

_tick_started = False


def _fmt(n):
    return '{:,}'.format(int(n)).replace(',', ' ')


def add_log(g, text):
    g['log'].append(text)
    if len(g['log']) > 100:
        g['log'] = g['log'][-100:]


def upgrade_cost(node_id, current_level, type_mult):
    tier = COST_BY_TIER[SKILL_NODES[node_id]['cost_tier']]
    cost = tier['base'] + current_level * tier['per_level']
    return max(1, int(round(cost * type_mult)))


def new_disease(dtype):
    return {
        'type': dtype,
        'infected': START_INFECTED, 'peak_infected': START_INFECTED, 'killed': 0,
        'visibility': START_VIS, 'detected': False,
        'cure': 0.0, 'neutralized': False, 'neutralized_at': None, 'extinct': False,
        'skills': {nid: 0 for nid in SKILL_NODES},
        'skill_points': START_SKILL_POINTS,
        'om_peaks_used': 0, 'score': 0, 'passive_counter': 0,
    }


# ---------- Эффекты ----------
def compute_effects(d):
    eff = {'spread': 0.0, 'kill': 0.0, 'stealth': 0.0,
           'visibility': 0.0, 'vaccine_slow': 0.0, 'om_rate': 0.0}
    if d['type'] is None:
        return eff
    dt = DISEASE_TYPES[d['type']]
    org = dt['organ_mult']
    body_bonus = d['skills'].get('body', 0) * SKILL_NODES['body']['effect']['all']
    all_mult = 1.0 + body_bonus
    for nid, lvl in d['skills'].items():
        if lvl <= 0 or nid == 'body':
            continue
        node = SKILL_NODES[nid]
        sys_mult = org.get(node.get('system', 'root'), 1.0)
        for k, v in node['effect'].items():
            eff[k] += v * lvl * sys_mult * all_mult
    return eff


def recompute_score(d):
    d['score'] = int(d['killed'] // 100) * 5 + int(d['peak_infected'] // 1000) * 1


# ---------- Такт мира ----------
def _rebalance_pool(g):
    total_alive = max(0, WORLD_POP - int(g['world_killed']))
    total_inf = sum(d['infected'] for d in g['diseases'] if not d['extinct'])
    if total_inf > total_alive and total_inf > 0:
        scale = total_alive / total_inf
        for d in g['diseases']:
            if not d['extinct']:
                d['infected'] *= scale


def world_tick(g):
    total_alive = max(0, WORLD_POP - int(g['world_killed']))
    total_inf_before = sum(d['infected'] for d in g['diseases'] if not d['extinct'])
    free_pool = max(0.0, total_alive - total_inf_before)

    claims = []
    for d in g['diseases']:
        if d['extinct'] or d['neutralized'] or d['type'] is None or d['infected'] <= 0:
            claims.append((d, 0.0))
            continue
        eff = compute_effects(d)
        dt = DISEASE_TYPES[d['type']]
        spread_mult = (1.0 + eff['spread']) * dt['spread']
        if d['type'] == 'parasite':
            if d['visibility'] < 30:
                spread_mult *= 1.5
            elif d['visibility'] >= 50:
                spread_mult *= 0.8
        rate = BASE_SPREAD * spread_mult
        desired = d['infected'] * rate * (1.0 - d['infected'] / float(WORLD_POP))
        if desired < 0:
            desired = 0.0
        claims.append((d, desired))

    total_desired = sum(c[1] for c in claims)
    scale = (free_pool / total_desired) if (total_desired > free_pool and total_desired > 0) else 1.0

    for d, desired in claims:
        if desired <= 0:
            continue
        add = desired * scale
        if add <= 0:
            continue
        d['infected'] += add
        if d['infected'] > d['peak_infected']:
            d['peak_infected'] = d['infected']

    for d in g['diseases']:
        if d['extinct'] or d['type'] is None:
            continue
        eff = compute_effects(d)
        dt = DISEASE_TYPES[d['type']]
        kill_mult = (1.0 + eff['kill']) * dt['kill']
        if d['type'] == 'prion' and g['tick_count'] >= 30:
            kill_mult *= 2.0
        if d['neutralized'] and d['neutralized_at'] is not None:
            elapsed = g['tick_count'] - d['neutralized_at']
            kill_mult = 0.0 if elapsed >= NEUT_MU_ZERO_TICK else kill_mult * 0.25 ** elapsed
        if d['infected'] > 0 and kill_mult > 0:
            deaths = d['infected'] * BASE_KILL * kill_mult
            if deaths > d['infected']:
                deaths = d['infected']
            d['killed'] += deaths
            d['infected'] -= deaths
            g['world_killed'] += deaths
    if g['world_killed'] > WORLD_POP:
        g['world_killed'] = WORLD_POP

    for d in g['diseases']:
        if d['extinct'] or d['detected'] or d['neutralized'] or d['type'] is None:
            continue
        eff = compute_effects(d)
        dt = DISEASE_TYPES[d['type']]
        ratio = d['infected'] / float(WORLD_POP)
        growth = (BASE_VIS + ratio * VIS_PER_RATIO) * dt['vis']
        growth *= 1.0 / (1.0 + eff['stealth'])
        growth *= 1.0 + eff['visibility']
        d['visibility'] += growth
        if d['visibility'] >= DETECT_THRESHOLD:
            d['visibility'] = DETECT_THRESHOLD
            d['detected'] = True

    for d in g['diseases']:
        if d['extinct'] or not d['neutralized']:
            continue
        if d['infected'] > 0:
            d['infected'] *= (1.0 - CURE_REMOVAL)

    for d in g['diseases']:
        if d['extinct']:
            continue
        if d['infected'] < EXTINCT_THRESHOLD and (d['neutralized'] or d['infected'] <= 0):
            d['extinct'] = True
            d['infected'] = 0

    _rebalance_pool(g)
    for d in g['diseases']:
        recompute_score(d)


def update_cure(g):
    for idx, d in enumerate(g['diseases']):
        if d['extinct'] or d['neutralized'] or not d['detected'] or d['type'] is None:
            continue
        eff = compute_effects(d)
        dt = DISEASE_TYPES[d['type']]
        rate = CURE_DETECTED * dt['vaccine'] / (1.0 + eff['vaccine_slow'])
        d['cure'] += rate
        if d['cure'] >= CURE_MAX:
            d['cure'] = CURE_MAX
            d['neutralized'] = True
            d['neutralized_at'] = g['tick_count']
            add_log(g, '💉 Болезнь %s нейтрализована!' % g['names'][idx])


def award_om(g):
    for d in g['diseases']:
        if d['extinct'] or d['type'] is None:
            continue
        om_rate = 1.0 + compute_effects(d).get('om_rate', 0.0)
        new_chunk = int(d['peak_infected'] // 1000)
        if new_chunk > d['om_peaks_used']:
            delta = new_chunk - d['om_peaks_used']
            d['om_peaks_used'] = new_chunk
            d['skill_points'] += max(0, int(delta * om_rate))


def handle_passives(g):
    for idx, d in enumerate(g['diseases']):
        if d['extinct'] or d['neutralized'] or d['type'] is None:
            continue
        if d['type'] == 'bacteria':
            d['passive_counter'] += 1
            if d['passive_counter'] >= 20:
                d['passive_counter'] = 0
                opened = [nid for nid, lvl in d['skills'].items()
                          if 0 < lvl < SKILL_NODES[nid]['max']]
                if opened:
                    nid = random.choice(opened)
                    d['skills'][nid] += 1
                    add_log(g, '🦠 %s: %s ур. %d' % (
                        g['names'][idx], SKILL_NODES[nid]['name'], d['skills'][nid]))
        elif d['type'] == 'virus':
            d['passive_counter'] += 1
            if d['passive_counter'] >= 15:
                d['passive_counter'] = 0
                critical = set()
                for nid, lvl in d['skills'].items():
                    if lvl > 0:
                        for rid, _ in SKILL_NODES[nid]['requires']:
                            critical.add(rid)
                up = [nid for nid, lvl in d['skills'].items()
                      if 0 < lvl < SKILL_NODES[nid]['max']]
                dn = [nid for nid, lvl in d['skills'].items()
                      if lvl > 0 and nid not in critical]
                if random.random() < 0.5 and up:
                    nid = random.choice(up)
                    d['skills'][nid] += 1
                    add_log(g, '🧬 %s: +1 %s' % (g['names'][idx], SKILL_NODES[nid]['name']))
                elif dn:
                    nid = random.choice(dn)
                    d['skills'][nid] -= 1
                    add_log(g, '🧬 %s: −1 %s' % (g['names'][idx], SKILL_NODES[nid]['name']))


def record_history(g):
    g['history'].append({
        'n': g['tick_count'],
        'cure':     [round(d['cure'], 2) for d in g['diseases']],
        'infected': [int(d['infected']) for d in g['diseases']],
        'killed':   [int(d['killed'])   for d in g['diseases']],
        'vis':      [round(d['visibility'], 1) for d in g['diseases']],
        'score':    [int(d['score'])    for d in g['diseases']],
    })
    if len(g['history']) > 400:
        g['history'] = g['history'][-400:]


def check_over(g):
    if g['phase'] != 'battle':
        return
    if not all(d['extinct'] for d in g['diseases']):
        return
    g['phase'] = 'over'
    n = g['num_players']
    if n == 1:
        g['winner'] = 0
        add_log(g, '🧪 Конец. Убито: %s' % _fmt(g['diseases'][0]['killed']))
        return
    kills = sorted(((i, d['killed']) for i, d in enumerate(g['diseases'])),
                   key=lambda x: -x[1])
    top = kills[0][1]
    second = kills[1][1] if len(kills) > 1 else 0
    if top > 0 and (top - second) / max(top, 1.0) > 0.005:
        g['winner'] = kills[0][0]
        add_log(g, '🏆 Побеждает %s — убито %s' % (
            g['names'][g['winner']], _fmt(top)))
    else:
        g['winner'] = -1
        add_log(g, '🤝 Ничья')


# ---------- Жизненный цикл партии ----------
def start_game(g):
    g['diseases'] = [new_disease(dt) for dt in g['dtypes']]
    g['world_killed'] = 0
    g['winner'] = None
    g['phase'] = 'battle'
    g['tick_count'] = 0
    g['history'] = []
    for i, d in enumerate(g['diseases']):
        recompute_score(d)
        add_log(g, '🧫 %s играет за %s' % (
            g['names'][i], DISEASE_TYPES[d['type']]['name']))
    add_log(g, '🦠 Пандемия началась! Население: %s' % _fmt(WORLD_POP))
    record_history(g)
    _ensure_tick_thread()


def _tick_one(g, now):
    if g['phase'] != 'battle':
        return
    if not heartbeat.alive_tokens(g):
        return
    world_tick(g)
    update_cure(g)
    award_om(g)
    g['tick_count'] += 1
    handle_passives(g)
    record_history(g)
    check_over(g)


def _tick_loop():
    while True:
        time.sleep(TICK_INTERVAL)
        try:
            with LOCK:
                for code, g in list(GAMES.items()):
                    try:
                        _tick_one(g, time.time())
                        _broadcast(g)
                    except Exception:
                        pass
        except Exception:
            pass


def _ensure_tick_thread():
    global _tick_started
    if _tick_started:
        return
    _tick_started = True
    threading.Thread(target=_tick_loop, daemon=True, name='pl_tick').start()


def _broadcast(g):
    items = []
    for i, tok in enumerate(g['tokens']):
        if tok is None:
            continue
        items.append((tok, {'type': 'state', 'state': _build_state(g, i)}))
    if items:
        events_bus.push_many(g['id'], items)


# ---------- Действия ----------
def do_upgrade(g, pid, node_id):
    if g['phase'] != 'battle':
        return 'Игра не активна'
    if pid is None or pid >= len(g['diseases']):
        return 'Вы ещё не в игре'
    d = g['diseases'][pid]
    if d['extinct'] or d['neutralized']:
        return 'Болезнь нейтрализована'
    node = SKILL_NODES.get(node_id)
    if node is None:
        return 'Неизвестный узел'
    if d['skills'].get(node_id, 0) >= node['max']:
        return 'Узел максимален'
    for rid, rlvl in node['requires']:
        if d['skills'].get(rid, 0) < rlvl:
            return 'Требуется %s ур. %d' % (SKILL_NODES[rid]['name'], rlvl)
    dt = DISEASE_TYPES[d['type']]
    cost = upgrade_cost(node_id, d['skills'].get(node_id, 0), dt['cost_mult'])
    if d['skill_points'] < cost:
        return 'Нужно %d ОМ (у вас %d)' % (cost, d['skill_points'])
    d['skill_points'] -= cost
    d['skills'][node_id] = d['skills'].get(node_id, 0) + 1
    add_log(g, '⬆ %s: %s → ур. %d (−%d ОМ)' % (
        g['names'][pid], node['name'], d['skills'][node_id], cost))
    return None


def do_set_dtype(g, pid, dtype):
    if g['phase'] != 'lobby':
        return 'Игра уже началась'
    if pid is None or pid >= g['num_players'] or not g['occupied'][pid]:
        return 'Вы не в игре'
    if dtype not in DISEASE_TYPES:
        return 'Неизвестный тип болезни'
    g['dtypes'][pid] = dtype
    add_log(g, '🧫 %s выбрал %s' % (g['names'][pid], DISEASE_TYPES[dtype]['name']))
    _maybe_start(g)
    return None


def do_reset(g, pid):
    if g['phase'] == 'lobby':
        return None
    g['phase'] = 'lobby'
    g['winner'] = None
    g['diseases'] = []
    g['world_killed'] = 0
    g['tick_count'] = 0
    g['history'] = []
    for i in range(g['num_players']):
        g['dtypes'][i] = None
    add_log(g, '🔄 Новая партия')
    return None


# ---------- Колбэки GameServer ----------
def _new_game(code, mode=2):
    mode = max(1, min(4, mode))
    return {
        'id': code,
        'num_players': mode,
        'names': ['Игрок %d' % (i + 1) for i in range(mode)],
        'tokens': [None] * mode,
        'occupied': [False] * mode,
        'dtypes': [None] * mode,
        'phase': 'lobby',
        'winner': None,
        'diseases': [],
        'world_killed': 0,
        'log': [],
        'history': [],
        'created': time.time(),
        'tick_count': 0,
    }


def _attach_player(g, slot, name, tok):
    g['names'][slot] = name
    g['tokens'][slot] = tok
    g['occupied'][slot] = True
    add_log(g, '👤 %s присоединяется' % name)


def _get_tokens(g):
    return g['tokens']


def _drop_player(g, tok):
    if g['phase'] == 'lobby':
        for i in range(g['num_players']):
            if g['tokens'][i] == tok:
                g['tokens'][i] = None
                g['occupied'][i] = False
                g['dtypes'][i] = None
                break


def _player_names(g):
    return [g['names'][i] for i in range(g['num_players']) if g['occupied'][i]]


def _phase_of(g):
    return g['phase']


def _maybe_start(g):
    if g['phase'] != 'lobby':
        return
    if not all(g['occupied']):
        return
    if any(dt is None for dt in g['dtypes']):
        return
    start_game(g)


# ---------- Сериализация ----------
def _node_view(node_id, node, d, can_act):
    lvl = d['skills'].get(node_id, 0) if d.get('type') else 0
    reqs_info = []
    reqs_ok = True
    for rid, rlvl in node['requires']:
        cur = d['skills'].get(rid, 0)
        met = cur >= rlvl
        if not met:
            reqs_ok = False
        reqs_info.append({'id': rid, 'name': SKILL_NODES[rid]['name'],
                          'have': cur, 'need': rlvl, 'met': met})
    if d.get('type'):
        dt = DISEASE_TYPES[d['type']]
        cost = upgrade_cost(node_id, lvl, dt['cost_mult'])
        can = can_act and lvl < node['max'] and reqs_ok and d['skill_points'] >= cost
    else:
        cost = 0
        can = False
    return {
        'id': node_id, 'name': node['name'], 'emoji': node['emoji'],
        'x': node['x'], 'y': node['y'], 'max': node['max'], 'level': lvl,
        'is_combo': node['cost_tier'] in ('combo', 'final', 'legendary'),
        'desc': node['desc'],
        'requires': reqs_info, 'reqs_ok': reqs_ok,
        'can_upgrade': can, 'cost': cost,
    }


def _empty_disease(idx, name, dtype):
    return {
        'idx': idx, 'name': name, 'type': dtype,
        'infected': 0, 'peak_infected': 0, 'killed': 0,
        'visibility': 0.0, 'detected': False,
        'cure': 0.0, 'neutralized': False, 'extinct': False, 'is_me': True,
        'skills': {nid: 0 for nid in SKILL_NODES},
        'skill_points': 0, 'score': 0,
    }


def _build_state(g, pid):
    if g is None:
        return {'joined': False, 'phase': 'no_game'}

    base = {
        'game_id': g['id'],
        'num_players': g['num_players'],
        'names': list(g['names']),
        'occupied': list(g['occupied']),
        'dtypes': list(g['dtypes']),
        'cure_max': CURE_MAX,
        'detect_threshold': DETECT_THRESHOLD,
        'extinct_threshold': EXTINCT_THRESHOLD,
        'tick_interval': TICK_INTERVAL,
        'disease_types': DISEASE_TYPES,
    }

    if pid is None or pid < 0 or pid >= g['num_players']:
        base.update({
            'joined': False, 'slot': -1, 'phase': g['phase'],
            'my_name': '', 'my_dtype': None,
            'world_pop': WORLD_POP, 'world_alive': WORLD_POP,
            'world_killed': 0, 'tick_count': 0,
            'uninfected': WORLD_POP, 'total_infected': 0,
            'diseases': [], 'my_disease': None,
            'skill_nodes': [],
            'winner': None, 'i_won': False, 'draw': False,
            'can_act': False,
            'log': g['log'][-30:], 'history': [],
            'waiting_opponents': True, 'all_ready': False,
        })
        return base

    if g['phase'] == 'lobby' or not g['diseases']:
        my_dtype = g['dtypes'][pid]
        empty = _empty_disease(pid, g['names'][pid], my_dtype)
        base.update({
            'joined': True, 'slot': pid, 'phase': 'lobby',
            'my_name': g['names'][pid], 'my_dtype': my_dtype,
            'world_pop': WORLD_POP, 'world_alive': WORLD_POP,
            'world_killed': 0, 'tick_count': 0,
            'uninfected': WORLD_POP, 'total_infected': 0,
            'diseases': [], 'my_disease': empty,
            'skill_nodes': [],
            'winner': None, 'i_won': False, 'draw': False,
            'can_act': False,
            'log': g['log'][-30:], 'history': [],
            'waiting_opponents': not all(g['occupied']),
            'all_ready': all(g['occupied']) and all(dt is not None for dt in g['dtypes']),
        })
        return base

    diseases_view = []
    for i, d in enumerate(g['diseases']):
        diseases_view.append({
            'idx': i, 'name': g['names'][i], 'type': d['type'],
            'infected': int(d['infected']), 'peak_infected': int(d['peak_infected']),
            'killed': int(d['killed']),
            'visibility': round(d['visibility'], 1), 'detected': d['detected'],
            'cure': round(d['cure'], 1),
            'neutralized': d['neutralized'], 'extinct': d['extinct'],
            'is_me': i == pid,
            'skills': dict(d['skills']),
            'skill_points': int(d['skill_points']),
            'score': int(d['score']),
        })

    me_raw = g['diseases'][pid]
    me = diseases_view[pid]
    total_alive = max(0, WORLD_POP - int(g['world_killed']))
    total_infected = sum(int(d['infected']) for d in g['diseases'] if not d['extinct'])
    uninfected = max(0, total_alive - total_infected)
    can_act = (g['phase'] == 'battle') and not me['extinct'] and not me['neutralized']

    nodes_view = [_node_view(nid, n, me_raw, can_act)
                  for nid, n in SKILL_NODES.items()]

    base.update({
        'joined': True, 'slot': pid, 'phase': g['phase'],
        'my_name': g['names'][pid], 'my_dtype': g['dtypes'][pid],
        'world_pop': WORLD_POP, 'world_alive': total_alive,
        'world_killed': int(g['world_killed']),
        'uninfected': uninfected, 'total_infected': total_infected,
        'tick_count': g['tick_count'],
        'diseases': diseases_view, 'my_disease': me,
        'skill_nodes': nodes_view,
        'winner': g['winner'],
        'i_won': g['winner'] == pid, 'draw': g['winner'] == -1,
        'can_act': can_act,
        'log': g['log'][-30:], 'history': g['history'],
        'waiting_opponents': False, 'all_ready': True,
    })
    return base


# ---------- GameServer ----------
server = game_base.GameServer(
    prefix='PL',
    max_players=4,
    min_players=1,
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


# ---------- API ----------
@bp.route('/api/state')
def api_state():
    with LOCK:
        g, pid = _current()
        if g is None:
            return jsonify({'ok': False, 'error': 'Нет активной игры'}), 404
        return jsonify({'ok': True, 'state': _build_state(g, pid)})


@bp.route('/api/events')
def api_events():
    g, pid = _current()
    if g is None or pid is None:
        return ('', 401)
    code = g['id']
    tok = g['tokens'][pid]
    sub = events_bus.subscribe(code, tok)

    @stream_with_context
    def _gen():
        try:
            yield 'event: hello\ndata: {}\n\n'
            with LOCK:
                g2 = GAMES.get(code)
                if (g2 is not None and pid < len(g2['tokens'])
                        and g2['tokens'][pid] == tok):
                    yield events_bus.to_sse(
                        {'type': 'state', 'state': _build_state(g2, pid)})
            while True:
                payload, status = sub.wait_next(20)
                if status == 'closed':
                    break
                if payload is None:
                    yield ': keepalive\n\n'
                    continue
                yield events_bus.to_sse(payload)
        finally:
            events_bus.unsubscribe(sub)

    resp = Response(_gen(), mimetype='text/event-stream')
    resp.headers['Cache-Control'] = 'no-cache, no-transform'
    resp.headers['X-Accel-Buffering'] = 'no'
    return resp


@bp.route('/api/action', methods=['POST'])
def api_action():
    with LOCK:
        g, pid = _current()
        if g is None:
            return jsonify({'ok': False, 'error': 'Нет активной игры'}), 404
        data = request.get_json(silent=True) or {}
        action = data.get('action')
        if action == 'upgrade':
            err = do_upgrade(g, pid, data.get('node'))
        elif action == 'set_type':
            err = do_set_dtype(g, pid, data.get('dtype'))
        elif action == 'reset':
            err = do_reset(g, pid)
        else:
            return jsonify({'ok': False, 'error': 'Неизвестное действие'}), 400
        if err:
            return jsonify({'ok': False, 'error': err}), 400
        _broadcast(g)
        return jsonify({'ok': True, 'state': _build_state(g, pid)})
