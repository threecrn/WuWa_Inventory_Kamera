"""
wuwa_inventory_kamera.scraping.ocr._rapidocr
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Adapter that wraps ``rapidocr_onnxruntime.RapidOCR`` to satisfy the
:class:`~wuwa_inventory_kamera.scraping.ocr.OcrBackend` protocol.
"""
from __future__ import annotations

import contextlib
import logging

import numpy as np

from ._types import OcrResult

logger = logging.getLogger(__name__)

_DEDUP_Y_THRESHOLD = 15


def _y_center(bbox) -> float:
    return sum(pt[1] for pt in bbox) / len(bbox)


def _merge_unique(base: list[OcrResult], candidates: list[OcrResult]) -> list[OcrResult]:
    existing_ys = [_y_center(bbox) for bbox, _, _ in base]
    added = list(base)
    for bbox, text, conf in candidates:
        yc = _y_center(bbox)
        if all(abs(yc - ey) > _DEDUP_Y_THRESHOLD for ey in existing_ys):
            added.append((bbox, text, conf))
            existing_ys.append(yc)
    return added


class RapidOcrBackend:
    """
    Wraps ``rapidocr_onnxruntime.RapidOCR`` as an
    :class:`~wuwa_inventory_kamera.scraping.ocr.OcrBackend`.

    Parameters
    ----------
    pad_px:
        Padding (pixels) used in :meth:`thorough_recognize`.
    fallback_text_score:
        ``text_score`` used by the low-confidence fallback pass inside
        :meth:`thorough_recognize`.  ``None`` disables the fallback pass.
    onnx_providers:
        List of ONNX Runtime execution provider names, e.g.
        ``['DmlExecutionProvider', 'CPUExecutionProvider']``.
        When ``None``, the library's default provider selection is used.
    **kwargs:
        Forwarded verbatim to ``RapidOCR(**kwargs)``.
    """

    @staticmethod
    @contextlib.contextmanager
    def _provider_patch(providers: list):
        """
        Temporarily monkey-patch ``OrtInferSession.__init__`` so that every
        ONNX session created inside the context uses *providers*.
        """
        from rapidocr_onnxruntime import utils as _rutils
        from onnxruntime import InferenceSession, SessionOptions, GraphOptimizationLevel

        _orig_init = _rutils.OrtInferSession.__init__

        def _patched_init(self, config):
            sess_opt = SessionOptions()
            sess_opt.log_severity_level = 4
            sess_opt.enable_cpu_mem_arena = False
            sess_opt.graph_optimization_level = GraphOptimizationLevel.ORT_ENABLE_ALL
            _rutils.OrtInferSession._verify_model(config['model_path'])
            self.session = InferenceSession(
                config['model_path'],
                sess_options=sess_opt,
                providers=providers,
            )
            logger.debug(
                'RapidOCR session providers (patched): %s',
                self.session.get_providers(),
            )

        _rutils.OrtInferSession.__init__ = _patched_init
        try:
            yield
        finally:
            _rutils.OrtInferSession.__init__ = _orig_init

    def __init__(
        self,
        pad_px: int = 10,
        fallback_text_score: float | None = 0.3,
        onnx_providers: list | None = None,
        **kwargs,
    ):
        from rapidocr_onnxruntime import RapidOCR
        ctx = self._provider_patch(onnx_providers) if onnx_providers else contextlib.nullcontext()
        with ctx:
            self._ocr = RapidOCR(**kwargs)
        self._pad_px = pad_px
        self._kwargs = dict(kwargs)

        if fallback_text_score is not None:
            fallback_kwargs = dict(kwargs)
            fallback_kwargs['text_score'] = fallback_text_score
            with (self._provider_patch(onnx_providers) if onnx_providers else contextlib.nullcontext()):
                self._fallback_ocr = RapidOCR(**fallback_kwargs)
            self._fallback_text_score: float | None = fallback_text_score
        else:
            self._fallback_ocr = None
            self._fallback_text_score = None

    def _run_once(self, image: np.ndarray, ocr=None) -> list[OcrResult]:
        if ocr is None:
            ocr = self._ocr
        raw, _elapsed = ocr(image)
        if not raw:
            return []
        return [(bbox, text, float(conf)) for bbox, text, conf in raw]

    def _padded_results(self, image: np.ndarray) -> list[OcrResult]:
        p = self._pad_px
        pad_width = ((p, p), (p, p)) if image.ndim == 2 else ((p, p), (p, p), (0, 0))
        padded = np.pad(image, pad_width, mode='edge')
        raw = self._run_once(padded)
        return [
            ([[pt[0] - p, pt[1] - p] for pt in bbox], text, conf)
            for bbox, text, conf in raw
        ]

    def recognize(self, image: np.ndarray) -> list[OcrResult]:
        """Fast single-pass OCR, sorted by Y-centre."""
        results = self._run_once(image)
        results.sort(key=lambda r: _y_center(r[0]))
        return results

    def thorough_recognize(self, image: np.ndarray) -> list[OcrResult]:
        """
        Multi-pass OCR for maximum recall.

        Three passes (primary, padded, fallback-score) merged by Y-centre
        deduplication.
        """
        primary = self._run_once(image)
        merged = list(primary)

        padded = self._padded_results(image)
        merged = _merge_unique(merged, padded)

        if self._fallback_ocr is not None:
            fallback = self._run_once(image, ocr=self._fallback_ocr)
            merged = _merge_unique(merged, fallback)

        merged.sort(key=lambda r: _y_center(r[0]))
        return merged
