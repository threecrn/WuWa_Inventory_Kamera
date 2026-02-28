from __future__ import annotations

import cv2
import numpy as np
from dataclasses import dataclass, field
from pathlib import Path


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
    # Default to None so that loadRawScans() can populate only the paths and
    # defer the actual cv2.imread to load_images(), called just before OCR.
    full_screenshot: np.ndarray | None = field(default=None, repr=False, compare=False)
    """Full game screenshot; None until load_images() is called."""

    sonata_screenshot: np.ndarray | None = field(default=None, repr=False, compare=False)
    """Sonata region crop; None until load_images() is called."""

    # --- disk paths (set by loadRawScans; None when images are held in-memory) ---
    full_path: Path | None = field(default=None, repr=False, compare=False)
    """Absolute path to full.png written by saveRawScan()."""

    sonata_path: Path | None = field(default=None, repr=False, compare=False)
    """Absolute path to sonata.png written by saveRawScan()."""

    # --- screen context (needed to reconstruct ScreenInfo in Phase 2) ---
    screen_width: int = 1920
    """Game window width in pixels."""

    screen_height: int = 1080
    """Game window height in pixels."""

    monitor: int = 1
    """Monitor index passed to mss (1-based)."""

    def load_images(self) -> None:
        """
        Load ``full_screenshot`` and ``sonata_screenshot`` from disk.

        Called by :func:`~scraping.processing.echoesProcessor._processRawScan`
        immediately before OCR is run.  Safe to call repeatedly — images that
        are already in memory are not reloaded.

        Raises
        ------
        FileNotFoundError
            If a path field is ``None`` or the referenced file cannot be read.
        """
        if self.full_screenshot is None:
            if self.full_path is None:
                raise FileNotFoundError(f"Scan {self.index}: full_path is not set.")
            bgr = cv2.imread(str(self.full_path))
            if bgr is None:
                raise FileNotFoundError(f"Scan {self.index}: could not read {self.full_path}")
            self.full_screenshot = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

        if self.sonata_screenshot is None:
            if self.sonata_path is None:
                raise FileNotFoundError(f"Scan {self.index}: sonata_path is not set.")
            bgr = cv2.imread(str(self.sonata_path))
            if bgr is None:
                raise FileNotFoundError(f"Scan {self.index}: could not read {self.sonata_path}")
            self.sonata_screenshot = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

    def release_images(self) -> None:
        """
        Drop in-memory image arrays to free RAM.

        ``full_path`` and ``sonata_path`` remain set, so :meth:`load_images`
        can reload them if needed.
        """
        self.full_screenshot = None
        self.sonata_screenshot = None

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
