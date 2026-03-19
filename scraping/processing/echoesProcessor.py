"""
scraping.processing.echoesProcessor
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Phase 2 of the two-phase echo scraper: **OCR, parsing, and structured data
extraction**.

This module is intentionally kept free of all game interaction.  It has no
mouse/keyboard dependencies, takes no screenshots, and does not import
``WindowsInputController``.  All input arrives as :class:`~scraping.models.rawScan.RawEchoScan`
objects — either freshly produced by the scanner in the same run, or loaded from
disk by :func:`~scraping.utils.loadRawScans` for a completely offline re-process.

Public API
----------
echoProcessor(scans, session_id, raw_base=None) -> list[dict]
    Process a list of raw scans into structured echo data.

reprocessSession(session_id) -> list[dict]
    Load a previously saved session from disk and re-run Phase 2 without the game.

Internal helpers (module-private, prefixed with ``_``)
------------------------------------------------------
_matchStats           — token-level stat name assembler
_setupRarityDetection — builds colour-tolerance bounds dict
_RARITY_BOUNDS        — module-level constant produced by the above
_getRarity            — colour-based rarity detection from an echo card image
_extractStats         — OCR + parse stat names/values from the full screenshot
_extractSonata        — OCR the pre-captured sonata image to a known set name
_buildEcho            — fuzzy name match + final dict assembly
_writeDebugCrops      — write intermediate crops to debug/ when DEBUG logging is on
_processRawScan       — orchestrate helpers for one RawEchoScan
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from difflib import get_close_matches as getMatches
from pathlib import Path

import cv2
import numpy as np

from game.screenInfo import ScreenInfo
from properties.app_config import app_config
from scraping.models.rawScan import RawEchoScan
from scraping.processing.echoesValidator import infer_cost, validate_echo_stats
from scraping.processing.statsExtractor import (
    RapidOcrStatsExtractor,
    StatsExtractor,
    TesserOcrStatsExtractor,
)
from scraping.data import echoesID, echoStats, sonataName
from scraping.utils import (
    convertToBlackWhite,
    imageToString,
    loadRawScans,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Rarity detection
# ---------------------------------------------------------------------------

def _setupRarityDetection() -> dict[int, tuple[np.ndarray, np.ndarray]]:
    rarityColors: dict[int, np.ndarray] = {
        5: np.array([90, 230, 255]),
        4: np.array([255, 109, 202]),
        3: np.array([211, 180, 89]),
        2: np.array([94, 195, 92]),
        1: np.array([225, 236, 239]),
    }
    tolerance = 10
    return {r: (c - tolerance, c + tolerance) for r, c in rarityColors.items()}


_RARITY_BOUNDS = _setupRarityDetection()

# Monster ID prefix → slot cost  (first two digits of the numeric ID).
# e.g. 310000020 (prefix "31") → cost 1,  320000020 → cost 3,  340000020 → cost 4.
_MONSTER_COST_MAP: dict[str, int] = {'31': 1, '32': 3, '34': 4}


def _getRarity(image: np.ndarray) -> int:
    """Detect rarity from the colour of an echo card crop."""
    for rarity, (lower, upper) in _RARITY_BOUNDS.items():
        if np.any(cv2.inRange(image, lower, upper)):
            return rarity
    return 1


# ---------------------------------------------------------------------------
# Individual extraction helpers
# ---------------------------------------------------------------------------

def _extractStats(
    full_image: np.ndarray,
    screenInfo: ScreenInfo,
    _cache: dict,
    scan_index: int = 0,
    extractor: StatsExtractor | None = None,
) -> tuple[int, dict, dict]:
    """
    Crop the stats panel from *full_image* and delegate OCR + parsing to
    *extractor*.

    Parameters
    ----------
    full_image:
        The full-screen capture stored in ``RawEchoScan.full_screenshot``.
    screenInfo:
        Layout coordinates reconstructed from the scan's resolution.
    _cache:
        Shared OCR result cache keyed by image hash.  Avoids re-OCR-ing
        identical crops that appear across multiple echoes in the same session.
    scan_index:
        Echo scan index included in log messages.
    extractor:
        :class:`~scraping.processing.statsExtractor.StatsExtractor` instance
        to use.  When ``None`` a :class:`~scraping.processing.statsExtractor
        .RapidOcrStatsExtractor` is created with default settings.

    Returns
    -------
    tuple[int, dict, dict]
        ``(tune_level, stats_dict, ocr_trace)`` where ``stats_dict`` has
        ``'main'`` and ``'sub'`` keys, and ``ocr_trace`` carries the raw OCR
        token lists for debug dumps.
    """
    if extractor is None:
        extractor = RapidOcrStatsExtractor()

    name_crop = full_image[
        screenInfo.echoes.fullStatsName.y : screenInfo.echoes.fullStatsName.y + screenInfo.echoes.fullStatsName.h,
        screenInfo.echoes.fullStatsName.x : screenInfo.echoes.fullStatsName.x + screenInfo.echoes.fullStatsName.w,
    ]
    value_crop = full_image[
        screenInfo.echoes.fullStatsValue.y : screenInfo.echoes.fullStatsValue.y + screenInfo.echoes.fullStatsValue.h,
        screenInfo.echoes.fullStatsValue.x : screenInfo.echoes.fullStatsValue.x + screenInfo.echoes.fullStatsValue.w,
    ]

    return extractor.execute(name_crop, value_crop, _cache, scan_index)


def _extractSonata(
    sonata_image: np.ndarray,
    _cache: dict,
    scan_index: int = 0,
) -> tuple[str, str]:
    """
    Identify the sonata set name from the pre-captured sonata region image.

    Unlike the original ``getSonata``, this function performs **no game
    interaction** — the image was captured and scrolled during Phase 1.

    Parameters
    ----------
    sonata_image:
        The cropped sonata region stored in ``RawEchoScan.sonata_screenshot``.
    _cache:
        Shared OCR result cache keyed by image hash.
    scan_index:
        Echo scan index included in log messages.

    Returns
    -------
    tuple[str, str]
        ``(matched_name, raw_ocr_text)`` — the matched set name (or raw OCR text
        when no known name matched) and the unfiltered OCR output for the debug
        trace.
    """
    sonata_hash = hash(sonata_image.tobytes())
    if sonata_hash in _cache:
        return _cache[sonata_hash]

    raw_text = imageToString(sonata_image, '', bannedChars=' ').lower()
    for name in sonataName:
        if name in raw_text:
            result: tuple[str, str] = (name, raw_text)
            _cache[sonata_hash] = result
            logger.debug("Scan %d — sonata matched: %r → %r", scan_index, raw_text, name)
            return result

    logger.debug("Scan %d — sonata unmatched — raw OCR: %r", scan_index, raw_text)
    result = (raw_text, raw_text)
    _cache[sonata_hash] = result
    return result


def _buildEcho(
    name: str,
    level: int,
    tune_lv: int,
    sonata: str,
    rarity: int,
    stats: dict,
) -> dict:
    """
    Fuzzy-match *name* to a known echo ID and assemble the output dict.

    Parameters
    ----------
    name:
        Echo name, already resolved to an ``echoesID`` key by the caller.
        The internal fuzzy match is kept as a safety net for direct callers.
    level, tune_lv, sonata, rarity, stats:
        Parsed echo attributes.

    Returns
    -------
    dict
        ``{ echo_id: { 'level': ..., 'tuneLv': ..., 'sonata': ...,
                       'rarity': ..., 'stats': ... } }``
    """
    matches = getMatches(name, echoesID, 1, 0.9)
    logger.debug("Name fuzzy match: %r → %s", name, matches)
    if matches:
        name = matches[0]
    echo_id = str(echoesID.get(name, name))
    return {
        echo_id: {
            'level': level,
            'tuneLv': tune_lv,
            'sonata': sonata,
            'rarity': rarity,
            'stats': stats,
        }
    }


# ---------------------------------------------------------------------------
# Debug image dumping
# ---------------------------------------------------------------------------

# BGR colours used for ROI annotation boxes and labels.
_ROI_ANNOTATIONS: list[tuple] = [
    # (attr_name_on_screenInfo.echoes,  BGR colour,       label)
    ('echoCard',       (0,  200,   0), 'card'),
    ('fullStatsName',  (255, 150,  0), 'stats_name'),
    ('fullStatsValue', (0,   50, 255), 'stats_value'),
]


def _writeDebugCrops(
    scan: RawEchoScan,
    screenInfo: ScreenInfo,
    echo_card: np.ndarray,
    debug_dir: Path,
    ocr_data: dict | None = None,
) -> None:
    """
    Write intermediate crop images to *debug_dir* for visual inspection.

    Called only when ``logging.DEBUG`` is active.  Creates *debug_dir* if it
    does not yet exist.

    Files written
    -------------
    full_annotated.png   — full screenshot with all ROI bounding boxes drawn
    card.png             — the echo card crop (name/level/rarity area, colour)
    stats_name.png       — the stat names panel crop (colour)
    stats_name_bw.png    — the stat names panel crop (B&W, as seen by OCR)
    stats_value.png      — the stat values panel crop (colour)
    stats_value_bw.png   — the stat values panel crop (B&W, as seen by OCR)
    sonata.png           — the pre-captured sonata region (colour)
    """
    debug_dir.mkdir(parents=True, exist_ok=True)

    si = screenInfo.echoes

    name_crop = scan.full_screenshot[
        si.fullStatsName.y : si.fullStatsName.y + si.fullStatsName.h,
        si.fullStatsName.x : si.fullStatsName.x + si.fullStatsName.w,
    ]
    value_crop = scan.full_screenshot[
        si.fullStatsValue.y : si.fullStatsValue.y + si.fullStatsValue.h,
        si.fullStatsValue.x : si.fullStatsValue.x + si.fullStatsValue.w,
    ]

    # -- Annotated full screenshot (ROI bounding boxes) ----------------------
    annotated = cv2.cvtColor(scan.full_screenshot, cv2.COLOR_RGB2BGR)
    for attr, colour, label in _ROI_ANNOTATIONS:
        roi = getattr(si, attr)
        x1, y1 = int(roi.x), int(roi.y)
        x2, y2 = int(roi.x + roi.w), int(roi.y + roi.h)
        cv2.rectangle(annotated, (x1, y1), (x2, y2), colour, 2)
        cv2.putText(
            annotated, label,
            (x1, max(0, y1 - 6)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, 1, cv2.LINE_AA,
        )
    cv2.imwrite(str(debug_dir / "full_annotated.png"), annotated)

    # -- Individual crops (colour + B&W) -------------------------------------
    # Screenshots are RGB in-memory; cv2.imwrite expects BGR.
    cv2.imwrite(str(debug_dir / "card.png"),            cv2.cvtColor(echo_card,              cv2.COLOR_RGB2BGR))
    cv2.imwrite(str(debug_dir / "stats_name.png"),      cv2.cvtColor(name_crop,              cv2.COLOR_RGB2BGR))
    cv2.imwrite(str(debug_dir / "stats_name_bw.png"),   convertToBlackWhite(name_crop))
    cv2.imwrite(str(debug_dir / "stats_value.png"),     cv2.cvtColor(value_crop,             cv2.COLOR_RGB2BGR))
    cv2.imwrite(str(debug_dir / "stats_value_bw.png"),  convertToBlackWhite(value_crop))
    cv2.imwrite(str(debug_dir / "sonata.png"),          cv2.cvtColor(scan.sonata_screenshot, cv2.COLOR_RGB2BGR))

    if ocr_data is not None:
        with open(debug_dir / "ocr.json", 'w', encoding='utf-8') as fh:
            json.dump(ocr_data, fh, indent=2, ensure_ascii=False)

    logger.debug("Debug crops written to %s", debug_dir)


# ---------------------------------------------------------------------------
# Per-scan processor
# ---------------------------------------------------------------------------

def _processRawScan(
    scan: RawEchoScan,
    screenInfo: ScreenInfo,
    _cache: dict,
    raw_base: Path,
    write_debug: bool = False,
    extractor: StatsExtractor | None = None,
) -> dict | None:
    """
    Process one :class:`~scraping.models.rawScan.RawEchoScan`: OCR, filter,
    and return the parsed echo dict (or ``None`` if rejected/errored).

    Mirrors the logic of the original ``processGridEcho`` but:

    * Accepts a ``RawEchoScan`` instead of a raw ``np.ndarray`` + controller.
    * Uses ``scan.sonata_screenshot`` directly — no game scrolling needed.
    * Never signals early-stop (the processor always iterates all scans).
    * Writes debug crops on any rejection path when DEBUG logging is active.

    Parameters
    ----------
    scan:
        The raw capture to process.
    screenInfo:
        Layout coordinates reconstructed from the scan's resolution.
    _cache:
        OCR result cache.  In single-threaded mode pass a shared dict for
        cross-echo deduplication; in multi-threaded mode pass a fresh ``{}``
        per invocation to avoid any locking overhead.
    raw_base:
        Root of the session's ``raw/`` folder, used for debug-crop paths in log
        messages and for writing ``debug/`` sub-folders.

    Returns
    -------
    dict | None
        The parsed echo dict on success, or ``None`` when the scan is
        rejected (below rarity/level threshold) or an error occurs.
    """
    logger.debug("Scan %d — processing started", scan.index)
    # Load images from disk just-in-time — only this one echo is in RAM.
    try:
        scan.load_images()
    except FileNotFoundError as e:
        logger.error("Scan %d — images missing on disk, skipping: %s", scan.index, e)
        return None

    try:
        image = scan.full_screenshot

        echo_card = image[
            screenInfo.echoes.echoCard.y : screenInfo.echoes.echoCard.y + screenInfo.echoes.echoCard.h,
            screenInfo.echoes.echoCard.x : screenInfo.echoes.echoCard.x + screenInfo.echoes.echoCard.w,
        ]
        echo_hash = hash(echo_card.tobytes())

        # --- Card OCR (name / level text) ---
        if echo_hash in _cache:
            info: list = _cache[echo_hash]
        else:
            info = [imageToString(echo_card, '', bannedChars=' +').lower().split('\n')]
            _cache[echo_hash] = info

        name_raw: str = info[0][0] if info[0] else ''
        name: str = name_raw
        logger.debug("Scan %d — raw card name: %r", scan.index, name)

        # Strip 'phantom:' prefix — phantom echoes are cosmetic variants of a
        # base echo and share its ID.
        is_phantom = name.startswith('phantom:')
        if is_phantom:
            name = name[len('phantom:'):]
            logger.debug("Scan %d — phantom prefix stripped: %r → %r", scan.index, name_raw, name)

        debug_dir = raw_base / f"echo_{scan.index:04d}" / "debug"
        ocr_trace: dict = {
            'scan_index': scan.index,
            'card': {
                'raw_lines': info[0],
                'name_raw': name_raw,
                'is_phantom': is_phantom,
            },
        }

        # --- Name lookup (exact first, fuzzy fallback for OCR artefacts) ---
        # The fuzzy fallback catches things like inserted punctuation, extra
        # letters, or character substitutions that OCR commonly introduces
        # (e.g. "nightmare:mourning.jaix" → "nightmare:mourningaix").
        if name not in echoesID:
            close = getMatches(name, echoesID, n=1, cutoff=0.75)
            if close:
                logger.info(
                    "Scan %d — fuzzy-resolved OCR artefact %r → %r",
                    scan.index, name, close[0],
                )
                name = close[0]
            else:
                logger.warning(
                    "Scan %d — name not in echoesID: %r | image: %s",
                    scan.index,
                    name_raw,
                    raw_base / f"echo_{scan.index:04d}" / "full.png",
                )
                if write_debug:
                    ocr_trace['card']['name_resolved'] = name
                    ocr_trace['decision'] = 'rejected: name not in echoesID'
                    _writeDebugCrops(scan, screenInfo, echo_card, debug_dir, ocr_trace)
                return None

        ocr_trace['card']['name_resolved'] = name

        # --- Rarity ---
        try:
            rarity: int = info[1][0]
        except (IndexError, TypeError):
            rarity = _getRarity(echo_card)
            _cache[echo_hash].append(rarity)

        ocr_trace['card']['rarity'] = rarity

        if rarity < app_config.echoMinRarity:
            logger.debug(
                "Scan %d — rarity %d below minimum %d, skipping.",
                scan.index, rarity, app_config.echoMinRarity,
            )
            return None

        # --- Level ---
        try:
            level_text: str = info[0][2]
        except IndexError:
            logger.error(
                "Scan %d — IndexError reading level from card OCR: %s | image: %s",
                scan.index,
                info,
                raw_base / f"echo_{scan.index:04d}" / "full.png",
            )
            level_text = ''

        try:
            level = min(25, int(level_text))
        except ValueError:
            level = 0

        ocr_trace['card']['level_text'] = level_text
        ocr_trace['card']['level'] = level

        if level < app_config.echoMinLevel:
            logger.debug(
                "Scan %d — level %d below minimum %d, skipping.",
                scan.index, level, app_config.echoMinLevel,
            )
            return None

        # --- Stats + sonata ---
        tune_lv, stats, stats_trace = _extractStats(image, screenInfo, _cache, scan.index, extractor=extractor)
        sonata, sonata_raw = _extractSonata(scan.sonata_screenshot, _cache, scan.index)

        if sonata not in sonataName:
            logger.warning(
                "Scan %d — sonata name %r not in known set names (raw OCR: %r)",
                scan.index, sonata, sonata_raw,
            )

        echo = _buildEcho(name, level, tune_lv, sonata, rarity, stats)

        # --- Scan metadata ---
        echo_data = next(iter(echo.values()))
        echo_data['_scanIndex'] = scan.index
        monster_id = echoesID.get(name)
        cost_from_id: int | None = None
        if monster_id is not None:
            echo_data['_monsterId'] = monster_id
            cost_from_id = _MONSTER_COST_MAP.get(str(monster_id)[:2])
            if cost_from_id is not None:
                echo_data['_cost'] = cost_from_id

        ocr_trace['stats'] = stats_trace
        ocr_trace['sonata'] = {'raw_ocr': sonata_raw, 'matched': sonata}

        # --- Validation ---
        cost = cost_from_id if cost_from_id is not None else infer_cost(stats)
        if cost is not None:
            vresult = validate_echo_stats(cost, level, rarity, stats)
            for msg in vresult.warnings:
                logger.warning("Scan %d — validation warning: %s", scan.index, msg)
            if not vresult.valid:
                logger.warning(
                    "Scan %d — rejected by validator (%d error(s)): %s | image: %s",
                    scan.index, len(vresult.errors), vresult.errors,
                    raw_base / f"echo_{scan.index:04d}" / "full.png",
                )
                if write_debug:
                    ocr_trace['validation'] = {'errors': vresult.errors, 'warnings': vresult.warnings}
                    ocr_trace['decision'] = 'rejected: validation errors'
                    _writeDebugCrops(scan, screenInfo, echo_card, debug_dir, ocr_trace)
                return None
        else:
            logger.debug("Scan %d — slot cost could not be inferred; validation skipped.", scan.index)

        logger.debug("Scan %d — accepted: %s", scan.index, echo)
        ocr_trace['decision'] = 'accepted'

        # Write result.json immediately so individual echo results are
        # available without waiting for the full session to complete.
        debug_dir.mkdir(parents=True, exist_ok=True)
        with open(debug_dir / "result.json", 'w', encoding='utf-8') as fh:
            json.dump(echo, fh, indent=2, ensure_ascii=False)

        #if logger.isEnabledFor(logging.DEBUG):
        if write_debug:
            _writeDebugCrops(scan, screenInfo, echo_card, debug_dir, ocr_trace)

        return echo
    finally:
        scan.release_images()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def echoProcessor(
    scans: list[RawEchoScan],
    session_id: str,
    raw_base: Path | None = None,
    workers: int = 1,
    write_debug: bool = False,
    extractor: StatsExtractor | None = None,
) -> list[dict]:
    """
    Phase 2 — process raw echo captures into structured echo data.

    No game access is required.  All OCR and fuzzy-matching happen here.

    Parameters
    ----------
    scans:
        :class:`~scraping.models.rawScan.RawEchoScan` objects produced by
        :func:`~scraping.scanning.echoesScanner.echoScanner` or loaded from
        disk with :func:`~scraping.utils.loadRawScans`.
    session_id:
        Scan session identifier.  Used to derive *raw_base* when not supplied.
    raw_base:
        Root of the session's ``raw/`` folder.  When ``None``, derived as
        ``app_config.exportFolder / session_id / "raw"``.  Must point to the same
        directory that was used during Phase 1 so that debug-crop log messages
        contain valid paths.
    workers:
        Number of parallel worker threads for OCR processing.  ``1`` (default)
        runs sequentially with a shared OCR-result cache for cross-echo
        deduplication.  Values ``> 1`` use a
        :class:`~concurrent.futures.ThreadPoolExecutor` where each task gets
        its own fresh cache — better throughput on multi-core machines at the
        cost of losing cross-echo cache hits (which are rare in practice).
        ONNX Runtime's inference engine is thread-safe for concurrent calls.
    extractor:
        :class:`~scraping.processing.statsExtractor.StatsExtractor` instance
        to use for OCR and stat parsing.  When ``None`` (default) a
        :class:`~scraping.processing.statsExtractor.RapidOcrStatsExtractor`
        with default settings is used.  Pass a custom instance to swap in a
        different OCR backend or processing pipeline without touching the
        scanner code.

    Returns
    -------
    list[dict]
        Parsed echo dicts in the same order as the input *scans* list.
    """
    if raw_base is None:
        raw_base = Path(app_config.exportFolder) / session_id / "raw"

    echoes: list[dict] = []

    if not scans:
        logger.warning(
            "echoProcessor called with an empty scan list (session=%s).", session_id
        )
        return echoes

    # All scans in one session share the same resolution; reconstruct once.
    screenInfo = ScreenInfo(scans[0].screen_width, scans[0].screen_height, scans[0].monitor)
    logger.info(
        "Echo processor started — session=%s  scans=%d  resolution=%dx%d  workers=%d",
        session_id, len(scans), scans[0].screen_width, scans[0].screen_height, workers,
    )

    if workers > 1:
        # Each worker thread gets its own empty cache so there is no shared
        # mutable state to protect with a lock.  executor.map preserves
        # submission order, so echoes remain in their original scan order.
        def _worker(scan: RawEchoScan) -> dict | None:
            try:
                return _processRawScan(scan, screenInfo, {}, raw_base, write_debug=write_debug, extractor=extractor)
            except Exception:
                logger.exception("Unhandled error processing scan %d", scan.index)
                return None

        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(_worker, scans))
        echoes = [r for r in results if r is not None]
    else:
        # Sequential path: share one cache across all scans so identical crops
        # (same echo name/level) are only OCR-processed once.
        _cache: dict = {}
        for scan in scans:
            result = _processRawScan(scan, screenInfo, _cache, raw_base, write_debug=write_debug, extractor=extractor)
            if result is not None:
                echoes.append(result)

    logger.info(
        "Echo processor finished — session=%s  accepted=%d / %d",
        session_id, len(echoes), len(scans),
    )
    return echoes


def reprocessSession(session_id: str, workers: int = 1) -> list[dict]:
    """
    Re-run Phase 2 on a previously saved session — **no game required**.

    Loads all :class:`~scraping.models.rawScan.RawEchoScan` objects from
    ``export/{session_id}/raw/`` (written by Phase 1) and passes them to
    :func:`echoProcessor`.

    Parameters
    ----------
    session_id:
        The session folder name under ``app_config.exportFolder``, e.g.
        ``"2026-02-28_14-30-00"``.
    workers:
        Number of parallel OCR worker threads.  Forwarded to
        :func:`echoProcessor`.  Defaults to ``1`` (sequential).

    Returns
    -------
    list[dict]
        Parsed echo dicts in the same format produced by the original
        ``echoScraper``.
    """
    raw_base = Path(app_config.exportFolder) / session_id / "raw"
    scans = loadRawScans(raw_base)
    logger.info(
        "reprocessSession — session=%s  loaded %d raw scan(s)", session_id, len(scans)
    )
    return echoProcessor(scans, session_id, raw_base, workers=workers, write_debug=True)
