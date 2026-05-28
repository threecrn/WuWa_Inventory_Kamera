from __future__ import annotations

import json
from pathlib import Path

import wuwa_inventory_kamera.game.navigation as navigation


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def test_load_sonata_catalog_prefers_generated_catalog(tmp_path, monkeypatch) -> None:
    _write_json(
        tmp_path / 'data' / 'catalog' / 'sonatas.json',
        {'moonlitclouds': {'id': 42, 'text_key': 'PhantomFetter_42_Name'}},
    )
    _write_json(tmp_path / 'data' / 'en' / 'sonataName.json', {'stale': 1})

    monkeypatch.setattr(navigation, 'basePATH', tmp_path)

    assert navigation._load_sonata_catalog() == {'moonlitclouds': 42}


def test_load_sonata_catalog_ignores_legacy_file_without_generated_catalog(tmp_path, monkeypatch) -> None:
    _write_json(tmp_path / 'data' / 'en' / 'sonataName.json', {'Moonlit Clouds': 12})

    monkeypatch.setattr(navigation, 'basePATH', tmp_path)

    assert navigation._load_sonata_catalog() == {}


def test_sonata_text_matching_uses_localized_candidates(tmp_path, monkeypatch) -> None:
    _write_json(tmp_path / 'data' / 'languages.json', {'English': 'en', '日本語': 'ja'})
    _write_json(
        tmp_path / 'data' / 'locale' / 'ja' / 'sonatas.json',
        {
            'moonlitclouds': {
                'display_name': '月を窺う軽雲',
                'normalized': '月を窺う軽雲',
                'aliases': ['月を窺う軽雲', 'moonlitclouds'],
            },
        },
    )

    monkeypatch.setattr(navigation, 'basePATH', tmp_path)
    monkeypatch.setattr(navigation.app_config, 'gameLanguage', '日本語')

    language_code = navigation._resolve_game_language_code()
    locale = navigation._load_sonata_locale(language_code)

    assert navigation._sonata_text_candidates('moonlitclouds', locale_data=locale) == (
        'moonlitclouds',
        '月を窺う軽雲',
    )
    assert navigation._sonata_text_matches('月を窺う軽雲', 'moonlitclouds', locale_data=locale)
