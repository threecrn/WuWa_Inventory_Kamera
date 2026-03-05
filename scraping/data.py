"""
Shared in-memory data caches loaded from ./data/*.json.

These are the single source of truth for all scrapers and the updater.
The DataUpdater's post-update hooks mutate these dicts/lists in-place so
that a running session always sees current data without a restart.
"""

import json


def loadFile(filePATH: str, default={}):
    try:
        with open(filePATH, 'r') as file:
            data = json.load(file)
            if isinstance(default, list):
                data = list(data)
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        return default


itemsID: dict         = loadFile('./data/items.json')
charactersID: dict    = loadFile('./data/characters.json')
weaponsID: dict       = loadFile('./data/weapons.json')
echoesID: dict        = loadFile('./data/echoes.json')
achievementsID: dict  = loadFile('./data/achievements.json')
echoStats: dict       = loadFile('./data/echoStats.json')
definedText: dict     = loadFile('./data/definedText.json')
sonataName: list      = loadFile('./data/sonataName.json', [])
