from __future__ import annotations

import logging

import numpy as np

from wuwa_inventory_kamera.scraping.service.assemblers import character_assembler as character_assembler_module
from wuwa_inventory_kamera.scraping.service.assemblers.character_assembler import CharAssembler
from wuwa_inventory_kamera.scraping.service.captures import CharCapture


def _token(x: int, y: int, text: str) -> tuple[list[list[int]], str, float]:
    return ([[x, y], [x + 1, y], [x + 1, y + 1], [x, y + 1]], text, 0.99)


def test_character_assembler_logs_resonator_name_matching(caplog, monkeypatch) -> None:
    monkeypatch.setattr(
        character_assembler_module,
        '_get_data',
        lambda: ({'jinhsi': '1205'}, {}, {}),
    )

    assembler = CharAssembler()
    capture = CharCapture(
        char_index=7,
        section=0,
        crops={
            'name': np.zeros((1, 1, 3), dtype=np.uint8),
            'level': np.zeros((1, 1, 3), dtype=np.uint8),
        },
    )

    with caplog.at_level(
        logging.DEBUG,
        logger='wuwa_inventory_kamera.scraping.service.assemblers.character_assembler',
    ):
        result = assembler.assemble(
            capture,
            [_token(0, 0, 'Jinhsi')],
            [_token(0, 0, '80')],
        )

    assert result.fields['name'] == 'jinhsi'
    assert "Character 7 — resonator name matched: 'jinhsi' -> 'jinhsi' (id='1205')" in caplog.text


def test_character_assembler_logs_weapon_name_matching(caplog, monkeypatch) -> None:
    monkeypatch.setattr(
        character_assembler_module,
        '_get_data',
        lambda: ({}, {'emeraldofgenesis': '21020064'}, {}),
    )

    assembler = CharAssembler()
    capture = CharCapture(
        char_index=7,
        section=1,
        crops={
            'weaponName': np.zeros((1, 1, 3), dtype=np.uint8),
            'weaponLevel': np.zeros((1, 1, 3), dtype=np.uint8),
            'weaponRank': np.zeros((1, 1, 3), dtype=np.uint8),
        },
    )

    with caplog.at_level(
        logging.DEBUG,
        logger='wuwa_inventory_kamera.scraping.service.assemblers.character_assembler',
    ):
        result = assembler.assemble(
            capture,
            [_token(0, 0, 'Emerald'), _token(2, 0, 'of'), _token(4, 0, 'Genesis')],
            [_token(0, 0, '70/80')],
            [_token(0, 0, '3')],
        )

    assert result.fields['weaponName'] == 'emeraldofgenesis'
    assert (
        "Character 7 — weapon name matched: 'emeraldofgenesis' -> 'emeraldofgenesis' "
        "(id='21020064')"
    ) in caplog.text


def test_character_assembler_extracts_scalar_weapon_id_from_metadata_lookup(monkeypatch) -> None:
    monkeypatch.setattr(
        character_assembler_module,
        '_get_data',
        lambda: ({}, {
            'everbrightpolestar': {
                'id': 21020076,
                'name': 'Everbright Polestar',
                'image': 'IconWeapon/T_IconWeapon21020076_UI.png',
                'rarity': 5,
            }
        }, {}),
    )

    assembler = CharAssembler()
    capture = CharCapture(
        char_index=7,
        section=1,
        crops={
            'weaponName': np.zeros((1, 1, 3), dtype=np.uint8),
            'weaponLevel': np.zeros((1, 1, 3), dtype=np.uint8),
            'weaponRank': np.zeros((1, 1, 3), dtype=np.uint8),
        },
    )

    result = assembler.assemble(
        capture,
        [_token(0, 0, 'Everbright'), _token(2, 0, 'Polestar')],
        [_token(0, 0, '90/90')],
        [_token(0, 0, '1')],
    )

    assert result.fields['weaponName'] == 'everbrightpolestar'
    assert result.fields['weaponId'] == 21020076


def test_character_assembler_parses_resonator_ascension_from_level_pair(monkeypatch) -> None:
    monkeypatch.setattr(
        character_assembler_module,
        '_get_data',
        lambda: ({'jinhsi': '1205'}, {}, {}),
    )

    assembler = CharAssembler()
    capture = CharCapture(
        char_index=7,
        section=0,
        crops={
            'name': np.zeros((1, 1, 3), dtype=np.uint8),
            'level': np.zeros((1, 1, 3), dtype=np.uint8),
        },
    )

    result = assembler.assemble(
        capture,
        [_token(0, 0, 'Jinhsi')],
        [_token(0, 0, '80/90')],
    )

    assert result.fields['level'] == 80
    assert result.fields['ascension'] == 6


def test_character_assembler_parses_passive_skill_unlock_counts(monkeypatch) -> None:
    monkeypatch.setattr(
        character_assembler_module,
        '_get_data',
        lambda: ({}, {}, {'PrefabTextItem_3963945691_Text': 'Activated'}),
    )

    assembler = CharAssembler()
    capture = CharCapture(
        char_index=3,
        section=3,
        crops={
            'skill_0': np.zeros((1, 1, 3), dtype=np.uint8),
            'passive_stats0_1': np.zeros((1, 1, 3), dtype=np.uint8),
            'passive_stats0_2': np.zeros((1, 1, 3), dtype=np.uint8),
            'passive_inherent_1': np.zeros((1, 1, 3), dtype=np.uint8),
            'passive_inherent_2': np.zeros((1, 1, 3), dtype=np.uint8),
        },
    )

    result = assembler.assemble(
        capture,
        [_token(0, 0, '6')],
        [_token(0, 0, 'Activated')],
        [_token(0, 0, 'Activated')],
        [_token(0, 0, 'Activated')],
        [_token(0, 0, 'Locked')],
    )

    assert result.fields['skills'] == {
        'skill_0': 6,
        'stats0': 2,
        'inherent': 1,
    }