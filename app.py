# -*- coding: utf-8 -*-
"""Единый игровой портал с автообнаружением игр."""
import inspect
import os

from flask import Flask, jsonify, render_template, request, session, redirect

from games import registry


app = Flask(__name__)
app.secret_key = os.environ.get('PORTAL_SECRET', 'portal-dev-secret-change-me')

try:
    app.json.ensure_ascii = False
except AttributeError:
    app.config['JSON_AS_ASCII'] = False

app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True

# === вот и вся интеграция ===
registry.autodiscover()
registry.register_blueprints(app)
# ============================


def _safe_call(fn, *args, **kwargs):
    """Вызывает fn, отбрасывая kwargs, которых нет в её сигнатуре."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return fn(*args, **kwargs)
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return fn(*args, **kwargs)
    filtered = {k: v for k, v in kwargs.items() if k in params}
    return fn(*args, **filtered)


def _entry_for_session():
    gt = session.get('game_type')
    code = session.get('code')
    tok = session.get('player_token')
    if not gt or not code or not tok:
        return None
    entry = registry.get(gt)
    if entry is None:
        return None
    has = getattr(entry['module'], 'has_player', None)
    if has is None:
        return entry
    try:
        ok = has(code, tok)
    except Exception:
        ok = False
    return entry if ok else None


def _set_session(gt, code, token, name):
    session['game_type'] = gt
    session['code'] = code
    session['player_token'] = token
    session['name'] = name


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/play')
def play():
    entry = _entry_for_session()
    if entry is None:
        session.clear()
        return redirect('/')
    return render_template(entry['meta']['template'])


@app.route('/api/games')
def api_games():
    """Список игр для лендинга."""
    out = []
    for e in registry.all_games():
        m = e['meta']
        out.append({
            'code': m['code'],
            'prefix': m.get('prefix', m['code'].upper()),
            'name': m['name'],
            'players': m.get('players', '2'),
            'modes': m.get('modes', []),
            'extra_options': m.get('extra_options', []),
        })
    return jsonify({'ok': True, 'games': out})


@app.route('/api/create', methods=['POST'])
def api_create():
    data = request.get_json(silent=True) or {}
    gt = (data.get('game_type') or '').lower()
    entry = registry.get(gt)
    if entry is None:
        return jsonify({'ok': False, 'error': 'Неизвестный тип игры'}), 400

    name = (data.get('name') or '').strip()[:16] or 'Игрок'
    m = entry['meta']
    opts = {}

    if m.get('modes'):
        try:
            mode = int(data.get('mode') or m['modes'][0])
        except (TypeError, ValueError):
            mode = m['modes'][0]
        if mode not in m['modes']:
            mode = m['modes'][0]
        opts['mode'] = mode

    for k in m.get('extra_options', []):
        if k in data and data[k] is not None:
            opts[k] = data[k]

    try:
        code, token, err = _safe_call(entry['module'].create_game, name, **opts)
    except Exception as e:
        return jsonify({'ok': False, 'error': 'Ошибка создания: %s' % e}), 500

    if err:
        return jsonify({'ok': False, 'error': err}), 400

    _set_session(gt, code, token, name)
    return jsonify({'ok': True, 'code': code, 'redirect': '/play'})


@app.route('/api/join', methods=['POST'])
def api_join():
    data = request.get_json(silent=True) or {}
    code = (data.get('code') or '').strip().upper()
    name = (data.get('name') or '').strip()[:16] or 'Игрок'

    if '-' not in code:
        return jsonify({'ok': False, 'error': 'Неверный формат кода (XX-XXXX)'}), 400

    prefix = code.split('-', 1)[0]
    entry = registry.get_by_prefix(prefix)
    if entry is None:
        return jsonify({'ok': False, 'error': 'Неизвестный тип игры'}), 400

    m = entry['meta']
    opts = {}
    for k in m.get('extra_options', []):
        if k in data and data[k] is not None:
            opts[k] = data[k]

    try:
        token, err = _safe_call(entry['module'].join_game, code, name, **opts)
    except Exception as e:
        return jsonify({'ok': False, 'error': 'Ошибка подключения: %s' % e}), 500

    if err:
        return jsonify({'ok': False, 'error': err}), 400

    _set_session(m['code'], code, token, name)
    return jsonify({'ok': True, 'code': code, 'redirect': '/play'})


@app.route('/api/whoami')
def api_whoami():
    entry = _entry_for_session()
    if entry is None:
        session.clear()
        return jsonify({'game_type': None})
    return jsonify({
        'game_type': session.get('game_type'),
        'code': session.get('code'),
        'name': session.get('name'),
    })


@app.route('/api/leave', methods=['POST'])
def api_leave():
    gt = session.get('game_type')
    code = session.get('code')
    tok = session.get('player_token')
    entry = registry.get(gt) if gt else None
    if entry is not None:
        leave_fn = getattr(entry['module'], 'leave', None)
        if leave_fn is not None:
            try:
                leave_fn(code, tok)
            except Exception:
                pass
    session.clear()
    return jsonify({'ok': True})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
