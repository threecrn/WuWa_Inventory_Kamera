from __future__ import annotations

import concurrent.futures
import threading
from types import SimpleNamespace
from typing import Any, cast

import numpy as np

import wuwa_inventory_kamera.scraping.scanning.character_workflow as character_workflow_module
import wuwa_inventory_kamera.scraping.scanning.session_orchestrator as session_orchestrator_module
import wuwa_inventory_kamera.scraping.scanning.weapon_workflow as weapon_workflow_module
import wuwa_inventory_kamera.scraping.service.ocr_service as ocr_service_module
from wuwa_inventory_kamera.game.navigation import GameNavigator, InventoryTab
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
        'wuwa_inventory_kamera.scraping.service.echo_reprocess._write_region_debug_artifacts',
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


def test_ocr_service_uses_level_spec_for_weapons_and_value_spec_for_items() -> None:
    service = ocr_service_module.OcrService.__new__(ocr_service_module.OcrService)
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
            WeaponCapture(index=2, name=image, value=image, rank=None),
            1,
            concurrent.futures.Future(),
        ),
    ]

    service._process_weapons(group)

    assert spec_calls == [
        ('weapons.name', 1, 5),
        ('weapons.name', 1, None),
        ('weapons.level', 1, None),
        ('weapons.value', 1, None),
        ('weapons.equipped', 1, None),
    ]
    assert assemble_calls[0]['name_texts'] == ['weapons.name']
    assert assemble_calls[1]['name_texts'] == ['weapons.name']
    assert assemble_calls[0]['value_texts'] == ['weapons.level']
    assert assemble_calls[1]['value_texts'] == ['weapons.value']
    assert assemble_calls[0]['equipped_texts'] == ['weapons.equipped']
    assert assemble_calls[1]['equipped_texts'] == []
    assert group[0].future.result().is_weapon is True
    assert group[1].future.result().is_weapon is False


def test_ocr_service_passes_equipped_tokens_for_echoes() -> None:
    service = ocr_service_module.OcrService.__new__(ocr_service_module.OcrService)
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
    service._echo_asm = _FakeEchoAssembler()

    group = [
        ocr_service_module._QueueItem(
            EchoCapture(echo_index=1, card=image, stats_name=image, stats_value=image, equipped=image),
            0,
            concurrent.futures.Future(),
        ),
        ocr_service_module._QueueItem(
            EchoCapture(echo_index=2, card=image, stats_name=image, stats_value=image),
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
        'wuwa_inventory_kamera.scraping.service.echo_reprocess._write_region_debug_artifacts',
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
    ctrl = SimpleNamespace(
        press_key=lambda *_args, **_kwargs: None,
        click=lambda *_args, **_kwargs: None,
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
    assert len(debug_calls) == 18
    assert {call['roi_key'] for call in debug_calls} == {
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
        'characters.chain_0',
        'characters.chain_1',
        'characters.chain_2',
        'characters.chain_3',
        'characters.chain_4',
        'characters.chain_5',
    }
    assert {call['debug_dir'] for call in debug_calls} == {
        tmp_path / 'raw' / 'char_0000' / 'section_0' / 'debug',
        tmp_path / 'raw' / 'char_0000' / 'section_1' / 'debug',
        tmp_path / 'raw' / 'char_0000' / 'section_3' / 'debug',
        tmp_path / 'raw' / 'char_0000' / 'section_4' / 'debug',
        tmp_path / 'raw' / 'char_0001' / 'section_0' / 'debug',
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

    assert events[:3] == [
        ('press_key', 'c', None, 2.0),
        ('click', 10, 20, 0.7),
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
    assert slot_one_clicks == [
        (10, 21, 0.7),
        (10, 21, character_workflow_module._CHARACTER_SLOT_RETRY_WAIT_SECONDS),
    ]


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