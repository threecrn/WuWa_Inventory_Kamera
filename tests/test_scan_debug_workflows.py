from __future__ import annotations

import concurrent.futures
import threading
from types import SimpleNamespace
from typing import Any, cast

import numpy as np

import wuwa_inventory_kamera.game.stop_signal as stop_signal_module
import wuwa_inventory_kamera.scraping.scanning.character_workflow as character_workflow_module
import wuwa_inventory_kamera.scraping.scanning.session_orchestrator as session_orchestrator_module
import wuwa_inventory_kamera.scraping.scanning.weapon_workflow as weapon_workflow_module
import wuwa_inventory_kamera.scraping.service.ocr_service as ocr_service_module
import wuwa_inventory_kamera.ui.home as home_module
from wuwa_inventory_kamera.game.navigation import GameNavigator, InventoryTab
from wuwa_inventory_kamera.game.stop_signal import StopSignal
from wuwa_inventory_kamera.scraping.scanning.character_workflow import CharacterWorkflow
from wuwa_inventory_kamera.scraping.scanning.session_orchestrator import SessionOrchestrator
from wuwa_inventory_kamera.scraping.scanning.weapon_workflow import WeaponWorkflow
from wuwa_inventory_kamera.scraping.service.captures import (
    CharResult,
    EchoCapture,
    EchoResult,
    WeaponCapture,
    WeaponResult,
)


def test_weapon_workflow_write_debug_dumps_region_artifacts(monkeypatch, tmp_path) -> None:
    image = np.arange(8 * 8 * 3, dtype=np.uint8).reshape(8, 8, 3)

    monkeypatch.setattr(weapon_workflow_module, 'capture_full', lambda *args, **kwargs: image)
    monkeypatch.setattr(WeaponWorkflow, '_save_raw', lambda self, *args, **kwargs: None)

    debug_calls: list[dict[str, object]] = []

    def _fake_write_region_debug_artifacts(debug_dir, *, basename, roi_key, raw_bgr, rarity):
        debug_calls.append({
            'debug_dir': debug_dir,
            'basename': basename,
            'roi_key': roi_key,
            'raw_bgr': raw_bgr.copy(),
            'rarity': rarity,
        })

    monkeypatch.setattr(
        'wuwa_inventory_kamera.scraping.service.shared_scan_helpers._write_region_debug_artifacts',
        _fake_write_region_debug_artifacts,
    )

    class _FakeGridNavigator:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def scan_forward(self, visitor) -> None:
            assert visitor(SimpleNamespace(scan_index=7, page=0, row=0, col=0)) is True

    monkeypatch.setattr(weapon_workflow_module, 'GridNavigator', _FakeGridNavigator)

    session = SimpleNamespace(
        total_items=1,
        sort_order=None,
        session_id='session-id',
        mark_scanned=lambda *_args, **_kwargs: None,
        mark_skipped=lambda *_args, **_kwargs: None,
        mark_failed=lambda *_args, **_kwargs: None,
    )
    layout = SimpleNamespace(
        width=8,
        height=8,
        monitor=1,
        weapons=SimpleNamespace(
            name=SimpleNamespace(x=0, y=0, w=2, h=2),
            level=SimpleNamespace(x=2, y=0, w=2, h=2),
            value=SimpleNamespace(x=4, y=0, w=2, h=2),
            rank=SimpleNamespace(x=0, y=2, w=2, h=2),
            equipped=SimpleNamespace(x=2, y=2, w=2, h=2),
            rarityColorPick=SimpleNamespace(x=1, y=1),
        ),
    )
    nav = SimpleNamespace(
        layout=layout,
        gw=None,
        switch_tab=lambda *_args, **_kwargs: None,
        set_sort_order=lambda *_args, **_kwargs: None,
        read_item_count=lambda: (1, 1),
    )

    class _FakeFuture:
        def result(self, timeout: int) -> WeaponResult:
            return WeaponResult(index=7, is_weapon=True, data={'id': 'weapon'}, below_minimum=False)

    class _FakeOcrService:
        def submit(self, capture) -> _FakeFuture:
            return _FakeFuture()

    monkeypatch.setattr(
        weapon_workflow_module,
        '_rarity_from_capture_pixel',
        lambda _pixel: (5, 'BGR', 0.0),
    )

    workflow = WeaponWorkflow(
        nav=cast(Any, nav),
        ocr_service=cast(Any, _FakeOcrService()),
        session=cast(Any, session),
        save_raw=tmp_path / 'raw',
        write_debug=True,
    )

    results = workflow.run()

    assert results == [{'id': 'weapon'}]
    assert [call['basename'] for call in debug_calls] == ['name', 'level', 'rank', 'equipped']
    assert [call['roi_key'] for call in debug_calls] == [
        'weapons.name',
        'weapons.level',
        'weapons.rank',
        'weapons.equipped',
    ]
    assert [call['rarity'] for call in debug_calls] == [5, None, None, None]
    assert all(
        call['debug_dir'] == tmp_path / 'raw' / 'weapon_0007' / 'debug'
        for call in debug_calls
    )
    np.testing.assert_array_equal(debug_calls[0]['raw_bgr'], image[0:2, 0:2])
    np.testing.assert_array_equal(debug_calls[1]['raw_bgr'], image[0:2, 2:4])
    np.testing.assert_array_equal(debug_calls[2]['raw_bgr'], image[2:4, 0:2])
    np.testing.assert_array_equal(debug_calls[3]['raw_bgr'], image[2:4, 2:4])


def test_item_workflow_write_debug_uses_item_region_artifacts(monkeypatch, tmp_path) -> None:
    image = np.arange(8 * 8 * 3, dtype=np.uint8).reshape(8, 8, 3)

    monkeypatch.setattr(weapon_workflow_module, 'capture_full', lambda *args, **kwargs: image)
    monkeypatch.setattr(WeaponWorkflow, '_save_raw', lambda self, *args, **kwargs: None)

    debug_calls: list[dict[str, object]] = []

    def _fake_write_region_debug_artifacts(debug_dir, *, basename, roi_key, raw_bgr, rarity):
        debug_calls.append({
            'debug_dir': debug_dir,
            'basename': basename,
            'roi_key': roi_key,
            'raw_bgr': raw_bgr.copy(),
            'rarity': rarity,
        })

    monkeypatch.setattr(
        'wuwa_inventory_kamera.scraping.service.shared_scan_helpers._write_region_debug_artifacts',
        _fake_write_region_debug_artifacts,
    )

    class _FakeGridNavigator:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def scan_forward(self, visitor) -> None:
            assert visitor(SimpleNamespace(scan_index=9, page=0, row=0, col=0)) is True

    monkeypatch.setattr(weapon_workflow_module, 'GridNavigator', _FakeGridNavigator)

    session = SimpleNamespace(
        total_items=1,
        sort_order=None,
        session_id='session-id',
        mark_scanned=lambda *_args, **_kwargs: None,
        mark_skipped=lambda *_args, **_kwargs: None,
        mark_failed=lambda *_args, **_kwargs: None,
    )
    layout = SimpleNamespace(
        width=8,
        height=8,
        monitor=1,
        items=SimpleNamespace(
            name=SimpleNamespace(x=0, y=0, w=2, h=2),
            value=SimpleNamespace(x=2, y=0, w=2, h=2),
            rarityColorPick=SimpleNamespace(x=1, y=1),
        ),
        weapons=SimpleNamespace(
            name=SimpleNamespace(x=4, y=4, w=2, h=2),
            level=SimpleNamespace(x=6, y=4, w=2, h=2),
            rank=SimpleNamespace(x=4, y=6, w=2, h=2),
            equipped=SimpleNamespace(x=6, y=6, w=2, h=2),
        ),
    )
    nav = SimpleNamespace(
        layout=layout,
        gw=None,
        switch_tab=lambda *_args, **_kwargs: None,
        set_sort_order=lambda *_args, **_kwargs: None,
        read_item_count=lambda: (1, 1),
    )

    class _FakeFuture:
        def result(self, timeout: int) -> WeaponResult:
            return WeaponResult(index=9, is_weapon=False, data={'id': 'item'}, below_minimum=False)

    class _FakeOcrService:
        def submit(self, capture) -> _FakeFuture:
            return _FakeFuture()

    monkeypatch.setattr(
        weapon_workflow_module,
        '_rarity_from_capture_pixel',
        lambda _pixel: (4, 'BGR', 0.0),
    )

    workflow = WeaponWorkflow(
        nav=cast(Any, nav),
        ocr_service=cast(Any, _FakeOcrService()),
        session=cast(Any, session),
        tab=InventoryTab.DEV_ITEMS,
        save_raw=tmp_path / 'raw',
        write_debug=True,
    )

    results = workflow.run()

    assert results == [{'id': 'item'}]
    assert [call['basename'] for call in debug_calls] == ['name', 'value']
    assert [call['roi_key'] for call in debug_calls] == ['items.name', 'items.value']
    assert [call['rarity'] for call in debug_calls] == [4, None]
    np.testing.assert_array_equal(debug_calls[0]['raw_bgr'], image[0:2, 0:2])
    np.testing.assert_array_equal(debug_calls[1]['raw_bgr'], image[0:2, 2:4])


