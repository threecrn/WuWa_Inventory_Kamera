"""
reprocess.py — Standalone CLI for offline echo scan reprocessing
================================================================

Re-runs Phase 2 (OCR + parsing) on raw images that were captured during a
previous scan session, without needing the game, the GUI, or any Win32 API.

Requirements
------------
Only the packages that are genuinely needed for OCR work:

    rapidocr-onnxruntime    opencv-python    numpy

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
import os
import sys
import logging
import argparse
from pathlib import Path

# Add the parent directory to sys.path so project packages are importable.
sys.path.append(str(Path(__file__).resolve().parent.parent))

# ---------------------------------------------------------------------------
# Project imports
# ---------------------------------------------------------------------------
from properties.app_config import app_config                           # noqa: E402
from scraping.models.rawScan import RawEchoScan                        # noqa: E402
from scraping.utils.common import loadRawScans                         # noqa: E402
from scraping.processing.echoesProcessor import echoProcessor          # noqa: E402
import scraping.ocr as _ocr                                            # noqa: E402

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

# Register TRACE (level 5) below DEBUG (10) before any project import so that
# scraping.utils.common finds the level already present and the name is
# available for --log-level validation below.
_TRACE_LEVEL: int = 5
if not hasattr(logging, 'TRACE'):
    logging.addLevelName(_TRACE_LEVEL, 'TRACE')
    logging.TRACE = _TRACE_LEVEL  # type: ignore[attr-defined]


def _configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    fmt = logging.Formatter('%(asctime)s | %(levelname)-8s | %(name)s | %(message)s')
    root = logging.getLogger()
    root.setLevel(level)
    if root.handlers:
        for h in root.handlers:
            h.setLevel(level)
    else:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(fmt)
        root.addHandler(handler)


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
        export_dir = Path(args.export_dir) if args.export_dir else Path(app_config.exportFolder)
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
        choices=['TRACE', 'DEBUG', 'INFO', 'WARNING', 'ERROR'],
        help='Logging verbosity (default: INFO).',
    )
    parser.add_argument(
        '--workers', type=int, default=None, metavar='N',
        help=(
            'Number of parallel OCR worker threads. '
            f'Defaults to the CPU count ({os.cpu_count() or 4}). '
            'Use --workers 1 to disable multi-threading.'
        ),
    )
    parser.add_argument(
        '--echo-ids', metavar='ID[,ID,...]',
        help=(
            'Comma-separated list of echo scan IDs to reprocess (e.g. 0111,0231). '
            'IDs are the 4-digit zero-padded numbers in the echo_NNNN folder names. '
            'All other scans are skipped. Useful for debugging individual failures.'
        ),
    )
    parser.add_argument(
        '--ocr-backend', default='rapidocr', metavar='NAME',
        help=(
            'OCR backend to use. Built-in: rapidocr (default). '
            'Use scraping.ocr.register() to add custom backends.'
        ),
    )
    parser.add_argument(
        '--ocr-params', default='{}', metavar='JSON',
        help=(
            'JSON object of keyword arguments forwarded to the backend constructor. '
            'Example: \'{"text_score": 0.6}\' or \'{"use_angle_cls": true}\''
        ),
    )
    parser.add_argument(
        '--write-debug', action='store_true', default=False,
        help=(
            'Write debug crop images and OCR trace files for every processed echo. '
            'Output is placed in the session\'s raw/ sub-directories.'
        ),
    )
    args = parser.parse_args()
    _configure_logging(args.log_level)

    # -- Apply config overrides from CLI args --------------------------------
    if args.min_rarity is not None:
        app_config.echoMinRarity = args.min_rarity
    if args.min_level is not None:
        app_config.echoMinLevel = args.min_level

    # -- Configure OCR backend -----------------------------------------------
    try:
        ocr_params = json.loads(args.ocr_params)
    except json.JSONDecodeError as exc:
        logger.error('--ocr-params is not valid JSON: %s', exc)
        sys.exit(1)
    try:
        _ocr.set_default(args.ocr_backend, **ocr_params)
    except KeyError:
        logger.error(
            'Unknown OCR backend %r. Available: %s',
            args.ocr_backend, _ocr.list_backends(),
        )
        sys.exit(1)

    export_dir = Path(args.export_dir) if args.export_dir else Path(app_config.exportFolder)

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
    logger.info('Min rarity: %d', app_config.echoMinRarity)
    logger.info('Min level : %d', app_config.echoMinLevel)

    # -- Load raw scans -------------------------------------------------------
    scans: list[RawEchoScan] = loadRawScans(raw_dir)
    if not scans:
        logger.error('No raw scans found in %s', raw_dir)
        sys.exit(1)
    logger.info('Loaded %d raw scan(s)', len(scans))

    # -- Filter by --echo-ids -------------------------------------------------
    if args.echo_ids:
        requested: set[int] = set()
        for token in args.echo_ids.split(','):
            token = token.strip()
            if not token:
                continue
            try:
                requested.add(int(token))
            except ValueError:
                logger.error('Invalid echo ID %r — must be a number (e.g. 0111).', token)
                sys.exit(1)
        scans = [s for s in scans if s.index in requested]
        missing = requested - {s.index for s in scans}
        if missing:
            logger.warning(
                'Echo IDs not found in session: %s',
                ', '.join(f'{i:04d}' for i in sorted(missing)),
            )
        if not scans:
            logger.error('No scans remain after applying --echo-ids filter.')
            sys.exit(1)
        logger.info('Filtered to %d scan(s): %s', len(scans),
                    ', '.join(f'{s.index:04d}' for s in scans))

    # -- Process --------------------------------------------------------------
    workers: int = args.workers if args.workers is not None else (os.cpu_count() or 4)
    logger.info('Workers   : %d', workers)
    echoes = echoProcessor(scans, session_id, raw_dir, workers=workers, write_debug=args.write_debug)
    logger.info('Accepted %d / %d echo(es)', len(echoes), len(scans))

    # -- Write output ---------------------------------------------------------
    out_path = _write_output(echoes, output_dir)
    logger.info('Saved → %s', out_path)
    print(f'Done. {len(echoes)} echo(es) written to {out_path}')


if __name__ == '__main__':
    main()
