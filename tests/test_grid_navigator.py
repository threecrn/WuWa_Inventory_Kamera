from __future__ import annotations

import pytest

from wuwa_inventory_kamera.scraping.scanning.grid_navigator import GridNavigator
from wuwa_inventory_kamera.scraping.scanning.scan_state import GridPosition, ScanSession


class _FakeNav:
    def __init__(self) -> None:
        self.scrolls: list[tuple[int, int]] = []
        self.clicks: list[tuple[int, int]] = []

    def scroll_to_page(self, target_page: int, current_page: int) -> None:
        self.scrolls.append((current_page, target_page))

    def click_grid_cell(self, row: int, col: int) -> None:
        self.clicks.append((row, col))


@pytest.mark.parametrize(
    ('total_items', 'index', 'expected'),
    [
        (30, 23, (0, 3, 5)),
        (30, 24, (1, 3, 0)),
        (30, 29, (1, 3, 5)),
        (50, 47, (1, 3, 5)),
        (50, 48, (2, 3, 4)),
        (50, 49, (2, 3, 5)),
    ],
)
def test_grid_position_maps_tail_chunk_to_overlapping_final_page(
    total_items: int,
    index: int,
    expected: tuple[int, int, int],
) -> None:
    position = GridPosition.from_index(index, total_items=total_items)

    assert (position.page, position.row, position.col) == expected
    assert position.to_index(total_items=total_items) == index


def test_scan_session_uses_final_page_overlap_positions() -> None:
    session = ScanSession(total_items=30)

    assert session.items[24].position == GridPosition(page=1, row=3, col=0, scan_index=24)
    assert session.items[29].position == GridPosition(page=1, row=3, col=5, scan_index=29)


def test_grid_navigator_scan_forward_clicks_tail_items_on_overlapping_final_page() -> None:
    nav = _FakeNav()
    grid = GridNavigator(nav, total_items=30, total_pages=2)
    visited: list[GridPosition] = []

    count = grid.scan_forward(lambda pos: visited.append(pos) or True)

    assert count == 30
    assert nav.scrolls == [(0, 1)]
    assert nav.clicks[23] == (3, 5)
    assert nav.clicks[24] == (3, 0)
    assert nav.clicks[-1] == (3, 5)
    assert visited[24] == GridPosition(page=1, row=3, col=0, scan_index=24)
    assert visited[-1] == GridPosition(page=1, row=3, col=5, scan_index=29)