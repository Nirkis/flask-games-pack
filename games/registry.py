# -*- coding: utf-8 -*-
"""Автообнаружение игровых модулей.

Каждый модуль в games/ может объявить:
    GAME_META = {...}   — манифест
    bp = Blueprint(...) — Flask-блюпринт

Всё остальное подхватывается автоматически.
"""
import importlib
import os
import pkgutil

_REGISTRY = {}
_loaded = False


def register(meta, module):
    code = meta['code']
    _REGISTRY[code] = {'meta': meta, 'module': module}


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
            print('⚠ games/%s.py не загрузился: %s' % (name, e))
            continue
        meta = getattr(mod, 'GAME_META', None)
        bp = getattr(mod, 'bp', None)
        if not meta or bp is None:
            continue
        register(meta, mod)


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
