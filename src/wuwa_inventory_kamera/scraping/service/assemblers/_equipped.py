from __future__ import annotations

import logging
import re
from difflib import get_close_matches

from .... import localization_data as _localization_data
from ....config.app_config import app_config, basePATH
from ...ocr import tokens_to_string
from ...ocr._types import OcrResult

logger = logging.getLogger(__name__)

_EQUIPPED_RE = re.compile(
    r'^\s*equipped\s*by\b[:\s-]*(.+?)\s*$',
    re.IGNORECASE,
)
_NON_WORD_RE = re.compile(r'[\W_]+', re.UNICODE)

_CHARACTER_NAMES_CACHE_KEY: tuple[str, str, int, int] | None = None
_CHARACTER_NAMES_CACHE_VALUE: dict[str, str] | None = None


def _resolve_game_language_code() -> str:
    return _localization_data.resolve_game_language_code(
        base_path=basePATH,
        selected_language=getattr(app_config, 'gameLanguage', 'English'),
    )


def _runtime_character_names() -> tuple[str, ...]:
    global _CHARACTER_NAMES_CACHE_KEY
    global _CHARACTER_NAMES_CACHE_VALUE

    language_code = _resolve_game_language_code()
    candidate_paths = _localization_data.iter_locale_data_paths(
        'characters.json',
        language_code,
        base_path=basePATH,
    )

    last_exc: Exception | None = None
    for characters_path in candidate_paths:
        try:
            stat = characters_path.stat()
            cache_key = (language_code, str(characters_path), stat.st_mtime_ns, stat.st_size)
            if cache_key == _CHARACTER_NAMES_CACHE_KEY and _CHARACTER_NAMES_CACHE_VALUE is not None:
                return tuple(_CHARACTER_NAMES_CACHE_VALUE.keys())

            payload = _localization_data.load_json_file(characters_path)
            if payload is None:
                continue

            if not isinstance(payload, dict):
                continue

            names_by_normalized: dict[str, str] = {}
            if any(isinstance(entry, dict) for entry in payload.values()):
                for canonical_key, entry in payload.items():
                    if not isinstance(canonical_key, str) or not canonical_key or not isinstance(entry, dict):
                        continue
                    candidates: list[str] = []
                    for value in (entry.get('display_name'), entry.get('normalized')):
                        if isinstance(value, str) and value and value not in candidates:
                            candidates.append(value)
                    aliases = entry.get('aliases')
                    if isinstance(aliases, list):
                        for alias in aliases:
                            if isinstance(alias, str) and alias and alias not in candidates:
                                candidates.append(alias)
                    for candidate in candidates:
                        normalized_candidate = _normalize_character_name(candidate)
                        if normalized_candidate:
                            names_by_normalized.setdefault(normalized_candidate, canonical_key)
            else:
                for name in payload.keys():
                    if not isinstance(name, str) or not name:
                        continue
                    normalized_name = _normalize_character_name(name)
                    if normalized_name:
                        names_by_normalized.setdefault(normalized_name, name)

            if names_by_normalized:
                _CHARACTER_NAMES_CACHE_KEY = cache_key
                _CHARACTER_NAMES_CACHE_VALUE = names_by_normalized
                return tuple(names_by_normalized.keys())
        except Exception as exc:
            last_exc = exc

    if last_exc is not None:
        logger.debug('Equipped-name runtime character fallback: %s', last_exc)

    from ...data import getCharactersID

    charactersID = getCharactersID(language_code)

    fallback: dict[str, str] = {}
    for name in charactersID.keys():
        if not isinstance(name, str) or not name:
            continue
        normalized_name = _normalize_character_name(name)
        if normalized_name:
            fallback.setdefault(normalized_name, normalized_name)

    if fallback:
        _CHARACTER_NAMES_CACHE_KEY = None
        _CHARACTER_NAMES_CACHE_VALUE = fallback

    return tuple(fallback.keys())


def _normalize_character_name(text: str) -> str:
    return _NON_WORD_RE.sub('', text.casefold())


def _canonicalize_character_name(text: str) -> str | None:
    raw = text.strip()
    if not raw:
        return None

    normalized = _normalize_character_name(raw)
    if not normalized:
        return raw

    canonical_by_normalized = dict(_CHARACTER_NAMES_CACHE_VALUE or {})
    if not canonical_by_normalized:
        _runtime_character_names()
        canonical_by_normalized = dict(_CHARACTER_NAMES_CACHE_VALUE or {})

    if not canonical_by_normalized:
        return normalized

    exact = canonical_by_normalized.get(normalized)
    if exact is not None:
        return exact

    close = get_close_matches(normalized, canonical_by_normalized.keys(), n=1, cutoff=0.75)
    if close:
        return canonical_by_normalized[close[0]]

    return normalized


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