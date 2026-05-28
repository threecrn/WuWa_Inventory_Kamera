from __future__ import annotations

import json

import pytest

import wuwa_inventory_kamera.ui.inventory_models as inventory_models


@pytest.fixture(autouse=True)
def _patch_metadata(monkeypatch) -> None:
    monkeypatch.setattr(
        inventory_models.scraping_data,
        'itemsID',
        {
            'shellcredit': {'id': 2, 'name': 'Shell Credit', 'image': 'IconA/shell.png'},
            'resonancepotion': {'id': 10800, 'name': 'Resonance Potion', 'image': 'IconA/potion.png'},
        },
    )
    monkeypatch.setattr(
        inventory_models.scraping_data,
        'weaponsID',
        {
            'emeraldofgenesis': {
                'id': 21010074,
                'name': 'Emerald of Genesis',
                'rarity': 5,
                'image': 'IconWeapon/emerald.png',
            },
        },
    )
    monkeypatch.setattr(
        inventory_models.scraping_data,
        'charactersID',
        {'shorekeeper': 1105},
    )
    monkeypatch.setattr(
        inventory_models.scraping_data,
        'echoesID',
        {'bell borne geochelone': 310000010},
    )
    monkeypatch.setattr(
        inventory_models.scraping_data,
        'achievementsID',
        {'First Steps': 9001},
    )


def test_load_inventory_document_normalizes_echo_export() -> None:
    payload = [
        {
            '310000010': {
                'level': 25,
                'tuneLv': 5,
                'sonata': 'Moonlit Clouds',
                'rarity': 5,
                '_cost': 4,
                '_equipped': 'Shorekeeper',
                'stats': {
                    'main': {'Healing Bonus': '26.4%'},
                    'sub': {'Crit Rate': '8.4%', 'ATK%': '9.4%'},
                },
            }
        }
    ]

    document = inventory_models.load_inventory_document('echoes_wuwainventorykamera.json', payload)

    assert document.kind == 'echoes_export'
    assert document.sections[0].title == 'Echoes'
    row = document.sections[0].rows[0]
    assert row.title == 'Bell Borne Geochelone'
    assert row.subtitle == 'Echo ID: 310000010'
    assert 'Lv. 25 | Tune 5 | Rarity 5' in row.body_lines
    assert 'Sonata: Moonlit Clouds' in row.body_lines
    assert 'Cost: 4' in row.body_lines
    assert 'Equipped: Shorekeeper' in row.body_lines
    assert 'Main: Healing Bonus 26.4%' in row.body_lines
    assert 'Substats: 2' in row.body_lines
    assert 'Echo ID: 310000010' in row.details_lines
    assert 'Main Stat: Healing Bonus 26.4%' in row.details_lines
    assert 'Substat: Crit Rate 8.4%' in row.details_lines


def test_load_inventory_document_normalizes_weapon_export() -> None:
    payload = [{'id': 21010074, 'level': 90, 'maxLevel': 90, 'rank': 1, '_equipped': 'Shorekeeper'}]

    document = inventory_models.load_inventory_document('weapons_wuwainventorykamera.json', payload)

    assert document.kind == 'weapons_export'
    row = document.sections[0].rows[0]
    assert row.title == 'Emerald of Genesis'
    assert row.image_path == 'IconWeapon/emerald.png'
    assert 'Lv. 90 | Max 90 | Rank 1 | Rarity 5' in row.body_lines
    assert 'Equipped: Shorekeeper' in row.body_lines


def test_load_inventory_document_normalizes_item_export_from_filename() -> None:
    payload = [{'id': 10800, 'count': 3}]

    document = inventory_models.load_inventory_document('devItems_wuwainventorykamera.json', payload)

    assert document.kind == 'items_export'
    assert document.sections[0].title == 'Development Items'
    row = document.sections[0].rows[0]
    assert row.title == 'Resonance Potion'
    assert row.body_lines == ('Count: 3',)


def test_load_inventory_document_normalizes_character_export() -> None:
    payload = {
        '1105': {
            '_name': 'shorekeeper',
            'level': 90,
            'ascension': 6,
            'weapon': {'id': 21010074, 'level': 90, 'rank': 1},
            'skills': {'normal': 10, 'skill': 10},
            'chain': 2,
        }
    }

    document = inventory_models.load_inventory_document('characters_wuwainventorykamera.json', payload)

    assert document.kind == 'characters_export'
    row = document.sections[0].rows[0]
    assert row.title == 'Shorekeeper'
    assert 'Lv. 90 | Ascension 6 | Chain 2' in row.body_lines
    assert 'Weapon: Emerald of Genesis | Lv. 90 | Rank 1' in row.body_lines
    assert 'Skills: 2 entries' in row.body_lines
    assert 'Weapon: Emerald of Genesis' in row.details_lines
    assert 'Skill normal: 10' in row.details_lines
    assert 'Skill skill: 10' in row.details_lines


