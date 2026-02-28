"""
reprocess.py — Standalone CLI for offline echo scan reprocessing
================================================================

Re-runs Phase 2 (OCR + parsing) on raw images that were captured during a
previous scan session, without needing the game, the GUI, or any Win32 API.

Requirements
------------
Only the packages that are genuinely needed for OCR work:

    rapidocr-onnxruntime    opencv-python    numpy

The GUI/Win32 packages (qfluentwidgets, PySide6, win32api, win32con,
win32clipboard, mss) are stubbed out at startup so their presence in the
environment is optional.

Usage
-----
List available sessions::

    python reprocess.py --list [--export-dir export]

Reprocess by session ID (reads export folder from config/config.json)::

    python reprocess.py --session-id 2026-02-28_14-30-00

Reprocess by explicit raw directory::

    python reprocess.py --raw-dir export/2026-02-28_14-30-00/raw

Override quality filters and output location::

    python reprocess.py --raw-dir ./raw --min-rarity 4 --min-level 10
    python reprocess.py --session-id 2026-02-28_14-30-00 --output-dir ./out

Output
------
``echoes_wuwainventorykamera.json`` is written into *output-dir*, which
defaults to the session folder (the parent of the ``raw/`` directory).
"""

from __future__ import annotations

import json
import sys
import types
import logging
import argparse
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: install lightweight stubs for GUI / Win32 modules.
#
# These modules are imported at the *module level* of properties.config and
# scraping.utils.common, but none of their functions are ever called during
# offline reprocessing.  We install stubs before any project import so that
# the import machinery succeeds without the real packages being installed.
# ---------------------------------------------------------------------------

# -- qfluentwidgets ----------------------------------------------------------
# config.py does:   class Config(QConfig): ...
# That requires QConfig to be a real Python *type*, not a MagicMock instance.
# We therefore provide proper class stubs.

def _make_qfluentwidgets_stub() -> types.ModuleType:
    """Return a module whose classes satisfy all uses in properties/config.py."""

    class _Signal:
        """No-op signal: connect/emit silently ignored."""
        def __init__(self, *_args): pass
        def connect(self, *_args): pass
        def emit(self, *_args): pass

    class _ConfigValidator:
        def __init__(self, *_args, **_kw): pass
        def validate(self, v): return True
        def correct(self, v): return v

    class _BoolValidator(_ConfigValidator): pass

    class _FolderValidator(_ConfigValidator):
        def correct(self, v): return v if v else 'export'

    class _OptionsValidator(_ConfigValidator):
        def __init__(self, options): self.options = options

    class _RangeValidator(_ConfigValidator):
        def __init__(self, lo, hi): self.lo = lo; self.hi = hi

    class _TextValidator(_ConfigValidator): pass

    class _ConfigItem:
        """Minimal ConfigItem: stores (group, name, default) and a mutable value."""
        def __init__(self, group: str, name: str, default, validator=None):
            self.group = group
            self.name = name
            self.value = default
            self._default = default
            self._validator = validator

    class _OptionsConfigItem(_ConfigItem): pass

    class _QConfig:
        """Minimal QConfig base: get(item) returns item.value, save() is a no-op."""
        def get(self, item):
            return getattr(item, 'value', None)
        def save(self): pass

    class _qconfig:
        """Minimal qconfig: load() reads config/config.json if it exists."""
        @staticmethod
        def load(path: str, cfg_instance) -> None:
            try:
                data = json.loads(Path(path).read_text(encoding='utf-8'))
                # Walk class-level ConfigItem descriptors and update their values.
                for attr_name in vars(type(cfg_instance)):
                    item = getattr(type(cfg_instance), attr_name, None)
                    if isinstance(item, _ConfigItem):
                        val = data.get(item.group, {}).get(item.name)
                        if val is not None:
                            item.value = val
            except Exception:
                pass  # Config file absent or malformed — use class defaults.

    mod = types.ModuleType('qfluentwidgets')
    mod.QConfig = _QConfig
    mod.Signal = _Signal
    mod.ConfigValidator = _ConfigValidator
    mod.ConfigItem = _ConfigItem
    mod.OptionsConfigItem = _OptionsConfigItem
    mod.BoolValidator = _BoolValidator
    mod.FolderValidator = _FolderValidator
    mod.OptionsValidator = _OptionsValidator
    mod.RangeValidator = _RangeValidator
    mod.qconfig = _qconfig()
    return mod


# -- Simple attribute stub (for packages whose code is never executed) ------
# Supports: attribute access, calls, context-manager protocol.
# Used for: win32api, win32con, win32clipboard, mss.

def _make_simple_stub(name: str) -> types.ModuleType:
    class _S:
        def __init__(self, *_a, **_kw): pass
        def __call__(self, *_a, **_kw): return type(self)()
        def __getattr__(self, _n): return type(self)()
        def __enter__(self): return self
        def __exit__(self, *_a): pass
        def __iter__(self): return iter([])
        def __index__(self): return 0
        def __int__(self): return 0

    mod = types.ModuleType(name)
    mod.__getattr__ = lambda _n: _S()  # handles: from mss import mss
    return mod


# Install stubs before ANY project import so that the module loader never
# tries to import the real packages.
sys.modules.setdefault('qfluentwidgets', _make_qfluentwidgets_stub())
for _stub_name in ('win32api', 'win32con', 'win32clipboard', 'mss'):
    sys.modules.setdefault(_stub_name, _make_simple_stub(_stub_name))

