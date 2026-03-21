"""
scraping.ocr._rapidocr
~~~~~~~~~~~~~~~~~~~~~~

Adapter that wraps ``rapidocr_onnxruntime.RapidOCR`` to satisfy the
:class:`~scraping.ocr.OcrBackend` protocol.
"""
from __future__ import annotations

import logging

import numpy as np

from scraping.ocr._types import OcrResult

logger = logging.getLogger(__name__)

# Minimum Y-centre distance (pixels) for two bounding boxes to be considered
# distinct tokens when merging dual-pass results.
_DEDUP_Y_THRESHOLD = 15


def _y_center(bbox) -> float:
    return sum(pt[1] for pt in bbox) / len(bbox)


class RapidOcrBackend:
    """
    Wraps ``rapidocr_onnxruntime.RapidOCR`` as an :class:`~scraping.ocr.OcrBackend`.

    All constructor keyword arguments are forwarded directly to ``RapidOCR``,
    giving access to the full upstream parameterisation::

        RapidOcrBackend()                            # library defaults
        RapidOcrBackend(text_score=0.6)              # lower confidence threshold
        RapidOcrBackend(use_angle_cls=True)          # enable angle classification
        RapidOcrBackend(
            det_model_path='path/to/det.onnx',
            rec_model_path='path/to/rec.onnx',
        )

    The ``RapidOCR`` import is deferred to ``__init__`` time so that simply
    importing ``scraping.ocr`` does not eagerly load the ONNX runtime.

    Parameters
    ----------
    pad_px:
        When non-zero, :meth:`recognize` runs RapidOCR **twice**: once on the
        original image and once on a copy padded by *pad_px* pixels on every
        side using ``mode='edge'``.  Results from the padded pass are merged
        into the original-pass results, adding only tokens whose Y-centre
        differs from every already-present token by more than
        ``_DEDUP_Y_THRESHOLD`` pixels.  This catches text that sits flush
        against the image boundary (which the detector often misses on the
        original) **without** disturbing the detection of interior tokens
        (which padding can sometimes suppress).  Defaults to ``10``.
    fallback_text_score:
        When set, a second ``RapidOCR`` instance is created with
        ``text_score`` overridden to this value (everything else is
        identical to the primary instance).  After the primary + padded
        passes, the fallback instance runs on the *original* image and any
        tokens whose Y-centre is not already represented (within
        ``_DEDUP_Y_THRESHOLD`` pixels) are appended.  This recovers
        interior tokens that fall below the primary confidence threshold
        without noising up tokens that were already detected.  Defaults to
        ``0.3``.  Pass ``None`` to disable.
    **kwargs:
        Forwarded verbatim to ``RapidOCR(**kwargs)``.
    """

    def __init__(self, pad_px: int = 10, fallback_text_score: float | None = 0.3, **kwargs):
        from rapidocr_onnxruntime import RapidOCR  # deferred — keeps top-level import fast
        self._ocr = RapidOCR(**kwargs)
        self._pad_px = pad_px
        self._kwargs = dict(kwargs)
        if fallback_text_score is not None:
            fallback_kwargs = dict(kwargs)
            fallback_kwargs['text_score'] = fallback_text_score
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
        """Return OCR results from the padded image, with coords shifted back."""
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
        Run RapidOCR on *image* and return normalised token results.

        ``RapidOCR.__call__`` returns ``(results | None, elapsed_time)``.
        This method normalises ``confidence`` to a plain Python ``float``
        so callers never have to handle string or numpy-scalar values (a
        version-dependent quirk of the upstream library).

        When :attr:`pad_px` is non-zero a second pass is run on a padded copy
        of the image and any tokens not already present in the first-pass
        results (judged by Y-centre proximity) are appended.  The merged list
        is sorted by Y-centre so callers receive tokens in top-to-bottom order.
        """
        results = self._run_once(image)

        if not self._pad_px:
            return results

        # Merge tokens from the padded pass that are absent in the original pass.
        merged = list(results)
        existing_ys = [_y_center(bbox) for bbox, _, _ in merged]

        for bbox, text, conf in self._padded_results(image):
            yc = _y_center(bbox)
            if all(abs(yc - ey) > _DEDUP_Y_THRESHOLD for ey in existing_ys):
                merged.append((bbox, text, conf))
                existing_ys.append(yc)
                logger.debug(
                    'RapidOCR dual-pass: added edge token %r (y=%.1f) missing from first pass',
                    text, yc,
                )

        merged.sort(key=lambda r: _y_center(r[0]))

        # Merge tokens from the fallback (lower text_score) pass on the
        # original image.  Recovers interior tokens that fell below the
        # primary confidence threshold.
        if self._fallback_ocr is not None:
            for bbox, text, conf in self._run_once(image, ocr=self._fallback_ocr):
                yc = _y_center(bbox)
                if all(abs(yc - ey) > _DEDUP_Y_THRESHOLD for ey in existing_ys):
                    merged.append((bbox, text, conf))
                    existing_ys.append(yc)
                    logger.debug(
                        'RapidOCR fallback-pass: added low-conf token %r '
                        '(y=%.1f, conf=%.3f) missing from primary passes',
                        text, yc, conf,
                    )
            merged.sort(key=lambda r: _y_center(r[0]))

        return merged

    def __repr__(self) -> str:
        parts = [f'pad_px={self._pad_px!r}']
        if self._fallback_text_score is not None:
            parts.append(f'fallback_text_score={self._fallback_text_score!r}')
        parts += [f'{k}={v!r}' for k, v in self._kwargs.items()]
        return f"RapidOcrBackend({', '.join(parts)})"
