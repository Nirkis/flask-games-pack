"""games/rts/__init__.py — RTS для Flask-Games-pack.

Модуль экспортирует GAME_META и bp — этого достаточно для autodiscover.
Вся симуляция живёт в games/rts/sim/, здесь — только обвязка портала:
словарь партии, хуки GameServer, heartbeat-пауза и роуты.
"""
import json
import os
import threading
import time

from flask import Blueprint, jsonify, request

from games import game_base, heartbeat
from games import events as events_bus
from games.data_loader import load

from .sim.world import World
from .sim.runtime import GameRuntime
from .sim.grid import Grid


# --------------------------------------------------------------------
# Метаданные игры (обязательно для autodiscover)
# --------------------------------------------------------------------
GAME_META = {
    'code': 'rts',
    'prefix': 'RT',
    'name': 'RTS',
    'players': '2-4',
    'template': 'rts/index.html',
    'modes': [2, 3, 4],
    'realtime': True,
    'order': 60,
}


# --------------------------------------------------------------------
# Blueprint (статика и шаблоны — из пакета rts)
# --------------------------------------------------------------------
bp = Blueprint(
    'rts', __name__, url_prefix='/rts',
    template_folder='templates',
    static_folder='static',
    static_url_path='/static',
)


# --------------------------------------------------------------------
# Данные: загружаем один раз через data_loader
# --------------------------------------------------------------------
_DATA = {}


def _load_map():
    path = os.path.join(os.path.dirname(__file__), 'data', 'maps',
                        'example_map.json')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print('[rts] map load failed: %r' % (e,))
        # безопасный fallback — пустая карта 64×64
        return {
            'name': 'fallback',
            'size': [64, 64],
            'tile_size': 32,
            'tiles': [0] * (64 * 64),
            'start_positions': [[8, 8], [56, 56]],
            'resource_nodes': [],
            'neutral_creeps': [],
        }


def _ensure_data():
    """Кэшируем всё, что не меняется между партиями."""
    if _DATA:
        return _DATA
    _DATA['abilities'] = load('abilities')
    _DATA['units'] = load('units')
    _DATA['buildings'] = load('buildings')
    _DATA['rules'] = load('rules')
    _DATA['map'] = _load_map()
    return _DATA


# --------------------------------------------------------------------
# Хуки GameServer
# --------------------------------------------------------------------
def _new_game(code, mode=2):
    n = max(2, min(4, int(mode)))
    return {
        'id': code,
        'num_players': n,
        'names': ['Игрок %d' % (i + 1) for i in range(n)],
        'player_races': [None] * n,  # что выбрал игрок (может быть None)
        'races': None,  # финальные расы после старта
        'tokens': [None] * n,
        'occupied': [False] * n,
        'phase': 'lobby',
        'created': time.time(),
        'last_seen': {},
        'world': None,
        'runtime': None,
        'thread': None,
        'started_at': None,
    }


def _attach_player(g, slot, name, tok):
    g['occupied'][slot] = True
    g['tokens'][slot] = tok
    g['names'][slot] = name or ('Игрок %d' % (slot + 1))


def _get_tokens(g):
    return g['tokens']


def _drop_player(g, tok):
    for i in range(g['num_players']):
        if g['tokens'][i] == tok:
            g['tokens'][i] = None
            g['occupied'][i] = False
            break


def _player_names(g):
    return [g['names'][i] for i, occ in enumerate(g['occupied']) if occ]


def _phase_of(g):
    return g['phase']


def _is_lobby(g):
    return g['phase'] == 'lobby'

# расы по индексу слота
_RACES_BY_SLOT = ['alliance', 'horde', 'undead', 'night_elf']

_VALID_RACES = set(_RACES_BY_SLOT)

# главное здание каждой расы
_MAIN_BUILDING = {
    'alliance':   'town_hall',
    'horde':      'great_hall',
    'undead':     'necropolis',
    'night_elf':  'tree_of_life',
}