def test_load_inventory_document_normalizes_scan_session_sections() -> None:
    payload = {
        'date': '2026-05-28_120000',
        'cancelled': True,
        'echoes': [{'310000010': {'level': 25, 'tuneLv': 5, 'rarity': 5, 'stats': {'main': {'ATK%': '18%'}}}}],
        'achievements': ['9001'],
        'shell': {'2': 123456},
    }

    document = inventory_models.load_inventory_document('scan_result.json', payload)

    assert document.kind == 'scan_session'
    assert document.message_lines == ('Session: 2026-05-28_120000', 'Status: Cancelled')
    assert [section.title for section in document.sections] == ['Echoes', 'Achievements', 'Shell']
    assert document.sections[1].rows[0].title == 'First Steps'
    assert document.sections[2].rows[0].title == 'Shell Credit'
    assert document.sections[2].rows[0].body_lines == ('Count: 123456',)


def test_load_inventory_document_rejects_legacy_inventory() -> None:
    payload = {'2': 5000, '10800': 3}

    document = inventory_models.load_inventory_document('inventory_wuwainventorykamera.json', payload)

    assert document.kind == 'unsupported_legacy'
    assert document.sections == ()
    assert document.message_lines[0] == 'Legacy inventory files are no longer supported.'


def test_filter_section_rows_returns_all_rows_for_blank_query() -> None:
    section = inventory_models.InventorySection(
        title='Echoes',
        rows=(
            inventory_models.InventoryRow(title='Bell Borne Geochelone', subtitle='Echo ID: 310000010'),
            inventory_models.InventoryRow(title='Tempest Mephis', subtitle='Echo ID: 320000001'),
        ),
    )

    filtered = inventory_models.filter_section_rows(section, '   ')

    assert filtered == section


def test_filter_section_rows_matches_title_subtitle_and_body() -> None:
    section = inventory_models.InventorySection(
        title='Weapons',
        rows=(
            inventory_models.InventoryRow(
                title='Emerald of Genesis',
                subtitle='Weapon ID: 21010074',
                body_lines=('Lv. 90 | Max 90 | Rank 1 | Rarity 5', 'Equipped: Shorekeeper'),
            ),
            inventory_models.InventoryRow(
                title='Static Mist',
                subtitle='Weapon ID: 21010015',
                body_lines=('Lv. 80 | Max 80 | Rank 2 | Rarity 5',),
            ),
        ),
    )

    by_title = inventory_models.filter_section_rows(section, 'emerald')
    by_subtitle = inventory_models.filter_section_rows(section, '21010015')
    by_body = inventory_models.filter_section_rows(section, 'shorekeeper')

    assert [row.title for row in by_title.rows] == ['Emerald of Genesis']
    assert [row.title for row in by_subtitle.rows] == ['Static Mist']
    assert [row.title for row in by_body.rows] == ['Emerald of Genesis']


def test_load_inventory_session_prefers_scan_result(tmp_path) -> None:
    session_dir = tmp_path / '2026-05-28_120000'
    session_dir.mkdir()

    (session_dir / 'scan_result.json').write_text(
        json.dumps(
            {
                'date': '2026-05-28_120000',
                'characters': {
                    '1105': {
                        '_name': 'shorekeeper',
                        'level': 90,
                        'ascension': 6,
                        'weapon': {'id': 21010074, 'level': 90, 'rank': 1},
                        'skills': {'normal': 10},
                        'chain': 2,
                    }
                },
            }
        ),
        encoding='utf-8',
    )
    (session_dir / 'echoes_wuwainventorykamera.json').write_text(
        json.dumps([{'310000010': {'level': 25, 'tuneLv': 5, 'rarity': 5, 'stats': {'main': {'ATK%': '18%'}}}}]),
        encoding='utf-8',
    )

    document = inventory_models.load_inventory_session(session_dir)

    assert document.kind == 'scan_session'
    assert document.message_lines[0] == 'Session folder: 2026-05-28_120000'
    assert document.message_lines[1] == 'Session: 2026-05-28_120000'
    assert [section.title for section in document.sections] == ['Characters']


def test_load_inventory_session_aggregates_standalone_exports(tmp_path) -> None:
    session_dir = tmp_path / '2026-05-28_130000'
    session_dir.mkdir()

    (session_dir / 'echoes_wuwainventorykamera.json').write_text(
        json.dumps([{'310000010': {'level': 25, 'tuneLv': 5, 'rarity': 5, 'stats': {'main': {'ATK%': '18%'}}}}]),
        encoding='utf-8',
    )
    (session_dir / 'resources_wuwainventorykamera.json').write_text(
        json.dumps([{'id': 2, 'count': 321}]),
        encoding='utf-8',
    )

    document = inventory_models.load_inventory_session(session_dir)

    assert document.kind == 'scan_session'
    assert document.message_lines == ('Session folder: 2026-05-28_130000',)
    assert [section.title for section in document.sections] == ['Echoes', 'Resources']


def test_load_inventory_session_reports_missing_supported_exports(tmp_path) -> None:
    session_dir = tmp_path / 'empty_session'
    session_dir.mkdir()

    document = inventory_models.load_inventory_session(session_dir)

    assert document.kind == 'scan_session'
    assert document.sections == ()
    assert document.message_lines[-1] == 'No supported result files were found in this session folder.'