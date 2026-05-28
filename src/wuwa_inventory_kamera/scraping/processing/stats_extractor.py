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

RapidOcrCoordStatsExtractor
    Uses RapidOCR and aligns stat names to values by bounding-box Y
    coordinate.  Additionally handles stat names that wrap across two
    lines by merging consecutive name rows that individually produce no
    valid match.
"""
from __future__ import annotations

import abc
import logging
import re
import string
from collections import defaultdict

import numpy as np

from ..utils.common import convertToBlackWhite, darken_background_preserve_edges_ndarray
from ..ocr import imageToString

logger = logging.getLogger(__name__)


def _get_echo_stats() -> dict:
    from ..data import getEchoStats

    return getEchoStats()


# ---------------------------------------------------------------------------
# Shared parsing helpers (module-private)
# ---------------------------------------------------------------------------

def _matchStats(text: list[str]) -> list[str]:
    """
    Assemble stat names from OCR token strings.

    Some stat names span two adjacent tokens (e.g. ``['crit', 'rate']``
    → ``'critrate'``).

    Spaces are stripped from every token before lookup.  This handles the
    case where :func:`~scraping.ocr.imageToString` re-introduces spaces
    between sub-tokens that were placed on the same visual row (joined by the
    default ``divisor=' '``), even though the individual sub-tokens had their
    spaces removed by the ``allowedChars`` filter.  For example, when
    ``'Resonance Liberation'`` and ``'DMG Bonus'`` land on the same Y-row
    they arrive as the single token ``'resonanceliberation dmgbonus'``; after
    space-stripping this matches ``'resonanceliberationdmgbonus'`` directly.
    """
    valid = set(_get_echo_stats())
    results: list[str] = []
    i = 0
    while i < len(text):
        t0 = text[i].replace(' ', '')
        if i < len(text) - 1:
            t1 = text[i + 1].replace(' ', '')
            combined = t0 + t1
            if combined in valid:
                results.append(combined)
                i += 2
                continue
        if t0 in valid:
            results.append(t0)
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
        """Preprocess *image* for OCR: darken the gradient background, then
        optionally convert to B/W for Tesseract-based backends."""
        img = darken_background_preserve_edges_ndarray(image)
        return convertToBlackWhite(img) if self._use_bw else img

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
        echo_stats = _get_echo_stats()

        for idx, (stat_name, stat_value) in enumerate(zip(names, values)):
            stat_name = echo_stats.get(stat_name, stat_name)
            bucket = 'main' if idx < 2 else 'sub'
            try:
                if stat_value.endswith('%'):
                    stats[bucket][f"{stat_name}%"] = float(stat_value[:-1])
                else:
                    stats[bucket][stat_name] = int(stat_value)
            except Exception:
                stats[bucket][stat_name] = stat_value

        return tune_lv, dict(stats), trace

    def retry_execute(
        self,
        name_crop: np.ndarray,
        value_crop: np.ndarray,
        scan_index: int = 0,
    ) -> tuple[int, dict, dict]:
        """
        Re-run stat extraction using the backend's thorough multi-pass OCR.

        Called when semantic validation of a :meth:`execute` result has
        flagged errors or suspicious values.  Unlike :meth:`execute` the
        result is **never cached** — the whole point is to get a better
        result than the cached fast-pass.

        The default implementation checks whether the extractor's backend
        exposes a ``thorough_recognize`` method (as
        :class:`~scraping.ocr._rapidocr.RapidOcrBackend` does).  If it does,
        ``recognize`` is temporarily replaced with ``thorough_recognize`` for
        the duration of the call.  Subclasses may override this for custom
        retry logic.

        Returns the same ``(tune_lv, stats_dict, ocr_trace)`` tuple as
        :meth:`execute`.
        """
        backend = getattr(self, '_backend', None)
        thorough = getattr(backend, 'thorough_recognize', None)

        if backend is not None and thorough is not None:
            # Temporarily patch recognize → thorough_recognize
            original_recognize = backend.recognize
            backend.recognize = thorough  # type: ignore[method-assign]
            try:
                names, values, trace = self._ocr_and_pair(
                    self._prepare(name_crop),
                    self._prepare(value_crop),
                    scan_index,
                )
            finally:
                backend.recognize = original_recognize  # type: ignore[method-assign]
        else:
            # Backend has no thorough mode — plain re-run (clears the cache effect)
            names, values, trace = self._ocr_and_pair(
                self._prepare(name_crop),
                self._prepare(value_crop),
                scan_index,
            )

        logger.debug("Scan %d — retry stats names: %s", scan_index, names)
        logger.debug("Scan %d — retry stats values: %s", scan_index, values)

        tune_lv = max(0, len(values) - 2)
        stats: dict = defaultdict(dict)
        echo_stats = _get_echo_stats()
        for idx, (stat_name, stat_value) in enumerate(zip(names, values)):
            stat_name = echo_stats.get(stat_name, stat_name)
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
        from ..ocr._rapidocr import RapidOcrBackend
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


class RapidOcrCoordStatsExtractor(StatsExtractor):
    """
    Stats extractor backed by RapidOCR that uses bounding-box Y coordinates
    to align stat names with their values.

    Uses the same coordinate-aware row pairing strategy as the removed
    Tesseract implementation, but on RapidOCR tokens.  Additionally handles
    stat names that wrap across two display
    lines: when a row of name tokens produces no valid match on its own,
    it is merged with the next row and resolved as a single name, with the
    value paired to the *first* row's Y position (where the value glyph
    is anchored).

    Parameters
    ----------
    row_tolerance:
        Maximum pixel distance between two tokens' Y centres to be
        considered part of the same text row.  Defaults to ``10``.
    use_bw:
        Apply B/W pre-processing before OCR.  Defaults to ``False`` —
        RapidOCR performs its own internal pre-processing on colour images.
    **kwargs:
        Forwarded verbatim to
        :class:`~scraping.ocr._rapidocr.RapidOcrBackend`.
    """

    _ALPHA_RE = re.compile(r'[^a-zA-Z]')
    _DIGIT_RE = re.compile(r'[^0-9.%]')

    def __init__(self, row_tolerance: int = 10, use_bw: bool = False, **kwargs):
        super().__init__(use_bw=use_bw)
        from ..ocr._rapidocr import RapidOcrBackend
        self._backend = RapidOcrBackend(**kwargs)
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

        # Resolve stat names row by row.  When a row produces no valid name
        # on its own, attempt to merge it with the following row to handle
        # names that wrap onto a second display line.  The resolved name is
        # anchored to the *first* row's Y so it aligns with the value glyph.
        named_rows: list[tuple[float, str]] = []  # (y_center, resolved_name)
        i = 0
        while i < len(grouped):
            row_y, xtokens = grouped[i]
            tokens = [t for _, t in sorted(xtokens)]

            names = _matchStats(tokens)
            if names:
                for name in names:
                    named_rows.append((row_y, name))
                i += 1
                continue

            # No match — try combining with the next row (wrapped name)
            if i + 1 < len(grouped):
                next_y, next_xtokens = grouped[i + 1]
                combined = tokens + [t for _, t in sorted(next_xtokens)]
                names = _matchStats(combined)
                if names:
                    for name in names:
                        named_rows.append((row_y, name))  # anchor to first row Y
                    i += 2
                    continue

            # Still no match — skip this row
            logger.debug(
                "Scan %d — unmatched name tokens (skipped): %s", scan_index, tokens
            )
            i += 1

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
            best = min(range(len(available)), key=lambda j: abs(available[j][0] - name_y))
            _, value = available.pop(best)
            paired_names.append(name)
            paired_values.append(value)

        trace = {
            'raw_names_ocr': [t for _, _, t in name_items],
            'matched_names': paired_names,
            'raw_values_ocr': [t for _, t in value_items],
        }
        return paired_names, paired_values, trace
