"""
wuwa_inventory_kamera.scraping.service.assemblers.weapon_assembler
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Parses OCR tokens for one weapon or item grid cell into a structured dict.

The weapon panel shows:
  * A name region — the weapon or item name.
  * A level region — ``"Lv. X/Y"`` for weapons or a plain integer count for
    items.
  * A rank region — a single digit for weapon refinement rank; absent for
    items.

Assembly is simple (two integer parses + one lookup) and never requires
retry.
"""
from __future__ import annotations

import logging
import re

from ...ocr._types import OcrResult
from ...ocr import tokens_to_string
from ..captures import WeaponCapture, WeaponResult
from ._equipped import parse_equipped_character

logger = logging.getLogger(__name__)

# Regex for weapon level text: "Lv. 40/90" → groups (40, 90)
_LEVEL_RE = re.compile(r'(\d+)\s*/\s*(\d+)')
# Rank is a single digit 1–5 on the refinement badge
_RANK_RE  = re.compile(r'\d')
_DIGIT_TOKEN_RE = re.compile(r'\d+')


def _token_x_bounds(token: OcrResult) -> tuple[float, float]:
    bbox = token[0]
    if not bbox:
        return 0.0, 0.0

    xs = [float(point[0]) for point in bbox if point]
    if not xs:
        return 0.0, 0.0

    return min(xs), max(xs)


def _has_strong_horizontal_overlap(
    lhs: tuple[str, float, float, float],
    rhs: tuple[str, float, float, float],
) -> bool:
    overlap = min(lhs[2], rhs[2]) - max(lhs[1], rhs[1])
    if overlap <= 0:
        return False

    lhs_width = max(lhs[2] - lhs[1], 1.0)
    rhs_width = max(rhs[2] - rhs[1], 1.0)
    return (overlap / min(lhs_width, rhs_width)) >= 0.35


def _parse_item_quantity(value_tokens: list[OcrResult]) -> int:
    digit_tokens: list[tuple[str, float, float, float]] = []
    for token in value_tokens:
        digits = ''.join(_DIGIT_TOKEN_RE.findall(token[1]))
        if not digits:
            continue
        x0, x1 = _token_x_bounds(token)
        digit_tokens.append((digits, x0, x1, float(token[2])))

    if not digit_tokens:
        return 0

    digit_tokens.sort(key=lambda item: (item[1], item[2], -len(item[0]), -item[3]))

    candidates: list[tuple[str, float, float, float, float]] = []
    for start_index, first in enumerate(digit_tokens):
        digits = first[0]
        x0 = first[1]
        x1 = first[2]
        confidence = first[3]
        candidates.append((digits, x0, x1, x1 - x0, confidence))

        previous = first
        for token in digit_tokens[start_index + 1:]:
            if _has_strong_horizontal_overlap(previous, token):
                break

            digits += token[0]
            x1 = max(x1, token[2])
            confidence += token[3]
            previous = token
            candidates.append((digits, x0, x1, x1 - x0, confidence))

    best_digits = max(
        candidates,
        key=lambda item: (item[2], len(item[0]), item[3], item[4]),
    )[0]
    return int(best_digits)


def _extract_lookup_id(value):
    if isinstance(value, dict) and 'id' in value:
        return value.get('id')
    return value


def _get_data():
    from ...data import getWeaponsID, getItemsID

    return getWeaponsID(), getItemsID()


class WeaponAssembler:
    """
    Assembles one :class:`~...captures.WeaponCapture` into a
    :class:`~...captures.WeaponResult`.

    Parameters
    ----------
    min_rarity:
        Cells below this rarity are rejected (``data=None``).
        Since rarity isn't OCR-able from the name region alone, this is
        checked by the scanner before creating the capture.
    min_level:
        Weapons below this level are rejected.
    """

    def __init__(self, min_rarity: int = 1, min_level: int = 0) -> None:
        self._min_rarity = min_rarity
        self._min_level  = min_level

    def assemble(
        self,
        capture: WeaponCapture,
        name_tokens:  list[OcrResult],
        value_tokens: list[OcrResult],
        rank_tokens:  list[OcrResult] | None,
        equipped_tokens: list[OcrResult] | None = None,
    ) -> WeaponResult:
        """
        Parameters
        ----------
        capture:
            The originating :class:`WeaponCapture`.
        name_tokens:
            OCR tokens from the name region.
        value_tokens:
            OCR tokens from the level / quantity region.
        rank_tokens:
            OCR tokens from the rank badge, or ``None`` if this is a plain
            item (where there is no rank badge).
        equipped_tokens:
            OCR tokens from the equipped-text region.
        """
        weaponsID, itemsID = _get_data()
        idx = capture.index

        # Strip all whitespace so "Tyro Sword" → "tyrosword", matching the
        # space-free lowercase keys in weaponsID / itemsID regardless of
        # whether the OCR engine returns one token per word or one per phrase.
        raw_name   = tokens_to_string(name_tokens,  divisor=' ').lower().strip()
        name_text  = re.sub(r'\s+', '', raw_name)
        value_text = tokens_to_string(value_tokens, divisor=' ').strip()

        # ── Determine weapon vs item ──────────────────────────────────────
        is_weapon = name_text in weaponsID or rank_tokens is not None
        lookup_id = _extract_lookup_id((weaponsID if is_weapon else itemsID).get(name_text))

        if lookup_id is None:
            # Fuzzy fallback
            from difflib import get_close_matches
            candidates = weaponsID if is_weapon else itemsID
            close = get_close_matches(name_text, candidates, n=1, cutoff=0.8)
            if close:
                logger.info('Weapon %d — fuzzy-resolved %r → %r', idx, name_text, close[0])
                name_text = close[0]
                lookup_id = _extract_lookup_id(candidates[close[0]])
            else:
                logger.warning('Weapon %d — name %r not recognised, rejecting.', idx, name_text)
                return WeaponResult(index=idx, is_weapon=is_weapon, data=None)

        # ── Level / quantity ──────────────────────────────────────────────
        if is_weapon:
            m = _LEVEL_RE.search(value_text)
            level   = int(m.group(1)) if m else 0
            max_lv  = int(m.group(2)) if m else 0

            if level < self._min_level:
                logger.debug('Weapon %d — level %d < min %d, rejecting.', idx, level, self._min_level)
                return WeaponResult(index=idx, is_weapon=True, data=None, below_minimum=True)
        else:
            level   = 0
            max_lv  = 0

        # ── Quantity (items) / Rank (weapons) ─────────────────────────────
        if is_weapon:
            rank_text = tokens_to_string(rank_tokens, divisor='').strip() if rank_tokens else '1'
            m_rank = _RANK_RE.search(rank_text)
            rank = int(m_rank.group()) if m_rank else 1
            data: dict = {
                'id': lookup_id,
                'weapon_key': name_text,
                'level': level,
                'maxLevel': max_lv,
                'rank': rank,
            }
            equipped_character = parse_equipped_character(equipped_tokens)
            if equipped_character is not None:
                data['_equipped'] = equipped_character
        else:
            quantity = _parse_item_quantity(value_tokens)
            data = {'id': lookup_id, 'item_key': name_text, 'count': quantity}

        return WeaponResult(index=idx, is_weapon=is_weapon, data=data)
