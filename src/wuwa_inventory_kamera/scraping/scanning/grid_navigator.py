"""
wuwa_inventory_kamera.scraping.scanning.grid_navigator
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Reusable grid traversal logic for inventory scanning.

The :class:`GridNavigator` drives the :class:`~...game.navigation.GameNavigator`
through the pages/rows/cols of a grid-based inventory (echoes, weapons,
items) while respecting:

* Forward scan order (page → row → col).
* Random-access navigation for **rescans** — jumps to any
  ``(page, row, col)`` by scrolling to the correct page first.
* Early termination when all items have been visited.
* A callback hook for each cell visit so the scanning workflow can
  capture screenshots and submit OCR work.

This class does NOT do OCR or manage scan state — those are the workflow's
responsibility.
"""
from __future__ import annotations

import logging
from typing import Callable, Protocol

from wuwa_inventory_kamera.game.navigation import (
    GameNavigator,
    GRID_ROWS,
    GRID_COLS,
    CELLS_PER_PAGE,
)
from wuwa_inventory_kamera.scraping.scanning.scan_state import GridPosition

logger = logging.getLogger(__name__)


class CellVisitor(Protocol):
    """
    Callback invoked for each grid cell the navigator lands on.

    The visitor receives the grid position and returns ``True`` to
    continue scanning or ``False`` to abort immediately.
    """

    def __call__(self, position: GridPosition) -> bool: ...


class GridNavigator:
    """
    Drives forward and random-access traversal of a grid inventory.

    Parameters
    ----------
    nav:
        :class:`~...game.navigation.GameNavigator` controlling the game.
    total_items:
        Total number of items in the inventory (from the page-count OCR).
    total_pages:
        Number of full pages (ceil(total_items / 24)).
    """

    def __init__(
        self,
        nav: GameNavigator,
        total_items: int,
        total_pages: int,
    ) -> None:
        self.nav = nav
        self.total_items = total_items
        self.total_pages = total_pages
        self._current_page = 0

    @property
    def current_page(self) -> int:
        return self._current_page

    # ── Forward scan ─────────────────────────────────────────────────────

    def scan_forward(
        self,
        visitor: CellVisitor,
        start_index: int = 0,
    ) -> int:
        """
        Visit every grid cell from *start_index* to the end.

        Calls *visitor* for each cell.  Returns the number of cells visited.
        """
        visited = 0
        start_pos = GridPosition.from_index(start_index)

        # Navigate to the correct starting page
        if start_pos.page != self._current_page:
            self.navigate_to_page(start_pos.page)

        for index in range(start_index, self.total_items):
            pos = GridPosition.from_index(index)

            # Page transition
            if pos.page != self._current_page:
                self.navigate_to_page(pos.page)

            # Click the cell
            self.nav.click_grid_cell(pos.row, pos.col)
            visited += 1

            # Invoke the visitor; abort if it returns False
            if not visitor(pos):
                logger.info('Visitor requested abort at index %d', index)
                break

        logger.debug('Forward scan visited %d cell(s)', visited)
        return visited

    # ── Random-access navigation (for rescans) ───────────────────────────

    def navigate_to_cell(self, position: GridPosition) -> None:
        """
        Navigate to a specific grid cell, scrolling pages as needed.

        After this call the cell at *position* is selected (clicked).
        """
        if position.page != self._current_page:
            self.navigate_to_page(position.page)
        self.nav.click_grid_cell(position.row, position.col)
        logger.debug(
            'Navigated to cell index=%d (page=%d row=%d col=%d)',
            position.scan_index, position.page, position.row, position.col,
        )

    def navigate_to_page(self, page: int) -> None:
        """Scroll to the given page (0-based)."""
        self.nav.scroll_to_page(page, self._current_page)
        self._current_page = page

    # ── Rescan batch ─────────────────────────────────────────────────────

    def visit_positions(
        self,
        positions: list[GridPosition],
        visitor: CellVisitor,
    ) -> int:
        """
        Visit an arbitrary list of positions (for rescans).

        Positions are sorted by page to minimize scrolling, then visited
        in order.  Returns the number of cells visited.
        """
        if not positions:
            return 0

        # Sort by page, then row, then col for minimal scrolling
        sorted_pos = sorted(positions, key=lambda p: (p.page, p.row, p.col))
        visited = 0

        for pos in sorted_pos:
            self.navigate_to_cell(pos)
            visited += 1
            if not visitor(pos):
                break

        return visited
