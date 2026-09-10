# -*- coding: utf-8 -*-
"""HearthLite — упрощённая Hearthstone на 2-3 игрока (многокомнатная)."""
import random
import threading
import uuid

from flask import Blueprint, jsonify, request, session

bp = Blueprint('hearthlite', __name__, url_prefix='/hl')

MIN_PLAYERS = 2
MAX_PLAYERS = 3
START_HP = 30
MAX_MANA = 10
HAND_LIMIT = 10
BOARD_LIMIT = 7

CARDS = {
    "murloc":      {"name": "Мурлок",   "type": "minion", "cost": 1, "attack": 1, "health": 2, "taunt": False, "emoji": "🐟", "target": "none", "text": ""},
    "skeleton":    {"name": "Скелет",   "type": "minion", "cost": 1, "attack": 2, "health": 1, "taunt": False, "emoji": "💀", "target": "none", "text": ""},
    "wolf":        {"name": "Лютый волк","type":"minion", "cost": 2, "attack": 3, "health": 2, "taunt": False, "emoji": "🐺", "target": "none", "text": ""},
    "guard":       {"name": "Стражник", "type": "minion", "cost": 2, "attack": 1, "health": 4, "taunt": True,  "emoji": "🛡️", "target": "none", "text": "Провокация"},
    "goblin":      {"name": "Гоблин",   "type": "minion", "cost": 3, "attack": 3, "health": 3, "taunt": False, "emoji": "👺", "target": "none", "text": ""},
    "ogre":        {"name": "Огр",      "type": "minion", "cost": 4, "attack": 4, "health": 4, "taunt": False, "emoji": "👹", "target": "none", "text": ""},
    "stone_guard": {"name": "Каменный страж","type":"minion","cost":5,"attack": 3, "health": 8, "taunt": True,  "emoji": "🗿", "target": "none", "text": "Провокация"},
    "dragon":      {"name": "Дракон",   "type": "minion", "cost": 7, "attack": 7, "health": 7, "taunt": False, "emoji": "🐉", "target": "none", "text": ""},
    "lightning":   {"name": "Молния",   "type": "spell",  "cost": 1, "target": "enemy", "emoji": "⚡", "text": "2 урона выбранной цели", "damage": 2},
    "fireball":    {"name": "Огненный шар","type":"spell", "cost": 4, "target": "enemy", "emoji": "🔥", "text": "5 урона выбранной цели", "damage": 5},
    "heal":        {"name": "Исцеление", "type": "spell", "cost": 2, "target": "friendly", "emoji": "💚", "text": "+6 здоровья", "heal": 6},
    "wisdom":      {"name": "Мудрость",  "type": "spell", "cost": 2, "target": "none", "emoji": "📖", "text": "Взять 2 карты", "draw": 2},
    "explosion":   {"name": "Взрыв",     "type": "spell", "cost": 4, "target": "none", "emoji": "💥", "text": "2 урона всем существам противников", "aoe": 2},
    "blessing":    {"name": "Благословение","type":"spell","cost": 2, "target": "friendly_minion", "emoji": "✨", "text": "Существо получает +2/+2", "buff": [2, 2]},
}

DECK_TEMPLATE = (
    ["murloc"] * 2 + ["skeleton"] * 2 + ["wolf"] * 2 + ["guard"] * 2 +
    ["goblin"] * 2 + ["ogre"] * 2 + ["stone_guard"] + ["dragon"] +
    ["lightning"] * 2 + ["fireball"] * 2 + ["heal"] * 2 + ["wisdom"] * 2 +
    ["explosion"] + ["blessing"] * 2
)

GAMES = {}
LOCK = threading.RLock()
CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'


def _gen_code():
    return 'HL-' + ''.join(random.choice(CODE_ALPHABET) for _ in range(4))


def build_deck():
    d = list(DECK_TEMPLATE)
    random.shuffle(d)
    return d


def find_minion(player, uid):
    if player is None or uid is None:
        return None
    for m in player.board:
        if m["uid"] == uid:
            return m
    return None


