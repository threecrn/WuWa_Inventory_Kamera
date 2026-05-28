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
_loaded_language: str | None = None
_loaded_cache_languages: dict[str, str | None] = {
    'itemsID': None,
    'charactersID': None,
    'weaponsID': None,
    'echoesID': None,
    'achievementsID': None,
    'echoStats': None,
    'definedText': None,
    'sonataName': None,
}


def _load_json(path: pathlib.Path, default):
    try:
        with open(path, encoding='utf-8') as fh:
            obj = json.load(fh)
            if isinstance(default, list):
                return list(obj)
            return obj
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _load_payload(
    data_root: pathlib.Path,
    data_dir: pathlib.Path,
    filename: str,
    *,
    default=None,
    generated=None,
):
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


def _sync_loaded_language() -> None:
    global _loaded_language

    loaded_languages = set(_loaded_cache_languages.values())
    if None not in loaded_languages and len(loaded_languages) == 1:
        _loaded_language = next(iter(loaded_languages))
        return
    _loaded_language = None


def _load_cache(cache_name: str, language: str) -> None:
    data_root = pathlib.Path('data')
    data_dir = data_root / language

    if cache_name == 'itemsID':
        itemsID.clear()
        itemsID.update(
            _load_payload(
                data_root,
                data_dir,
                'items.json',
                generated=lambda: _build_generated_items(data_root, language),
            )
        )
    elif cache_name == 'charactersID':
        charactersID.clear()
        charactersID.update(
            _load_payload(
                data_root,
                data_dir,
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
    elif cache_name == 'weaponsID':
        weaponsID.clear()
        weaponsID.update(
            _load_payload(
                data_root,
                data_dir,
                'weapons.json',
                generated=lambda: _build_generated_weapons(data_root, language),
            )
        )
    elif cache_name == 'echoesID':
        echoesID.clear()
        echoesID.update(
            _load_payload(
                data_root,
                data_dir,
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
    elif cache_name == 'achievementsID':
        achievementsID.clear()
        achievementsID.update(
            _load_payload(
                data_root,
                data_dir,
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
    elif cache_name == 'echoStats':
        echoStats.clear()
        echoStats.update(
            _load_payload(
                data_root,
                data_dir,
                'echoStats.json',
                generated=lambda: _build_generated_stats(data_root, language),
            )
        )
    elif cache_name == 'definedText':
        definedText.clear()
        definedText.update(
            _load_payload(
                data_root,
                data_dir,
                'definedText.json',
                generated=lambda: _build_generated_defined_text(data_root, language),
            )
        )
    elif cache_name == 'sonataName':
        sonataName.clear()
        sonataName.extend(
            _load_payload(
                data_root,
                data_dir,
                'sonataName.json',
                default=[],
                generated=lambda: _build_generated_sonata_keys(data_root),
            )
        )
    else:
        raise ValueError(f'Unknown scraping data cache: {cache_name}')

    _loaded_cache_languages[cache_name] = language
    _sync_loaded_language()


def _ensure_cache_loaded(cache_name: str, language: str | None = None) -> None:
    requested_language = language or 'en'
    if _loaded_cache_languages[cache_name] != requested_language:
        _load_cache(cache_name, requested_language)


def loadData(language: str | None = None) -> None:
    """(Re-)load all data files for *language* (default ``'en'``)."""
    if language is None:
        language = 'en'

    logging.info('Loading data for language: %s', language)
    for cache_name in _loaded_cache_languages:
        _load_cache(cache_name, language)

    logging.info('Data loaded: %d definedText entries', len(definedText))


def ensureDataLoaded(language: str | None = None) -> None:
    """Load scraping lookup caches on demand.

    When *language* is omitted, the legacy compatibility caches keep their
    existing English-default behavior.
    """
    requested_language = language or 'en'
    if _loaded_language != requested_language:
        loadData(requested_language)


def getItemsID(language: str | None = None) -> dict:
    _ensure_cache_loaded('itemsID', language)
    return itemsID


def getCharactersID(language: str | None = None) -> dict:
    _ensure_cache_loaded('charactersID', language)
    return charactersID


def getWeaponsID(language: str | None = None) -> dict:
    _ensure_cache_loaded('weaponsID', language)
    return weaponsID


def getEchoesID(language: str | None = None) -> dict:
    _ensure_cache_loaded('echoesID', language)
    return echoesID


def getAchievementsID(language: str | None = None) -> dict:
    _ensure_cache_loaded('achievementsID', language)
    return achievementsID


def getEchoStats(language: str | None = None) -> dict:
    _ensure_cache_loaded('echoStats', language)
    return echoStats


def getDefinedText(language: str | None = None) -> dict:
    _ensure_cache_loaded('definedText', language)
    return definedText


def getSonataName(language: str | None = None) -> list:
    _ensure_cache_loaded('sonataName', language)
    return sonataName
