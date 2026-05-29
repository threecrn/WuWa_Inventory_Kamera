"""
wuwa_inventory_kamera.scraping.scanning.item_workflow
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Scanning workflow for Development Items and Resources inventory tabs.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

import numpy as np

from ...game.navigation import GameNavigator, InventoryTab, SortOrder
from ...game.screen import capture_full
from .grid_navigator import GridNavigator
from .scan_state import GridPosition, ScanSession
from ..service.captures import WeaponCapture, WeaponResult
from ..service.ocr_service import OcrService
from ..service.shared_scan_helpers import _rarity_from_capture_pixel

logger = logging.getLogger(__name__)


class ItemWorkflow:
    """Scanning workflow for the Development Items and Resources tabs."""

    def __init__(
        self,
        nav: GameNavigator,
        ocr_service: OcrService,
        session: ScanSession,
        tab: InventoryTab = InventoryTab.DEV_ITEMS,
        sort_order: SortOrder | None = None,
        save_raw: Path | None = None,
        stop_event: threading.Event | None = None,
        write_debug: bool = False,
    ) -> None:
        if tab not in (InventoryTab.DEV_ITEMS, InventoryTab.RESOURCES):
            raise ValueError(f'ItemWorkflow only supports item tabs, got {tab!r}')

        self.nav = nav
        self.ocr = ocr_service
        self.session = session
        self.tab = tab
        self.sort_order = sort_order
        self.save_raw = save_raw
        self._stop_event = stop_event
        self.write_debug = write_debug

    def run(self, on_progress: Callable | None = None) -> list[dict]:
        """Execute the item scan and return accepted result dicts."""
        self.nav.switch_tab(self.tab)

        total_items, total_pages = self.nav.read_item_count()
        logger.info(
            'Item workflow — tab=%s items=%d pages=%d',
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
            full = capture_full(layout.width, layout.height, layout.monitor, gw=self.nav.gw)

            if self.save_raw:
                self._save_raw(position, full)

            img_hash = hash(full.tobytes())
            if img_hash in seen_hashes:
                self.session.mark_skipped(position.scan_index)
                logger.debug('Item %d — duplicate image, skipping', position.scan_index)
                processed += 1
                if on_progress:
                    on_progress(processed, self.session.total_items)
                return True
            seen_hashes.add(img_hash)

            panel = layout.items
            name_crop = full[
                int(panel.name.y) : int(panel.name.y + panel.name.h),
                int(panel.name.x) : int(panel.name.x + panel.name.w),
            ]
            value_crop = full[
                int(panel.value.y) : int(panel.value.y + panel.value.h),
                int(panel.value.x) : int(panel.value.x + panel.value.w),
            ]

            detected_rarity: int | None = None
            if hasattr(panel, 'rarityColorPick'):
                rcp = panel.rarityColorPick
                rarity_pixel = full[int(rcp.y), int(rcp.x)]
                detected_rarity, rarity_order, rarity_dist = _rarity_from_capture_pixel(rarity_pixel)
                logger.debug(
                    'Item %d — rarity pixel raw=%s interpreted_as=%s → rarity %d (dist=%.1f)',
                    position.scan_index,
                    rarity_pixel.tolist(),
                    rarity_order,
                    detected_rarity,
                    rarity_dist,
                )

            if self.write_debug:
                self._write_debug_artifacts(
                    position,
                    name=name_crop,
                    value=value_crop,
                    detected_rarity=detected_rarity,
                )

            capture = WeaponCapture(
                index=position.scan_index,
                name=name_crop,
                value=value_crop,
                rank=None,
                equipped=None,
                detected_rarity=detected_rarity,
            )

            try:
                result: WeaponResult = self.ocr.submit(capture).result(timeout=30)
            except Exception as exc:
                logger.error('Item %d — OCR error: %s', position.scan_index, exc)
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
                logger.debug('Item %d — rejected', position.scan_index)

            processed += 1
            if on_progress:
                on_progress(processed, self.session.total_items)
            return True

        grid.scan_forward(_visitor)

        logger.info('Item workflow finished — %d/%d accepted', len(results), total_items)
        return results

    def _debug_base(self) -> Path:
        if self.save_raw is not None:
            return self.save_raw
        from ...config.app_config import app_config

        return Path(app_config.exportFolder) / self.session.session_id / 'raw'

    def _item_prefix(self) -> str:
        return 'devItem' if self.tab == InventoryTab.DEV_ITEMS else 'resource'

    def _scan_dir_for_scan(self, pos: GridPosition) -> Path:
        return self._debug_base() / f'{self._item_prefix()}_{pos.scan_index:04d}'

    def _write_debug_artifacts(
        self,
        pos: GridPosition,
        *,
        name: np.ndarray,
        value: np.ndarray,
        detected_rarity: int | None,
    ) -> None:
        from ..service.shared_scan_helpers import _write_region_debug_artifacts

        debug_dir = self._scan_dir_for_scan(pos) / 'debug'
        _write_region_debug_artifacts(
            debug_dir,
            basename='name',
            roi_key='items.name',
            raw_bgr=name,
            rarity=detected_rarity,
        )
        _write_region_debug_artifacts(
            debug_dir,
            basename='value',
            roi_key='items.value',
            raw_bgr=value,
            rarity=None,
        )

    def _save_raw(
        self,
        pos: GridPosition,
        full: np.ndarray,
    ) -> None:
        import cv2
        import json

        assert self.save_raw is not None
        item_dir = self._scan_dir_for_scan(pos)
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