"""
scraping.ocr._rapidocr
~~~~~~~~~~~~~~~~~~~~~~

Adapter that wraps ``rapidocr_onnxruntime.RapidOCR`` to satisfy the
:class:`~scraping.ocr.OcrBackend` protocol.
"""
from __future__ import annotations

import contextlib
import logging

import numpy as np

from scraping.ocr._types import OcrResult

logger = logging.getLogger(__name__)

# Minimum Y-centre distance (pixels) for two bounding boxes to be considered
# distinct tokens when merging multi-pass results.
_DEDUP_Y_THRESHOLD = 15


def _y_center(bbox) -> float:
    return sum(pt[1] for pt in bbox) / len(bbox)


def _merge_unique(base: list[OcrResult], candidates: list[OcrResult]) -> list[OcrResult]:
    """
    Append tokens from *candidates* whose Y-centre is absent in *base*
    (within ``_DEDUP_Y_THRESHOLD`` pixels).  Returns the extended list.
    """
    existing_ys = [_y_center(bbox) for bbox, _, _ in base]
    added = list(base)
    for bbox, text, conf in candidates:
        yc = _y_center(bbox)
        if all(abs(yc - ey) > _DEDUP_Y_THRESHOLD for ey in existing_ys):
            added.append((bbox, text, conf))
            existing_ys.append(yc)
            logger.debug(
                'RapidOCR merge: added token %r (y=%.1f) missing from previous pass',
                text, yc,
            )
    return added


class RapidOcrBackend:
    """
    Wraps ``rapidocr_onnxruntime.RapidOCR`` as an :class:`~scraping.ocr.OcrBackend`.

    All constructor keyword arguments are forwarded directly to ``RapidOCR``,
    giving access to the full upstream parameterisation::

        RapidOcrBackend()                            # library defaults
        RapidOcrBackend(text_score=0.6)              # lower confidence threshold
        RapidOcrBackend(use_angle_cls=True)          # enable angle classification

    Two recognition modes are available:

    * :meth:`recognize` — **fast single pass** on the original image.  This is
      the normal path called by the OCR plumbing.
    * :meth:`thorough_recognize` — **multi-pass** that additionally runs on an
      edge-padded copy (to catch text flush against the boundary) and with a
      lower ``text_score`` threshold (to catch interior low-confidence tokens).
      Use this as a retry when semantic validation of the fast-pass result fails.

    Parameters
    ----------
    pad_px:
        Padding (pixels) used in :meth:`thorough_recognize`.  Text that sits
        flush against the image boundary is often missed by the detector;
        a small border provides the context it needs.  Defaults to ``10``.
    fallback_text_score:
        ``text_score`` used by the low-confidence fallback pass inside
        :meth:`thorough_recognize`.  Defaults to ``0.3``.  Set to ``None``
        to disable the fallback pass.
    onnx_providers:
        List of ONNX Runtime execution provider names to use for every model
        session, overriding the library's built-in CUDA-or-CPU selection.
        Example: ``['DmlExecutionProvider', 'CPUExecutionProvider']`` to use
        DirectML (GPU on Windows via DirectX 12) with a CPU fallback.
        When ``None`` (default), the library's normal provider selection is
        used (CPU, or CUDA if ``use_cuda=True`` and CUDA is available).
    **kwargs:
        Forwarded verbatim to ``RapidOCR(**kwargs)``.
    """

    @staticmethod
    @contextlib.contextmanager
    def _provider_patch(providers: list):
        """
        Temporarily monkey-patch ``OrtInferSession.__init__`` so that every
        ONNX session created while the context is active uses *providers*
        instead of the library's hardcoded CUDA-or-CPU logic.
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
        from rapidocr_onnxruntime import RapidOCR  # deferred — keeps top-level import fast
        ctx = self._provider_patch(onnx_providers) if onnx_providers else contextlib.nullcontext()
        with ctx:
            self._ocr = RapidOCR(**kwargs)
        self._pad_px = pad_px
        self._kwargs = dict(kwargs)

        if fallback_text_score is not None:
            fallback_kwargs = dict(kwargs)
            fallback_kwargs['text_score'] = fallback_text_score
            with (self._provider_patch(onnx_providers) if onnx_providers else contextlib.nullcontext()):
                self._fallback_ocr: RapidOCR | None = RapidOCR(**fallback_kwargs)
            self._fallback_text_score: float | None = fallback_text_score
        else:
            self._fallback_ocr = None
            self._fallback_text_score = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_once(self, image: np.ndarray, ocr=None) -> list[OcrResult]:
        """Run OCR on *image* exactly once and normalise confidence values."""
        if ocr is None:
            ocr = self._ocr
        raw, _elapsed = ocr(image)
        if not raw:
            return []
        return [(bbox, text, float(conf)) for bbox, text, conf in raw]

    def _padded_results(self, image: np.ndarray) -> list[OcrResult]:
        """Return OCR results from an edge-padded image, with coords shifted back."""
        p = self._pad_px
        pad_width = ((p, p), (p, p)) if image.ndim == 2 else ((p, p), (p, p), (0, 0))
        padded = np.pad(image, pad_width, mode='edge')
        raw = self._run_once(padded)
        return [
            ([[pt[0] - p, pt[1] - p] for pt in bbox], text, conf)
            for bbox, text, conf in raw
        ]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def recognize(self, image: np.ndarray) -> list[OcrResult]:
        """
        Fast single-pass OCR on *image*.

        Runs ``RapidOCR`` once on the image as-is and returns the results
        sorted by Y-centre.  This is the hot-path used during normal scanning.
        """
        results = self._run_once(image)
        results.sort(key=lambda r: _y_center(r[0]))
        return results

    def thorough_recognize(self, image: np.ndarray) -> list[OcrResult]:
        """
        Multi-pass OCR that maximises recall at the cost of extra time.

        Runs three passes and merges results by Y-centre deduplication:

        1. **Primary pass** — same as :meth:`recognize`.
        2. **Padded pass** — edge-padded image to catch tokens flush against
           the image boundary.
        3. **Fallback pass** — primary image with a lower ``text_score``
           threshold to catch low-confidence interior tokens (skipped when
           ``fallback_text_score=None``).

        Each subsequent pass only contributes tokens whose Y-centre is more
        than ``_DEDUP_Y_THRESHOLD`` pixels from every already-present token.
        The final list is sorted by Y-centre.

        Use this method as a retry when semantic validation of a fast-pass
        result has flagged errors or suspicious values.
        """
        merged = self._run_once(image)

        if self._pad_px:
            merged = _merge_unique(merged, self._padded_results(image))

        if self._fallback_ocr is not None:
            merged = _merge_unique(
                merged, self._run_once(image, ocr=self._fallback_ocr)
            )

        merged.sort(key=lambda r: _y_center(r[0]))
        return merged

    def __repr__(self) -> str:
        parts = [f'pad_px={self._pad_px!r}']
        if self._fallback_text_score is not None:
            parts.append(f'fallback_text_score={self._fallback_text_score!r}')
        parts += [f'{k}={v!r}' for k, v in self._kwargs.items()]
        return f"RapidOcrBackend({', '.join(parts)})"
