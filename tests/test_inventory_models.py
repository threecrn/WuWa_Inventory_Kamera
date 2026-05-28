from __future__ import annotations

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