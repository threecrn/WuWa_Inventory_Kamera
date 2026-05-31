"""
wuwa_inventory_kamera.cli.exporter
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Convert WuWa Inventory Kamera character/echo exports into the
WutheringTools export envelope.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_NUMERIC_PATTERN = re.compile(r'-?\d+(?:\.\d+)?')


@dataclass(frozen=True)
class _LocalizationMaps:
    echoes_by_id: dict[str, dict[str, str]]
    characters_by_id: dict[str, str]
    sonata_by_key: dict[str, str]


def _normalize_lookup(value: object) -> str:
    return ''.join(ch for ch in str(value).strip().lower() if ch.isalnum())


def _tokenize_name(value: object) -> str:
    text = str(value).strip()
    if not text:
        return ''
    parts = re.findall(r'[A-Za-z0-9]+', text)
    if not parts:
        return ''
    return ''.join(part[:1].upper() + part[1:] for part in parts)


def _compact_preserve_case(value: object) -> str:
    text = str(value).strip()
    if not text:
        return ''
    parts = re.findall(r'[A-Za-z0-9]+', text)
    return ''.join(parts)


def _load_json(path: Path) -> Any:
    with open(path, encoding='utf-8') as handle:
        return json.load(handle)


def _load_locale_file(data_root: Path, language: str, filename: str) -> dict[str, Any]:
    primary = data_root / 'locale' / language / filename
    fallback = data_root / 'locale' / 'en' / filename
    for candidate in (primary, fallback):
        try:
            payload = _load_json(candidate)
        except (FileNotFoundError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _build_localization_maps(*, language: str) -> _LocalizationMaps:
    repo_root = Path(__file__).resolve().parents[3]
    data_root = repo_root / 'data'

    echoes_catalog = _load_json(data_root / 'catalog' / 'echoes.json')
    echoes_locale = _load_locale_file(data_root, language, 'echoes.json')
    characters_catalog = _load_json(data_root / 'catalog' / 'characters.json')
    characters_locale = _load_locale_file(data_root, language, 'characters.json')
    sonata_locale = _load_locale_file(data_root, language, 'sonatas.json')

    echoes_by_id: dict[str, dict[str, str]] = {}
    if isinstance(echoes_catalog, dict) and isinstance(echoes_locale, dict):
        for canonical_key, info in echoes_catalog.items():
            if not isinstance(canonical_key, str) or not isinstance(info, dict):
                continue
            identifier = info.get('id')
            if identifier is None:
                continue
            locale_record = echoes_locale.get(canonical_key)
            display_name = (
                locale_record.get('display_name')
                if isinstance(locale_record, dict)
                else None
            )
            token_name = _tokenize_name(display_name or canonical_key)
            echoes_by_id[str(identifier)] = {
                'canonical': canonical_key,
                'display': token_name or str(identifier),
            }

    characters_by_id: dict[str, str] = {}
    if isinstance(characters_catalog, dict) and isinstance(characters_locale, dict):
        for canonical_key, info in characters_catalog.items():
            if not isinstance(canonical_key, str) or not isinstance(info, dict):
                continue
            identifier = info.get('id')
            if identifier is None:
                continue
            locale_record = characters_locale.get(canonical_key)
            display_name = (
                locale_record.get('display_name')
                if isinstance(locale_record, dict)
                else None
            )
            token_name = _tokenize_name(display_name or canonical_key)
            characters_by_id[str(identifier)] = token_name or canonical_key

    sonata_by_key: dict[str, str] = {}
    if isinstance(sonata_locale, dict):
        for canonical_key, record in sonata_locale.items():
            if not isinstance(canonical_key, str):
                continue
            display_name = record.get('display_name') if isinstance(record, dict) else None
            sonata_by_key[canonical_key] = _compact_preserve_case(display_name or canonical_key)

    return _LocalizationMaps(
        echoes_by_id=echoes_by_id,
        characters_by_id=characters_by_id,
        sonata_by_key=sonata_by_key,
    )


def _extract_payload(path: Path, *, section_name: str) -> Any:
    payload = _load_json(path)
    if isinstance(payload, dict) and section_name in payload:
        return payload[section_name]
    return payload


def _to_number(value: object) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if isinstance(value, float) and value.is_integer() else value
    if not isinstance(value, str):
        return None
    match = _NUMERIC_PATTERN.search(value)
    if not match:
        return None
    parsed = float(match.group(0))
    return int(parsed) if parsed.is_integer() else parsed


def _stat_token(stat_name: object, *, value: object, is_main: bool) -> str:
    raw = str(stat_name).strip()
    normalized = _normalize_lookup(raw)
    percent_hint = '%' in raw or (isinstance(value, str) and '%' in value)

    direct = {
        'cr': 'CritRate',
        'critrate': 'CritRate',
        'cd': 'CritDMG',
        'critdmg': 'CritDMG',
        'energyregen': 'EnergyRegen',
        'er': 'EnergyRegen',
        'resonanceliberationdmgbonus': 'ResonanceLiberationDMGBonus',
        'resonanceskilldmgbonus': 'ResonanceSkillDMGBonus',
        'basicattackdmgbonus': 'BasicAttackDMGBonus',
        'heavyattackdmgbonus': 'HeavyAttackDMGBonus',
        'liberationdmg': 'ResonanceLiberationDMGBonus',
        'skilldmg': 'ResonanceSkillDMGBonus',
        'healingbonus': 'HealingBonus',
        'fusiondmgbonus': 'Fusion',
        'electrodmgbonus': 'Electro',
        'glaciodmgbonus': 'Glacio',
        'aerodmgbonus': 'Aero',
        'spectrodmgbonus': 'Spectro',
        'havocdmgbonus': 'Havoc',
        'fusion': 'Fusion',
        'electro': 'Electro',
        'glacio': 'Glacio',
        'aero': 'Aero',
        'spectro': 'Spectro',
        'havoc': 'Havoc',
    }
    if normalized in direct:
        return direct[normalized]

    if normalized in {'hp', 'atk', 'def'}:
        base = normalized.upper()
        if percent_hint or is_main:
            return base
        return f'{base}_FLAT'

    if normalized in {'hpflat', 'atkflat', 'defflat'}:
        return {
            'hpflat': 'HP_FLAT',
            'atkflat': 'ATK_FLAT',
            'defflat': 'DEF_FLAT',
        }[normalized]

    if raw.endswith('%') and normalized:
        return _tokenize_name(normalized)
    return _tokenize_name(raw)


def _stable_echo_id(record: dict[str, Any], *, index: int) -> str:
    digest = hashlib.sha1(
        json.dumps(record, sort_keys=True, separators=(',', ':')).encode('utf-8')
        + f'#{index}'.encode('ascii')
    ).hexdigest()
    return digest[:10]


def _resolve_echo_name(
    echo_identifier: str,
    echo_record: dict[str, Any],
    maps: _LocalizationMaps,
) -> str:
    match = maps.echoes_by_id.get(str(echo_identifier))
    if match:
        return match.get('display', str(echo_identifier))
    echo_key = echo_record.get('echo_key') or echo_record.get('echo') or echo_identifier
    return _tokenize_name(echo_key) or str(echo_identifier)


def _resolve_sonata(echo_record: dict[str, Any], maps: _LocalizationMaps) -> str | None:
    sonata_key = echo_record.get('sonata_key') or echo_record.get('sonata')
    if sonata_key is None:
        return None
    sonata_ref = str(sonata_key)
    if sonata_ref in maps.sonata_by_key:
        return maps.sonata_by_key[sonata_ref]
    return _compact_preserve_case(sonata_ref) or None


def _build_wt_echo(
    echo_identifier: str,
    echo_record: dict[str, Any],
    maps: _LocalizationMaps,
    *,
    index: int,
) -> dict[str, Any]:
    stats = echo_record.get('stats') if isinstance(echo_record.get('stats'), dict) else {}
    main_stats: dict[str, Any] = {}
    sub_stats: dict[str, Any] = {}
    if isinstance(stats, dict):
        raw_main = stats.get('main')
        raw_sub = stats.get('sub')
        if isinstance(raw_main, dict):
            main_stats = raw_main
        if isinstance(raw_sub, dict):
            sub_stats = raw_sub

    main_name = None
    main_value: object = None
    if main_stats:
        main_name, main_value = next(iter(main_stats.items()))

    wt_echo: dict[str, Any] = {
        'echo': _resolve_echo_name(echo_identifier, echo_record, maps),
        'type': int(echo_record.get('_cost') or 1),
        'rank': int(echo_record.get('rarity') or 0),
        'stat': _stat_token(main_name or '', value=main_value, is_main=True) if main_name else '',
        'echoId': _stable_echo_id(echo_record, index=index),
        'echoSet': _resolve_sonata(echo_record, maps),
    }

    for offset, (sub_name, sub_value) in enumerate(sub_stats.items(), start=1):
        if offset > 5:
            break
        wt_echo[f'echoSubStatsType{offset}'] = _stat_token(sub_name, value=sub_value, is_main=False)
        wt_echo[f'echoSubStatsValue{offset}'] = _to_number(sub_value)

    return wt_echo


def _normalize_characters_payload(payload: Any) -> dict[str, dict[str, Any]]:
    if isinstance(payload, dict) and 'characters' in payload and isinstance(payload['characters'], dict):
        return payload['characters']
    if isinstance(payload, dict):
        return payload
    return {}


def _normalize_echoes_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and 'echoes' in payload and isinstance(payload['echoes'], list):
        return payload['echoes']
    if isinstance(payload, list):
        return payload
    return []


def build_wutheringtools_export(
    *,
    characters_payload: Any,
    echoes_payload: Any,
    language: str = 'en',
) -> dict[str, Any]:
    maps = _build_localization_maps(language=language)

    characters_export = _normalize_characters_payload(characters_payload)
    echoes_export = _normalize_echoes_payload(echoes_payload)

    characters_out: dict[str, dict[str, Any]] = {}
    for character_id, details in characters_export.items():
        if not isinstance(details, dict):
            continue
        resolved_name = maps.characters_by_id.get(str(character_id))
        if not resolved_name:
            key_hint = details.get('_name') or details.get('character_key') or character_id
            resolved_name = _tokenize_name(key_hint) or str(character_id)
        characters_out[resolved_name] = {
            'echoes': {},
        }

    inventory_echoes: list[dict[str, Any]] = []
    equipped_echoes: dict[str, list[dict[str, Any]]] = {}
    for index, entry in enumerate(echoes_export):
        if not isinstance(entry, dict) or len(entry) != 1:
            continue
        echo_identifier, echo_record = next(iter(entry.items()))
        if not isinstance(echo_record, dict):
            continue

        wt_echo = _build_wt_echo(str(echo_identifier), echo_record, maps, index=index)
        inventory_echoes.append(wt_echo)

        equipped_name = echo_record.get('_equipped')
        if isinstance(equipped_name, str) and equipped_name.strip():
            character_name = _tokenize_name(equipped_name)
            if character_name:
                equipped_entry = dict(wt_echo)
                equipped_entry.pop('echoId', None)
                equipped_echoes.setdefault(character_name, []).append(equipped_entry)
                characters_out.setdefault(character_name, {'echoes': {}})

    for character_name, echoes in equipped_echoes.items():
        echo_slots: dict[str, dict[str, Any]] = {}
        for slot_index, wt_echo in enumerate(echoes[:5]):
            echo_slots[str(slot_index)] = wt_echo
        characters_out[character_name]['echoes'] = echo_slots

    active_character = next(iter(characters_out.keys()), '')

    character_data = {
        'characters': characters_out,
        'activeCharacter': active_character,
    }
    inventory_data = {
        'echoes': inventory_echoes,
        'equipped': {},
        'echoPresets': [],
        'equippedPresets': {},
    }

    return {
        'meta': {'version': '2', 'source': 'WutheringTools'},
        'data': {
            'character': json.dumps(character_data, ensure_ascii=False, separators=(',', ':')),
            'inventory': json.dumps(inventory_data, ensure_ascii=False, separators=(',', ':')),
        },
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='wuwa-exporter',
        description=(
            'Convert WuWa Inventory Kamera character + echo exports into '
            'WutheringTools export format.'
        ),
    )
    parser.add_argument(
        '--characters',
        required=True,
        metavar='PATH',
        help='Path to character export JSON (characters_wuwainventorykamera.json or scan_result.json).',
    )
    parser.add_argument(
        '--echoes',
        required=True,
        metavar='PATH',
        help='Path to echo export JSON (echoes_wuwainventorykamera.json or scan_result.json).',
    )
    parser.add_argument(
        '--output',
        metavar='PATH',
        default='wutheringtools_export.json',
        help='Output JSON path (default: wutheringtools_export.json).',
    )
    parser.add_argument(
        '--language',
        default='en',
        help='Localization language code for display-name mapping (default: en).',
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    characters_path = Path(args.characters)
    echoes_path = Path(args.echoes)
    output_path = Path(args.output)

    characters_payload = _extract_payload(characters_path, section_name='characters')
    echoes_payload = _extract_payload(echoes_path, section_name='echoes')

    converted = build_wutheringtools_export(
        characters_payload=characters_payload,
        echoes_payload=echoes_payload,
        language=str(args.language),
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as handle:
        json.dump(converted, handle, indent=2, ensure_ascii=False)

    print(f'Wrote WutheringTools export to {output_path}')


if __name__ == '__main__':
    main()