def make_minion(game, card_id):
    c = CARDS[card_id]
    return {
        "uid": game.next_uid(), "card": card_id, "name": c["name"],
        "emoji": c.get("emoji", ""), "attack": c.get("attack", 0),
        "health": c.get("health", 1), "max_health": c.get("health", 1),
        "taunt": bool(c.get("taunt")), "can_attack": False,
    }


class Player(object):
    def __init__(self, pid, name):
        self.id = pid
        self.token = pid
        self.name = name
        self.hp = START_HP
        self.max_hp = START_HP
        self.mana = 0
        self.max_mana = 0
        self.deck = []
        self.hand = []
        self.board = []
        self.fatigue = 0
        self.turn_count = 0
        self.dead = False


class Game(object):
    def __init__(self, gid):
        self.id = gid
        self.players = []
        self.started = False
        self.finished = False
        self.turn_index = 0
        self.turn_number = 0
        self.winner = None
        self.log = []
        self.history = []
        self._uid = 0
        self.lock = threading.RLock()

    def next_uid(self):
        self._uid += 1
        return self._uid

    def log_msg(self, text):
        self.log.append(text)
        if len(self.log) > 80:
            self.log = self.log[-80:]

    def get_player(self, pid):
        for p in self.players:
            if p.id == pid:
                return p
        return None

    def current_player(self):
        if not self.players or self.turn_index < 0 or self.turn_index >= len(self.players):
            return None
        return self.players[self.turn_index]

    def alive_players(self):
        return [p for p in self.players if p.hp > 0]

    def record_history(self):
        self.history.append({
            "turn": self.turn_number,
            "hp": dict((p.id, max(0, p.hp)) for p in self.players),
        })
        if len(self.history) > 120:
            self.history = self.history[-120:]

    def add_player(self, name):
        if self.started or len(self.players) >= MAX_PLAYERS:
            return None
        pid = uuid.uuid4().hex[:8]
        if not name:
            name = "Игрок %d" % (len(self.players) + 1)
        p = Player(pid, name)
        self.players.append(p)
        self.log_msg("➕ %s присоединился к игре" % p.name)
        return p

    def start(self):
        if self.started or len(self.players) < MIN_PLAYERS:
            return False
        self.started = True
        for p in self.players:
            p.deck = build_deck()
            p.hp = START_HP
            p.max_hp = START_HP
            p.turn_count = 0
            p.fatigue = 0
            p.board = []
            p.hand = []
            for _ in range(4):
                self.draw_card(p)
        self.turn_index = 0
        self.turn_number = 0
        self.log_msg("🎮 Игра началась! Игроков: %d" % len(self.players))
        self.begin_turn()
        return True

    def begin_turn(self):
        p = self.current_player()
        if p is None or p.hp <= 0:
            return
        self.turn_number += 1
        p.turn_count += 1
        p.max_mana = min(MAX_MANA, p.turn_count)
        p.mana = p.max_mana
        for m in p.board:
            m["can_attack"] = True
        self.record_history()
        self.log_msg("— Ход %s (мана %d/%d) —" % (p.name, p.mana, p.max_mana))
        self.draw_card(p)
        self.check_deaths()
        if not self.finished and p.hp <= 0:
            self.advance_turn()

    def advance_turn(self):
        if self.finished:
            return
        n = len(self.players)
        if n == 0:
            return
        for i in range(1, n + 1):
            idx = (self.turn_index + i) % n
            p = self.players[idx]
            if p.hp > 0:
                self.turn_index = idx
                self.begin_turn()
                return

    def end_turn(self, pid):
        if not self.started or self.finished:
            return "Игра не активна"
        cur = self.current_player()
        if cur is None or cur.id != pid:
            return "Сейчас не ваш ход"
        self.check_deaths()
        if self.finished:
            return None
        self.advance_turn()
        return None

    def _post_action(self):
        self.check_deaths()
        if self.finished:
            return
        cur = self.current_player()
        if cur is None or cur.hp <= 0:
            self.advance_turn()

    def draw_card(self, p, count=1):
        for _ in range(count):
            if p.hp <= 0:
                return
            if len(p.hand) >= HAND_LIMIT:
                if p.deck:
                    p.deck.pop()
                self.log_msg("🔥 Рука %s переполнена — карта сгорела" % p.name)
                continue
            if not p.deck:
                p.fatigue += 1
                p.hp -= p.fatigue
                self.log_msg("😵 %s истощён: -%d HP" % (p.name, p.fatigue))
                continue
            p.hand.append(p.deck.pop())

    def damage_minion(self, owner, minion, amount):
        if amount <= 0 or minion is None:
            return
        minion["health"] -= amount
        self.log_msg("%s %s получает %d урона" % (minion.get("emoji", ""), minion["name"], amount))
        if minion["health"] <= 0:
            if minion in owner.board:
                owner.board.remove(minion)
            self.log_msg("💀 %s погибает" % minion["name"])

    def damage_hero(self, player, amount):
        if amount <= 0 or player is None:
            return
        player.hp -= amount
        self.log_msg("💢 %s получает %d урона (HP %d)" % (player.name, amount, max(0, player.hp)))

    def heal_hero(self, player, amount):
        before = player.hp
        player.hp = min(player.max_hp, player.hp + amount)
        if player.hp > before:
            self.log_msg("💚 %s восстанавливает %d HP" % (player.name, player.hp - before))

    def heal_minion(self, minion, amount):
        before = minion["health"]
        minion["health"] = min(minion["max_health"], minion["health"] + amount)
        if minion["health"] > before:
            self.log_msg("💚 %s восстанавливает %d HP" % (minion["name"], minion["health"] - before))

    def check_deaths(self):
        for p in self.players:
            if p.hp <= 0 and not p.dead:
                p.dead = True
                self.log_msg("☠ %s выбывает из игры!" % p.name)
        if self.started and not self.finished:
            alive = self.alive_players()
            if len(alive) <= 1:
                self.finished = True
                self.winner = alive[0].id if alive else None
                if self.winner:
                    self.log_msg("🏆 Победитель: %s" % self.get_player(self.winner).name)

    def _resolve_target(self, target):
        if not target:
            return None, None, "Нужно выбрать цель"
        owner = self.get_player(target.get("player"))
        if owner is None:
            return None, None, "Некорректная цель"
        uid = target.get("minion")
        if uid is None:
            return owner, None, None
        m = find_minion(owner, uid)
        if m is None:
            return None, None, "Некорректная цель"
        return owner, m, None

    def play_card(self, pid, index, target):
        if not self.started or self.finished:
            return "Игра не активна"
        cur = self.current_player()
        if cur is None or cur.id != pid:
            return "Сейчас не ваш ход"
        p = self.get_player(pid)
        if p is None or p.hp <= 0:
            return "Вы не можете играть"
        if not isinstance(index, int) or index < 0 or index >= len(p.hand):
            return "Карта не найдена"
        card_id = p.hand[index]
        card = CARDS[card_id]
        if p.mana < card["cost"]:
            return "Недостаточно маны"
        need = card.get("target", "none")
        tgt_owner = None
        tgt_minion = None
        if card["type"] == "minion":
            if len(p.board) >= BOARD_LIMIT:
                return "На поле боя нет места"
        elif need in ("enemy", "friendly", "friendly_minion"):
            tgt_owner, tgt_minion, err = self._resolve_target(target)
            if err:
                return err
            if tgt_owner is None or tgt_owner.hp <= 0:
                return "Цель недоступна"
            if need == "enemy" and tgt_owner is p:
                return "Нужна цель противника"
            if need in ("friendly", "friendly_minion") and tgt_owner is not p:
                return "Нужна своя цель"
            if need == "friendly_minion" and tgt_minion is None:
                return "Нужно выбрать существо"

        p.mana -= card["cost"]
        p.hand.pop(index)
        self.log_msg("%s разыгрывает %s «%s»" % (p.name, card.get("emoji", ""), card["name"]))

        if card["type"] == "minion":
            p.board.append(make_minion(self, card_id))
        else:
            if need == "enemy":
                if tgt_minion is not None:
                    self.damage_minion(tgt_owner, tgt_minion, card.get("damage", 0))
                else:
                    self.damage_hero(tgt_owner, card.get("damage", 0))
            elif need == "friendly":
                if tgt_minion is not None:
                    self.heal_minion(tgt_minion, card.get("heal", 0))
                else:
                    self.heal_hero(tgt_owner, card.get("heal", 0))
            elif need == "friendly_minion":
                buff = card.get("buff", [0, 0])
                tgt_minion["attack"] += buff[0]
                tgt_minion["max_health"] += buff[1]
                tgt_minion["health"] += buff[1]
                self.log_msg("✨ %s получает +%d/+%d" % (tgt_minion["name"], buff[0], buff[1]))
            if card.get("draw"):
                self.draw_card(p, card["draw"])
            if card.get("aoe"):
                dmg = card["aoe"]
                for other in self.players:
                    if other is p or other.hp <= 0:
                        continue
                    for m in list(other.board):
                        self.damage_minion(other, m, dmg)
        self._post_action()
        return None

    def attack(self, pid, attacker_uid, target_player_id, target_uid):
        if not self.started or self.finished:
            return "Игра не активна"
        cur = self.current_player()
        if cur is None or cur.id != pid:
            return "Сейчас не ваш ход"
        p = self.get_player(pid)
        if p is None or p.hp <= 0:
            return "Вы не можете атаковать"
        attacker = find_minion(p, attacker_uid)
        if attacker is None:
            return "Существо не найдено"
        if not attacker["can_attack"]:
            return "Это существо ещё не может атаковать"
        if attacker["attack"] <= 0:
            return "У существа нет атаки"
        tp = self.get_player(target_player_id)
        if tp is None or tp is p or tp.hp <= 0:
            return "Некорректная цель"
        target = None
        if target_uid is not None:
            target = find_minion(tp, target_uid)
            if target is None:
                return "Некорректная цель"
        taunts = [m for m in tp.board if m["taunt"]]
        if taunts and (target is None or not target["taunt"]):
            return "Сначала уничтожьте существ с Провокацией"
        attacker["can_attack"] = False
        atk = attacker["attack"]
        self.log_msg("⚔ %s атакует %s" % (attacker["name"], target["name"] if target else tp.name))
        if target is not None:
            target_atk = target["attack"]
            self.damage_minion(tp, target, atk)
            if target["health"] > 0 and attacker["health"] > 0:
                self.damage_minion(p, attacker, target_atk)
        else:
            self.damage_hero(tp, atk)
        self._post_action()
        return None

    def card_view(self, card_id, index, playable):
        c = dict(CARDS[card_id])
        c["id"] = card_id
        c["index"] = index
        c["playable"] = playable
        return c

    def player_view(self, p, viewer_id):
        cur = self.current_player()
        view = {
            "id": p.id, "name": p.name, "hp": p.hp, "max_hp": p.max_hp,
            "mana": p.mana, "max_mana": p.max_mana,
            "hand_count": len(p.hand), "deck_count": len(p.deck),
            "board": [dict(m) for m in p.board],
            "alive": p.hp > 0,
            "is_me": p.id == viewer_id,
            "is_current": (cur is not None and cur.id == p.id and not self.finished),
            "turn_count": p.turn_count,
        }
        if p.id == viewer_id:
            can_play = (cur is not None and cur.id == p.id and not self.finished and p.hp > 0)
            view["hand"] = [
                self.card_view(cid, i, can_play and p.mana >= CARDS[cid]["cost"])
                for i, cid in enumerate(p.hand)
            ]
        return view

    def state_for(self, viewer_id):
        cur = self.current_player()
        return {
            "game_id": self.id,
            "started": self.started, "finished": self.finished,
            "winner": self.winner,
            "winner_name": self.get_player(self.winner).name if self.winner else None,
            "current": cur.id if cur else None,
            "my_turn": bool(cur is not None and cur.id == viewer_id and not self.finished),
            "turn_number": self.turn_number,
            "players": [self.player_view(p, viewer_id) for p in self.players],
            "me": viewer_id,
            "log": self.log[-14:],
            "history": self.history,
            "can_start": (not self.started) and len(self.players) >= MIN_PLAYERS,
            "is_host": bool(self.players) and self.players[0].id == viewer_id,
            "max_players": MAX_PLAYERS, "min_players": MIN_PLAYERS,
        }


