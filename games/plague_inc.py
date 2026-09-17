# -*- coding: utf-8 -*-
"""Plague Inc (realtime + SSE). 1-4 игрока, общий пул живых."""
import random
import threading
import time

from flask import Blueprint, jsonify, request, session, Response, stream_with_context

from games import events as events_bus

bp = Blueprint('plague_inc', __name__, url_prefix='/pl')

GAME_META = {
    'code': 'pl', 'prefix': 'PL', 'name': 'Plague Inc',
    'players': '1-4', 'template': 'plague.html',
    'modes': [1, 2, 3, 4], 'realtime': True, 'order': 50,
}

# ---------- Мир ----------
WORLD_POP         = 10_000_000
START_INFECTED    = 500
START_VIS         = 5.0
DETECT_THRESHOLD  = 100.0
EXTINCT_THRESHOLD = 100

TICK_INTERVAL = 1.0
GAME_TTL      = 2 * 3600

BASE_SPREAD   = 0.04
BASE_KILL     = 0.005
BASE_VIS      = 0.5
VIS_PER_RATIO = 20.0

CURE_MAX      = 100.0
CURE_DETECTED = 0.9
CURE_REMOVAL  = 0.25
NEUT_MU_ZERO_TICK = 5

START_SKILL_POINTS = 3
DEFAULT_DISEASE_TYPE = 'bacteria'

GAMES = {}
LOCK = threading.RLock()
CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
_tick_started = False


# ---------- Типы болезней ----------
DISEASE_TYPES = {
    'bacteria': {
        'name': '🦠 Бактерия',
        'desc': 'Быстрая и живучая. Отличный старт для агрессивного роста.',
        'spread': 1.30, 'kill': 0.80, 'vis': 1.10, 'vaccine': 1.20, 'cost_mult': 1.00,
        'organ_mult': {'respiratory': 1.0, 'digestive': 1.3, 'nervous': 0.8,
                       'circulatory': 1.1, 'reproductive': 1.0, 'immune': 1.1},
        'passive': 'Каждые 20 тактов один открытый узел +1 бесплатно.',
    },
    'virus': {
        'name': '🧬 Вирус',
        'desc': 'Баланс. Мутирует сам: ±1 случайного узла каждые 15 тактов.',
        'spread': 1.10, 'kill': 1.00, 'vis': 1.30, 'vaccine': 1.00, 'cost_mult': 1.00,
        'organ_mult': {'respiratory': 1.2, 'digestive': 0.9, 'nervous': 0.9,
                       'circulatory': 1.1, 'reproductive': 1.4, 'immune': 1.2},
        'passive': 'Каждые 15 тактов один открытый узел ±1 (50/50).',
    },
    'parasite': {
        'name': '🪱 Паразит',
        'desc': 'Смертельный, но медленный. Тише 30 % — заражение ×1.5.',
        'spread': 0.80, 'kill': 1.50, 'vis': 0.80, 'vaccine': 0.90, 'cost_mult': 1.10,
        'organ_mult': {'respiratory': 0.8, 'digestive': 1.1, 'nervous': 1.0,
                       'circulatory': 1.3, 'reproductive': 0.9, 'immune': 1.3},
        'passive': 'При заметности < 30 % заражение ×1.5; при ≥ 50 % — ×0.8.',
    },
    'prion': {
        'name': '🧫 Прион',
        'desc': 'Очень смертельный, очень тихий, очень медленный.',
        'spread': 0.60, 'kill': 2.00, 'vis': 0.50, 'vaccine': 0.70, 'cost_mult': 1.20,
        'organ_mult': {'respiratory': 0.7, 'digestive': 0.7, 'nervous': 1.5,
                       'circulatory': 1.0, 'reproductive': 0.6, 'immune': 0.8},
        'passive': 'Вакцина против вас ×0.7. После 30 такта смертность ×2.',
    },
}