def test_ocr_service_uses_level_spec_for_weapons_and_item_specs_for_items() -> None:
    service = cast(Any, ocr_service_module.OcrService.__new__(ocr_service_module.OcrService))
    spec_calls: list[tuple[str, int, int | None]] = []
    assemble_calls: list[dict[str, object]] = []
    image = np.zeros((2, 2, 3), dtype=np.uint8)

    def _fake_ocr_with_spec(
        roi_key: str,
        images: list[np.ndarray],
        rarity: int | None = None,
    ) -> list[list[tuple[str, float, np.ndarray]]]:
        spec_calls.append((roi_key, len(images), rarity))
        return [[(roi_key, 1.0, np.array([0, 0, 1, 1]))] for _ in images]

    class _FakeBatchOcr:
        def ocr_images(self, images: list[np.ndarray]) -> list[list[tuple[str, float, np.ndarray]]]:
            return [[('1', 1.0, np.array([0, 0, 1, 1]))] for _ in images]

    class _FakeWeaponAssembler:
        def assemble(self, capture, name_tokens, value_tokens, rank_tokens, equipped_tokens=None):
            assemble_calls.append({
                'index': capture.index,
                'name_texts': [tok[1] for tok in name_tokens],
                'value_texts': [tok[1] for tok in value_tokens],
                'rank_tokens': rank_tokens,
                'equipped_texts': [tok[1] for tok in equipped_tokens] if equipped_tokens else [],
            })
            return WeaponResult(
                index=capture.index,
                is_weapon=capture.rank is not None,
                data={'id': f'capture-{capture.index}'},
                below_minimum=False,
            )

    service._ocr_with_spec = _fake_ocr_with_spec
    service._batch_ocr = _FakeBatchOcr()
    service._weapon_asm = _FakeWeaponAssembler()

    group = [
        ocr_service_module._QueueItem(
            WeaponCapture(index=1, name=image, value=image, rank=image, equipped=image, detected_rarity=5),
            0,
            concurrent.futures.Future(),
        ),
        ocr_service_module._QueueItem(
            WeaponCapture(index=2, name=image, value=image, rank=None, detected_rarity=4),
            1,
            concurrent.futures.Future(),
        ),
    ]

    service._process_weapons(group)

    assert spec_calls == [
        ('weapons.name', 1, 5),
        ('items.name', 1, 4),
        ('weapons.level', 1, None),
        ('items.value', 1, None),
        ('weapons.equipped', 1, None),
    ]
    assert assemble_calls[0]['name_texts'] == ['weapons.name']
    assert assemble_calls[1]['name_texts'] == ['items.name']
    assert assemble_calls[0]['value_texts'] == ['weapons.level']
    assert assemble_calls[1]['value_texts'] == ['items.value']
    assert assemble_calls[0]['equipped_texts'] == ['weapons.equipped']
    assert assemble_calls[1]['equipped_texts'] == []
    assert group[0].future.result().is_weapon is True
    assert group[1].future.result().is_weapon is False


def test_game_navigator_uses_item_page_roi_for_item_tabs() -> None:
    items_page = SimpleNamespace(x=1, y=2, w=3, h=4)
    weapons_page = SimpleNamespace(x=5, y=6, w=7, h=8)

    navigator = GameNavigator.__new__(GameNavigator)
    navigator.layout = SimpleNamespace(
        items=SimpleNamespace(page=items_page),
        weapons=SimpleNamespace(page=weapons_page),
    )

    navigator._current_tab = InventoryTab.DEV_ITEMS
    assert navigator._page_count_roi() is items_page

    navigator._current_tab = InventoryTab.RESOURCES
    assert navigator._page_count_roi() is items_page

    navigator._current_tab = InventoryTab.WEAPONS
    assert navigator._page_count_roi() is weapons_page


def test_ocr_service_passes_equipped_tokens_for_echoes() -> None:
    service = cast(Any, ocr_service_module.OcrService.__new__(ocr_service_module.OcrService))
    spec_calls: list[tuple[str, int, int | None]] = []
    assemble_calls: list[dict[str, object]] = []
    image = np.zeros((2, 2, 3), dtype=np.uint8)
    box = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=np.float32)

    def _fake_ocr_with_spec(
        roi_key: str,
        images: list[np.ndarray],
        rarity: int | None = None,
    ) -> list[list[tuple[str, float, np.ndarray]]]:
        spec_calls.append((roi_key, len(images), rarity))
        return [[(roi_key, 1.0, box)] for _ in images]

    class _FakeBatchOcr:
        def ocr_images(self, images: list[np.ndarray]) -> list[list[tuple[str, float, np.ndarray]]]:
            return [[('echo-card', 1.0, box)] for _ in images]

    class _FakeOcrCache:
        def lookup(self, *_args, **_kwargs):
            return None

        def store(self, *_args, **_kwargs) -> None:
            return None

    class _FakeEchoAssembler:
        def assemble(self, capture, card_tokens, name_tokens, value_tokens, equipped_tokens=None):
            assemble_calls.append({
                'index': capture.echo_index,
                'card_texts': [tok[1] for tok in card_tokens],
                'name_texts': [tok[1] for tok in name_tokens],
                'value_texts': [tok[1] for tok in value_tokens],
                'equipped_texts': [tok[1] for tok in equipped_tokens] if equipped_tokens else [],
            })
            return EchoResult(
                echo_index=capture.echo_index,
                data={'echo': {'id': capture.echo_index}},
                warnings=[],
                retried=False,
                detected_level=0,
            )

    service._ocr_with_spec = _fake_ocr_with_spec
    service._batch_ocr = _FakeBatchOcr()
    service._ocr_cache = _FakeOcrCache()
    service._backend = SimpleNamespace(
        recognize_single_line=lambda _image: [],
        recognize=lambda _image: [],
        thorough_recognize=lambda _image: [],
    )
    service._echo_asm = _FakeEchoAssembler()

    group = [
        ocr_service_module._QueueItem(
            EchoCapture(
                echo_index=1,
                card=image,
                echo_name=image,
                level=image,
                stats_name=image,
                stats_value=image,
                equipped=image,
                detected_level=25,
            ),
            0,
            concurrent.futures.Future(),
        ),
        ocr_service_module._QueueItem(
            EchoCapture(
                echo_index=2,
                card=image,
                echo_name=image,
                level=image,
                stats_name=image,
                stats_value=image,
                detected_level=8,
            ),
            1,
            concurrent.futures.Future(),
        ),
    ]

    service._process_echoes(group)

    assert spec_calls == [
        ('echoes.fullStatsName', 2, None),
        ('echoes.fullStatsValue', 2, None),
        ('echoes.equipped', 1, None),
    ]
    assert assemble_calls[0]['card_texts'] == ['echo-card']
    assert assemble_calls[0]['name_texts'] == ['echoes.fullStatsName']
    assert assemble_calls[0]['value_texts'] == ['echoes.fullStatsValue']
    assert assemble_calls[0]['equipped_texts'] == ['echoes.equipped']
    assert assemble_calls[1]['equipped_texts'] == []
    assert group[0].future.result().echo_index == 1
    assert group[1].future.result().echo_index == 2


