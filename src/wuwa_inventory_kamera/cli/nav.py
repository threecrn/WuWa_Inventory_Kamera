"""
wuwa_inventory_kamera.cli.nav
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Interactive / scripted game-navigation CLI.

Provides a simple **command DSL** for:

* Navigating the game inventory — open/close, switch tabs, set sort order,
  scroll pages, click specific grid cells or scan indices.
* Capturing screenshots — full window or specific named UI regions.
* Inspecting the current logical screen state.

Three usage modes
-----------------
One-shot command::

    wuwa-nav switch-tab echoes
    wuwa-nav goto-index 47
    wuwa-nav screenshot --roi echo-card --out echo_47.png

Script file::

    wuwa-nav --script my_session.wuwa

Interactive REPL::

    wuwa-nav --interactive

State round-tripping::

    # Save state at the end of a session
    wuwa-nav --state-out state.json switch-tab echoes

    # Resume a session — no redundant navigation clicks
    wuwa-nav --state-in state.json goto-index 72

Command DSL reference
---------------------
Lines starting with ``#`` are comments.  Empty lines are ignored.
Each line is one command: the first token is the verb, the rest are
arguments.  Quoted strings are supported for paths with spaces
(standard POSIX shell quoting via :func:`shlex.split`).

=================================  =====================================================
Command                            Description
=================================  =====================================================
``open-inventory``                 Press the inventory keybind
``close-inventory``                Press Esc
``switch-tab <tab>``               echoes | weapons | devItems | resources
``set-sort <order>``               newest | oldest | quality_desc | quality_asc |
                                   level_desc | level_asc
``goto-page <n>``                  Scroll to page N (1-based)
``goto-cell <row> <col>``          Click cell at 0-based row, col
``goto-index <n>``                 Navigate to 0-based scan index (page + click)
``read-count``                     Print ``{"items": N, "pages": N}``
``sonata-down``                    Scroll echo panel to reveal sonata section
``sonata-up``                      Scroll echo panel back to stats
``click <x> <y>``                  Raw left-click at game-relative coords
``move <x> <y>``                   Move the cursor
``scroll <amount>``                Scroll wheel (positive = down, negative = up)
``key <name>``                     Press a single key (e.g. ``esc``, ``b``, ``f5``)
``hotkey <k1> [<k2> …]``           Press a combination (e.g. ``ctrl v``)
``screenshot [opts]``              Capture a screenshot; see below
``state``                          Print current :class:`~...game.state.GameState` JSON
``in-menu``                        OCR-check whether the main-menu screen is visible
``wait <seconds>``                 Sleep for N seconds
=================================  =====================================================

Screenshot syntax::

    screenshot
    screenshot --roi full
    screenshot --roi echo-card
    screenshot --roi echoes.fullStatsName   # dot-path into layout tree
    screenshot --out /tmp/echo47.png

Named ROIs: ``full`` | ``echo-card`` | ``echo-stats-name`` |
``echo-stats-value`` | ``sonata`` | ``weapon-name`` | ``weapon-level``

Any dot-path string (e.g. ``echoes.echoCard``) navigates the
:class:`~...game.screen.ScreenLayout` attribute tree directly.

Entry point
-----------
Registered as ``wuwa-nav`` console script in ``pyproject.toml``.
"""
from __future__ import annotations

import argparse
import json
import logging
import shlex
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger('wuwa.nav')


# ---------------------------------------------------------------------------
# Named ROI aliases
# ---------------------------------------------------------------------------

#: Mapping from friendly ROI name → dot-path into a ScreenLayout.
_ROI_ALIASES: dict[str, str] = {
    'echo-card':        'echoes.echoCard',
    'echo-stats-name':  'echoes.fullStatsName',
    'echo-stats-value': 'echoes.fullStatsValue',
    'sonata':           'echoes.sonataName',
    'weapon-name':      'weapons.name',
    'weapon-level':     'weapons.level',
}