# ---------- Дерево симптомов (45 узлов) ----------
SKILL_NODES = {
    'body': {'name': 'Организм', 'emoji': '🧬', 'x': 50, 'y': 3, 'max': 5,
             'requires': [], 'system': 'root', 'effect': {'all': 0.02},
             'desc': '+2 % ко всем эффектам ниже.', 'cost_tier': 'system'},

    'sys_resp': {'name': 'Дыхательная', 'emoji': '🫁', 'x': 8, 'y': 14, 'max': 5,
                 'requires': [('body', 2)], 'system': 'respiratory',
                 'effect': {'spread': 0.02}, 'desc': '+2 % распространение.', 'cost_tier': 'system'},
    'sys_dig': {'name': 'Пищеварительная', 'emoji': '🫄', 'x': 24, 'y': 14, 'max': 5,
                'requires': [('body', 2)], 'system': 'digestive',
                'effect': {'spread': 0.02}, 'desc': '+2 % распространение.', 'cost_tier': 'system'},
    'sys_ner': {'name': 'Нервная', 'emoji': '🧠', 'x': 40, 'y': 14, 'max': 5,
                'requires': [('body', 3)], 'system': 'nervous',
                'effect': {'kill': 0.02}, 'desc': '+2 % смертность.', 'cost_tier': 'system'},
    'sys_circ': {'name': 'Кровеносная', 'emoji': '🩸', 'x': 56, 'y': 14, 'max': 5,
                 'requires': [('body', 3)], 'system': 'circulatory',
                 'effect': {'spread': 0.02}, 'desc': '+2 % распространение.', 'cost_tier': 'system'},
    'sys_rep': {'name': 'Половая', 'emoji': '⚧', 'x': 72, 'y': 14, 'max': 5,
                'requires': [('body', 3)], 'system': 'reproductive',
                'effect': {'spread': 0.02}, 'desc': '+2 % распространение.', 'cost_tier': 'system'},
    'sys_imm': {'name': 'Иммунная', 'emoji': '🛡', 'x': 88, 'y': 14, 'max': 5,
                'requires': [('body', 4)], 'system': 'immune',
                'effect': {'om_rate': 0.05, 'vaccine_slow': 0.02},
                'desc': '+5 % ОМ, −2 % вакцина.', 'cost_tier': 'system'},

    'trachea': {'name': 'Трахея', 'emoji': '🌬', 'x': 4, 'y': 28, 'max': 5,
                'requires': [('sys_resp', 2)], 'system': 'respiratory',
                'effect': {'spread': 0.03}, 'desc': '+3 % распространение.', 'cost_tier': 'organ'},
    'bronchi': {'name': 'Бронхи', 'emoji': '🌿', 'x': 12, 'y': 28, 'max': 5,
                'requires': [('sys_resp', 4)], 'system': 'respiratory',
                'effect': {'spread': 0.03, 'kill': 0.03}, 'desc': '+3 %/+3 %.', 'cost_tier': 'organ'},
    'lungs': {'name': 'Лёгкие', 'emoji': '🫁', 'x': 8, 'y': 40, 'max': 5,
              'requires': [('trachea', 3)], 'system': 'respiratory',
              'effect': {'kill': 0.05}, 'desc': '+5 % смертность.', 'cost_tier': 'organ'},
    'cough': {'name': 'Кашель', 'emoji': '💨', 'x': 4, 'y': 52, 'max': 1,
              'requires': [('trachea', 2), ('lungs', 2)], 'system': 'respiratory',
              'effect': {'spread': 0.25}, 'desc': 'КОМБО: +25 % распространение.', 'cost_tier': 'combo'},
    'pleuritis': {'name': 'Плеврит', 'emoji': '🩸', 'x': 12, 'y': 52, 'max': 1,
                  'requires': [('bronchi', 3), ('lungs', 3)], 'system': 'respiratory',
                  'effect': {'spread': 0.20, 'kill': 0.10}, 'desc': 'КОМБО: +20 %/+10 %.', 'cost_tier': 'combo'},
    'pneumonia': {'name': 'Пневмония', 'emoji': '🔥', 'x': 8, 'y': 64, 'max': 1,
                  'requires': [('bronchi', 5), ('lungs', 5)], 'system': 'respiratory',
                  'effect': {'kill': 0.50}, 'desc': 'СУПЕР: +50 % смертность.', 'cost_tier': 'combo'},
    'tuberculosis': {'name': 'Туберкулёз', 'emoji': '☠', 'x': 8, 'y': 76, 'max': 1,
                     'requires': [('pneumonia', 1), ('pleuritis', 1)], 'system': 'respiratory',
                     'effect': {'spread': 0.60, 'kill': 0.60, 'visibility': 0.40},
                     'desc': 'ФИНАЛ: +60 %/+60 %, +40 % заметность.', 'cost_tier': 'final'},

    'stomach': {'name': 'Желудок', 'emoji': '🫄', 'x': 20, 'y': 28, 'max': 5,
                'requires': [('sys_dig', 2)], 'system': 'digestive',
                'effect': {'spread': 0.03}, 'desc': '+3 % распространение.', 'cost_tier': 'organ'},
    'intestine': {'name': 'Кишечник', 'emoji': '🧶', 'x': 28, 'y': 28, 'max': 5,
                  'requires': [('sys_dig', 4)], 'system': 'digestive',
                  'effect': {'spread': 0.04}, 'desc': '+4 % распространение.', 'cost_tier': 'organ'},
    'liver': {'name': 'Печень', 'emoji': '🍺', 'x': 24, 'y': 40, 'max': 5,
              'requires': [('stomach', 3)], 'system': 'digestive',
              'effect': {'kill': 0.05}, 'desc': '+5 % смертность.', 'cost_tier': 'organ'},
    'diarrhea': {'name': 'Диарея', 'emoji': '💩', 'x': 20, 'y': 52, 'max': 1,
                 'requires': [('stomach', 2), ('intestine', 2)], 'system': 'digestive',
                 'effect': {'spread': 0.30}, 'desc': 'КОМБО: +30 % распространение.', 'cost_tier': 'combo'},
    'vomit': {'name': 'Рвота', 'emoji': '🤢', 'x': 28, 'y': 52, 'max': 1,
              'requires': [('stomach', 3), ('intestine', 3)], 'system': 'digestive',
              'effect': {'spread': 0.15, 'kill': 0.15}, 'desc': 'КОМБО: +15 %/+15 %.', 'cost_tier': 'combo'},
    'cholera': {'name': 'Холера', 'emoji': '💧', 'x': 24, 'y': 64, 'max': 1,
                'requires': [('diarrhea', 1), ('vomit', 1), ('intestine', 5)], 'system': 'digestive',
                'effect': {'spread': 0.40, 'kill': 0.20}, 'desc': 'СУПЕР: +40 %/+20 %.', 'cost_tier': 'combo'},
    'dysentery': {'name': 'Дизентерия', 'emoji': '☣', 'x': 24, 'y': 76, 'max': 1,
                  'requires': [('cholera', 1), ('liver', 5)], 'system': 'digestive',
                  'effect': {'spread': 0.30, 'kill': 0.50, 'visibility': 0.20},
                  'desc': 'ФИНАЛ: +30 %/+50 %, +20 % заметность.', 'cost_tier': 'final'},

    'brain': {'name': 'Головной мозг', 'emoji': '🧠', 'x': 36, 'y': 28, 'max': 5,
              'requires': [('sys_ner', 2)], 'system': 'nervous',
              'effect': {'kill': 0.04}, 'desc': '+4 % смертность.', 'cost_tier': 'organ'},
    'spine': {'name': 'Спинной мозг', 'emoji': '🦴', 'x': 44, 'y': 28, 'max': 5,
              'requires': [('sys_ner', 4)], 'system': 'nervous',
              'effect': {'kill': 0.03, 'spread': 0.02}, 'desc': '+3 %/+2 %.', 'cost_tier': 'organ'},
    'headache': {'name': 'Головная боль', 'emoji': '🤕', 'x': 36, 'y': 52, 'max': 1,
                 'requires': [('brain', 2), ('spine', 2)], 'system': 'nervous',
                 'effect': {'spread': 0.20}, 'desc': 'КОМБО: +20 % распространение.', 'cost_tier': 'combo'},
    'confusion': {'name': 'Помутнение', 'emoji': '😵', 'x': 44, 'y': 52, 'max': 1,
                  'requires': [('brain', 3), ('spine', 3)], 'system': 'nervous',
                  'effect': {'spread': 0.15, 'kill': 0.15}, 'desc': 'КОМБО: +15 %/+15 %.', 'cost_tier': 'combo'},
    'encephalitis': {'name': 'Энцефалит', 'emoji': '🧠', 'x': 40, 'y': 64, 'max': 1,
                     'requires': [('brain', 5), ('confusion', 1)], 'system': 'nervous',
                     'effect': {'kill': 0.40}, 'desc': 'СУПЕР: +40 % смертность.', 'cost_tier': 'combo'},
    'madness': {'name': 'Безумие', 'emoji': '☠', 'x': 40, 'y': 76, 'max': 1,
                'requires': [('encephalitis', 1), ('headache', 1)], 'system': 'nervous',
                'effect': {'spread': 0.50, 'kill': 0.30, 'visibility': 0.30},
                'desc': 'ФИНАЛ: +50 %/+30 %, +30 % заметность.', 'cost_tier': 'final'},

    'vessels': {'name': 'Сосуды', 'emoji': '🩸', 'x': 52, 'y': 28, 'max': 5,
                'requires': [('sys_circ', 2)], 'system': 'circulatory',
                'effect': {'spread': 0.03}, 'desc': '+3 % распространение.', 'cost_tier': 'organ'},
    'heart': {'name': 'Сердце', 'emoji': '❤', 'x': 60, 'y': 28, 'max': 5,
              'requires': [('sys_circ', 4)], 'system': 'circulatory',
              'effect': {'kill': 0.05}, 'desc': '+5 % смертность.', 'cost_tier': 'organ'},
    'blood': {'name': 'Кровь', 'emoji': '🩸', 'x': 56, 'y': 40, 'max': 5,
              'requires': [('vessels', 3), ('heart', 2)], 'system': 'circulatory',
              'effect': {'spread': 0.04, 'kill': 0.02}, 'desc': '+4 %/+2 %.', 'cost_tier': 'organ'},
    'bleeding': {'name': 'Кровотечение', 'emoji': '🩸', 'x': 52, 'y': 52, 'max': 1,
                 'requires': [('vessels', 2), ('blood', 2)], 'system': 'circulatory',
                 'effect': {'spread': 0.25, 'kill': 0.10}, 'desc': 'КОМБО: +25 %/+10 %.', 'cost_tier': 'combo'},
    'infarction': {'name': 'Инфаркт', 'emoji': '💔', 'x': 60, 'y': 52, 'max': 1,
                   'requires': [('heart', 4), ('vessels', 3)], 'system': 'circulatory',
                   'effect': {'kill': 0.40}, 'desc': 'СУПЕР: +40 % смертность.', 'cost_tier': 'combo'},
    'sepsis': {'name': 'Сепсис', 'emoji': '🔥', 'x': 56, 'y': 64, 'max': 1,
               'requires': [('blood', 5), ('infarction', 1)], 'system': 'circulatory',
               'effect': {'kill': 0.55, 'visibility': 0.20},
               'desc': 'ФИНАЛ: +55 % смертность, +20 % заметность.', 'cost_tier': 'final'},
    'hemorrhage': {'name': 'Геморрагия', 'emoji': '🩸', 'x': 56, 'y': 76, 'max': 1,
                   'requires': [('sepsis', 1), ('bleeding', 1)], 'system': 'circulatory',
                   'effect': {'spread': 0.35, 'kill': 0.65, 'visibility': 0.35},
                   'desc': 'ЛЕГЕНДА: +35 %/+65 %, +35 % заметность.', 'cost_tier': 'legendary'},

    'mucosa': {'name': 'Слизистые', 'emoji': '🫧', 'x': 68, 'y': 28, 'max': 5,
               'requires': [('sys_rep', 2)], 'system': 'reproductive',
               'effect': {'spread': 0.04}, 'desc': '+4 % распространение.', 'cost_tier': 'organ'},
    'gonads': {'name': 'Гонады', 'emoji': '🥚', 'x': 76, 'y': 28, 'max': 5,
               'requires': [('sys_rep', 4)], 'system': 'reproductive',
               'effect': {'spread': 0.03, 'stealth': 0.03}, 'desc': '+3 %/+3 % скрытность.', 'cost_tier': 'organ'},
    'hidden_transmission': {'name': 'Скрытая передача', 'emoji': '🩸', 'x': 68, 'y': 52, 'max': 1,
                            'requires': [('mucosa', 2), ('gonads', 2)], 'system': 'reproductive',
                            'effect': {'spread': 0.30, 'stealth': 0.20},
                            'desc': 'КОМБО: +30 % распр., +20 % скрытность.', 'cost_tier': 'combo'},
    'vertical_transmission': {'name': 'Вертикальная передача', 'emoji': '🧬', 'x': 76, 'y': 52, 'max': 1,
                              'requires': [('gonads', 4), ('mucosa', 3)], 'system': 'reproductive',
                              'effect': {'spread': 0.20, 'stealth': 0.25},
                              'desc': 'КОМБО: +20 % распр., +25 % скрытность.', 'cost_tier': 'combo'},
    'sexual_storm': {'name': 'Половой шторм', 'emoji': '🌐', 'x': 72, 'y': 64, 'max': 1,
                     'requires': [('vertical_transmission', 1), ('hidden_transmission', 1)],
                     'system': 'reproductive',
                     'effect': {'spread': 0.50, 'stealth': 0.40},
                     'desc': 'СУПЕР: +50 % распр., +40 % скрытность.', 'cost_tier': 'final'},

    'immunosuppression': {'name': 'Иммуносупрессия', 'emoji': '⛔', 'x': 84, 'y': 28, 'max': 5,
                          'requires': [('sys_imm', 2)], 'system': 'immune',
                          'effect': {'vaccine_slow': 0.10}, 'desc': '−10 % вакцина.', 'cost_tier': 'organ'},
    'masking': {'name': 'Маскировка', 'emoji': '🎭', 'x': 92, 'y': 28, 'max': 5,
                'requires': [('sys_imm', 4)], 'system': 'immune',
                'effect': {'stealth': 0.15}, 'desc': '+15 % скрытность.', 'cost_tier': 'organ'},
    'genomic': {'name': 'Геномная изменчивость', 'emoji': '🧬', 'x': 88, 'y': 52, 'max': 1,
                'requires': [('immunosuppression', 3), ('masking', 3)], 'system': 'immune',
                'effect': {'vaccine_slow': 0.30}, 'desc': 'КОМБО: −30 % вакцина.', 'cost_tier': 'combo'},
    'cytokine': {'name': 'Цитокиновый шторм', 'emoji': '☣', 'x': 88, 'y': 64, 'max': 1,
                 'requires': [('immunosuppression', 5), ('masking', 3)], 'system': 'immune',
                 'effect': {'vaccine_slow': 0.40, 'kill': 0.30},
                 'desc': 'СУПЕР: −40 % вакцина, +30 % смертность.', 'cost_tier': 'final'},
    'autoimmune': {'name': 'Автоиммунный коллапс', 'emoji': '☠', 'x': 88, 'y': 76, 'max': 1,
                   'requires': [('cytokine', 1), ('genomic', 1)], 'system': 'immune',
                   'effect': {'vaccine_slow': 0.60, 'visibility': 0.50},
                   'desc': 'ФИНАЛ: −60 % вакцина, +50 % заметность.', 'cost_tier': 'legendary'},

    'pandemic': {'name': 'Пандемия', 'emoji': '🦠', 'x': 50, 'y': 92, 'max': 1,
                 'requires': [('tuberculosis', 1), ('dysentery', 1), ('madness', 1),
                              ('hemorrhage', 1), ('sexual_storm', 1), ('autoimmune', 1)],
                 'system': 'root',
                 'effect': {'spread': 0.80, 'kill': 0.80, 'visibility': 0.50},
                 'desc': 'ЛЕГЕНДА: все 6 финалов. +80 %/+80 %, +50 % заметность.',
                 'cost_tier': 'legendary'},
}