def test_ocr_service_recovers_missing_detected_level_before_echo_assembly() -> None:
    service = cast(Any, ocr_service_module.OcrService.__new__(ocr_service_module.OcrService))
    image = np.zeros((2, 2, 3), dtype=np.uint8)
    box = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=np.float32)
    resolved_levels: list[int] = []

    service._ocr_with_spec = lambda _roi_key, images, rarity=None: [
        [('echoes.fullStats', 1.0, box)] for _ in images
    ]
    service._batch_ocr = SimpleNamespace(
        ocr_images=lambda images: [[('echo-card', 1.0, box)] for _ in images]
    )
    service._ocr_cache = SimpleNamespace(
        lookup=lambda *_args, **_kwargs: None,
        store=lambda *_args, **_kwargs: None,
    )
    service._backend = SimpleNamespace(
        recognize_single_line=lambda _image: [],
        recognize=lambda _image: [],
        thorough_recognize=lambda _image: [],
    )
    service._resolve_echo_level = lambda capture: 17

    class _RecordingEchoAssembler:
        def assemble(self, capture, card_tokens, name_tokens, value_tokens, equipped_tokens=None):
            resolved_levels.append(capture.detected_level)
            return EchoResult(
                echo_index=capture.echo_index,
                data={'echo': {'id': capture.echo_index}},
                warnings=[],
                retried=False,
                detected_level=capture.detected_level,
            )

    service._echo_asm = _RecordingEchoAssembler()

    future = concurrent.futures.Future()
    group = [
        ocr_service_module._QueueItem(
            EchoCapture(
                echo_index=3,
                card=image,
                echo_name=image,
                level=image,
                stats_name=image,
                stats_value=image,
            ),
            0,
            future,
        ),
    ]

    service._process_echoes(group)

    assert resolved_levels == [17]
    assert future.result().detected_level == 17


def test_ocr_service_rejects_echo_when_dedicated_level_recovery_fails() -> None:
    service = cast(Any, ocr_service_module.OcrService.__new__(ocr_service_module.OcrService))
    image = np.zeros((2, 2, 3), dtype=np.uint8)
    box = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=np.float32)
    assembler_calls: list[int] = []

    service._ocr_with_spec = lambda _roi_key, images, rarity=None: [
        [('echoes.fullStats', 1.0, box)] for _ in images
    ]
    service._batch_ocr = SimpleNamespace(
        ocr_images=lambda images: [[('echo-card', 1.0, box)] for _ in images]
    )
    service._ocr_cache = SimpleNamespace(
        lookup=lambda *_args, **_kwargs: None,
        store=lambda *_args, **_kwargs: None,
    )
    service._backend = SimpleNamespace(
        recognize_single_line=lambda _image: [],
        recognize=lambda _image: [],
        thorough_recognize=lambda _image: [],
    )
    service._resolve_echo_level = lambda capture: None

    class _FailingIfCalledAssembler:
        def assemble(self, capture, card_tokens, name_tokens, value_tokens, equipped_tokens=None):
            assembler_calls.append(capture.echo_index)
            raise AssertionError('assembler should not be called when level recovery fails')

    service._echo_asm = _FailingIfCalledAssembler()

    future = concurrent.futures.Future()
    group = [
        ocr_service_module._QueueItem(
            EchoCapture(
                echo_index=4,
                card=image,
                echo_name=image,
                level=image,
                stats_name=image,
                stats_value=image,
            ),
            0,
            future,
        ),
    ]

    service._process_echoes(group)

    result = future.result()
    assert assembler_calls == []
    assert result.data is None
    assert result.detected_level == 0
    assert result.warnings == [
        'Dedicated level ROI OCR returned no digits; echo rejected before assembly.'
    ]


