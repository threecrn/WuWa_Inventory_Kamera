from __future__ import annotations

import json
import sys
from types import ModuleType, SimpleNamespace

import cv2
import numpy as np

from wuwa_inventory_kamera.scraping.service.captures import EchoResult
from wuwa_inventory_kamera.scraping.service.echo_reprocess import (
    _write_echo_debug_artifacts,
    reprocess_echo_scans_with_service,
)
from wuwa_inventory_kamera.scraping.utils.common import loadRawScans


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


class _RecordingOcrService(_FakeOcrService):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.adhoc_calls: list[tuple[np.ndarray, str]] = []

    def ocr_adhoc_text(self, image, roi_key: str) -> str:
        self.adhoc_calls.append((image.copy(), roi_key))
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
            equipped=SimpleNamespace(x=4, y=2, w=2, h=2),
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


def _install_fake_reprocess_modules(monkeypatch, *, ocr_service_cls=_FakeOcrService) -> None:
    screen_info_module = ModuleType('wuwa_inventory_kamera.game.screen_info')
    screen_info_module.ScreenInfo = _FakeScreenInfo
    monkeypatch.setitem(sys.modules, 'wuwa_inventory_kamera.game.screen_info', screen_info_module)

    ocr_service_module = ModuleType('wuwa_inventory_kamera.scraping.service.ocr_service')
    ocr_service_module.OcrService = ocr_service_cls
    monkeypatch.setitem(sys.modules, 'wuwa_inventory_kamera.scraping.service.ocr_service', ocr_service_module)


def _install_fake_debug_modules(monkeypatch) -> None:
    echo_workflow_module = ModuleType('wuwa_inventory_kamera.scraping.scanning.echo_workflow')
    echo_workflow_module._rarity_from_rgb_pixel = lambda _pixel: 5
    monkeypatch.setitem(sys.modules, 'wuwa_inventory_kamera.scraping.scanning.echo_workflow', echo_workflow_module)

    region_specs_module = ModuleType('wuwa_inventory_kamera.scraping.ocr.region_specs')

    class _FakeSpec:
        def preprocess(self, image, rarity=None):
            assert rarity == 5
            plane = image[:, :, 0] if image.ndim == 3 else image
            return SimpleNamespace(
                ocr_rgb=cv2.cvtColor(plane, cv2.COLOR_GRAY2RGB),
                signature_image=plane,
            )

        def _image_for_signature(self, image, rarity):
            assert rarity == 5
            plane = image[:, :, 1] if image.ndim == 3 else image
            return plane

    region_specs_module.get_spec = lambda _roi_key: _FakeSpec()
    monkeypatch.setitem(sys.modules, 'wuwa_inventory_kamera.scraping.ocr.region_specs', region_specs_module)


def _write_raw_echo_dir(raw_base, *, index: int, image: np.ndarray):
    echo_dir = raw_base / f'echo_{index:04d}'
    echo_dir.mkdir(parents=True)

    cv2.imwrite(str(echo_dir / 'full.png'), image)
    with open(echo_dir / 'meta.json', 'w', encoding='utf-8') as file_handle:
        json.dump(
            {
                'session_id': 'test-session',
                'index': index,
                'page': 0,
                'row': 0,
                'col': 0,
                'screen_width': 1920,
                'screen_height': 1080,
                'monitor': 1,
            },
            file_handle,
            indent=2,
        )
    return echo_dir


def _snapshot_debug_images(debug_dir):
    snapshots = {}
    for path in sorted(debug_dir.glob('*.png')):
        image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        assert image is not None, path.name
        snapshots[path.name] = image
    return snapshots


def test_reprocess_reconstructs_echo_name_crop(monkeypatch) -> None:
    _install_fake_reprocess_modules(monkeypatch)

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
        capture.card,
        cv2.cvtColor(image[0:2, 0:2], cv2.COLOR_RGB2BGR),
    )
    np.testing.assert_array_equal(
        capture.echo_name,
        cv2.cvtColor(image[2:4, 2:4], cv2.COLOR_RGB2BGR),
    )
    np.testing.assert_array_equal(
        capture.equipped,
        cv2.cvtColor(image[2:4, 4:6], cv2.COLOR_RGB2BGR),
    )


def test_reprocess_allows_custom_batch_size(monkeypatch) -> None:
    _install_fake_reprocess_modules(monkeypatch)

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
    _install_fake_reprocess_modules(monkeypatch)
    _install_fake_debug_modules(monkeypatch)

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
        'equipped.png',
        'equipped_preprocessed.png',
        'equipped_signature.png',
        'level.png',
        'level_preprocessed.png',
        'level_signature.png',
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
    equipped_raw = cv2.imread(str(debug_dir / 'equipped.png'), cv2.IMREAD_COLOR)
    level_raw = cv2.imread(str(debug_dir / 'level.png'), cv2.IMREAD_COLOR)
    stats_name_raw = cv2.imread(str(debug_dir / 'stats_name.png'), cv2.IMREAD_COLOR)
    stats_value_raw = cv2.imread(str(debug_dir / 'stats_value.png'), cv2.IMREAD_COLOR)
    np.testing.assert_array_equal(echo_name_raw, cv2.cvtColor(image[2:4, 2:4], cv2.COLOR_RGB2BGR))
    np.testing.assert_array_equal(equipped_raw, cv2.cvtColor(image[2:4, 4:6], cv2.COLOR_RGB2BGR))
    np.testing.assert_array_equal(level_raw, cv2.cvtColor(image[0:2, 4:6], cv2.COLOR_RGB2BGR))
    np.testing.assert_array_equal(stats_name_raw, cv2.cvtColor(image[0:2, 2:4], cv2.COLOR_RGB2BGR))
    np.testing.assert_array_equal(stats_value_raw, cv2.cvtColor(image[2:4, 0:2], cv2.COLOR_RGB2BGR))


