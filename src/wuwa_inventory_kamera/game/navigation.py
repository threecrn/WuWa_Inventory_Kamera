"""
wuwa_inventory_kamera.game.navigation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

High-level game navigation primitives — opening the inventory, switching
tabs, manipulating sort orders, and detecting the current screen state.

These are built on top of :class:`~.input_controller.InputController` and
:class:`~.screen.GameWindow` / :class:`~.screen.ScreenLayout`, but are
completely independent of the Qt UI.

The key design goals are:

* **Callable from CLI or UI** — no signals, no Qt types.
* **State-aware** — the :class:`GameNavigator` tracks which inventory tab
  is currently open and what sort order is active so callers don't repeat
  unnecessary clicks.
* **Sort-order control** — can read and change the sort order dropdown,
  which is important for echo scanning workflows that need a specific
  list ordering.

Usage::

    from wuwa_inventory_kamera.game.input_controller import InputController
    from wuwa_inventory_kamera.game.screen import GameWindow
    from wuwa_inventory_kamera.game.navigation import GameNavigator

    gw   = GameWindow()
    ctrl = InputController(gw.monitor_index)
    nav  = GameNavigator(ctrl, gw)

    nav.open_inventory()
    nav.switch_tab('echoes')
    nav.set_sort_order(SortOrder.NEWEST)
"""
from __future__ import annotations

import enum
import logging
import string
import time

import numpy as np

