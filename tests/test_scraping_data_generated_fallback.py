from __future__ import annotations

import importlib
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import wuwa_inventory_kamera.scraping.service.assemblers.item_assembler as item_assembler_module
import wuwa_inventory_kamera.scraping.data as scraping_data
import wuwa_inventory_kamera.scraping.processing.echoes_processor as echoes_processor_module
import wuwa_inventory_kamera.scraping.processing.stats_extractor as stats_extractor_module
import wuwa_inventory_kamera.scraping.scanning.achievement_workflow as achievement_workflow_module
import wuwa_inventory_kamera.game.navigation as navigation_module


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def test_scraping_data_does_not_preload_until_requested(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_json(tmp_path / 'data' / 'en' / 'items.json', {'shellcredit': {'id': 1}})

    reloaded = importlib.reload(scraping_data)

    assert reloaded.itemsID == {}

    assert item_assembler_module._get_data() == {'shellcredit': {'id': 1}}


def test_cache_specific_getter_only_loads_requested_file(tmp_path, monkeypatch, caplog) -> None:
    monkeypatch.chdir(tmp_path)
    _write_json(tmp_path / 'data' / 'en' / 'echoStats.json', {'critrate': 'cr%'})
    _write_json(
        tmp_path / 'data' / 'en' / 'definedText.json',
        {'PrefabTextItem_1547656443_Text': 'terminal localized'},
    )

    reloaded = importlib.reload(scraping_data)

    caplog.set_level(logging.INFO)

    assert reloaded.getEchoStats() == {'critrate': 'cr%'}
    assert reloaded.definedText == {}

    loading_lines = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith('Loading file:')
    ]
    assert loading_lines == [f'Loading file: {Path("data") / "en" / "echoStats.json"}']


def test_echoes_processor_get_data_only_loads_echo_name_and_sonata_files(tmp_path, monkeypatch, caplog) -> None:
    monkeypatch.chdir(tmp_path)
    _write_json(tmp_path / 'data' / 'en' / 'echoes.json', {'vanguardjunrock': 310000010})
    _write_json(tmp_path / 'data' / 'en' / 'sonataName.json', ['moonlitclouds'])
    _write_json(tmp_path / 'data' / 'en' / 'echoStats.json', {'critrate': 'cr%'})

    reloaded_data = importlib.reload(scraping_data)
    reloaded_processor = importlib.reload(echoes_processor_module)

    caplog.set_level(logging.INFO)

    echoes_lookup, sonata_keys = reloaded_processor._get_data()

    assert echoes_lookup == {'vanguardjunrock': 310000010}
    assert sonata_keys == ['moonlitclouds']
    assert reloaded_data.echoStats == {}

    loading_lines = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith('Loading file:')
    ]
    assert loading_lines == [
        f'Loading file: {Path("data") / "en" / "echoes.json"}',
        f'Loading file: {Path("data") / "en" / "sonataName.json"}',
    ]


def test_stats_extractor_loads_echo_stats_on_demand(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_json(tmp_path / 'data' / 'en' / 'echoStats.json', {'critrate': 'cr%'})

    reloaded_data = importlib.reload(scraping_data)
    reloaded_stats = importlib.reload(stats_extractor_module)

    assert reloaded_data.echoStats == {}

    assert reloaded_stats._matchStats(['crit', 'rate']) == ['critrate']
    assert reloaded_data.echoStats == {'critrate': 'cr%'}


def test_navigation_main_menu_check_loads_defined_text_on_demand(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_json(
        tmp_path / 'data' / 'en' / 'definedText.json',
        {'PrefabTextItem_1547656443_Text': 'terminal localized'},
    )

    reloaded_data = importlib.reload(scraping_data)

    monkeypatch.setattr(
        navigation_module,
        'capture_full',
        lambda *_args, **_kwargs: np.zeros((12, 12, 3), dtype=np.uint8),
    )
    monkeypatch.setattr(navigation_module, '_nav_ocr', lambda *_args, **_kwargs: 'terminal localized')

    navigator = navigation_module.GameNavigator.__new__(navigation_module.GameNavigator)
    navigator.layout = SimpleNamespace(
        width=12,
        height=12,
        monitor=1,
        terminal=SimpleNamespace(x=0, y=0, w=12, h=12),
    )
    navigator.gw = object()

    assert reloaded_data.definedText == {}
    assert navigator.is_in_main_menu() is True
    assert reloaded_data.definedText == {'PrefabTextItem_1547656443_Text': 'terminal localized'}


def test_achievement_workflow_loads_achievements_on_demand(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_json(tmp_path / 'data' / 'en' / 'achievements.json', {'first step': 1})

    reloaded_data = importlib.reload(scraping_data)
    monkeypatch.setattr(
        achievement_workflow_module,
        'capture_region',
        lambda *_args, **_kwargs: np.zeros((4, 4, 3), dtype=np.uint8),
    )

    pasted_names: list[str] = []

    class _FakeCtrl:
        def press_key(self, *_args, **_kwargs) -> None:
            return None

        def click(self, *_args, **_kwargs) -> None:
            return None

        def paste(self, text: str, **_kwargs) -> None:
            pasted_names.append(text)

    class _FakeFuture:
        def result(self, timeout: int | None = None):
            _ = timeout
            return SimpleNamespace(completed=True)

    class _FakeOcr:
        def submit(self, capture):
            assert capture.achievement_name == 'first step'
            assert capture.achievement_id == 1
            return _FakeFuture()

    achievements_layout = SimpleNamespace(
        achievementsButton=SimpleNamespace(x=0, y=0),
        achievementsTab=SimpleNamespace(x=0, y=0),
        searchBar=SimpleNamespace(x=0, y=0),
        searchButton=SimpleNamespace(x=0, y=0),
        status=SimpleNamespace(x=0, y=0, w=4, h=4),
    )
    nav = SimpleNamespace(
        layout=SimpleNamespace(achievements=achievements_layout, width=1920, height=1080, monitor=1),
        ctrl=_FakeCtrl(),
        gw=object(),
    )

    workflow = achievement_workflow_module.AchievementWorkflow(
        nav=nav,
        ocr_service=_FakeOcr(),
        session=object(),
    )

    assert reloaded_data.achievementsID == {}
    assert workflow.run() == ['1']
    assert pasted_names == ['first step']
    assert reloaded_data.achievementsID == {'first step': 1}


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
