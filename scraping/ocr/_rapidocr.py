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
    **kwargs:
        Forwarded verbatim to ``RapidOCR(**kwargs)``.
    """

    def __init__(self, **kwargs):
        from rapidocr_onnxruntime import RapidOCR  # deferred — keeps top-level import fast
        self._ocr = RapidOCR(**kwargs)
        self._kwargs = dict(kwargs)

    def recognize(self, image: np.ndarray) -> list[OcrResult]:
        """
        Run RapidOCR on *image* and return normalised token results.

        ``RapidOCR.__call__`` returns ``(results | None, elapsed_time)``.
        This method normalises ``confidence`` to a plain Python ``float``
        so callers never have to handle string or numpy-scalar values (a
        version-dependent quirk of the upstream library).
        """
        raw_results, _elapsed = self._ocr(image)
        if not raw_results:
            return []
        return [(bbox, text, float(conf)) for bbox, text, conf in raw_results]

    def __repr__(self) -> str:
        kw = ', '.join(f'{k}={v!r}' for k, v in self._kwargs.items())
        return f"RapidOcrBackend({kw})"
