"""
wuwa_inventory_kamera.scraping.service.item_result_normalization
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Shared post-processing for dev-item/resource result rows.

The game detail pane reports total-owned counts, not per-stack counts, for
overflowed 999-sized stacks. When the same item appears in multiple slots, the
OCR result for each slot can therefore be identical (for example ``1372`` and
``1372`` for a two-stack item). This module reconstructs per-slot counts only
for the specific duplicate pattern that unambiguously matches the game's 999
stack cap.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

ITEM_MAX_STACK_COUNT = 999


def normalize_item_rows(payload: list[Any]) -> list[Any]:
    """Return a normalized deep copy of *payload*.

    Safe normalization rule:

    * rows must share the same ``id`` or ``item_key``;
    * every duplicate row must carry the same ``count``;
    * that count must be greater than the stack cap; and
    * the number of duplicate rows must equal ``ceil(total / 999)``.

    Only then are the rows rewritten to ``[999, ..., remainder]`` in their
    original occurrence order.
    """
    normalized = deepcopy(payload)
    groups: dict[tuple[str, str], list[int]] = {}

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

        total_owned = counts[0]
        if total_owned <= ITEM_MAX_STACK_COUNT:
            continue
        if any(count != total_owned for count in counts[1:]):
            continue

        expected_stacks = (total_owned + ITEM_MAX_STACK_COUNT - 1) // ITEM_MAX_STACK_COUNT
        if expected_stacks != len(indices):
            continue

        split_counts = [ITEM_MAX_STACK_COUNT] * len(indices)
        remainder = total_owned % ITEM_MAX_STACK_COUNT
        if remainder != 0:
            split_counts[-1] = remainder

        for index, split_count in zip(indices, split_counts):
            entry = normalized[index]
            if isinstance(entry, dict):
                entry['count'] = split_count

    return normalized


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None