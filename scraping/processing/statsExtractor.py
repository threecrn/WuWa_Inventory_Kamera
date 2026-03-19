"""
scraping.processing.statsExtractor
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Pluggable stat-extraction strategy used by
:func:`~scraping.processing.echoesProcessor._extractStats`.

Classes
-------
StatsExtractor
    Abstract base class.  Manages the cache and final stats-dict assembly;
    subclasses implement :meth:`_ocr_and_pair` to decide how names and
    values are extracted and aligned.

RapidOcrStatsExtractor
    Uses RapidOCR with colour crops; aligns names to values by line order.

TesserOcrStatsExtractor
    Uses Tesseract with B/W pre-processing; aligns by line order.

TesserOcrCoordStatsExtractor
    Uses Tesseract and aligns stat names to values by bounding-box Y
    coordinate rather than line order — more robust when OCR skips or
    merges lines differently in the two columns.
"""
from __future__ import annotations

import abc
import logging
import re
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
    Assemble stat names from OCR token strings.

    Some stat names span two adjacent tokens (e.g. ``['crit', 'rate']``
    → ``'critrate'``).
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


def _bbox_center(bbox) -> tuple[float, float]:
    """Return ``(x_center, y_center)`` of a four-cornered bounding box."""
    xs = [pt[0] for pt in bbox]
    ys = [pt[1] for pt in bbox]
    return sum(xs) / len(xs), sum(ys) / len(ys)


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class StatsExtractor(abc.ABC):
    """
    Abstract base class for echo stat extraction.

    Subclasses implement :meth:`_ocr_and_pair` to run OCR on the name and
    value crops and return matched ``(names, values)`` lists.  All cache
    management and final stats-dict assembly live in :meth:`execute` so
    subclasses stay minimal.
    """

    def __init__(self, use_bw: bool = False) -> None:
        """
        Parameters
        ----------
        use_bw:
            When ``True``, both crops are converted to greyscale B/W via
            :func:`~scraping.utils.convertToBlackWhite` before being passed
            to :meth:`_ocr_and_pair` (and before the result cache key is
            computed).  Defaults to ``False``.
        """
        self._use_bw = use_bw

    def _prepare(self, image: np.ndarray) -> np.ndarray:
        """Return *image* after the optional B/W conversion."""
        return convertToBlackWhite(image) if self._use_bw else image

    @abc.abstractmethod
    def _ocr_and_pair(
        self,
        name_crop: np.ndarray,
        value_crop: np.ndarray,
        scan_index: int,
    ) -> tuple[list[str], list[str], dict]:
        """
        OCR both crops and return aligned ``(names, values, ocr_trace)``.

        Parameters
        ----------
        name_crop:
            Cropped image of the stat-names column.
        value_crop:
            Cropped image of the stat-values column.
        scan_index:
            Echo scan index for log messages.

        Returns
        -------
        tuple[list[str], list[str], dict]
            * **names** — resolved stat name strings (already processed by
              :func:`_matchStats`).
            * **values** — corresponding raw value strings aligned to *names*
              (e.g. ``'42'``, ``'3.2%'``).
            * **ocr_trace** — dict of raw OCR data for debug dumps.  Must
              contain at least ``'raw_names_ocr'``, ``'matched_names'``, and
              ``'raw_values_ocr'`` keys.

        The implementation is responsible for aligning names with values.
        Simple implementations may zip by line order; coordinate-aware ones
        may use bounding-box Y positions to pair rows spatially.
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
            Shared OCR result cache keyed by ``(name_hash, value_hash)``.
            Pass a fresh ``{}`` per scan when running concurrently to avoid
            lock contention.
        scan_index:
            Echo scan index used in log messages.

        Returns
        -------
        tuple[int, dict, dict]
            ``(tune_level, stats_dict, ocr_trace)`` where *stats_dict* has
            ``'main'`` and ``'sub'`` keys, and *ocr_trace* carries the raw
            OCR token lists for debug dumps.
        """
        name_in  = self._prepare(name_crop)
        value_in = self._prepare(value_crop)
        cache_key = (hash(name_in.tobytes()), hash(value_in.tobytes()))
        if cache_key in _cache:
            names, values, trace = _cache[cache_key]
        else:
            names, values, trace = self._ocr_and_pair(name_in, value_in, scan_index)
            _cache[cache_key] = (names, values, trace)

        logger.debug("Scan %d — stats names: %s", scan_index, names)
        logger.debug("Scan %d — stats values: %s", scan_index, values)

        tune_lv = max(0, len(values) - 2)
        stats: dict = defaultdict(dict)

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

        return tune_lv, dict(stats), trace


# ---------------------------------------------------------------------------
# Concrete implementations
# ---------------------------------------------------------------------------

class RapidOcrStatsExtractor(StatsExtractor):
    """
    Stats extractor backed by ``rapidocr_onnxruntime.RapidOCR``.

    Passes colour crops directly to the backend — RapidOCR handles its own
    internal pre-processing, so no B/W conversion is applied here.
    Names and values are aligned by line order (zip).

    Parameters
    ----------
    **kwargs:
        Forwarded verbatim to :class:`~scraping.ocr._rapidocr.RapidOcrBackend`,
        giving access to the full upstream parameterisation (e.g.
        ``text_score``, ``use_angle_cls``, custom model paths).
    """

    def __init__(self, use_bw: bool = False, **kwargs):
        super().__init__(use_bw=use_bw)
        from scraping.ocr._rapidocr import RapidOcrBackend
        self._backend = RapidOcrBackend(**kwargs)

    def _ocr_and_pair(
        self,
        name_crop: np.ndarray,
        value_crop: np.ndarray,
        scan_index: int,
    ) -> tuple[list[str], list[str], dict]:
        raw_names = (
            imageToString(name_crop, allowedChars=string.ascii_letters, backend=self._backend)
            .lower()
            .split('\n')
        )
        names = _matchStats(raw_names)
        values = imageToString(
            value_crop, allowedChars=string.digits + '.%', backend=self._backend
        ).split()
        trace = {'raw_names_ocr': raw_names, 'matched_names': names, 'raw_values_ocr': values}
        return names, values, trace


