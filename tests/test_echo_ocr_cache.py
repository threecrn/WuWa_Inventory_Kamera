from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from wuwa_inventory_kamera.scraping.service.echo_ocr_cache import EchoOcrCache
from wuwa_inventory_kamera.scraping.service.ocr_service import OcrService


_SCREENSHOT_DIR = Path(__file__).resolve().parents[1] / 'screenshots' / 'echo_cache_problem'


def _image(seed: int) -> np.ndarray:
    return np.full((4, 6, 3), seed, dtype=np.uint8)


def _stat_image(
    background: tuple[int, int, int],
    shimmer: tuple[int, int, int],
    *,
    glyph: str,
) -> np.ndarray:
    image = np.full((18, 48, 3), background, dtype=np.uint8)
    image[1::4, :] = np.asarray(shimmer, dtype=np.uint8)
    image[:, 3::11] = np.asarray(
        [
            min(background[0] + 12, 255),
            min(background[1] + 14, 255),
            min(background[2] + 16, 255),
        ],
        dtype=np.uint8,
    )
    text = np.asarray([249, 255, 255], dtype=np.uint8)

    if glyph == 'atk':
        image[4:14, 6:9] = text
        image[4:7, 9:18] = text
        image[8:10, 9:16] = text
        image[11:14, 9:20] = text
    elif glyph == 'hp':
        image[4:14, 6:9] = text
        image[4:14, 15:18] = text
        image[8:10, 9:15] = text
    else:
        raise ValueError(f'Unsupported glyph: {glyph}')

    return image


def _ocr_result(label: str):
    return [
        (
            label,
            0.99,
            np.asarray([[0, 0], [5, 0], [5, 1], [0, 1]], dtype=np.float32),
        )
    ]


def _problem_image(name: str) -> np.ndarray:
    image = cv2.imread(str(_SCREENSHOT_DIR / name), cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(_SCREENSHOT_DIR / name)
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


class _FakeBatchOcr:
    def __init__(self) -> None:
        self.calls: list[list[np.ndarray]] = []

    def ocr_images(self, images: list[np.ndarray]):
        self.calls.append(images)
        return [_ocr_result(f'image-{int(image[0, 0, 0])}') for image in images]


def test_echo_ocr_cache_round_trip_across_instances(tmp_path):
    db_path = tmp_path / 'echo-cache.sqlite3'
    source = [_image(7)]
    expected = [_ocr_result('cached-name')]

    cache = EchoOcrCache(db_path)
    cache.store_many('echo_stats_name', source, expected)
    cache.close()

    reopened = EchoOcrCache(db_path)
    _keys, cached, misses = reopened.lookup_many('echo_stats_name', source)
    reopened.close()

    assert misses == []
    assert cached[0] is not None
    assert cached[0][0][0] == 'cached-name'
    assert cached[0][0][1] == 0.99
    np.testing.assert_array_equal(cached[0][0][2], expected[0][0][2])


def test_echo_ocr_cache_keeps_crop_kinds_separate(tmp_path):
    cache = EchoOcrCache(tmp_path / 'echo-cache.sqlite3')
    image = [_image(11)]
    cache.store_many('echo_stats_name', image, [_ocr_result('name-side')])

    _keys, cached, misses = cache.lookup_many('echo_stats_value', image)
    cache.close()

    assert cached == [None]
    assert misses == [0]


def test_ocr_service_uses_cache_before_batch_ocr(tmp_path):
    service = object.__new__(OcrService)
    service._batch_ocr = _FakeBatchOcr()
    service._echo_stat_cache = EchoOcrCache(tmp_path / 'echo-cache.sqlite3')
    images = [_image(3), _image(9)]

    try:
        first = OcrService._ocr_images_with_cache(service, 'echo_stats_name', images)
        second = OcrService._ocr_images_with_cache(service, 'echo_stats_name', images)
    finally:
        service._echo_stat_cache.close()

    assert len(service._batch_ocr.calls) == 1
    assert first[0][0][0] == 'image-3'
    assert first[1][0][0] == 'image-9'
    assert second[0][0][0] == 'image-3'
    assert second[1][0][0] == 'image-9'


def test_echo_ocr_cache_ignores_background_animation(tmp_path):
    cache = EchoOcrCache(tmp_path / 'echo-cache.sqlite3')
    first = [_stat_image((53, 68, 80), (74, 92, 111), glyph='atk')]
    second = [_stat_image((113, 141, 157), (96, 118, 136), glyph='atk')]

    try:
        cache.store_many('echo_stats_name', first, [_ocr_result('cached-name')])
        _keys, cached, misses = cache.lookup_many('echo_stats_name', second)
    finally:
        cache.close()

    assert misses == []
    assert cached[0] is not None
    assert cached[0][0][0] == 'cached-name'


def test_echo_ocr_cache_matches_real_stat_name_capture_pair(tmp_path):
    cache = EchoOcrCache(tmp_path / 'echo-cache.sqlite3')
    first = [_problem_image('echo_0000_name_a.png')]
    second = [_problem_image('echo_0000_name_b.png')]

    try:
        cache.store_many('echo_stats_name', first, [_ocr_result('cached-name')])
        _keys, cached, misses = cache.lookup_many('echo_stats_name', second)
    finally:
        cache.close()

    assert misses == []
    assert cached[0] is not None
    assert cached[0][0][0] == 'cached-name'


def test_echo_ocr_cache_keeps_distinct_text_shapes_separate(tmp_path):
    cache = EchoOcrCache(tmp_path / 'echo-cache.sqlite3')
    first = [_stat_image((53, 68, 80), (74, 92, 111), glyph='atk')]
    second = [_stat_image((53, 68, 80), (74, 92, 111), glyph='hp')]

    try:
        cache.store_many('echo_stats_name', first, [_ocr_result('atk-text')])
        _keys, cached, misses = cache.lookup_many('echo_stats_name', second)
    finally:
        cache.close()

    assert cached == [None]
    assert misses == [0]


def test_ocr_service_cache_survives_background_animation(tmp_path):
    service = object.__new__(OcrService)
    service._batch_ocr = _FakeBatchOcr()
    service._echo_stat_cache = EchoOcrCache(tmp_path / 'echo-cache.sqlite3')
    first_images = [
        _stat_image((53, 68, 80), (74, 92, 111), glyph='atk'),
        _stat_image((53, 68, 80), (74, 92, 111), glyph='hp'),
    ]
    second_images = [
        _stat_image((113, 141, 157), (96, 118, 136), glyph='atk'),
        _stat_image((113, 141, 157), (96, 118, 136), glyph='hp'),
    ]

    try:
        first = OcrService._ocr_images_with_cache(service, 'echo_stats_value', first_images)
        second = OcrService._ocr_images_with_cache(service, 'echo_stats_value', second_images)
    finally:
        service._echo_stat_cache.close()

    assert len(service._batch_ocr.calls) == 1
    assert second[0][0][0] == first[0][0][0]
    assert second[1][0][0] == first[1][0][0]