"""
wuwa_inventory_kamera.scraping.scanning.echo_workflow
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

End-to-end echo scanning workflow.

Orchestrates the :class:`~.grid_navigator.GridNavigator` and the
:class:`~...service.ocr_service.OcrService` to scan all echoes in the
game inventory, with support for:

* **Lookahead decoupling** — screenshots are taken at input-device speed
  (~200 ms/echo) while OCR runs asynchronously on the GPU.  Futures are
  collected after the full grid sweep.
* **Icon-based sonata detection** — the sonata set is identified by
  matching the small circular icon on the echo card against reference
  PNGs, eliminating the need to scroll the detail panel.
* **Rescan** — after the forward pass, futures that reported missing
  substats (or sonata scroll failures) are re-navigated and re-scanned.
  The OCR assembler flags these automatically.
* **Sort-order control** — the workflow can set a specific sort order
  before starting the scan, and restores it afterward if needed.
* **Save-to-disk (debug)** — optionally persists raw screenshots so the
  offline reprocess CLI can re-run OCR without the game.

Usage::

    from .echo_workflow import EchoWorkflow

    wf = EchoWorkflow(nav, ocr_service, session)
    results = wf.run()
"""
from __future__ import annotations

import concurrent.futures
import logging
import threading
from pathlib import Path
from typing import Callable

import numpy as np

from ...game.navigation import (
    GameNavigator,
    InventoryTab,
    SortOrder,
    CELLS_PER_PAGE,
)
from ...game.screen import capture_full
from .grid_navigator import GridNavigator
from .scan_state import (
    GridPosition,
    ScanItemStatus,
    ScanSession,
)
from ..service.captures import EchoCapture, EchoResult
from ..service.ocr_service import OcrService

logger = logging.getLogger(__name__)


