from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import cv2
import numpy as np

from wuwa_inventory_kamera.scraping.service.captures import WeaponResult
from wuwa_inventory_kamera.scraping.service.weapon_reprocess import (
    reprocess_weapon_scans_with_service,
)


class _FakeFuture:
    def result(self, timeout: float | None = None) -> WeaponResult:
        _ = timeout
        return WeaponResult(index=3, is_weapon=True, data=None)


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
        return _FakeFuture()


class _FakeScan:
    def __init__(self, image: np.ndarray, *, full_path=None) -> None:
        self.index = 3
        self.screen_width = 1920
        self.screen_height = 1080
        self.full_screenshot = image
        self.full_path = full_path

    def load_images(self) -> None:
        return None


class _FakeScreenInfo:
    def __init__(self, _width: int, _height: int) -> None:
        self.weapons = SimpleNamespace(
            name=SimpleNamespace(x=0, y=0, w=2, h=2),
            level=SimpleNamespace(x=2, y=0, w=2, h=2),
            rank=SimpleNamespace(x=0, y=2, w=2, h=2),
            equipped=SimpleNamespace(x=2, y=2, w=2, h=2),
            rarityColorPick=SimpleNamespace(x=4, y=4),
        )


def test_reprocess_reconstructs_weapon_crops(monkeypatch) -> None:
    screen_info_module = ModuleType('wuwa_inventory_kamera.game.screen_info')
    screen_info_module.ScreenInfo = _FakeScreenInfo
    monkeypatch.setitem(sys.modules, 'wuwa_inventory_kamera.game.screen_info', screen_info_module)

    echo_workflow_module = ModuleType('wuwa_inventory_kamera.scraping.scanning.echo_workflow')
    echo_workflow_module._rarity_from_capture_pixel = lambda _pixel: (5, 'RGB', 0.0)
    monkeypatch.setitem(sys.modules, 'wuwa_inventory_kamera.scraping.scanning.echo_workflow', echo_workflow_module)

    ocr_service_module = ModuleType('wuwa_inventory_kamera.scraping.service.ocr_service')
    ocr_service_module.OcrService = _FakeOcrService
    monkeypatch.setitem(sys.modules, 'wuwa_inventory_kamera.scraping.service.ocr_service', ocr_service_module)

    _FakeOcrService.instances.clear()
    image = np.arange(6 * 6 * 3, dtype=np.uint8).reshape(6, 6, 3)
    scan = _FakeScan(image)

    result = reprocess_weapon_scans_with_service(
        scans=[scan],
        providers=['CPUExecutionProvider'],
        min_rarity=5,
        min_level=21,
        write_debug=False,
    )

    assert result == []
    assert _FakeOcrService.instances[0].kwargs['max_batch_size'] == 8
    capture = _FakeOcrService.instances[0].submitted[0]
    np.testing.assert_array_equal(
        capture.name,
        cv2.cvtColor(image[0:2, 0:2], cv2.COLOR_RGB2BGR),
    )
    np.testing.assert_array_equal(
        capture.value,
        cv2.cvtColor(image[0:2, 2:4], cv2.COLOR_RGB2BGR),
    )
    np.testing.assert_array_equal(
        capture.rank,
        cv2.cvtColor(image[2:4, 0:2], cv2.COLOR_RGB2BGR),
    )
    np.testing.assert_array_equal(
        capture.equipped,
        cv2.cvtColor(image[2:4, 2:4], cv2.COLOR_RGB2BGR),
    )
    assert capture.detected_rarity == 5


def test_reprocess_weapon_write_debug_dumps_region_images(monkeypatch, tmp_path) -> None:
    screen_info_module = ModuleType('wuwa_inventory_kamera.game.screen_info')
    screen_info_module.ScreenInfo = _FakeScreenInfo
    monkeypatch.setitem(sys.modules, 'wuwa_inventory_kamera.game.screen_info', screen_info_module)

    echo_workflow_module = ModuleType('wuwa_inventory_kamera.scraping.scanning.echo_workflow')
    echo_workflow_module._rarity_from_capture_pixel = lambda _pixel: (5, 'RGB', 0.0)
    monkeypatch.setitem(sys.modules, 'wuwa_inventory_kamera.scraping.scanning.echo_workflow', echo_workflow_module)

    ocr_service_module = ModuleType('wuwa_inventory_kamera.scraping.service.ocr_service')
    ocr_service_module.OcrService = _FakeOcrService
    monkeypatch.setitem(sys.modules, 'wuwa_inventory_kamera.scraping.service.ocr_service', ocr_service_module)

    region_specs_module = ModuleType('wuwa_inventory_kamera.scraping.ocr.region_specs')

    class _FakeSpec:
        def preprocess(self, image, rarity=None):
            plane = image[:, :, 0] if image.ndim == 3 else image
            assert rarity in (None, 5)
            return SimpleNamespace(
                ocr_rgb=cv2.cvtColor(plane, cv2.COLOR_GRAY2RGB),
                signature_image=plane,
            )

        def _image_for_signature(self, image, rarity):
            plane = image[:, :, 1] if image.ndim == 3 else image
            assert rarity in (None, 5)
            return plane

    region_specs_module.get_spec = lambda _roi_key: _FakeSpec()
    monkeypatch.setitem(sys.modules, 'wuwa_inventory_kamera.scraping.ocr.region_specs', region_specs_module)

    raw_base = tmp_path / 'raw'
    weapon_dir = raw_base / 'weapon_0003'
    weapon_dir.mkdir(parents=True)

    _FakeOcrService.instances.clear()
    image = np.arange(6 * 6 * 3, dtype=np.uint8).reshape(6, 6, 3)
    scan = _FakeScan(image, full_path=weapon_dir / 'full.png')

    reprocess_weapon_scans_with_service(
        scans=[scan],
        providers=['CPUExecutionProvider'],
        min_rarity=5,
        min_level=21,
        write_debug=True,
        raw_base=raw_base,
    )

    debug_dir = weapon_dir / 'debug'
    assert debug_dir.is_dir()

    expected_files = {
        'equipped.png',
        'equipped_preprocessed.png',
        'equipped_signature.png',
        'name.png',
        'name_preprocessed.png',
        'name_signature.png',
        'level.png',
        'level_preprocessed.png',
        'level_signature.png',
        'rank.png',
        'rank_preprocessed.png',
        'rank_signature.png',
    }
    assert expected_files == {path.name for path in debug_dir.iterdir()}

    for filename in expected_files:
        saved = cv2.imread(str(debug_dir / filename), cv2.IMREAD_UNCHANGED)
        assert saved is not None, filename

    equipped_raw = cv2.imread(str(debug_dir / 'equipped.png'), cv2.IMREAD_COLOR)
    np.testing.assert_array_equal(equipped_raw, cv2.cvtColor(image[2:4, 2:4], cv2.COLOR_RGB2BGR))