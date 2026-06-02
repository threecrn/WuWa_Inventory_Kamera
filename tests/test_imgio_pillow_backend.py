from __future__ import annotations

import numpy as np
import pytest

from wuwa_inventory_kamera.imgio import (
    ColorCode,
    ImreadMode,
    backend_capabilities,
    backend_name,
    bounding_rect,
    circle,
    convert_color,
    find_nonzero,
    imread,
    imwrite,
    in_range,
    line,
    match_template,
    put_text,
    rectangle,
    set_backend,
    warp_perspective,
)
from wuwa_inventory_kamera.imgio.errors import ImgioUnsupportedOperationError

pytest.importorskip('PIL')


@pytest.fixture(autouse=True)
def _use_pillow_backend() -> None:
    set_backend('pillow')


def test_backend_name_and_capabilities() -> None:
    caps = backend_capabilities()

    assert backend_name() == 'pillow'
    assert caps.io_basic is True
    assert caps.resize is True
    assert caps.mask_ops is True
    assert caps.draw is True
    assert caps.template_matching is False
    assert caps.perspective_warp is False


def test_imread_imwrite_color_roundtrip(tmp_path) -> None:
    sample_bgr = np.zeros((5, 6, 3), dtype=np.uint8)
    sample_bgr[1, 2] = (15, 70, 200)
    out_path = tmp_path / 'sample.png'

    assert imwrite(out_path, sample_bgr) is True

    loaded = imread(out_path, mode=ImreadMode.COLOR)
    assert loaded is not None
    assert loaded.shape == sample_bgr.shape
    assert loaded.dtype == np.uint8
    assert tuple(loaded[1, 2]) == (15, 70, 200)


def test_mask_ops_find_nonzero_and_bbox() -> None:
    image = np.array(
        [
            [[0, 0, 0], [5, 10, 15], [40, 40, 40]],
            [[0, 0, 0], [5, 10, 15], [80, 80, 80]],
            [[0, 0, 0], [0, 0, 0], [0, 0, 0]],
        ],
        dtype=np.uint8,
    )

    mask = in_range(
        image,
        np.array([4, 9, 14], dtype=np.uint8),
        np.array([6, 11, 16], dtype=np.uint8),
    )

    points = find_nonzero(mask)
    assert points is not None
    assert bounding_rect(points) == (1, 0, 1, 2)


def test_draw_helpers_mutate_pixels() -> None:
    canvas = np.zeros((40, 40, 3), dtype=np.uint8)

    canvas = line(canvas, (1, 1), (35, 1), (0, 0, 255), thickness=2)
    canvas = rectangle(canvas, (5, 5), (20, 20), (0, 255, 0), thickness=1)
    canvas = circle(canvas, (30, 30), 5, (255, 0, 0), thickness=-1)
    canvas = put_text(canvas, 'X', (8, 30), 1.0, (255, 255, 255), thickness=1)

    assert int(np.count_nonzero(canvas)) > 0


def test_unsupported_advanced_ops_raise() -> None:
    image = np.zeros((6, 6, 3), dtype=np.uint8)
    template = np.zeros((3, 3, 3), dtype=np.uint8)
    quad = np.array([[0, 0], [5, 0], [5, 5], [0, 5]], dtype=np.float32)

    with pytest.raises(ImgioUnsupportedOperationError):
        match_template(image, template)

    with pytest.raises(ImgioUnsupportedOperationError):
        warp_perspective(image, quad, (6, 6))

    with pytest.raises(ImgioUnsupportedOperationError):
        convert_color(image, ColorCode.BGR2LAB)
