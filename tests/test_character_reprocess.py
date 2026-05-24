from __future__ import annotations

import json
import sys
from types import ModuleType, SimpleNamespace

import cv2
import numpy as np

from wuwa_inventory_kamera.scraping.service.captures import CharResult
from wuwa_inventory_kamera.scraping.service.character_reprocess import (
    reprocess_character_scans_with_service,
)
from wuwa_inventory_kamera.scraping.utils.common import loadCharacterRawScans


class _FakeFuture:
    def __init__(self, result: CharResult) -> None:
        self._result = result

    def result(self, timeout: float | None = None) -> CharResult:
        _ = timeout
        return self._result


class _FakeOcrService:
    instances: list['_FakeOcrService'] = []

    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs
        self.submitted = []

    def __enter__(self) -> '_FakeOcrService':
        self.__class__.instances.append(self)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        _ = exc_type, exc, tb

    def submit(self, capture):
        self.submitted.append(capture)
        if capture.section == 4:
            result = CharResult(
                char_index=capture.char_index,
                section=capture.section,
                fields={
                    'char_id': 'alpha',
                    'name': 'alpha',
                    'level': 80,
                    'weaponId': 'sword_alpha',
                    'weaponLevel': 70,
                    'weaponMaxLevel': 80,
                    'weaponRank': 3,
                    'skills': {
                        'skill_0': 6,
                        'skill_1': 7,
                        'skill_2': 8,
                        'skill_3': 9,
                        'skill_4': 10,
                        'stats0': 2,
                        'stats1': 1,
                        'inherent': 2,
                        'stats3': 0,
                        'stats4': 1,
                    },
                    'chain': {
                        'chain_0': True,
                        'chain_1': True,
                        'chain_2': False,
                    },
                },
            )
        else:
            result = CharResult(
                char_index=capture.char_index,
                section=capture.section,
                fields={},
            )
        return _FakeFuture(result)


class _FakeCharacterScan:
    def __init__(
        self,
        sections: dict[int, dict[str, np.ndarray]],
        *,
        base_path=None,
    ) -> None:
        self.index = 0
        self.screen_width = 1920
        self.screen_height = 1080
        self.sections = sections
        self.base_path = base_path

    def load_section_images(self, section: int) -> dict[str, np.ndarray]:
        return {name: image.copy() for name, image in self.sections[section].items()}


class _FakeScreenInfo:
    def __init__(self, _width: int, _height: int) -> None:
        self.characters = SimpleNamespace(
            resonatorName=SimpleNamespace(x=0, y=0, w=2, h=2),
            resonatorLevel=SimpleNamespace(x=2, y=0, w=2, h=2),
            weaponName=SimpleNamespace(x=0, y=0, w=2, h=2),
            weaponLevel=SimpleNamespace(x=2, y=0, w=2, h=2),
            weaponRank=SimpleNamespace(x=4, y=0, w=2, h=2),
        )


def test_load_character_raw_scans_skips_incomplete_character_dirs(tmp_path) -> None:
    raw_dir = tmp_path / 'raw'
    char0 = raw_dir / 'char_0000'
    char0.mkdir(parents=True)
    with open(char0 / 'meta.json', 'w', encoding='utf-8') as f:
        json.dump({'char_index': 0, 'screen_width': 1920, 'screen_height': 1200, 'monitor': 9}, f)

    for section in (0, 1):
        section_dir = char0 / f'section_{section}'
        section_dir.mkdir()
        cv2.imwrite(str(section_dir / 'full.png'), np.zeros((4, 6, 3), dtype=np.uint8))

    section3 = char0 / 'section_3'
    section3.mkdir()
    cv2.imwrite(str(section3 / 'skill_0.png'), np.zeros((2, 2, 3), dtype=np.uint8))

    section4 = char0 / 'section_4'
    section4.mkdir()
    cv2.imwrite(str(section4 / 'chain_0.png'), np.zeros((2, 2, 3), dtype=np.uint8))

    char1 = raw_dir / 'char_0001'
    char1.mkdir()
    with open(char1 / 'meta.json', 'w', encoding='utf-8') as f:
        json.dump({'char_index': 1}, f)
    (char1 / 'section_0').mkdir()
    cv2.imwrite(str(char1 / 'section_0' / 'full.png'), np.zeros((4, 6, 3), dtype=np.uint8))

    scans = loadCharacterRawScans(raw_dir)

    assert len(scans) == 1
    assert scans[0].index == 0
    assert set(scans[0].section_paths) == {0, 1, 3, 4}


