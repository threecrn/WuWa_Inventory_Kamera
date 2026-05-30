from __future__ import annotations

import wuwa_inventory_kamera.scraping.service.item_result_normalization as item_result_normalization
from wuwa_inventory_kamera.scraping.service.item_result_normalization import normalize_item_rows


def test_normalize_item_rows_reconstructs_overflow_stack_counts(monkeypatch) -> None:
    monkeypatch.setattr(
        item_result_normalization,
        '_item_stack_caps',
        lambda: {'42310060': 999, '42400030': 999},
    )

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


def test_normalize_item_rows_uses_per_item_stack_cap(monkeypatch) -> None:
    monkeypatch.setattr(
        item_result_normalization,
        '_item_stack_caps',
        lambda: {'50000001': 9999},
    )

    payload = [
        {'id': 50000001, 'item_key': 'mystery-item', 'count': 12000},
        {'id': 50000001, 'item_key': 'mystery-item', 'count': 12000},
    ]

    assert normalize_item_rows(payload) == [
        {'id': 50000001, 'item_key': 'mystery-item', 'count': 9999},
        {'id': 50000001, 'item_key': 'mystery-item', 'count': 2001},
    ]


def test_normalize_item_rows_leaves_non_matching_duplicates_unchanged(monkeypatch) -> None:
    monkeypatch.setattr(
        item_result_normalization,
        '_item_stack_caps',
        lambda: {'42310060': 999, '42400030': 999},
    )

    payload = [
        {'id': 42310060, 'item_key': 'angelica', 'count': 1372},
        {'id': 42310060, 'item_key': 'angelica', 'count': 1200},
        {'id': 42400030, 'item_key': 'rawmeat', 'count': 900},
        {'id': 42400030, 'item_key': 'rawmeat', 'count': 900},
    ]

    assert normalize_item_rows(payload) == payload


def test_normalize_item_rows_leaves_unknown_stack_caps_unchanged(monkeypatch) -> None:
    monkeypatch.setattr(item_result_normalization, '_item_stack_caps', lambda: {})

    payload = [
        {'id': 50000001, 'item_key': 'mystery-item', 'count': 12000},
        {'id': 50000001, 'item_key': 'mystery-item', 'count': 12000},
    ]

    assert normalize_item_rows(payload) == payload