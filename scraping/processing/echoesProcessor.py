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

import logging
import string
from collections import defaultdict
from difflib import get_close_matches as getMatches
from pathlib import Path

import cv2
import numpy as np

from game.screenInfo import ScreenInfo
from properties.config import cfg
from scraping.models.rawScan import RawEchoScan
from scraping.utils import (
    convertToBlackWhite,
    echoesID,
    echoStats,
    imageToString,
    loadRawScans,
    sonataName,
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


def _getRarity(image: np.ndarray) -> int:
    """Detect rarity from the colour of an echo card crop."""
    for rarity, (lower, upper) in _RARITY_BOUNDS.items():
        if np.any(cv2.inRange(image, lower, upper)):
            return rarity
    return 1


# ---------------------------------------------------------------------------
# Stat name matching
# ---------------------------------------------------------------------------

def _matchStats(text: list[str]) -> list[str]:
    """
    Assemble stat names from OCR token lines.

    Some stat names span two tokens (e.g. ``['crit', 'rate']`` → ``'critrate'``).
    This mirrors the original ``matchStats`` logic exactly.
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
# Individual extraction helpers
# ---------------------------------------------------------------------------

def _extractStats(
    full_image: np.ndarray,
    screenInfo: ScreenInfo,
    _cache: dict,
) -> tuple[int, dict]:
    """
    OCR the stats panel from *full_image* and parse names, values, and tune level.

    Parameters
    ----------
    full_image:
        The full-screen capture stored in ``RawEchoScan.full_screenshot``.
    screenInfo:
        Layout coordinates reconstructed from the scan's resolution.
    _cache:
        Shared OCR result cache keyed by image hash.  Avoids re-OCR-ing identical
        crops that appear across multiple echoes in the same session.

    Returns
    -------
    tuple[int, dict]
        ``(tune_level, stats_dict)`` where ``stats_dict`` has ``'main'`` and
        ``'sub'`` keys, each mapping stat name → value.
    """
    stats: dict = defaultdict(dict)

    name_crop = full_image[
        screenInfo.echoes.fullStatsName.y : screenInfo.echoes.fullStatsName.y + screenInfo.echoes.fullStatsName.h,
        screenInfo.echoes.fullStatsName.x : screenInfo.echoes.fullStatsName.x + screenInfo.echoes.fullStatsName.w,
    ]
    name_crop_bw = convertToBlackWhite(name_crop)
    name_hash = hash(name_crop_bw.tobytes())

    value_crop = full_image[
        screenInfo.echoes.fullStatsValue.y : screenInfo.echoes.fullStatsValue.y + screenInfo.echoes.fullStatsValue.h,
        screenInfo.echoes.fullStatsValue.x : screenInfo.echoes.fullStatsValue.x + screenInfo.echoes.fullStatsValue.w,
    ]
    value_crop_bw = convertToBlackWhite(value_crop)
    value_hash = hash(value_crop_bw.tobytes())

    if name_hash in _cache:
        names: list[str] = _cache[name_hash]
    else:
        raw_names = imageToString(name_crop_bw, allowedChars=string.ascii_letters).lower().split('\n')
        names = _matchStats(raw_names)
        _cache[name_hash] = names
    logger.debug("Stats names: %s", names)

    if value_hash in _cache:
        values: list[str] = _cache[value_hash]
    else:
        values = imageToString(value_crop_bw, allowedChars=string.digits + '.%').split()
        _cache[value_hash] = values
    logger.debug("Stats values: %s", values)

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

    return tune_lv, dict(stats)


def _extractSonata(sonata_image: np.ndarray, _cache: dict) -> str:
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

    Returns
    -------
    str
        The matched sonata set name, or the raw (lowercased) OCR text if no
        known name matches.
    """
    sonata_hash = hash(sonata_image.tobytes())
    if sonata_hash in _cache:
        return _cache[sonata_hash]

    raw_text = imageToString(sonata_image, '', bannedChars=' ').lower()
    for name in sonataName:
        if name in raw_text:
            _cache[sonata_hash] = name
            logger.debug("Sonata matched: %r → %r", raw_text, name)
            return name

    logger.debug("Sonata unmatched — raw OCR: %r", raw_text)
    return raw_text


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
        Raw echo name from card OCR (possibly noisy).
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

    logger.debug("Debug crops written to %s", debug_dir)


# ---------------------------------------------------------------------------
# Per-scan processor
# ---------------------------------------------------------------------------

def _processRawScan(
    scan: RawEchoScan,
    screenInfo: ScreenInfo,
    echoes: list[dict],
    _cache: dict,
    raw_base: Path,
) -> None:
    """
    Process one :class:`~scraping.models.rawScan.RawEchoScan`: OCR, filter,
    and optionally append to *echoes*.

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
    echoes:
        Accumulator list; accepted echoes are appended here.
    _cache:
        Shared OCR result cache for the whole session.
    raw_base:
        Root of the session's ``raw/`` folder, used for debug-crop paths in log
        messages and for writing ``debug/`` sub-folders.
    """
    # Load images from disk just-in-time — only this one echo is in RAM.
    try:
        scan.load_images()
    except FileNotFoundError as e:
        logger.error("Scan %d — images missing on disk, skipping: %s", scan.index, e)
        return

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

        name: str = info[0][0] if info[0] else ''
        logger.debug("Scan %d — raw card name: %r", scan.index, name)

        # Normalise known OCR artefacts (mirrors original echoesScraper logic).
        if name.startswith('phantom:'):
            name = name[len('phantom:'):]
        if 'mourning.jaix' in name:
            name = name.replace('mourning.jaix', 'mourningaix')

        debug_dir = raw_base / f"echo_{scan.index:04d}" / "debug"

        # --- Name lookup ---
        if name not in echoesID:
            logger.warning(
                "Scan %d — name not in echoesID: %r | image: %s",
                scan.index,
                name,
                raw_base / f"echo_{scan.index:04d}" / "full.png",
            )
            if logger.isEnabledFor(logging.DEBUG):
                _writeDebugCrops(scan, screenInfo, echo_card, debug_dir)
            return

        # --- Rarity ---
        try:
            rarity: int = info[1][0]
        except (IndexError, TypeError):
            rarity = _getRarity(echo_card)
            _cache[echo_hash].append(rarity)

        if rarity < cfg.get(cfg.echoMinRarity):
            logger.debug(
                "Scan %d — rarity %d below minimum %d, skipping.",
                scan.index, rarity, cfg.get(cfg.echoMinRarity),
            )
            return

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

        if level < cfg.get(cfg.echoMinLevel):
            logger.debug(
                "Scan %d — level %d below minimum %d, skipping.",
                scan.index, level, cfg.get(cfg.echoMinLevel),
            )
            return

        # --- Stats + sonata ---
        tune_lv, stats = _extractStats(image, screenInfo, _cache)
        sonata = _extractSonata(scan.sonata_screenshot, _cache)
        echo = _buildEcho(name, level, tune_lv, sonata, rarity, stats)

        logger.debug("Scan %d — accepted: %s", scan.index, echo)
        if logger.isEnabledFor(logging.DEBUG):
            _writeDebugCrops(scan, screenInfo, echo_card, debug_dir)

        echoes.append(echo)
    finally:
        scan.release_images()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def echoProcessor(
    scans: list[RawEchoScan],
    session_id: str,
    raw_base: Path | None = None,
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
        ``cfg.exportFolder / session_id / "raw"``.  Must point to the same
        directory that was used during Phase 1 so that debug-crop log messages
        contain valid paths.

    Returns
    -------
    list[dict]
        Parsed echo dicts in the same format produced by the original
        ``echoScraper``.
    """
    if raw_base is None:
        raw_base = Path(cfg.get(cfg.exportFolder)) / session_id / "raw"

    echoes: list[dict] = []
    _cache: dict = {}

    if not scans:
        logger.warning(
            "echoProcessor called with an empty scan list (session=%s).", session_id
        )
        return echoes

    # All scans in one session share the same resolution; reconstruct once.
    screenInfo = ScreenInfo(scans[0].screen_width, scans[0].screen_height, scans[0].monitor)
    logger.info(
        "Echo processor started — session=%s  scans=%d  resolution=%dx%d",
        session_id, len(scans), scans[0].screen_width, scans[0].screen_height,
    )

    for scan in scans:
        _processRawScan(scan, screenInfo, echoes, _cache, raw_base)

    logger.info(
        "Echo processor finished — session=%s  accepted=%d / %d",
        session_id, len(echoes), len(scans),
    )
    return echoes


def reprocessSession(session_id: str) -> list[dict]:
    """
    Re-run Phase 2 on a previously saved session — **no game required**.

    Loads all :class:`~scraping.models.rawScan.RawEchoScan` objects from
    ``export/{session_id}/raw/`` (written by Phase 1) and passes them to
    :func:`echoProcessor`.

    Parameters
    ----------
    session_id:
        The session folder name under ``cfg.exportFolder``, e.g.
        ``"2026-02-28_14-30-00"``.

    Returns
    -------
    list[dict]
        Parsed echo dicts in the same format produced by the original
        ``echoScraper``.
    """
    raw_base = Path(cfg.get(cfg.exportFolder)) / session_id / "raw"
    scans = loadRawScans(raw_base)
    logger.info(
        "reprocessSession — session=%s  loaded %d raw scan(s)", session_id, len(scans)
    )
    return echoProcessor(scans, session_id, raw_base)
