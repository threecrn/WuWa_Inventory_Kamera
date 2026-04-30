"""
wuwa_inventory_kamera.scraping.scanning.achievement_workflow
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Scanning workflow for the achievements panel.

Strategy (mirrors the legacy ``achievementScraper``):

1. Press Esc to ensure we're at the main overlay.
2. Click the achievements button (bottom-right HUD icon).
3. Click the achievements sub-tab.
4. For each achievement in ``achievementsID``:

   a. Copy the name to the clipboard and paste into the search bar.
   b. Click the search button.
   c. Capture the ``status`` ROI.
   d. Submit an :class:`~..service.captures.AchievementCapture` to the
      OcrService and block for the result.
   e. If the result is *completed*, record the achievement ID.

5. Click the search button again (clears the search field).
6. Press Esc to close the achievements panel.

The "completed" condition (from the assembler) is:

* Status text matches the defined "claim" text (reward ready to claim).
* Or status text contains ``'/'`` (numeric progress indicator, e.g. "3/3").

Note: the clipboard-paste approach is used for non-ASCII achievement names
to avoid keyboard-layout issues — identical to V1.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

import numpy as np

from ...game.navigation import GameNavigator
from ...game.screen import capture_region
from ..service.captures import AchievementCapture, AchievementResult
from ..service.ocr_service import OcrService
from .scan_state import ScanSession

logger = logging.getLogger(__name__)


class AchievementWorkflow:
    """
    Scanning workflow for the achievements panel.

    Parameters
    ----------
    nav:
        Game navigator.
    ocr_service:
        OCR service for assembling achievement status.
    session:
        Scan session (used for progress tracking only).
    stop_event:
        Optional :class:`~threading.Event`; scanning stops when set.
    save_raw:
        If set, raw screenshots are saved to this directory for offline
        reprocessing.
    """

    def __init__(
        self,
        nav: GameNavigator,
        ocr_service: OcrService,
        session: ScanSession,
        stop_event: threading.Event | None = None,
        save_raw: Path | None = None,
    ) -> None:
        self.nav = nav
        self.ocr = ocr_service
        self.session = session
        self._stop_event = stop_event
        self.save_raw = save_raw

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self, on_progress: Callable | None = None) -> list[str]:
        """
        Execute the achievement scan.

        Returns
        -------
        list[str]
            List of completed achievement IDs.
        """
        from ...scraping.data import achievementsID

        layout = self.nav.layout
        ctrl   = self.nav.ctrl
        ach    = layout.achievements  # coordinate block

        # Open achievements panel
        ctrl.press_key('esc', wait=0.5)
        ctrl.click(ach.achievementsButton.x, ach.achievementsButton.y, wait=1.2)
        ctrl.click(ach.achievementsTab.x, ach.achievementsTab.y, wait=1.0)

        completed_ids: list[str] = []
        total = len(achievementsID)

        for idx, (achievement_name, achievement_id) in enumerate(achievementsID.items()):
            if self._stop_event and self._stop_event.is_set():
                logger.info('Achievement scan cancelled by user at %d/%d', idx, total)
                break

            # Paste name into search
            ctrl.click(ach.searchBar.x, ach.searchBar.y, wait=0.3)
            ctrl.paste(achievement_name, wait=0.3)
            ctrl.click(ach.searchButton.x, ach.searchButton.y, wait=0.6)

            # Capture status crop
            status_crop = capture_region(self.nav.gw, ach.status)

            # Optionally save raw images
            if self.save_raw:
                self._save_raw(achievement_id, achievement_name, status_crop)

            capture = AchievementCapture(
                achievement_name=achievement_name,
                achievement_id=achievement_id,
                status=status_crop,
            )

            try:
                result: AchievementResult = self.ocr.submit(capture).result(timeout=30)
                if result.completed:
                    completed_ids.append(str(achievement_id))
            except Exception as exc:
                logger.error('Achievement %r — OCR error: %s', achievement_name, exc)

            # Clear search field for the next iteration
            ctrl.click(ach.searchButton.x, ach.searchButton.y, wait=0.0)

            if on_progress:
                on_progress(idx + 1, total)

        ctrl.press_key('esc', wait=0.5)
        logger.info('Achievement workflow finished — %d/%d completed', len(completed_ids), total)
        return completed_ids

    # ── Raw image persistence ────────────────────────────────────────────

    def _save_raw(
        self,
        achievement_id: int,
        achievement_name: str,
        status_crop: np.ndarray,
    ) -> None:
        """Save raw screenshots to disk for offline reprocessing."""
        import json
        import cv2

        assert self.save_raw is not None
        ach_dir = self.save_raw / f'achievement_{achievement_id}'
        ach_dir.mkdir(parents=True, exist_ok=True)

        cv2.imwrite(str(ach_dir / 'status.png'), status_crop)

        meta = {
            'achievement_name': achievement_name,
            'achievement_id': achievement_id,
            'screen_width': self.nav.layout.width,
            'screen_height': self.nav.layout.height,
            'monitor': self.nav.layout.monitor,
        }
        with open(ach_dir / 'meta.json', 'w') as f:
            json.dump(meta, f, indent=2)