def test_character_workflow_write_debug_dumps_section_artifacts(monkeypatch, tmp_path) -> None:
    image = np.arange(8 * 8 * 3, dtype=np.uint8).reshape(8, 8, 3)

    monkeypatch.setattr(character_workflow_module, 'capture_full', lambda *args, **kwargs: image)
    monkeypatch.setattr(
        character_workflow_module,
        'capture_region',
        lambda _gw, roi: image[
            int(roi.y): int(roi.y + roi.h),
            int(roi.x): int(roi.x + roi.w),
        ],
    )
    monkeypatch.setattr(CharacterWorkflow, '_save_raw', lambda self, *args, **kwargs: None)

    debug_calls: list[dict[str, object]] = []

    def _fake_write_region_debug_artifacts(debug_dir, *, basename, roi_key, raw_bgr, rarity):
        debug_calls.append({
            'debug_dir': debug_dir,
            'basename': basename,
            'roi_key': roi_key,
            'raw_bgr': raw_bgr.copy(),
            'rarity': rarity,
        })

    monkeypatch.setattr(
        'wuwa_inventory_kamera.scraping.service.shared_scan_helpers._write_region_debug_artifacts',
        _fake_write_region_debug_artifacts,
    )

    layout = SimpleNamespace(
        width=8,
        height=8,
        monitor=1,
        characters=SimpleNamespace(
            rightSide=SimpleNamespace(x=0, y=0),
            leftSide=SimpleNamespace(x=0, y=0),
            offsets=SimpleNamespace(
                rightSide=SimpleNamespace(y=1),
                leftSide=SimpleNamespace(y=1),
                skillPosition=SimpleNamespace(y=1),
            ),
            resonatorName=SimpleNamespace(x=0, y=0, w=2, h=2),
            resonatorLevel=SimpleNamespace(x=2, y=0, w=2, h=2),
            weaponName=SimpleNamespace(x=0, y=2, w=2, h=2),
            weaponLevel=SimpleNamespace(x=2, y=2, w=2, h=2),
            weaponRank=SimpleNamespace(x=4, y=2, w=2, h=2),
            skillClick=SimpleNamespace(x=0, y=0),
            skillPositions=[SimpleNamespace(x=i, y=2) for i in range(5)],
            skillLevel=SimpleNamespace(x=0, y=4, w=2, h=2),
            skillButton=SimpleNamespace(x=2, y=4, w=2, h=2),
            chainClick=SimpleNamespace(x=0, y=0),
            chainPositions=[SimpleNamespace(x=i, y=0) for i in range(6)],
            chainButton=SimpleNamespace(x=2, y=4, w=2, h=2),
        ),
    )
    ctrl = SimpleNamespace(
        press_key=lambda *_args, **_kwargs: None,
        click=lambda *_args, **_kwargs: None,
        scroll=lambda *_args, **_kwargs: None,
    )
    nav = SimpleNamespace(
        layout=layout,
        ctrl=ctrl,
        gw=None,
        scroll_character_list=lambda *_args, **_kwargs: None,
    )

    class _FakeFuture:
        def __init__(self, result: CharResult) -> None:
            self._result = result

        def result(self, timeout: int) -> CharResult:
            return self._result

    class _FakeOcrService:
        def __init__(self) -> None:
            self._overview_calls = 0

        def submit(self, capture) -> _FakeFuture:
            if capture.section == 0:
                self._overview_calls += 1
                fields = {
                    'already_seen': self._overview_calls > 1,
                    'name': 'alpha',
                    'char_id': 'alpha',
                    'level': 1,
                }
            elif capture.section == 1:
                fields = {
                    'weaponName': 'sword',
                    'weaponId': 'weapon',
                    'weaponLevel': 10,
                    'weaponMaxLevel': 20,
                    'weaponRank': 1,
                }
            elif capture.section == 3:
                fields = {'skills': {key: 1 for key in capture.crops}}
            else:
                fields = {
                    'name': 'alpha',
                    'char_id': 'alpha',
                    'level': 1,
                    'weaponName': 'sword',
                    'weaponId': 'weapon',
                    'weaponLevel': 10,
                    'weaponMaxLevel': 20,
                    'weaponRank': 1,
                    'skills': {f'skill_{idx}': 1 for idx in range(5)},
                    'chain': {f'chain_{idx}': False for idx in range(6)},
                }
            return _FakeFuture(CharResult(capture.char_index, capture.section, fields))

    workflow = CharacterWorkflow(
        nav=cast(Any, nav),
        ocr_service=cast(Any, _FakeOcrService()),
        session=cast(Any, SimpleNamespace(session_id='session-id')),
        save_raw=tmp_path / 'raw',
        write_debug=True,
    )

    results = workflow.run()

    assert list(results) == ['alpha']
    assert results['alpha']['_name'] == 'alpha'
    section_3_debug_calls = [
        call
        for call in debug_calls
        if call['debug_dir'] == tmp_path / 'raw' / 'char_0000' / 'section_3' / 'debug'
    ]
    assert len(section_3_debug_calls) == 15
    assert {call['roi_key'] for call in section_3_debug_calls} == {
        'characters.skill_0',
        'characters.skill_1',
        'characters.skill_2',
        'characters.skill_3',
        'characters.skill_4',
        'characters.skillButton',
    }
    assert {call['basename'] for call in section_3_debug_calls} == {
        'skill_0',
        'passive_stats0_1',
        'passive_stats0_2',
        'skill_1',
        'passive_stats1_1',
        'passive_stats1_2',
        'skill_2',
        'passive_inherent_1',
        'passive_inherent_2',
        'skill_3',
        'passive_stats3_1',
        'passive_stats3_2',
        'skill_4',
        'passive_stats4_1',
        'passive_stats4_2',
    }
    assert {call['roi_key'] for call in debug_calls} >= {
        'characters.resonatorName',
        'characters.resonatorLevel',
        'characters.weaponName',
        'characters.weaponLevel',
        'characters.weaponRank',
        'characters.skill_0',
        'characters.skill_1',
        'characters.skill_2',
        'characters.skill_3',
        'characters.skill_4',
        'characters.skillButton',
        'characters.chain_0',
        'characters.chain_1',
        'characters.chain_2',
        'characters.chain_3',
        'characters.chain_4',
        'characters.chain_5',
    }
    assert {call['debug_dir'] for call in debug_calls} >= {
        tmp_path / 'raw' / 'char_0000' / 'section_0' / 'debug',
        tmp_path / 'raw' / 'char_0000' / 'section_1' / 'debug',
        tmp_path / 'raw' / 'char_0000' / 'section_3' / 'debug',
        tmp_path / 'raw' / 'char_0000' / 'section_4' / 'debug',
    }


def test_character_workflow_resets_to_overview_before_next_character(monkeypatch) -> None:
    image = np.arange(8 * 8 * 3, dtype=np.uint8).reshape(8, 8, 3)

    monkeypatch.setattr(character_workflow_module, 'capture_full', lambda *args, **kwargs: image)
    monkeypatch.setattr(
        character_workflow_module,
        'capture_region',
        lambda _gw, roi: image[
            int(roi.y): int(roi.y + roi.h),
            int(roi.x): int(roi.x + roi.w),
        ],
    )

    layout = SimpleNamespace(
        width=8,
        height=8,
        monitor=1,
        characters=SimpleNamespace(
            rightSide=SimpleNamespace(x=0, y=0),
            leftSide=SimpleNamespace(x=0, y=0),
            offsets=SimpleNamespace(
                rightSide=SimpleNamespace(y=1),
                leftSide=SimpleNamespace(y=1),
            ),
            resonatorName=SimpleNamespace(x=0, y=0, w=2, h=2),
            resonatorLevel=SimpleNamespace(x=2, y=0, w=2, h=2),
            weaponName=SimpleNamespace(x=0, y=2, w=2, h=2),
            weaponLevel=SimpleNamespace(x=2, y=2, w=2, h=2),
            weaponRank=SimpleNamespace(x=4, y=2, w=2, h=2),
            skillClick=SimpleNamespace(x=0, y=0),
            skillPositions=[SimpleNamespace(x=i, y=0) for i in range(5)],
            skillLevel=SimpleNamespace(x=0, y=4, w=2, h=2),
            chainClick=SimpleNamespace(x=0, y=0),
            chainPositions=[SimpleNamespace(x=i, y=0) for i in range(6)],
            chainButton=SimpleNamespace(x=2, y=4, w=2, h=2),
        ),
    )

    click_calls: list[tuple[int, int, float | None]] = []

    class _RecordedCtrl:
        def press_key(self, *_args, **_kwargs) -> None:
            pass

        def click(self, x: int, y: int, wait: float | None = None) -> None:
            click_calls.append((x, y, wait))

        def scroll(self, *_args, **_kwargs) -> None:
            pass

        def scroll(self, *_args, **_kwargs) -> None:
            pass

    nav = SimpleNamespace(
        layout=layout,
        ctrl=_RecordedCtrl(),
        gw=None,
        scroll_character_list=lambda *_args, **_kwargs: None,
    )

    class _FakeFuture:
        def __init__(self, result: CharResult) -> None:
            self._result = result

        def result(self, timeout: int) -> CharResult:
            return self._result

    class _FakeOcrService:
        def __init__(self) -> None:
            self._overview_calls = 0

        def submit(self, capture) -> _FakeFuture:
            if capture.section == 0:
                self._overview_calls += 1
                fields = {
                    'already_seen': self._overview_calls > 1,
                    'name': 'alpha',
                    'char_id': 'alpha',
                    'level': 1,
                }
            elif capture.section == 1:
                fields = {
                    'weaponName': 'sword',
                    'weaponId': 'weapon',
                    'weaponLevel': 10,
                    'weaponMaxLevel': 20,
                    'weaponRank': 1,
                }
            elif capture.section == 3:
                fields = {'skills': {key: 1 for key in capture.crops}}
            else:
                fields = {
                    'name': 'alpha',
                    'char_id': 'alpha',
                    'level': 1,
                    'weaponName': 'sword',
                    'weaponId': 'weapon',
                    'weaponLevel': 10,
                    'weaponMaxLevel': 20,
                    'weaponRank': 1,
                    'skills': {f'skill_{idx}': 1 for idx in range(5)},
                    'chain': {f'chain_{idx}': False for idx in range(6)},
                }
            return _FakeFuture(CharResult(capture.char_index, capture.section, fields))

    workflow = CharacterWorkflow(
        nav=cast(Any, nav),
        ocr_service=cast(Any, _FakeOcrService()),
        session=cast(Any, SimpleNamespace(session_id='session-id')),
    )

    results = workflow.run()

    assert list(results) == ['alpha']
    second_slot_click = click_calls.index((0, 1, 0.7))
    assert click_calls[second_slot_click + 1] == (0, 0, 0.8)


