from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import cv2
import numpy as np

from wuwa_inventory_kamera.scraping.service.captures import EchoResult
from wuwa_inventory_kamera.scraping.service.echo_reprocess import (
    reprocess_echo_scans_with_service,
)


class _FakeFuture:
    last_timeout: float | None | object = object()

    def result(self, timeout: float | None = None) -> EchoResult:
        self.__class__.last_timeout = timeout
        return EchoResult(
            echo_index=3,
            data=None,
            warnings=[],
            retried=False,
            detected_level=25,
        )


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

    def ocr_adhoc_text(self, _image, _roi_key: str) -> str:
        return '25'


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
        self.echoes = SimpleNamespace(
            echoCard=SimpleNamespace(x=0, y=0, w=2, h=2),
            fullStatsName=SimpleNamespace(x=2, y=0, w=2, h=2),
            fullStatsValue=SimpleNamespace(x=0, y=2, w=2, h=2),
            echoName=SimpleNamespace(x=2, y=2, w=2, h=2),
            level=SimpleNamespace(x=4, y=0, w=2, h=2),
            sonataIcon=SimpleNamespace(
                radius=1.0,
                level_X=SimpleNamespace(
                    circle=SimpleNamespace(x=1.0, y=1.0),
                    icon=SimpleNamespace(x=4, y=0, w=2, h=2),
                ),
                level_XX=SimpleNamespace(
                    circle=SimpleNamespace(x=1.0, y=1.0),
                    icon=SimpleNamespace(x=4, y=0, w=2, h=2),
                ),
            ),
            rarityColorPick=SimpleNamespace(x=0, y=0),
        )


def test_reprocess_reconstructs_echo_name_crop(monkeypatch) -> None:
    screen_info_module = ModuleType('wuwa_inventory_kamera.game.screen_info')
    screen_info_module.ScreenInfo = _FakeScreenInfo
    monkeypatch.setitem(sys.modules, 'wuwa_inventory_kamera.game.screen_info', screen_info_module)

    ocr_service_module = ModuleType('wuwa_inventory_kamera.scraping.service.ocr_service')
    ocr_service_module.OcrService = _FakeOcrService
    monkeypatch.setitem(sys.modules, 'wuwa_inventory_kamera.scraping.service.ocr_service', ocr_service_module)

    _FakeOcrService.instances.clear()
    _FakeFuture.last_timeout = object()
    image = np.arange(6 * 6 * 3, dtype=np.uint8).reshape(6, 6, 3)
    scan = _FakeScan(image)

    result = reprocess_echo_scans_with_service(
        scans=[scan],
        providers=['CPUExecutionProvider'],
        min_rarity=5,
        min_level=21,
        write_debug=False,
    )

    assert result == []
    assert _FakeFuture.last_timeout is None
    assert _FakeOcrService.instances[0].kwargs['max_batch_size'] == 8
    capture = _FakeOcrService.instances[0].submitted[0]
    np.testing.assert_array_equal(
        capture.echo_name,
        cv2.cvtColor(image[2:4, 2:4], cv2.COLOR_RGB2BGR),
    )


def test_reprocess_allows_custom_batch_size(monkeypatch) -> None:
    screen_info_module = ModuleType('wuwa_inventory_kamera.game.screen_info')
    screen_info_module.ScreenInfo = _FakeScreenInfo
    monkeypatch.setitem(sys.modules, 'wuwa_inventory_kamera.game.screen_info', screen_info_module)

    ocr_service_module = ModuleType('wuwa_inventory_kamera.scraping.service.ocr_service')
    ocr_service_module.OcrService = _FakeOcrService
    monkeypatch.setitem(sys.modules, 'wuwa_inventory_kamera.scraping.service.ocr_service', ocr_service_module)

    _FakeOcrService.instances.clear()
    _FakeFuture.last_timeout = object()
    image = np.arange(6 * 6 * 3, dtype=np.uint8).reshape(6, 6, 3)
    scan = _FakeScan(image)

    reprocess_echo_scans_with_service(
        scans=[scan],
        providers=['CPUExecutionProvider'],
        min_rarity=5,
        min_level=21,
        write_debug=False,
        max_batch_size=4,
    )

    assert _FakeOcrService.instances[0].kwargs['max_batch_size'] == 4