class TesserOcrStatsExtractor(StatsExtractor):
    """
    Stats extractor backed by Tesseract OCR via ``tesserocr``.

    Converts crops to greyscale B/W before recognition — Tesseract achieves
    higher accuracy on high-contrast monochrome images than on colour crops.
    Names and values are aligned by line order (zip).

    Parameters
    ----------
    **kwargs:
        Forwarded verbatim to
        :class:`~scraping.ocr._tesserocr.TesserOcrBackend` (e.g. ``lang``,
        ``psm``, ``tessdata_path``, ``char_whitelist``).
    """

    def __init__(self, use_bw: bool = True, **kwargs):
        super().__init__(use_bw=use_bw)
        from scraping.ocr._tesserocr import TesserOcrBackend
        self._backend = TesserOcrBackend(**kwargs)

    def _ocr_and_pair(
        self,
        name_crop: np.ndarray,
        value_crop: np.ndarray,
        scan_index: int,
    ) -> tuple[list[str], list[str], dict]:
        raw_names = (
            imageToString(name_crop, allowedChars=string.ascii_letters, backend=self._backend)
            .lower()
            .split('\n')
        )
        names = _matchStats(raw_names)
        values = imageToString(
            value_crop, allowedChars=string.digits + '.%', backend=self._backend
        ).split()
        trace = {'raw_names_ocr': raw_names, 'matched_names': names, 'raw_values_ocr': values}
        return names, values, trace


class TesserOcrCoordStatsExtractor(StatsExtractor):
    """
    Stats extractor backed by Tesseract that uses bounding-box Y coordinates
    to align stat names with their values.

    Instead of relying on line order, each resolved stat name is paired with
    the value token whose vertical centre is nearest to the name row's
    vertical centre.  This is more robust when Tesseract produces a different
    number of output rows for the two columns (e.g. one column merges two
    adjacent stat lines that the other splits).

    Parameters
    ----------
    row_tolerance:
        Maximum pixel distance between two tokens' Y centres to be
        considered part of the same text row.  Defaults to ``10``.
    use_bw:
        Apply B/W pre-processing before OCR.  Defaults to ``True``.
    **kwargs:
        Forwarded verbatim to
        :class:`~scraping.ocr._tesserocr.TesserOcrBackend`.
    """

    _ALPHA_RE = re.compile(r'[^a-zA-Z]')
    _DIGIT_RE = re.compile(r'[^0-9.%]')

    def __init__(self, row_tolerance: int = 10, use_bw: bool = True, **kwargs):
        super().__init__(use_bw=use_bw)
        from scraping.ocr._tesserocr import TesserOcrBackend
        self._backend = TesserOcrBackend(**kwargs)
        self._row_tolerance = row_tolerance

    def _ocr_and_pair(
        self,
        name_crop: np.ndarray,
        value_crop: np.ndarray,
        scan_index: int,
    ) -> tuple[list[str], list[str], dict]:
        raw_name_tokens = self._backend.recognize(name_crop)
        raw_value_tokens = self._backend.recognize(value_crop)

        # --- Name tokens: keep letters, record (y_center, x_center, text) ---
        name_items: list[tuple[float, float, str]] = []
        for bbox, text, _conf in raw_name_tokens:
            cleaned = self._ALPHA_RE.sub('', text).lower()
            if cleaned:
                xc, yc = _bbox_center(bbox)
                name_items.append((yc, xc, cleaned))
        name_items.sort()  # primary: y, secondary: x

        # Group tokens into rows by Y proximity; sort tokens within each
        # row left-to-right so _matchStats sees them in the correct order.
        tol = self._row_tolerance
        grouped: list[tuple[float, list[tuple[float, str]]]] = []
        for yc, xc, text in name_items:
            if grouped and abs(yc - grouped[-1][0]) <= tol:
                grouped[-1][1].append((xc, text))
            else:
                grouped.append((yc, [(xc, text)]))

        # Apply _matchStats per row to resolve multi-token stat names.
        named_rows: list[tuple[float, str]] = []  # (y_center, resolved_name)
        for row_y, xtokens in grouped:
            xtokens.sort()
            for name in _matchStats([t for _, t in xtokens]):
                named_rows.append((row_y, name))

        # --- Value tokens: keep digits/./%, record (y_center, text) ---
        value_items: list[tuple[float, str]] = []
        for bbox, text, _conf in raw_value_tokens:
            cleaned = self._DIGIT_RE.sub('', text)
            if cleaned:
                _, yc = _bbox_center(bbox)
                value_items.append((yc, cleaned))
        value_items.sort()

        # --- Pair each name row with the nearest unused value by Y ---
        available = list(value_items)
        paired_names: list[str] = []
        paired_values: list[str] = []
        for name_y, name in named_rows:
            if not available:
                break
            best = min(range(len(available)), key=lambda i: abs(available[i][0] - name_y))
            _, value = available.pop(best)
            paired_names.append(name)
            paired_values.append(value)

        trace = {
            'raw_names_ocr': [t for _, _, t in name_items],
            'matched_names': paired_names,
            'raw_values_ocr': [t for _, t in value_items],
        }
        return paired_names, paired_values, trace
