from __future__ import annotations

import json
import logging
import re
from difflib import get_close_matches

from ....config.app_config import app_config, basePATH
from ...ocr import tokens_to_string
from ...ocr._types import OcrResult

logger = logging.getLogger(__name__)

_EQUIPPED_RE = re.compile(
    r'^\s*equipped\s*by\b[:\s-]*(.+?)\s*$',
    re.IGNORECASE,
)
_NON_WORD_RE = re.compile(r'[\W_]+', re.UNICODE)

_CHARACTER_NAMES_CACHE_KEY: tuple[str, int, int] | None = None
_CHARACTER_NAMES_CACHE_VALUE: tuple[str, ...] | None = None


def _resolve_game_language_code() -> str:
    selected = str(getattr(app_config, 'gameLanguage', 'English') or 'English')
    if (basePATH / 'data' / selected).is_dir():
        return selected

    languages_path = basePATH / 'data' / 'languages.json'
    try:
        with open(languages_path, 'r', encoding='utf-8') as f:
            mapping = json.load(f)
        if isinstance(mapping, dict):
            mapped = mapping.get(selected)
            if isinstance(mapped, str) and mapped:
                return mapped
            if selected in mapping.values():
                return selected
    except Exception:
        pass

    return 'en'


def _runtime_character_names() -> tuple[str, ...]:
    global _CHARACTER_NAMES_CACHE_KEY
    global _CHARACTER_NAMES_CACHE_VALUE

    language_code = _resolve_game_language_code()
    characters_path = basePATH / 'data' / language_code / 'characters.json'

    try:
        stat = characters_path.stat()
        cache_key = (language_code, stat.st_mtime_ns, stat.st_size)
        if cache_key == _CHARACTER_NAMES_CACHE_KEY and _CHARACTER_NAMES_CACHE_VALUE is not None:
            return _CHARACTER_NAMES_CACHE_VALUE

        with open(characters_path, 'r', encoding='utf-8') as f:
            payload = json.load(f)

        if isinstance(payload, dict):
            names = tuple(
                name for name in payload.keys()
                if isinstance(name, str) and name
            )
            _CHARACTER_NAMES_CACHE_KEY = cache_key
            _CHARACTER_NAMES_CACHE_VALUE = names
            return names
    except Exception as exc:
        logger.debug('Equipped-name runtime character fallback: %s', exc)

    from ...data import charactersID

    return tuple(
        name for name in charactersID.keys()
        if isinstance(name, str) and name
    )


def _normalize_character_name(text: str) -> str:
    return _NON_WORD_RE.sub('', text.casefold())


def _canonicalize_character_name(text: str) -> str | None:
    raw = text.strip()
    if not raw:
        return None

    normalized = _normalize_character_name(raw)
    if not normalized:
        return raw

    canonical_by_normalized: dict[str, str] = {}
    for name in _runtime_character_names():
        normalized_name = _normalize_character_name(name)
        if normalized_name:
            canonical_by_normalized.setdefault(normalized_name, name)

    if not canonical_by_normalized:
        return raw

    exact = canonical_by_normalized.get(normalized)
    if exact is not None:
        return exact

    close = get_close_matches(normalized, canonical_by_normalized.keys(), n=1, cutoff=0.75)
    if close:
        return canonical_by_normalized[close[0]]

    return raw


def parse_equipped_character(tokens: list[OcrResult] | None) -> str | None:
    if not tokens:
        return None

    raw = re.sub(r'\s+', ' ', tokens_to_string(tokens, divisor=' ').strip())
    if not raw:
        return None

    match = _EQUIPPED_RE.match(raw)
    if not match:
        return None

    character = match.group(1).strip(' :-')
    return _canonicalize_character_name(character)