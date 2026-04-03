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
    nav.set_sort_order(SortOrder.TIME_ADDED)
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
    Sort orders available in the echo inventory dropdown (top → bottom).

    The values are the 0-based position in the dropdown list as it opens
    upward from the sort button.
    """
    LEVEL           = 0   # Sort by Level
    RARITY          = 1   # Sort by Rarity
    TIME_ADDED      = 2   # Sort by Time Added  (most recently acquired first)
    TUNING_STATUS   = 3   # Sort by Tuning Status
    DISCARDED_FIRST = 4   # Sort Discarded First


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

        Clicks the sort-button to open the upward-opening dropdown, then
        clicks the desired option.  Sort state is tracked so repeated calls
        for the same order are skipped.

        The dropdown opens *above* the trigger button.  Coordinates are
        stored in ``game_roi.COORDINATES`` under ``echoes.sort``.  Only
        the options visible in the default dropdown (indices 0-3) are
        directly reachable; higher indices raise ``ValueError``.
        """
        if self._current_sort == order:
            logger.debug('Sort order already %s', order.name)
            return

        sort_coords = getattr(getattr(self.layout, 'echoes', None), 'sort', None)
        if sort_coords is None:
            logger.warning(
                'No sort coordinates in layout for %dx%d — skipping sort change',
                self.layout.width, self.layout.height,
            )
            return

        sort_items = getattr(sort_coords, 'items', [])
        if order.value >= len(sort_items):
            raise ValueError(
                f'SortOrder {order.name!r} (index {order.value}) is outside the '
                f'{len(sort_items)}-item dropdown visible in the current layout.'
            )

        # Open the dropdown
        btn = sort_coords.button
        self.ctrl.click(btn.x, btn.y, wait=0.3)

        # Click the target option (dropdown opens upward: index 0 = topmost)
        item = sort_items[order.value]
        self.ctrl.click(item.x, item.y, wait=wait)

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

        full = capture_full(self.layout.width, self.layout.height, self.layout.monitor, gw=self.gw)
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

    def scroll_to_page(self, target_page: int, current_page: int, wait: float | None = None) -> None:
        """
        Scroll from *current_page* to *target_page* (0-based).

        Scrolls one page at a time.  Direction is determined automatically.
        """
        if target_page == current_page:
            return
        direction = 1 if target_page > current_page else -1
        kw = {} if wait is None else {'wait': wait}
        for _ in range(abs(target_page - current_page)):
            self.scroll_page(direction, **kw)
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

    # ── Echo filter: sonata ──────────────────────────────────────────────

    def set_sonata_filter(self, sonata_slug: str | None) -> None:
        """
        Apply a sonata filter to the echoes list.

        Parameters
        ----------
        sonata_slug:
            Slug key from ``sonataName.json`` (e.g. ``'chromaticfoam'``),
            or ``None`` / ``'off'`` to clear the filter ("Filter On/Off").

        The method:
        1. Opens the filter submenu.
        2. Reads the current filter via OCR; returns early if already set.
        3. Opens the dropdown, scrolls to the target entry, verifies with
           OCR, clicks it, and closes the submenu.

        Raises :class:`ValueError` for unknown slugs and :class:`RuntimeError`
        when OCR verification fails.
        """
        import json as _json
        import pathlib as _pathlib
        from difflib import get_close_matches

        # ── Resolve the target dropdown position ────────────────────────
        sonata_dict = _json.loads(
            (_pathlib.Path('data') / 'en' / 'sonataName.json')
            .read_text(encoding='utf-8')
        )

        want_off = sonata_slug is None or sonata_slug == 'off'
        if not want_off and sonata_slug not in sonata_dict:
            valid = ', '.join(sorted(sonata_dict))
            raise ValueError(
                f'Unknown sonata slug {sonata_slug!r}.  Valid: {valid}'
            )

        # Dropdown order: position 0 = "Filter On/Off",
        # then sonatas sorted by descending ID.
        sorted_slugs = [
            slug for slug, _ in
            sorted(sonata_dict.items(), key=lambda kv: kv[1], reverse=True)
        ]
        if want_off:
            target_pos = 0
        else:
            target_pos = 1 + sorted_slugs.index(sonata_slug)

        # ── Layout coords ───────────────────────────────────────────────
        flt = self.layout.echoes.filter
        sonata_flt = flt.sonata
        item_names = sonata_flt.item_names      # list of Coordinates ROIs
        scroll_delta = sonata_flt.scroll.y       # per-step scroll amount
        visible = len(item_names)                # typically 5

        def _ocr_roi(roi) -> str:
            """Capture a single ROI and return lowercased OCR text."""
            img = capture_region(self.gw, roi)
            return _nav_ocr(img).strip()

        def _slug_matches(ocr_text: str, slug: str | None) -> bool:
            """Check whether *ocr_text* matches *slug* (or 'off')."""
            normalised = ocr_text.lower().replace(' ', '')
            if slug is None or slug == 'off':
                # "Filter On/Off" → "filteron/off" or similar
                return 'filter' in normalised or 'on/off' in normalised
            return slug in normalised or bool(
                get_close_matches(normalised, [slug], n=1, cutoff=0.75)
            )

        # ── 1. Open filter submenu ──────────────────────────────────────
        self.ctrl.click(flt.button.x, flt.button.y, wait=0.5)

        # ── 2. Read current active filter ───────────────────────────────
        current_text = _ocr_roi(sonata_flt.dropdown)
        target_slug = None if want_off else sonata_slug
        if _slug_matches(current_text, target_slug):
            logger.info(
                'Sonata filter already set to %s (OCR: %r)',
                sonata_slug, current_text,
            )
            self.ctrl.press_key('esc', wait=0.3)
            return

        # ── 3. Open the dropdown ────────────────────────────────────────
        dd = sonata_flt.dropdown
        self.ctrl.click(dd.x + dd.w // 2, dd.y + dd.h // 2, wait=0.4)

        # ── 4. Scroll to bring target into view ─────────────────────────
        # Without scrolling positions 0..(visible-1) are shown.
        scrolls_needed = max(0, target_pos - (visible - 1))
        # Move cursor over the dropdown area so scroll events hit it.
        first_roi = item_names[0]
        self.ctrl.move(
            first_roi.x + first_roi.w // 2,
            first_roi.y + first_roi.h // 2,
            wait=0.05,
        )
        for _ in range(scrolls_needed):
            self.ctrl.scroll(scroll_delta, wait=0.15)
        # After scrolling, the visible window starts at ``scrolls_needed``.
        # The target should be at slot index ``target_pos - scrolls_needed``.
        expected_slot = target_pos - scrolls_needed

        # ── 5. OCR-verify and click ─────────────────────────────────────
        # Try the expected slot first, then scan all visible slots.
        matched_slot = None
        for attempt_slot in [expected_slot] + [
            i for i in range(visible) if i != expected_slot
        ]:
            if attempt_slot < 0 or attempt_slot >= visible:
                continue
            text = _ocr_roi(item_names[attempt_slot])
            if _slug_matches(text, target_slug):
                matched_slot = attempt_slot
                break

        if matched_slot is None:
            self.ctrl.press_key('esc', wait=0.2)
            self.ctrl.press_key('esc', wait=0.2)
            raise RuntimeError(
                f'Could not find {sonata_slug!r} in visible dropdown slots '
                f'after {scrolls_needed} scrolls.  '
                f'Last OCR texts: (expected slot {expected_slot})'
            )

        roi = item_names[matched_slot]
        self.ctrl.click(roi.x + roi.w // 2, roi.y + roi.h // 2, wait=0.3)
        logger.info('Sonata filter set to %s (slot %d)', sonata_slug, matched_slot)

        # ── 6. Close the filter submenu ─────────────────────────────────
        self.ctrl.press_key('esc', wait=0.3)

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
        from wuwa_inventory_kamera.scraping.data import definedText
        full = capture_full(self.layout.width, self.layout.height, self.layout.monitor, gw=self.gw)
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