COST_BY_TIER = {
    'system':    {'base': 1, 'per_level': 1},
    'organ':     {'base': 2, 'per_level': 1},
    'combo':     {'base': 5, 'per_level': 0},
    'final':     {'base': 10, 'per_level': 0},
    'legendary': {'base': 20, 'per_level': 0},
}


def upgrade_cost(node_id, current_level, type_mult):
    tier = COST_BY_TIER[SKILL_NODES[node_id]['cost_tier']]
    cost = tier['base'] + current_level * tier['per_level']
    return max(1, int(round(cost * type_mult)))


# ---------- Утилиты ----------
def _gen_code():
    return 'PL-' + ''.join(random.choice(CODE_ALPHABET) for _ in range(4))


def _gen_token():
    return '%d-%d' % (random.randint(0, 10 ** 9), id(object()) % 10 ** 9)


def _fmt(n):
    return '{:,}'.format(int(n)).replace(',', ' ')


def add_log(g, text):
    g['log'].append(text)
    if len(g['log']) > 100:
        g['log'] = g['log'][-100:]


# ---------- Модель ----------
def new_disease(dtype=None):
    return {
        'type': dtype,
        'infected': START_INFECTED, 'peak_infected': START_INFECTED, 'killed': 0,
        'visibility': START_VIS, 'detected': False,
        'cure': 0.0, 'neutralized': False, 'neutralized_at': None, 'extinct': False,
        'skills': {nid: 0 for nid in SKILL_NODES},
        'skill_points': START_SKILL_POINTS,
        'om_peaks_used': 0, 'score': 0, 'passive_counter': 0,
    }


