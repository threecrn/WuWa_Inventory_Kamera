from __future__ import annotations

from types import SimpleNamespace

import cv2
import numpy as np

import wuwa_inventory_kamera.game.screen as screen_module
from wuwa_inventory_kamera.cli.nav import NavSession


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