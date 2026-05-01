"""
wuwa_inventory_kamera.scraping.service.assemblers.echo_assembler
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Parses pre-computed OCR token lists for one echo card into a structured
echo dict.

The assembler receives lists of
:data:`~wuwa_inventory_kamera.scraping.ocr._types.OcrResult` tokens
(already produced by
:class:`~wuwa_inventory_kamera.scraping.ocr.batch.BatchOcr`) and applies
all fuzzy-matching, stat alignment, and validation logic that was
previously embedded in ``echoesProcessor._processRawScan``.

Sonata detection uses icon template matching
(:class:`~...matching.sonata_icon.SonataIconMatcher`) against the small
circular icon on the echo card, instead of OCR text matching.

The expensive data imports (``echoesID``, ``echoStats``, ``sonataName``,
validators) are still pulled from the existing ``scraping`` package; this
module contains only the assembly logic.

Public API
----------
EchoAssembler
    Call :meth:`~EchoAssembler.assemble` with the card, stats_name, and
    stats_value token lists for a single echo to get an
    :class:`~...captures.EchoResult`.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from difflib import get_close_matches

import numpy as np

from ...ocr._types import OcrResult
from ...ocr import tokens_to_lines
from ...matching.sonata_icon import SonataIconMatcher
from ..captures import EchoCapture, EchoResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lazy data imports (from the existing scraping package)
# ---------------------------------------------------------------------------

def _get_data():
    from ...data import echoesID, echoStats, sonataName
    return echoesID, echoStats, sonataName


def _get_validators():
    from ...processing.echoesValidator import (
        infer_cost,
        expected_sub_count,
        validate_echo_stats,
    )
    return infer_cost, expected_sub_count, validate_echo_stats


# Monster ID prefix → slot cost  (first two digits of the numeric ID)
_MONSTER_COST_MAP: dict[str, int] = {'31': 1, '32': 3, '34': 4}


# ---------------------------------------------------------------------------
# Stat-name matching
# ---------------------------------------------------------------------------

_NONALPHA_RE = re.compile(r'[^a-z]')


def _norm_stat(text: str) -> str:
    """Lowercase and strip non-alpha chars so OCR output matches echoStats keys."""
    return _NONALPHA_RE.sub('', text.lower())


def _match_stats(text_lines: list[str], valid_stats: set[str]) -> list[str]:
    """
    Resolve OCR text lines to known stat name keys.

    Normalises each line (lowercase, strip non-alpha) before lookup.
    Also tries combining adjacent lines for names that wrap across two
    display rows (e.g. ``'Resonance Skill DMG'`` + ``'Bonus'``).
    """
    results: list[str] = []
    i = 0
    while i < len(text_lines):
        t0 = _norm_stat(text_lines[i])
        if i < len(text_lines) - 1:
            t1 = _norm_stat(text_lines[i + 1])
            combined = t0 + t1
            if combined in valid_stats:
                results.append(combined)
                i += 2
                continue
        if t0 in valid_stats:
            results.append(t0)
        i += 1
    return results


# ---------------------------------------------------------------------------
# Value parsing
# ---------------------------------------------------------------------------

def _parse_stat_value(raw: str) -> int | float | str:
    """
    Convert a raw OCR value string to a number.

    ``'5.00%'`` → ``5.0`` (float), ``'1234'`` → ``1234`` (int).
    Falls back to the raw string on parse error.
    """
    raw = raw.strip()
    try:
        if raw.endswith('%'):
            return float(raw[:-1])
        return int(raw)
    except ValueError:
        return raw


# ---------------------------------------------------------------------------
# Assembler
# ---------------------------------------------------------------------------

class EchoAssembler:
    """
    Converts pre-computed OCR token lists for one echo into an
    :class:`~wuwa_inventory_kamera.scraping.service.captures.EchoResult`.

    Parameters
    ----------
    min_rarity:
        Echoes below this rarity are rejected (``data=None``).
    min_level:
        Echoes below this level are rejected (``data=None``).
    """

    def __init__(self, min_rarity: int = 1, min_level: int = 0) -> None:
        self._min_rarity = min_rarity
        self._min_level  = min_level
        self._sonata_matcher = SonataIconMatcher()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def assemble(
        self,
        capture: EchoCapture,
        card_tokens:    list[OcrResult],
        name_tokens:    list[OcrResult],
        value_tokens:   list[OcrResult],
    ) -> EchoResult:
        """
        Assemble *capture* from pre-computed OCR tokens.

        Parameters
        ----------
        capture:
            The originating :class:`EchoCapture`.  Used for the echo index,
            the sonata icon crop (for icon matching), and (if
            ``full_screenshot`` is set) for debug purposes.
        card_tokens:
            OCR tokens from the echo card region (name + level + rarity).
        name_tokens:
            OCR tokens from the stat-name column.
        value_tokens:
            OCR tokens from the stat-value column.

        Returns
        -------
        EchoResult
            ``data=None`` when the echo is rejected; otherwise a dict
            keyed by echo ID.
        """
        echoesID, echoStats, sonataName = _get_data()
        infer_cost, expected_sub_count, validate_echo_stats = _get_validators()

        idx = capture.echo_index
        warnings: list[str] = []

        # ── Card parsing ──────────────────────────────────────────────────
        card_lines = tokens_to_lines(card_tokens, divisor='', bannedChars=' +')
        card_lines = [line.lower() for line in card_lines]

        name_raw = card_lines[0] if card_lines else ''
        name = name_raw

        # Strip 'phantom:' prefix
        is_phantom = name.startswith('phantom:')
        if is_phantom:
            name = name[len('phantom:'):]
            logger.debug('Echo %d — phantom prefix stripped: %r → %r', idx, name_raw, name)

        # Level: prefer the value already parsed during capture (new UI path);
        # fall back to the third card-OCR line for legacy resolutions.
        if capture.detected_level is not None:
            level = capture.detected_level
        else:
            level_text = card_lines[2] if len(card_lines) > 2 else ''
            try:
                level = min(25, int(level_text))
            except ValueError:
                level = 0

        logger.debug('Echo %d — name lines: %s | name=%r level=%d', idx, card_lines, name, level)

        # Name lookup: exact → fuzzy
        if name not in echoesID:
            close = get_close_matches(name, echoesID, n=1, cutoff=0.75)
            if close:
                logger.info('Echo %d — fuzzy-resolved %r → %r', idx, name, close[0])
                name = close[0]
            else:
                logger.warning('Echo %d — name %r not in echoesID, rejecting.', idx, name_raw)
                return EchoResult(echo_index=idx, data=None, warnings=warnings, retried=False, detected_level=level)

        # Rarity from the colour-pick pixel sampled during capture
        rarity = capture.detected_rarity if capture.detected_rarity is not None else 1
        if capture.detected_rarity is None:
            logger.warning('Echo %d — detected_rarity not set; defaulting to 1.', idx)

        if rarity < self._min_rarity:
            logger.debug('Echo %d — rarity %d < min %d, rejecting.', idx, rarity, self._min_rarity)
            return EchoResult(echo_index=idx, data=None, warnings=warnings, retried=False, detected_level=level)

        if level < self._min_level:
            logger.debug('Echo %d — level %d < min %d, rejecting.', idx, level, self._min_level)
            return EchoResult(echo_index=idx, data=None, warnings=warnings, retried=False, detected_level=level)

        # ── Sonata detection (icon matching) ──────────────────────────────
        if capture.sonata_icon is not None:
            sonata = self._sonata_matcher.match_to_sonata_key(
                capture.sonata_icon, sonataName,
                cx=capture.sonata_icon_cx,
                cy=capture.sonata_icon_cy,
                r=capture.sonata_icon_r,
            )
            if sonata is None:
                logger.warning('Echo %d — sonata icon not matched, rejecting.', idx)
                return EchoResult(echo_index=idx, data=None, warnings=warnings, retried=False, detected_level=level)
        else:
            logger.warning('Echo %d — no sonata icon crop available, rejecting.', idx)
            return EchoResult(echo_index=idx, data=None, warnings=warnings, retried=False, detected_level=level)

        logger.debug('Echo %d — sonata: %r', idx, sonata)

        # ── Stats parsing ─────────────────────────────────────────────────
        tune_lv, stats = self._parse_stats(
            name_tokens, value_tokens, echoStats, idx
        )

        # ── Assemble echo dict ────────────────────────────────────────────
        echo = self._build_echo(name, level, tune_lv, sonata, rarity, stats, echoesID, echoStats)
        echo_data = next(iter(echo.values()))
        echo_data['_scanIndex'] = idx

        monster_id = echoesID.get(name)
        cost_from_id: int | None = None
        if monster_id is not None:
            echo_data['_monsterId'] = monster_id
            cost_from_id = _MONSTER_COST_MAP.get(str(monster_id)[:2])
            if cost_from_id is not None:
                echo_data['_cost'] = cost_from_id

        # ── Validation ────────────────────────────────────────────────────
        cost = cost_from_id if cost_from_id is not None else infer_cost(stats)
        if cost is not None:
            vresult = validate_echo_stats(cost, level, rarity, stats)
            for msg in vresult.warnings:
                warnings.append(msg)
                logger.warning('Echo %d — validation warning: %s', idx, msg)

            if not vresult.valid:
                logger.warning(
                    'Echo %d — rejected by validator (%d error(s)): %s',
                    idx, len(vresult.errors), vresult.errors,
                )
                return EchoResult(echo_index=idx, data=None, warnings=warnings, retried=False, detected_level=level)

            # Check for missing substats (may indicate an OCR miss)
            sub_count      = len(stats.get('sub', {}))
            expected_subs  = expected_sub_count(level)
            if sub_count < expected_subs:
                logger.info(
                    'Echo %d — only %d/%d substats parsed; '
                    'retry with thorough OCR is needed.',
                    idx, sub_count, expected_subs,
                )
                warnings.append(
                    f'Missing substats: {sub_count}/{expected_subs} parsed '
                    '(thorough retry recommended)'
                )

        logger.debug('Echo %d — accepted: %s', idx, echo)
        return EchoResult(echo_index=idx, data=echo, warnings=warnings, retried=False, detected_level=level)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_stats(
        name_tokens:  list[OcrResult],
        value_tokens: list[OcrResult],
        echoStats: dict,
        scan_index: int,
    ) -> tuple[int, dict]:
        """
        Align stat names from *name_tokens* with values from *value_tokens*
        by line order and return ``(tune_level, stats_dict)``.

        Both token lists are already in top-to-bottom row order (produced by
        :class:`~wuwa_inventory_kamera.scraping.ocr.batch.BatchOcr`).

        Returns
        -------
        tuple[int, dict]
            ``(tune_level, {'main': {...}, 'sub': {...}})``
        """
        valid_stats = set(echoStats)

        # Use a tight row gap for the densely-packed stats panel: adjacent
        # stat rows are only ~5 px apart, so the default 10 px gap causes
        # tokens from different rows to merge into one line.
        name_lines  = tokens_to_lines(name_tokens,  divisor=' ', row_gap=3)
        value_lines = tokens_to_lines(value_tokens, divisor=' ', row_gap=3)

        matched_names  = _match_stats(name_lines,  valid_stats)
        matched_values = value_lines[:len(matched_names)]  # align by line order

        logger.debug(
            'Echo %d — stat names: %s | values: %s',
            scan_index, matched_names, matched_values,
        )

        tune_lv = max(0, len(matched_values) - 2)
        stats: dict = defaultdict(dict)

        for i, (stat_name, raw_value) in enumerate(zip(matched_names, matched_values)):
            display_name = echoStats.get(stat_name, stat_name)
            bucket = 'main' if i < 2 else 'sub'
            value  = _parse_stat_value(raw_value)
            if isinstance(value, float) or (isinstance(value, str) and raw_value.endswith('%')):
                stats[bucket][f'{display_name}%'] = value
            else:
                stats[bucket][display_name] = value

        return tune_lv, dict(stats)

    @staticmethod
    def _build_echo(
        name: str,
        level: int,
        tune_lv: int,
        sonata: str,
        rarity: int,
        stats: dict,
        echoesID: dict,
        echoStats: dict,
    ) -> dict:
        """Fuzzy-match *name* to a known echo ID and assemble the output dict."""
        matches = get_close_matches(name, echoesID, 1, 0.9)
        if matches:
            name = matches[0]
        echo_id = str(echoesID.get(name, name))
        return {
            echo_id: {
                'level':   level,
                'tuneLv':  tune_lv,
                'sonata':  sonata,
                'rarity':  rarity,
                'stats':   stats,
            }
        }