def new_game(code, num_players):
    return {
        'id': code,
        'num_players': num_players,
        'names': ['Игрок %d' % (i + 1) for i in range(num_players)],
        'tokens': [None] * num_players,
        'occupied': [False] * num_players,
        'dtypes': [None] * num_players,
        'phase': 'lobby',
        'winner': None,
        'diseases': [],
        'world_killed': 0,
        'log': [],
        'history': [],
        'created': time.time(),
        'tick_count': 0,
    }


def start_game(g):
    g['diseases'] = [new_disease(dt) for dt in g['dtypes']]
    g['world_killed'] = 0
    g['winner'] = None
    g['phase'] = 'battle'
    g['tick_count'] = 0
    for i, d in enumerate(g['diseases']):
        recompute_score(d)
        add_log(g, '🧫 %s играет за %s' % (g['names'][i], DISEASE_TYPES[d['type']]['name']))
    add_log(g, '🦠 Пандемия началась! Население: %s' % _fmt(WORLD_POP))
    record_history(g)
    _ensure_tick_thread()


def maybe_start(g):
    if g['phase'] != 'lobby':
        return
    if not all(g['occupied']):
        return
    if any(dt is None for dt in g['dtypes']):
        return
    start_game(g)


# ---------- Эффекты ----------
def compute_effects(d):
    eff = {'spread': 0.0, 'kill': 0.0, 'stealth': 0.0,
           'visibility': 0.0, 'vaccine_slow': 0.0, 'om_rate': 0.0}
    if d['type'] is None:
        return eff
    dt = DISEASE_TYPES[d['type']]
    org = dt['organ_mult']
    body_bonus = d['skills'].get('body', 0) * SKILL_NODES['body']['effect']['all']
    all_mult = 1.0 + body_bonus
    for nid, lvl in d['skills'].items():
        if lvl <= 0 or nid == 'body':
            continue
        node = SKILL_NODES[nid]
        sys_mult = org.get(node.get('system', 'root'), 1.0)
        for k, v in node['effect'].items():
            eff[k] += v * lvl * sys_mult * all_mult
    return eff


