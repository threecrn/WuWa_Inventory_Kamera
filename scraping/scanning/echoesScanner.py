"""
scraping.scanning.echoesScanner
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Phase 1 of the two-phase echo scraper: **game navigation and raw image capture**.

This module is intentionally kept free of OCR, fuzzy matching, and any data
parsing.  Its only responsibilities are:

1. Open the in-game echo inventory.
2. Determine how many echoes and pages exist (minimal OCR for navigation only).
3. For every echo cell, in order:
   a. Click the cell to select the echo.
   b. Capture the full screen (stats panel is now visible).
   c. Scroll down to reveal the sonata section, capture that crop, scroll back.
4. Build a :class:`~scraping.models.rawScan.RawEchoScan` for each capture.
5. Persist each scan to disk via :func:`~scraping.utils.saveRawScan`.
6. Return the complete list of :class:`~scraping.models.rawScan.RawEchoScan`
   objects so the caller can pass them directly to Phase 2 without an extra
   disk round-trip.

The saved images can be reloaded at any time with
:func:`~scraping.utils.loadRawScans` and fed into Phase 2 (the processor)
without the game running.
"""

from __future__ import annotations

import logging
import string
from pathlib import Path

import numpy as np

from game.screenInfo import ScreenInfo
from properties.app_config import app_config
from scraping.models.rawScan import RawEchoScan
from scraping.utils import (
    imageToString,
    saveRawScan,
    screenshot,
)
from scraping.utils.mouse_keyboard import WindowsInputController

logger = logging.getLogger(__name__)

