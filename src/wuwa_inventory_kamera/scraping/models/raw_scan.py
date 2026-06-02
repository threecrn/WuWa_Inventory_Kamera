from __future__ import annotations

import numpy as np
from dataclasses import dataclass, field
from pathlib import Path

from ... import imgio


@dataclass
class RawEchoScan:
    """
    All data captured for a single echo during the scan phase (Phase 1).

    No OCR has been performed at this point. This is the contract between the
    Scanner (Phase 1, game navigation) and the Processor (Phase 2, OCR + parsing).

    Instances are reconstructed by ``loadRawScans`` in
    ``scraping.utils.common`` from the persisted ``full.png`` + ``meta.json``
    session format, so the Processor can be run offline without the game.

    Session folder layout
    ---------------------
    export/{session_id}/raw/
        echo_0000/
            full.png        <- full_screenshot (RGB)
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
    # defer the actual imgio.imread to load_images(), called just before OCR.
    full_screenshot: np.ndarray | None = field(default=None, repr=False, compare=False)
    """Full game screenshot; None until load_images() is called."""

    # --- disk paths (set by loadRawScans; None when images are held in-memory) ---
    full_path: Path | None = field(default=None, repr=False, compare=False)
    """Absolute path to full.png written by the raw capture workflow."""

    # --- screen context (needed to reconstruct ScreenInfo in Phase 2) ---
    screen_width: int = 1920
    """Game window width in pixels."""

    screen_height: int = 1080
    """Game window height in pixels."""

    monitor: int = 1
    """Monitor index passed to mss (1-based)."""

    def load_images(self) -> None:
        """
        Load ``full_screenshot`` from disk.

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
            bgr = imgio.imread(str(self.full_path))
            if bgr is None:
                raise FileNotFoundError(f"Scan {self.index}: could not read {self.full_path}")
            self.full_screenshot = imgio.convert_color(bgr, imgio.ColorCode.BGR2RGB)

    def release_images(self) -> None:
        """
        Drop in-memory image arrays to free RAM.

        ``full_path`` remains set, so :meth:`load_images` can reload it if
        needed.
        """
        self.full_screenshot = None

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


@dataclass
class RawCharacterScan:
    """
    Disk-backed raw capture for one scanned character.

    Session folder layout
    ---------------------
    export/{session_id}/raw/
        char_0000/
            meta.json
            section_0/
                full.png
            section_1/
                full.png
            section_3/
                skill_0.png
                ...
            section_4/
                chain_0.png
                ...
    """

    index: int
    screen_width: int = 1920
    screen_height: int = 1080
    monitor: int = 1
    section_paths: dict[int, dict[str, Path]] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )
    base_path: Path | None = field(default=None, repr=False, compare=False)

    def load_section_images(self, section: int) -> dict[str, np.ndarray]:
        """Load all saved PNGs for *section* into memory."""
        paths = self.section_paths.get(section)
        if not paths:
            raise FileNotFoundError(
                f'Character scan {self.index}: no saved images for section {section}.'
            )

        images: dict[str, np.ndarray] = {}
        for name, path in paths.items():
            bgr = imgio.imread(str(path))
            if bgr is None:
                raise FileNotFoundError(
                    f'Character scan {self.index}: could not read {path}'
                )
            images[name] = bgr
        return images

    def __repr__(self) -> str:
        sections = ','.join(str(section) for section in sorted(self.section_paths))
        return (
            f"RawCharacterScan(index={self.index}, "
            f"screen={self.screen_width}x{self.screen_height}, "
            f"monitor={self.monitor}, sections=[{sections}])"
        )
