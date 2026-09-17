# -*- coding: utf-8 -*-
"""Flask-Games-pack — единый портал с автообнаружением игр."""
import inspect
import logging
import os
import threading
import time

from flask import Flask, jsonify, render_template, request, session, redirect

from games import registry
from games import events as events_bus


# ---------- Логирование ----------
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(name)s: %(message)s')


# ---------- Секрет ----------
_secret = os.environ.get('PORTAL_SECRET')
if not _secret:
    if os.environ.get('PORTAL_DEV') == '1':
        _secret = 'dev-insecure-do-not-use-in-prod'
        logging.warning('PORTAL_DEV=1 — используется небезопасный секрет. '
                        'Никогда не запускайте так в продакшене.')
    else:
        raise SystemExit(
            'PORTAL_SECRET не задан.\n'
            '  Прод:     export PORTAL_SECRET="$(python -c \'import secrets;print(secrets.token_urlsafe(32))\')"\n'
            '  Локально: export PORTAL_DEV=1'
        )


app = Flask(__name__)
app.secret_key = _secret

try:
    app.json.ensure_ascii = False
except AttributeError:
    app.config['JSON_AS_ASCII'] = False

app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True


# ---------- Игры ----------
registry.autodiscover()
registry.register_blueprints(app)


# ---------- Фоновый sweeper ----------
def _sweeper_loop():
    while True:
        time.sleep(60)
        try:
            events_bus.sweep_dead()
            registry.sweep_games()
        except Exception:
            pass


_sweeper_started = False
def _ensure_sweeper():
    global _sweeper_started
    if _sweeper_started:
        return
    _sweeper_started = True
    threading.Thread(target=_sweeper_loop, daemon=True, name='portal_sweep').start()


# ---------- Хелперы ----------
def _safe_call(fn, *args, **kwargs):
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


# ---------- Страницы ----------
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


# ---------- API ----------
@app.route('/api/games')
def api_games():
    out = []
    for e in registry.all_games():
        m = e['meta']
        out.append({
            'code': m['code'],
            'prefix': m.get('prefix', m['code'].upper()),
            'name': m['name'],
            'players': m.get('players', '2'),
            'modes': m.get('modes', []),
            'realtime': bool(m.get('realtime')),
        })
    return jsonify({'ok': True, 'games': out})


@app.route('/api/active_games')
def api_active_games():
    """Публичный список запущенных партий."""
    return jsonify({'ok': True, 'games': registry.snapshot_active()})


@app.route('/api/create', methods=['POST'])
def api_create():
    _ensure_sweeper()
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
    _ensure_sweeper()
    data = request.get_json(silent=True) or {}
    code = (data.get('code') or '').strip().upper()
    name = (data.get('name') or '').strip()[:16] or 'Игрок'

    if '-' not in code:
        return jsonify({'ok': False, 'error': 'Неверный формат кода (XX-XXXX)'}), 400

    prefix = code.split('-', 1)[0]
    entry = registry.get_by_prefix(prefix)
    if entry is None:
        return jsonify({'ok': False, 'error': 'Неизвестный тип игры'}), 400

    try:
        token, err = _safe_call(entry['module'].join_game, code, name)
    except Exception as e:
        return jsonify({'ok': False, 'error': 'Ошибка подключения: %s' % e}), 500

    if err:
        return jsonify({'ok': False, 'error': err}), 400

    _set_session(entry['meta']['code'], code, token, name)
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
    if code and tok:
        events_bus.drop_player(code, tok)
    session.clear()
    return jsonify({'ok': True})


# ---------- Точка входа ----------
if __name__ == '__main__':
    _ensure_sweeper()
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
