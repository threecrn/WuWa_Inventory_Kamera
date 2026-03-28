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
click, move, scroll, key, hotkey, screenshot, state, in_menu, wait,
ocr_roi, snapshot

Entry point
-----------
Registered as ``wuwa-nav`` console script in ``pyproject.toml``.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger('wuwa.nav')


# ---------------------------------------------------------------------------
# Named ROI aliases
# ---------------------------------------------------------------------------

_ROI_ALIASES: dict[str, str] = {
    'echo-card':        'echoes.echoCard',
    'echo-stats-name':  'echoes.fullStatsName',
    'echo-stats-value': 'echoes.fullStatsValue',
    'sonata':           'echoes.sonata',
    'weapon-name':      'weapons.name',
    'weapon-level':     'weapons.level',
}


def _resolve_roi(layout, roi_name: str):
    """Resolve *roi_name* to a coordinate object, or ``None`` for ``'full'``."""
    if roi_name == 'full':
        return None
    path = _ROI_ALIASES.get(roi_name, roi_name)
    obj = layout
    for part in path.split('.'):
        obj = getattr(obj, part, None)
        if obj is None:
            raise NavError(
                f'ROI {roi_name!r} â†’ layout path {path!r}: '
                f'attribute {part!r} not found'
            )
    return obj


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
    'switch_tab', 'set_sort',
    'goto_page', 'goto_cell', 'goto_index', 'read_count',
    'sonata_down', 'sonata_up',
    'click', 'move', 'scroll', 'key', 'hotkey',
    'screenshot', 'state', 'in_menu', 'wait', 'ocr_roi',
    'snapshot',
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
        self,
        nav,
        gw,
        screenshot_dir: Path | None = None,
        dry_run: bool = False,
    ) -> None:
        self.nav = nav
        self.gw  = gw
        self.screenshot_dir = screenshot_dir or Path('screenshots')
        self.dry_run = dry_run
        self._page_0: int = 0
        self._cell: Optional[tuple[int, int]] = None
        self._ocr_backend = None

    # â”€â”€ Script execution â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def run_script(self, path: Path) -> None:
        """Execute a Python script with all session methods in scope."""
        env = self._script_namespace()
        exec(compile(path.read_text('utf-8'), str(path), 'exec'), env)  # noqa: S102

    def repl(self) -> None:
        """Start an interactive Python REPL with all session methods in scope."""
        import code as _code
        banner = (
            'WuWa Navigator â€” Python REPL\n'
            'Nav functions are already in scope.  '
            'Type help(focus_window) for docs.\n'
            'Press Ctrl-D (Ctrl-Z on Windows) to quit.\n'
        )
        _code.interact(local=self._script_namespace(), banner=banner, exitmsg='')

    def _script_namespace(self) -> dict:
        """Build the globals dict exposed to scripts and the REPL."""
        import builtins
        ns: dict = {name: getattr(self, name) for name in _SCRIPT_API}
        ns.update({'__builtins__': builtins, 'Path': Path, 'json': json})
        return ns

    # â”€â”€ State snapshot â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def snapshot(self):
        """Return a :class:`~...game.state.GameState` reflecting current state."""
        from wuwa_inventory_kamera.game.state import CellRef, GameState
        s = GameState.from_navigator(self.nav, self.gw)
        s.page = self._page_0 + 1
        if self._cell is not None:
            s.cell = CellRef(self._cell[0], self._cell[1])
        return s

    # â”€â”€ Navigation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def focus_window(self) -> None:
        """Bring the game window to the foreground."""
        logger.info('focus-window')
        if not self.dry_run:
            if not self.gw.activate():
                raise NavError('focus_window: game window not found')

    def open_inventory(self) -> None:
        """Press the inventory keybind."""
        logger.info('open-inventory')
        if not self.dry_run:
            self.nav.open_inventory()
        self._page_0 = 0
        self._cell   = None

    def close_inventory(self) -> None:
        """Press Esc to close the inventory."""
        logger.info('close-inventory')
        if not self.dry_run:
            self.nav.close_inventory()
        self._page_0 = 0
        self._cell   = None

    def switch_tab(self, tab: str) -> None:
        """Switch to an inventory tab.  tab: echoes | weapons | devItems | resources"""
        logger.info('switch-tab %s', tab)
        if not self.dry_run:
            from wuwa_inventory_kamera.game.navigation import InventoryTab
            try:
                t = InventoryTab(tab)
            except ValueError:
                valid = ', '.join(v.value for v in InventoryTab)
                raise NavError(f'Unknown tab {tab!r}. Valid: {valid}')
            self.nav.switch_tab(t)
        self._page_0 = 0
        self._cell   = None

    def set_sort(self, order: str) -> None:
        """Set inventory sort order.  order: level | rarity | time_added | tuning_status | discarded_first"""
        logger.info('set-sort %s', order)
        if not self.dry_run:
            from wuwa_inventory_kamera.game.navigation import SortOrder
            try:
                o = SortOrder[order.upper()]
            except KeyError:
                valid = ', '.join(s.name.lower() for s in SortOrder)
                raise NavError(f'Unknown sort order {order!r}. Valid: {valid}')
            self.nav.set_sort_order(o)

    def goto_page(self, n: int) -> None:
        """Scroll to page *n* (1-based)."""
        if n < 1:
            raise NavError('goto_page: page number must be >= 1')
        target_0 = n - 1
        logger.info('goto-page %d', n)
        if not self.dry_run:
            self.nav.scroll_to_page(target_0, self._page_0)
        self._page_0 = target_0
        self._cell   = None

    def goto_cell(self, row: int, col: int) -> None:
        """Click grid cell at 0-based *row*, *col*."""
        logger.info('goto-cell row=%d col=%d', row, col)
        if not self.dry_run:
            self.nav.click_grid_cell(row, col)
        self._cell = (row, col)

    def goto_index(self, n: int) -> None:
        """Navigate to a 0-based scan index (page-aware)."""
        if n < 0:
            raise NavError('goto_index: index must be >= 0')
        logger.info('goto-index %d', n)
        from wuwa_inventory_kamera.game.navigation import GRID_COLS
        from wuwa_inventory_kamera.scraping.scanning.scan_state import GridPosition
        pos = GridPosition.from_index(n, GRID_COLS)
        if not self.dry_run:
            self.nav.scroll_to_page(pos.page, self._page_0)
            self.nav.click_grid_cell(pos.row, pos.col)
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

    def sonata_down(self) -> None:
        """Scroll the echo panel to reveal the sonata section."""
        logger.info('sonata-down')
        if not self.dry_run:
            self.nav.scroll_to_sonata()

    def sonata_up(self) -> None:
        """Scroll the echo panel back from the sonata section."""
        logger.info('sonata-up')
        if not self.dry_run:
            self.nav.scroll_back_from_sonata()

    # â”€â”€ Raw input â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def click(self, x: float, y: float) -> None:
        """Left-click at game-relative coords."""
        logger.info('click %.1f %.1f', x, y)
        if not self.dry_run:
            self.nav.ctrl.click(x, y)

    def move(self, x: float, y: float) -> None:
        """Move cursor to game-relative coords."""
        logger.info('move %.1f %.1f', x, y)
        if not self.dry_run:
            self.nav.ctrl.move(x, y)

    def scroll(self, amount: float) -> None:
        """Scroll the mouse wheel.  Positive = down, negative = up."""
        logger.info('scroll %.2f', amount)
        if not self.dry_run:
            self.nav.ctrl.scroll(amount)

    def key(self, name: str) -> None:
        """Press a single key (e.g. ``'esc'``, ``'b'``, ``'f5'``)."""
        logger.info('key %s', name)
        if not self.dry_run:
            self.nav.ctrl.press_key(name)

    def hotkey(self, *keys: str) -> None:
        """Press a key combination (e.g. ``hotkey('ctrl', 'v')``)."""
        if len(keys) < 2:
            raise NavError('hotkey requires at least 2 key names')
        logger.info('hotkey %s', ' '.join(keys))
        if not self.dry_run:
            self.nav.ctrl.hotkey(*keys)

    # â”€â”€ Screenshot â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def screenshot(self, roi: str = 'full', out: str | Path | None = None) -> dict:
        """
        Capture and save a screenshot.

        Parameters
        ----------
        roi:
            ``'full'``, a named ROI (``'echo-card'``, ``'sonata'``, â€¦), or a
            dot-path into the layout tree (e.g. ``'echoes.echoCard'``).
        out:
            Output file path.  Auto-generated under ``screenshot_dir`` if omitted.

        Returns a dict with ``saved``, ``roi``, and ``shape`` keys.
        """
        out_path = Path(out) if out is not None else None
        if out_path is None:
            ts = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            self.screenshot_dir.mkdir(parents=True, exist_ok=True)
            out_path = self.screenshot_dir / f'screenshot_{ts}.png'

        logger.info('screenshot roi=%s out=%s', roi, out_path)
        if self.dry_run:
            return {'saved': str(out_path), 'roi': roi, 'dry_run': True}

        import cv2
        from wuwa_inventory_kamera.game.screen import capture_full

        layout  = self.nav.layout
        full    = capture_full(layout.width, layout.height, layout.monitor)
        roi_obj = _resolve_roi(layout, roi)
        img = (
            full if roi_obj is None
            else full[
                int(roi_obj.y): int(roi_obj.y + roi_obj.h),
                int(roi_obj.x): int(roi_obj.x + roi_obj.w),
            ]
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out_path), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
        return {'saved': str(out_path), 'roi': roi, 'shape': list(img.shape)}

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

    def ocr_roi(self, roi: str = 'full', *, thorough: bool = False) -> dict:
        """
        Capture the game screen, crop to *roi*, and run OCR.

        Parameters
        ----------
        roi:
            ROI name or dot-path â€” same values accepted as :meth:`screenshot`.
        thorough:
            Use multi-pass OCR (higher recall, ~3Ã— slower).

        Returns a dict::

            {"roi": "echo-card", "lines": [{"text": "â€¦", "conf": 0.97}, â€¦]}
        """
        logger.info('ocr-roi roi=%s thorough=%s', roi, thorough)
        if self.dry_run:
            return {'roi': roi, 'lines': []}

        from wuwa_inventory_kamera.game.screen import capture_full
        from wuwa_inventory_kamera.scraping.ocr._rapidocr import RapidOcrBackend

        layout  = self.nav.layout
        full    = capture_full(layout.width, layout.height, layout.monitor)
        roi_obj = _resolve_roi(layout, roi)
        crop = (
            full if roi_obj is None
            else full[
                int(roi_obj.y): int(roi_obj.y + roi_obj.h),
                int(roi_obj.x): int(roi_obj.x + roi_obj.w),
            ]
        )
        if self._ocr_backend is None:
            self._ocr_backend = RapidOcrBackend()
        recognize = (
            self._ocr_backend.thorough_recognize if thorough
            else self._ocr_backend.recognize
        )
        return {
            'roi': roi,
            'lines': [
                {'text': t, 'conf': round(float(c), 4)}
                for _, t, c in recognize(crop)
            ],
        }


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
    else:
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
        '--log-level', default='INFO',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        help='Logging verbosity (default: INFO).',
    )

    args = parser.parse_args()
    _configure_logging(args.log_level)

    if args.script and args.oneliner:
        parser.error('Specify at most one of: script, -c.')

    # Delayed imports so --help is fast
    from wuwa_inventory_kamera.game.input_controller import InputController
    from wuwa_inventory_kamera.game.navigation import GameNavigator
    from wuwa_inventory_kamera.game.screen import GameWindow
    from wuwa_inventory_kamera.game.state import GameState

    gw = GameWindow()
    if not gw.found and not args.dry_run:
        print('Error: game window not found.  Is the game running?', file=sys.stderr)
        sys.exit(1)

    ctrl = InputController(gw.monitor_index if gw.found else 1)
    nav  = GameNavigator(ctrl, gw, inventory_key=args.inventory_key)

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
        session.run_script(Path(args.script))
    elif args.oneliner:
        env = session._script_namespace()
        exec(compile(args.oneliner, '<-c>', 'exec'), env)  # noqa: S102
    else:
        session.repl()
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