# стартовые юниты (по SPEC: 3 рабочих + 1 боевой, у нежити/НЭ — только рабочие)
_STARTING_UNITS = {
    'alliance':   ['worker', 'worker', 'worker', 'footman'],
    'horde':      ['peon',   'peon',   'peon',   'grunt'],
    'undead':     ['acolyte', 'acolyte', 'acolyte'],
    'night_elf':  ['wisp',   'wisp',   'wisp'],
}


def _spawn_starting_units(runtime, g, data):
    """Спавним главное здание + стартовых юнитов возле start_positions."""
    from .sim.entity import Entity

    rules = data['rules']
    units_data = data['units']
    buildings_data = data['buildings']
    map_data = data['map']
    start_positions = map_data.get('start_positions') or [[8, 8], [56, 56]]

    world = runtime.world

    for slot in range(g['num_players']):
        if not g['occupied'][slot]:
            continue
        race = g['races'][slot]
        base_x, base_y = start_positions[slot % len(start_positions)]
        base_x = float(base_x); base_y = float(base_y)

        # --- главное здание ---
        btype = _MAIN_BUILDING[race]
        bd = buildings_data.get(btype, {})
        b_hp = int(bd.get('hp', 1500))
        b = Entity(
            id=None,
            type_id=btype,
            owner=slot,
            pos=[base_x, base_y],
            hp=b_hp,
            max_hp=b_hp,
            speed=0.0,
            vision=float(bd.get('vision', 10)),
            radius=float((bd.get('footprint') or [4, 4])[0]) * 0.5,
            armor=int(bd.get('armor', 5)),
            armor_type=str(bd.get('armor_type', 'fortified')),
        )
        b.is_building = True
        b.under_construction = False
        b.food_provided = int(bd.get('food_provided', 10))
        b.accepts_resources = bool(bd.get('accepts_resources', True))
        b.produces = list(bd.get('produces', []))
        b.footprint = list(bd.get('footprint', [4, 4]))
        world.spawn(b)

        # сколько еды «стоит» каждый стартовый юнит — берём из train-меню
        main_data = buildings_data.get(btype) or {}
        main_abils = main_data.get('abilities') or []
        food_map = {}
        for ab_id in main_abils:
            ab = data['abilities'].get(ab_id) or {}
            if ab.get('kind') != 'menu':
                continue
            for opt in ab.get('options') or []:
                food_map[opt.get('id')] = int((opt.get('cost') or {}).get('food', 0))

        # --- стартовые юниты ---
        starting = _STARTING_UNITS.get(race, [])
        for i, utype in enumerate(starting):
            ud = units_data.get(utype)
            if not ud:
                continue
            # расставим по небольшому кругу вокруг базы
            import math
            angle = (i / max(1, len(starting))) * 2.0 * math.pi
            ux = base_x + 3.0 + math.cos(angle) * 1.5
            uy = base_y + 3.0 + math.sin(angle) * 1.5
            u_hp = int(ud.get('hp', 100))
            u = Entity(
                id=None,
                type_id=utype,
                owner=slot,
                pos=[ux, uy],
                hp=u_hp,
                max_hp=u_hp,
                speed=float(ud.get('speed', 2.0)),
                vision=float(ud.get('vision', 8)),
                radius=float(ud.get('radius', 0.4)),
                attack=int(ud.get('attack', 0)),
                attack_speed=float(ud.get('attack_speed', 1.0)),
                attack_type=str(ud.get('attack_type', 'normal')),
                attack_range=float(ud.get('range', 0.0)),
                armor=int(ud.get('armor', 0)),
                armor_type=str(ud.get('armor_type', 'light')),
            )
            u.cost_food = int(food_map.get(utype, 0))
            world.spawn(u)

