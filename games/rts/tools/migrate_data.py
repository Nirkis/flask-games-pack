# -*- coding: utf-8 -*-
"""Одноразовая миграция units.json и buildings.json под abilities-систему.
Убирает cost/hotkey/produced_by/build_time/produces, добавляет abilities.
"""
import json, os, io

DATA = os.path.join(os.path.dirname(__file__), '..', 'data')

UNIT_ABILITIES = {
    'worker':      ['move','stop','hold','gather','build_alliance','repair'],
    'footman':     ['move','stop','hold','attack','attack_move','patrol'],
    'rifleman':    ['move','stop','hold','attack','attack_move','patrol'],
    'knight':      ['move','stop','hold','attack','attack_move','patrol'],
    'mortar_team': ['move','stop','hold','attack','attack_move','patrol'],
    'paladin':     ['move','stop','hold','attack','attack_move','patrol',
                    'holy_light','devotion_aura','divine_shield'],

    'peon':        ['move','stop','hold','gather','build_horde','repair'],
    'grunt':       ['move','stop','hold','attack','attack_move','patrol'],
    'headhunter':  ['move','stop','hold','attack','attack_move','patrol'],
    'raider':      ['move','stop','hold','attack','attack_move','patrol'],
    'catapult':    ['move','stop','hold','attack','attack_move','patrol'],
    'blademaster': ['move','stop','hold','attack','attack_move','patrol',
                    'wind_walk','mirror_image','critical_strike'],

    'acolyte':     ['move','stop','hold','gather','build_undead','repair'],
    'death_knight':['move','stop','hold','attack','attack_move','patrol',
                    'death_coil','unholy_aura','animate_dead'],

    'wisp':        ['move','stop','hold','gather','build_night_elf','repair'],
    'priestess_of_the_moon': ['move','stop','hold','attack','attack_move','patrol',
                              'searing_arrow','trueshot_aura','starfall'],
}

BUILDING_ABILITIES = {
    'town_hall':         ['train_town_hall'],
    'barracks_alliance': ['train_barracks_alliance'],
    'farm':              [],
    'altar_of_light':    ['train_altar_of_light'],
    'great_hall':        ['train_great_hall'],
    'barracks_horde':    ['train_barracks_horde'],
    'food_totem':        [],
    'altar_of_storms':   ['train_altar_of_storms'],
    'necropolis':        ['train_necropolis'],
    'crypt':             [],
    'ziggurat':          [],
    'altar_of_darkness': ['train_altar_of_darkness'],
    'tree_of_life':      ['train_tree_of_life'],
    'ancient_of_war':    [],
    'moon_well':         [],
    'altar_of_elders':   ['train_altar_of_elders'],
}

DROP_UNIT_KEYS = ('cost', 'hotkey', 'produced_by', 'build_time')
DROP_BUILDING_KEYS = ('cost', 'hotkey', 'produces', 'build_time')


def load(name):
    print(DATA, name)
    with io.open(os.path.join(DATA, name), 'r', encoding='utf-8') as f:
        return json.load(f)

def save(name, obj):
    with io.open(os.path.join(DATA, name), 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def migrate_units():
    units = load('units.json')
    for uid, u in units.items():
        for k in DROP_UNIT_KEYS:
            u.pop(k, None)
        u['abilities'] = UNIT_ABILITIES.get(uid, ['move','stop','hold'])
    save('units.json', units)
    print('units.json migrated:', len(units))

def migrate_buildings():
    b = load('buildings.json')
    for bid, d in b.items():
        for k in DROP_BUILDING_KEYS:
            d.pop(k, None)
        d['abilities'] = BUILDING_ABILITIES.get(bid, [])
    save('buildings.json', b)
    print('buildings.json migrated:', len(b))

if __name__ == '__main__':
    migrate_units()
    migrate_buildings()
    print('OK')
