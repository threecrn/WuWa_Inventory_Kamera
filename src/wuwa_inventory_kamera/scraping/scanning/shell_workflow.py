"""
wuwa_inventory_kamera.scraping.scanning.shell_workflow
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Scanning workflow for the shell-currency HUD counter.

Strategy (mirrors the legacy ``shellScraper``):

1. Ensure the main HUD is visible (press Esc to close any open panel).
2. Capture the ``shell`` ROI — a number displayed permanently in the
   top bar of the main screen.
3. Submit a :class:`~..service.captures.ShellCapture` to the OcrService
   and block for the result.
4. Return ``{'2': amount}`` — the shell item ID ``'2'`` matches the V1
   convention and the data layer expects this key.

No navigation beyond pressing Esc is needed because the shell counter is
part of the persistent HUD and is always visible on the main screen.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from ...game.navigation import GameNavigator
from ...game.screen import capture_region
from ..service.captures import ShellCapture, ShellResult
from ..service.ocr_service import OcrService
from .scan_state import ScanSession

logger = logging.getLogger(__name__)

# Item-data key for shell currency (matches V1 convention)
_SHELL_ITEM_ID = '2'


class ShellWorkflow:
    """
    Scanning workflow for the shell-currency HUD counter.

    Parameters
    ----------
    nav:
        Game navigator.
    ocr_service:
        OCR service used to assemble the shell amount.
    session:
        Scan session (carried for consistency; not actively used).
    save_raw:
        If set, raw screenshots are saved to this directory for offline
        reprocessing.
    """

    def __init__(
        self,
        nav: GameNavigator,
        ocr_service: OcrService,
        session: ScanSession,
        save_raw: Path | None = None,
    ) -> None:
        self.nav = nav
        self.ocr = ocr_service
        self.session = session
        self.save_raw = save_raw

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self) -> dict[str, int]:
        """
        Execute the shell scan.

        Returns
        -------
        dict[str, int]
            ``{'2': <shell_amount>}`` — ready to merge into the session
            result dict.
        """
        layout = self.nav.layout

        # Ensure HUD is visible
        self.nav.ctrl.press_key('esc', wait=0.5)

        amount_crop = capture_region(self.nav.gw, layout.shell)

        # Optionally save raw images
        if self.save_raw:
            self._save_raw(amount_crop)

        capture = ShellCapture(amount=amount_crop)

        try:
            result: ShellResult = self.ocr.submit(capture).result(timeout=30)
            amount = result.amount
        except Exception as exc:
            logger.error('ShellWorkflow — OCR error: %s', exc)
            amount = 0

        logger.info('ShellWorkflow finished — shell amount: %d', amount)
        return {_SHELL_ITEM_ID: amount}

    # ── Raw image persistence ────────────────────────────────────────────

    def _save_raw(self, amount_crop: np.ndarray) -> None:
        """Save raw screenshot to disk for offline reprocessing."""
        import json
        import cv2

        assert self.save_raw is not None
        self.save_raw.mkdir(parents=True, exist_ok=True)

        cv2.imwrite(str(self.save_raw / 'shell.png'), amount_crop)

        meta = {
            'screen_width': self.nav.layout.width,
            'screen_height': self.nav.layout.height,
            'monitor': self.nav.layout.monitor,
        }
        with open(self.save_raw / 'meta.json', 'w') as f:
            json.dump(meta, f, indent=2)
