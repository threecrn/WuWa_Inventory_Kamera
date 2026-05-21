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
from pathlib import Path
from typing import Callable

import numpy as np

from ...game.navigation import (
    GameNavigator,
    InventoryTab,
    SortOrder,
)
from ...game.screen import capture_full
from .echo_workflow import _rarity_from_capture_pixel
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
    save_raw:
        If set, raw screenshots are saved to this directory for offline
        reprocessing.
    write_debug:
        If set, write OCR debug crops and preprocessed/signature artifacts.
    """

    def __init__(
        self,
        nav: GameNavigator,
        ocr_service: OcrService,
        session: ScanSession,
        tab: InventoryTab = InventoryTab.WEAPONS,
        sort_order: SortOrder | None = None,
        save_raw: Path | None = None,
        stop_event: threading.Event | None = None,
        write_debug: bool = False,
    ) -> None:
        self.nav = nav
        self.ocr = ocr_service
        self.session = session
        self.tab = tab
        self.sort_order = sort_order
        self.save_raw = save_raw
        self._stop_event = stop_event
        self.write_debug = write_debug

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

            # Optionally save raw images
            if self.save_raw:
                self._save_raw(position, full)

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
            detected_rarity: int | None = None
            if not is_item_tab and hasattr(wi, 'rarityColorPick'):
                rcp = wi.rarityColorPick
                rarity_pixel = full[int(rcp.y), int(rcp.x)]
                detected_rarity, rarity_order, rarity_dist = _rarity_from_capture_pixel(rarity_pixel)
                logger.debug(
                    'Weapon %d — rarity pixel raw=%s interpreted_as=%s → rarity %d (dist=%.1f)',
                    position.scan_index,
                    rarity_pixel.tolist(),
                    rarity_order,
                    detected_rarity,
                    rarity_dist,
                )
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

            equipped_crop = None
            if not is_item_tab and hasattr(wi, 'equipped'):
                equipped_crop = full[
                    int(wi.equipped.y) : int(wi.equipped.y + wi.equipped.h),
                    int(wi.equipped.x) : int(wi.equipped.x + wi.equipped.w),
                ]

            if self.write_debug:
                self._write_debug_artifacts(
                    position,
                    name=name_crop,
                    value=value_crop,
                    rank=rank_crop,
                    detected_rarity=detected_rarity,
                )

            capture = WeaponCapture(
                index=position.scan_index,
                name=name_crop,
                value=value_crop,
                rank=rank_crop,
                equipped=equipped_crop,
                detected_rarity=detected_rarity,
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

    def _debug_base(self) -> Path:
        if self.save_raw is not None:
            return self.save_raw
        from ...config.app_config import app_config

        return Path(app_config.exportFolder) / self.session.session_id / 'raw'

    def _write_debug_artifacts(
        self,
        pos: GridPosition,
        *,
        name: np.ndarray,
        value: np.ndarray,
        rank: np.ndarray | None,
        detected_rarity: int | None,
    ) -> None:
        from ..service.echo_reprocess import _write_region_debug_artifacts

        debug_dir = self._debug_base() / f'weapon_{pos.scan_index:04d}' / 'debug'
        value_basename = 'level' if rank is not None else 'value'
        value_roi_key = 'weapons.level' if rank is not None else 'weapons.value'
        _write_region_debug_artifacts(
            debug_dir,
            basename='name',
            roi_key='weapons.name',
            raw_bgr=name,
            rarity=detected_rarity if rank is not None else None,
        )
        _write_region_debug_artifacts(
            debug_dir,
            basename=value_basename,
            roi_key=value_roi_key,
            raw_bgr=value,
            rarity=None,
        )
        if rank is not None:
            _write_region_debug_artifacts(
                debug_dir,
                basename='rank',
                roi_key='weapons.rank',
                raw_bgr=rank,
                rarity=None,
            )

    # ── Raw image persistence ────────────────────────────────────────────

    def _save_raw(
        self,
        pos: GridPosition,
        full: np.ndarray,
    ) -> None:
        """Save raw screenshots to disk for offline reprocessing."""
        import json
        import cv2

        assert self.save_raw is not None
        item_dir = self.save_raw / f'weapon_{pos.scan_index:04d}'
        item_dir.mkdir(parents=True, exist_ok=True)

        cv2.imwrite(str(item_dir / 'full.png'), full)

        meta = {
            'session_id': self.session.session_id,
            'index': pos.scan_index,
            'page': pos.page,
            'row': pos.row,
            'col': pos.col,
            'screen_width': self.nav.layout.width,
            'screen_height': self.nav.layout.height,
            'monitor': self.nav.layout.monitor,
        }
        with open(item_dir / 'meta.json', 'w') as f:
            json.dump(meta, f, indent=2)