def test_character_workflow_waits_longer_before_first_chain_capture(monkeypatch) -> None:
    image = np.arange(8 * 8 * 3, dtype=np.uint8).reshape(8, 8, 3)

    monkeypatch.setattr(character_workflow_module, 'capture_full', lambda *args, **kwargs: image)
    monkeypatch.setattr(
        character_workflow_module,
        'capture_region',
        lambda _gw, roi: image[
            int(roi.y): int(roi.y + roi.h),
            int(roi.x): int(roi.x + roi.w),
        ],
    )

    layout = SimpleNamespace(
        width=8,
        height=8,
        monitor=1,
        characters=SimpleNamespace(
            rightSide=SimpleNamespace(x=10, y=20),
            leftSide=SimpleNamespace(x=30, y=40),
            offsets=SimpleNamespace(
                rightSide=SimpleNamespace(y=1),
                leftSide=SimpleNamespace(y=1),
            ),
            resonatorName=SimpleNamespace(x=0, y=0, w=2, h=2),
            resonatorLevel=SimpleNamespace(x=2, y=0, w=2, h=2),
            weaponName=SimpleNamespace(x=0, y=2, w=2, h=2),
            weaponLevel=SimpleNamespace(x=2, y=2, w=2, h=2),
            weaponRank=SimpleNamespace(x=4, y=2, w=2, h=2),
            skillClick=SimpleNamespace(x=50, y=60),
            skillPositions=[SimpleNamespace(x=70 + i, y=80) for i in range(5)],
            skillLevel=SimpleNamespace(x=0, y=4, w=2, h=2),
            chainClick=SimpleNamespace(x=90, y=100),
            chainPositions=[SimpleNamespace(x=110 + i, y=120) for i in range(6)],
            chainButton=SimpleNamespace(x=2, y=4, w=2, h=2),
        ),
    )

    click_calls: list[tuple[int, int, float | None]] = []

    class _RecordedCtrl:
        def press_key(self, *_args, **_kwargs) -> None:
            pass

        def click(self, x: int, y: int, wait: float | None = None) -> None:
            click_calls.append((x, y, wait))

        def scroll(self, *_args, **_kwargs) -> None:
            pass

    nav = SimpleNamespace(
        layout=layout,
        ctrl=_RecordedCtrl(),
        gw=None,
        scroll_character_list=lambda *_args, **_kwargs: None,
    )

    class _FakeFuture:
        def __init__(self, result: CharResult) -> None:
            self._result = result

        def result(self, timeout: int) -> CharResult:
            return self._result

    class _FakeOcrService:
        def __init__(self) -> None:
            self._overview_calls = 0

        def submit(self, capture) -> _FakeFuture:
            if capture.section == 0:
                self._overview_calls += 1
                fields = {
                    'already_seen': self._overview_calls > 1,
                    'name': 'alpha',
                    'char_id': 'alpha',
                    'level': 1,
                }
            elif capture.section == 1:
                fields = {
                    'weaponName': 'sword',
                    'weaponId': 'weapon',
                    'weaponLevel': 10,
                    'weaponMaxLevel': 20,
                    'weaponRank': 1,
                }
            elif capture.section == 3:
                fields = {'skills': {key: 1 for key in capture.crops}}
            else:
                fields = {
                    'name': 'alpha',
                    'char_id': 'alpha',
                    'level': 1,
                    'weaponName': 'sword',
                    'weaponId': 'weapon',
                    'weaponLevel': 10,
                    'weaponMaxLevel': 20,
                    'weaponRank': 1,
                    'skills': {f'skill_{idx}': 1 for idx in range(5)},
                    'chain': {f'chain_{idx}': False for idx in range(6)},
                }
            return _FakeFuture(CharResult(capture.char_index, capture.section, fields))

    workflow = CharacterWorkflow(
        nav=cast(Any, nav),
        ocr_service=cast(Any, _FakeOcrService()),
        session=cast(Any, SimpleNamespace(session_id='session-id')),
    )

    results = workflow.run()

    assert list(results) == ['alpha']
    chain_clicks = [call for call in click_calls if call[0] >= 110]
    assert chain_clicks == [
        (110, 120, 0.35),
        (111, 120, 0.2),
        (112, 120, 0.2),
        (113, 120, 0.2),
        (114, 120, 0.2),
        (115, 120, 0.2),
    ]


def test_character_workflow_scans_passive_skill_unlock_buttons(monkeypatch) -> None:
    image = np.arange(8 * 8 * 3, dtype=np.uint8).reshape(8, 8, 3)

    monkeypatch.setattr(character_workflow_module, 'capture_full', lambda *args, **kwargs: image)
    monkeypatch.setattr(
        character_workflow_module,
        'capture_region',
        lambda _gw, roi: image[
            int(roi.y): int(roi.y + roi.h),
            int(roi.x): int(roi.x + roi.w),
        ],
    )

    layout = SimpleNamespace(
        width=8,
        height=8,
        monitor=1,
        characters=SimpleNamespace(
            rightSide=SimpleNamespace(x=10, y=20),
            leftSide=SimpleNamespace(x=30, y=40),
            offsets=SimpleNamespace(
                rightSide=SimpleNamespace(y=1),
                leftSide=SimpleNamespace(y=1),
                skillPosition=SimpleNamespace(y=10),
            ),
            resonatorName=SimpleNamespace(x=0, y=0, w=2, h=2),
            resonatorLevel=SimpleNamespace(x=2, y=0, w=2, h=2),
            weaponName=SimpleNamespace(x=0, y=2, w=2, h=2),
            weaponLevel=SimpleNamespace(x=2, y=2, w=2, h=2),
            weaponRank=SimpleNamespace(x=4, y=2, w=2, h=2),
            skillClick=SimpleNamespace(x=50, y=60),
            skillPositions=[SimpleNamespace(x=70 + i, y=80) for i in range(5)],
            skillLevel=SimpleNamespace(x=0, y=4, w=2, h=2),
            skillButton=SimpleNamespace(x=2, y=4, w=2, h=2),
            chainClick=SimpleNamespace(x=90, y=100),
            chainPositions=[SimpleNamespace(x=110 + i, y=120) for i in range(6)],
            chainButton=SimpleNamespace(x=2, y=4, w=2, h=2),
        ),
    )

    click_calls: list[tuple[int, int, float | None]] = []

    class _RecordedCtrl:
        def press_key(self, *_args, **_kwargs) -> None:
            pass

        def click(self, x: int, y: int, wait: float | None = None) -> None:
            click_calls.append((x, y, wait))

        def scroll(self, *_args, **_kwargs) -> None:
            pass

    nav = SimpleNamespace(
        layout=layout,
        ctrl=_RecordedCtrl(),
        gw=None,
        scroll_character_list=lambda *_args, **_kwargs: None,
    )

    class _FakeFuture:
        def __init__(self, result: CharResult) -> None:
            self._result = result

        def result(self, timeout: int) -> CharResult:
            return self._result

    class _FakeOcrService:
        def __init__(self) -> None:
            self._overview_calls = 0
            self.submitted = []

        def submit(self, capture) -> _FakeFuture:
            self.submitted.append(capture)
            if capture.section == 0:
                self._overview_calls += 1
                fields = {
                    'already_seen': self._overview_calls > 1,
                    'name': 'alpha',
                    'char_id': 'alpha',
                    'level': 1,
                }
            elif capture.section == 1:
                fields = {
                    'weaponName': 'sword',
                    'weaponId': 'weapon',
                    'weaponLevel': 10,
                    'weaponMaxLevel': 20,
                    'weaponRank': 1,
                }
            elif capture.section == 3:
                fields = {'skills': {key: 1 for key in capture.crops}}
            else:
                fields = {
                    'name': 'alpha',
                    'char_id': 'alpha',
                    'level': 1,
                    'weaponName': 'sword',
                    'weaponId': 'weapon',
                    'weaponLevel': 10,
                    'weaponMaxLevel': 20,
                    'weaponRank': 1,
                    'skills': {
                        'skill_0': 1,
                        'skill_1': 1,
                        'skill_2': 1,
                        'skill_3': 1,
                        'skill_4': 1,
                        'stats0': 2,
                        'stats1': 2,
                        'inherent': 1,
                        'stats3': 0,
                        'stats4': 1,
                    },
                    'chain': {f'chain_{idx}': False for idx in range(6)},
                }
            return _FakeFuture(CharResult(capture.char_index, capture.section, fields))

    ocr_service = _FakeOcrService()
    workflow = CharacterWorkflow(
        nav=cast(Any, nav),
        ocr_service=cast(Any, ocr_service),
        session=cast(Any, SimpleNamespace(session_id='session-id')),
    )

    results = workflow.run()

    assert list(results) == ['alpha']
    assert results['alpha']['skills']['stats0'] == 2
    assert results['alpha']['skills']['inherent'] == 1

    section_3_capture = next(capture for capture in ocr_service.submitted if capture.section == 3)
    assert list(section_3_capture.crops) == [
        'skill_0',
        'passive_stats0_1',
        'passive_stats0_2',
        'skill_1',
        'passive_stats1_1',
        'passive_stats1_2',
        'skill_2',
        'passive_inherent_1',
        'passive_inherent_2',
        'skill_3',
        'passive_stats3_1',
        'passive_stats3_2',
        'skill_4',
        'passive_stats4_1',
        'passive_stats4_2',
    ]

    skill_tree_clicks = [call for call in click_calls if 70 <= call[0] <= 74]
    assert skill_tree_clicks[:6] == [
        (70, 80, character_workflow_module._SKILL_NODE_CAPTURE_WAIT_SECONDS),
        (70, 70, character_workflow_module._PASSIVE_SKILL_CAPTURE_WAIT_SECONDS),
        (70, 60, character_workflow_module._PASSIVE_SKILL_CAPTURE_WAIT_SECONDS),
        (71, 80, character_workflow_module._SKILL_NODE_CAPTURE_WAIT_SECONDS),
        (71, 70, character_workflow_module._PASSIVE_SKILL_CAPTURE_WAIT_SECONDS),
        (71, 60, character_workflow_module._PASSIVE_SKILL_CAPTURE_WAIT_SECONDS),
    ]
    assert len(skill_tree_clicks) == 15


