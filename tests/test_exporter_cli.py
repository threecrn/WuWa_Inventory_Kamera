from __future__ import annotations

import json

from wuwa_inventory_kamera.cli import exporter


def test_build_wutheringtools_export_builds_expected_envelope() -> None:
    characters_payload = {
        '1105': {
            '_name': 'shorekeeper',
            'character_key': 'shorekeeper',
            'echoes': {},
        }
    }
    echoes_payload = [
        {
            '310000010': {
                'echo_key': 'bellbornegeochelone',
                'rarity': 5,
                '_cost': 4,
                '_equipped': 'Shorekeeper',
                'sonata_key': 'moonlitclouds',
                'stats': {
                    'main': {'Crit Rate': '22.0%'},
                    'sub': {
                        'Crit DMG': '16.2%',
                        'ATK': '40',
                        'Energy Regen': '6.8%',
                    },
                },
            }
        }
    ]

    payload = exporter.build_wutheringtools_export(
        characters_payload=characters_payload,
        echoes_payload=echoes_payload,
        language='en',
    )

    assert payload['meta'] == {'version': '2', 'source': 'WutheringTools'}

    character_data = json.loads(payload['data']['character'])
    inventory_data = json.loads(payload['data']['inventory'])

    assert 'characters' in character_data
    assert 'activeCharacter' in character_data
    assert character_data['activeCharacter']
    assert 'mainEcho' not in next(iter(character_data['characters'].values()))

    assert isinstance(inventory_data['echoes'], list)
    assert len(inventory_data['echoes']) == 1

    echo_row = inventory_data['echoes'][0]
    assert echo_row['type'] == 4
    assert echo_row['rank'] == 5
    assert echo_row['stat'] == 'CritRate'
    assert echo_row['echoSet'] == 'MoonlitClouds'
    assert echo_row['echoSubStatsType1'] == 'CritDMG'
    assert echo_row['echoSubStatsValue1'] == 16.2
    assert echo_row['echoSubStatsType2'] == 'ATK_FLAT'
    assert echo_row['echoSubStatsValue2'] == 40
    assert echo_row['echoSubStatsType3'] == 'EnergyRegen'
    assert echo_row['echoSubStatsValue3'] == 6.8
    assert isinstance(echo_row['echoId'], str)
    assert echo_row['echoId']

    characters_out = character_data['characters']
    equipped_sets = [details.get('echoes', {}) for details in characters_out.values()]
    assert any('0' in echoes for echoes in equipped_sets)

    equipped_echo = next(echoes['0'] for echoes in equipped_sets if '0' in echoes)
    assert equipped_echo['echo'] == echo_row['echo']
    assert equipped_echo['stat'] == 'CritRate'
    assert 'echoId' not in equipped_echo


def test_main_writes_json_output(tmp_path, monkeypatch) -> None:
    chars_path = tmp_path / 'characters.json'
    echoes_path = tmp_path / 'echoes.json'
    output_path = tmp_path / 'wutheringtools_export.json'

    chars_path.write_text(
        json.dumps(
            {
                '1105': {
                    '_name': 'shorekeeper',
                    'character_key': 'shorekeeper',
                    'echoes': {},
                }
            }
        ),
        encoding='utf-8',
    )
    echoes_path.write_text(
        json.dumps(
            [
                {
                    '310000010': {
                        'echo_key': 'bellbornegeochelone',
                        'rarity': 5,
                        '_cost': 4,
                        'stats': {
                            'main': {'Crit Rate': '22.0%'},
                            'sub': {'Crit DMG': '16.2%'},
                        },
                    }
                }
            ]
        ),
        encoding='utf-8',
    )

    monkeypatch.setattr(
        exporter,
        'build_wutheringtools_export',
        lambda **kwargs: {
            'meta': {'version': '2', 'source': 'WutheringTools'},
            'data': {'character': '{}', 'inventory': '{}'},
        },
    )
    monkeypatch.setattr(
        exporter,
        '_extract_payload',
        lambda path, section_name: {'ok': (str(path), section_name)},
    )

    monkeypatch.setattr(
        exporter.argparse.ArgumentParser,
        'parse_args',
        lambda self: exporter.argparse.Namespace(
            characters=str(chars_path),
            echoes=str(echoes_path),
            output=str(output_path),
            language='en',
        ),
    )

    exporter.main()

    written = json.loads(output_path.read_text(encoding='utf-8'))
    assert written['meta']['source'] == 'WutheringTools'
    assert written['data'] == {'character': '{}', 'inventory': '{}'}


def test_resolve_sonata_preserves_display_case_for_connector_words() -> None:
    maps = exporter._LocalizationMaps(
        echoes_by_id={},
        characters_by_id={},
        sonata_by_key={'wishesofquietsnowfall': 'WishesofQuietSnowfall'},
    )

    resolved = exporter._resolve_sonata({'sonata_key': 'wishesofquietsnowfall'}, maps)
    assert resolved == 'WishesofQuietSnowfall'


def test_stat_token_percent_aliases_map_to_wutheringtools_keys() -> None:
    expected = {
        'heavy%': 'HeavyAttackDMGBonus',
        'basic%': 'BasicAttackDMGBonus',
        'skill%': 'ResonanceSkillDMGBonus',
        'liberation%': 'ResonanceLiberationDMGBonus',
        'glacio%': 'Glacio',
        'fusion%': 'Fusion',
        'electro%': 'Electro',
        'aero%': 'Aero',
        'spectro%': 'Spectro',
        'havoc%': 'Havoc',
    }

    for raw, token in expected.items():
        assert exporter._stat_token(raw, value='1.0%', is_main=False) == token


def test_stat_token_keeps_flat_main_stat_for_non_percent_damage_aliases() -> None:
    assert exporter._stat_token('heavy', value='40', is_main=False) == 'Heavy'
    assert exporter._stat_token('basic', value='40', is_main=False) == 'Basic'
