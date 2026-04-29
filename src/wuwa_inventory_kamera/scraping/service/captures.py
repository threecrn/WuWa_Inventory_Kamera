"""
wuwa_inventory_kamera.scraping.service.captures
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Capture and result dataclasses exchanged between scanner threads and the
:class:`~wuwa_inventory_kamera.scraping.service.ocr_service.OcrService`.

Each scraper submits a ``*Capture`` object to the service and receives a
``concurrent.futures.Future`` that resolves to the matching ``*Result``
when OCR + assembly is complete.

Capture objects carry **in-memory image arrays only** — no disk I/O.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


# ---------------------------------------------------------------------------
# Internal sentinel (do not use outside this package)
# ---------------------------------------------------------------------------

@dataclass
class _Stop:
    """Posted to the OcrService queue to request a clean shutdown."""


# ---------------------------------------------------------------------------
# Echoes
# ---------------------------------------------------------------------------

@dataclass
class EchoCapture:
    """All image crops needed to process one echo card."""
    echo_index:      int
    card:            np.ndarray        # name + level + rarity region (RGB)
    stats_name:      np.ndarray        # stat name column (RGB)
    stats_value:     np.ndarray        # stat value column (RGB)
    sonata_icon:     np.ndarray | None = None  # small circular sonata icon crop (BGR)
    full_screenshot: np.ndarray | None = None  # full frame, debug mode only
    # Set by OcrService.submit(); callers must not touch this field.
    _uid: int = field(default=-1, init=False, repr=False, compare=False)


@dataclass
class EchoResult:
    """Assembled result for one echo, produced by :class:`EchoAssembler`."""
    echo_index:     int
    data:           dict | None    # None = rejected (below threshold or invalid)
    warnings:       list[str]
    retried:        bool
    detected_level: int = 0        # level parsed from card OCR (0 if unparseable)


# ---------------------------------------------------------------------------
# Weapons
# ---------------------------------------------------------------------------

@dataclass
class WeaponCapture:
    """Image crops for one grid cell in the weapon/items inventory."""
    index: int
    name:  np.ndarray           # weapon / item name region (RGB)
    value: np.ndarray           # quantity (items) or level string (weapons) (RGB)
    rank:  np.ndarray | None    # refinement rank digit; None for plain items (RGB)
    _uid: int = field(default=-1, init=False, repr=False, compare=False)


@dataclass
class WeaponResult:
    """Assembled result for one weapon or item stack."""
    index:          int
    is_weapon:      bool
    data:           dict | None   # None = below threshold or not recognised
    below_minimum:  bool = False  # True when rejected solely because level < min_level


# ---------------------------------------------------------------------------
# Items / Resources
# ---------------------------------------------------------------------------

@dataclass
class ItemCapture:
    """Image crop for one item cell (name + count in a single crop)."""
    index: int
    info:  np.ndarray    # single crop containing the name + count lines (RGB)
    _uid: int = field(default=-1, init=False, repr=False, compare=False)


@dataclass
class ItemResult:
    """Assembled result for one item stack."""
    index:   int
    name:    str
    item_id: str | None   # key into itemsID; None = unrecognised
    count:   int


# ---------------------------------------------------------------------------
# Characters
# ---------------------------------------------------------------------------

@dataclass
class CharCapture:
    """
    One section of data readable from the current character panel.

    The scanner submits one ``CharCapture`` per UI section and adds crops
    relevant to that section.  The assembler accumulates partial results
    across all five sections and produces a ``CharResult`` when the final
    section (4) is resolved.

    Sections
    --------
    0 — resonator overview  (name, level, ascension)
    1 — weapon panel        (weapon name, level, rank)
    2 — echoes panel        (skipped — handled by the echo scraper)
    3 — skills panel        (skill levels for all active nodes)
    4 — resonance chain     (button status per chain node)
    """
    char_index: int
    section:    int                         # 0–4, as above
    crops:      dict[str, np.ndarray]       # field_name → RGB crop
    _uid: int = field(default=-1, init=False, repr=False, compare=False)


@dataclass
class CharResult:
    """
    Assembled result for all sections of one character.

    The assembler only resolves a ``CharCapture`` future when the last
    section (4) has been received.
    """
    char_index: int
    section:    int      # section this partial result belongs to
    fields:     dict     # parsed values for this section (and any prior ones)


# ---------------------------------------------------------------------------
# Achievements
# ---------------------------------------------------------------------------

@dataclass
class AchievementCapture:
    """Image crop for one achievement status check."""
    achievement_name: str         # name used to search
    achievement_id:   int         # from achievementsID
    status:           np.ndarray  # status button crop (RGB)
    _uid: int = field(default=-1, init=False, repr=False, compare=False)


@dataclass
class AchievementResult:
    """Result for one achievement."""
    achievement_name: str
    achievement_id:   int
    completed:        bool


# ---------------------------------------------------------------------------
# Shell currency
# ---------------------------------------------------------------------------

@dataclass
class ShellCapture:
    """Image crop of the shell-currency HUD region."""
    amount: np.ndarray  # shell count text region (RGB)
    _uid: int = field(default=-1, init=False, repr=False, compare=False)


@dataclass
class ShellResult:
    """Assembled result for the shell-currency amount."""
    amount: int   # 0 when OCR failed


# ---------------------------------------------------------------------------
# Tagged union helper
# ---------------------------------------------------------------------------

CaptureType = EchoCapture | WeaponCapture | ItemCapture | CharCapture | AchievementCapture