def _resolve_roi(layout, roi_name: str):
    """
    Resolve *roi_name* to a coordinate object from *layout*.

    Accepts:

    * ``'full'`` → ``None`` (capture the full window, no crop).
    * A key from :data:`_ROI_ALIASES`.
    * Any dot-separated path, e.g. ``'echoes.echoCard'``.

    Returns the coordinate object (with ``.x``, ``.y``, ``.w``, ``.h``)
    or ``None`` for a full-window capture.

    Raises :class:`CommandError` on unresolvable names.
    """
    if roi_name == 'full':
        return None

    path = _ROI_ALIASES.get(roi_name, roi_name)
    obj = layout
    for part in path.split('.'):
        obj = getattr(obj, part, None)
        if obj is None:
            raise CommandError(
                f'ROI {roi_name!r} → layout path {path!r}: '
                f'attribute {part!r} not found'
            )
    return obj


# ---------------------------------------------------------------------------
# Command error
# ---------------------------------------------------------------------------

class CommandError(Exception):
    """Raised by a command handler on bad arguments or failed preconditions."""


# ---------------------------------------------------------------------------
# NavCommandDispatcher
# ---------------------------------------------------------------------------

class NavCommandDispatcher:
    """
    Parses and executes navigation DSL commands.

    Each public ``run_*`` method accepts commands in different forms
    (token list, script string, interactive REPL).  All commands ultimately
    call the private ``_cmd_*`` handlers which interact with the game via
    a :class:`~...game.navigation.GameNavigator`.

    Parameters
    ----------
    nav:
        Live :class:`~...game.navigation.GameNavigator`.
    gw:
        Live :class:`~...game.screen.GameWindow`.
    screenshot_dir:
        Directory for auto-named screenshots (default: ``screenshots/``
        in the current working directory).
    dry_run:
        If ``True``, log commands without sending any game input.
        Useful for testing scripts.
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

        # Page and cell are tracked here explicitly so the state snapshot
        # can expose them cleanly without OCR round-trips.
        self._page_0: int = 0                        # 0-based current page
        self._cell: Optional[tuple[int, int]] = None  # (row, col)

    # ── Dispatch ────────────────────────────────────────────────────────

    def run_tokens(self, tokens: list[str]) -> str | None:
        """
        Execute a single command represented as a *tokens* list.

        The first token is the verb; the rest are arguments.

        Returns a result string for inspection commands (``state``,
        ``screenshot``, ``read-count``, ``in-menu``), or ``None`` for
        side-effect-only commands.

        Raises :class:`CommandError` on unknown verbs or bad arguments.
        """
        if not tokens:
            return None

        verb = tokens[0].lower()
        args = tokens[1:]

        # Build dispatch table as a local dict so the linter can verify
        # all handlers exist.
        _dispatch: dict[str, Callable] = {
            'open-inventory':  self._cmd_open_inventory,
            'close-inventory': self._cmd_close_inventory,
            'switch-tab':      self._cmd_switch_tab,
            'set-sort':        self._cmd_set_sort,
            'goto-page':       self._cmd_goto_page,
            'goto-cell':       self._cmd_goto_cell,
            'goto-index':      self._cmd_goto_index,
            'read-count':      self._cmd_read_count,
            'sonata-down':     self._cmd_sonata_down,
            'sonata-up':       self._cmd_sonata_up,
            'click':           self._cmd_click,
            'move':            self._cmd_move,
            'scroll':          self._cmd_scroll,
            'key':             self._cmd_key,
            'hotkey':          self._cmd_hotkey,
            'screenshot':      self._cmd_screenshot,
            'state':           self._cmd_state,
            'in-menu':         self._cmd_in_menu,
            'wait':            self._cmd_wait,
        }

        handler = _dispatch.get(verb)
        if handler is None:
            raise CommandError(
                f'Unknown command {verb!r}. Type "help" for a command list.'
            )
        return handler(args)

    def run_script(self, text: str) -> None:
        """Execute all commands in a script string, ignoring nothing."""
        for lineno, tokens in _parse_script(text):
            try:
                result = self.run_tokens(tokens)
                if result is not None:
                    print(result)
            except CommandError as exc:
                logger.error('Line %d: %s', lineno, exc)

    def run_interactive(self) -> None:
        """
        Start an interactive REPL.

        Reads one command per line.  Type ``help`` for reference,
        ``exit`` / ``quit`` to stop.
        """
        print("WuWa Navigator  —  type 'help' for commands, 'exit' to quit.")
        while True:
            try:
                line = input('wuwa> ').strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not line or line.startswith('#'):
                continue
            if line.lower() in ('exit', 'quit', 'q'):
                break
            if line.lower() == 'help':
                _print_help()
                continue

            try:
                tokens = shlex.split(line)
            except ValueError as exc:
                print(f'Parse error: {exc}')
                continue

            try:
                result = self.run_tokens(tokens)
                if result is not None:
                    print(result)
            except CommandError as exc:
                print(f'Error: {exc}')

    # ── State snapshot helper ────────────────────────────────────────────

    def snapshot(self):
        """Return a :class:`~...game.state.GameState` reflecting current state."""
        from wuwa_inventory_kamera.game.state import CellRef, GameState
        s = GameState.from_navigator(self.nav, self.gw)
        s.page = self._page_0 + 1  # expose as 1-based
        if self._cell is not None:
            s.cell = CellRef(self._cell[0], self._cell[1])
        return s

    # ── Navigation commands ──────────────────────────────────────────────

    def _cmd_open_inventory(self, args: list[str]) -> None:
        logger.info('open-inventory')
        if not self.dry_run:
            self.nav.open_inventory()
        self._page_0 = 0
        self._cell   = None

    def _cmd_close_inventory(self, args: list[str]) -> None:
        logger.info('close-inventory')
        if not self.dry_run:
            self.nav.close_inventory()
        self._page_0 = 0
        self._cell   = None

    def _cmd_switch_tab(self, args: list[str]) -> None:
        if not args:
            raise CommandError('switch-tab requires a tab name')
        tab_name = args[0]
        logger.info('switch-tab %s', tab_name)
        if not self.dry_run:
            from wuwa_inventory_kamera.game.navigation import InventoryTab
            try:
                tab = InventoryTab(tab_name)
            except ValueError:
                valid = ', '.join(t.value for t in InventoryTab)
                raise CommandError(
                    f'Unknown tab {tab_name!r}. Valid values: {valid}'
                )
            self.nav.switch_tab(tab)
        self._page_0 = 0
        self._cell   = None

    def _cmd_set_sort(self, args: list[str]) -> None:
        if not args:
            raise CommandError('set-sort requires a sort order name')
        order_name = args[0]
        logger.info('set-sort %s', order_name)
        if not self.dry_run:
            from wuwa_inventory_kamera.game.navigation import SortOrder
            try:
                order = SortOrder[order_name.upper()]
            except KeyError:
                valid = ', '.join(s.name.lower() for s in SortOrder)
                raise CommandError(
                    f'Unknown sort order {order_name!r}. Valid: {valid}'
                )
            self.nav.set_sort_order(order)

    def _cmd_goto_page(self, args: list[str]) -> None:
        if not args:
            raise CommandError('goto-page requires a page number (1-based)')
        try:
            page_1 = int(args[0])
        except ValueError:
            raise CommandError(f'goto-page: {args[0]!r} is not an integer')
        if page_1 < 1:
            raise CommandError('goto-page: page number must be ≥ 1')
        target_0 = page_1 - 1
        logger.info('goto-page %d (internal 0-based: %d)', page_1, target_0)
        if not self.dry_run:
            self.nav.scroll_to_page(target_0, self._page_0)
        self._page_0 = target_0
        self._cell   = None

    def _cmd_goto_cell(self, args: list[str]) -> None:
        if len(args) < 2:
            raise CommandError('goto-cell requires row and col (both 0-based)')
        try:
            row, col = int(args[0]), int(args[1])
        except ValueError:
            raise CommandError('goto-cell: row and col must be integers')
        logger.info('goto-cell row=%d col=%d', row, col)
        if not self.dry_run:
            self.nav.click_grid_cell(row, col)
        self._cell = (row, col)

    def _cmd_goto_index(self, args: list[str]) -> None:
        """
        Navigate to a 0-based scan index.

        The index matches the flat traversal order used by
        :class:`~...scraping.scanning.scan_state.ScanSession`:
        page 0 row 0 col 0 = index 0, page 0 row 0 col 5 = index 5,
        page 0 row 1 col 0 = index 6, page 1 row 0 col 0 = index 24, …
        """
        if not args:
            raise CommandError('goto-index requires a 0-based scan index')
        try:
            idx = int(args[0])
        except ValueError:
            raise CommandError(f'goto-index: {args[0]!r} is not an integer')
        if idx < 0:
            raise CommandError('goto-index: index must be ≥ 0')
        logger.info('goto-index %d', idx)
        if not self.dry_run:
            from wuwa_inventory_kamera.game.navigation import GRID_COLS
            from wuwa_inventory_kamera.scraping.scanning.scan_state import GridPosition
            pos = GridPosition.from_index(idx, GRID_COLS)
            self.nav.scroll_to_page(pos.page, self._page_0)
            self.nav.click_grid_cell(pos.row, pos.col)
            self._page_0 = pos.page
            self._cell   = (pos.row, pos.col)
        else:
            from wuwa_inventory_kamera.game.navigation import GRID_COLS
            from wuwa_inventory_kamera.scraping.scanning.scan_state import GridPosition
            pos = GridPosition.from_index(idx, GRID_COLS)
            logger.info(
                'dry-run: would navigate to page=%d row=%d col=%d',
                pos.page, pos.row, pos.col,
            )

    def _cmd_read_count(self, args: list[str]) -> str:
        logger.info('read-count')
        if self.dry_run:
            return json.dumps({'items': 0, 'pages': 0, 'dry_run': True})
        count, pages = self.nav.read_item_count()
        return json.dumps({'items': count, 'pages': pages})

    # ── Echo detail commands ─────────────────────────────────────────────

    def _cmd_sonata_down(self, args: list[str]) -> None:
        logger.info('sonata-down')
        if not self.dry_run:
            self.nav.scroll_to_sonata()

    def _cmd_sonata_up(self, args: list[str]) -> None:
        logger.info('sonata-up')
        if not self.dry_run:
            self.nav.scroll_back_from_sonata()

    # ── Raw input commands ───────────────────────────────────────────────

    def _cmd_click(self, args: list[str]) -> None:
        if len(args) < 2:
            raise CommandError('click requires x and y coordinates')
        try:
            x, y = float(args[0]), float(args[1])
        except ValueError:
            raise CommandError('click: x and y must be numbers')
        logger.info('click %.1f %.1f', x, y)
        if not self.dry_run:
            self.nav.ctrl.click(x, y)

    def _cmd_move(self, args: list[str]) -> None:
        if len(args) < 2:
            raise CommandError('move requires x and y coordinates')
        try:
            x, y = float(args[0]), float(args[1])
        except ValueError:
            raise CommandError('move: x and y must be numbers')
        logger.info('move %.1f %.1f', x, y)
        if not self.dry_run:
            self.nav.ctrl.move(x, y)

    def _cmd_scroll(self, args: list[str]) -> None:
        if not args:
            raise CommandError('scroll requires an amount (positive=down, negative=up)')
        try:
            amount = float(args[0])
        except ValueError:
            raise CommandError(f'scroll: {args[0]!r} is not a number')
        logger.info('scroll %.2f', amount)
        if not self.dry_run:
            self.nav.ctrl.scroll(amount)

    def _cmd_key(self, args: list[str]) -> None:
        if not args:
            raise CommandError('key requires a key name (e.g. esc, b, f5)')
        key = args[0]
        logger.info('key %s', key)
        if not self.dry_run:
            self.nav.ctrl.press_key(key)

    def _cmd_hotkey(self, args: list[str]) -> None:
        if len(args) < 2:
            raise CommandError('hotkey requires at least 2 key names')
        logger.info('hotkey %s', ' '.join(args))
        if not self.dry_run:
            self.nav.ctrl.hotkey(*args)

    # ── Screenshot command ───────────────────────────────────────────────

    def _cmd_screenshot(self, args: list[str]) -> str:
        """
        Capture a screenshot and save it to disk.

        Options
        -------
        --roi <name>     ROI to crop.  Default: ``full``.
        --out <path>     Output file.  Default: auto-named under
                         ``screenshot_dir``.
        """
        roi_name = 'full'
        out_path: Path | None = None

        i = 0
        while i < len(args):
            if args[i] == '--roi' and i + 1 < len(args):
                roi_name = args[i + 1]
                i += 2
            elif args[i] == '--out' and i + 1 < len(args):
                out_path = Path(args[i + 1])
                i += 2
            else:
                raise CommandError(
                    f'screenshot: unexpected argument {args[i]!r}  '
                    f'(expected --roi or --out)'
                )

        if out_path is None:
            ts = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            self.screenshot_dir.mkdir(parents=True, exist_ok=True)
            out_path = self.screenshot_dir / f'screenshot_{ts}.png'

        logger.info('screenshot --roi %s --out %s', roi_name, out_path)

        if self.dry_run:
            return json.dumps({'saved': str(out_path), 'dry_run': True})

        import cv2
        from wuwa_inventory_kamera.game.screen import capture_full

        layout = self.nav.layout
        full = capture_full(layout.width, layout.height, layout.monitor)

        try:
            roi = _resolve_roi(layout, roi_name)
        except CommandError:
            raise

        if roi is None:
            img = full
        else:
            img = full[
                int(roi.y) : int(roi.y + roi.h),
                int(roi.x) : int(roi.x + roi.w),
            ]

        out_path.parent.mkdir(parents=True, exist_ok=True)
        # capture_full returns RGB; cv2 expects BGR
        cv2.imwrite(str(out_path), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
        return json.dumps({
            'saved': str(out_path),
            'roi':   roi_name,
            'shape': list(img.shape),
        })

    # ── State / inspection commands ──────────────────────────────────────

    def _cmd_state(self, args: list[str]) -> str:
        return self.snapshot().to_json()

    def _cmd_in_menu(self, args: list[str]) -> str:
        logger.info('in-menu')
        if self.dry_run:
            return json.dumps({'in_menu': False, 'dry_run': True})
        result = self.nav.is_in_main_menu()
        return json.dumps({'in_menu': result})

    def _cmd_wait(self, args: list[str]) -> None:
        if not args:
            raise CommandError('wait requires a duration in seconds')
        try:
            secs = float(args[0])
        except ValueError:
            raise CommandError(f'wait: {args[0]!r} is not a number')
        logger.info('wait %.2fs', secs)
        if not self.dry_run:
            time.sleep(secs)


# ---------------------------------------------------------------------------
# Script parser
# ---------------------------------------------------------------------------

def _parse_script(text: str) -> list[tuple[int, list[str]]]:
    """
    Parse a DSL script string into ``(lineno, tokens)`` pairs.

    * Lines starting with ``#`` are comments.
    * Empty lines are ignored.
    * Tokenisation uses :func:`shlex.split` so quoted paths work.
    """
    result: list[tuple[int, list[str]]] = []
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        try:
            tokens = shlex.split(line)
        except ValueError as exc:
            logger.warning('Line %d: parse error — %s', lineno, exc)
            continue
        if tokens:
            result.append((lineno, tokens))
    return result


# ---------------------------------------------------------------------------
# Help text
# ---------------------------------------------------------------------------

def _print_help() -> None:
    print("""\
