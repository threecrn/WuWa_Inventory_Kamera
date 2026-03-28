"""
wuwa_inventory_kamera.scraping.data
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Shared in-memory data caches loaded from ``./data/<language>/*.json``.

These are the single source of truth for all scrapers and the updater.
The path ``./data/`` is relative to CWD, so run the tool from the repo root.
"""
from __future__ import annotations

import json
import logging
import pathlib

itemsID: dict = {}
charactersID: dict = {}
weaponsID: dict = {}
echoesID: dict = {}
achievementsID: dict = {}
echoStats: dict = {}
definedText: dict = {}
sonataName: list = []


def loadData(language: str | None = None) -> None:
    """(Re-)load all data files for *language* (default ``'en'``)."""
    if language is None:
        language = 'en'

    logging.info('Loading data for language: %s', language)
    data_dir = pathlib.Path('data') / language

    def _load(filename: str, default=None):
        if default is None:
            default = {}
        path = data_dir / filename
        try:
            logging.info('Loading file: %s', path)
            with open(path, encoding='utf-8') as fh:
                obj = json.load(fh)
                if isinstance(default, list):
                    return list(obj)
                return obj
        except (FileNotFoundError, json.JSONDecodeError):
            return default

    global itemsID, charactersID, weaponsID, echoesID, achievementsID
    global echoStats, definedText, sonataName

    itemsID.update(_load('items.json'))
    charactersID.update(_load('characters.json'))
    weaponsID.update(_load('weapons.json'))
    echoesID.update(_load('echoes.json'))
    achievementsID.update(_load('achievements.json'))
    echoStats.update(_load('echoStats.json'))
    definedText.update(_load('definedText.json'))

    sonataName.clear()
    sonataName.extend(_load('sonataName.json', default=[]))

    logging.info('Data loaded: %d definedText entries', len(definedText))


# Load defaults at import time (same behaviour as the legacy scraping.data module)
loadData('en')
