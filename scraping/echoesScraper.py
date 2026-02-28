"""
scraping.echoesScraper
~~~~~~~~~~~~~~~~~~~~~~

Thin orchestrator that wires Phase 1 (scanner) into Phase 2 (processor).

All game-navigation logic lives in :mod:`scraping.scanning.echoesScanner`.
All OCR / parsing logic lives in :mod:`scraping.processing.echoesProcessor`.

The public signature of :func:`echoScraper` is the only thing that
:mod:`scraping.scraperManager` depends on.  It is intentionally minimal.
"""

from __future__ import annotations

import logging
from pathlib import Path

from game.screenInfo import ScreenInfo
from properties.config import cfg
from scraping.processing.echoesProcessor import echoProcessor
from scraping.scanning.echoesScanner import echoScanner
from scraping.utils import WindowsInputController

logger = logging.getLogger(__name__)


def echoScraper(
    controller: WindowsInputController,
    x: float,
    y: float,
    screenInfo: ScreenInfo,
    session_id: str,
) -> list[dict]:
    """
    Scan all echoes from the in-game inventory and return parsed echo data.

    Orchestrates the two-phase pipeline:

    1. **Phase 1** (:func:`~scraping.scanning.echoesScanner.echoScanner`) —
       navigate the game, capture raw images, and persist them under
       ``export/{session_id}/raw/``.
    2. **Phase 2** (:func:`~scraping.processing.echoesProcessor.echoProcessor`) —
       run OCR and fuzzy matching on the captured images to produce structured
       echo dicts.

    Parameters
    ----------
    controller:
        Input controller used for all mouse / keyboard interaction.
    x, y:
        Screen coordinates of the **Echoes** tab in the main inventory menu.
    screenInfo:
        Screen layout information for the current resolution.
    session_id:
        Current scan session identifier (e.g. ``"2026-02-28_14-30-00"``).
        Used as the sub-folder name for saved raw images and forwarded to the
        processor for logging.

    Returns
    -------
    list[dict]
        Parsed echo dicts, one per accepted echo.
    """
    raw_base = Path(cfg.get(cfg.exportFolder)) / session_id / "raw"

    logger.info("echoScraper — session=%s  raw_base=%s", session_id, raw_base)

    # Phase 1: game navigation + raw image capture
    scans = echoScanner(controller, x, y, screenInfo, session_id, raw_base)

    # Phase 2: offline OCR + parsing (no game access)
    return echoProcessor(scans, session_id, raw_base)
