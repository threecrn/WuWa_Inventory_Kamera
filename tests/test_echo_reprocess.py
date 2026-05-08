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
        _ = args, kwargs
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
    def __init__(self, image: np.ndarray) -> None:
        self.index = 3
        self.screen_width = 1920
        self.screen_height = 1080
        self.full_screenshot = image

    def load_images(self) -> None:
        return None


class _FakeScreenInfo:
    def __init__(self, _width: int, _height: int) -> None:
        self.echoes = SimpleNamespace(
            echoCard=SimpleNamespace(x=0, y=0, w=2, h=2),
            fullStatsName=SimpleNamespace(x=2, y=0, w=2, h=2),
            fullStatsValue=SimpleNamespace(x=0, y=2, w=2, h=2),
            echoName=SimpleNamespace(x=2, y=2, w=2, h=2),
            sonataIcon=SimpleNamespace(x=4, y=0, w=2, h=2),
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
    capture = _FakeOcrService.instances[0].submitted[0]
    np.testing.assert_array_equal(
        capture.echo_name,
        cv2.cvtColor(image[2:4, 2:4], cv2.COLOR_RGB2BGR),
    )