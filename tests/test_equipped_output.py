from __future__ import annotations

from types import SimpleNamespace

import numpy as np

import wuwa_inventory_kamera.scraping.service.assemblers.echo_assembler as echo_assembler_module
import wuwa_inventory_kamera.scraping.service.assemblers._equipped as equipped_module
import wuwa_inventory_kamera.scraping.service.assemblers.weapon_assembler as weapon_assembler_module
from wuwa_inventory_kamera.scraping.service.assemblers.echo_assembler import EchoAssembler
from wuwa_inventory_kamera.scraping.service.assemblers._equipped import parse_equipped_character
from wuwa_inventory_kamera.scraping.service.assemblers.weapon_assembler import WeaponAssembler
from wuwa_inventory_kamera.scraping.service.captures import EchoCapture, WeaponCapture


def _token(text: str):
    return ([[0, 0], [1, 0], [1, 1], [0, 1]], text, 1.0)


def test_weapon_assembler_adds_equipped_character(monkeypatch) -> None:
    monkeypatch.setattr(
        weapon_assembler_module,
        '_get_data',
        lambda: ({'commandoofconviction': 'weapon-id'}, {}),
    )

    image = np.zeros((1, 1, 3), dtype=np.uint8)
    assembler = WeaponAssembler()
    result = assembler.assemble(
        WeaponCapture(index=3, name=image, value=image, rank=image),
        [_token('Commando of Conviction')],
        [_token('Lv. 40/90')],
        [_token('3')],
        [_token('Equipped by Camellya')],
    )

    assert result.data == {
        'id': 'weapon-id',
        'level': 40,
        'maxLevel': 90,
        'rank': 3,
        '_equipped': 'camellya',
    }


def test_echo_assembler_adds_equipped_character(monkeypatch) -> None:
    monkeypatch.setattr(
        echo_assembler_module,
        '_get_data',
        lambda: ({'geochelone': 31001}, {}, {}),
    )
    monkeypatch.setattr(
        echo_assembler_module,
        '_get_validators',
        lambda: (
            lambda _stats: 1,
            lambda _level: 0,
            lambda _cost, _level, _rarity, _stats: SimpleNamespace(
                valid=True,
                warnings=[],
                errors=[],
            ),
        ),
    )

    image = np.zeros((1, 1, 3), dtype=np.uint8)
    assembler = EchoAssembler()
    monkeypatch.setattr(
        assembler,
        '_parse_stats',
        lambda _name_tokens, _value_tokens, _echo_stats, _scan_index: (0, {'main': {}, 'sub': {}}),
    )
    monkeypatch.setattr(
        assembler,
        '_build_echo',
        lambda _name, _level, _tune_lv, _sonata, _rarity, _stats, _echoes_id, _echo_stats: {
            'echo-id': {'validated': True},
        },
    )
    monkeypatch.setattr(
        assembler._sonata_matcher,
        'match_to_sonata_key',
        lambda *_args, **_kwargs: 'moonlit-clouds',
    )

    result = assembler.assemble(
        EchoCapture(
            echo_index=7,
            card=image,
            echo_name=image,
            level=image,
            stats_name=image,
            stats_value=image,
            sonata_icon=image,
            detected_level=25,
            detected_rarity=5,
        ),
        [_token('Geochelone')],
        [],
        [],
        [_token('Equipped by Shorekeeper')],
    )

    assert result.data == {
        'echo-id': {
            'validated': True,
            '_equipped': 'shorekeeper',
            '_scanIndex': 7,
            '_monsterId': 31001,
            '_cost': 1,
        }
    }


def test_parse_equipped_character_fuzzy_matches_localized_character_name(
    tmp_path,
    monkeypatch,
) -> None:
    data_dir = tmp_path / 'data'
    (data_dir / 'locale' / 'ja').mkdir(parents=True)
    (data_dir / 'languages.json').write_text(
        '{"English": "en", "日本語": "ja"}',
        encoding='utf-8',
    )
    (data_dir / 'locale' / 'ja' / 'characters.json').write_text(
        (
            '{'
            '"iuno": {"display_name": "イウノ", "normalized": "イウノ", "aliases": ["イウノ", "iuno"]},'
            '"shorekeeper": {"display_name": "ショアキーパー", "normalized": "ショアキーパー", "aliases": ["ショアキーパー"]}'
            '}'
        ),
        encoding='utf-8',
    )

    monkeypatch.setattr(equipped_module, 'basePATH', tmp_path)
    monkeypatch.setattr(equipped_module.app_config, 'gameLanguage', '日本語')
    monkeypatch.setattr(equipped_module, '_CHARACTER_NAMES_CACHE_KEY', None)
    monkeypatch.setattr(equipped_module, '_CHARACTER_NAMES_CACHE_VALUE', None)

    assert parse_equipped_character([_token('Equipped by luno')]) == 'iuno'


def test_parse_equipped_character_prefers_generated_locale_character_data(
    tmp_path,
    monkeypatch,
) -> None:
    data_dir = tmp_path / 'data'
    (data_dir / 'locale' / 'ja').mkdir(parents=True)
    (data_dir / 'languages.json').write_text(
        '{"English": "en", "日本語": "ja"}',
        encoding='utf-8',
    )
    (data_dir / 'locale' / 'ja' / 'characters.json').write_text(
        (
            '{'
            '"iuno": {"display_name": "イウノ", "normalized": "イウノ", "aliases": ["イウノ", "iuno"]},'
            '"shorekeeper": {"display_name": "ショアキーパー", "normalized": "ショアキーパー", "aliases": ["ショアキーパー"]}'
            '}'
        ),
        encoding='utf-8',
    )

    monkeypatch.setattr(equipped_module, 'basePATH', tmp_path)
    monkeypatch.setattr(equipped_module.app_config, 'gameLanguage', '日本語')
    monkeypatch.setattr(equipped_module, '_CHARACTER_NAMES_CACHE_KEY', None)
    monkeypatch.setattr(equipped_module, '_CHARACTER_NAMES_CACHE_VALUE', None)

    assert parse_equipped_character([_token('Equipped by luno')]) == 'iuno'


def test_parse_equipped_character_matches_generated_localized_alias(
    tmp_path,
    monkeypatch,
) -> None:
    data_dir = tmp_path / 'data'
    (data_dir / 'locale' / 'ja').mkdir(parents=True)
    (data_dir / 'languages.json').write_text(
        '{"English": "en", "日本語": "ja"}',
        encoding='utf-8',
    )
    (data_dir / 'locale' / 'ja' / 'characters.json').write_text(
        (
            '{'
            '"iuno": {"display_name": "イウノ", "normalized": "イウノ", "aliases": ["イウノ"]},'
            '"shorekeeper": {"display_name": "ショアキーパー", "normalized": "ショアキーパー", "aliases": ["ショアキーパー"]}'
            '}'
        ),
        encoding='utf-8',
    )

    monkeypatch.setattr(equipped_module, 'basePATH', tmp_path)
    monkeypatch.setattr(equipped_module.app_config, 'gameLanguage', '日本語')
    monkeypatch.setattr(equipped_module, '_CHARACTER_NAMES_CACHE_KEY', None)
    monkeypatch.setattr(equipped_module, '_CHARACTER_NAMES_CACHE_VALUE', None)

    assert parse_equipped_character([_token('Equipped by イウノ')]) == 'iuno'