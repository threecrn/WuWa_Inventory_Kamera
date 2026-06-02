from __future__ import annotations

import numpy as np
import pytest

from wuwa_inventory_kamera.imgio import (
    ColorCode,
    ImreadMode,
    Interpolation,
    backend_name,
    convert_color,
    imread,
    imwrite,
    lut,
    resize,
    set_backend,
    warp_perspective,
)

pytest.importorskip("cv2")


@pytest.fixture(autouse=True)
def _use_cv2_backend() -> None:
    set_backend("cv2")


def test_backend_name_reports_cv2() -> None:
    assert backend_name() == "cv2"


def test_imread_imwrite_roundtrip(tmp_path) -> None:
    sample = np.zeros((4, 5, 3), dtype=np.uint8)
    sample[1, 2] = (3, 7, 240)
    path = tmp_path / "sample.png"

    assert imwrite(path, sample) is True
    loaded = imread(path, mode=ImreadMode.COLOR)

    assert loaded is not None
    assert loaded.shape == sample.shape
    assert loaded.dtype == np.uint8


def test_convert_color_bgr_rgb_roundtrip() -> None:
    bgr = np.array([[[1, 2, 200]]], dtype=np.uint8)

    rgb = convert_color(bgr, ColorCode.BGR2RGB)
    back = convert_color(rgb, ColorCode.RGB2BGR)

    assert tuple(rgb[0, 0]) == (200, 2, 1)
    assert np.array_equal(back, bgr)


def test_resize_and_lut() -> None:
    gray = np.array([[0, 64], [128, 255]], dtype=np.uint8)
    up = resize(gray, (4, 4), interpolation=Interpolation.NEAREST)

    assert up.shape == (4, 4)

    table = np.arange(255, -1, -1, dtype=np.uint8)
    inverted = lut(gray, table)
    assert inverted.tolist() == [[255, 191], [127, 0]]


def test_warp_perspective_identity_shape_dtype() -> None:
    image = np.zeros((5, 7, 3), dtype=np.uint8)
    image[2, 3] = (50, 100, 150)
    src_quad = np.array([[0, 0], [6, 0], [6, 4], [0, 4]], dtype=np.float32)

    warped = warp_perspective(image, src_quad, (7, 5))

    assert warped.shape == image.shape
    assert warped.dtype == np.uint8