def recompute_score(d):
    d['score'] = int(d['killed'] // 100) * 5 + int(d['peak_infected'] // 1000) * 1


# ---------- Такт мира ----------
def _rebalance_pool(g):
    total_alive = max(0, WORLD_POP - int(g['world_killed']))
    total_inf = sum(d['infected'] for d in g['diseases'] if not d['extinct'])
    if total_inf > total_alive and total_inf > 0:
        scale = total_alive / total_inf
        for d in g['diseases']:
            if not d['extinct']:
                d['infected'] *= scale


def world_tick(g):
    total_alive = max(0, WORLD_POP - int(g['world_killed']))
    total_inf_before = sum(d['infected'] for d in g['diseases'] if not d['extinct'])
    free_pool = max(0.0, total_alive - total_inf_before)

    # 1) Распространение — общий пул
    claims = []
    for d in g['diseases']:
        if d['extinct'] or d['neutralized'] or d['type'] is None or d['infected'] <= 0:
            claims.append((d, 0.0))
            continue
        eff = compute_effects(d)
        dt = DISEASE_TYPES[d['type']]
        spread_mult = (1.0 + eff['spread']) * dt['spread']
        if d['type'] == 'parasite':
            if d['visibility'] < 30:
                spread_mult *= 1.5
            elif d['visibility'] >= 50:
                spread_mult *= 0.8
        rate = BASE_SPREAD * spread_mult
        desired = d['infected'] * rate * (1.0 - d['infected'] / float(WORLD_POP))
        if desired < 0:
            desired = 0.0
        claims.append((d, desired))

    total_desired = sum(c[1] for c in claims)
    scale = (free_pool / total_desired) if (total_desired > free_pool and total_desired > 0) else 1.0

    for d, desired in claims:
        if desired <= 0:
            continue
        add = desired * scale
        if add <= 0:
            continue
        d['infected'] += add
        if d['infected'] > d['peak_infected']:
            d['peak_infected'] = d['infected']

    # 2) Смертность
    for d in g['diseases']:
        if d['extinct'] or d['type'] is None:
            continue
        eff = compute_effects(d)
        dt = DISEASE_TYPES[d['type']]
        kill_mult = (1.0 + eff['kill']) * dt['kill']
        if d['type'] == 'prion' and g['tick_count'] >= 30:
            kill_mult *= 2.0
        if d['neutralized'] and d['neutralized_at'] is not None:
            elapsed = g['tick_count'] - d['neutralized_at']
            kill_mult = 0.0 if elapsed >= NEUT_MU_ZERO_TICK else kill_mult * 0.25 ** elapsed
        if d['infected'] > 0 and kill_mult > 0:
            deaths = d['infected'] * BASE_KILL * kill_mult
            if deaths > d['infected']:
                deaths = d['infected']
            d['killed'] += deaths
            d['infected'] -= deaths
            g['world_killed'] += deaths
    if g['world_killed'] > WORLD_POP:
        g['world_killed'] = WORLD_POP

    # 3) Заметность
    for d in g['diseases']:
        if d['extinct'] or d['detected'] or d['neutralized'] or d['type'] is None:
            continue
        eff = compute_effects(d)
        dt = DISEASE_TYPES[d['type']]
        ratio = d['infected'] / float(WORLD_POP)
        growth = (BASE_VIS + ratio * VIS_PER_RATIO) * dt['vis']
        growth *= 1.0 / (1.0 + eff['stealth'])
        growth *= 1.0 + eff['visibility']
        d['visibility'] += growth
        if d['visibility'] >= DETECT_THRESHOLD:
            d['visibility'] = DETECT_THRESHOLD
            d['detected'] = True

    # 4) Вылечивание нейтрализованных
    for d in g['diseases']:
        if d['extinct'] or not d['neutralized']:
            continue
        if d['infected'] > 0:
            d['infected'] *= (1.0 - CURE_REMOVAL)

    # 5) Экстинкция
    for d in g['diseases']:
        if d['extinct']:
            continue
        if d['infected'] < EXTINCT_THRESHOLD and (d['neutralized'] or d['infected'] <= 0):
            d['extinct'] = True
            d['infected'] = 0

    _rebalance_pool(g)
    for d in g['diseases']:
        recompute_score(d)


def update_cure(g):
    for idx, d in enumerate(g['diseases']):
        if d['extinct'] or d['neutralized'] or not d['detected'] or d['type'] is None:
            continue
        eff = compute_effects(d)
        dt = DISEASE_TYPES[d['type']]
        rate = CURE_DETECTED * dt['vaccine'] / (1.0 + eff['vaccine_slow'])
        d['cure'] += rate
        if d['cure'] >= CURE_MAX:
            d['cure'] = CURE_MAX
            d['neutralized'] = True
            d['neutralized_at'] = g['tick_count']
            add_log(g, '💉 Болезнь %s нейтрализована!' % g['names'][idx])


def award_om(g):
    for d in g['diseases']:
        if d['extinct'] or d['type'] is None:
            continue
        om_rate = 1.0 + compute_effects(d).get('om_rate', 0.0)
        new_chunk = int(d['peak_infected'] // 1000)
        if new_chunk > d['om_peaks_used']:
            delta = new_chunk - d['om_peaks_used']
            d['om_peaks_used'] = new_chunk
            d['skill_points'] += max(0, int(delta * om_rate))


def handle_passives(g):
    for idx, d in enumerate(g['diseases']):
        if d['extinct'] or d['neutralized'] or d['type'] is None:
            continue
        if d['type'] == 'bacteria':
            d['passive_counter'] += 1
            if d['passive_counter'] >= 20:
                d['passive_counter'] = 0
                opened = [nid for nid, lvl in d['skills'].items()
                          if 0 < lvl < SKILL_NODES[nid]['max']]
                if opened:
                    nid = random.choice(opened)
                    d['skills'][nid] += 1
                    add_log(g, '🦠 %s: %s ур. %d' % (g['names'][idx], SKILL_NODES[nid]['name'], d['skills'][nid]))
        elif d['type'] == 'virus':
            d['passive_counter'] += 1
            if d['passive_counter'] >= 15:
                d['passive_counter'] = 0
                critical = set()
                for nid, lvl in d['skills'].items():
                    if lvl > 0:
                        for rid, _ in SKILL_NODES[nid]['requires']:
                            critical.add(rid)
                up = [nid for nid, lvl in d['skills'].items() if 0 < lvl < SKILL_NODES[nid]['max']]
                dn = [nid for nid, lvl in d['skills'].items() if lvl > 0 and nid not in critical]
                if random.random() < 0.5 and up:
                    nid = random.choice(up); d['skills'][nid] += 1
                    add_log(g, '🧬 %s: +1 %s' % (g['names'][idx], SKILL_NODES[nid]['name']))
                elif dn:
                    nid = random.choice(dn); d['skills'][nid] -= 1
                    add_log(g, '🧬 %s: −1 %s' % (g['names'][idx], SKILL_NODES[nid]['name']))


def record_history(g):
    g['history'].append({
        'n': g['tick_count'],
        'cure':     [round(d['cure'], 2) for d in g['diseases']],
        'infected': [int(d['infected']) for d in g['diseases']],
        'killed':   [int(d['killed'])   for d in g['diseases']],
        'vis':      [round(d['visibility'], 1) for d in g['diseases']],
        'score':    [int(d['score'])    for d in g['diseases']],
    })
    if len(g['history']) > 400:
        g['history'] = g['history'][-400:]


def check_over(g):
    if g['phase'] != 'battle':
        return
    if not all(d['extinct'] for d in g['diseases']):
        return
    g['phase'] = 'over'
    n = g['num_players']
    if n == 1:
        g['winner'] = 0
        add_log(g, '🧪 Конец. Убито: %s' % _fmt(g['diseases'][0]['killed']))
        return
    kills = sorted(((i, d['killed']) for i, d in enumerate(g['diseases'])), key=lambda x: -x[1])
    top = kills[0][1]
    second = kills[1][1] if len(kills) > 1 else 0
    if top > 0 and (top - second) / max(top, 1.0) > 0.005:
        g['winner'] = kills[0][0]
        add_log(g, '🏆 Побеждает %s — убито %s' % (g['names'][g['winner']], _fmt(top)))
    else:
        g['winner'] = -1
        add_log(g, '🤝 Ничья')


# ---------- Тик-поток ----------
def _tick_one(g):
    if g['phase'] != 'battle':
        return
    world_tick(g)
    update_cure(g)
    award_om(g)
    g['tick_count'] += 1
    handle_passives(g)
    record_history(g)
    check_over(g)


def _tick_loop():
    while True:
        time.sleep(TICK_INTERVAL)
        try:
            with LOCK:
                now = time.time()
                for code, g in list(GAMES.items()):
                    if now - g['created'] > GAME_TTL:
                        GAMES.pop(code, None)
                        events_bus.drop_code(code)
                        continue
                    if g['phase'] != 'battle':
                        continue
                    try:
                        _tick_one(g)
                        _broadcast(g)
                    except Exception:
                        pass
        except Exception:
            pass


def _ensure_tick_thread():
    global _tick_started
    if _tick_started:
        return
    _tick_started = True
    threading.Thread(target=_tick_loop, daemon=True, name='pl_tick').start()


# ---------- Действия ----------
def do_upgrade(g, pid, node_id):
    if g['phase'] != 'battle':
        return 'Игра не активна'
    if pid >= len(g['diseases']):
        return 'Вы ещё не в игре'
    d = g['diseases'][pid]
    if d['extinct'] or d['neutralized']:
        return 'Болезнь нейтрализована'
    node = SKILL_NODES.get(node_id)
    if node is None:
        return 'Неизвестный узел'
    if d['skills'].get(node_id, 0) >= node['max']:
        return 'Узел максимален'
    for rid, rlvl in node['requires']:
        if d['skills'].get(rid, 0) < rlvl:
            return 'Требуется %s ур. %d' % (SKILL_NODES[rid]['name'], rlvl)
    dt = DISEASE_TYPES[d['type']]
    cost = upgrade_cost(node_id, d['skills'].get(node_id, 0), dt['cost_mult'])
    if d['skill_points'] < cost:
        return 'Нужно %d ОМ (у вас %d)' % (cost, d['skill_points'])
    d['skill_points'] -= cost
    d['skills'][node_id] = d['skills'].get(node_id, 0) + 1
    add_log(g, '⬆ %s: %s → ур. %d (−%d ОМ)'
            % (g['names'][pid], node['name'], d['skills'][node_id], cost))
    return None


def do_set_dtype(g, pid, dtype):
    if g['phase'] != 'lobby':
        return 'Игра уже началась'
    if pid >= g['num_players'] or not g['occupied'][pid]:
        return 'Вы не в игре'
    if dtype not in DISEASE_TYPES:
        return 'Неизвестный тип болезни'
    g['dtypes'][pid] = dtype
    add_log(g, '🧫 %s выбрал %s' % (g['names'][pid], DISEASE_TYPES[dtype]['name']))
    maybe_start(g)
    return None


# ---------- Реестр ----------
def _find_player(g, token):
    for i, t in enumerate(g['tokens']):
        if t == token:
            return i
    return None


def create_game(name, mode=2):
    mode = max(1, min(4, mode))
    with LOCK:
        code = _gen_code()
        while code in GAMES:
            code = _gen_code()
        g = new_game(code, mode)
        GAMES[code] = g
        tok = _gen_token()
        g['occupied'][0] = True
        g['tokens'][0] = tok
        g['names'][0] = name
        add_log(g, '%s создаёт партию' % name)
    return code, tok, None


def join_game(code, name):
    with LOCK:
        g = GAMES.get(code)
        if g is None:
            return None, 'Игра не найдена'
        if g['phase'] != 'lobby':
            return None, 'Игра уже началась'
        for slot in range(g['num_players']):
            if not g['occupied'][slot]:
                tok = _gen_token()
                g['occupied'][slot] = True
                g['tokens'][slot] = tok
                g['names'][slot] = name
                add_log(g, '%s присоединяется' % name)
                maybe_start(g)
                return tok, None
        return None, 'Мест нет'


def leave(code, token):
    with LOCK:
        g = GAMES.get(code)
        if g is None:
            return
        if g['phase'] == 'lobby':
            for i in range(g['num_players']):
                if g['tokens'][i] == token:
                    g['tokens'][i] = None
                    g['occupied'][i] = False
                    g['dtypes'][i] = None
                    break
            if not any(g['occupied']):
                GAMES.pop(code, None)


def has_player(code, token):
    with LOCK:
        g = GAMES.get(code)
        if not g:
            return False
        return _find_player(g, token) is not None


def _current():
    code = session.get('code')
    tok = session.get('player_token')
    if not code or not tok:
        return None, None
    g = GAMES.get(code)
    if g is None:
        return None, None
    i = _find_player(g, tok)
    if i is None:
        return None, None
    return g, i


# ---------- Сериализация ----------
def _node_view(node_id, node, d, can_act):
    lvl = d['skills'].get(node_id, 0) if d.get('type') else 0
    reqs_info = []
    reqs_ok = True
    for rid, rlvl in node['requires']:
        cur = d['skills'].get(rid, 0)
        met = cur >= rlvl
        if not met:
            reqs_ok = False
        reqs_info.append({'id': rid, 'name': SKILL_NODES[rid]['name'],
                          'have': cur, 'need': rlvl, 'met': met})
    if d.get('type'):
        dt = DISEASE_TYPES[d['type']]
        cost = upgrade_cost(node_id, lvl, dt['cost_mult'])
        can = can_act and lvl < node['max'] and reqs_ok and d['skill_points'] >= cost
    else:
        cost = 0
        can = False
    return {
        'id': node_id, 'name': node['name'], 'emoji': node['emoji'],
        'x': node['x'], 'y': node['y'], 'max': node['max'], 'level': lvl,
        'is_combo': node['cost_tier'] in ('combo', 'final', 'legendary'),
        'desc': node['desc'],
        'requires': reqs_info, 'reqs_ok': reqs_ok,
        'can_upgrade': can, 'cost': cost,
    }


def _empty_disease(idx, name, dtype):
    return {
        'idx': idx, 'name': name, 'type': dtype,
        'infected': 0, 'peak_infected': 0, 'killed': 0,
        'visibility': 0.0, 'detected': False,
        'cure': 0.0, 'neutralized': False, 'extinct': False, 'is_me': True,
        'skills': {nid: 0 for nid in SKILL_NODES},
        'skill_points': 0, 'score': 0,
    }


def build_state(g, pid):
    if g is None:
        return {'joined': False, 'phase': 'no_game'}
    if pid is None:
        return {'joined': False, 'phase': g['phase']}

    base = {
        'game_id': g['id'],
        'num_players': g['num_players'],
        'names': g['names'],
        'occupied': g['occupied'],
        'dtypes': g['dtypes'],
        'cure_max': CURE_MAX,
        'detect_threshold': DETECT_THRESHOLD,
        'extinct_threshold': EXTINCT_THRESHOLD,
        'tick_interval': TICK_INTERVAL,
        'disease_types': DISEASE_TYPES,
    }

    # Лобби
    if g['phase'] == 'lobby' or not g['diseases']:
        my_dtype = g['dtypes'][pid] if pid < len(g['dtypes']) else None
        empty = _empty_disease(pid, g['names'][pid], my_dtype)
        base.update({
            'joined': True, 'slot': pid, 'phase': 'lobby',
            'my_name': g['names'][pid], 'my_dtype': my_dtype,
            'world_pop': WORLD_POP, 'world_alive': WORLD_POP,
            'world_killed': 0, 'tick_count': 0,
            'diseases': [], 'my_disease': empty,
            'skill_nodes': [],
            'winner': None, 'i_won': False, 'draw': False,
            'can_act': False,
            'log': g['log'][-40:], 'history': [],
            'waiting_opponents': not all(g['occupied']),
            'all_ready': all(g['occupied']) and all(dt is not None for dt in g['dtypes']),
        })
        return base

    # Игра
    diseases_view = []
    for i, d in enumerate(g['diseases']):
        diseases_view.append({
            'idx': i, 'name': g['names'][i], 'type': d['type'],
            'infected': int(d['infected']), 'peak_infected': int(d['peak_infected']),
            'killed': int(d['killed']),
            'visibility': round(d['visibility'], 1), 'detected': d['detected'],
            'cure': round(d['cure'], 1),
            'neutralized': d['neutralized'], 'extinct': d['extinct'],
            'is_me': i == pid,
            'skills': dict(d['skills']),
            'skill_points': int(d['skill_points']),
            'score': int(d['score']),
        })

    me_raw = g['diseases'][pid]
    me = diseases_view[pid]
    total_alive = max(0, WORLD_POP - int(g['world_killed']))
    total_infected = sum(int(d['infected']) for d in g['diseases'] if not d['extinct'])
    uninfected = max(0, total_alive - total_infected)
    can_act = (g['phase'] == 'battle') and not me['extinct'] and not me['neutralized']

    nodes_view = [_node_view(nid, n, me_raw, can_act) for nid, n in SKILL_NODES.items()]

    base.update({
        'joined': True, 'slot': pid, 'phase': g['phase'],
        'my_name': g['names'][pid], 'my_dtype': g['dtypes'][pid],
        'world_pop': WORLD_POP, 'world_alive': total_alive,
        'world_killed': int(g['world_killed']),
        'uninfected': uninfected, 'total_infected': total_infected,
        'tick_count': g['tick_count'],
        'diseases': diseases_view, 'my_disease': me,
        'skill_nodes': nodes_view,
        'winner': g['winner'],
        'i_won': g['winner'] == pid, 'draw': g['winner'] == -1,
        'can_act': can_act,
        'log': g['log'][-40:], 'history': g['history'],
        'waiting_opponents': False, 'all_ready': True,
    })
    return base


def build_state_for_token(code, token):
    """Для портальной SSE-шины."""
    with LOCK:
        g = GAMES.get(code)
        if not g:
            return None
        i = _find_player(g, token)
        if i is None:
            return None
        return build_state(g, i)


# ---------- Broadcast ----------
def _broadcast(g):
    """Рассылает состояние всем подключённым игрокам. ВЫЗЫВАТЬ ПОД LOCK."""
    if g['phase'] == 'lobby' and not g['diseases']:
        # В лобби тоже шлём — там меняется dtypes/occupied
        pass
    items = []
    for i, tok in enumerate(g['tokens']):
        if tok is None:
            continue
        items.append((tok, {'type': 'state', 'state': build_state(g, i)}))
    if items:
        events_bus.push_many(g['id'], items)


# ---------- API ----------
@bp.route('/api/state')
def api_state():
    with LOCK:
        g, pid = _current()
        if g is None:
            return jsonify({'ok': False, 'error': 'Нет активной игры'}), 404
        return jsonify({'ok': True, 'state': build_state(g, pid)})


@bp.route('/api/events')
def api_events():
    """SSE-канал для этой игры."""
    g, pid = _current()
    if g is None:
        return ('', 401)

    code = g['id']
    tok = session.get('player_token')
    sub = events_bus.subscribe(code, tok)

    @stream_with_context
    def _gen():
        try:
            yield 'event: hello\ndata: {}\n\n'
            # Начальное состояние
            with LOCK:
                st = build_state(g, pid) if _find_player(g, tok) is not None else None
            if st is not None:
                yield events_bus.to_sse({'type': 'state', 'state': st})
            # Цикл
            while True:
                payload, status = sub.wait_next(20)
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


@bp.route('/api/action', methods=['POST'])
def api_action():
    with LOCK:
        g, pid = _current()
        if g is None:
            return jsonify({'ok': False, 'error': 'Нет активной игры'}), 404
        data = request.get_json(silent=True) or {}
        action = data.get('action')
        if action == 'upgrade':
            err = do_upgrade(g, pid, data.get('node'))
        elif action == 'set_type':
            err = do_set_dtype(g, pid, data.get('dtype'))
        else:
            return jsonify({'ok': False, 'error': 'Неизвестное действие'}), 400
        if err:
            return jsonify({'ok': False, 'error': err}), 400
        _broadcast(g)
        return jsonify({'ok': True, 'state': build_state(g, pid)})


@bp.route('/api/reset', methods=['POST'])
def api_reset():
    with LOCK:
        g, pid = _current()
        if g is None:
            return jsonify({'ok': False, 'error': 'Нет игры'}), 404
        old_tokens = g['tokens']
        old_names = g['names']
        old_dtypes = g['dtypes']
        num = g['num_players']
        code = g['id']
        newg = new_game(code, num)
        newg['tokens'] = old_tokens
        newg['names'] = old_names
        newg['dtypes'] = old_dtypes
        newg['occupied'] = [t is not None for t in old_tokens]
        add_log(newg, 'Новая партия')
        GAMES[code] = newg
        maybe_start(newg)
        _broadcast(newg)
        return jsonify({'ok': True, 'state': build_state(newg, pid)})

def list_active():
    with LOCK:
        out = []
        for code, g in GAMES.items():
            players = [g['names'][i] for i, occ in enumerate(g['occupied']) if occ]
            out.append({
                'id': code,
                'phase': g['phase'],
                'players': players,
                'occupied': len(players),
                'max_players': g['num_players'],
                'started': g['phase'] != 'lobby',
                'created': g['created'],
            })
        return out
