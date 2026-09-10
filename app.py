# -*- coding: utf-8 -*-
"""
Единый игровой портал: HearthLite, Морской бой, Дурак.
"""
import os
import random

from flask import Flask, jsonify, render_template, request, session, redirect

from games import hearthlite as hl
from games import battleship as bs
from games import durak as dk


app = Flask(__name__)
app.secret_key = os.environ.get('PORTAL_SECRET', 'portal-dev-secret-change-me')

try:
    app.json.ensure_ascii = False
except AttributeError:
    app.config['JSON_AS_ASCII'] = False

app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True

# === ЭТОГО НЕ ХВАТАЛО ===
app.register_blueprint(hl.bp)   # /hl/api/...
app.register_blueprint(bs.bp)   # /bs/api/...
app.register_blueprint(dk.bp)   # /dk/api/...
# ========================

def _set_session(game_type, code, token, name):
    session['game_type'] = game_type
    session['code'] = code
    session['player_token'] = token
    session['name'] = name


@app.route('/')
def index():
    return render_template('index.html')


def _session_valid(gt, code, tok):
    if not gt or not code or not tok:
        return False
    if gt == 'hl':
        return hl.has_player(code, tok)
    if gt == 'bs':
        return bs.has_player(code, tok)
    if gt == 'dk':
        return dk.has_player(code, tok)
    return False


@app.route('/play')
def play():
    gt = session.get('game_type')
    code = session.get('code')
    tok = session.get('player_token')
    if not _session_valid(gt, code, tok):
        session.clear()
        return redirect('/')
    if gt == 'hl':
        return render_template('hearthlite.html')
    if gt == 'bs':
        return render_template('battleship.html')
    if gt == 'dk':
        return render_template('durak.html')
    return redirect('/')


@app.route('/api/create', methods=['POST'])
def api_create():
    data = request.get_json(silent=True) or {}
    game_type = (data.get('game_type') or '').lower()
    name = (data.get('name') or '').strip()[:16] or 'Игрок'
    try:
        mode = int(data.get('mode') or 2)
    except (TypeError, ValueError):
        mode = 2

    if game_type == 'hl':
        code, token, err = hl.create_game(name)
    elif game_type == 'bs':
        code, token, err = bs.create_game(name)
    elif game_type == 'dk':
        code, token, err = dk.create_game(name, mode)
    else:
        return jsonify({'ok': False, 'error': 'Неизвестный тип игры'}), 400

    if err:
        return jsonify({'ok': False, 'error': err}), 400

    _set_session(game_type, code, token, name)
    return jsonify({'ok': True, 'code': code, 'redirect': '/play'})


@app.route('/api/join', methods=['POST'])
def api_join():
    data = request.get_json(silent=True) or {}
    code = (data.get('code') or '').strip().upper()
    name = (data.get('name') or '').strip()[:16] or 'Игрок'

    if '-' not in code:
        return jsonify({'ok': False, 'error': 'Неверный формат кода (нужно XX-XXXX)'}), 400

    prefix = code.split('-', 1)[0]
    if prefix == 'HL':
        game_type, token, err = 'hl', *hl.join_game(code, name)
    elif prefix == 'BS':
        game_type, token, err = 'bs', *bs.join_game(code, name)
    elif prefix == 'DK':
        game_type, token, err = 'dk', *dk.join_game(code, name)
    else:
        return jsonify({'ok': False, 'error': 'Неизвестный тип игры'}), 400

    if err:
        return jsonify({'ok': False, 'error': err}), 400

    _set_session(game_type, code, token, name)
    return jsonify({'ok': True, 'code': code, 'redirect': '/play'})


@app.route('/api/whoami')
def api_whoami():
    gt = session.get('game_type')
    code = session.get('code')
    tok = session.get('player_token')
    if not _session_valid(gt, code, tok):
        session.clear()
        return jsonify({'game_type': None})
    return jsonify({
        'game_type': gt,
        'code': code,
        'name': session.get('name'),
    })


@app.route('/api/leave', methods=['POST'])
def api_leave():
    gt = session.get('game_type')
    code = session.get('code')
    tok = session.get('player_token')
    if gt == 'hl':
        hl.leave(code, tok)
    elif gt == 'bs':
        bs.leave(code, tok)
    elif gt == 'dk':
        dk.leave(code, tok)
    session.clear()
    return jsonify({'ok': True})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
