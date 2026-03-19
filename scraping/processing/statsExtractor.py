"""
scraping.processing.statsExtractor
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Pluggable stat-extraction strategy used by
:func:`~scraping.processing.echoesProcessor._extractStats`.

Classes
-------
StatsExtractor
    Abstract base class.  Handles cache look-ups and shared parsing logic;
    subclasses only need to implement :meth:`_ocr_names` and
    :meth:`_ocr_values`.

RapidOcrStatsExtractor
    Concrete implementation backed by ``rapidocr_onnxruntime.RapidOCR``
    (colour crops, no B/W pre-processing needed).

TesserOcrStatsExtractor
    Concrete implementation backed by ``tesserocr.PyTessBaseAPI``
    (converts crops to B/W before OCR, which Tesseract prefers).
"""
from __future__ import annotations

import abc
import logging
import string
from collections import defaultdict

import numpy as np

from scraping.data import echoStats
from scraping.utils import convertToBlackWhite, imageToString

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared parsing helpers (module-private)
# ---------------------------------------------------------------------------

def _matchStats(text: list[str]) -> list[str]:
    """
    Assemble stat names from OCR token lines.

    Some stat names span two tokens (e.g. ``['crit', 'rate']`` → ``'critrate'``).
    """
    valid = set(echoStats)
    results: list[str] = []
    i = 0
    while i < len(text):
        if i < len(text) - 1:
            combined = text[i] + text[i + 1]
            if combined in valid:
                results.append(combined)
                i += 2
                continue
        if text[i] in valid:
            results.append(text[i])
        i += 1
    return results


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class StatsExtractor(abc.ABC):
    """
    Abstract base class for echo stat extraction.

    Concrete subclasses supply backend-specific OCR via :meth:`_ocr_names`
    and :meth:`_ocr_values`.  All cache management and value parsing live
    here so the subclasses stay minimal.
    """

    @abc.abstractmethod
    def _ocr_names(self, name_crop: np.ndarray) -> list[str]:
        """
        Run OCR on *name_crop* and return one lowercased token per line.

        Parameters
        ----------
        name_crop:
            Cropped image of the stat-names column, in the colour space
            most appropriate for this backend.

        Returns
        -------
        list[str]
            One entry per recognised line; each entry is already lowercased.
        """

    @abc.abstractmethod
    def _ocr_values(self, value_crop: np.ndarray) -> list[str]:
        """
        Run OCR on *value_crop* and return all recognised value tokens.

        Parameters
        ----------
        value_crop:
            Cropped image of the stat-values column.

        Returns
        -------
        list[str]
            Flat list of value strings (digits, ``'.'``, ``'%'``).
        """

    def execute(
        self,
        name_crop: np.ndarray,
        value_crop: np.ndarray,
        _cache: dict,
        scan_index: int = 0,
    ) -> tuple[int, dict, dict]:
        """
        Parse echo stats from pre-cropped name and value images.

        Parameters
        ----------
        name_crop:
            Cropped image of the stat-names column (already extracted from
            the full screenshot by the caller).
        value_crop:
            Cropped image of the stat-values column.
        _cache:
            Shared OCR result cache keyed by image hash.  Pass a fresh
            ``{}`` per scan when running concurrently to avoid lock contention.
        scan_index:
            Echo scan index used in log messages.

        Returns
        -------
        tuple[int, dict, dict]
            ``(tune_level, stats_dict, ocr_trace)`` where *stats_dict* has
            ``'main'`` and ``'sub'`` keys, and *ocr_trace* carries the raw
            OCR token lists for debug dumps.
        """
        stats: dict = defaultdict(dict)

        name_hash = hash(name_crop.tobytes())
        value_hash = hash(value_crop.tobytes())

        if name_hash in _cache:
            raw_names, names = _cache[name_hash]
        else:
            raw_names = self._ocr_names(name_crop)
            names = _matchStats(raw_names)
            _cache[name_hash] = (raw_names, names)
        logger.debug("Scan %d — stats names: %s", scan_index, names)

        if value_hash in _cache:
            values: list[str] = _cache[value_hash]
        else:
            values = self._ocr_values(value_crop)
            _cache[value_hash] = values
        logger.debug("Scan %d — stats values: %s", scan_index, values)

        tune_lv = max(0, len(values) - 2)

        for idx, (stat_name, stat_value) in enumerate(zip(names, values)):
            stat_name = echoStats.get(stat_name, stat_name)
            bucket = 'main' if idx < 2 else 'sub'
            try:
                if stat_value.endswith('%'):
                    stats[bucket][f"{stat_name}%"] = float(stat_value[:-1])
                else:
                    stats[bucket][stat_name] = int(stat_value)
            except Exception:
                stats[bucket][stat_name] = stat_value

        trace = {
            'raw_names_ocr': raw_names,
            'matched_names': names,
            'raw_values_ocr': values,
        }
        return tune_lv, dict(stats), trace


# ---------------------------------------------------------------------------
# Concrete implementations
# ---------------------------------------------------------------------------

class RapidOcrStatsExtractor(StatsExtractor):
    """
    Stats extractor backed by ``rapidocr_onnxruntime.RapidOCR``.

    Passes colour crops directly to the backend — RapidOCR handles its own
    internal pre-processing, so no B/W conversion is applied here.

    Parameters
    ----------
    **kwargs:
        Forwarded verbatim to :class:`~scraping.ocr._rapidocr.RapidOcrBackend`,
        giving access to the full upstream parameterisation (e.g.
        ``text_score``, ``use_angle_cls``, custom model paths).
    """

    def __init__(self, **kwargs):
        from scraping.ocr._rapidocr import RapidOcrBackend
        self._backend = RapidOcrBackend(**kwargs)

    def _ocr_names(self, name_crop: np.ndarray) -> list[str]:
        return (
            imageToString(name_crop, allowedChars=string.ascii_letters, backend=self._backend)
            .lower()
            .split('\n')
        )

    def _ocr_values(self, value_crop: np.ndarray) -> list[str]:
        return imageToString(
            value_crop, allowedChars=string.digits + '.%', backend=self._backend
        ).split()


class TesserOcrStatsExtractor(StatsExtractor):
    """
    Stats extractor backed by Tesseract OCR via ``tesserocr``.

    Converts crops to greyscale B/W before recognition — Tesseract achieves
    higher accuracy on high-contrast monochrome images than on colour crops.

    Parameters
    ----------
    **kwargs:
        Forwarded verbatim to
        :class:`~scraping.ocr._tesserocr.TesserOcrBackend` (e.g. ``lang``,
        ``psm``, ``tessdata_path``, ``char_whitelist``).
    """

    def __init__(self, **kwargs):
        from scraping.ocr._tesserocr import TesserOcrBackend
        self._backend = TesserOcrBackend(**kwargs)

    def _ocr_names(self, name_crop: np.ndarray) -> list[str]:
        bw = convertToBlackWhite(name_crop)
        return (
            imageToString(bw, allowedChars=string.ascii_letters, backend=self._backend)
            .lower()
            .split('\n')
        )

    def _ocr_values(self, value_crop: np.ndarray) -> list[str]:
        bw = convertToBlackWhite(value_crop)
        return imageToString(
            bw, allowedChars=string.digits + '.%', backend=self._backend
        ).split()
