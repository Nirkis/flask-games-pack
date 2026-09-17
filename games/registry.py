# -*- coding: utf-8 -*-
"""Автообнаружение игровых модулей + агрегация активных партий."""
import importlib
import logging
import os
import pkgutil
import threading
import time

_REGISTRY = {}
_loaded = False
_lock = threading.RLock()

REQUIRED_HOOKS = ('create_game', 'join_game', 'leave', 'has_player')


def register(meta, module):
    _REGISTRY[meta['code']] = {'meta': meta, 'module': module}


def autodiscover():
    global _loaded
    if _loaded:
        return
    _loaded = True
    here = os.path.dirname(__file__)
    for _, name, is_pkg in pkgutil.iter_modules([here]):
        if is_pkg or name.startswith('_') or name == 'registry':
            continue
        try:
            mod = importlib.import_module('games.%s' % name)
        except Exception as e:
            logging.warning('games/%s.py не импортируется: %s', name, e)
            continue
        meta = getattr(mod, 'GAME_META', None)
        bp = getattr(mod, 'bp', None)
        if not meta or bp is None:
            continue
        missing = [h for h in REQUIRED_HOOKS if not callable(getattr(mod, h, None))]
        if missing:
            logging.warning('games/%s.py: нет хуков %s', name, missing)
            continue
        register(meta, mod)
        logging.info('Зарегистрирована игра: %s (%s)', meta.get('name', name), meta['code'])


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


def snapshot_active():
    """Список всех активных партий во всех играх. Для панели на лендинге."""
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
                'occupied': int(g.get('occupied', len(g.get('players', [])))),
                'max_players': int(g.get('max_players', 2)),
                'started': bool(g.get('started', False)),
                'age_sec': int(max(0, now - created)),
            })
    # Сначала идущие, потом свежие лобби
    out.sort(key=lambda x: (not x['started'], x['age_sec']))
    return out


def sweep_games():
    """Один тик уборки: TTL, если игра экспортирует cleanup()."""
    for entry in all_games():
        fn = getattr(entry['module'], 'cleanup', None)
        if callable(fn):
            try:
                fn()
            except Exception:
                pass