def _maybe_start(g):
    """Стартуем, только когда все слоты заняты И все выбрали расу."""
    if g['phase'] != 'lobby':
        return
    if not all(g['occupied']):
        return

    pr = g.get('player_races') or [None] * g['num_players']
    for i in range(g['num_players']):
        if g['occupied'][i] and not pr[i]:
            return        # кто-то ещё не выбрал расу

    chosen = pr
    races = [
        chosen[i] if (i < len(chosen) and chosen[i] in _VALID_RACES)
        else _RACES_BY_SLOT[i % len(_RACES_BY_SLOT)]
        for i in range(g['num_players'])
    ]
    g['races'] = races

    data = _ensure_data()
    rules = data['rules']
    units = data['units']
    buildings = data['buildings']

    grid = None
    try:
        grid = Grid.from_map(data['map'])
    except Exception as e:
        print('[rts:%s] grid build failed: %r' % (g['id'], e))

    world = World(rules, units)
    world.buildings_data = buildings
    world.abilities_data = data.get('abilities') or {}
    world.grid = grid
    try:
        world.load_resource_nodes(data['map'])
    except Exception as e:
        print('[rts:%s] resource nodes load failed: %r' % (g['id'], e))

    runtime = GameRuntime(rules, units, buildings_data=buildings,
                          grid=grid, world=world)

    for i in range(g['num_players']):
        if g['occupied'][i]:
            runtime.world.resources[i] = {
                'gold': int(rules.get('starting_gold', 500)),
                'wood': int(rules.get('starting_wood', 200)),
                'food_used': 0,
                'food_cap': 10,
            }

    try:
        _spawn_starting_units(runtime, g, data)
    except Exception as e:
        print('[rts:%s] spawn starting units failed: %r' % (g['id'], e))

    runtime.set_alive_check(lambda gg=g: bool(heartbeat.alive_tokens(gg)))

    g['world'] = world
    g['runtime'] = runtime
    g['phase'] = 'battle'
    g['started_at'] = time.time()

    for i, occ in enumerate(g['occupied']):
        if occ:
            try:
                heartbeat.touch(g, i)
            except Exception:
                pass

    th = threading.Thread(
        target=_tick_loop, args=(g,),
        name='rts-%s' % g['id'], daemon=True,
    )
    g['thread'] = th
    th.start()


def _check_victory(g):
    """Проверяет, жив ли у каждого занятого игрока главный бункер.
    Возвращает None (бой продолжается) или dict {winners, losers}."""
    world = g['world']
    if world is None:
        return None
    has_main = {}
    for e in world.entities.values():
        if not getattr(e, 'is_building', False):
            continue
        bd = world.buildings_data.get(e.type_id) or {}
        if bd.get('is_main'):
            has_main[e.owner] = True
    winners = []
    losers = []
    for slot in range(g['num_players']):
        if not g['occupied'][slot]:
            continue
        if has_main.get(slot):
            winners.append(slot)
        else:
            losers.append(slot)
    if not losers:
        return None
    if len(winners) <= 1:
        return {'winners': winners, 'losers': losers}
    return None


def _tick_loop(g):
    """Отдельный daemon-thread. Уважает heartbeat-паузу и таймер матча."""
    rt = g['runtime']
    if rt is None:
        return
    interval = 1.0 / max(1, int(rt.tick_rate))
    max_t = float(rt.rules.get('match_time_limit_sec', 1500))

    while g['phase'] == 'battle':
        try:
            alive = heartbeat.alive_tokens(g)
        except Exception:
            alive = [t for t in g['tokens'] if t is not None]
        if not alive:
            time.sleep(0.2)
            continue

        if (g['started_at'] is not None
                and time.time() - g['started_at'] > max_t):
            with LOCK:
                g['phase'] = 'over'
                try:
                    _broadcast(g)
                except Exception:
                    pass
            break

        t0 = time.perf_counter()
        with LOCK:
            try:
                rt.tick()
            except Exception as e:
                print('[rts:%s] tick error: %r' % (g['id'], e))
            result = _check_victory(g)
            if result is not None:
                g['phase'] = 'over'
                g['result'] = result
            try:
                _broadcast(g)
            except Exception as e:
                print('[rts:%s] broadcast error: %r' % (g['id'], e))
        elapsed = time.perf_counter() - t0
        time.sleep(max(0.0, interval - elapsed))


