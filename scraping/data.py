"""
Shared in-memory data caches loaded from ./data/*.json.

These are the single source of truth for all scrapers and the updater.
The DataUpdater's post-update hooks mutate these dicts/lists in-place so
that a running session always sees current data without a restart.
"""

import json
import pathlib
import logging

itemsID: dict = {}
charactersID: dict = {}
weaponsID: dict = {}
echoesID: dict = {}
achievementsID: dict = {}
echoStats: dict = {}
definedText: dict = {}
sonataName: dict = {}

def loadData(language):
    logging.error(f"Loading data for language: {language}")

    if language is None: language = 'en'
    dir = pathlib.Path('data') / language

    def loadFile(filePATH: str, default={}):
        try:
            logging.error(f"Loading file: {dir / filePATH}")
            with open(dir / filePATH, 'r', encoding="utf-8") as file:
                data = json.load(file)
                if isinstance(default, list):
                    data = list(data)
                return data
        except (FileNotFoundError, json.JSONDecodeError):
            return default

    global itemsID, charactersID, weaponsID, echoesID, achievementsID, echoStats, definedText, sonataName

    # replace the contents of the dicts with the loaded data, preserving the reference
    itemsID.update(loadFile('items.json'))
    charactersID.update(loadFile('characters.json'))
    weaponsID.update(loadFile('weapons.json'))
    echoesID.update(loadFile('echoes.json'))
    achievementsID.update(loadFile('achievements.json'))
    echoStats.update(loadFile('echoStats.json'))
    definedText.update(loadFile('definedText.json'))
    sonataName.update(loadFile('sonataName.json', []))

    logging.error(f"loaded: {definedText=}")

loadData('en')