# ---------- Функции для app.py ----------

def create_game(name):
    with LOCK:
        code = _gen_code()
        while code in GAMES:
            code = _gen_code()
        game = Game(code)
        GAMES[code] = game
        player = game.add_player(name)
    return code, player.token, None


def join_game(code, name):
    with LOCK:
        game = GAMES.get(code)
        if game is None:
            return None, 'Игра с таким кодом не найдена'
        if game.started:
            return None, 'Игра уже началась'
        if len(game.players) >= MAX_PLAYERS:
            return None, 'В игре уже максимум игроков'
        player = game.add_player(name)
    return player.token, None


def leave(code, token):
    with LOCK:
        game = GAMES.get(code)
        if game is None:
            return
        if not game.started:
            for p in list(game.players):
                if p.token == token:
                    game.players.remove(p)
            if not game.players:
                GAMES.pop(code, None)


def _current():
    code = session.get('code')
    tok = session.get('player_token')
    if not code or not tok:
        return None, None
    game = GAMES.get(code)
    if game is None:
        return None, None
    p = game.get_player(tok)
    if p is None:
        return None, None
    return game, p


# ---------- API ----------

@bp.route('/api/state')
def api_state():
    game, p = _current()
    if game is None:
        return jsonify({'ok': False, 'error': 'Нет активной игры'}), 404
    with game.lock:
        return jsonify({'ok': True, 'state': game.state_for(p.id)})


