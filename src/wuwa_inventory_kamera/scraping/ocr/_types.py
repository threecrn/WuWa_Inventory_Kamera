"""
wuwa_inventory_kamera.scraping.ocr._types
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Shared type definitions for the OCR abstraction layer.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np

# A single OCR token returned by a backend.
#   bbox        — four [x, y] corner points
#   text        — the recognised string for this token
#   confidence  — plain Python float in [0.0, 1.0]
OcrResult = tuple[list, str, float]


@runtime_checkable
class OcrBackend(Protocol):
    """
    Minimal interface that every OCR backend must satisfy.

    Implement a class with a ``recognize`` method matching the signature
    below — no base class or decorator is required.
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
