"""
wuwa_inventory_kamera.scraping.scanning.scan_state
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

State tracking for inventory scanning sessions.

The :class:`ScanSession` manages the complete lifecycle of one scanning
run — tracking which items have been scanned, which need rescanning, the
current grid position, and the global progress.

The :class:`GridPosition` value object identifies a single cell in the
inventory grid by its ``(page, row, col)`` triple plus a flat
``scan_index``.

Design goals:

* **Rescan support** — Any previously-scanned item can be marked for
  rescan (e.g. when OCR detects a sonata scroll timing issue).  The
  session tracks a rescan queue that the scanning workflow drains.
* **Sort-order awareness** — The session records the sort order active
  when scanning started so that rescan navigation can correctly
  re-locate items even if the user changes the sort later.
* **Decoupled from OCR** — The session knows nothing about OCR; it only
  tracks indices and grid positions.
"""
from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from wuwa_inventory_kamera.game.navigation import SortOrder

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Grid position
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class GridPosition:
    """
    Unique address of one cell in the inventory grid.

    ``scan_index`` is the 0-based sequential index in grid-traversal order
    (page → row → col).
    """
    page:       int
    row:        int
    col:        int
    scan_index: int

    @staticmethod
    def from_index(index: int, cols: int = 6, rows: int = 4) -> 'GridPosition':
        """Compute page/row/col from a flat index."""
        per_page = rows * cols
        page = index // per_page
        remainder = index % per_page
        row = remainder // cols
        col = remainder % cols
        return GridPosition(page=page, row=row, col=col, scan_index=index)

    def to_index(self, cols: int = 6, rows: int = 4) -> int:
        per_page = rows * cols
        return self.page * per_page + self.row * cols + self.col


# ---------------------------------------------------------------------------
# Scan item state
# ---------------------------------------------------------------------------

class ScanItemStatus(enum.Enum):
    PENDING   = 'pending'
    SCANNED   = 'scanned'
    NEEDS_RESCAN = 'needs_rescan'
    RESCANNED = 'rescanned'
    FAILED    = 'failed'
    SKIPPED   = 'skipped'


@dataclass
class ScanItem:
    """Tracks the state of one inventory slot during a scan session."""
    position:   GridPosition
    status:     ScanItemStatus = ScanItemStatus.PENDING
    rescan_reason: str | None = None
    result:     Any = None  # set by the workflow after OCR
    attempts:   int = 0


# ---------------------------------------------------------------------------
# Scan session
# ---------------------------------------------------------------------------

class ScanSession:
    """
    Mutable state for one inventory scan run.

    Provides an ordered list of :class:`ScanItem` objects covering all
    grid cells, plus a rescan queue that workflows drain after the initial
    forward scan.

    Parameters
    ----------
    total_items:
        Total number of items reported by the game UI.
    sort_order:
        The sort order active when the scan started.
    session_id:
        Unique identifier for this session (defaults to a timestamp).
    """

    def __init__(
        self,
        total_items: int,
        sort_order: SortOrder = SortOrder.NEWEST,
        session_id: str | None = None,
    ) -> None:
        self.session_id = session_id or datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        self.sort_order = sort_order
        self.total_items = total_items

        # Pre-allocate scan items for every grid slot
        self.items: list[ScanItem] = [
            ScanItem(position=GridPosition.from_index(i))
            for i in range(total_items)
        ]

        # Rescan queue: indices into self.items
        self._rescan_queue: list[int] = []

    # ── Progress ─────────────────────────────────────────────────────────

    @property
    def scanned_count(self) -> int:
        return sum(
            1 for it in self.items
            if it.status in (ScanItemStatus.SCANNED, ScanItemStatus.RESCANNED)
        )

    @property
    def failed_count(self) -> int:
        return sum(1 for it in self.items if it.status == ScanItemStatus.FAILED)

    @property
    def progress(self) -> float:
        """0.0 – 1.0 progress ratio."""
        if not self.items:
            return 1.0
        return self.scanned_count / len(self.items)

    # ── Item access ──────────────────────────────────────────────────────

    def get(self, index: int) -> ScanItem:
        return self.items[index]

    def mark_scanned(self, index: int, result: Any = None) -> None:
        item = self.items[index]
        item.status = ScanItemStatus.SCANNED
        item.result = result
        item.attempts += 1

    def mark_failed(self, index: int, reason: str | None = None) -> None:
        item = self.items[index]
        item.status = ScanItemStatus.FAILED
        item.rescan_reason = reason
        item.attempts += 1

    def mark_skipped(self, index: int) -> None:
        self.items[index].status = ScanItemStatus.SKIPPED

    # ── Rescan queue ─────────────────────────────────────────────────────

    def request_rescan(self, index: int, reason: str = '') -> None:
        """
        Queue item *index* for rescanning.

        Can be called at any time — during the forward scan (e.g. from the
        OCR assembler's future callback) or after the scan completes.
        """
        item = self.items[index]
        item.status = ScanItemStatus.NEEDS_RESCAN
        item.rescan_reason = reason
        if index not in self._rescan_queue:
            self._rescan_queue.append(index)
            logger.info(
                'Rescan requested for item %d (page=%d row=%d col=%d): %s',
                index, item.position.page, item.position.row,
                item.position.col, reason,
            )

    def pop_rescan(self) -> int | None:
        """
        Pop the next item from the rescan queue.

        Returns the item index, or ``None`` if the queue is empty.
        """
        if not self._rescan_queue:
            return None
        return self._rescan_queue.pop(0)

    @property
    def rescan_pending(self) -> int:
        return len(self._rescan_queue)

    def mark_rescanned(self, index: int, result: Any = None) -> None:
        item = self.items[index]
        item.status = ScanItemStatus.RESCANNED
        item.result = result
        item.attempts += 1

    # ── Iteration helpers ────────────────────────────────────────────────

    def pending_indices(self) -> list[int]:
        """Return indices of items still in PENDING state."""
        return [
            i for i, it in enumerate(self.items)
            if it.status == ScanItemStatus.PENDING
        ]

    def results(self) -> list[Any]:
        """Return non-None results in scan order."""
        return [
            it.result for it in self.items
            if it.result is not None
        ]
