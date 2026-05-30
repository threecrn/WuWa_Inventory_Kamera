"""
wuwa_inventory_kamera.output_serialization
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Central export serializers for on-disk scan results.

The scanning and reprocessing pipelines keep their current in-memory result shapes.
This module is the compatibility boundary that converts those shapes into the
backward-compatible JSON artifacts written to disk.
"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

SCAN_RESULT_FILENAME = 'scan_result.json'
ECHO_EXPORT_FILENAME = 'echoes_wuwainventorykamera.json'
WEAPON_EXPORT_FILENAME = 'weapons_wuwainventorykamera.json'
INVENTORY_EXPORT_FILENAME = 'inventory_wuwainventorykamera.json'
DEV_ITEMS_EXPORT_FILENAME = 'devItems_wuwainventorykamera.json'
RESOURCES_EXPORT_FILENAME = 'resources_wuwainventorykamera.json'
CHARACTER_EXPORT_FILENAME = 'characters_wuwainventorykamera.json'
ACHIEVEMENT_EXPORT_FILENAME = 'achievements_wuwainventorykamera.json'

_WEAPON_ASCENSION_LEVELS: tuple[int, ...] = (20, 40, 50, 60, 70, 80, 90)


def serialize_scan_result(result: Mapping[str, Any]) -> dict[str, Any]:
    """Return the canonical session payload written to ``scan_result.json``."""
    serialized: dict[str, Any] = {}

    if 'date' in result:
        serialized['date'] = result.get('date')
    if 'cancelled' in result:
        serialized['cancelled'] = result.get('cancelled')

    if 'echoes' in result:
        serialized['echoes'] = serialize_echo_export(result.get('echoes'))
    if 'weapons' in result:
        serialized['weapons'] = serialize_weapon_export(result.get('weapons'))

    inventory = serialize_inventory_export(
        dev_items=result.get('devItems'),
        resources=result.get('resources'),
        shell=result.get('shell'),
    )
    if inventory:
        serialized['inventory'] = inventory

    if 'characters' in result:
        serialized['characters'] = serialize_character_export(result.get('characters'))
    if 'achievements' in result:
        serialized['achievements'] = serialize_achievement_export(result.get('achievements'))
    if 'shell' in result:
        serialized['shell'] = serialize_shell_export(result.get('shell'))
    if 'devItems' in result:
        serialized['devItems'] = serialize_item_rows(result.get('devItems'))
    if 'resources' in result:
        serialized['resources'] = serialize_item_rows(result.get('resources'))

    return serialized


def build_standalone_exports(
    result: Mapping[str, Any],
    *,
    include_item_convenience: bool = True,
) -> dict[str, Any]:
    """Return the standalone export files that should be written for *result*."""
    exports: dict[str, Any] = {}

    echoes = serialize_echo_export(result.get('echoes'))
    if _should_write_payload(echoes):
        exports[ECHO_EXPORT_FILENAME] = echoes

    weapons = serialize_weapon_export(result.get('weapons'))
    if _should_write_payload(weapons):
        exports[WEAPON_EXPORT_FILENAME] = weapons

    inventory = serialize_inventory_export(
        dev_items=result.get('devItems'),
        resources=result.get('resources'),
        shell=result.get('shell'),
    )
    if _should_write_payload(inventory):
        exports[INVENTORY_EXPORT_FILENAME] = inventory

    if include_item_convenience:
        dev_items = serialize_item_rows(result.get('devItems'))
        if _should_write_payload(dev_items):
            exports[DEV_ITEMS_EXPORT_FILENAME] = dev_items

        resources = serialize_item_rows(result.get('resources'))
        if _should_write_payload(resources):
            exports[RESOURCES_EXPORT_FILENAME] = resources

    characters = serialize_character_export(result.get('characters'))
    if _should_write_payload(characters):
        exports[CHARACTER_EXPORT_FILENAME] = characters

    achievements = serialize_achievement_export(result.get('achievements'))
    if _should_write_payload(achievements):
        exports[ACHIEVEMENT_EXPORT_FILENAME] = achievements

    return exports


def merge_export_payloads(existing: Mapping[str, Any], incoming: Mapping[str, Any]) -> dict[str, Any]:
    """Merge two export maps, combining inventory payloads by item id."""
    merged = {filename: deepcopy(payload) for filename, payload in existing.items()}

    for filename, payload in incoming.items():
        if filename == INVENTORY_EXPORT_FILENAME and filename in merged:
            merged[filename] = merge_inventory_exports(merged[filename], payload)
            continue
        merged[filename] = deepcopy(payload)

    return merged


def write_json_exports(exports: Mapping[str, Any], output_dir: str | Path) -> dict[str, Path]:
    """Write *exports* into *output_dir* and return the written file paths."""
    output_path = Path(output_dir)
    written: dict[str, Path] = {}

    if not any(_should_write_payload(payload) for payload in exports.values()):
        return written

    output_path.mkdir(parents=True, exist_ok=True)

    for filename, payload in exports.items():
        if not _should_write_payload(payload):
            continue
        file_path = output_path / filename
        with open(file_path, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        written[filename] = file_path

    return written


def serialize_echo_export(payload: Any) -> Any:
    if _is_error_payload(payload):
        return deepcopy(payload)
    return deepcopy(payload) if isinstance(payload, list) else payload


def serialize_weapon_export(payload: Any) -> Any:
    if _is_error_payload(payload):
        return deepcopy(payload)
    if not isinstance(payload, list):
        return deepcopy(payload)

    serialized: list[Any] = []
    for entry in payload:
        if _looks_like_legacy_weapon_entry(entry):
            serialized.append(deepcopy(entry))
            continue
        if not _looks_like_weapon_row(entry):
            serialized.append(deepcopy(entry))
            continue

        weapon_id = entry.get('id')
        if weapon_id is None:
            serialized.append(deepcopy(entry))
            continue

        record: dict[str, Any] = {
            'level': _coerce_int(entry.get('level'), default=0),
            'ascension': _weapon_ascension_from_max_level(entry.get('maxLevel')),
            'rank': _coerce_int(entry.get('rank'), default=1),
        }

        for key in ('weapon_key', 'maxLevel', '_equipped'):
            value = entry.get(key)
            if value is not None and value != '':
                record[key] = deepcopy(value)

        serialized.append({str(weapon_id): record})

    return serialized


def serialize_item_rows(payload: Any) -> Any:
    if _is_error_payload(payload):
        return deepcopy(payload)
    return deepcopy(payload) if isinstance(payload, list) else deepcopy(payload)


def serialize_inventory_export(
    *,
    dev_items: Any = None,
    resources: Any = None,
    shell: Any = None,
) -> dict[str, int]:
    inventory: dict[str, int] = {}
    _merge_item_rows_into_inventory(inventory, dev_items)
    _merge_item_rows_into_inventory(inventory, resources)
    _merge_shell_into_inventory(inventory, shell)
    return inventory


def merge_inventory_exports(existing: Any, incoming: Any) -> dict[str, int]:
    merged: dict[str, int] = {}
    _merge_inventory_map(merged, existing)
    _merge_inventory_map(merged, incoming)
    return merged


def serialize_character_export(payload: Any) -> Any:
    if _is_error_payload(payload):
        return deepcopy(payload)
    if not isinstance(payload, dict):
        return payload

    serialized = deepcopy(payload)
    for details in serialized.values():
        if not isinstance(details, dict):
            continue
        weapon = details.get('weapon')
        if not isinstance(weapon, dict):
            continue
        weapon_id = weapon.get('id')
        normalized_weapon_id = _extract_lookup_id(weapon_id)
        if normalized_weapon_id is not None:
            weapon['id'] = normalized_weapon_id
    return serialized


def serialize_achievement_export(payload: Any) -> Any:
    if _is_error_payload(payload):
        return deepcopy(payload)
    if not isinstance(payload, list):
        return deepcopy(payload)

    serialized: list[Any] = []
    for achievement_id in payload:
        coerced = _coerce_int(achievement_id)
        serialized.append(coerced if coerced is not None else deepcopy(achievement_id))
    return serialized


def serialize_shell_export(payload: Any) -> Any:
    if _is_error_payload(payload):
        return deepcopy(payload)
    if not isinstance(payload, dict):
        return deepcopy(payload)

    serialized: dict[str, int] = {}
    for item_id, amount in payload.items():
        coerced = _coerce_int(amount)
        if coerced is None:
            continue
        serialized[str(item_id)] = coerced
    return serialized


def _should_write_payload(payload: Any) -> bool:
    if payload is None or _is_error_payload(payload):
        return False
    if isinstance(payload, (list, dict)):
        return bool(payload)
    return True


def _is_error_payload(payload: Any) -> bool:
    return isinstance(payload, dict) and set(payload.keys()) == {'error'}


def _looks_like_weapon_row(entry: Any) -> bool:
    return isinstance(entry, dict) and 'id' in entry and any(key in entry for key in ('level', 'maxLevel', 'rank'))


def _looks_like_legacy_weapon_entry(entry: Any) -> bool:
    if not isinstance(entry, dict) or len(entry) != 1:
        return False
    details = next(iter(entry.values()))
    return isinstance(details, dict) and any(key in details for key in ('level', 'ascension', 'rank'))


def _extract_lookup_id(value: Any) -> Any:
    if isinstance(value, dict) and 'id' in value:
        return value.get('id')
    return value


def _weapon_ascension_from_max_level(max_level: Any) -> int:
    resolved = _coerce_int(max_level)
    if resolved is None:
        return 0
    try:
        return _WEAPON_ASCENSION_LEVELS.index(resolved)
    except ValueError:
        return 0


def _merge_item_rows_into_inventory(inventory: dict[str, int], payload: Any) -> None:
    if _is_error_payload(payload) or not isinstance(payload, list):
        return

    for entry in payload:
        if not isinstance(entry, dict) or 'id' not in entry or 'count' not in entry:
            continue
        item_id = str(entry.get('id'))
        count = _coerce_int(entry.get('count'))
        if count is None:
            continue
        inventory[item_id] = inventory.get(item_id, 0) + count


def _merge_shell_into_inventory(inventory: dict[str, int], payload: Any) -> None:
    if _is_error_payload(payload) or not isinstance(payload, dict):
        return

    for item_id, amount in payload.items():
        coerced = _coerce_int(amount)
        if coerced is None:
            continue
        inventory[str(item_id)] = inventory.get(str(item_id), 0) + coerced


def _merge_inventory_map(inventory: dict[str, int], payload: Any) -> None:
    if _is_error_payload(payload) or not isinstance(payload, dict):
        return

    for item_id, amount in payload.items():
        coerced = _coerce_int(amount)
        if coerced is None:
            continue
        key = str(item_id)
        inventory[key] = inventory.get(key, 0) + coerced


def _coerce_int(value: Any, default: int | None = None) -> int | None:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return default