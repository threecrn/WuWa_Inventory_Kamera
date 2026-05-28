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

    from .input_controller import InputController
    from .screen import GameWindow
    from .navigation import GameNavigator

    gw   = GameWindow()
    ctrl = InputController(gw.monitor_index)
    nav  = GameNavigator(ctrl, gw)

    nav.open_inventory()
    nav.switch_tab('echoes')
    nav.set_sort_order(SortOrder.TIME_ADDED)
"""
from __future__ import annotations

import enum
import functools
import logging
from pathlib import Path
import string
import time

import numpy as np

from .. import localization_data as _localization_data
from ..config.app_config import app_config, basePATH
from .input_controller import InputController
from .screen import (
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
    Sort orders available in inventory dropdowns.

    For the **echoes** tab the values equal the 0-based dropdown index.
    For other tabs (weapons, …) a separate per-tab index table is used by
    :meth:`GameNavigator.set_sort_order`.
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


@functools.lru_cache(maxsize=1)
def _nav_cpu_backend():
    """Create and cache a CPU-only OCR backend for navigation reads."""
    from ..scraping.ocr import get_backend

    return get_backend(
        'rapidocr',
        onnx_providers=['CPUExecutionProvider'],
        fallback_text_score=None,
    )


def _nav_ocr(image: np.ndarray, allowed: str | None = None) -> str:
    """
    Quick inline OCR used only for navigation control (page counts, etc.).

    This does NOT use the batched OcrService — it routes through a cached
    CPU-only backend so navigation reads do not allocate DirectML VRAM.
    """
    from ..scraping.ocr import imageToString

    return imageToString(
        image,
        allowedChars=allowed,
        backend=_nav_cpu_backend(),
    )


def _load_json_file(path: Path) -> object | None:
    return _localization_data.load_json_file(path)


def _resolve_game_language_code() -> str:
    return _localization_data.resolve_game_language_code(
        base_path=basePATH,
        selected_language=getattr(app_config, 'gameLanguage', 'English'),
    )


def _load_sonata_catalog() -> dict[str, int]:
    return _localization_data.load_sonata_id_map(data_root=basePATH / 'data')


def _load_sonata_locale(language_code: str) -> dict[str, dict]:
    return _localization_data.load_generated_locale(
        'sonatas.json',
        language_code,
        base_path=basePATH,
    )


def _sonata_text_candidates(sonata_slug: str, *, locale_data: dict[str, dict]) -> tuple[str, ...]:
    candidates: list[str] = [sonata_slug]
    record = locale_data.get(sonata_slug)
    if isinstance(record, dict):
        for value in (record.get('display_name'), record.get('normalized')):
            if isinstance(value, str) and value and value not in candidates:
                candidates.append(value)
        aliases = record.get('aliases')
        if isinstance(aliases, list):
            for alias in aliases:
                if isinstance(alias, str) and alias and alias not in candidates:
                    candidates.append(alias)
    return tuple(candidates)


def _normalize_sonata_text(text: str) -> str:
    return text.casefold().replace(' ', '')


def _sonata_text_matches(
    ocr_text: str,
    sonata_slug: str | None,
    *,
    locale_data: dict[str, dict],
) -> bool:
    from difflib import get_close_matches

    normalized = _normalize_sonata_text(ocr_text)
    if sonata_slug is None or sonata_slug == 'off':
        return 'filter' in normalized or 'on/off' in normalized

    for candidate in _sonata_text_candidates(sonata_slug, locale_data=locale_data):
        normalized_candidate = _normalize_sonata_text(candidate)
        if not normalized_candidate:
            continue
        if normalized_candidate in normalized:
            return True
        if get_close_matches(normalized, [normalized_candidate], n=1, cutoff=0.75):
            return True

    return False


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

    # Weapons sort dropdown: Rarity (0) → Level (1) → Amount (2).
    # This differs from the echoes dropdown where LEVEL=0, RARITY=1.
    _WEAPON_SORT_INDICES: dict = {
        SortOrder.RARITY: 0,
        SortOrder.LEVEL:  1,
    }

    def set_sort_order(self, order: SortOrder, wait: float = 0.3) -> None:
        """
        Activate a specific sort order in the current inventory tab.

        Clicks the sort-button to open the upward-opening dropdown, then
        clicks the desired option.  Sort state is tracked so repeated calls
        for the same order are skipped.

        For the **echoes** tab the ``SortOrder`` value is used directly as
        the dropdown index.  For the **weapons** tab a separate mapping is
        applied because the weapon dropdown has a different order.
        """
        if self._current_sort == order:
            logger.debug('Sort order already %s', order.name)
            return

        # Resolve coordinates: prefer the current tab's own sort block,
        # fall back to echoes.sort for tabs that share that dropdown.
        tab_layout = getattr(self.layout, self._current_tab.value, None) if self._current_tab else None
        sort_coords = getattr(tab_layout, 'sort', None)
        if sort_coords is None:
            sort_coords = getattr(getattr(self.layout, 'echoes', None), 'sort', None)
        if sort_coords is None:
            logger.warning(
                'No sort coordinates in layout for %dx%d — skipping sort change',
                self.layout.width, self.layout.height,
            )
            return

        # Resolve the dropdown index for this tab.
        if self._current_tab == InventoryTab.WEAPONS:
            idx = self._WEAPON_SORT_INDICES.get(order)
            if idx is None:
                raise ValueError(
                    f'SortOrder {order.name!r} is not available for the weapons tab. '
                    f'Supported: {list(self._WEAPON_SORT_INDICES)}'
                )
        else:
            idx = order.value

        sort_items = getattr(sort_coords, 'items', [])
        if idx >= len(sort_items):
            raise ValueError(
                f'SortOrder {order.name!r} (index {idx}) is outside the '
                f'{len(sort_items)}-item dropdown visible in the current layout.'
            )

        # Open the dropdown
        btn = sort_coords.button
        self.ctrl.click(btn.x, btn.y, wait=0.3)

        # Click the target option (dropdown opens upward: index 0 = topmost)
        item = sort_items[idx]
        self.ctrl.click(item.x, item.y, wait=wait)

        self._current_sort = order
        logger.info('Sort order set to %s (dropdown index %d)', order.name, idx)

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
        logger.debug(
            'read_item_count: tab=%s roi=(%d,%d,%d,%d) ocr=%r',
            self._current_tab.value if self._current_tab else '?',
            int(page_roi.x), int(page_roi.y), int(page_roi.w), int(page_roi.h),
            raw,
        )
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

        # devItems and resources share the same grid layout as the generic
        # 'items' coordinate block.
        layout_key = tab.value if tab not in (InventoryTab.DEV_ITEMS, InventoryTab.RESOURCES) else 'items'
        tab_coords = getattr(self.layout, layout_key)
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

    def set_sonata_filter(self, sonata_slug: str | None) -> int | None:
        """
        Apply a sonata filter to the echoes list.

        Parameters
        ----------
        sonata_slug:
            Canonical sonata slug (e.g. ``'chromaticfoam'``),
            or ``None`` / ``'off'`` to clear the filter ("Filter On/Off").

        Returns
        -------
        int | None
            The number of echoes matching the selected filter as shown in
            the dropdown, or ``None`` if the count could not be read (e.g.
            when the filter was already active and we returned early).

        The method:
        1. Opens the filter submenu.
        2. Reads the current filter via OCR; returns early if already set.
        3. Opens the dropdown, scrolls to the target entry, reads the
           echo count from the adjacent amount ROI, verifies with OCR,
           clicks it, and closes the submenu.

        Raises :class:`ValueError` for unknown slugs and :class:`RuntimeError`
        when OCR verification fails.
        """
        # ── Resolve the target dropdown position ────────────────────────
        language_code = _resolve_game_language_code()
        sonata_dict = _load_sonata_catalog()
        sonata_locale = _load_sonata_locale(language_code)

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
        item_names = sonata_flt.item_names       # list of Coordinates ROIs
        item_amounts = sonata_flt.item_amounts   # list of Coordinates ROIs for counts
        scroll_delta = sonata_flt.scroll.y       # per-step scroll amount (negative = down)
        visible = len(item_names)                # typically 5

        def _ocr_roi(roi) -> str:
            """Capture a single ROI and return stripped OCR text."""
            return _nav_ocr(capture_region(self.gw, roi)).strip()

        def _slug_matches(ocr_text: str, slug: str | None) -> bool:
            """Check whether *ocr_text* matches *slug* (or the Filter On/Off entry)."""
            return _sonata_text_matches(ocr_text, slug, locale_data=sonata_locale)

        def _find_slug_in_text(text: str) -> int | None:
            """Return index *j* in *sorted_slugs* if *text* matches it, else None."""
            for j, slug in enumerate(sorted_slugs):
                if _slug_matches(text, slug):
                    return j
            return None

        def _shifted_roi_y(roi, delta_y: int | float):
            """Return a new ROI with ``y`` shifted by ``delta_y``."""
            return roi.__class__(roi.x, roi.y + delta_y, roi.w, roi.h)

        # ── 1. Open filter submenu ──────────────────────────────────────
        self.ctrl.click(flt.button.x, flt.button.y, wait=0.5)

        # ── 2. Read current active filter ───────────────────────────────
        target_slug = None if want_off else sonata_slug
        if _slug_matches(_ocr_roi(sonata_flt.dropdown), target_slug):
            logger.info('Sonata filter already set to %s', sonata_slug)
            self.ctrl.press_key('esc', wait=0.3)
            return None

        # ── 3. Open the dropdown ────────────────────────────────────────
        dd = sonata_flt.dropdown
        self.ctrl.click(dd.x + dd.w // 2, dd.y + dd.h // 2, wait=0.4)

        # ── 4. Probe current scroll offset ──────────────────────────────
        # The dropdown forgets its previous scroll position after the submenu is closed.
        # We just opened the submenu, so the dropdown should be at the top.
        # However, first we need to take care of scrolling weirdness where the top position is "overscrolled"
        # and scrolling down 1 position actually ends up at position 0.5 inbetween positions 0 and 1.

        scroll_offset = 0 # Assume top

        # Hover over position 1
        probe_roi = item_names[1] #if visible > 1 else item_names[0]
        self.ctrl.move(
            probe_roi.x + probe_roi.w // 2,
            probe_roi.y + probe_roi.h // 2,
            wait=0.05,
        )
        logger.debug(f'moved mouse to probe ROI for position 1: {probe_roi}')
        self.ctrl.scroll(scroll_delta * -2, wait=0.1) # Scroll down 2 positions ends up perfectly with position 1 in the slot 0
        logger.debug(f'Scrolled down 2 positions to probe position 1')
        scroll_offset = 1 #
        #self.ctrl.scroll(scroll_delta * 1, wait=1.0) # Scroll back up 1 position: now at the real top without the weird overscroll
        #logger.info(f'Scrolled back up 1 position to correct overscroll')

        # ── 5. Scroll from current offset to target offset ──────────────
        # Show the target at slot 1 (one step from the top of the visible
        # window) for a reliable click.  Clamp to the valid scroll range.
        total_items = 1 + len(sorted_slugs)
        max_scroll_offset = total_items - visible - 2

        # not at the very bottom of the list?
        if target_pos < total_items - 1:
            desired_offset = max(0, min(target_pos - 1, max_scroll_offset))
            steps = desired_offset - scroll_offset
            logger.debug(f'Target sonata {sonata_slug!r} at position {target_pos}, currently at offset {scroll_offset}, scrolling to offset {desired_offset} ({steps} steps) with {max_scroll_offset=}')

            # scroll all in one go
            self.ctrl.scroll(scroll_delta * -steps, wait=0.3)

            # ── 6. OCR-verify and click ─────────────────────────────────────
            # expected_slot is 1 for most targets; 0 for "Filter On/Off";
            # and up to (visible-1) for entries near the very end of the list.
            expected_slot = target_pos - desired_offset
            matched_slot = None
            for attempt_slot in [expected_slot] + [
                i for i in range(visible) if i != expected_slot
            ]:
                if not 0 <= attempt_slot < visible:
                    continue
                if _slug_matches(_ocr_roi(item_names[attempt_slot]), target_slug):
                    matched_slot = attempt_slot
                    break

            if matched_slot is None:
                self.ctrl.press_key('esc', wait=0.2)
                self.ctrl.press_key('esc', wait=0.2)
                raise RuntimeError(
                    f'Could not find {sonata_slug!r} in visible dropdown slots '
                    f'after scrolling to offset {desired_offset} '
                    f'(probed offset was {scroll_offset}).'
                )

            # Read the echo count from the adjacent amount cell before clicking.
            count = self._ocr_sonata_amount(item_amounts[matched_slot])

            roi = item_names[matched_slot]
            self.ctrl.click(roi.x + roi.w // 2, roi.y + roi.h // 2, wait=0.3)
            logger.info('Sonata filter set to %s (slot %d, count=%s)', sonata_slug, matched_slot, count)
        
        # last element in the list: things get rough around here
        else:
            logger.debug(f'Target sonata {sonata_slug!r} at position {target_pos} is outside the visible dropdown range (max visible index {visible-1})')
            desired_offset = max(0, min(target_pos - 1, max_scroll_offset+3))
            steps = desired_offset - scroll_offset
            logger.debug(f'Target sonata {sonata_slug!r} at position {target_pos}, currently at offset {scroll_offset}, scrolling to offset {desired_offset} ({steps} steps) with {max_scroll_offset=}')

            # scroll all in one go
            self.ctrl.scroll(scroll_delta * -steps, wait=0.5) # wait a bit more for the dropdown to stabilize when scrolling near the end of the list

            # ── 6. OCR-verify and click ─────────────────────────────────────
            # expected_slot is 1 for most targets; 0 for "Filter On/Off";
            # and up to (visible-1) for entries near the very end of the list.
            expected_slot = len(item_names) - 1 
            matched_slot = None
            attempt_slot = expected_slot

            # Near the end of the list the item name positions shift down by a few pixels.
            adjusted_roi = _shifted_roi_y(
                item_names[attempt_slot],
                sonata_flt.bottom_offset_item_names.y,
            )

            logging.info(f'Adjusted ROI for OCR verification: {adjusted_roi=}')
            if _slug_matches(_ocr_roi(adjusted_roi), target_slug):
                matched_slot = attempt_slot
                logging.debug(f'Matched target sonata {sonata_slug!r} at adjusted slot {attempt_slot} (expected {expected_slot})')

            if matched_slot is None:
                self.ctrl.press_key('esc', wait=0.2)
                self.ctrl.press_key('esc', wait=0.2)
                raise RuntimeError(
                    f'Could not find {sonata_slug!r} in visible dropdown slots '
                    f'after scrolling to offset {desired_offset} '
                    f'(probed offset was {scroll_offset}).'
                )

            # Read the echo count from the shifted amount cell before clicking.
            count = self._ocr_sonata_amount(_shifted_roi_y(
                item_amounts[matched_slot],
                sonata_flt.bottom_offset_item_names.y,
            ))

            roi = _shifted_roi_y(
                item_names[matched_slot],
                sonata_flt.bottom_offset_item_names.y,
            )
            
            self.ctrl.click(roi.x + roi.w // 2, roi.y + roi.h // 2, wait=0.3)
            logger.info(f'Sonata filter set to %s (slot %d, count=%s) adjust roi {roi=}', sonata_slug, matched_slot, count)

        # ── 7. Close the filter submenu ─────────────────────────────────
        self.ctrl.press_key('esc', wait=0.3)
        return count

    # ── Character-specific navigation ────────────────────────────────────

    def scroll_character_list(self, amount: float | None = None, wait: float = 0.5) -> None:
        """Scroll the character sidebar list."""
        self.ctrl.move(
            self.layout.characters.rightSide.x,
            self.layout.characters.rightSide.y,
            wait=0.3,
        )
        scroll_amount: float = amount if amount is not None else self.layout.scroll.characters.y
        self.ctrl.scroll(scroll_amount, wait=wait)

    # ── Menu detection ───────────────────────────────────────────────────

    def is_in_main_menu(self) -> bool:
        """
        Quick OCR check to see if the game is showing the main terminal
        menu.
        """
        from ..scraping.data import getDefinedText

        full = capture_full(self.layout.width, self.layout.height, self.layout.monitor, gw=self.gw)
        terminal_roi = self.layout.terminal
        crop = full[
            int(terminal_roi.y) : int(terminal_roi.y + terminal_roi.h),
            int(terminal_roi.x) : int(terminal_roi.x + terminal_roi.w),
        ]
        text = _nav_ocr(crop).lower()
        from difflib import get_close_matches
        target = getDefinedText().get('PrefabTextItem_1547656443_Text', 'terminal')
        return bool(get_close_matches(text, [target]))

    # ── Internal helpers ─────────────────────────────────────────────────

    def _ocr_sonata_amount(self, roi) -> int | None:
        """OCR an ``item_amounts`` cell and return the integer count, or None."""
        raw = _nav_ocr(capture_region(self.gw, roi), allowed=string.digits).strip()
        try:
            return int(raw)
        except ValueError:
            logger.warning('Could not parse sonata amount from %r', raw)
            return None

    def _page_count_roi(self):
        """Return the page-count ROI for the current tab, or None."""
        tab = self._current_tab
        if tab is None:
            return None
        # devItems/resources share the weapons page-count ROI (same panel location).
        layout_key = tab.value if tab not in (InventoryTab.DEV_ITEMS, InventoryTab.RESOURCES) else 'weapons'
        tab_coords = getattr(self.layout, layout_key, None)
        if tab_coords is None:
            return None
        return getattr(tab_coords, 'page', None)
