"""
wuwa_inventory_kamera.scraping.service.assemblers.character_assembler
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Accumulates partial OCR results across the five character panel sections
and merges them into a complete character dict.

Section mapping
---------------
0 — resonator overview  (name, level, ascension)
1 — weapon panel        (weapon name, level, rank)
2 — echoes panel        (skipped; handled by the echo scraper)
3 — skills panel        (active skill levels + passive unlock buttons)
4 — resonance chain     (Activated / not activated per chain node)

The scanner submits one :class:`~...captures.CharCapture` per section.
The assembler is **stateful**: it accumulates partial results for each
character index and only resolves the future when section 4 arrives.
Before that each :meth:`assemble` call returns a ``CharResult`` with the
partial fields so the scanner can read navigation-relevant data (e.g.
the resonator name from section 0 for duplicate detection).
"""
from __future__ import annotations

import logging
import re
from difflib import get_close_matches

from ...ocr._types import OcrResult
from ...ocr import tokens_to_string, tokens_to_lines
from ..captures import CharCapture, CharResult

logger = logging.getLogger(__name__)

_LEVEL_RE = re.compile(r'(\d+)')
_LEVEL_PAIR_RE = re.compile(r'(\d+)\s*/\s*(\d+)')
_ASCENSION_LEVELS = (20, 40, 50, 60, 70, 80, 90)


def _get_data():
    from ...data import charactersID, weaponsID, definedText
    return charactersID, weaponsID, definedText


