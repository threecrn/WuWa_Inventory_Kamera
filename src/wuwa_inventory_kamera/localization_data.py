"""
wuwa_inventory_kamera.localization_data
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Shared helpers for loading generated canonical and localized game-data files.
"""
from __future__ import annotations

import json
from pathlib import Path


def load_json_file(path: Path) -> object | None:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None


def resolve_game_language_code(*, base_path: Path, selected_language: object | None) -> str:
    selected = str(selected_language or 'English')
    data_root = base_path / 'data'

    if (data_root / 'locale' / selected).is_dir() or (data_root / selected).is_dir():
        return selected

    payload = load_json_file(data_root / 'languages.json')
    if isinstance(payload, dict):
        mapped = payload.get(selected)
        if isinstance(mapped, str) and mapped:
            return mapped
        if selected in payload.values():
            return selected

    return 'en'


def load_generated_catalog(filename: str, *, base_path: Path) -> dict[str, dict]:
    payload = load_json_file(base_path / 'data' / 'catalog' / filename)
    return payload if isinstance(payload, dict) else {}


def load_generated_locale(
    filename: str,
    language_code: str,
    *,
    base_path: Path,
    fallback_to_english: bool = True,
) -> dict[str, dict]:
    for code in _language_candidates(language_code, fallback_to_english=fallback_to_english):
        payload = load_json_file(base_path / 'data' / 'locale' / code / filename)
        if isinstance(payload, dict) and payload:
            return payload
    return {}


def iter_locale_data_paths(
    filename: str,
    language_code: str,
    *,
    base_path: Path,
    include_lookup: bool = False,
    include_legacy: bool = False,
    fallback_to_english: bool = True,
) -> tuple[Path, ...]:
    paths: list[Path] = []
    for code in _language_candidates(language_code, fallback_to_english=fallback_to_english):
        if include_lookup:
            paths.append(base_path / 'data' / 'locale' / code / 'lookup' / filename)
        paths.append(base_path / 'data' / 'locale' / code / filename)
    if include_legacy:
        for code in _language_candidates(language_code, fallback_to_english=fallback_to_english):
            paths.append(base_path / 'data' / code / filename)
    return tuple(paths)


def _language_candidates(language_code: str, *, fallback_to_english: bool) -> tuple[str, ...]:
    if language_code == 'en' or not fallback_to_english:
        return (language_code,)
    return (language_code, 'en')