def test_reprocess_character_scans_reconstructs_sections_and_outputs_dict(monkeypatch) -> None:
    screen_info_module = ModuleType('wuwa_inventory_kamera.game.screen_info')
    screen_info_module.ScreenInfo = _FakeScreenInfo
    monkeypatch.setitem(sys.modules, 'wuwa_inventory_kamera.game.screen_info', screen_info_module)

    ocr_service_module = ModuleType('wuwa_inventory_kamera.scraping.service.ocr_service')
    ocr_service_module.OcrService = _FakeOcrService
    monkeypatch.setitem(sys.modules, 'wuwa_inventory_kamera.scraping.service.ocr_service', ocr_service_module)

    _FakeOcrService.instances.clear()
    overview_full = np.arange(4 * 6 * 3, dtype=np.uint8).reshape(4, 6, 3)
    weapon_full = np.arange(4 * 6 * 3, dtype=np.uint8).reshape(4, 6, 3) + 50
    skill = np.arange(2 * 2 * 3, dtype=np.uint8).reshape(2, 2, 3) + 100
    passive_skill = np.arange(2 * 2 * 3, dtype=np.uint8).reshape(2, 2, 3) + 120
    chain = np.arange(2 * 2 * 3, dtype=np.uint8).reshape(2, 2, 3) + 150

    scan = _FakeCharacterScan({
        0: {'full': overview_full},
        1: {'full': weapon_full},
        3: {
            'skill_0': skill,
            'passive_stats0_1': passive_skill,
        },
        4: {'chain_0': chain},
    })

    result = reprocess_character_scans_with_service(
        scans=[scan],
        providers=['CPUExecutionProvider'],
        write_debug=False,
        max_batch_size=4,
    )

    assert result == {
        'alpha': {
            '_name': 'alpha',
            'level': 80,
            'ascension': 0,
            'weapon': {
                'id': 'sword_alpha',
                'level': 70,
                'ascension': 5,
                'rank': 3,
            },
            'echoes': {},
            'skills': {
                'normal': 6,
                'resonance': 7,
                'forte': 8,
                'liberation': 9,
                'intro': 10,
                'stats0': 2,
                'stats1': 1,
                'inherent': 2,
                'stats3': 0,
                'stats4': 1,
            },
            'chain': 2,
        }
    }

    assert _FakeOcrService.instances[0].kwargs['max_batch_size'] == 4
    submitted = _FakeOcrService.instances[0].submitted
    assert [capture.section for capture in submitted] == [0, 1, 3, 4]

    np.testing.assert_array_equal(
        submitted[0].crops['name'],
        cv2.cvtColor(overview_full[0:2, 0:2], cv2.COLOR_RGB2BGR),
    )
    np.testing.assert_array_equal(
        submitted[0].crops['level'],
        cv2.cvtColor(overview_full[0:2, 2:4], cv2.COLOR_RGB2BGR),
    )
    np.testing.assert_array_equal(
        submitted[1].crops['weaponName'],
        cv2.cvtColor(weapon_full[0:2, 0:2], cv2.COLOR_RGB2BGR),
    )
    np.testing.assert_array_equal(
        submitted[1].crops['weaponLevel'],
        cv2.cvtColor(weapon_full[0:2, 2:4], cv2.COLOR_RGB2BGR),
    )
    np.testing.assert_array_equal(
        submitted[1].crops['weaponRank'],
        cv2.cvtColor(weapon_full[0:2, 4:6], cv2.COLOR_RGB2BGR),
    )
    np.testing.assert_array_equal(
        submitted[2].crops['skill_0'],
        cv2.cvtColor(skill, cv2.COLOR_RGB2BGR),
    )
    np.testing.assert_array_equal(
        submitted[2].crops['passive_stats0_1'],
        cv2.cvtColor(passive_skill, cv2.COLOR_RGB2BGR),
    )
    np.testing.assert_array_equal(
        submitted[3].crops['chain_0'],
        cv2.cvtColor(chain, cv2.COLOR_RGB2BGR),
    )


def test_reprocess_character_write_debug_writes_overview_preprocessed_artifacts(
    monkeypatch,
    tmp_path,
) -> None:
    screen_info_module = ModuleType('wuwa_inventory_kamera.game.screen_info')
    screen_info_module.ScreenInfo = _FakeScreenInfo
    monkeypatch.setitem(sys.modules, 'wuwa_inventory_kamera.game.screen_info', screen_info_module)

    ocr_service_module = ModuleType('wuwa_inventory_kamera.scraping.service.ocr_service')
    ocr_service_module.OcrService = _FakeOcrService
    monkeypatch.setitem(sys.modules, 'wuwa_inventory_kamera.scraping.service.ocr_service', ocr_service_module)

    _FakeOcrService.instances.clear()
    overview_full = np.arange(4 * 6 * 3, dtype=np.uint8).reshape(4, 6, 3)
    weapon_full = np.arange(4 * 6 * 3, dtype=np.uint8).reshape(4, 6, 3) + 50
    skill = np.arange(2 * 2 * 3, dtype=np.uint8).reshape(2, 2, 3) + 100
    chain = np.arange(2 * 2 * 3, dtype=np.uint8).reshape(2, 2, 3) + 150

    char_dir = tmp_path / 'char_0000'
    scan = _FakeCharacterScan(
        {
            0: {'full': overview_full},
            1: {'full': weapon_full},
            3: {'skill_0': skill},
            4: {'chain_0': chain},
        },
        base_path=char_dir,
    )

    reprocess_character_scans_with_service(
        scans=[scan],
        providers=['CPUExecutionProvider'],
        write_debug=True,
        max_batch_size=4,
    )

    debug_dir = char_dir / 'section_0' / 'debug'
    assert (debug_dir / 'name.png').exists()
    assert (debug_dir / 'name_preprocessed.png').exists()
    assert (debug_dir / 'name_signature.png').exists()
    assert (debug_dir / 'level.png').exists()
    assert (debug_dir / 'level_preprocessed.png').exists()
    assert (debug_dir / 'level_signature.png').exists()