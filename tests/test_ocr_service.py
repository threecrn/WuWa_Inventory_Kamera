from __future__ import annotations

import concurrent.futures
from types import SimpleNamespace

import numpy as np

import wuwa_inventory_kamera.scraping.service.ocr_service as ocr_service_module
from wuwa_inventory_kamera.scraping.service.captures import CharCapture


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


def test_process_chars_uses_canonical_character_roi_keys(monkeypatch) -> None:
    capture = CharCapture(
        char_index=27,
        section=0,
        crops={
            'name': np.zeros((2, 2, 3), dtype=np.uint8),
            'level': np.zeros((2, 2, 3), dtype=np.uint8),
        },
    )
    future: concurrent.futures.Future = concurrent.futures.Future()

    observed_roi_keys: list[str] = []

    service = ocr_service_module.OcrService.__new__(ocr_service_module.OcrService)

    def _fake_ocr_with_spec(roi_key: str, images, rarity=None):
        _ = rarity
        observed_roi_keys.append(roi_key)
        return [[] for _ in images]

    service._ocr_with_spec = _fake_ocr_with_spec
    service._char_asm = SimpleNamespace(
        assemble=lambda _cap, *_tokens: SimpleNamespace(char_index=27, section=0, fields={'level': 80})
    )

    queue_item = SimpleNamespace(capture=capture, future=future)
    service._process_chars([queue_item])

    assert observed_roi_keys == ['characters.resonatorName', 'characters.resonatorLevel']
    assert future.done()