class EchoWorkflow:
    """
    Complete echo scanning workflow.

    Parameters
    ----------
    nav:
        :class:`~...game.navigation.GameNavigator` for game interaction.
    ocr_service:
        :class:`~...service.ocr_service.OcrService` for async OCR.
    session:
        :class:`~.scan_state.ScanSession` tracking scan progress.
    sort_order:
        Desired sort order for the scan.  If ``None``, the current order
        is left unchanged.
    save_raw:
        If set, raw screenshots are saved to this directory for offline
        reprocessing.
    max_rescans:
        Maximum number of rescan attempts per item before giving up.
    """

    def __init__(
        self,
        nav: GameNavigator,
        ocr_service: OcrService,
        session: ScanSession,
        sort_order: SortOrder | None = None,
        save_raw: Path | None = None,
        max_rescans: int = 2,
        stop_event: threading.Event | None = None,
        min_level: int = 0,
    ) -> None:
        self.nav = nav
        self.ocr = ocr_service
        self.session = session
        self.sort_order = sort_order
        self.save_raw = save_raw
        self.max_rescans = max_rescans
        self._stop_event = stop_event
        self.min_level = min_level

    # ── Public entry point ───────────────────────────────────────────────

    def run(
        self,
        on_progress: Callable | None = None,
        on_process_progress: Callable | None = None,
    ) -> list[dict]:
        """
        Execute the full echo scan workflow.

        1. Open inventory → echoes tab.
        2. Optionally set the sort order.
        3. Read the echo count.
        4. Forward-scan all grid cells, submitting OCR futures.
        5. Collect futures and check for rescan requests.
        6. Execute rescan passes (up to ``max_rescans`` per item).
        7. Return final results.

        Parameters
        ----------
        on_progress:
            Optional callback ``(scanned: int, total: int) -> None``
            invoked after each cell is photographed (scan phase).
        on_process_progress:
            Optional callback ``(processed: int, total: int) -> None``
            invoked after each OCR future resolves (process phase).

        Returns
        -------
        list[dict]
            Accepted echo dicts in scan order.
        """
        # 1. Navigate to echoes tab
        self.nav.switch_tab(InventoryTab.ECHOES)

        # 2. Sort order
        if self.sort_order is not None:
            self.nav.set_sort_order(self.sort_order)

        # 3. Read count
        total_items, total_pages = self.nav.read_item_count()
        logger.info(
            'Echo workflow — session=%s items=%d pages=%d sort=%s',
            self.session.session_id, total_items, total_pages,
            self.sort_order.name if self.sort_order else 'unchanged',
        )

        # Update session if the game reports a different count
        if total_items != self.session.total_items:
            logger.warning(
                'Session total_items=%d but game reports %d — using game value',
                self.session.total_items, total_items,
            )
            self.session = ScanSession(
                total_items=total_items,
                sort_order=self.sort_order or self.session.sort_order,
                session_id=self.session.session_id,
            )

        grid = GridNavigator(self.nav, total_items, total_pages)

        # 4. Forward scan
        futures: list[tuple[int, concurrent.futures.Future]] = []

        # Set up a thread-safe counter so on_process_progress fires as each
        # OCR future completes (concurrently with the scan, not after).
        # _scan_total is mutable so callbacks always report the correct
        # denominator even when the scan stops early.
        _processed_count = [0]
        _processed_lock = threading.Lock()
        _scan_total = [total_items]  # updated after scan_forward if cut short

        def _process_done_callback(_fut: concurrent.futures.Future) -> None:
            with _processed_lock:
                _processed_count[0] += 1
                count = _processed_count[0]
            if on_process_progress:
                on_process_progress(count, _scan_total[0])

        def _forward_visitor(position: GridPosition) -> bool:
            if self._stop_event and self._stop_event.is_set():
                return False
            future = self._capture_echo(position)
            future.add_done_callback(_process_done_callback)
            futures.append((position.scan_index, future))
            if on_progress:
                on_progress(len(futures), total_items)

            # At the end of each full page, do a synchronous level check
            # on the last captured echo.  Since echoes are sorted by level
            # (descending), a level below the threshold means all remaining
            # echoes can be skipped.
            if (
                self.min_level > 0
                and (position.scan_index + 1) % CELLS_PER_PAGE == 0
            ):
                try:
                    lvl_result = future.result(timeout=10.0)
                    if lvl_result.detected_level < self.min_level:
                        logger.info(
                            'Level-based early stop at index %d: '
                            'detected_level=%d < min_level=%d',
                            position.scan_index,
                            lvl_result.detected_level,
                            self.min_level,
                        )
                        return False
                except concurrent.futures.TimeoutError:
                    logger.warning(
                        'Level check timed out at index %d — continuing scan',
                        position.scan_index,
                    )
                except Exception:
                    logger.exception('Level check error at index %d', position.scan_index)

            return True

        grid.scan_forward(_forward_visitor)

        # If the scan was cut short (level threshold or stop event), update
        # the mutable total so subsequent OCR-done callbacks report the
        # correct denominator, and emit corrective progress signals.
        actual_scanned = len(futures)
        if actual_scanned < total_items:
            _scan_total[0] = actual_scanned
            # Scan bar: show completed (n/n = 100 %)
            if on_progress:
                on_progress(actual_scanned, actual_scanned)
            # Processing bar: correct the total using already-resolved count
            if on_process_progress:
                with _processed_lock:
                    already_processed = _processed_count[0]
                on_process_progress(already_processed, actual_scanned)

        # 5. Collect results
        self._collect_results(futures)

        # 6. Rescan pass(es) — skip if cancelled
        rescan_pass = 0
        while (
            self.session.rescan_pending > 0
            and rescan_pass < self.max_rescans
            and not (self._stop_event and self._stop_event.is_set())
        ):
            rescan_pass += 1
            logger.info(
                'Echo rescan pass %d — %d item(s) queued',
                rescan_pass, self.session.rescan_pending,
            )
            self._run_rescan_pass(grid, on_progress)

        # 7. Collect accepted results
        return self.session.results()

    # ── Core capture logic ───────────────────────────────────────────────

    def _capture_echo(self, pos: GridPosition) -> concurrent.futures.Future:
        """
        Capture screenshots for one echo and submit to the OcrService.

        The echo cell must already be selected (clicked) before this is
        called.

        Steps:
          1. Full-screen capture (stats panel visible).
          2. Crop the sonata icon, card, and stats from the screenshot.
          3. Submit an :class:`EchoCapture` to the OcrService.

        Returns the Future[EchoResult].
        """
        layout = self.nav.layout

        # Full screenshot (no scrolling needed — the sonata icon is visible
        # on the un-scrolled echo detail panel).
        full = capture_full(layout.width, layout.height, layout.monitor, gw=self.nav.gw)

        # Crop regions from the full screenshot
        ei = layout.echoes
        card = full[
            int(ei.echoCard.y) : int(ei.echoCard.y + ei.echoCard.h),
            int(ei.echoCard.x) : int(ei.echoCard.x + ei.echoCard.w),
        ]
        stats_name = full[
            int(ei.fullStatsName.y) : int(ei.fullStatsName.y + ei.fullStatsName.h),
            int(ei.fullStatsName.x) : int(ei.fullStatsName.x + ei.fullStatsName.w),
        ]
        stats_value = full[
            int(ei.fullStatsValue.y) : int(ei.fullStatsValue.y + ei.fullStatsValue.h),
            int(ei.fullStatsValue.x) : int(ei.fullStatsValue.x + ei.fullStatsValue.w),
        ]
        # Small circular sonata icon from the echo card area
        si = ei.sonataIcon
        sonata_icon = full[
            int(si.y) : int(si.y + si.h),
            int(si.x) : int(si.x + si.w),
        ]

        # Optionally save raw images
        if self.save_raw:
            self._save_raw(pos, full)

        capture = EchoCapture(
            echo_index=pos.scan_index,
            card=card,
            sonata_icon=sonata_icon,
            stats_name=stats_name,
            stats_value=stats_value,
            full_screenshot=full if self.save_raw else None,
        )

        return self.ocr.submit(capture)

    # ── Result collection ────────────────────────────────────────────────

    def _collect_results(
        self,
        futures: list[tuple[int, concurrent.futures.Future]],
    ) -> None:
        """
        Block on all futures and update the session state.

        Items whose results indicate missing substats or failed
        validation are automatically queued for rescan.
        """
        for scan_index, fut in futures:
            try:
                result: EchoResult = fut.result(timeout=120)
            except Exception as exc:
                logger.error('Echo %d — OCR error: %s', scan_index, exc)
                self.session.mark_failed(scan_index, str(exc))
                continue

            if result.data is not None:
                self.session.mark_scanned(scan_index, result.data)

                # Check for rescan hints in warnings
                for w in result.warnings:
                    if 'thorough retry recommended' in w.lower() or 'missing substats' in w.lower():
                        item = self.session.get(scan_index)
                        if item.attempts < self.max_rescans:
                            self.session.request_rescan(
                                scan_index,
                                reason=w,
                            )
                        break
            else:
                self.session.mark_failed(scan_index, 'rejected by assembler')
                logger.debug('Echo %d — rejected', scan_index)

    # ── Rescan pass ──────────────────────────────────────────────────────

    def _run_rescan_pass(
        self,
        grid: GridNavigator,
        on_progress: Callable | None = None,
    ) -> None:
        """
        Drain the session's rescan queue and re-capture each item.
        """
        rescan_futures: list[tuple[int, concurrent.futures.Future]] = []

        # Collect all positions to rescan, then visit them in sorted order
        positions: list[GridPosition] = []
        indices: list[int] = []

        idx = self.session.pop_rescan()
        while idx is not None:
            item = self.session.get(idx)
            positions.append(item.position)
            indices.append(idx)
            idx = self.session.pop_rescan()

        if not positions:
            return

        pos_index_map = dict(zip(
            [p.scan_index for p in positions],
            indices,
        ))

        def _rescan_visitor(position: GridPosition) -> bool:
            if self._stop_event and self._stop_event.is_set():
                return False
            future = self._capture_echo(position)
            rescan_futures.append((position.scan_index, future))
            return True

        grid.visit_positions(positions, _rescan_visitor)

        # Collect rescan results
        for scan_index, fut in rescan_futures:
            try:
                result: EchoResult = fut.result(timeout=120)
            except Exception as exc:
                logger.error('Echo %d rescan — OCR error: %s', scan_index, exc)
                self.session.mark_failed(scan_index, f'rescan error: {exc}')
                continue

            if result.data is not None:
                self.session.mark_rescanned(scan_index, result.data)
                logger.info('Echo %d — rescan accepted', scan_index)
            else:
                self.session.mark_failed(scan_index, 'rejected after rescan')
                logger.warning('Echo %d — still rejected after rescan', scan_index)

            if on_progress:
                on_progress(self.session.scanned_count, self.session.total_items)

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
        echo_dir = self.save_raw / f'echo_{pos.scan_index:04d}'
        echo_dir.mkdir(parents=True, exist_ok=True)

        cv2.imwrite(str(echo_dir / 'full.png'), full)

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
        with open(echo_dir / 'meta.json', 'w') as f:
            json.dump(meta, f, indent=2)
