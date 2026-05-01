from __future__ import annotations

import numpy as np

from wuwa_inventory_kamera.scraping.service.echo_ocr_cache import EchoOcrCache
from wuwa_inventory_kamera.scraping.service.ocr_service import OcrService


def _image(seed: int) -> np.ndarray:
    return np.full((4, 6, 3), seed, dtype=np.uint8)


def _ocr_result(label: str):
    return [
        (
            label,
            0.99,
            np.asarray([[0, 0], [5, 0], [5, 1], [0, 1]], dtype=np.float32),
        )
    ]


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