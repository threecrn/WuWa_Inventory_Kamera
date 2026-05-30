from __future__ import annotations

from wuwa_inventory_kamera import output_serialization


def test_serialize_weapon_export_restores_v1_container_shape() -> None:
    payload = [
        {
            'id': 21010074,
            'weapon_key': 'emeraldofgenesis',
            'level': 90,
            'maxLevel': 90,
            'rank': 1,
            '_equipped': 'shorekeeper',
        }
    ]

    serialized = output_serialization.serialize_weapon_export(payload)

    assert serialized == [
        {
            '21010074': {
                'level': 90,
                'ascension': 6,
                'rank': 1,
                'weapon_key': 'emeraldofgenesis',
                'maxLevel': 90,
                '_equipped': 'shorekeeper',
            }
        }
    ]


def test_build_standalone_exports_restores_inventory_and_achievement_files() -> None:
    result = {
        'devItems': [{'id': 10800, 'item_key': 'resonancepotion', 'count': 3}],
        'shell': {'2': 123456},
        'achievements': ['9001'],
    }

    exports = output_serialization.build_standalone_exports(result)

    assert exports[output_serialization.INVENTORY_EXPORT_FILENAME] == {
        '10800': 3,
        '2': 123456,
    }
    assert exports[output_serialization.DEV_ITEMS_EXPORT_FILENAME] == [
        {'id': 10800, 'item_key': 'resonancepotion', 'count': 3}
    ]
    assert exports[output_serialization.ACHIEVEMENT_EXPORT_FILENAME] == [9001]


def test_serialize_scan_result_adds_inventory_and_converts_sections() -> None:
    result = {
        'date': '2026-05-29_12-00-00',
        'weapons': [
            {
                'id': 21010074,
                'weapon_key': 'emeraldofgenesis',
                'level': 90,
                'maxLevel': 90,
                'rank': 1,
            }
        ],
        'devItems': [{'id': 10800, 'item_key': 'resonancepotion', 'count': 3}],
        'achievements': ['9001'],
    }

    serialized = output_serialization.serialize_scan_result(result)

    assert serialized['date'] == '2026-05-29_12-00-00'
    assert serialized['inventory'] == {'10800': 3}
    assert serialized['achievements'] == [9001]
    assert serialized['devItems'] == [{'id': 10800, 'item_key': 'resonancepotion', 'count': 3}]
    assert serialized['weapons'] == [
        {
            '21010074': {
                'level': 90,
                'ascension': 6,
                'rank': 1,
                'weapon_key': 'emeraldofgenesis',
                'maxLevel': 90,
            }
        }
    ]


def test_serialize_character_export_restores_scalar_weapon_id() -> None:
    payload = {
        '1210': {
            '_name': 'aemeath',
            'character_key': 'aemeath',
            'level': 90,
            'ascension': 6,
            'weapon': {
                'id': {
                    'id': 21020076,
                    'name': 'Everbright Polestar',
                    'image': 'IconWeapon/T_IconWeapon21020076_UI.png',
                    'rarity': 5,
                },
                'weapon_key': 'everbrightpolestar',
                'level': 90,
                'ascension': 6,
                'rank': 1,
            },
            'echoes': {},
            'skills': {},
            'chain': 3,
        }
    }

    serialized = output_serialization.serialize_character_export(payload)

    assert serialized['1210']['weapon']['id'] == 21020076
    assert serialized['1210']['weapon']['weapon_key'] == 'everbrightpolestar'


def test_serialize_item_rows_reconstructs_overflow_stack_counts() -> None:
    payload = [
        {'id': 42310060, 'item_key': 'angelica', 'count': 1372},
        {'id': 42310060, 'item_key': 'angelica', 'count': 1372},
        {'id': 42400030, 'item_key': 'rawmeat', 'count': 3094},
        {'id': 42400030, 'item_key': 'rawmeat', 'count': 3094},
        {'id': 42400030, 'item_key': 'rawmeat', 'count': 3094},
        {'id': 42400030, 'item_key': 'rawmeat', 'count': 3094},
    ]

    assert output_serialization.serialize_item_rows(payload) == [
        {'id': 42310060, 'item_key': 'angelica', 'count': 999},
        {'id': 42310060, 'item_key': 'angelica', 'count': 373},
        {'id': 42400030, 'item_key': 'rawmeat', 'count': 999},
        {'id': 42400030, 'item_key': 'rawmeat', 'count': 999},
        {'id': 42400030, 'item_key': 'rawmeat', 'count': 999},
        {'id': 42400030, 'item_key': 'rawmeat', 'count': 97},
    ]


def test_serialize_inventory_export_uses_reconstructed_overflow_stack_counts() -> None:
    payload = [
        {'id': 42310060, 'item_key': 'angelica', 'count': 1372},
        {'id': 42310060, 'item_key': 'angelica', 'count': 1372},
    ]

    assert output_serialization.serialize_inventory_export(resources=payload) == {
        '42310060': 1372,
    }