def test_reprocess_write_debug_roundtrips_live_scan_artifacts(monkeypatch, tmp_path) -> None:
    _install_fake_reprocess_modules(monkeypatch)
    _install_fake_debug_modules(monkeypatch)

    raw_base = tmp_path / 'raw'
    images_by_index = {
        3: np.arange(6 * 6 * 3, dtype=np.uint8).reshape(6, 6, 3),
        4: (np.arange(6 * 6 * 3, dtype=np.uint8).reshape(6, 6, 3) + 37).astype(np.uint8),
    }
    expected_debug = {}

    for index, image in images_by_index.items():
        echo_dir = _write_raw_echo_dir(raw_base, index=index, image=image)
        _write_echo_debug_artifacts(
            SimpleNamespace(index=index),
            raw_base=raw_base,
            full_screenshot_space='bgr',
            detected_rarity=5,
            echo_name=image[2:4, 2:4],
            level=image[0:2, 4:6],
            stats_name=image[0:2, 2:4],
            stats_value=image[2:4, 0:2],
        )
        expected_debug[index] = _snapshot_debug_images(echo_dir / 'debug')

    _FakeOcrService.instances.clear()
    scans = loadRawScans(raw_base)
    assert [scan.index for scan in scans] == [3, 4]

    reprocess_echo_scans_with_service(
        scans=scans,
        providers=['CPUExecutionProvider'],
        min_rarity=5,
        min_level=21,
        write_debug=True,
        raw_base=raw_base,
    )

    for index, expected_images in expected_debug.items():
        actual_images = _snapshot_debug_images(raw_base / f'echo_{index:04d}' / 'debug')
        assert actual_images.keys() == expected_images.keys()
        for filename, expected_image in expected_images.items():
            np.testing.assert_array_equal(actual_images[filename], expected_image)


def test_reprocess_uses_shared_level_decision_for_sonata_slot(monkeypatch) -> None:
    class _FakeScreenInfoWithDistinctSlots:
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
                        icon=SimpleNamespace(x=0, y=4, w=2, h=2),
                    ),
                    level_XX=SimpleNamespace(
                        circle=SimpleNamespace(x=1.0, y=1.0),
                        icon=SimpleNamespace(x=2, y=4, w=2, h=2),
                    ),
                ),
                rarityColorPick=SimpleNamespace(x=0, y=0),
            )

    class _FakeOcrServiceWithArtifacts(_FakeOcrService):
        def ocr_adhoc_text(self, _image, _roi_key: str) -> str:
            return '25.'

    screen_info_module = ModuleType('wuwa_inventory_kamera.game.screen_info')
    screen_info_module.ScreenInfo = _FakeScreenInfoWithDistinctSlots
    monkeypatch.setitem(sys.modules, 'wuwa_inventory_kamera.game.screen_info', screen_info_module)

    ocr_service_module = ModuleType('wuwa_inventory_kamera.scraping.service.ocr_service')
    ocr_service_module.OcrService = _FakeOcrServiceWithArtifacts
    monkeypatch.setitem(sys.modules, 'wuwa_inventory_kamera.scraping.service.ocr_service', ocr_service_module)

    _FakeOcrService.instances.clear()
    image = np.arange(6 * 6 * 3, dtype=np.uint8).reshape(6, 6, 3)
    scan = _FakeScan(image)

    reprocess_echo_scans_with_service(
        scans=[scan],
        providers=['CPUExecutionProvider'],
        min_rarity=5,
        min_level=21,
        write_debug=False,
    )

    capture = _FakeOcrService.instances[0].submitted[0]
    assert capture.detected_level == 25
    np.testing.assert_array_equal(
        capture.sonata_icon,
        cv2.cvtColor(image[4:6, 2:4], cv2.COLOR_RGB2BGR),
    )


def test_reprocess_converts_level_crop_to_bgr_for_adhoc_ocr(monkeypatch) -> None:
    screen_info_module = ModuleType('wuwa_inventory_kamera.game.screen_info')
    screen_info_module.ScreenInfo = _FakeScreenInfo
    monkeypatch.setitem(sys.modules, 'wuwa_inventory_kamera.game.screen_info', screen_info_module)

    ocr_service_module = ModuleType('wuwa_inventory_kamera.scraping.service.ocr_service')
    ocr_service_module.OcrService = _RecordingOcrService
    monkeypatch.setitem(sys.modules, 'wuwa_inventory_kamera.scraping.service.ocr_service', ocr_service_module)

    _RecordingOcrService.instances.clear()
    image = np.arange(6 * 6 * 3, dtype=np.uint8).reshape(6, 6, 3)
    scan = _FakeScan(image)

    reprocess_echo_scans_with_service(
        scans=[scan],
        providers=['CPUExecutionProvider'],
        min_rarity=5,
        min_level=21,
        write_debug=False,
    )

    service = _RecordingOcrService.instances[0]
    assert len(service.adhoc_calls) == 1
    adhoc_image, roi_key = service.adhoc_calls[0]
    assert roi_key == 'echoes.level'
    np.testing.assert_array_equal(
        adhoc_image,
        cv2.cvtColor(image[0:2, 4:6], cv2.COLOR_RGB2BGR),
    )