def test_character_workflow_selects_first_character_after_opening_panel(monkeypatch) -> None:
    image = np.arange(8 * 8 * 3, dtype=np.uint8).reshape(8, 8, 3)

    monkeypatch.setattr(character_workflow_module, 'capture_full', lambda *args, **kwargs: image)
    monkeypatch.setattr(
        character_workflow_module,
        'capture_region',
        lambda _gw, roi: image[
            int(roi.y): int(roi.y + roi.h),
            int(roi.x): int(roi.x + roi.w),
        ],
    )

    layout = SimpleNamespace(
        width=8,
        height=8,
        monitor=1,
        characters=SimpleNamespace(
            rightSide=SimpleNamespace(x=10, y=20),
            leftSide=SimpleNamespace(x=1, y=2),
            offsets=SimpleNamespace(
                rightSide=SimpleNamespace(y=1),
                leftSide=SimpleNamespace(y=1),
            ),
            resonatorName=SimpleNamespace(x=0, y=0, w=2, h=2),
            resonatorLevel=SimpleNamespace(x=2, y=0, w=2, h=2),
            weaponName=SimpleNamespace(x=0, y=2, w=2, h=2),
            weaponLevel=SimpleNamespace(x=2, y=2, w=2, h=2),
            weaponRank=SimpleNamespace(x=4, y=2, w=2, h=2),
            skillClick=SimpleNamespace(x=0, y=0),
            skillPositions=[SimpleNamespace(x=i, y=0) for i in range(5)],
            skillLevel=SimpleNamespace(x=0, y=4, w=2, h=2),
            chainClick=SimpleNamespace(x=0, y=0),
            chainPositions=[SimpleNamespace(x=i, y=0) for i in range(6)],
            chainButton=SimpleNamespace(x=2, y=4, w=2, h=2),
        ),
    )

    events: list[tuple[str, object, object | None, float | None]] = []

    class _RecordedCtrl:
        def press_key(self, key: str, wait: float | None = None) -> None:
            events.append(('press_key', key, None, wait))

        def click(self, x: int, y: int, wait: float | None = None) -> None:
            events.append(('click', x, y, wait))

        def scroll(self, amount: float, wait: float | None = None) -> None:
            events.append(('scroll', amount, None, wait))

    nav = SimpleNamespace(
        layout=layout,
        ctrl=_RecordedCtrl(),
        gw=None,
        scroll_character_list=lambda *_args, **_kwargs: None,
    )

    class _FakeFuture:
        def __init__(self, result: CharResult) -> None:
            self._result = result

        def result(self, timeout: int) -> CharResult:
            return self._result

    class _FakeOcrService:
        def submit(self, capture) -> _FakeFuture:
            return _FakeFuture(
                CharResult(
                    capture.char_index,
                    capture.section,
                    {
                        'already_seen': True,
                        'name': 'alpha',
                        'char_id': 'alpha',
                        'level': 1,
                    },
                )
            )

    workflow = CharacterWorkflow(
        nav=cast(Any, nav),
        ocr_service=cast(Any, _FakeOcrService()),
        session=cast(Any, SimpleNamespace(session_id='session-id')),
    )

    workflow.run()

    assert events[:5] == [
        ('press_key', 'c', None, 2.0),
        ('click', 10, 20, 0.7),
        ('scroll', -1, None, None),
        ('scroll', 0.25, None, None),
        ('click', 1, 2, 0.8),
    ]