def _build_state(g, pid):
    """Полный снапшот для игрока. Не лезет в world, пока его нет."""
    base = {
        'game_id': g['id'],
        'num_players': g['num_players'],
        'names': list(g['names']),
        'occupied': list(g['occupied']),
        'phase': g['phase'],
        'slot': pid,
        'player_index': pid,
        'my_name': g['names'][pid] if 0 <= pid < g['num_players'] else '',
        'log': [],
    }
    base['player_races'] = list(g.get('player_races')
                                or [None] * g['num_players'])
    base['my_race'] = (
        g['races'][pid] if (g.get('races') and 0 <= pid < g['num_players'])
        else base['player_races'][pid] if 0 <= pid < g['num_players']
        else None
    )

    # --- lobby: только имена, никто не трогает world ---
    if g['phase'] == 'lobby':
        base['waiting'] = not all(g['occupied'])
        base['player_races'] = list(g.get('player_races')
                                    or [None] * g['num_players'])
        base['my_race'] = (
            g['player_races'][pid]
            if g.get('player_races') and 0 <= pid < g['num_players']
            else None
        )
        return base

    world = g['world']
    runtime = g['runtime']
    if world is None or runtime is None:
        base['waiting'] = False
        return base

    data = _ensure_data()
    base['abilities_data'] = data['abilities']


    resources = {}
    for slot, st in world.resources.items():
        resources[str(slot)] = {
            'gold': int(st.get('gold', 0)),
            'wood': int(st.get('wood', 0)),
            'food_used': int(st.get('food_used', 0)),
            'food_cap': int(st.get('food_cap', 0)),
        }

    entities = [e.to_dict() for e in world.entities.values()]

    projectiles = []
    for p in world.projectiles:
        projectiles.append({
            'pos': [p.pos[0], p.pos[1]],
            'target_id': p.target_id,
            'owner': p.owner,
            'attack_type': p.attack_type,
        })

    nodes = [n.to_dict() for n in world.resource_nodes.values()]

    base.update({
        'waiting': False,
        'tick': world.tick_count,
        'resources': resources,
        'entities': entities,
        'projectiles': projectiles,
        'resource_nodes': nodes,
        'map': data['map'],
        'units_data': data['units'],
        'buildings_data': data['buildings'],
        'result': g.get('result'),
    })
    return base


def _broadcast(g):
    """
    Пушит state каждому подключённому игроку в SSE-канал портала.
    Портал оборачивает начальный state сам (через build_state_for_token
    на hello), но дальше игру никто не пушит — это делает игра.

    Обёртку {'type':'state','state':...} формируем здесь.
    Вызывать под with LOCK — push_many берёт свой внутренний лок,
    рекурсии нет.
    """
    # лобби: маленький state, ничего из world не читаем
    if g['phase'] == 'lobby':
        items = []
        for i, tok in enumerate(g['tokens']):
            if tok is None:
                continue
            items.append((tok, {
                'type': 'state',
                'state': _build_state(g, i),
            }))
        if items:
            events_bus.push_many(g['id'], items)
        return

    # battle / over: собираем общий кусок один раз
    world = g['world']
    if world is None:
        return

    data = _ensure_data()

    resources = {}
    for slot, st in world.resources.items():
        resources[str(slot)] = {
            'gold': int(st.get('gold', 0)),
            'wood': int(st.get('wood', 0)),
            'food_used': int(st.get('food_used', 0)),
            'food_cap': int(st.get('food_cap', 0)),
        }

    entities = [e.to_dict() for e in world.entities.values()]

    projectiles = []
    for p in world.projectiles:
        projectiles.append({
            'pos': [p.pos[0], p.pos[1]],
            'target_id': p.target_id,
            'owner': p.owner,
            'attack_type': p.attack_type,
        })

    nodes = [n.to_dict() for n in world.resource_nodes.values()]

    shared = {
        'tick': world.tick_count,
        'resources': resources,
        'entities': entities,
        'projectiles': projectiles,
        'resource_nodes': nodes,
        'map': data['map'],
        'units_data': data['units'],
        'buildings_data': data['buildings'],
        'abilities_data': data['abilities'],       # ← добавить
        'waiting': False,
        'log': [],
    }

    items = []
    for i, tok in enumerate(g['tokens']):
        if tok is None or not g['occupied'][i]:
            continue
        st = dict(shared)
        st.update({
            'game_id': g['id'],
            'num_players': g['num_players'],
            'names': list(g['names']),
            'occupied': list(g['occupied']),
            'phase': g['phase'],
            'slot': i,
            'player_index': i,
            'my_name': g['names'][i],
        })
        items.append((tok, {'type': 'state', 'state': st}))

    if items:
        events_bus.push_many(g['id'], items)

