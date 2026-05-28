"""
wuwa_inventory_kamera.scraping.data
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Shared in-memory data caches loaded from ``./data/<language>/*.json``.

These are the single source of truth for all scrapers and the updater.
When the legacy compatibility files are missing, the loader synthesizes the
same lookup shapes from generated ``data/catalog`` and ``data/locale``
outputs. The path ``./data/`` is relative to CWD, so run the tool from the
repo root.
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


def _load_json(path: pathlib.Path, default):
    try:
        with open(path, encoding='utf-8') as fh:
            obj = json.load(fh)
            if isinstance(default, list):
                return list(obj)
            return obj
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _load_generated_locale(data_root: pathlib.Path, language: str, filename: str) -> dict:
    for candidate in (language, 'en') if language != 'en' else ('en',):
        path = data_root / 'locale' / candidate / filename
        payload = _load_json(path, {})
        if isinstance(payload, dict) and payload:
            return payload
    return {}


def _load_generated_catalog(data_root: pathlib.Path, filename: str) -> dict:
    payload = _load_json(data_root / 'catalog' / filename, {})
    return payload if isinstance(payload, dict) else {}


def _build_generated_items(data_root: pathlib.Path, language: str) -> dict:
    catalog = _load_generated_catalog(data_root, 'items.json')
    locale = _load_generated_locale(data_root, language, 'items.json')
    if not catalog or not locale:
        return {}

    result: dict[str, dict] = {}
    for canonical_key, info in catalog.items():
        record = locale.get(canonical_key)
        if not isinstance(info, dict) or not isinstance(record, dict):
            continue
        normalized = record.get('normalized')
        display_name = record.get('display_name')
        identifier = info.get('id')
        image = info.get('image')
        if not isinstance(normalized, str) or not normalized:
            continue
        if not isinstance(display_name, str) or not display_name:
            continue
        if identifier is None:
            continue
        entry = {
            'id': identifier,
            'name': display_name,
        }
        if image is not None:
            entry['image'] = image
        result[normalized] = entry
    return result


def _build_generated_weapons(data_root: pathlib.Path, language: str) -> dict:
    catalog = _load_generated_catalog(data_root, 'weapons.json')
    locale = _load_generated_locale(data_root, language, 'weapons.json')
    if not catalog or not locale:
        return {}

    result: dict[str, dict] = {}
    for canonical_key, info in catalog.items():
        record = locale.get(canonical_key)
        if not isinstance(info, dict) or not isinstance(record, dict):
            continue
        normalized = record.get('normalized')
        display_name = record.get('display_name')
        identifier = info.get('id')
        if not isinstance(normalized, str) or not normalized:
            continue
        if not isinstance(display_name, str) or not display_name:
            continue
        if identifier is None:
            continue
        entry = {
            'id': identifier,
            'name': display_name,
        }
        if 'image' in info:
            entry['image'] = info['image']
        if 'rarity' in info:
            entry['rarity'] = info['rarity']
        result[normalized] = entry
    return result


def _build_generated_id_lookup(
    data_root: pathlib.Path,
    language: str,
    *,
    catalog_filename: str,
    locale_filename: str,
    key_field: str,
) -> dict:
    catalog = _load_generated_catalog(data_root, catalog_filename)
    locale = _load_generated_locale(data_root, language, locale_filename)
    if not catalog or not locale:
        return {}

    result: dict[str, int] = {}
    for canonical_key, info in catalog.items():
        record = locale.get(canonical_key)
        if not isinstance(info, dict) or not isinstance(record, dict):
            continue
        lookup_key = record.get(key_field)
        identifier = info.get('id')
        if not isinstance(lookup_key, str) or not lookup_key:
            continue
        if identifier is None:
            continue
        result[lookup_key] = identifier
    return result


def _build_generated_stats(data_root: pathlib.Path, language: str) -> dict:
    locale = _load_generated_locale(data_root, language, 'stats.json')
    if not locale:
        return {}

    result: dict[str, str] = {}
    for canonical_key, record in locale.items():
        if not isinstance(record, dict):
            continue
        normalized = record.get('normalized')
        if isinstance(normalized, str) and normalized:
            result[normalized] = canonical_key
    return result


def _build_generated_defined_text(data_root: pathlib.Path, language: str) -> dict:
    locale = _load_generated_locale(data_root, language, 'definedText.json')
    if not locale:
        return {}

    result: dict[str, str] = {}
    for text_key, record in locale.items():
        if not isinstance(text_key, str) or not isinstance(record, dict):
            continue
        normalized = record.get('normalized')
        if isinstance(normalized, str) and normalized:
            result[text_key] = normalized
    return result


def _build_generated_sonata_keys(data_root: pathlib.Path) -> list[str]:
    catalog = _load_generated_catalog(data_root, 'sonatas.json')
    if not catalog:
        return []

    ordered = sorted(
        (
            (canonical_key, info.get('id'))
            for canonical_key, info in catalog.items()
            if isinstance(canonical_key, str) and isinstance(info, dict)
        ),
        key=lambda item: (item[1] is None, item[1], item[0]),
    )
    return [canonical_key for canonical_key, _ in ordered]


def loadData(language: str | None = None) -> None:
    """(Re-)load all data files for *language* (default ``'en'``)."""
    if language is None:
        language = 'en'

    logging.info('Loading data for language: %s', language)
    data_root = pathlib.Path('data')
    data_dir = data_root / language

    def _load(filename: str, default=None, generated=None):
        if default is None:
            default = {}
        path = data_dir / filename
        logging.info('Loading file: %s', path)
        payload = _load_json(path, default)
        if payload != default:
            return payload
        if generated is not None:
            generated_payload = generated()
            if generated_payload:
                logging.info('Falling back to generated data for %s', filename)
                return generated_payload
        return default

    global itemsID, charactersID, weaponsID, echoesID, achievementsID
    global echoStats, definedText, sonataName

    itemsID.clear()
    itemsID.update(_load('items.json', generated=lambda: _build_generated_items(data_root, language)))

    charactersID.clear()
    charactersID.update(
        _load(
            'characters.json',
            generated=lambda: _build_generated_id_lookup(
                data_root,
                language,
                catalog_filename='characters.json',
                locale_filename='characters.json',
                key_field='normalized',
            ),
        )
    )

    weaponsID.clear()
    weaponsID.update(_load('weapons.json', generated=lambda: _build_generated_weapons(data_root, language)))

    echoesID.clear()
    echoesID.update(
        _load(
            'echoes.json',
            generated=lambda: _build_generated_id_lookup(
                data_root,
                language,
                catalog_filename='echoes.json',
                locale_filename='echoes.json',
                key_field='normalized',
            ),
        )
    )

    achievementsID.clear()
    achievementsID.update(
        _load(
            'achievements.json',
            generated=lambda: _build_generated_id_lookup(
                data_root,
                language,
                catalog_filename='achievements.json',
                locale_filename='achievements.json',
                key_field='display_name',
            ),
        )
    )

    echoStats.clear()
    echoStats.update(_load('echoStats.json', generated=lambda: _build_generated_stats(data_root, language)))

    definedText.clear()
    definedText.update(_load('definedText.json', generated=lambda: _build_generated_defined_text(data_root, language)))

    sonataName.clear()
    sonataName.extend(_load('sonataName.json', default=[], generated=lambda: _build_generated_sonata_keys(data_root)))

    logging.info('Data loaded: %d definedText entries', len(definedText))


# Load defaults at import time (same behaviour as the legacy scraping.data module)
loadData('en')
