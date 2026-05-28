from __future__ import annotations

import importlib
import json
from pathlib import Path

import wuwa_inventory_kamera.scraping.service.assemblers.item_assembler as item_assembler_module
import wuwa_inventory_kamera.scraping.data as scraping_data


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def test_scraping_data_does_not_preload_until_requested(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_json(tmp_path / 'data' / 'en' / 'items.json', {'shellcredit': {'id': 1}})

    reloaded = importlib.reload(scraping_data)

    assert reloaded.itemsID == {}

    assert item_assembler_module._get_data() == {'shellcredit': {'id': 1}}


def test_load_data_falls_back_to_generated_outputs_when_compat_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    _write_json(
        tmp_path / 'data' / 'catalog' / 'items.json',
        {'shellcredit': {'id': 1, 'text_key': 'ItemInfo_1_Name', 'image': 'IconA/T_IconA_hsb_UI.png'}},
    )
    _write_json(
        tmp_path / 'data' / 'catalog' / 'weapons.json',
        {'trainingbroadblade': {'id': 101, 'text_key': 'WeaponConf_1_WeaponName', 'image': 'IconWeapon/T_Weapon_UI.png', 'rarity': 4}},
    )
    _write_json(
        tmp_path / 'data' / 'catalog' / 'characters.json',
        {'iuno': {'id': 1410, 'text_key': 'RoleInfo_1410_Name'}},
    )
    _write_json(
        tmp_path / 'data' / 'catalog' / 'echoes.json',
        {'vanguardjunrock': {'id': 310000010, 'text_key': 'MonsterInfo_310000010_Name'}},
    )
    _write_json(
        tmp_path / 'data' / 'catalog' / 'achievements.json',
        {'firststep': {'id': 1, 'text_key': 'Achievement_1_Name'}},
    )
    _write_json(
        tmp_path / 'data' / 'catalog' / 'sonatas.json',
        {'moonlitclouds': {'id': 12, 'text_key': 'PhantomFetter_12_Name'}},
    )

    _write_json(
        tmp_path / 'data' / 'locale' / 'ja' / 'items.json',
        {'shellcredit': {'display_name': 'シェルコイン', 'normalized': 'シェルコイン', 'aliases': ['シェルコイン']}},
    )
    _write_json(
        tmp_path / 'data' / 'locale' / 'ja' / 'weapons.json',
        {'trainingbroadblade': {'display_name': '訓練用大剣', 'normalized': '訓練用大剣', 'aliases': ['訓練用大剣']}},
    )
    _write_json(
        tmp_path / 'data' / 'locale' / 'ja' / 'characters.json',
        {'iuno': {'display_name': 'イウノ', 'normalized': 'イウノ', 'aliases': ['イウノ', 'iuno']}},
    )
    _write_json(
        tmp_path / 'data' / 'locale' / 'ja' / 'echoes.json',
        {'vanguardjunrock': {'display_name': '先鋒岩塊', 'normalized': '先鋒岩塊', 'aliases': ['先鋒岩塊']}},
    )
    _write_json(
        tmp_path / 'data' / 'locale' / 'ja' / 'achievements.json',
        {'firststep': {'display_name': '最初の一歩', 'normalized': '最初の一歩', 'aliases': ['最初の一歩']}},
    )
    _write_json(
        tmp_path / 'data' / 'locale' / 'ja' / 'stats.json',
        {'hp': {'display_name': '体力', 'normalized': '体力', 'aliases': ['体力']}},
    )
    _write_json(
        tmp_path / 'data' / 'locale' / 'ja' / 'definedText.json',
        {'PrefabTextItem_1547656443_Text': {'display_text': '端末', 'normalized': '端末', 'aliases': ['端末']}},
    )
    _write_json(
        tmp_path / 'data' / 'locale' / 'ja' / 'sonatas.json',
        {'moonlitclouds': {'display_name': '月を窺う軽雲', 'normalized': '月を窺う軽雲', 'aliases': ['月を窺う軽雲']}},
    )

    scraping_data.loadData('ja')

    assert scraping_data.itemsID == {
        'シェルコイン': {
            'id': 1,
            'name': 'シェルコイン',
            'image': 'IconA/T_IconA_hsb_UI.png',
        },
    }
    assert scraping_data.weaponsID == {
        '訓練用大剣': {
            'id': 101,
            'name': '訓練用大剣',
            'image': 'IconWeapon/T_Weapon_UI.png',
            'rarity': 4,
        },
    }
    assert scraping_data.charactersID == {'イウノ': 1410}
    assert scraping_data.echoesID == {'先鋒岩塊': 310000010}
    assert scraping_data.achievementsID == {'最初の一歩': 1}
    assert scraping_data.echoStats == {'体力': 'hp'}
    assert scraping_data.definedText == {'PrefabTextItem_1547656443_Text': '端末'}
    assert scraping_data.sonataName == ['moonlitclouds']



def test_load_data_clears_stale_values_on_reload(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    _write_json(tmp_path / 'data' / 'en' / 'characters.json', {'alpha': 1})
    _write_json(tmp_path / 'data' / 'ja' / 'characters.json', {'beta': 2})

    scraping_data.loadData('en')
    assert scraping_data.charactersID == {'alpha': 1}

    scraping_data.loadData('ja')
    assert scraping_data.charactersID == {'beta': 2}
