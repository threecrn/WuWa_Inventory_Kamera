"""
scraping.ocr._types
~~~~~~~~~~~~~~~~~~~

Shared type definitions for the OCR abstraction layer.

Kept in a dedicated module so that both ``scraping.ocr.__init__`` (which
owns the registry) and the backend adapters (which need ``OcrResult``) can
import from here without creating circular dependencies.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np

# A single OCR token returned by a backend.
#   bbox        — four [x, y] corner points, e.g. [[x0,y0], [x1,y1], [x2,y2], [x3,y3]]
#   text        — the recognised string for this token
#   confidence  — plain Python float in [0.0, 1.0]
OcrResult = tuple[list, str, float]


@runtime_checkable
class OcrBackend(Protocol):
    """
    Minimal interface that every OCR backend must satisfy.

    To add a custom backend, implement a class with a ``recognize`` method
    matching the signature below — no base class or decorator is required::

        class MyBackend:
            def recognize(self, image: np.ndarray) -> list[OcrResult]:
                ...          # return list of (bbox, text, float_conf) tuples

        from scraping.ocr import register, set_default
        register('mybackend', MyBackend)
        set_default('mybackend')

    The ``@runtime_checkable`` decorator means you can use
    ``isinstance(obj, OcrBackend)`` to verify conformance at runtime.
    """

    def recognize(self, image: np.ndarray) -> list[OcrResult]:
        """
        Run OCR on *image* and return per-token results.

        Parameters
        ----------
        image:
            An RGB uint8 numpy array (H × W × 3).

        Returns
        -------
        list[OcrResult]
            A (possibly empty) list of ``(bbox, text, confidence)`` tuples.
            ``confidence`` **must** be a plain Python ``float``.
        """
        ...
