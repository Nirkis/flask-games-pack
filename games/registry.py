# -*- coding: utf-8 -*-
"""Автообнаружение игр-пакетов и агрегация активных партий."""
import importlib
import logging
import os
import pkgutil
import time

_REGISTRY = {}
_loaded = False

REQUIRED_HOOKS = ('create_game', 'join_game', 'leave', 'has_player')
INFRA = {'registry', 'events', 'data_loader', 'heartbeat', 'game_base'}


def register(meta, module):
    _REGISTRY[meta['code']] = {'meta': meta, 'module': module}


def _include_examples():
    return os.environ.get('PORTAL_EXAMPLES') == '1'


def autodiscover():
    global _loaded
    if _loaded:
        return
    _loaded = True

    here = os.path.dirname(__file__)
    for _, name, is_pkg in pkgutil.iter_modules([here]):
        if not is_pkg:
            continue
        if name in INFRA:
            continue
        if name.startswith('_') and not _include_examples():
            continue
        try:
            mod = importlib.import_module('games.%s' % name)
        except Exception as e:
            logging.warning('games/%s: не импортируется: %s', name, e)
            continue

        meta = getattr(mod, 'GAME_META', None)
        bp = getattr(mod, 'bp', None)
        if not meta or bp is None:
            logging.warning('games/%s: нет GAME_META или bp', name)
            continue

        missing = [h for h in REQUIRED_HOOKS
                   if not callable(getattr(mod, h, None))]
        if missing:
            logging.warning('games/%s: нет хуков %s', name, missing)
            continue

        register(meta, mod)
        logging.info('Игра зарегистрирована: %s (%s)',
                     meta.get('name', name), meta['code'])


def all_games():
    items = list(_REGISTRY.values())
    items.sort(key=lambda x: x['meta'].get('order', 100))
    return items


def get(code):
    return _REGISTRY.get(code)


def get_by_prefix(prefix):
    prefix = (prefix or '').upper()
    for code, entry in _REGISTRY.items():
        if entry['meta'].get('prefix', code.upper()) == prefix:
            return entry
    return None


def register_blueprints(app):
    for entry in _REGISTRY.values():
        app.register_blueprint(entry['module'].bp)


def sweep_games(now=None):
    now = now or time.time()
    for entry in all_games():
        fn = getattr(entry['module'], 'sweep', None)
        if callable(fn):
            try:
                fn(now)
            except Exception:
                logging.exception('sweep failed in %s', entry['meta']['code'])


def snapshot_active():
    out = []
    now = time.time()
    for entry in all_games():
        fn = getattr(entry['module'], 'list_active', None)
        if not callable(fn):
            continue
        try:
            games = fn() or []
        except Exception:
            continue
        m = entry['meta']
        for g in games:
            created = g.get('created') or now
            out.append({
                'game_code': m['code'],
                'game_name': m['name'],
                'prefix': m.get('prefix', m['code'].upper()),
                'id': g.get('id', '?'),
                'phase': g.get('phase', 'unknown'),
                'players': list(g.get('players', [])),
                'active': int(g.get('active', 0)),
                'occupied': int(g.get('occupied', len(g.get('players', [])))),
                'max_players': int(g.get('max_players', 2)),
                'started': bool(g.get('started', False)),
                'age_sec': int(max(0, now - created)),
                'joinable': bool(g.get('joinable', False)),
            })
    out.sort(key=lambda x: (not x['started'], x['age_sec']))
    return out
