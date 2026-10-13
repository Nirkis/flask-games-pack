# -*- coding: utf-8 -*-
"""Flask-Games-pack — портал с автообнаружением игр-пакетов."""
import inspect
import logging
import os
import threading
import time

from flask import (Flask, Response, jsonify, redirect, render_template,
                   request, session, stream_with_context)

from games import events as events_bus
from games import heartbeat
from games import registry


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s')


# ---------- Секрет ----------
_secret = os.environ.get('PORTAL_SECRET')
if not _secret:
    if os.environ.get('PORTAL_DEV') == '1':
        _secret = 'dev-insecure-do-not-use-in-prod'
        logging.warning('PORTAL_DEV=1 — небезопасный секрет, только для разработки')
    else:
        raise SystemExit(
            'PORTAL_SECRET не задан.\n'
            '  Прод:     export PORTAL_SECRET="$(python -c \'import secrets;print(secrets.token_urlsafe(32))\')"\n'
            '  Локально: PORTAL_DEV=1 python app.py')


app = Flask(__name__)
app.secret_key = _secret

try:
    app.json.ensure_ascii = False
except AttributeError:
    app.config['JSON_AS_ASCII'] = False

app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True


# ---------- Регистрация игр ----------
registry.autodiscover()
registry.register_blueprints(app)


# ---------- Sweeper ----------
_sweeper_started = False


def _sweeper_loop():
    while True:
        time.sleep(heartbeat.SWEEP_INTERVAL)
        try:
            events_bus.sweep_dead()
            registry.sweep_games()
        except Exception:
            logging.exception('sweeper')


def _ensure_sweeper():
    global _sweeper_started
    if _sweeper_started:
        return
    _sweeper_started = True
    threading.Thread(target=_sweeper_loop, daemon=True, name='sweep').start()


# ---------- Хелперы ----------
def _safe_call(fn, *args, **kwargs):
    """Вызвать fn, отфильтровав kwargs, которых нет в её сигнатуре."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return fn(*args, **kwargs)
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return fn(*args, **kwargs)
    return fn(*args, **{k: v for k, v in kwargs.items() if k in params})


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
        return entry if has(code, tok) else None
    except Exception:
        return None


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
        logging.exception('create failed')
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
        return jsonify({'ok': False, 'error': 'Формат кода: XX-XXXX'}), 400

    entry = registry.get_by_prefix(code.split('-', 1)[0])
    if entry is None:
        return jsonify({'ok': False, 'error': 'Неизвестный тип игры'}), 400

    try:
        token, err = _safe_call(entry['module'].join_game, code, name)
    except Exception as e:
        logging.exception('join failed')
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


@app.route('/api/rejoin', methods=['POST'])
def api_rejoin():
    """Публичная проверка партии по коду (без session)."""
    data = request.get_json(silent=True) or {}
    code = (data.get('code') or '').strip().upper()
    if '-' not in code:
        return jsonify({'ok': False, 'error': 'Формат кода: XX-XXXX'}), 400
    entry = registry.get_by_prefix(code.split('-', 1)[0])
    if entry is None:
        return jsonify({'ok': False, 'error': 'Игра не найдена'}), 404

    fn = getattr(entry['module'], 'is_joinable', None)
    info = None
    if callable(fn):
        try:
            info = fn(code)
        except Exception:
            info = None
    if info is None:
        return jsonify({'ok': False, 'error': 'Игра не найдена или удалена'}), 404
    return jsonify({'ok': True, 'game': info})


@app.route('/api/leave', methods=['POST'])
def api_leave():
    gt = session.get('game_type')
    code = session.get('code')
    tok = session.get('player_token')
    entry = registry.get(gt) if gt else None
    if entry is not None:
        leave_fn = getattr(entry['module'], 'leave', None)
        if callable(leave_fn):
            try:
                leave_fn(code, tok)
            except Exception:
                logging.exception('leave failed')
    if code and tok:
        events_bus.drop_player(code, tok)
    session.clear()
    return jsonify({'ok': True})

# Сколько секунд SSE-стрим ждёт события, прежде чем отправить keepalive.
# Меньше PLAYER_TIMEOUT (45 c), иначе sweep успеет пометить игрока offline.
_SSE_WAIT = 10


@app.route('/api/events')
def api_events():
    """SSE-канал портала. Один для всех игр.

    Каждые _SSE_WAIT секунд (или раньше, если пришло событие) обновляет
    heartbeat игрока, чтобы sweeper не посчитал его мёртвым.
    """
    gt = session.get('game_type')
    code = session.get('code')
    tok = session.get('player_token')
    entry = registry.get(gt) if gt else None
    if entry is None or not code or not tok:
        return ('', 401)

    module = entry['module']
    has = getattr(module, 'has_player', None)
    if callable(has):
        try:
            if not has(code, tok):
                return ('', 401)
        except Exception:
            return ('', 401)

    game_games = getattr(module, 'GAMES', None)
    game_lock = getattr(module, 'LOCK', None)

    def _touch():
        """Обновить метку жизни игрока. Безопасно вызывать с любым состоянием."""
        if game_games is None or game_lock is None:
            return
        try:
            with game_lock:
                g = game_games.get(code)
                if g is not None:
                    heartbeat.touch(g, tok)
        except Exception:
            pass

    sub = events_bus.subscribe(code, tok)

    @stream_with_context
    def _gen():
        try:
            _touch()
            yield 'event: hello\ndata: {}\n\n'

            build = getattr(module, 'build_state_for_token', None)
            if callable(build):
                try:
                    st = build(code, tok)
                except Exception:
                    st = None
                if st is not None:
                    yield events_bus.to_sse({'type': 'state', 'state': st})

            while True:
                _touch()
                payload, status = sub.wait_next(_SSE_WAIT)
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


if __name__ == '__main__':
    _ensure_sweeper()
    app.run(host='0.0.0.0', port=5001, debug=False, threaded=True)