# ---------------------------------------------------------------------------
# Project imports — safe now that stubs are installed.
# ---------------------------------------------------------------------------
# Avoid importing scraping.utils (which pulls in mouse_keyboard → win32api).
# Import from the sub-modules directly instead.
from scraping.models.rawScan import RawEchoScan           # noqa: E402  (numpy + dataclasses only)
from scraping.utils.common import loadRawScans             # noqa: E402
from scraping.processing.echoesProcessor import echoProcessor  # noqa: E402
from properties.config import cfg                         # noqa: E402

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
        level=level,
        stream=sys.stdout,
    )


logger = logging.getLogger('reprocess')

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_sessions(export_dir: Path) -> list[Path]:
    """Return session folders that contain a ``raw/`` sub-directory, sorted newest-first."""
    if not export_dir.is_dir():
        return []
    return sorted(
        [d for d in export_dir.iterdir() if d.is_dir() and (d / 'raw').is_dir()],
        reverse=True,
    )


def _write_output(echoes: list[dict], output_dir: Path) -> Path:
    """Serialise *echoes* to ``echoes_wuwainventorykamera.json`` in *output_dir*."""
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / 'echoes_wuwainventorykamera.json'
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(echoes, f, indent=2, ensure_ascii=False)
    return out_path


def _resolve_raw_dir(args: argparse.Namespace) -> Path:
    """Resolve the ``raw/`` directory from CLI arguments."""
    if args.raw_dir:
        raw_dir = Path(args.raw_dir)
        if not raw_dir.is_dir():
            logger.error('--raw-dir does not exist: %s', raw_dir)
            sys.exit(1)
        return raw_dir

    if args.session_id:
        export_dir = Path(args.export_dir) if args.export_dir else Path(cfg.get(cfg.exportFolder))
        raw_dir = export_dir / args.session_id / 'raw'
        if not raw_dir.is_dir():
            logger.error(
                'raw/ directory not found for session %r.\n'
                '  Expected: %s\n'
                '  Use --export-dir if your export folder is not %s',
                args.session_id, raw_dir, export_dir,
            )
            sys.exit(1)
        return raw_dir

    logger.error('Provide --session-id or --raw-dir. Use --list to see available sessions.')
    sys.exit(1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog='reprocess',
        description=(
            'Re-run OCR processing on a previously captured WuWa echo scan session.\n'
            'Does not require the game, the GUI, or any Win32 APIs.'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split('Usage')[1] if 'Usage' in __doc__ else '',
    )

    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        '--session-id', metavar='SESSION_ID',
        help='Session folder name under the export folder (e.g. 2026-02-28_14-30-00).',
    )
    source.add_argument(
        '--raw-dir', metavar='PATH',
        help='Explicit path to the raw/ directory of a captured session.',
    )
    source.add_argument(
        '--list', action='store_true',
        help='List available sessions and exit.',
    )

    parser.add_argument(
        '--export-dir', metavar='PATH',
        help='Root export folder (default: value from config/config.json, or "export").',
    )
    parser.add_argument(
        '--output-dir', metavar='PATH',
        help=(
            'Directory where echoes_wuwainventorykamera.json is written. '
            'Defaults to the session folder (parent of raw/).'
        ),
    )
    parser.add_argument(
        '--min-rarity', type=int, choices=range(1, 6), metavar='1-5', default=None,
        help='Minimum echo rarity to include (overrides config; default: 1).',
    )
    parser.add_argument(
        '--min-level', type=int, choices=range(0, 26), metavar='0-25', default=None,
        help='Minimum echo level to include (overrides config; default: 0).',
    )
    parser.add_argument(
        '--log-level', default='INFO',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        help='Logging verbosity (default: INFO).',
    )

    args = parser.parse_args()
    _configure_logging(args.log_level)

    # -- Apply config overrides from CLI args --------------------------------
    if args.min_rarity is not None:
        cfg.echoMinRarity.value = args.min_rarity
    if args.min_level is not None:
        cfg.echoMinLevel.value = args.min_level

    export_dir = Path(args.export_dir) if args.export_dir else Path(cfg.get(cfg.exportFolder))

    # -- --list --------------------------------------------------------------
    if args.list:
        sessions = _find_sessions(export_dir)
        if not sessions:
            print(f'No sessions with raw scan data found in: {export_dir}')
        else:
            print(f'Sessions in {export_dir} (newest first):')
            for s in sessions:
                raw_dirs = list((s / 'raw').glob('echo_*/'))
                echo_count = len(raw_dirs)
                print(f'  {s.name}  ({echo_count} raw scan{"s" if echo_count != 1 else ""})')
        sys.exit(0)

    # -- Resolve paths -------------------------------------------------------
    raw_dir = _resolve_raw_dir(args)
    session_id = raw_dir.parent.name
    output_dir = Path(args.output_dir) if args.output_dir else raw_dir.parent

    logger.info('Session   : %s', session_id)
    logger.info('Raw dir   : %s', raw_dir)
    logger.info('Output dir: %s', output_dir)
    logger.info('Min rarity: %d', cfg.get(cfg.echoMinRarity))
    logger.info('Min level : %d', cfg.get(cfg.echoMinLevel))

    # -- Load raw scans -------------------------------------------------------
    scans: list[RawEchoScan] = loadRawScans(raw_dir)
    if not scans:
        logger.error('No raw scans found in %s', raw_dir)
        sys.exit(1)
    logger.info('Loaded %d raw scan(s)', len(scans))

    # -- Process --------------------------------------------------------------
    echoes = echoProcessor(scans, session_id, raw_dir)
    logger.info('Accepted %d / %d echo(es)', len(echoes), len(scans))

    # -- Write output ---------------------------------------------------------
    out_path = _write_output(echoes, output_dir)
    logger.info('Saved → %s', out_path)
    print(f'Done. {len(echoes)} echo(es) written to {out_path}')


if __name__ == '__main__':
    main()