def test_character_workflow_retries_stale_duplicate_before_stopping(monkeypatch) -> None:
    image = np.arange(8 * 8 * 3, dtype=np.uint8).reshape(8, 8, 3)

    monkeypatch.setattr(character_workflow_module, 'capture_full', lambda *args, **kwargs: image)
    monkeypatch.setattr(
        character_workflow_module,
        'capture_region',
        lambda _gw, roi: image[
            int(roi.y): int(roi.y + roi.h),
            int(roi.x): int(roi.x + roi.w),
        ],
    )

    layout = SimpleNamespace(
        width=8,
        height=8,
        monitor=1,
        characters=SimpleNamespace(
            rightSide=SimpleNamespace(x=10, y=20),
            leftSide=SimpleNamespace(x=1, y=2),
            offsets=SimpleNamespace(
                rightSide=SimpleNamespace(y=1),
                leftSide=SimpleNamespace(y=1),
            ),
            resonatorName=SimpleNamespace(x=0, y=0, w=2, h=2),
            resonatorLevel=SimpleNamespace(x=2, y=0, w=2, h=2),
            weaponName=SimpleNamespace(x=0, y=2, w=2, h=2),
            weaponLevel=SimpleNamespace(x=2, y=2, w=2, h=2),
            weaponRank=SimpleNamespace(x=4, y=2, w=2, h=2),
            skillClick=SimpleNamespace(x=0, y=0),
            skillPositions=[SimpleNamespace(x=i, y=0) for i in range(5)],
            skillLevel=SimpleNamespace(x=0, y=4, w=2, h=2),
            chainClick=SimpleNamespace(x=0, y=0),
            chainPositions=[SimpleNamespace(x=i, y=0) for i in range(6)],
            chainButton=SimpleNamespace(x=2, y=4, w=2, h=2),
        ),
    )

    click_calls: list[tuple[int, int, float | None]] = []

    class _RecordedCtrl:
        def press_key(self, *_args, **_kwargs) -> None:
            pass

        def click(self, x: int, y: int, wait: float | None = None) -> None:
            click_calls.append((x, y, wait))

        def scroll(self, *_args, **_kwargs) -> None:
            pass

    nav = SimpleNamespace(
        layout=layout,
        ctrl=_RecordedCtrl(),
        gw=None,
        scroll_character_list=lambda *_args, **_kwargs: None,
    )

    class _FakeFuture:
        def __init__(self, result: CharResult) -> None:
            self._result = result

        def result(self, timeout: int) -> CharResult:
            return self._result

    class _FakeOcrService:
        def __init__(self) -> None:
            self._overview_attempts: dict[int, int] = {}

        def submit(self, capture) -> _FakeFuture:
            if capture.section == 0:
                attempt = self._overview_attempts.get(capture.char_index, 0)
                self._overview_attempts[capture.char_index] = attempt + 1
                if capture.char_index == 0:
                    fields = {
                        'already_seen': False,
                        'name': 'alpha',
                        'char_id': 'alpha',
                        'level': 1,
                    }
                elif capture.char_index == 1 and attempt == 0:
                    fields = {
                        'already_seen': True,
                        'name': 'alpha',
                        'char_id': 'alpha',
                        'level': 1,
                    }
                elif capture.char_index == 1:
                    fields = {
                        'already_seen': False,
                        'name': 'beta',
                        'char_id': 'beta',
                        'level': 1,
                    }
                else:
                    fields = {
                        'already_seen': True,
                        'name': 'beta',
                        'char_id': 'beta',
                        'level': 1,
                    }
            elif capture.section == 1:
                fields = {
                    'weaponName': 'sword',
                    'weaponId': 'weapon',
                    'weaponLevel': 10,
                    'weaponMaxLevel': 20,
                    'weaponRank': 1,
                }
            elif capture.section == 3:
                fields = {'skills': {key: 1 for key in capture.crops}}
            else:
                name = 'alpha' if capture.char_index == 0 else 'beta'
                fields = {
                    'name': name,
                    'char_id': name,
                    'level': 1,
                    'weaponName': 'sword',
                    'weaponId': 'weapon',
                    'weaponLevel': 10,
                    'weaponMaxLevel': 20,
                    'weaponRank': 1,
                    'skills': {f'skill_{idx}': 1 for idx in range(5)},
                    'chain': {f'chain_{idx}': False for idx in range(6)},
                }
            return _FakeFuture(CharResult(capture.char_index, capture.section, fields))

    workflow = CharacterWorkflow(
        nav=cast(Any, nav),
        ocr_service=cast(Any, _FakeOcrService()),
        session=cast(Any, SimpleNamespace(session_id='session-id')),
    )

    results = workflow.run()

    assert list(results) == ['alpha', 'beta']
    slot_one_clicks = [
        call for call in click_calls
        if call[0] == 10 and call[1] == 21
    ]
    assert slot_one_clicks[:2] == [
        (10, 21, 0.7),
        (10, 21, character_workflow_module._CHARACTER_SLOT_RETRY_WAIT_SECONDS),
    ]


def test_character_workflow_finishes_partial_final_page_before_stopping(monkeypatch) -> None:
    image = np.arange(8 * 8 * 3, dtype=np.uint8).reshape(8, 8, 3)

    monkeypatch.setattr(character_workflow_module, 'capture_full', lambda *args, **kwargs: image)
    monkeypatch.setattr(
        character_workflow_module,
        'capture_region',
        lambda _gw, roi: image[
            int(roi.y): int(roi.y + roi.h),
            int(roi.x): int(roi.x + roi.w),
        ],
    )

    layout = SimpleNamespace(
        width=8,
        height=8,
        monitor=1,
        characters=SimpleNamespace(
            rightSide=SimpleNamespace(x=10, y=20),
            leftSide=SimpleNamespace(x=1, y=2),
            offsets=SimpleNamespace(
                rightSide=SimpleNamespace(y=1),
                leftSide=SimpleNamespace(y=1),
            ),
            resonatorName=SimpleNamespace(x=0, y=0, w=2, h=2),
            resonatorLevel=SimpleNamespace(x=2, y=0, w=2, h=2),
            weaponName=SimpleNamespace(x=0, y=2, w=2, h=2),
            weaponLevel=SimpleNamespace(x=2, y=2, w=2, h=2),
            weaponRank=SimpleNamespace(x=4, y=2, w=2, h=2),
            skillClick=SimpleNamespace(x=0, y=0),
            skillPositions=[SimpleNamespace(x=i, y=0) for i in range(5)],
            skillLevel=SimpleNamespace(x=0, y=4, w=2, h=2),
            chainClick=SimpleNamespace(x=0, y=0),
            chainPositions=[SimpleNamespace(x=i, y=0) for i in range(6)],
            chainButton=SimpleNamespace(x=2, y=4, w=2, h=2),
        ),
    )

    scroll_calls: list[float] = []

    class _RecordedCtrl:
        def press_key(self, *_args, **_kwargs) -> None:
            pass

        def click(self, *_args, **_kwargs) -> None:
            pass

        def scroll(self, *_args, **_kwargs) -> None:
            pass

    nav = SimpleNamespace(
        layout=layout,
        ctrl=_RecordedCtrl(),
        gw=None,
        scroll_character_list=lambda *_args, **_kwargs: scroll_calls.append(0.5),
    )

    overview_results = iter(
        [
            {
                'already_seen': False,
                'name': f'char_{idx}',
                'char_id': f'char_{idx}',
                'level': 1,
            }
            for idx in range(7)
        ]
        + [
            repeated
            for idx in range(2, 7)
            for repeated in (
                {
                    'already_seen': True,
                    'name': f'char_{idx}',
                    'char_id': f'char_{idx}',
                    'level': 1,
                },
                {
                    'already_seen': True,
                    'name': f'char_{idx}',
                    'char_id': f'char_{idx}',
                    'level': 1,
                },
            )
        ]
        + [
            {
                'already_seen': False,
                'name': 'char_7',
                'char_id': 'char_7',
                'level': 1,
            },
            {
                'already_seen': False,
                'name': 'char_8',
                'char_id': 'char_8',
                'level': 1,
            },
        ]
    )

    class _FakeFuture:
        def __init__(self, result: CharResult) -> None:
            self._result = result

        def result(self, timeout: int) -> CharResult:
            return self._result

    class _FakeOcrService:
        def submit(self, capture) -> _FakeFuture:
            if capture.section == 0:
                fields = next(overview_results)
            elif capture.section == 1:
                fields = {
                    'weaponName': 'sword',
                    'weaponId': 'weapon',
                    'weaponLevel': 10,
                    'weaponMaxLevel': 20,
                    'weaponRank': 1,
                }
            elif capture.section == 3:
                fields = {'skills': {key: 1 for key in capture.crops}}
            else:
                fields = {
                    'name': f'char_{capture.char_index}',
                    'char_id': f'char_{capture.char_index}',
                    'level': 1,
                    'weaponName': 'sword',
                    'weaponId': 'weapon',
                    'weaponLevel': 10,
                    'weaponMaxLevel': 20,
                    'weaponRank': 1,
                    'skills': {f'skill_{idx}': 1 for idx in range(5)},
                    'chain': {f'chain_{idx}': False for idx in range(6)},
                }
            return _FakeFuture(CharResult(capture.char_index, capture.section, fields))

    workflow = CharacterWorkflow(
        nav=cast(Any, nav),
        ocr_service=cast(Any, _FakeOcrService()),
        session=cast(Any, SimpleNamespace(session_id='session-id')),
    )

    results = workflow.run()

    assert list(results) == [f'char_{idx}' for idx in range(9)]
    assert scroll_calls == [0.5]