Navigation:
  open-inventory            Press inventory key
  close-inventory           Press Esc
  switch-tab <tab>          echoes | weapons | devItems | resources
  set-sort <order>          newest | oldest | quality_desc | quality_asc |
                            level_desc | level_asc
  goto-page <n>             Scroll to page N (1-based)
  goto-cell <row> <col>     Click cell (0-based row, col)
  goto-index <n>            Navigate to 0-based scan index

Echo detail:
  sonata-down               Scroll to show sonata section
  sonata-up                 Scroll back to stats

Raw input:
  click <x> <y>             Left-click at game-relative coords
  move <x> <y>              Move cursor
  scroll <amount>           Scroll wheel (+ve=down, -ve=up)
  key <name>                Press a key  (esc, b, f5, ...)
  hotkey <k1> [<k2> ...]   Key combo  (ctrl v,  alt f4, ...)

Screenshots:
  screenshot                Full window  →  screenshots/screenshot_<ts>.png
  screenshot --roi <name>   full | echo-card | echo-stats-name |
                            echo-stats-value | sonata | <layout.dot.path>
  screenshot --out <path>   Save to a specific file

State / inspection:
  read-count                Print {"items": N, "pages": N}
  state                     Print current state JSON
  in-menu                   OCR-check for main-menu screen
  wait <seconds>            Sleep

