"""
wuwa_inventory_kamera.cli.nav
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Python API for scripted game navigation plus an interactive REPL.

Usage
-----
Run a Python script with all nav functions already in scope::

    wuwa-nav session.py

One-liner::

    wuwa-nav -c "focus_window(); switch_tab('echoes')"

Interactive REPL::

    wuwa-nav

Scripts are plain Python â€” use any language features naturally::

    # session.py
    focus_window()
    switch_tab('echoes')
    set_sort('rarity')

    for idx in range(10):
        goto_index(idx)
        result = ocr_roi('echo-card')
        print(result['lines'])

State round-tripping::

    # Save state at the end of a run
    wuwa-nav --state-out state.json session.py

    # Resume â€” skips redundant navigation
    wuwa-nav --state-in state.json session.py

Available nav functions
-----------------------
focus_window, open_inventory, close_inventory, switch_tab, set_sort,
goto_page, goto_cell, goto_index, read_count, sonata_down, sonata_up,
click, move, drag, scroll, key, hotkey, screenshot, state, in_menu, wait,
ocr_roi, snapshot, mouse_pos

Entry point
-----------
Registered as ``wuwa-nav`` console script in ``pyproject.toml``.
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import numpy as np

logger = logging.getLogger('wuwa.nav')

_REPL_ANSI_ESCAPE_RE = re.compile(
    r'\x1b(?:\[[0-?]*[ -/]*[@-~]|O[@-~]|\][^\x07]*(?:\x07|\x1b\\))'
)


# ---------------------------------------------------------------------------
# Named ROI aliases
# ---------------------------------------------------------------------------

_ROI_ALIASES: dict[str, str] = {
    'echo-card':        'echoes.echoCard',
    'echo-stats-name':  'echoes.fullStatsName',
    'echo-stats-value': 'echoes.fullStatsValue',
    'sonata':           'echoes.sonata',
    'sonata-icon':      'echoes.sonataIcon',
    'weapon-name':      'weapons.name',
    'weapon-level':     'weapons.level',
}


def _resolve_roi(layout, roi_name):
    """
    Resolve *roi_name* to a coordinate object, or ``None`` for ``'full'``.

    Accepted forms:

    * ``'full'``                     — full viewport (returns ``None``)
    * ``'echo-card'``, ``'sonata'``  — named alias (see ``_ROI_ALIASES``)
    * ``'echoes.echoCard'``          — dot-path into the layout tree
    * ``(x, y, w, h)``              — numeric tuple / list
    * ``'x,y,w,h'``                 — comma-separated number string
    """
    # Numeric tuple / list
    if isinstance(roi_name, (tuple, list)):
        if len(roi_name) != 4:
            raise NavError(
                f'Numeric ROI must have exactly 4 values (x, y, w, h), got {len(roi_name)}'
            )
        from ..game.game_roi import Coordinates
        x, y, w, h = roi_name
        return Coordinates(x, y, w, h)

    # Comma-separated string  "x,y,w,h"
    if isinstance(roi_name, str) and roi_name.count(',') == 3:
        parts = roi_name.split(',')
        try:
            coords = [float(p.strip()) for p in parts]
        except ValueError:
            pass  # not a numeric string — fall through to name resolution
        else:
            from ..game.game_roi import Coordinates
            return Coordinates(*coords)

    if roi_name == 'full':
        return None
    path = _ROI_ALIASES.get(roi_name, roi_name)
    obj = layout
    for part in path.split('.'):
        m = re.fullmatch(r'([^\[]+)((?:\[\d+\])+)?', part)
        if not m:
            raise NavError(
                f'ROI {roi_name!r} -> layout path {path!r}: '
                f'invalid path segment {part!r}'
            )
        attr, indices_str = m.group(1), m.group(2) or ''
        obj = getattr(obj, attr, None)
        if obj is None:
            raise NavError(
                f'ROI {roi_name!r} -> layout path {path!r}: '
                f'attribute {attr!r} not found'
            )
        for idx_str in re.findall(r'\[(\d+)\]', indices_str):
            idx = int(idx_str)
            try:
                obj = obj[idx]
            except (IndexError, TypeError) as exc:
                raise NavError(
                    f'ROI {roi_name!r} -> layout path {path!r}: '
                    f'index [{idx}] on {attr!r} failed: {exc}'
                ) from exc
    return obj


# ---------------------------------------------------------------------------
# Annotation helpers
# ---------------------------------------------------------------------------

_ANNOTATE_COLORS = [
    (0,   200,  0  ),  # green
    (0,   140, 255),  # orange
    (255,  0, 128),  # pink
    (255, 255,   0),  # cyan
    (0,    0, 255),  # red
    (200,   0, 200),  # purple
]


def _iter_coordinates(obj, prefix: str = ''):
    """
    Recursively yield ``(label, Coordinates)`` pairs from a
    :class:`~...game.screen_info.ScreenInfoObject`, dict, or list.

    Skips non-:class:`~...game.game_roi.Coordinates` scalars silently.
    """
    from ..game.game_roi import Coordinates as _Coords
    from ..game.screen_info import ScreenInfoObject as _SIO

    if isinstance(obj, _Coords):
        yield prefix, obj
    elif isinstance(obj, (_SIO, dict)):
        items = obj.__dict__.items() if isinstance(obj, _SIO) else obj.items()
        for key, val in items:
            child = f'{prefix}.{key}' if prefix else key
            yield from _iter_coordinates(val, child)
    elif isinstance(obj, list):
        for i, val in enumerate(obj):
            yield from _iter_coordinates(val, f'{prefix}[{i}]')


def _capture_roi(obj, roi_name):
    """
    Normalize a resolved ROI into a concrete capture rectangle.

    Section objects such as ``echoes`` and ``echoes.sonataIcon`` are reduced
    to the union of their nested rectangular coordinates. Zero-area helper
    coordinates such as click targets, scroll deltas, and local circle centers
    are ignored for capture purposes.
    """
    from ..game.game_roi import Coordinates as _Coords
    from ..game.screen_info import ScreenInfoObject as _SIO

    if obj is None or isinstance(obj, _Coords):
        return obj
    if not isinstance(obj, (_SIO, dict, list)):
        raise NavError(
            f'ROI {roi_name!r} resolved to unsupported capture type {type(obj).__name__!r}'
        )

    rectangles = [
        coord
        for _, coord in _iter_coordinates(obj)
        if isinstance(coord, _Coords) and coord.w > 0 and coord.h > 0
    ]
    if not rectangles:
        raise NavError(
            f'ROI {roi_name!r} resolved to a section with no rectangular capture regions; '
            'use a concrete leaf ROI instead'
        )

    min_x = min(coord.x for coord in rectangles)
    min_y = min(coord.y for coord in rectangles)
    max_x = max(coord.x + coord.w for coord in rectangles)
    max_y = max(coord.y + coord.h for coord in rectangles)
    return _Coords(min_x, min_y, max_x - min_x, max_y - min_y)


def _draw_single_coord(
    img: 'np.ndarray',
    label: str,
    coord,
    color: tuple,
    ox: int,
    oy: int,
) -> None:
    """Draw one :class:`~...game.game_roi.Coordinates` onto *img* (in-place)."""
    import cv2

    if coord is None:
        h_img, w_img = img.shape[:2]
        cv2.rectangle(img, (1, 1), (w_img - 2, h_img - 2), color, 2)
    elif coord.w > 0 and coord.h > 0:
        x1 = int(coord.x) - ox
        y1 = int(coord.y) - oy
        x2 = x1 + int(coord.w)
        y2 = y1 + int(coord.h)
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
        cv2.putText(
            img, label, (x1 + 3, y1 + 14),
            cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA,
        )
    else:
        cx = int(coord.x) - ox
        cy = int(coord.y) - oy
        arm = 12
        cv2.line(img, (cx - arm, cy), (cx + arm, cy), color, 2)
        cv2.line(img, (cx, cy - arm), (cx, cy + arm), color, 2)
        cv2.circle(img, (cx, cy), 3, color, -1)
        cv2.putText(
            img, label, (cx + arm + 3, cy + 5),
            cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA,
        )


def _draw_annotations(
    img: 'np.ndarray',
    layout,
    annotate: 'str | tuple | list',
    crop_origin: 'tuple[float, float]',
) -> 'np.ndarray':
    """
    Draw ROI boxes and click-target crosshairs onto *img* (BGR) and return
    a new annotated copy.

    Parameters
    ----------
    img:
        BGR image (as returned by ``cv2.cvtColor(rgb, COLOR_RGB2BGR)``).
    layout:
        The navigator layout object (passed to :func:`_resolve_roi`).
    annotate:
        A single ROI specifier or a list of specifiers — the same forms
        accepted by :meth:`NavSession.screenshot` for its *roi* parameter.
        Each resolved :class:`~...game.game_roi.Coordinates` is drawn as:

        * **Rectangle** when ``w > 0`` and ``h > 0``.
        * **Crosshair** when ``w == 0`` or ``h == 0`` (click target).
        * **Border** when the specifier resolves to ``'full'`` (``None``).

        When a specifier resolves to a **section dict** (e.g. ``'echoes'``,
        ``'weapons'``, ``'characters'``) rather than a single
        :class:`~...game.game_roi.Coordinates`, *all* Coordinates nested
        within that section are drawn recursively, each labelled with its
        full dotpath (e.g. ``echoes.echoCard``, ``echoes.sort.button``).
    crop_origin:
        ``(x, y)`` top-left of the captured region in game-viewport pixels.
        Annotation coordinates are shifted by this offset so they align with
        the cropped image.
    """
    from ..game.game_roi import Coordinates as _Coords
    from ..game.screen_info import ScreenInfoObject as _SIO

    img = img.copy()
    ox, oy = int(crop_origin[0]), int(crop_origin[1])
    names: list = annotate if isinstance(annotate, list) else [annotate]

    # Global color counter so each drawn element gets a distinct colour even
    # when multiple section dicts are expanded across one annotate call.
    color_idx = 0

    for name in names:
        label = name if isinstance(name, str) else repr(name)
        try:
            coord = _resolve_roi(layout, name)
        except NavError as exc:
            logger.warning('annotate: skipping %r — %s', name, exc)
            continue

        if isinstance(coord, (_SIO, dict)):
            # Section name — draw every nested Coordinates with its dotpath label
            for entry_label, entry_coord in _iter_coordinates(coord, label):
                color = _ANNOTATE_COLORS[color_idx % len(_ANNOTATE_COLORS)]
                color_idx += 1
                _draw_single_coord(img, entry_label, entry_coord, color, ox, oy)
        else:
            color = _ANNOTATE_COLORS[color_idx % len(_ANNOTATE_COLORS)]
            color_idx += 1
            _draw_single_coord(img, label, coord, color, ox, oy)

    return img


# ---------------------------------------------------------------------------
# Domain error
# ---------------------------------------------------------------------------

class NavError(Exception):
    """Raised by :class:`NavSession` methods on bad arguments or failed preconditions."""


# ---------------------------------------------------------------------------
# Public API exposed to nav scripts and the REPL
# ---------------------------------------------------------------------------

_SCRIPT_API: frozenset[str] = frozenset({
    'focus_window', 'open_inventory', 'close_inventory',
    'switch_tab', 'set_sort', 'set_sonata_filter',
    'goto_page', 'goto_cell', 'goto_index', 'read_count',
    'sonata_down', 'sonata_up',
    'click', 'move', 'drag', 'scroll', 'key', 'hotkey',
    'screenshot', 'state', 'in_menu', 'wait', 'ocr_roi',
    'snapshot', 'mouse_pos',
})


# ---------------------------------------------------------------------------
# NavSession
# ---------------------------------------------------------------------------

class NavSession:
    """
    Python API for controlling the WuWa game window.

    All methods interact with the live game via a
    :class:`~...game.navigation.GameNavigator`.  In *dry-run* mode they log
    their intent without sending any input.

    Typical nav script::

        focus_window()
        switch_tab('echoes')
        set_sort('rarity')
        goto_index(47)
        data = ocr_roi('echo-card')
        print(data['lines'])
    """

    def __init__(
        self: NavSession,
        nav,
        gw,
        screenshot_dir: Path | None = None,
        dry_run: bool = False,
    ) -> None:
        from ..game.navigation import GameNavigator

        self.nav: GameNavigator = nav
        self.gw  = gw
        self.screenshot_dir = screenshot_dir or Path('screenshots')
        self.dry_run = dry_run
        self._page_0: int = 0
        self._cell: Optional[tuple[int, int]] = None
        self._ocr_backend = None

    # â”€â”€ Script execution â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def run_script(self, path: Path, script_args: 'list[str] | None' = None) -> None:
        """Execute a Python script with all session methods in scope.

        ``script_args`` (the extra arguments that follow the script path on
        the command line) are made available to the script as ``sys.argv``:
        ``sys.argv[0]`` is the resolved script path and ``sys.argv[1:]`` are
        the forwarded arguments.  The original ``sys.argv`` is restored after
        the script exits.
        """
        old_argv = sys.argv
        try:
            sys.argv = [str(path.resolve())] + (script_args or [])
            env = self._script_namespace()
            env['__file__'] = str(path.resolve())
            exec(compile(path.read_text('utf-8'), str(path), 'exec'), env)  # noqa: S102
        finally:
            sys.argv = old_argv

    def repl(self, auto_focus: bool = True) -> None:
        """
        Start an interactive Python REPL with all session methods in scope.

        Parameters
        ----------
        auto_focus:
            When *True* (default), the game window is automatically focused
            before every command and the terminal is restored afterwards.
            This removes the need to type ``focus_window()`` manually before
            each interactive command.
        """
        import code as _code
        af_line = (
            'Auto-focus ON  - game is focused before each command, '
            'terminal restored after.\n'
            if auto_focus else
            'Auto-focus OFF - call focus_window() manually as needed.\n'
        )
        banner = (
            'WuWa Navigator - Python REPL\n'
            + af_line +
            'Nav functions are already in scope.  '
            'Type help(focus_window) for docs.\n'
            'Press Ctrl-D to quit. If plain Windows input is active, use Ctrl-Z.\n'
        )
        ns = self._script_namespace()
        if auto_focus:
            console = _AutoFocusConsole(self, ns)
            console.interact(banner=banner, exitmsg='')
        else:
            readfunc = _build_repl_readfunc()
            _code.interact(local=ns, banner=banner, exitmsg='', readfunc=readfunc)

    def _script_namespace(self) -> dict:
        """Build the globals dict exposed to scripts and the REPL."""
        import builtins
        ns: dict = {name: getattr(self, name) for name in _SCRIPT_API}
        ns.update({'__builtins__': builtins, 'Path': Path, 'json': json})
        return ns

    # â”€â”€ State snapshot â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def snapshot(self):
        """Return a :class:`~...game.state.GameState` reflecting current state."""
        from ..game.state import CellRef, GameState
        s = GameState.from_navigator(self.nav, self.gw)
        s.page = self._page_0 + 1
        if self._cell is not None:
            s.cell = CellRef(self._cell[0], self._cell[1])
        return s

    # â”€â”€ Navigation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def focus_window(self, wait: float | None = 0.25) -> None:
        """Bring the game window to the foreground."""
        logger.info('focus-window')
        if not self.dry_run:
            if not self.gw.activate():
                raise NavError('focus_window: game window not found')
            if wait:
                time.sleep(wait)

    def open_inventory(self, wait: float | None = None) -> None:
        """Press the inventory keybind."""
        logger.info('open-inventory')
        if not self.dry_run:
            kw = {} if wait is None else {'wait': wait}
            self.nav.open_inventory(**kw)
        self._page_0 = 0
        self._cell   = None

    def close_inventory(self, wait: float | None = None) -> None:
        """Press Esc to close the inventory."""
        logger.info('close-inventory')
        if not self.dry_run:
            kw = {} if wait is None else {'wait': wait}
            self.nav.close_inventory(**kw)
        self._page_0 = 0
        self._cell   = None

    def switch_tab(self, tab: str, wait: float | None = None) -> None:
        """Switch to an inventory tab.  tab: echoes | weapons | devItems | resources"""
        logger.info('switch-tab %s', tab)
        if not self.dry_run:
            from ..game.navigation import InventoryTab
            try:
                t = InventoryTab(tab)
            except ValueError:
                valid = ', '.join(v.value for v in InventoryTab)
                raise NavError(f'Unknown tab {tab!r}. Valid: {valid}')
            kw = {} if wait is None else {'wait': wait}
            self.nav.switch_tab(t, **kw)
        self._page_0 = 0
        self._cell   = None

    def set_sort(self, order: str, wait: float | None = None) -> None:
        """Set inventory sort order.  order: level | rarity | time_added | tuning_status | discarded_first"""
        logger.info('set-sort %s', order)
        if not self.dry_run:
            from ..game.navigation import SortOrder
            try:
                o = SortOrder[order.upper()]
            except KeyError:
                valid = ', '.join(s.name.lower() for s in SortOrder)
                raise NavError(f'Unknown sort order {order!r}. Valid: {valid}')
            kw = {} if wait is None else {'wait': wait}
            self.nav.set_sort_order(o, **kw)

    def set_sonata_filter(self, sonata: str | None = None) -> int | None:
        """Filter the echoes list by sonata.  sonata: canonical sonata slug, or None/'off' to clear.

        Returns the number of echoes matching the filter as shown in the
        dropdown, or ``None`` if the count could not be read.
        """
        logger.info('set-sonata-filter %s', sonata)
        if not self.dry_run:
            return self.nav.set_sonata_filter(sonata)
        return None

    def goto_page(self, n: int, wait: float | None = None) -> None:
        """Scroll to page *n* (1-based)."""
        if n < 1:
            raise NavError('goto_page: page number must be >= 1')
        target_0 = n - 1
        logger.info('goto-page %d', n)
        if not self.dry_run:
            kw = {} if wait is None else {'wait': wait}
            self.nav.scroll_to_page(target_0, self._page_0, **kw)
        self._page_0 = target_0
        self._cell   = None

    def goto_cell(self, row: int, col: int, wait: float | None = None) -> None:
        """Click grid cell at 0-based *row*, *col*."""
        logger.info('goto-cell row=%d col=%d', row, col)
        if not self.dry_run:
            kw = {} if wait is None else {'wait': wait}
            self.nav.click_grid_cell(row, col, **kw)
        self._cell = (row, col)

    def goto_index(self, n: int, scroll_wait: float | None = None, click_wait: float | None = None) -> None:
        """Navigate to a 0-based scan index (page-aware)."""
        if n < 0:
            raise NavError('goto_index: index must be >= 0')
        logger.info('goto-index %d', n)
        from ..game.navigation import GRID_COLS
        from ..scraping.scanning.scan_state import GridPosition
        pos = GridPosition.from_index(n, GRID_COLS)
        if not self.dry_run:
            scroll_kw = {} if scroll_wait is None else {'wait': scroll_wait}
            self.nav.scroll_to_page(pos.page, self._page_0, **scroll_kw)
            click_kw = {} if click_wait is None else {'wait': click_wait}
            self.nav.click_grid_cell(pos.row, pos.col, **click_kw)
        self._page_0 = pos.page
        self._cell   = (pos.row, pos.col)

    def read_count(self) -> dict:
        """Return ``{"items": N, "pages": N}``."""
        logger.info('read-count')
        if self.dry_run:
            return {'items': 0, 'pages': 0}
        count, pages = self.nav.read_item_count()
        return {'items': count, 'pages': pages}

    # â”€â”€ Echo detail â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def sonata_down(self, wait: float | None = None) -> None:
        """Scroll the echo panel to reveal the sonata section."""
        logger.info('sonata-down')
        if not self.dry_run:
            self.nav.scroll_to_sonata()
            if wait:
                time.sleep(wait)

    def sonata_up(self, wait: float | None = None) -> None:
        """Scroll the echo panel back from the sonata section."""
        logger.info('sonata-up')
        if not self.dry_run:
            self.nav.scroll_back_from_sonata()
            if wait:
                time.sleep(wait)

    # â”€â”€ Raw input â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def click(self, x: float, y: float, wait: float | None = None) -> None:
        """Left-click at game-relative coords."""
        logger.info('click %.1f %.1f', x, y)
        if not self.dry_run:
            kw = {} if wait is None else {'wait': wait}
            self.nav.ctrl.click(x, y, **kw)

    def move(self, x: float, y: float, wait: float | None = None) -> None:
        """Move cursor to game-relative coords."""
        logger.info('move %.1f %.1f', x, y)
        if not self.dry_run:
            kw = {} if wait is None else {'wait': wait}
            self.nav.ctrl.move(x, y, **kw)

    def mouse_pos(self) -> dict:
        """
        Return the current mouse cursor position relative to the game window.

        Returns a dict ``{"x": <float>, "y": <float>}`` with game-relative
        coordinates (same coordinate space used by :meth:`move` and
        :meth:`click`).  Returns ``{"x": None, "y": None}`` in dry-run mode.
        """
        logger.info('mouse-pos')
        if self.dry_run:
            return {'x': None, 'y': None}
        abs_x, abs_y = self.nav.ctrl._w32.GetCursorPos()
        ox, oy = self.nav.ctrl._origin()
        return {
            'x': abs_x - ox,
            'y': abs_y - oy,
        }

    def drag(self, x1: float, y1: float, x2: float, y2: float,
             wait: float | None = None) -> None:
        """Hold left button at (*x1*, *y1*), move to (*x2*, *y2*), release."""
        logger.info('drag %.1f %.1f -> %.1f %.1f', x1, y1, x2, y2)
        if not self.dry_run:
            kw = {} if wait is None else {'wait': wait}
            self.nav.ctrl.drag(x1, y1, x2, y2, **kw)

    def scroll(self, amount: float, wait: float | None = None) -> None:
        """Scroll the mouse wheel.  Positive = down, negative = up."""
        logger.info('scroll %.2f', amount)
        if not self.dry_run:
            kw = {} if wait is None else {'wait': wait}
            self.nav.ctrl.scroll(amount, **kw)

    def key(self, name: str, wait: float | None = None) -> None:
        """Press a single key (e.g. ``'esc'``, ``'b'``, ``'f5'``)."""
        logger.info('key %s', name)
        if not self.dry_run:
            kw = {} if wait is None else {'wait': wait}
            self.nav.ctrl.press_key(name, **kw)

    def hotkey(self, *keys: str, wait: float | None = None) -> None:
        """Press a key combination (e.g. ``hotkey('ctrl', 'v')``)."""
        if len(keys) < 2:
            raise NavError('hotkey requires at least 2 key names')
        logger.info('hotkey %s', ' '.join(keys))
        if not self.dry_run:
            kw = {} if wait is None else {'wait': wait}
            self.nav.ctrl.hotkey(*keys, **kw)

    # â”€â”€ Screenshot â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def screenshot(
        self,
        roi: 'str | tuple[float, float, float, float]' = 'full',
        out: 'str | Path | None' = None,
        *,
        as_image: bool = False,
        annotate: 'str | tuple[float, float, float, float] | list | None' = None,
    ) -> 'dict | np.ndarray | None':
        """
        Capture a screenshot.

        Parameters
        ----------
        roi:
            ``'full'``, a named ROI (``'echo-card'``, ``'sonata'``, …), a
            dot-path into the layout tree (e.g. ``'echoes.echoCard'``),
            a numeric tuple ``(x, y, w, h)``, or a comma-separated string
            ``'x,y,w,h'``. Section names such as ``'echoes'`` are captured as
            the bounding box of their nested rectangular ROIs.
        out:
            Output file path.  Auto-generated under ``screenshot_dir`` if
            omitted.  Ignored when *as_image* is ``True``.
        as_image:
            When ``True``, skip file I/O and return the captured region as a
            BGR ``np.ndarray`` (same format as ``cv2.imread``).
        annotate:
            One ROI specifier or a list of specifiers to draw on top of the
            captured image.  Accepts the same forms as *roi*: named aliases
            (``'echo-card'``), dot-paths (``'echoes.echoCard'``), numeric
            tuples ``(x, y, w, h)``, or comma-separated strings.

            Each resolved coordinate is drawn as:

            * **Rectangle** — when ``w > 0`` and ``h > 0``.
            * **Crosshair** — when ``w == 0`` or ``h == 0`` (click target).

            Coordinates are automatically offset to align with the cropped
            region when *roi* is not ``'full'``.

        Returns a dict with ``saved``, ``roi``, and ``shape`` keys unless
        *as_image* is ``True``, in which case a BGR ``np.ndarray`` is returned
        (or ``None`` in dry-run mode).
        """
        import cv2
        from ..game.screen import capture, capture_region

        if as_image:
            logger.info('screenshot roi=%s annotate=%s (as_image)', roi, annotate)
            if self.dry_run:
                return None
            layout  = self.nav.layout
            roi_obj = _resolve_roi(layout, roi)
            capture_roi = _capture_roi(roi_obj, roi)
            bgr = (
                capture(self.gw) if roi_obj is None
                else capture_region(self.gw, capture_roi)
            )
            if annotate is not None:
                origin = (0.0, 0.0) if capture_roi is None else (capture_roi.x, capture_roi.y)
                bgr = _draw_annotations(bgr, layout, annotate, origin)
            return bgr

        out_path = Path(out) if out is not None else None
        if out_path is None:
            ts = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            out_path = self.screenshot_dir / f'screenshot_{ts}.png'

        logger.info('screenshot roi=%s annotate=%s out=%s', roi, annotate, out_path)
        if self.dry_run:
            return {'saved': str(out_path), 'roi': roi, 'dry_run': True}

        layout  = self.nav.layout
        roi_obj = _resolve_roi(layout, roi)
        capture_roi = _capture_roi(roi_obj, roi)
        bgr = (
            capture(self.gw) if roi_obj is None
            else capture_region(self.gw, capture_roi)
        )
        if annotate is not None:
            origin = (0.0, 0.0) if capture_roi is None else (capture_roi.x, capture_roi.y)
            bgr = _draw_annotations(bgr, layout, annotate, origin)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out_path), bgr)
        return {'saved': str(out_path), 'roi': roi, 'shape': list(bgr.shape)}

    # â”€â”€ State / inspection â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def state(self) -> str:
        """Return the current GameState as a JSON string."""
        return self.snapshot().to_json()

    def in_menu(self) -> dict:
        """OCR-check whether the main-menu screen is currently visible."""
        logger.info('in-menu')
        if self.dry_run:
            return {'in_menu': False}
        return {'in_menu': self.nav.is_in_main_menu()}

    def wait(self, seconds: float) -> None:
        """Sleep for *seconds* seconds."""
        logger.info('wait %.2fs', seconds)
        if not self.dry_run:
            time.sleep(seconds)

    # â”€â”€ OCR â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def ocr_roi(self, roi: 'str | tuple[float, float, float, float]' = 'full', *,
                mode: "Literal['thorough', 'single_line'] | None" = None) -> dict:
        """
        Capture the game screen, crop to *roi*, and run OCR.

        Parameters
        ----------
        roi:
            ROI name, dot-path, numeric tuple ``(x, y, w, h)``, or
            comma-separated string ``'x,y,w,h'`` — same values accepted
            as :meth:`screenshot`, including section names captured as the
            union of nested rectangular ROIs.
        mode:
            ``None`` (default) — normal multi-line detection.
            ``'thorough'`` — multi-pass detection (higher recall, ~3× slower).
            ``'single_line'`` — normal detection, all results merged into one line.

        Returns a dict::

            {"roi": "echo-card", "lines": [{"text": "…", "conf": 0.97}, …]}
        """
        logger.info('ocr-roi roi=%s mode=%s', roi, mode)
        if self.dry_run:
            return {'roi': roi, 'lines': []}

        from ..game.screen import capture, capture_region
        from ..scraping.ocr._rapidocr import RapidOcrBackend

        layout  = self.nav.layout
        roi_obj = _resolve_roi(layout, roi)
        capture_roi = _capture_roi(roi_obj, roi)
        crop = (
            capture(self.gw) if roi_obj is None
            else capture_region(self.gw, capture_roi)
        )
        if self._ocr_backend is None:
            self._ocr_backend = RapidOcrBackend()
        recognize = (
            self._ocr_backend.thorough_recognize if mode == 'thorough'
            else self._ocr_backend.recognize
        )
        results = recognize(crop)
        if mode == 'single_line':
            if results:
                results = sorted(results, key=lambda r: r[0][0][0] if r[0] is not None else 0)
                texts = [t for _, t, _ in results]
                confs = [float(c) for _, _, c in results]
                merged_text = ' '.join(texts)
                merged_conf = sum(confs) / len(confs)
            else:
                merged_text, merged_conf = '', 0.0
            return {
                'roi': roi,
                'lines': [{'text': merged_text, 'conf': round(merged_conf, 4)}],
            }
        return {
            'roi': roi,
            'lines': [
                {'text': t, 'conf': round(float(c), 4)}
                for _, t, c in results
            ],
        }


# ---------------------------------------------------------------------------
# Auto-focus REPL console — helpers
# ---------------------------------------------------------------------------

def _setup_readline() -> None:
    """
    Enable readline history and key-bindings for the interactive REPL.

    Tries the stdlib ``readline`` module first (available on Linux/macOS),
    then falls back to ``pyreadline3`` (Windows).  When neither is present
    the REPL still works but has no arrow-key history and Ctrl+D is
    inoperative (use Ctrl+Z on Windows).
    """
    try:
        import readline
    except ImportError:
        try:
            import pyreadline3  # noqa: F401 — self-registers as readline on import
            import readline
        except ImportError:
            return
    parse_and_bind = getattr(readline, 'parse_and_bind', None)
    if callable(parse_and_bind):
        parse_and_bind('tab: complete')


def _build_repl_readfunc() -> 'Callable[[str], str]':
    """
    Return the line-reader used by the interactive REPL.

    On Windows terminals such as Git Bash, the standard ``input()`` path has
    no readline editing, and ``pyreadline3`` only helps for native console
    hosts. ``prompt_toolkit`` handles VT-style terminals directly, so prefer
    it whenever stdin/stdout are attached to a TTY. Otherwise fall back to
    ``input()`` with best-effort readline setup.
    """
    stdin = sys.stdin
    stdout = sys.stdout
    if stdin is None:
        return input

    stdin_is_tty = bool(getattr(stdin, 'isatty', lambda: False)())
    stdout_is_tty = bool(getattr(stdout, 'isatty', lambda: False)()) if stdout is not None else False
    term = os.environ.get('TERM', '').lower()
    is_windows_pty = bool(
        sys.platform.startswith('win')
        and (os.environ.get('MSYSTEM') or os.environ.get('MINTTY_SHORTCUT') or term.startswith(('xterm', 'msys', 'cygwin')))
    )

    if not stdin_is_tty:
        return input

    base_readfunc: 'Callable[[str], str]'
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import InMemoryHistory
    except ImportError:
        _setup_readline()
        base_readfunc = input
    else:
        if not stdout_is_tty and not is_windows_pty:
            _setup_readline()
            base_readfunc = input
        else:
            session = PromptSession(history=InMemoryHistory())
            base_readfunc = session.prompt

    def readfunc(prompt: str) -> str:
        line = base_readfunc(prompt)
        line = _REPL_ANSI_ESCAPE_RE.sub('', line)
        return ''.join(
            ch for ch in line
            if ch == '\t' or (' ' <= ch and ch != '\x7f')
        )

    return readfunc


# ---------------------------------------------------------------------------
# Auto-focus REPL console
# ---------------------------------------------------------------------------

class _AutoFocusConsole:
    """
    An :class:`code.InteractiveConsole` wrapper that automatically focuses
    the game window before executing each command and restores focus to the
    terminal window afterwards.

    The terminal HWND is captured via ``GetForegroundWindow()`` at
    construction time, while the terminal still has focus.  Focus is
    restored via ``AttachThreadInput`` + ``SetForegroundWindow``, which
    avoids the Alt-key injection side-effect of the standard foreground-lock
    workaround and therefore leaves the console input buffer clean.
    """

    def __init__(self, session: NavSession, local: dict) -> None:
        import code as _code
        import ctypes
        self._session = session
        self._console = _code.InteractiveConsole(local)
        self._console.runcode = self._runcode  # type: ignore[method-assign]
        # Capture terminal HWND now while we still have focus.
        self._terminal_hwnd: int = ctypes.windll.user32.GetForegroundWindow()
        self._readfunc = _build_repl_readfunc()

    def interact(self, *, banner: str, exitmsg: str) -> None:
        """
        Custom REPL loop providing history (via readline) and Ctrl+D / Ctrl+Z support.

        Delegates compilation and execution to the underlying
        :class:`code.InteractiveConsole`, which calls through to our
        monkey-patched :meth:`_runcode` for focus management.
        """
        sys.stdout.write(banner)
        ps1 = getattr(sys, 'ps1', '>>> ')
        ps2 = getattr(sys, 'ps2', '... ')
        more = False
        while True:
            prompt = ps2 if more else ps1
            try:
                line = self._readfunc(prompt)
            except EOFError:          # Ctrl+D (readline) or Ctrl+Z+Enter (Windows)
                sys.stdout.write('\n')
                break
            except KeyboardInterrupt:
                sys.stdout.write('\nKeyboardInterrupt\n')
                more = False
                self._console.resetbuffer()
                continue
            more = self._console.push(line)
        if exitmsg:
            sys.stdout.write(exitmsg)

    def _restore_terminal_focus(self) -> None:
        if not self._terminal_hwnd:
            return
        try:
            import ctypes
            user32   = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            # Attach our input thread to the game window's thread temporarily
            # so that SetForegroundWindow succeeds without injecting Alt keys.
            fg_hwnd = user32.GetForegroundWindow()
            if fg_hwnd:
                fg_tid = user32.GetWindowThreadProcessId(fg_hwnd, None)
                my_tid = kernel32.GetCurrentThreadId()
                user32.AttachThreadInput(fg_tid, my_tid, True)
                try:
                    user32.SetForegroundWindow(self._terminal_hwnd)
                finally:
                    user32.AttachThreadInput(fg_tid, my_tid, False)
        except Exception:
            pass

    def _runcode(self, code_obj) -> None:  # signature matches InteractiveConsole.runcode
        import code as _code
        try:
            self._session.focus_window()
        except NavError as exc:
            logger.warning('auto-focus: %s', exc)
        try:
            _code.InteractiveConsole.runcode(self._console, code_obj)
        finally:
            self._restore_terminal_focus()


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    fmt   = logging.Formatter('%(asctime)s | %(levelname)-8s | %(name)s | %(message)s')
    root  = logging.getLogger()
    root.setLevel(level)
    if root.handlers:
        for h in root.handlers:
            h.setLevel(level)
            if sys.platform.startswith('win') and isinstance(h, logging.StreamHandler):
                stream = getattr(h, 'stream', None)
                if isinstance(stream, io.TextIOWrapper):
                    stream.reconfigure(encoding='utf-8', errors='replace')
    else:
        if sys.platform.startswith('win') and isinstance(sys.stderr, io.TextIOWrapper):
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(fmt)
        root.addHandler(handler)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog='wuwa-nav',
        description=(
            'Navigate and control the WuWa game window.\n\n'
            'Usage modes:\n'
            '  Script:      wuwa-nav session.py\n'
            '  One-liner:   wuwa-nav -c "focus_window(); switch_tab(\'echoes\')"\n'
            '  Interactive: wuwa-nav\n\n'
            'Scripts are plain Python â€” all nav session methods are pre-imported.\n'
            'Use any Python control flow: if/else, for, while, try/except.'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        'script', nargs='?', metavar='SCRIPT',
        help='Python nav script to run (session methods are in scope).',
    )
    parser.add_argument(
        '-c', dest='oneliner', metavar='CODE',
        help='Execute a Python one-liner (session methods are in scope).',
    )
    parser.add_argument(
        '--state-in', metavar='FILE',
        help='Load initial navigator state from a JSON file.',
    )
    parser.add_argument(
        '--state-out', metavar='FILE',
        help='Write final state JSON to FILE instead of stdout.',
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Log commands without sending any game input.',
    )
    parser.add_argument(
        '--inventory-key', default='b', metavar='KEY',
        help='Keybind that opens the inventory (default: b).',
    )
    parser.add_argument(
        '--screenshot-dir', default='screenshots', metavar='DIR',
        help='Directory for auto-named screenshots (default: screenshots).',
    )
    parser.add_argument(
        '--no-auto-focus', action='store_true',
        help=(
            'Disable automatic game-window focus in interactive mode.  '
            'By default the game is focused before each REPL command and '
            'the terminal is restored afterwards.'
        ),
    )
    parser.add_argument(
        '--log-level', default='INFO',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        help='Logging verbosity (default: INFO).',
    )
    parser.add_argument(
        '--windowed', action='store_true', default=False,
        help='Enable windowed-mode capture (PrintWindow).',
    )

    args, _script_argv = parser.parse_known_args()
    _configure_logging(args.log_level)

    if args.script and args.oneliner:
        parser.error('Specify at most one of: script, -c.')

    # Delayed imports so --help is fast
    from ..game.input_controller import InputController
    from ..game.navigation import GameNavigator
    from ..game.screen import GameWindow
    from ..game.state import GameState

    gw = GameWindow(windowed=args.windowed)
    if not gw.found and not args.dry_run:
        print('Error: game window not found.  Is the game running?', file=sys.stderr)
        sys.exit(1)

    get_origin = (lambda: gw.client_origin) if args.windowed else None
    ctrl: InputController = InputController(gw.monitor_index if gw.found else 1, get_origin=get_origin)
    nav: GameNavigator = GameNavigator(ctrl, gw, inventory_key=args.inventory_key)

    session = NavSession(
        nav=nav,
        gw=gw,
        screenshot_dir=Path(args.screenshot_dir),
        dry_run=args.dry_run,
    )

    if args.state_in:
        try:
            text    = Path(args.state_in).read_text(encoding='utf-8')
            initial = GameState.from_json(text)
            initial.apply_to_navigator(nav)
            if initial.page is not None:
                session._page_0 = initial.page - 1
            if initial.cell is not None:
                session._cell = (initial.cell.row, initial.cell.col)
            logger.info('Restored state from %s', args.state_in)
        except Exception as exc:
            logger.warning('Could not load state from %s: %s', args.state_in, exc)

    if args.script:
        session.run_script(Path(args.script), _script_argv)
    elif args.oneliner:
        env = session._script_namespace()
        exec(compile(args.oneliner, '<-c>', 'exec'), env)  # noqa: S102
    else:
        session.repl(auto_focus=not args.no_auto_focus)
        return  # final state irrelevant for interactive sessions

    final_json = session.snapshot().to_json()
    if args.state_out:
        out = Path(args.state_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(final_json, encoding='utf-8')
        logger.info('State written to %s', args.state_out)
    else:
        print(final_json)


if __name__ == '__main__':
    main()
