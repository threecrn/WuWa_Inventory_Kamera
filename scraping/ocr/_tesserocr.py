"""
scraping.ocr._tesserocr
~~~~~~~~~~~~~~~~~~~~~~~

Adapter that wraps ``tesserocr.PyTessBaseAPI`` to satisfy the
:class:`~scraping.ocr.OcrBackend` protocol.

Installation
------------
tesserocr requires both the Python package and the Tesseract system library.

Linux / macOS::

    sudo apt-get install tesseract-ocr libtesseract-dev libleptonica-dev
    pip install tesserocr

Windows (Conda — recommended)::

    conda install -c conda-forge tesserocr

Windows (pip — unofficial pre-built wheels)::

    # Download the wheel for your Python version from:
    # https://github.com/simonflueckiger/tesserocr-windows_build/releases
    pip install <wheel>.whl

Language data
-------------
Tesseract needs ``*.traineddata`` files for each recognised language.  They can
be downloaded from https://github.com/tesseract-ocr/tessdata and are typically
installed alongside the system package.

Point to the tessdata directory via the ``TESSDATA_PREFIX`` environment
variable, or pass ``tessdata_path=`` explicitly to :class:`TesserOcrBackend`.
"""
from __future__ import annotations

import logging
import threading

import numpy as np

from scraping.ocr._types import OcrResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Convenience constants — mirrors tesserocr.PSM / tesserocr.OEM so that
# callers never need to import tesserocr just to name a mode.
# The integer values are stable across all supported Tesseract 4/5 versions.
# ---------------------------------------------------------------------------

# Page segmentation modes (tesserocr.PSM)
PSM_OSD_ONLY              = 0   # Orientation and script detection only
PSM_AUTO_OSD              = 1   # Auto + OSD
PSM_AUTO_ONLY             = 2   # Auto, no OSD, no OCR
PSM_AUTO                  = 3   # Fully automatic (default)
PSM_SINGLE_COLUMN         = 4   # Single column of variable-size text
PSM_SINGLE_BLOCK_VERT_TEXT = 5  # Single uniform block, vertical text
PSM_SINGLE_BLOCK          = 6   # Single uniform block of text
PSM_SINGLE_LINE           = 7   # Single text line
PSM_SINGLE_WORD           = 8   # Single word
PSM_CIRCLE_WORD           = 9   # Single word in a circle
PSM_SINGLE_CHAR           = 10  # Single character
PSM_SPARSE_TEXT           = 11  # Sparse text — find as much as possible
PSM_SPARSE_TEXT_OSD       = 12  # Sparse text + OSD
PSM_RAW_LINE              = 13  # Raw line, no word-finding

# OCR engine modes (tesserocr.OEM)
OEM_TESSERACT_ONLY        = 0   # Legacy Tesseract engine
OEM_LSTM_ONLY             = 1   # Neural-net LSTM engine only
OEM_TESSERACT_LSTM        = 2   # Legacy + LSTM
OEM_DEFAULT               = 3   # Default — based on what is available