from wuwa_inventory_kamera.game.input_controller import InputController
from wuwa_inventory_kamera.game.screen import (
    GameWindow,
    ScreenLayout,
    capture_full,
    capture_region,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums / constants
# ---------------------------------------------------------------------------

class InventoryTab(enum.Enum):
    """Tabs in the game's inventory / backpack menu."""
    WEAPONS  = 'weapons'
    ECHOES   = 'echoes'
    DEV_ITEMS  = 'devItems'
    RESOURCES  = 'resources'


class SortOrder(enum.Enum):
    """
    Sort orders available in the echo / weapon inventory dropdown.

    The values are the 0-based position in the dropdown list.
    """
    NEWEST       = 0    # date acquired (newest first)
    OLDEST       = 1    # date acquired (oldest first)
    QUALITY_DESC = 2    # rarity descending
    QUALITY_ASC  = 3    # rarity ascending
    LEVEL_DESC   = 4    # level descending
    LEVEL_ASC    = 5    # level ascending


# Grid geometry shared across all grid-based inventories.
GRID_ROWS = 4
GRID_COLS = 6
CELLS_PER_PAGE = GRID_ROWS * GRID_COLS  # 24


# ---------------------------------------------------------------------------
# Minimal inline OCR helper (for navigation-only reads)
# ---------------------------------------------------------------------------

def _nav_ocr(image: np.ndarray, allowed: str | None = None) -> str:
    """
    Quick inline OCR used only for navigation control (page counts, etc.).

    This does NOT use the batched OcrService — it's a lightweight single
    call via the OCR registry default backend.
    """
    from wuwa_inventory_kamera.scraping.ocr import imageToString
    return imageToString(image, allowedChars=allowed)


# ---------------------------------------------------------------------------
# GameNavigator
# ---------------------------------------------------------------------------

class GameNavigator:
    """
    Stateful high-level game navigator.

    Tracks the current inventory tab and sort order so repeated calls
    avoid redundant input.

    Parameters
    ----------
    ctrl:
        :class:`~.input_controller.InputController` for this session.
    gw:
        :class:`~.screen.GameWindow` providing the screen layout.
    inventory_key:
        The keybind that opens the inventory (default: ``'b'``).
    """

    def __init__(
        self,
        ctrl: InputController,
        gw: GameWindow,
        inventory_key: str = 'b',
    ) -> None:
        self.ctrl = ctrl
        self.gw = gw
        self.layout: ScreenLayout = gw.layout
        self._inventory_key = inventory_key
        self._current_tab: InventoryTab | None = None
        self._current_sort: SortOrder | None = None
        self._inventory_open = False

    # ── Inventory open / close ──────────────────────────────────────────

    def open_inventory(self, wait: float = 2.0) -> None:
        """Press the inventory keybind and wait for the UI to render."""
        self.ctrl.press_key(self._inventory_key, wait=wait)
        self._inventory_open = True
        self._current_tab = None
        self._current_sort = None
        logger.debug('Opened inventory (key=%r)', self._inventory_key)

    def close_inventory(self, wait: float = 0.5) -> None:
        """Press Esc to close the inventory."""
        self.ctrl.press_key('esc', wait=wait)
        self._inventory_open = False
        self._current_tab = None
        self._current_sort = None
        logger.debug('Closed inventory')

    # ── Tab switching ────────────────────────────────────────────────────

    def switch_tab(self, tab: InventoryTab | str, wait: float = 0.5) -> None:
        """
        Navigate to the given inventory tab.

        If the inventory is not open, opens it first.  If already on the
        requested tab, this is a no-op.
        """
        if isinstance(tab, str):
            tab = InventoryTab(tab)

        if not self._inventory_open:
            self.open_inventory()

        if self._current_tab == tab:
            logger.debug('Already on tab %s', tab.value)
            return

        coords = getattr(self.layout.scrapers, tab.value, None)
        if coords is None:
            raise ValueError(f'No known screen coordinates for tab {tab!r}')

        self.ctrl.click(coords.x, coords.y, wait=wait)
        self._current_tab = tab
        self._current_sort = None  # sort state unknown after tab switch
        logger.debug('Switched to tab %s', tab.value)

    # ── Sort order manipulation ──────────────────────────────────────────

    def set_sort_order(self, order: SortOrder, wait: float = 0.3) -> None:
        """
        Activate a specific sort order in the current inventory tab.

        Opens the sort dropdown, scrolls/clicks to the desired position,
        then closes it.  Sort state is tracked so repeated calls for the
        same order are no-ops.
        """
        if self._current_sort == order:
            logger.debug('Sort order already %s', order.name)
            return

        # The sort button is near the top-right of the grid area.
        # Its exact coordinates depend on the resolution; we compute them
        # relative to the page-count area, offset to the right.
        page_roi = self._page_count_roi()
        if page_roi is None:
            logger.warning('Cannot determine sort button position — skipping sort change')
            return

        # Sort dropdown button is roughly at the right edge of the page
        # counter area, offset further right.  This is a conservative
        # estimate that works for 16:9 and 16:10.
        sort_btn_x = page_roi.x + page_roi.w + 60
        sort_btn_y = page_roi.y + page_roi.h // 2

        # Click the sort button to open the dropdown
        self.ctrl.click(sort_btn_x, sort_btn_y, wait=0.3)

        # Each entry in the dropdown is about 35px tall.  Click the
        # desired entry relative to the button position.
        entry_y = sort_btn_y + 40 + order.value * 35
        self.ctrl.click(sort_btn_x, entry_y, wait=wait)

        self._current_sort = order
        logger.info('Sort order set to %s', order.name)

    # ── Page / item count reading ────────────────────────────────────────

    def read_item_count(self) -> tuple[int, int]:
        """
        Read the item count and page count from the current inventory tab.

        Returns ``(item_count, page_count)``.
        """
        page_roi = self._page_count_roi()
        if page_roi is None:
            logger.warning('Cannot determine page-count ROI — defaulting to 24/1')
            return 24, 1

        full = capture_full(self.layout.width, self.layout.height, self.layout.monitor)
        crop = full[
            int(page_roi.y) : int(page_roi.y + page_roi.h),
            int(page_roi.x) : int(page_roi.x + page_roi.w),
        ]
        raw = _nav_ocr(crop, allowed=string.digits + '/')
        parts = raw.split('/')
        try:
            count = int(parts[0])
            pages = int(np.ceil(count / CELLS_PER_PAGE))
            return count, pages
        except (ValueError, IndexError):
            logger.warning('Could not parse item count from %r — defaulting', raw)
            return 24, 1

    # ── Grid cell coordinate calculation ─────────────────────────────────

    def grid_cell_center(self, row: int, col: int) -> tuple[float, float]:
        """
        Compute the screen coordinates of the center of grid cell
        (*row*, *col*) on the current tab.

        Uses the ``start`` and ``offsets.page`` attributes from the current
        tab's coordinate tree.

        Returns ``(center_x, center_y)``.
        """
        tab = self._current_tab
        if tab is None:
            raise RuntimeError('No inventory tab is selected')

        tab_coords = getattr(self.layout, tab.value)
        start = tab_coords.start
        offsets = self.layout.offsets.page

        cx = start.x + col * (start.w + offsets.x) + start.w // 2
        cy = start.y + row * (start.h + offsets.y) + start.h // 2
        return cx, cy

    def click_grid_cell(self, row: int, col: int, wait: float = 0.1) -> None:
        """Click the center of the given grid cell."""
        cx, cy = self.grid_cell_center(row, col)
        self.ctrl.click(cx, cy, wait=wait)

    # ── Page scrolling ───────────────────────────────────────────────────

    def scroll_page(self, direction: int = 1, wait: float = 1.2) -> None:
        """
        Scroll to the next (direction=1) or previous (direction=-1) page
        in the grid inventory.

        The scroll amount is taken from the screen layout.
        """
        amount = self.layout.scroll.page.y * direction
        self.ctrl.scroll(amount, wait=wait)

    def scroll_to_page(self, target_page: int, current_page: int) -> None:
        """
        Scroll from *current_page* to *target_page* (0-based).

        Scrolls one page at a time.  Direction is determined automatically.
        """
        if target_page == current_page:
            return
        direction = 1 if target_page > current_page else -1
        for _ in range(abs(target_page - current_page)):
            self.scroll_page(direction)
        logger.debug('Scrolled from page %d to page %d', current_page, target_page)

    # ── Echo-specific: sonata scroll ─────────────────────────────────────

    def scroll_to_sonata(self) -> None:
        """
        Scroll the echo detail panel down to reveal the sonata section,
        then fine-tune by scrolling back up slightly.
        """
        mouse_pos = self.layout.echoes.mouseMovement
        self.ctrl.move(mouse_pos.x, mouse_pos.y, wait=0.05)
        self.ctrl.scroll(self.layout.scroll.sonata.y, wait=0.3)
        # Fine-tune: scroll back up 2px worth (matches original behaviour)
        self.ctrl.scroll(-2, wait=0.15)

    def scroll_back_from_sonata(self) -> None:
        """
        Scroll the echo detail panel back up to the stats section.
        """
        mouse_pos = self.layout.echoes.mouseMovement
        self.ctrl.move(mouse_pos.x, mouse_pos.y, wait=0.05)
        sonata_y = self.layout.scroll.sonata.y
        # Reverse the scroll we did minus the fine-tune
        self.ctrl.scroll(-(sonata_y - 2), wait=0.2)

    # ── Character-specific navigation ────────────────────────────────────

    def scroll_character_list(self, amount: float | None = None, wait: float = 0.5) -> None:
        """Scroll the character sidebar list."""
        scroll_amount: float = amount if amount is not None else self.layout.scroll.characters.y
        self.ctrl.scroll(scroll_amount, wait=wait)

    # ── Menu detection ───────────────────────────────────────────────────

    def is_in_main_menu(self) -> bool:
        """
        Quick OCR check to see if the game is showing the main terminal
        menu.
        """
        from scraping.utils.common import definedText
        full = capture_full(self.layout.width, self.layout.height, self.layout.monitor)
        terminal_roi = self.layout.terminal
        crop = full[
            int(terminal_roi.y) : int(terminal_roi.y + terminal_roi.h),
            int(terminal_roi.x) : int(terminal_roi.x + terminal_roi.w),
        ]
        text = _nav_ocr(crop).lower()
        from difflib import get_close_matches
        target = definedText.get('PrefabTextItem_1547656443_Text', 'terminal')
        return bool(get_close_matches(text, [target]))

    # ── Internal helpers ─────────────────────────────────────────────────

    def _page_count_roi(self):
        """Return the page-count ROI for the current tab, or None."""
        tab = self._current_tab
        if tab is None:
            return None
        tab_coords = getattr(self.layout, tab.value, None)
        if tab_coords is None:
            return None
        return getattr(tab_coords, 'page', None)