# Grid dimensions — must stay in sync with the constants in echoesScraper.py
# until Step 5 (slim orchestrator) moves them to a shared location.
ROWS, COLS = 4, 6


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _getEchoPages(screenInfo: ScreenInfo) -> tuple[int, int]:
    """
    Read the echo count displayed in the inventory UI and derive the page count.

    This is the *only* place in the scanner that calls ``imageToString``.  It is
    used purely for navigation control (knowing when to stop and when to advance
    to the next page) — not for parsing echo data.

    Returns
    -------
    tuple[int, int]
        ``(echo_count, page_count)``
    """
    full = screenshot(
        width=screenInfo.width,
        height=screenInfo.height,
        monitor=screenInfo.monitor,
    )
    page_crop = full[
        screenInfo.echoes.page.y : screenInfo.echoes.page.y + screenInfo.echoes.page.h,
        screenInfo.echoes.page.x : screenInfo.echoes.page.x + screenInfo.echoes.page.w,
    ]
    raw_text = imageToString(page_crop, allowedChars=string.digits + '/').split('/')[0]
    try:
        count = int(raw_text)
        pages = int(np.ceil(count / 24))
        return count, pages
    except ValueError:
        logger.warning(
            "Could not parse echo count from inventory UI (got %r) — "
            "falling back to 24 echoes / 1 page.",
            raw_text,
        )
        return 24, 1


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def echoScanner(
    controller: WindowsInputController,
    x: float,
    y: float,
    screenInfo: ScreenInfo,
    session_id: str,
    raw_base: Path,
) -> list[RawEchoScan]:
    """
    Phase 1 — Navigate the game and capture raw images for every echo.

    Opens the echo inventory tab, iterates over every page/row/column, and for
    each cell:

    * clicks to select the echo,
    * captures the full screen (stats panel visible),
    * scrolls down to reveal the sonata section,
    * captures the sonata region crop,
    * scrolls back up,
    * saves a :class:`~scraping.models.rawScan.RawEchoScan` to *raw_base*.

    No OCR, no fuzzy matching, and no echo data parsing are performed here.

    Parameters
    ----------
    controller:
        Input controller used for all mouse/keyboard interaction.
    x, y:
        Screen coordinates of the **Echoes** tab in the main inventory menu.
    screenInfo:
        Screen layout information for the current resolution.
    session_id:
        Identifier for the current scan session.  Used as the sub-folder name
        inside *raw_base* and stored in each :class:`~scraping.models.rawScan.RawEchoScan`.
    raw_base:
        Root directory where raw scans will be saved, e.g.
        ``export/{session_id}/raw``.  Created automatically if it does not exist.

    Returns
    -------
    list[RawEchoScan]
        All captures for this session in grid-traversal order
        (page → row → column).
    """
    scans: list[RawEchoScan] = []
    index = 0

    # Open the inventory and navigate to the Echoes tab.
    controller.pressKey(app_config.inventoryKeybind, 2, False)
    controller.leftClick(x, y)

    echo_count, pages = _getEchoPages(screenInfo)
    logger.info(
        "Echo scanner started — session=%s  echoes=%d  pages=%d",
        session_id, echo_count, pages,
    )

    # center_x/center_y are kept outside the inner loop so the page-scroll
    # click after each page can reuse the last cell's coordinates, matching
    # the behaviour of the original echoScraper.
    center_x, center_y = 0, 0

    for page in range(pages):
        for row in range(ROWS):
            for col in range(COLS):

                # On the final page stop as soon as we have covered all echoes.
                # Using a simple >= comparison avoids the off-by-one present in
                # the original code when echo_count is an exact multiple of 24.
                cells_so_far = page * (ROWS * COLS) + row * COLS + col
                if page == pages - 1 and cells_so_far >= echo_count:
                    logger.debug(
                        "Reached echo limit (%d) at page=%d row=%d col=%d — stopping.",
                        echo_count, page, row, col,
                    )
                    logger.info(
                        "Echo scanner finished — session=%s  captured=%d",
                        session_id, len(scans),
                    )
                    return scans

                center_x = (
                    screenInfo.echoes.start.x
                    + col * (screenInfo.echoes.start.w + screenInfo.offsets.page.x)
                    + screenInfo.echoes.start.w // 2
                )
                center_y = (
                    screenInfo.echoes.start.y
                    + row * (screenInfo.echoes.start.h + screenInfo.offsets.page.y)
                    + screenInfo.echoes.start.h // 2
                )

                # ── 1. Select the echo cell ──────────────────────────────────
                controller.leftClick(center_x, center_y)

                # ── 2. Capture full screen (stats panel now visible) ─────────
                full = screenshot(
                    width=screenInfo.width,
                    height=screenInfo.height,
                    monitor=screenInfo.monitor,
                )

                # ── 3. Scroll down to reveal the sonata section ──────────────
                controller.moveMouse(
                    screenInfo.echoes.mouseMovement.x,
                    screenInfo.echoes.mouseMovement.y,
                    0.1,
                )
                #controller.mouseScroll(-screenInfo.scroll.sonata.y, 0.5)
                controller.mouseScroll(-100, 0.6) # scroll down to sonata
                controller.mouseScroll(2, 0.2)  # little scroll back to ensure sonata is fully in view

                # ── 4. Capture the sonata region crop ────────────────────────
                sonata = screenshot(
                    screenInfo.echoes.sonata.x,
                    screenInfo.echoes.sonata.y,
                    screenInfo.echoes.sonata.w,
                    screenInfo.echoes.sonata.h,
                    monitor=screenInfo.monitor,
                )

                # ── 5. Scroll back up to the stats section ───────────────────
                controller.moveMouse(
                    screenInfo.echoes.mouseMovement.x,
                    screenInfo.echoes.mouseMovement.y,
                    0.1,
                )
                controller.mouseScroll(screenInfo.scroll.sonata.y, 0.5)

                # ── 6. Build, save, and collect the raw scan ─────────────────
                scan = RawEchoScan(
                    session_id=session_id,
                    index=index,
                    page=page,
                    row=row,
                    col=col,
                    full_screenshot=full,
                    sonata_screenshot=sonata,
                    screen_width=screenInfo.width,
                    screen_height=screenInfo.height,
                    monitor=screenInfo.monitor,
                )
                saveRawScan(scan, raw_base)
                scans.append(scan)
                logger.debug("Captured %s", scan)
                index += 1

        # Advance to the next page (skip after the final page).
        if page < pages - 1:
            controller.leftClick(center_x, center_y)
            controller.mouseScroll(screenInfo.scroll.page.y, 1.2)

    logger.info(
        "Echo scanner finished — session=%s  captured=%d",
        session_id, len(scans),
    )
    return scans
