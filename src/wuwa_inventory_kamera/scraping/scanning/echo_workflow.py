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
import re
import threading
from pathlib import Path
from typing import Callable

import numpy as np

from ...game.navigation import (
    GameNavigator,
    InventoryTab,
    SortOrder,
    CELLS_PER_PAGE,
    _nav_ocr,
)
from ...game.screen import capture_full, capture_region
from .grid_navigator import GridNavigator
from .scan_state import (
    GridPosition,
    ScanItemStatus,
    ScanSession,
)
from ..service.captures import EchoCapture, EchoResult
from ..service.ocr_service import OcrService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rarity detection from a single sampled pixel (new-UI mechanic)
# ---------------------------------------------------------------------------
# Reference colors from game_roi.py comments, expressed in BGR order
# as used by OpenCV / mss captures.
#   rarity 5: gold   – (R=1.00, G=0.98, B=0.69)
#   rarity 4: purple – (R=0.91, G=0.63, B=1.00)
#   rarity 3: blue   – (R=0.60, G=0.60, B=1.00)
#   rarity 2: green  – (R=0.60, G=1.00, B=0.60)
_RARITY_PIXEL_COLORS_BGR: dict[int, np.ndarray] = {
    5: np.array([176, 250, 255], dtype=np.int32),   # B=176 G=250 R=255
    4: np.array([255, 161, 232], dtype=np.int32),   # B=255 G=161 R=232
    3: np.array([255, 153, 153], dtype=np.int32),   # B=255 G=153 R=153
    2: np.array([153, 255, 153], dtype=np.int32),   # B=153 G=255 R=153
}


def _rarity_from_bgr_pixel(pixel: np.ndarray) -> int:
    """Return the rarity (2–5) whose reference color is closest to *pixel* (BGR)."""
    px = pixel[:3].astype(np.int32)
    best_rarity = 1
    best_dist = float('inf')
    for rarity, ref in _RARITY_PIXEL_COLORS_BGR.items():
        dist = float(np.sum((px - ref) ** 2))
        if dist < best_dist:
            best_dist = dist
            best_rarity = rarity
    return best_rarity


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

            # Before capturing, check the level against min_level constraint.
            # Since echoes are sorted by level descending, any level below the
            # minimum means all remaining echoes can be skipped.  This avoids
            # submitting unnecessary OCR jobs to the service.
            if self.min_level > 0:
                level = self._read_last_echo_level()
                if level is not None and level < self.min_level:
                    logger.info(
                        'Level-based early stop at index %d: '
                        'detected_level=%d < min_level=%d',
                        position.scan_index, level, self.min_level,
                    )
                    return False

            future = self._capture_echo(position)
            future.add_done_callback(_process_done_callback)
            futures.append((position.scan_index, future))
            if on_progress:
                on_progress(len(futures), total_items)

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

    # ── Ad-hoc level read ────────────────────────────────────────────────

    def _read_last_echo_level(self) -> int | None:
        """
        Synchronously OCR just the level number from the currently selected
        echo panel without touching the OcrService queue.

        Uses the same lightweight ``imageToString`` path as ``_nav_ocr`` in
        :mod:`~...game.navigation` — a single recognition forward pass on the
        CPU backend — so it adds < 100 ms to the scan per page checked.

        Returns the integer level, or ``None`` if OCR failed to find digits.
        """
        roi = self.nav.layout.echoes.level
        crop = capture_region(self.nav.gw, roi)
        text = _nav_ocr(crop, allowed='0123456789')
        text = text.strip()
        if text.isdigit():
            return int(text)
        # Fallback: grab first run of digits (handles OCR artefacts)
        m = re.search(r'\d+', text)
        if m:
            return int(m.group())
        logger.debug('Level OCR returned no digits: %r', text)
        return None

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

        # ── Rarity via single pixel sample (new-UI) ──────────────────────
        detected_rarity: int | None = None
        if hasattr(ei, 'rarityColorPick'):
            rcp = ei.rarityColorPick
            detected_rarity = _rarity_from_bgr_pixel(full[int(rcp.y), int(rcp.x)])
            logger.debug('Echo %d — rarity pixel BGR=%s → rarity %d',
                         pos.scan_index, full[int(rcp.y), int(rcp.x)].tolist(), detected_rarity)

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

        # ── Sonata icon crop (level-dependent for new UI) ────────────────
        # In the new UI the sonata icon sits immediately to the right of the
        # level badge on the echo card header.  A two-digit level (10-25) is
        # wider than a single-digit one (0-9), so the icon position differs.
        # We OCR the level directly from the already-captured full screenshot
        # to select the correct ROI variant before submitting the future.
        si_raw = ei.sonataIcon
        sonata_icon_cx: float | None = None
        sonata_icon_cy: float | None = None
        sonata_icon_r:  float | None = None
        detected_level: int | None = None

        if hasattr(si_raw, 'level_X'):
            # New-UI nested structure — pick variant based on digit count.
            level_crop = full[
                int(ei.level.y) : int(ei.level.y + ei.level.h),
                int(ei.level.x) : int(ei.level.x + ei.level.w),
            ]
            level_text = _nav_ocr(level_crop, allowed='0123456789').strip()
            two_digits = len(level_text) == 2
            si_slot = si_raw.level_XX if two_digits else si_raw.level_X
            logger.debug(
                'Echo %d — level_text=%r two_digits=%s → sonataIcon=%s',
                pos.scan_index, level_text, two_digits,
                'level_XX' if two_digits else 'level_X',
            )
            si = si_slot.icon
            sonata_icon_cx = si_slot.circle.x
            sonata_icon_cy = si_slot.circle.y
            sonata_icon_r  = si_raw.radius
            # Parse the level here so the assembler doesn't need to OCR card for it
            if level_text.isdigit():
                detected_level = min(25, int(level_text))
        else:
            # Legacy flat Coordinates (older resolution entries).
            si = si_raw
            if hasattr(ei, 'sonataIconCircle'):
                sic = ei.sonataIconCircle
                if hasattr(sic, 'circle'):
                    sonata_icon_cx = sic.circle.x
                    sonata_icon_cy = sic.circle.y
                if hasattr(sic, 'radius'):
                    sonata_icon_r = sic.radius

        sonata_icon = full[
            int(si.y) : int(si.y + si.h),
            int(si.x) : int(si.x + si.w),
        ]

        # ── Echo name crop (colour-filtered in the OCR service) ──────────
        echo_name: np.ndarray | None = None
        if hasattr(ei, 'echoName'):
            en = ei.echoName
            echo_name = full[
                int(en.y) : int(en.y + en.h),
                int(en.x) : int(en.x + en.w),
            ]

        # Optionally save raw images
        if self.save_raw:
            self._save_raw(pos, full)

        capture = EchoCapture(
            echo_index=pos.scan_index,
            card=card,
            echo_name=echo_name,
            detected_level=detected_level,
            detected_rarity=detected_rarity,
            sonata_icon=sonata_icon,
            sonata_icon_cx=sonata_icon_cx,
            sonata_icon_cy=sonata_icon_cy,
            sonata_icon_r=sonata_icon_r,
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
