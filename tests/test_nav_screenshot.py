from __future__ import annotations

from types import SimpleNamespace

import cv2
import numpy as np

import wuwa_inventory_kamera.game.screen as screen_module
from wuwa_inventory_kamera.cli.nav import NavSession
from wuwa_inventory_kamera.game.game_roi import Coordinates
from wuwa_inventory_kamera.game.screen_info import ScreenInfoObject


def _make_session(tmp_path) -> NavSession:
    session = object.__new__(NavSession)
    session.nav = SimpleNamespace(layout=SimpleNamespace())
    session.gw = SimpleNamespace()
    session.screenshot_dir = tmp_path
    session.dry_run = False
    session._page_0 = 0
    session._cell = None
    session._ocr_backend = None
    return session


def test_nav_screenshot_preserves_bgr_capture(monkeypatch, tmp_path) -> None:
    bgr = np.array(
        [
            [[0, 0, 255], [0, 255, 0]],
            [[255, 0, 0], [9, 10, 11]],
        ],
        dtype=np.uint8,
    )
    monkeypatch.setattr(screen_module, 'capture', lambda _gw: bgr.copy())

    session = _make_session(tmp_path)

    np.testing.assert_array_equal(session.screenshot(as_image=True), bgr)

    out_path = tmp_path / 'nav-shot.png'
    result = session.screenshot(out=out_path)

    assert result['saved'] == str(out_path)
    saved = cv2.imread(str(out_path), cv2.IMREAD_COLOR)
    assert saved is not None
    np.testing.assert_array_equal(saved, bgr)


def test_nav_screenshot_captures_section_bounding_box(monkeypatch, tmp_path) -> None:
    captured = []

    def _capture_region(_gw, roi):
        captured.append(roi)
        return np.zeros((int(roi.h), int(roi.w), 3), dtype=np.uint8)

    monkeypatch.setattr(screen_module, 'capture_region', _capture_region)

    session = _make_session(tmp_path)
    session.nav.layout.echoes = ScreenInfoObject(
        {
            'page': Coordinates(10, 20, 30, 40),
            'mouseMovement': Coordinates(400, 500),
            'sonataIcon': {
                'radius': 14.5,
                'level_X': {
                    'circle': Coordinates(14.5, 14.5),
                    'icon': Coordinates(100, 200, 50, 60),
                },
                'level_XX': {
                    'circle': Coordinates(14.5, 14.5),
                    'icon': Coordinates(160, 210, 40, 30),
                },
            },
        }
    )

    result = session.screenshot(roi='echoes', as_image=True)

    assert result.shape == (240, 190, 3)
    assert len(captured) == 1
    assert captured[0].x == 10
    assert captured[0].y == 20
    assert captured[0].w == 190
    assert captured[0].h == 240


def test_nav_screenshot_ignores_local_helper_coordinates_for_nested_roi(monkeypatch, tmp_path) -> None:
    captured = []

    def _capture_region(_gw, roi):
        captured.append(roi)
        return np.zeros((int(roi.h), int(roi.w), 3), dtype=np.uint8)

    monkeypatch.setattr(screen_module, 'capture_region', _capture_region)

    session = _make_session(tmp_path)
    session.nav.layout.echoes = ScreenInfoObject(
        {
            'sonataIcon': {
                'radius': 14.5,
                'level_X': {
                    'circle': Coordinates(14.5, 14.5),
                    'icon': Coordinates(100, 200, 50, 60),
                },
                'level_XX': {
                    'circle': Coordinates(14.5, 14.5),
                    'icon': Coordinates(160, 210, 40, 30),
                },
            },
        }
    )

    result = session.screenshot(roi='sonata-icon', as_image=True)

    assert result.shape == (60, 100, 3)
    assert len(captured) == 1
    assert captured[0].x == 100
    assert captured[0].y == 200
    assert captured[0].w == 100
    assert captured[0].h == 60