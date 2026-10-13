# -*- coding: utf-8 -*-
"""Загрузка статических данных игры из её собственного data/.

Вызывается из games/<game>/__init__.py:
    from games.data_loader import load
    CARDS = load('cards', schema={...})
"""
import importlib.util
import json
import os
import sys
import threading

_LOCK = threading.RLock()
_CACHE = {}


def _caller_dir():
    frame = sys._getframe(2)
    caller_file = frame.f_globals.get('__file__')
    if not caller_file:
        raise RuntimeError('load() вызвана не из модуля игры')
    return os.path.dirname(os.path.abspath(caller_file))


def _load_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _load_py(path, key):
    mod_name = 'games_data_' + key.replace('/', '_').replace('.', '_')
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    data = getattr(mod, 'DATA', None)
    if data is None:
        raise ValueError('%s: ожидается переменная DATA' % path)
    return data


def _validate(data, schema, path='root'):
    """Минималистичная проверка. Схема:
       - dict со '*'  — wildcard, применяется к каждому значению
       - dict с ключами — проверка на наличие ключей и рекурсия
       - список        — значение должно быть одним из элементов
       - type          — isinstance проверка
    """
    errors = []
    if isinstance(schema, dict):
        if set(schema.keys()) == {'*'}:
            if not isinstance(data, dict):
                errors.append('%s: ожидался словарь' % path)
                return errors
            for k, v in data.items():
                errors.extend(_validate(v, schema['*'], '%s[%s]' % (path, k)))
        else:
            if not isinstance(data, dict):
                errors.append('%s: ожидался словарь' % path)
                return errors
            for key, sub in schema.items():
                if key not in data:
                    errors.append('%s.%s: отсутствует' % (path, key))
                    continue
                errors.extend(_validate(data[key], sub, '%s.%s' % (path, key)))
    elif isinstance(schema, list):
        if data not in schema:
            errors.append('%s: %r не из %r' % (path, data, schema))
    elif isinstance(schema, type):
        if not isinstance(data, schema):
            errors.append('%s: %r не %s' % (path, data, schema.__name__))
    return errors


def load(name, required=None, schema=None):
    game_dir = _caller_dir()
    key = os.path.abspath(os.path.join(game_dir, name))

    with _LOCK:
        if key in _CACHE:
            data = _CACHE[key]
        else:
            json_path = os.path.join(game_dir, 'data', name + '.json')
            py_path = os.path.join(game_dir, 'data', name + '.py')
            if os.path.exists(json_path):
                data = _load_json(json_path)
            elif os.path.exists(py_path):
                data = _load_py(py_path, key)
            else:
                raise FileNotFoundError(
                    'Не найден %s/data/%s.json или .py' % (game_dir, name))
            _CACHE[key] = data

    if required:
        if not isinstance(data, dict):
            raise ValueError('data/%s: ожидался словарь' % name)
        missing = [k for k in required if k not in data]
        if missing:
            raise ValueError('data/%s: нет ключей %s' % (name, missing))

    if schema:
        errs = _validate(data, schema, 'data/%s' % name)
        if errs:
            raise ValueError('Схема %s нарушена:\n  - %s'
                             % (name, '\n  - '.join(errs)))

    return data


def reload(name=None):
    with _LOCK:
        if name is None:
            _CACHE.clear()
        else:
            for k in list(_CACHE.keys()):
                if name in k:
                    _CACHE.pop(k, None)


def validate_all():
    """Прогоняет load() по всем data-файлам всех зарегистрированных игр."""
    from games import registry
    count = 0
    for entry in registry.all_games():
        game_dir = os.path.dirname(entry['module'].__file__)
        data_dir = os.path.join(game_dir, 'data')
        if not os.path.isdir(data_dir):
            continue
        for fn in os.listdir(data_dir):
            base, ext = os.path.splitext(fn)
            if ext not in ('.json', '.py') or base.startswith('_'):
                continue
            json_path = os.path.join(data_dir, base + '.json')
            py_path = os.path.join(data_dir, base + '.py')
            key = os.path.abspath(os.path.join(game_dir, base))
            if key not in _CACHE:
                if os.path.exists(json_path):
                    _CACHE[key] = _load_json(json_path)
                elif os.path.exists(py_path):
                    _CACHE[key] = _load_py(py_path, key)
            count += 1
    return count