# --------------------------------------------------------------------
# GameServer + хуки
# --------------------------------------------------------------------
server = game_base.GameServer(
    prefix='RT',
    max_players=4,
    min_players=2,
    new_game=_new_game,
    attach_player=_attach_player,
    get_tokens=_get_tokens,
    drop_player=_drop_player,
    is_lobby=_is_lobby,
    build_state=_build_state,
    player_names=_player_names,
    phase_of=_phase_of,
    maybe_start=_maybe_start,
)

(GAMES, LOCK,
 create_game, join_game, leave, has_player,
 sweep, list_active, is_joinable, build_state_for_token,
 _current) = server.install()


# --------------------------------------------------------------------
# Роуты (внутри bp, только /rts/*)
# --------------------------------------------------------------------
def _ctx():
    """Унифицированный доступ к (game, slot) через _current()."""
    ctx = _current()
    if ctx is None:
        return None
    # обычный вариант: (g, pid)
    if isinstance(ctx, tuple) and len(ctx) == 2 and isinstance(ctx[0], dict):
        return ctx
    # защита на случай (code, token)
    if isinstance(ctx, tuple) and len(ctx) == 2:
        code, tok = ctx
        g = GAMES.get(code)
        if g is not None:
            for i, t in enumerate(g['tokens']):
                if t == tok:
                    return (g, i)
    return None


@bp.route('/api/state')
def api_state():
    ctx = _ctx()
    if ctx is None:
        return jsonify({'ok': False, 'error': 'no session'}), 400
    g, pid = ctx
    try:
        heartbeat.touch(g, pid)
    except Exception:
        pass
    return jsonify({'ok': True, 'state': _build_state(g, pid)})


@bp.route('/api/action', methods=['POST'])
def api_action():
    ctx = _ctx()
    if ctx is None:
        return jsonify({'ok': False, 'error': 'no session'}), 400
    g, pid = ctx
    payload = request.get_json(force=True, silent=True) or {}
    action = payload.get('action')

    # лобби: только set_race
    if g['phase'] == 'lobby':
        if action == 'set_race':
            race = payload.get('race')
            if race in _VALID_RACES:
                if 'player_races' not in g or g['player_races'] is None:
                    g['player_races'] = [None] * g['num_players']
                g['player_races'][pid] = race
                with LOCK:
                    try:
                        _maybe_start(g)
                    except Exception as e:
                        print('[rts:%s] maybe_start error: %r' % (g['id'], e))
                    try:
                        _broadcast(g)
                    except Exception:
                        pass
        return jsonify({'ok': True, 'state': _build_state(g, pid)})

    if g['phase'] != 'battle' or g['runtime'] is None:
        return jsonify({'ok': False, 'error': 'not in battle'}), 400

    g['runtime'].enqueue_command(pid, payload)
    try:
        heartbeat.touch(g, pid)
    except Exception:
        pass
    with LOCK:
        try:
            _broadcast(g)
        except Exception:
            pass
    return jsonify({'ok': True, 'state': _build_state(g, pid)})


@bp.route('/api/reset', methods=['POST'])
def api_reset():
    ctx = _ctx()
    if ctx is None:
        return jsonify({'ok': False, 'error': 'no session'}), 400
    g, pid = ctx
    try:
        if heartbeat.alive_tokens(g):
            return jsonify({'ok': False, 'error': 'players online'}), 409
    except Exception:
        pass
    g['phase'] = 'lobby'
    g['world'] = None
    g['runtime'] = None
    return jsonify({'ok': True})


# --------------------------------------------------------------------
# Совместимость: экспорт публичных имён модуля
# --------------------------------------------------------------------
__all__ = [
    'GAME_META', 'bp',
    'GAMES', 'LOCK',
    'create_game', 'join_game', 'leave', 'has_player',
    'sweep', 'list_active', 'is_joinable', 'build_state_for_token',
    '_current',
]