@bp.route('/api/start', methods=['POST'])
def api_start():
    game, p = _current()
    if game is None:
        return jsonify({'ok': False, 'error': 'Нет активной игры'}), 404
    with game.lock:
        if not game.players or game.players[0].id != p.id:
            return jsonify({'ok': False, 'error': 'Игру запускает создатель'}), 403
        if len(game.players) < MIN_PLAYERS:
            return jsonify({'ok': False, 'error': 'Нужно минимум 2 игрока'}), 400
        if not game.started:
            game.start()
        return jsonify({'ok': True, 'state': game.state_for(p.id)})


@bp.route('/api/play_card', methods=['POST'])
def api_play_card():
    game, p = _current()
    if game is None:
        return jsonify({'ok': False, 'error': 'Нет активной игры'}), 404
    data = request.get_json(silent=True) or {}
    with game.lock:
        err = game.play_card(p.id, data.get('index'), data.get('target'))
        if err:
            return jsonify({'ok': False, 'error': err}), 400
        return jsonify({'ok': True, 'state': game.state_for(p.id)})


@bp.route('/api/attack', methods=['POST'])
def api_attack():
    game, p = _current()
    if game is None:
        return jsonify({'ok': False, 'error': 'Нет активной игры'}), 404
    data = request.get_json(silent=True) or {}
    with game.lock:
        err = game.attack(p.id, data.get('attacker'),
                          data.get('target_player'), data.get('target_minion'))
        if err:
            return jsonify({'ok': False, 'error': err}), 400
        return jsonify({'ok': True, 'state': game.state_for(p.id)})


@bp.route('/api/end_turn', methods=['POST'])
def api_end_turn():
    game, p = _current()
    if game is None:
        return jsonify({'ok': False, 'error': 'Нет активной игры'}), 404
    with game.lock:
        err = game.end_turn(p.id)
        if err:
            return jsonify({'ok': False, 'error': err}), 400
        return jsonify({'ok': True, 'state': game.state_for(p.id)})

def has_player(code, token):
    with LOCK:
        game = GAMES.get(code)
        if game is None:
            return False
        return game.get_player(token) is not None