def test_scroll_character_list_moves_to_right_side_before_scrolling() -> None:
    events: list[tuple] = []

    class _Ctrl:
        def move(self, x: float, y: float, wait: float = 0.1) -> None:
            events.append(('move', x, y, wait))

        def scroll(self, amount: float, wait: float = 0.1) -> None:
            events.append(('scroll', amount, wait))

    layout = SimpleNamespace(
        scroll=SimpleNamespace(
            characters=SimpleNamespace(y=-56),
        ),
        characters=SimpleNamespace(
            rightSide=SimpleNamespace(x=1813.1, y=202.9),
        ),
    )
    gw = SimpleNamespace(layout=layout)
    nav = GameNavigator(cast(Any, _Ctrl()), cast(Any, gw))

    nav.scroll_character_list(wait=0.5)

    assert events == [
        ('move', 1813.1, 202.9, 0.3),
        ('scroll', -56, 0.5),
    ]



def test_session_orchestrator_passes_write_debug_to_weapon_and_character_workflows(
    monkeypatch,
    tmp_path,
) -> None:
    weapon_init: dict[str, object] = {}
    character_init: dict[str, object] = {}

    class _FakeWeaponWorkflow:
        def __init__(self, **kwargs) -> None:
            weapon_init.update(kwargs)

        def run(self, on_progress=None) -> list[dict]:
            return []

    class _FakeCharacterWorkflow:
        def __init__(self, **kwargs) -> None:
            character_init.update(kwargs)

        def run(self, on_progress=None) -> dict:
            return {}

    monkeypatch.setattr(
        'wuwa_inventory_kamera.scraping.scanning.weapon_workflow.WeaponWorkflow',
        _FakeWeaponWorkflow,
    )
    monkeypatch.setattr(
        'wuwa_inventory_kamera.scraping.scanning.character_workflow.CharacterWorkflow',
        _FakeCharacterWorkflow,
    )

    orchestrator = SessionOrchestrator(scrapers=[], save_raw=tmp_path, write_debug=True)
    stop_event = threading.Event()

    orchestrator._run_weapons(
        cast(Any, object()),
        cast(Any, object()),
        'session-id',
        InventoryTab.WEAPONS,
        stop_event,
    )
    orchestrator._run_characters(
        cast(Any, object()),
        cast(Any, object()),
        'session-id',
        stop_event,
    )

    assert weapon_init['save_raw'] == tmp_path / 'session-id' / 'raw'
    assert character_init['save_raw'] == tmp_path / 'session-id' / 'raw'
    assert weapon_init['write_debug'] is True
    assert character_init['write_debug'] is True


def test_session_orchestrator_forwards_weapon_thresholds_to_ocr_service(monkeypatch) -> None:
    ocr_init: dict[str, object] = {}

    class _FakeGameWindow:
        def __init__(self, windowed: bool = False) -> None:
            self.windowed = windowed
            self.found = True
            self.layout = SimpleNamespace(width=1920, height=1080)
            self.monitor_index = 0

        def activate(self) -> None:
            return None

    class _FakeNavigator:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def is_in_main_menu(self) -> bool:
            return True

    class _FakeController:
        def press_key(self, *_args, **_kwargs) -> None:
            return None

    class _FakeStopSignal:
        def __init__(self) -> None:
            self.event = threading.Event()

        def is_set(self) -> bool:
            return False

        def stop(self) -> None:
            return None

    class _FakeOcrService:
        def __init__(self, **kwargs) -> None:
            ocr_init.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            _ = exc_type, exc, tb

    monkeypatch.setattr(session_orchestrator_module, 'GameWindow', _FakeGameWindow)
    monkeypatch.setattr(session_orchestrator_module, 'InputController', lambda *args, **kwargs: _FakeController())
    monkeypatch.setattr(session_orchestrator_module, 'GameNavigator', _FakeNavigator)
    monkeypatch.setattr(session_orchestrator_module, 'StopSignal', _FakeStopSignal)
    monkeypatch.setattr(session_orchestrator_module, 'OcrService', _FakeOcrService)
    monkeypatch.setattr(session_orchestrator_module.time, 'sleep', lambda *_args, **_kwargs: None)

    orchestrator = SessionOrchestrator(
        scrapers=[],
        min_rarity=2,
        min_level=5,
        weapon_min_rarity=4,
        weapon_min_level=0,
    )

    result = orchestrator.run()

    assert result['date']
    assert ocr_init['min_rarity'] == 2
    assert ocr_init['min_level'] == 5
    assert ocr_init['weapon_min_rarity'] == 4
    assert ocr_init['weapon_min_level'] == 0


def test_stop_signal_stop_does_not_mark_cancellation(monkeypatch) -> None:
    monkeypatch.setattr(stop_signal_module.threading.Thread, 'start', lambda self: None)
    monkeypatch.setattr(stop_signal_module.threading.Thread, 'join', lambda self, timeout=None: None)

    signal = StopSignal()

    signal.stop()

    assert signal.is_set() is False
    assert signal.event.is_set() is False


def test_home_scan_finished_saves_character_results(monkeypatch) -> None:
    saved_calls: list[tuple[dict[str, tuple[object, type]], str]] = []
    notifications: list[tuple[str, str, str]] = []

    monkeypatch.setattr(
        home_module,
        'savingScraped',
        lambda scan_data, START_DATE='': saved_calls.append((scan_data, START_DATE)),
    )

    class _FakeButton:
        def __init__(self) -> None:
            self.enabled: bool | None = None

        def setEnabled(self, enabled: bool) -> None:
            self.enabled = enabled

    class _FakeLabel:
        def __init__(self) -> None:
            self.text: str | None = None

        def setText(self, text: str) -> None:
            self.text = text

    class _FakeBar:
        def __init__(self) -> None:
            self.value: int | None = None

        def setValue(self, value: int) -> None:
            self.value = value

    class _FakeNotifier:
        def emit(self, level: str, title: str, message: str) -> None:
            notifications.append((level, title, message))

    home = SimpleNamespace(
        startScanning=_FakeButton(),
        _scan_thread=object(),
        scanProgressLabel=_FakeLabel(),
        scanProgressBar=_FakeBar(),
        processProgressLabel=_FakeLabel(),
        processProgressBar=_FakeBar(),
        signalNotifier=_FakeNotifier(),
    )

    home_module.LControlPanel._onScanFinished(
        cast(Any, home),
        {
            'date': 'session-id',
            'characters': {
                'alpha': {'_name': 'alpha'},
                'beta': {'_name': 'beta'},
            },
        },
    )

    assert saved_calls == [
        (
            {
                'characters_wuwainventorykamera.json': (
                    {
                        'alpha': {'_name': 'alpha'},
                        'beta': {'_name': 'beta'},
                    },
                    dict,
                ),
            },
            'session-id',
        ),
    ]
    assert notifications == [
        ('success', 'Scan Complete', 'characters: 2'),
    ]


