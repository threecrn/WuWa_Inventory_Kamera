"""
wuwa_inventory_kamera.scraping.scanning.weapon_workflow
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Scanning workflow for weapons (and items, which share the same grid
layout).

Weapons are simpler than echoes:
* No sonata scroll.
* No rescan (OCR is straightforward).
* Sequential: each cell is captured and the future is resolved
  immediately because the scanner needs the result for duplicate
  detection.

The workflow reuses the same :class:`~.grid_navigator.GridNavigator` and
:class:`~.scan_state.ScanSession` infrastructure.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

from ...game.navigation import (
    GameNavigator,
    InventoryTab,
    SortOrder,
)
from ...game.screen import capture_full
from .grid_navigator import GridNavigator
from .scan_state import (
    GridPosition,
    ScanSession,
)
from ..service.captures import WeaponCapture, WeaponResult
from ..service.ocr_service import OcrService

logger = logging.getLogger(__name__)


class WeaponWorkflow:
    """
    Scanning workflow for the weapon inventory tab.

    Each cell is captured, submitted to the OcrService, and resolved
    immediately (blocking).  This keeps the logic simple and allows
    duplicate detection based on the OCR result.

    Parameters
    ----------
    nav:
        Game navigator.
    ocr_service:
        OCR service for assembling weapon data.
    session:
        Scan session tracking progress.
    tab:
        Which inventory tab to scan (``WEAPONS`` by default; pass
        ``DEV_ITEMS`` or ``RESOURCES`` for item scanning).
    sort_order:
        Desired sort order, or ``None`` to leave unchanged.
    """

    def __init__(
        self,
        nav: GameNavigator,
        ocr_service: OcrService,
        session: ScanSession,
        tab: InventoryTab = InventoryTab.WEAPONS,
        sort_order: SortOrder | None = None,
        stop_event: threading.Event | None = None,
    ) -> None:
        self.nav = nav
        self.ocr = ocr_service
        self.session = session
        self.tab = tab
        self.sort_order = sort_order
        self._stop_event = stop_event

    def run(self, on_progress: Callable | None = None) -> list[dict]:
        """
        Execute the weapon/item scan.

        Returns a list of accepted result dicts.
        """
        self.nav.switch_tab(self.tab)
        # Only sort weapons by level — DEV_ITEMS/RESOURCES have no level
        # and share a 3-option dropdown whose positions differ from the
        # echoes dropdown that set_sort_order falls back to.
        if self.sort_order is not None and self.tab == InventoryTab.WEAPONS:
            self.nav.set_sort_order(self.sort_order)

        total_items, total_pages = self.nav.read_item_count()
        logger.info(
            'Weapon workflow — tab=%s items=%d pages=%d',
            self.tab.value, total_items, total_pages,
        )

        if total_items != self.session.total_items:
            self.session = ScanSession(
                total_items=total_items,
                sort_order=self.sort_order or self.session.sort_order,
                session_id=self.session.session_id,
            )

        grid = GridNavigator(self.nav, total_items, total_pages)
        seen_hashes: set[int] = set()
        results: list[dict] = []
        processed = 0

        def _visitor(position: GridPosition) -> bool:
            nonlocal processed
            if self._stop_event and self._stop_event.is_set():
                return False
            layout = self.nav.layout

            # Full screenshot for this cell
            full = capture_full(layout.width, layout.height, layout.monitor, gw=self.nav.gw)

            # Hash-based dedup: skip if identical to a previously seen cell
            img_hash = hash(full.tobytes())
            if img_hash in seen_hashes:
                self.session.mark_skipped(position.scan_index)
                logger.debug('Weapon %d — duplicate image, skipping', position.scan_index)
                processed += 1
                if on_progress:
                    on_progress(processed, self.session.total_items)
                return True
            seen_hashes.add(img_hash)

            # Crop regions.
            # devItems/resources share the weapons detail-panel layout for
            # name and value, but use .value (item count) instead of .level.
            is_item_tab = self.tab in (InventoryTab.DEV_ITEMS, InventoryTab.RESOURCES)
            wi = layout.weapons  # weapons coords cover both weapons and item panels
            name_crop = full[
                int(wi.name.y) : int(wi.name.y + wi.name.h),
                int(wi.name.x) : int(wi.name.x + wi.name.w),
            ]
            if is_item_tab:
                value_roi = wi.value
            else:
                value_roi = wi.level
            value_crop = full[
                int(value_roi.y) : int(value_roi.y + value_roi.h),
                int(value_roi.x) : int(value_roi.x + value_roi.w),
            ]

            # Rank crop (weapons only, not items)
            rank_crop = None
            if not is_item_tab:
                rank_crop = full[
                    int(wi.rank.y) : int(wi.rank.y + wi.rank.h),
                    int(wi.rank.x) : int(wi.rank.x + wi.rank.w),
                ]

            capture = WeaponCapture(
                index=position.scan_index,
                name=name_crop,
                value=value_crop,
                rank=rank_crop,
            )

            # Submit and block — weapons are fast enough to do synchronously
            try:
                result: WeaponResult = self.ocr.submit(capture).result(timeout=30)
            except Exception as exc:
                logger.error('Weapon %d — OCR error: %s', position.scan_index, exc)
                self.session.mark_failed(position.scan_index, str(exc))
                processed += 1
                if on_progress:
                    on_progress(processed, self.session.total_items)
                return True

            if result.data is not None:
                self.session.mark_scanned(position.scan_index, result.data)
                results.append(result.data)
            else:
                self.session.mark_skipped(position.scan_index)
                logger.debug('Weapon %d — rejected', position.scan_index)

            processed += 1
            if on_progress:
                on_progress(processed, self.session.total_items)

            # Early termination: when sorted by level, the first weapon below
            # the minimum guarantees all remaining weapons are also below it.
            if result.below_minimum and self.sort_order == SortOrder.LEVEL:
                logger.info(
                    'Weapon %d — below minimum level while sorted by level, stopping scan.',
                    position.scan_index,
                )
                return False

            return True

        grid.scan_forward(_visitor)

        logger.info(
            'Weapon workflow finished — %d/%d accepted',
            len(results), total_items,
        )
        return results
