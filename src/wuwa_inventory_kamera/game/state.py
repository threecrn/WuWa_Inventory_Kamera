"""
wuwa_inventory_kamera.game.state
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Serialisable snapshot of the game navigator's current logical position.

:class:`GameState` captures:

* Which screen the game is on (main-menu, inventory, unknown).
* Which inventory tab is active.
* The active sort order.
* The current grid page (1-based) and selected cell (0-based row/col).
* The game window geometry (size, monitor).

It is shared between:

* :mod:`~wuwa_inventory_kamera.cli.nav` — the navigation CLI stores and
  restores state across sessions via ``--state-in`` / ``--state-out``.
* The scanning workflows — :class:`~...scraping.scanning.scan_state.ScanSession`
  can embed a ``GameState`` for cycle-level reporting.
* Any Qt UI component that wants to expose or restore navigator state.

Usage::

    from wuwa_inventory_kamera.game.state import GameState

    # Snapshot from a live navigator
    state = GameState.from_navigator(nav, gw)
    print(state.to_json())

    # Restore from a previously saved snapshot (no game input sent)
    state = GameState.from_json(json_text)
    state.apply_to_navigator(nav)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------

@dataclass
class CellRef:
    """Zero-based ``(row, col)`` reference to a grid cell."""

    row: int
    col: int

    def to_dict(self) -> dict:
        return {'row': self.row, 'col': self.col}

    @classmethod
    def from_dict(cls, d: dict) -> 'CellRef':
        return cls(row=int(d['row']), col=int(d['col']))

    def __str__(self) -> str:
        return f'row={self.row} col={self.col}'


@dataclass
class WindowInfo:
    """Geometry snapshot of the game window at state-capture time."""

    found:   bool
    width:   int
    height:  int
    monitor: int

    def to_dict(self) -> dict:
        return {
            'found':   self.found,
            'width':   self.width,
            'height':  self.height,
            'monitor': self.monitor,
        }

    @classmethod
    def from_dict(cls, d: dict) -> 'WindowInfo':
        return cls(
            found=bool(d.get('found', False)),
            width=int(d.get('width', 1920)),
            height=int(d.get('height', 1080)),
            monitor=int(d.get('monitor', 1)),
        )


# ---------------------------------------------------------------------------
# GameState
# ---------------------------------------------------------------------------

@dataclass
class GameState:
    """
    Serialisable snapshot of the game navigator's logical state.

    Attributes
    ----------
    screen:
        Current logical screen.  One of:

        * ``'main-menu'`` — the game's main terminal / hub screen.
        * ``'inventory'`` — the backpack / inventory UI is open.
        * ``'unknown'`` — position not determined.

    tab:
        Active inventory tab value (``'echoes'``, ``'weapons'``,
        ``'devItems'``, ``'resources'``) or ``None`` when the inventory is
        closed.

    sort_order:
        Active sort order name in lowercase (e.g. ``'level_desc'``) or
        ``None`` if not set during this session.

    page:
        Current grid page, **1-based** (first page = 1).  ``None`` when
        the position has not yet been established.

    cell:
        Currently-selected grid cell as a zero-based ``(row, col)``
        :class:`CellRef`, or ``None`` if nothing is selected.

    window:
        Game window geometry at snapshot time.  Always present.
    """

    screen:     str                = 'unknown'
    tab:        Optional[str]      = None
    sort_order: Optional[str]      = None
    page:       Optional[int]      = None       # 1-based
    cell:       Optional[CellRef]  = None
    window:     WindowInfo         = field(
        default_factory=lambda: WindowInfo(False, 1920, 1080, 1)
    )

    # ── Serialisation ─────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            'screen':     self.screen,
            'tab':        self.tab,
            'sort_order': self.sort_order,
            'page':       self.page,
            'cell':       self.cell.to_dict() if self.cell else None,
            'window':     self.window.to_dict(),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: dict) -> 'GameState':
        cell   = CellRef.from_dict(d['cell'])      if d.get('cell')   else None
        window = WindowInfo.from_dict(d['window']) if d.get('window') else WindowInfo(False, 1920, 1080, 1)
        return cls(
            screen=    d.get('screen', 'unknown'),
            tab=       d.get('tab'),
            sort_order=d.get('sort_order'),
            page=      d.get('page'),
            cell=cell,
            window=window,
        )

    @classmethod
    def from_json(cls, text: str) -> 'GameState':
        return cls.from_dict(json.loads(text))

    # ── Integration helpers ───────────────────────────────────────────────

    @classmethod
    def from_navigator(cls, nav, gw) -> 'GameState':
        """
        Build a snapshot from a live
        :class:`~...game.navigation.GameNavigator` and
        :class:`~...game.screen.GameWindow`.

        The ``page`` and ``cell`` fields are left as ``None`` here — the
        :class:`~...cli.nav.NavCommandDispatcher` tracks those separately
        and fills them in before returning the state to callers.
        """
        w, h   = gw.size
        tab    = nav._current_tab.value    if nav._current_tab   else None
        sort_  = nav._current_sort.name.lower() if nav._current_sort else None
        screen = 'inventory' if nav._inventory_open else 'unknown'
        return cls(
            screen=screen,
            tab=tab,
            sort_order=sort_,
            page=None,
            cell=None,
            window=WindowInfo(found=gw.found, width=w, height=h, monitor=gw.monitor_index),
        )

    def apply_to_navigator(self, nav) -> None:
        """
        Restore this logical state to a navigator instance **without**
        sending any game input.

        Use this when the game is already in the state described by this
        snapshot — for example, when a ``--state-in`` JSON file is passed
        so the session can resume without redundant clicks.
        """
        from wuwa_inventory_kamera.game.navigation import InventoryTab, SortOrder

        if self.tab:
            try:
                nav._current_tab    = InventoryTab(self.tab)
                nav._inventory_open = (self.screen == 'inventory')
            except ValueError:
                pass

        if self.sort_order:
            try:
                nav._current_sort = SortOrder[self.sort_order.upper()]
            except KeyError:
                pass

    # ── Display ───────────────────────────────────────────────────────────

    def __str__(self) -> str:
        parts = [f'screen={self.screen}']
        if self.tab:
            parts.append(f'tab={self.tab}')
        if self.sort_order:
            parts.append(f'sort={self.sort_order}')
        if self.page is not None:
            parts.append(f'page={self.page}')
        if self.cell:
            parts.append(f'cell={self.cell}')
        parts.append(f'window={self.window.width}x{self.window.height}')
        return f'GameState({", ".join(parts)})'
