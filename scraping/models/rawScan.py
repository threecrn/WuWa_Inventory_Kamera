from __future__ import annotations

import numpy as np
from dataclasses import dataclass, field


@dataclass
class RawEchoScan:
    """
    All data captured for a single echo during the scan phase (Phase 1).

    No OCR has been performed at this point. This is the contract between the
    Scanner (Phase 1, game navigation) and the Processor (Phase 2, OCR + parsing).

    Instances are persisted to disk by ``saveRawScan`` and reconstructed by
    ``loadRawScans`` (both in ``scraping.utils.common``), so the Processor can be
    run offline without the game.

    Session folder layout
    ---------------------
    export/{session_id}/raw/
        echo_0000/
            full.png        <- full_screenshot (RGB)
            sonata.png      <- sonata_screenshot (RGB)
            meta.json       <- all non-image fields (see meta())
        echo_0001/
            ...
    """

    # --- identity ---
    session_id: str
    """Scan session identifier; matches the parent export folder name."""

    index: int
    """Sequential position of this echo within the session (0-based)."""

    # --- grid position ---
    page: int
    """Inventory page number this echo was found on (0-based)."""

    row: int
    """Grid row within the page (0-based)."""

    col: int
    """Grid column within the page (0-based)."""

    # --- images (excluded from repr / equality to keep those fast) ---
    full_screenshot: np.ndarray = field(repr=False, compare=False)
    """Full game screenshot taken immediately after clicking this echo cell."""

    sonata_screenshot: np.ndarray = field(repr=False, compare=False)
    """Cropped sonata-set region, captured after scrolling down to reveal it."""

    # --- screen context (needed to reconstruct ScreenInfo in Phase 2) ---
    screen_width: int = 1920
    """Game window width in pixels."""

    screen_height: int = 1080
    """Game window height in pixels."""

    monitor: int = 1
    """Monitor index passed to mss (1-based)."""

    def meta(self) -> dict:
        """Return all non-image fields as a JSON-serialisable dict."""
        return {
            'session_id': self.session_id,
            'index': self.index,
            'page': self.page,
            'row': self.row,
            'col': self.col,
            'screen_width': self.screen_width,
            'screen_height': self.screen_height,
            'monitor': self.monitor,
        }

    def __repr__(self) -> str:
        return (
            f"RawEchoScan("
            f"session_id={self.session_id!r}, "
            f"index={self.index}, "
            f"page={self.page}, row={self.row}, col={self.col}, "
            f"screen={self.screen_width}x{self.screen_height}, "
            f"monitor={self.monitor})"
        )