Script / REPL:
  # line comment
  exit | quit               Quit the REPL""")


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def _configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    fmt = logging.Formatter('%(asctime)s | %(levelname)-8s | %(name)s | %(message)s')
    root = logging.getLogger()
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
            'Navigate and control the WuWa game window from the command line.\n\n'
            'Three usage modes:\n'
            '  One-shot:    wuwa-nav switch-tab echoes\n'
            '  Script file: wuwa-nav --script session.wuwa\n'
            '  Interactive: wuwa-nav --interactive\n\n'
            'Run wuwa-nav --interactive and type "help" for a command reference.\n\n'
            'The final logical state is printed as JSON to stdout after every run,\n'
            'or written to --state-out.  Use --state-in to resume a previous session.'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        '--state-in', metavar='FILE',
        help=(
            'Load initial navigator state from a JSON file.  '
            'The navigator\'s internal tab/sort/page are set accordingly '
            'without sending any game input.'
        ),
    )
    parser.add_argument(
        '--state-out', metavar='FILE',
        help='Write final state JSON to FILE instead of stdout.',
    )
    parser.add_argument(
        '--script', metavar='FILE',
        help='Execute commands from a DSL script file.',
    )
    parser.add_argument(
        '--interactive', '-i', action='store_true',
        help='Start an interactive REPL.',
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Parse and log commands without sending any game input.',
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
    parser.add_argument(
        'command', nargs='*',
        metavar='COMMAND',
        help=(
            'One-shot command with its arguments, e.g. '
            '"switch-tab echoes" or "goto-index 47".  '
            'Mutually exclusive with --script and --interactive.'
        ),
    )

    args = parser.parse_args()
    _configure_logging(args.log_level)

    # Validate: at most one execution mode
    n_modes = sum([bool(args.command), bool(args.script), args.interactive])
    if n_modes > 1:
        parser.error(
            'Specify at most one of: a positional command, --script, --interactive.'
        )

    # --- Delayed imports (keeps --help instantaneous) ---
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

    # Restore prior state if requested
    initial_state: GameState | None = None
    if args.state_in:
        try:
            text = Path(args.state_in).read_text(encoding='utf-8')
            initial_state = GameState.from_json(text)
            initial_state.apply_to_navigator(nav)
            logger.info('Restored state from %s: %s', args.state_in, initial_state)
        except Exception as exc:
            logger.warning('Could not load state from %s: %s', args.state_in, exc)

    dispatcher = NavCommandDispatcher(
        nav=nav,
        gw=gw,
        screenshot_dir=Path(args.screenshot_dir),
        dry_run=args.dry_run,
    )

    # Sync dispatcher's page/cell tracking from the loaded state
    if initial_state is not None:
        if initial_state.page is not None:
            dispatcher._page_0 = initial_state.page - 1  # 1-based → 0-based
        if initial_state.cell is not None:
            dispatcher._cell = (initial_state.cell.row, initial_state.cell.col)

    # ── Execute ──────────────────────────────────────────────────────────

    if args.interactive:
        dispatcher.run_interactive()

    elif args.script:
        script_text = Path(args.script).read_text(encoding='utf-8')
        dispatcher.run_script(script_text)

    elif args.command:
        try:
            result = dispatcher.run_tokens(args.command)
            if result is not None:
                print(result)
        except CommandError as exc:
            print(f'Error: {exc}', file=sys.stderr)
            sys.exit(1)

    else:
        # No command given — just print the current state and exit
        print(dispatcher._cmd_state([]))
        return   # state already printed; skip the duplicate below

    # ── Output final state ────────────────────────────────────────────────

    final_json = dispatcher.snapshot().to_json()

    if args.state_out:
        out = Path(args.state_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(final_json, encoding='utf-8')
        logger.info('State written to %s', out)
    else:
        print(final_json)


if __name__ == '__main__':
    main()