def test_reprocess_write_debug_dumps_region_images(monkeypatch, tmp_path) -> None:
    screen_info_module = ModuleType('wuwa_inventory_kamera.game.screen_info')
    screen_info_module.ScreenInfo = _FakeScreenInfo
    monkeypatch.setitem(sys.modules, 'wuwa_inventory_kamera.game.screen_info', screen_info_module)

    echo_workflow_module = ModuleType('wuwa_inventory_kamera.scraping.scanning.echo_workflow')
    echo_workflow_module._rarity_from_bgr_pixel = lambda _pixel: 5
    echo_workflow_module._rarity_from_rgb_pixel = lambda _pixel: 5
    monkeypatch.setitem(sys.modules, 'wuwa_inventory_kamera.scraping.scanning.echo_workflow', echo_workflow_module)

    ocr_service_module = ModuleType('wuwa_inventory_kamera.scraping.service.ocr_service')
    ocr_service_module.OcrService = _FakeOcrService
    monkeypatch.setitem(sys.modules, 'wuwa_inventory_kamera.scraping.service.ocr_service', ocr_service_module)

    region_specs_module = ModuleType('wuwa_inventory_kamera.scraping.ocr.region_specs')

    class _FakeSpec:
        def preprocess(self, image, rarity=None):
            assert rarity == 5
            plane = image[:, :, 0] if image.ndim == 3 else image
            return cv2.cvtColor(plane, cv2.COLOR_GRAY2RGB)

        def _image_for_signature(self, image, rarity):
            assert rarity == 5
            plane = image[:, :, 1] if image.ndim == 3 else image
            return plane

    region_specs_module.get_spec = lambda _roi_key: _FakeSpec()
    monkeypatch.setitem(sys.modules, 'wuwa_inventory_kamera.scraping.ocr.region_specs', region_specs_module)

    raw_base = tmp_path / 'raw'
    echo_dir = raw_base / 'echo_0003'
    echo_dir.mkdir(parents=True)

    _FakeOcrService.instances.clear()
    _FakeFuture.last_timeout = object()
    image = np.arange(6 * 6 * 3, dtype=np.uint8).reshape(6, 6, 3)
    scan = _FakeScan(image, full_path=echo_dir / 'full.png')

    reprocess_echo_scans_with_service(
        scans=[scan],
        providers=['CPUExecutionProvider'],
        min_rarity=5,
        min_level=21,
        write_debug=True,
        raw_base=raw_base,
    )

    debug_dir = echo_dir / 'debug'
    assert debug_dir.is_dir()

    expected_files = {
        'echo_name.png',
        'echo_name_preprocessed.png',
        'echo_name_signature.png',
        'stats_name.png',
        'stats_name_preprocessed.png',
        'stats_name_signature.png',
        'stats_value.png',
        'stats_value_preprocessed.png',
        'stats_value_signature.png',
    }
    assert expected_files == {path.name for path in debug_dir.iterdir()}

    for filename in expected_files:
        saved = cv2.imread(str(debug_dir / filename), cv2.IMREAD_UNCHANGED)
        assert saved is not None, filename

    echo_name_raw = cv2.imread(str(debug_dir / 'echo_name.png'), cv2.IMREAD_COLOR)
    stats_name_raw = cv2.imread(str(debug_dir / 'stats_name.png'), cv2.IMREAD_COLOR)
    stats_value_raw = cv2.imread(str(debug_dir / 'stats_value.png'), cv2.IMREAD_COLOR)
    np.testing.assert_array_equal(echo_name_raw, cv2.cvtColor(image[2:4, 2:4], cv2.COLOR_RGB2BGR))
    np.testing.assert_array_equal(stats_name_raw, cv2.cvtColor(image[0:2, 2:4], cv2.COLOR_RGB2BGR))
    np.testing.assert_array_equal(stats_value_raw, cv2.cvtColor(image[2:4, 0:2], cv2.COLOR_RGB2BGR))