"""
wuwa_inventory_kamera.scraping.service.item_result_normalization
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Shared post-processing for dev-item/resource result rows.

The game detail pane reports total-owned counts, not per-stack counts, for
overflowed stacks. When the same item appears in multiple slots, the OCR
result for each slot can therefore be identical (for example ``1372`` and
``1372`` for a two-stack item). This module reconstructs per-slot counts only
for the specific duplicate pattern that unambiguously matches a known per-item
stack cap from ``ItemInfo.json``.
"""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from typing import Any

from ... import localization_data as _localization_data
from ...config.app_config import app_config, basePATH


def normalize_item_rows(payload: list[Any]) -> list[Any]:
    """Return a normalized deep copy of *payload*.

    Safe normalization rule:

    * rows must share the same ``id`` or ``item_key``;
    * every duplicate row must carry the same ``count``;
    * the item must have a known ``MaxStackableNum`` value; and
    * the number of duplicate rows must equal ``ceil(total / stack_cap)``.

    Only then are the rows rewritten to ``[stack_cap, ..., remainder]`` in
    their original occurrence order.
    """
    normalized = deepcopy(payload)
    groups: dict[tuple[str, str], list[int]] = {}
    stack_caps = _item_stack_caps()

    for index, entry in enumerate(normalized):
        if not isinstance(entry, dict):
            continue

        item_id = entry.get('id')
        if item_id is not None:
            key = ('id', str(item_id))
        else:
            item_key = entry.get('item_key')
            if item_key is None:
                continue
            key = ('item_key', str(item_key))

        groups.setdefault(key, []).append(index)

    for indices in groups.values():
        if len(indices) < 2:
            continue

        counts: list[int] = []
        for index in indices:
            entry = normalized[index]
            if not isinstance(entry, dict):
                counts = []
                break
            count = _coerce_int(entry.get('count'))
            if count is None:
                counts = []
                break
            counts.append(count)

        if not counts:
            continue

        stack_cap = _stack_cap_for_entry(normalized[indices[0]], stack_caps)
        if stack_cap is None:
            continue

        total_owned = counts[0]
        if total_owned <= stack_cap:
            continue
        if any(count != total_owned for count in counts[1:]):
            continue

        expected_stacks = (total_owned + stack_cap - 1) // stack_cap
        if expected_stacks != len(indices):
            continue

        split_counts = [stack_cap] * len(indices)
        remainder = total_owned % stack_cap
        if remainder != 0:
            split_counts[-1] = remainder

        for index, split_count in zip(indices, split_counts):
            entry = normalized[index]
            if isinstance(entry, dict):
                entry['count'] = split_count

    return normalized


def _resolve_game_language_code() -> str:
    return _localization_data.resolve_game_language_code(
        base_path=basePATH,
        selected_language=getattr(app_config, 'gameLanguage', 'English'),
    )


def _item_stack_caps() -> dict[str, int]:
    return _load_item_stack_caps(_resolve_game_language_code())


@lru_cache(maxsize=4)
def _load_item_stack_caps(language_code: str) -> dict[str, int]:
    candidate_codes = tuple(dict.fromkeys((language_code, 'en')))

    for code in candidate_codes:
        payload = _localization_data.load_json_file(basePATH / 'data' / 'raw' / code / 'ItemInfo.json')
        if not isinstance(payload, list):
            continue

        stack_caps: dict[str, int] = {}
        for entry in payload:
            if not isinstance(entry, dict):
                continue

            item_id = _coerce_int(entry.get('Id'))
            stack_cap = _coerce_int(entry.get('MaxStackableNum'))
            if item_id is None or stack_cap is None or stack_cap < 1:
                continue

            stack_caps[str(item_id)] = stack_cap

        if stack_caps:
            return stack_caps

    return {}


def _stack_cap_for_entry(entry: Any, stack_caps: dict[str, int]) -> int | None:
    if not isinstance(entry, dict):
        return None

    item_id = _coerce_int(entry.get('id'))
    if item_id is None:
        return None

    stack_cap = stack_caps.get(str(item_id))
    if stack_cap is None or stack_cap < 1:
        return None

    return stack_cap


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None