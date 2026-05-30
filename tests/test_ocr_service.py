from __future__ import annotations

from types import SimpleNamespace

import numpy as np

import wuwa_inventory_kamera.scraping.service.ocr_service as ocr_service_module


class _FakeCache:
    def __init__(self) -> None:
        self.stored = None
        self.latencies: list[tuple[str, float]] = []

    def lookup_many(self, spec, images_bgr, rarity=None):
        _ = spec, rarity
        return [f'key-{index}' for index, _image in enumerate(images_bgr)], [None] * len(images_bgr), list(range(len(images_bgr)))

    def store_many(self, spec, images, results, rarity=None, keys=None):
        _ = spec, images, rarity, keys
        self.stored = results

    def record_ocr_latency(self, roi_key: str, elapsed: float) -> None:
        self.latencies.append((roi_key, elapsed))


def test_ocr_with_spec_uses_single_line_backend(monkeypatch) -> None:
    image = np.zeros((6, 12, 3), dtype=np.uint8)
    cache = _FakeCache()
    single_line_calls: list[np.ndarray] = []

    spec = SimpleNamespace(
        single_line=True,
        allowed_chars=None,
        preprocess=lambda input_image, rarity=None: SimpleNamespace(ocr_rgb=input_image),
    )
    monkeypatch.setattr(ocr_service_module, 'get_spec', lambda _roi_key: spec)

    service = ocr_service_module.OcrService.__new__(ocr_service_module.OcrService)
    service._ocr_cache = cache
    service._backend = SimpleNamespace(
        recognize_single_line=lambda input_image: single_line_calls.append(input_image.copy()) or [
            (
                [[0, 0], [11, 0], [11, 5], [0, 5]],
                'Owned 84',
                0.91,
            )
        ],
    )
    service._batch_ocr = SimpleNamespace(
        ocr_images=lambda _images: (_ for _ in ()).throw(
            AssertionError('batch OCR should not be used for single-line specs')
        )
    )

    results = service._ocr_with_spec('items.value', [image])

    assert len(single_line_calls) == 1
    assert np.array_equal(single_line_calls[0], image)
    assert results[0][0][0] == 'Owned 84'
    assert results[0][0][1] == 0.91
    assert cache.stored is not None
    assert cache.stored[0][0][0] == 'Owned 84'