class CharAssembler:
    """
    Stateful assembler that merges fields across all five character sections.

    One :class:`CharAssembler` instance should be created per scanner
    session.  It keeps a dict of partial results keyed by character index
    so sections can arrive independently.

    Call :meth:`assemble` for each :class:`CharCapture`.  The returned
    :class:`CharResult` always contains ``fields`` for the sections seen
    so far; section 4 also triggers an ``already_seen`` check.
    """

    def __init__(self) -> None:
        # char_index → accumulated field dict
        self._partial: dict[int, dict] = {}
        # char_index → set of seen character names (for loop detection)
        self._seen_names: set[str] = set()

    def assemble(self, capture: CharCapture, *section_token_lists) -> CharResult:
        """
        Process one section of a character panel.

        Parameters
        ----------
        capture:
            The originating :class:`CharCapture`.
        *section_token_lists:
            Pre-computed OCR token lists for each crop in ``capture.crops``,
            passed in the same order as the keys in ``capture.crops``.
            The caller (``OcrService``) zips these based on crop order.

        Returns
        -------
        CharResult
            Partial or complete result for this character.
        """
        # Rebuild the (field_name → token_list) mapping from the positional args
        crop_keys   = list(capture.crops.keys())
        token_map   = dict(zip(crop_keys, section_token_lists))
        idx         = capture.char_index
        section     = capture.section

        partial = self._partial.setdefault(idx, {})
        fields: dict = {}

        if section == 0:
            fields.update(self._parse_overview(idx, token_map))
            name = fields.get('name', '')
            if name in self._seen_names:
                fields['already_seen'] = True
            else:
                self._seen_names.add(name)
                fields['already_seen'] = False

        elif section == 1:
            fields.update(self._parse_weapon(idx, token_map))

        elif section == 3:
            fields.update(self._parse_skills(token_map))

        elif section == 4:
            fields.update(self._parse_chain(token_map))

        partial.update(fields)
        return CharResult(char_index=idx, section=section, fields=dict(partial))

    # ------------------------------------------------------------------
    # Section parsers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_overview(idx: int, token_map: dict[str, list[OcrResult]]) -> dict:
        """Section 0: resonator name and level."""
        result = {}
        if 'name' in token_map:
            name_text = tokens_to_string(token_map['name'], divisor='').lower().strip()
            chars, _, _ = _get_data()
            close = get_close_matches(name_text, chars, n=1, cutoff=0.75)
            result['name'] = close[0] if close else name_text
            result['char_id'] = chars.get(result['name'])
            if close:
                logger.debug(
                    'Character %d — resonator name matched: %r -> %r (id=%r)',
                    idx,
                    name_text,
                    result['name'],
                    result['char_id'],
                )
            else:
                logger.debug(
                    'Character %d — resonator name unmatched: %r (id=%r)',
                    idx,
                    name_text,
                    result['char_id'],
                )

        if 'level' in token_map:
            level_text = tokens_to_string(token_map['level'], divisor='')
            pair_match = _LEVEL_PAIR_RE.search(level_text)
            if pair_match:
                result['level'] = int(pair_match.group(1))
                try:
                    result['ascension'] = _ASCENSION_LEVELS.index(int(pair_match.group(2)))
                except ValueError:
                    result['ascension'] = 0
            else:
                m = _LEVEL_RE.search(level_text)
                result['level'] = int(m.group(1)) if m else 0
                result['ascension'] = 0

        return result

    @staticmethod
    def _parse_weapon(idx: int, token_map: dict[str, list[OcrResult]]) -> dict:
        """Section 1: equipped weapon name, level, and refinement rank."""
        result = {}
        _, weaponsID, _ = _get_data()

        if 'weaponName' in token_map:
            name_text = tokens_to_string(token_map['weaponName'], divisor='').lower().strip()
            close = get_close_matches(name_text, weaponsID, n=1, cutoff=0.8)
            result['weaponName'] = close[0] if close else name_text
            result['weaponId'] = weaponsID.get(result['weaponName'])
            if close:
                logger.debug(
                    'Character %d — weapon name matched: %r -> %r (id=%r)',
                    idx,
                    name_text,
                    result['weaponName'],
                    result['weaponId'],
                )
            else:
                logger.debug(
                    'Character %d — weapon name unmatched: %r (id=%r)',
                    idx,
                    name_text,
                    result['weaponId'],
                )

        if 'weaponLevel' in token_map:
            level_text = tokens_to_string(token_map['weaponLevel'], divisor=' ')
            m = re.search(r'(\d+)\s*/\s*(\d+)', level_text)
            result['weaponLevel']    = int(m.group(1)) if m else 0
            result['weaponMaxLevel'] = int(m.group(2)) if m else 0

        if 'weaponRank' in token_map:
            rank_text = tokens_to_string(token_map['weaponRank'], divisor='')
            m = re.search(r'\d', rank_text)
            result['weaponRank'] = int(m.group()) if m else 1

        return result

    @staticmethod
    def _parse_skills(token_map: dict[str, list[OcrResult]]) -> dict:
        """Section 3: skill levels and passive unlock counts."""
        result: dict = {'skills': {}}
        activated_text: str | None = None
        for key, tokens in token_map.items():
            if key.startswith('skill_'):
                text = tokens_to_string(tokens, divisor='')
                m = _LEVEL_RE.search(text)
                result['skills'][key] = int(m.group(1)) if m else 0
                continue

            if not key.startswith('passive_'):
                continue

            if activated_text is None:
                _, _, definedText = _get_data()
                activated_text = definedText.get('PrefabTextItem_3963945691_Text', 'activated').lower()

            parts = key.split('_', 2)
            if len(parts) != 3:
                continue

            passive_skill_key = parts[1]
            text = tokens_to_string(tokens, divisor='').lower()
            if activated_text in text:
                result['skills'][passive_skill_key] = result['skills'].get(passive_skill_key, 0) + 1
        return result

    @staticmethod
    def _parse_chain(token_map: dict[str, list[OcrResult]]) -> dict:
        """Section 4: resonance chain node activation status."""
        _, _, definedText = _get_data()
        activated_text = definedText.get('PrefabTextItem_3963945691_Text', 'activated').lower()
        result: dict = {'chain': {}}
        for key, tokens in token_map.items():
            if key.startswith('chain_'):
                text = tokens_to_string(tokens, divisor='').lower()
                result['chain'][key] = activated_text in text
        return result
