from __future__ import annotations

from wuwa_inventory_kamera.scraping.service.item_result_normalization import normalize_item_rows


def test_normalize_item_rows_reconstructs_overflow_stack_counts() -> None:
    payload = [
        {'id': 42310060, 'item_key': 'angelica', 'count': 1372},
        {'id': 42310060, 'item_key': 'angelica', 'count': 1372},
        {'id': 42400030, 'item_key': 'rawmeat', 'count': 3094},
        {'id': 42400030, 'item_key': 'rawmeat', 'count': 3094},
        {'id': 42400030, 'item_key': 'rawmeat', 'count': 3094},
        {'id': 42400030, 'item_key': 'rawmeat', 'count': 3094},
    ]

    assert normalize_item_rows(payload) == [
        {'id': 42310060, 'item_key': 'angelica', 'count': 999},
        {'id': 42310060, 'item_key': 'angelica', 'count': 373},
        {'id': 42400030, 'item_key': 'rawmeat', 'count': 999},
        {'id': 42400030, 'item_key': 'rawmeat', 'count': 999},
        {'id': 42400030, 'item_key': 'rawmeat', 'count': 999},
        {'id': 42400030, 'item_key': 'rawmeat', 'count': 97},
    ]


def test_normalize_item_rows_leaves_non_matching_duplicates_unchanged() -> None:
    payload = [
        {'id': 42310060, 'item_key': 'angelica', 'count': 1372},
        {'id': 42310060, 'item_key': 'angelica', 'count': 1200},
        {'id': 42400030, 'item_key': 'rawmeat', 'count': 900},
        {'id': 42400030, 'item_key': 'rawmeat', 'count': 900},
    ]

    assert normalize_item_rows(payload) == payload