class TesserOcrBackend:
    """
    Wraps ``tesserocr.PyTessBaseAPI`` as an :class:`~scraping.ocr.OcrBackend`.

    A thread-local ``PyTessBaseAPI`` instance is created lazily on the first
    ``recognize`` call in each thread and reused for all subsequent calls,
    avoiding per-image API construction overhead.  This makes the backend safe
    to share across the worker threads used by
    :func:`~scraping.processing.echoesProcessor.echoProcessor` — each thread
    keeps its own independent Tesseract state.

    All constructor parameters are JSON-serialisable primitive types so the
    backend can be fully configured from the ``reprocess.py`` CLI::

        python reprocess.py --session-id ... \\
            --ocr-backend tesserocr \\
            --ocr-params '{"psm": 7, "char_whitelist": "0123456789.%"}'

    Parameters
    ----------
    lang:
        Tesseract language(s), e.g. ``'eng'``, ``'eng+fra'``.
        Defaults to ``'eng'``.
    psm:
        Page segmentation mode integer (see ``PSM_*`` module constants or
        ``tesserocr.PSM``).  ``PSM_AUTO`` (``3``) is the default.  For
        game-UI crops that are a single line of text, ``PSM_SINGLE_LINE``
        (``7``) can improve accuracy.
    oem:
        OCR engine mode integer (see ``OEM_*`` module constants or
        ``tesserocr.OEM``).  ``OEM_DEFAULT`` (``3``) uses LSTM when
        available, with the legacy engine as a fallback.
    tessdata_path:
        Explicit path to the directory containing ``*.traineddata`` files.
        When ``None`` (default) tesserocr reads the ``TESSDATA_PREFIX``
        environment variable.
    char_whitelist:
        Shorthand for the ``tessedit_char_whitelist`` Tesseract variable.
        Only characters in this string will appear in OCR output.  ``None``
        (default) disables the whitelist and allows all characters.
    variables:
        Arbitrary Tesseract ``SetVariable`` name → value pairs applied after
        the API is created.  Values are coerced to ``str``.  An entry for
        ``'tessedit_char_whitelist'`` here takes precedence over
        ``char_whitelist``.
    """

    def __init__(
        self,
        lang: str = 'eng',
        psm: int = PSM_AUTO,
        oem: int = OEM_DEFAULT,
        tessdata_path: str | None = None,
        char_whitelist: str | None = None,
        variables: dict | None = None,
    ):
        self._api_kwargs: dict = dict(lang=lang, psm=psm, oem=oem)
        if tessdata_path is not None:
            self._api_kwargs['path'] = tessdata_path

        # Build the variables dict applied after API construction.
        # Explicit *variables* entries override the *char_whitelist* shorthand.
        self._extra_vars: dict[str, str] = {}
        if char_whitelist is not None:
            self._extra_vars['tessedit_char_whitelist'] = char_whitelist
        if variables:
            self._extra_vars.update({k: str(v) for k, v in variables.items()})

        # Thread-local storage: one PyTessBaseAPI per calling thread.
        # The API is created on first use inside each thread (see _get_api).
        self._local = threading.local()
        logger.debug(
            "TesserOcrBackend created: kwargs=%r  variables=%s",
            self._api_kwargs, self._extra_vars,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_api(self):
        """
        Return the thread-local ``PyTessBaseAPI`` instance.

        Creates and configures one if this is the first call on the current
        thread.  ``PyTessBaseAPI`` is imported here (not at module load time)
        so that ``import scraping.ocr`` never pulls in the Tesseract runtime.
        """
        if not hasattr(self._local, 'api'):
            from tesserocr import PyTessBaseAPI  # deferred — avoids eager Tesseract load
            api = PyTessBaseAPI(**self._api_kwargs)
            for var_name, var_val in self._extra_vars.items():
                api.SetVariable(var_name, var_val)
            self._local.api = api
            logger.debug(
                "Created thread-local PyTessBaseAPI (thread=%s)",
                threading.current_thread().name,
            )
        return self._local.api

    # ------------------------------------------------------------------
    # OcrBackend protocol
    # ------------------------------------------------------------------

    def recognize(self, image: np.ndarray) -> list[OcrResult]:
        """
        Run Tesseract OCR on *image* and return normalised word-level results.

        Each recognised word becomes one ``(bbox, text, confidence)`` token:

        * ``bbox`` — four-corner polygon ``[[x0,y0],[x1,y1],[x2,y2],[x3,y3]]``
          in top-left → top-right → bottom-right → bottom-left order,
          compatible with the bbox format produced by RapidOCR.
        * ``text`` — stripped UTF-8 text for the word.
        * ``confidence`` — ``float`` in ``[0.0, 1.0]``; Tesseract returns
          ``0–100`` which is divided by ``100`` here.

        Empty and whitespace-only tokens are silently discarded.
        ``RuntimeError`` exceptions raised by tesserocr on empty image regions
        are caught and skipped gracefully.

        ``api.Clear()`` is called at the end of each invocation to release the
        per-call image data while keeping the thread-local API instance alive
        for the next call.
        """
        from PIL import Image as PILImage  # deferred — pillow only needed here
        from tesserocr import RIL, iterate_level  # deferred

        pil_image = PILImage.fromarray(image)
        api = self._get_api()
        api.SetImage(pil_image)
        api.Recognize()

        ri = api.GetIterator()
        results: list[OcrResult] = []

        if ri is not None:
            for r in iterate_level(ri, RIL.WORD):
                try:
                    text = r.GetUTF8Text(RIL.WORD)
                    if not text or not text.strip():
                        continue
                    text = text.strip()
                    # Tesseract confidence is in [0, 100]; normalise to [0.0, 1.0].
                    conf = r.Confidence(RIL.WORD) / 100.0
                    left, top, right, bottom = r.BoundingBox(RIL.WORD)
                    bbox = [
                        [left,  top   ],
                        [right, top   ],
                        [right, bottom],
                        [left,  bottom],
                    ]
                    results.append((bbox, text, conf))
                except RuntimeError:
                    # tesserocr raises RuntimeError on empty/invalid regions.
                    continue

        # Release the image data held by the API; the instance stays alive.
        api.Clear()
        return results

    def __repr__(self) -> str:
        kw = ', '.join(f'{k}={v!r}' for k, v in self._api_kwargs.items())
        if self._extra_vars:
            kw += f', variables={self._extra_vars!r}'
        return f"TesserOcrBackend({kw})"
