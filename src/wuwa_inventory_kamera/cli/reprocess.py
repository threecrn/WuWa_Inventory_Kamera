"""
wuwa_inventory_kamera.cli.reprocess
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Offline re-processing of raw WuWa echo scan sessions — shipped as the
``wuwa-reprocess`` console script entry point.

Usage
-----
List available sessions::

    wuwa-reprocess --list [--export-dir export]

Reprocess a session (reads config from config/config.json in cwd)::

    wuwa-reprocess --session-id 2026-02-28_14-30-00
    wuwa-reprocess --raw-dir export/2026-02-28_14-30-00/raw

OcrService path (batched GPU OCR, v2)::

    wuwa-reprocess --raw-dir ./raw --service
    wuwa-reprocess --raw-dir ./raw --service --provider dml

Legacy extractor path (original behaviour, default)::

    wuwa-reprocess --raw-dir ./raw --extractor rapid_coord --provider dml
    wuwa-reprocess --raw-dir ./raw --extractor rapid_coord --use-bw

Quality filters and output location::

    wuwa-reprocess --raw-dir ./raw --min-rarity 4 --min-level 10
    wuwa-reprocess --session-id 2026-02-28_14-30-00 --output-dir ./out

Output
------
``echoes_wuwainventorykamera.json`` is written into *output-dir*, which
defaults to the session folder (the parent of the ``raw/`` directory).
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Project root on sys.path so the legacy `scraping` package is importable.
# This file lives at src/wuwa_inventory_kamera/cli/reprocess.py, so the
# project root is four levels up.
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ---------------------------------------------------------------------------
# TRACE log level (below DEBUG)
# ---------------------------------------------------------------------------

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


logger = logging.getLogger('wuwa.reprocess')

# ---------------------------------------------------------------------------
# Provider map (shared by both paths)
# ---------------------------------------------------------------------------

_PROVIDER_MAP: dict[str, list[str]] = {
    'cpu': ['CPUExecutionProvider'],
    'dml': ['DmlExecutionProvider', 'CPUExecutionProvider'],
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_sessions(export_dir: Path) -> list[Path]:
    """Return session folders containing a ``raw/`` sub-directory, newest first."""
    if not export_dir.is_dir():
        return []
    return sorted(
        [d for d in export_dir.iterdir() if d.is_dir() and (d / 'raw').is_dir()],
        reverse=True,
    )


def _write_output(echoes: list[dict], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / 'echoes_wuwainventorykamera.json'
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(echoes, f, indent=2, ensure_ascii=False)
    return out_path


def _resolve_raw_dir(args, export_folder: str) -> Path:
    if args.raw_dir:
        raw_dir = Path(args.raw_dir)
        if not raw_dir.is_dir():
            logger.error('--raw-dir does not exist: %s', raw_dir)
            sys.exit(1)
        return raw_dir

    if args.session_id:
        export_dir = Path(args.export_dir) if args.export_dir else Path(export_folder)
        raw_dir = export_dir / args.session_id / 'raw'
        if not raw_dir.is_dir():
            logger.error(
                "raw/ directory not found for session %r.\n"
                "  Expected: %s\n"
                "  Use --export-dir if your export folder is not %s",
                args.session_id, raw_dir, export_dir,
            )
            sys.exit(1)
        return raw_dir

    logger.error('Provide --session-id or --raw-dir.  Use --list to see available sessions.')
    sys.exit(1)


# ---------------------------------------------------------------------------
# v2 path: OcrService + assemblers
# ---------------------------------------------------------------------------

def _run_service(
    scans,
    raw_dir: Path,
    session_id: str,
    providers: list[str],
    min_rarity: int,
    min_level: int,
    write_debug: bool,
) -> list[dict]:
    """
    Process *scans* using the new
    :class:`~wuwa_inventory_kamera.scraping.service.ocr_service.OcrService`.

    Each :class:`~scraping.models.rawScan.RawEchoScan` is converted to an
    :class:`~wuwa_inventory_kamera.scraping.service.captures.EchoCapture`
    by cropping the stored images using the scan's ``screenInfo``, submitted
    to the service, then collected in order.
    """
    from wuwa_inventory_kamera.scraping.service.ocr_service import OcrService
    from wuwa_inventory_kamera.scraping.service.captures import EchoCapture
    from game.screenInfo import ScreenInfo

    echoes: list[dict] = []

    with OcrService(
        providers=providers,
        min_rarity=min_rarity,
        min_level=min_level,
    ) as svc:
        futures = []
        for scan in scans:
            try:
                scan.load_images()
            except FileNotFoundError as exc:
                logger.error('Scan %d — images missing, skipping: %s', scan.index, exc)
                continue

            si = ScreenInfo(scan.screen_width, scan.screen_height).echoes

            card   = scan.full_screenshot[
                si.echoCard.y      : si.echoCard.y      + si.echoCard.h,
                si.echoCard.x      : si.echoCard.x      + si.echoCard.w,
            ]
            s_name = scan.full_screenshot[
                si.fullStatsName.y : si.fullStatsName.y + si.fullStatsName.h,
                si.fullStatsName.x : si.fullStatsName.x + si.fullStatsName.w,
            ]
            s_val  = scan.full_screenshot[
                si.fullStatsValue.y: si.fullStatsValue.y + si.fullStatsValue.h,
                si.fullStatsValue.x: si.fullStatsValue.x + si.fullStatsValue.w,
            ]
            sonata = scan.sonata_screenshot

            cap = EchoCapture(
                echo_index=scan.index,
                card=card,
                sonata=sonata,
                stats_name=s_name,
                stats_value=s_val,
                full_screenshot=scan.full_screenshot if write_debug else None,
            )
            futures.append((scan.index, svc.submit(cap)))

        for scan_index, fut in futures:
            try:
                result = fut.result(timeout=60)
            except Exception as exc:
                logger.error('Scan %d — service error: %s', scan_index, exc)
                continue
            if result.data is not None:
                echoes.append(result.data)
                for w in result.warnings:
                    logger.warning('Scan %d — %s', scan_index, w)
            else:
                logger.debug('Scan %d — rejected', scan_index)

    return echoes


# ---------------------------------------------------------------------------
# Legacy path: original echoProcessor
# ---------------------------------------------------------------------------

def _build_legacy_extractor(args, extractor_params: dict):
    """Build a StatsExtractor for the legacy echoProcessor path."""
    # These imports come from the existing (non-src) scraping package.
    from scraping.processing.statsExtractor import (
        RapidOcrStatsExtractor,
        RapidOcrCoordStatsExtractor,
        TesserOcrStatsExtractor,
        TesserOcrCoordStatsExtractor,
    )

    _EXTRACTOR_MAP = {
        'rapid':        RapidOcrStatsExtractor,
        'rapid_coord':  RapidOcrCoordStatsExtractor,
        'tesser':       TesserOcrStatsExtractor,
        'tesser_coord': TesserOcrCoordStatsExtractor,
    }

    extractor_cls = _EXTRACTOR_MAP[args.extractor]
    is_rapid = args.extractor.startswith('rapid')

    if args.provider and is_rapid and 'onnx_providers' not in extractor_params:
        extractor_params['onnx_providers'] = _PROVIDER_MAP[args.provider]
    elif args.provider and not is_rapid:
        logger.warning('--provider is ignored for non-RapidOCR extractors (%s)', args.extractor)

    try:
        extractor = extractor_cls(use_bw=args.use_bw, **extractor_params)
    except Exception as exc:
        logger.error('Failed to initialise extractor %r: %s', args.extractor, exc)
        sys.exit(1)

    logger.info(
        'Extractor : %s(use_bw=%r%s)',
        args.extractor, args.use_bw,
        f', {extractor_params}' if extractor_params else '',
    )
    if args.provider and is_rapid:
        logger.info('Provider  : %s', extractor_params.get('onnx_providers'))

    return extractor


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog='wuwa-reprocess',
        description=(
            'Re-run OCR processing on a previously captured WuWa echo scan session.\n'
            'Does not require the game, the GUI, or any Win32 APIs.'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        '--session-id', metavar='SESSION_ID',
        help='Session folder name under the export folder (e.g. 2026-02-28_14-30-00).',
    )
    source.add_argument(
        '--raw-dir', metavar='PATH',
        help='Explicit path to the raw/ directory.',
    )
    source.add_argument(
        '--list', action='store_true',
        help='List available sessions and exit.',
    )

    parser.add_argument(
        '--export-dir', metavar='PATH',
        help='Root export folder (default: from config/config.json, or "export").',
    )
    parser.add_argument(
        '--output-dir', metavar='PATH',
        help=(
            'Directory for echoes_wuwainventorykamera.json. '
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
        '--provider', choices=['cpu', 'dml'], default=None,
        help=(
            '"dml" = DirectML GPU (Windows, DirectX 12); "cpu" = CPU only. '
            'Applies to both the service path and the RapidOCR legacy extractors.'
        ),
    )
    parser.add_argument(
        '--log-level', default='INFO',
        choices=['TRACE', 'DEBUG', 'INFO', 'WARNING', 'ERROR'],
        help='Logging verbosity (default: INFO).',
    )
    parser.add_argument(
        '--write-debug', action='store_true', default=False,
        help='Write debug crop images and OCR trace files for every echo.',
    )

    # ── Mode selection ─────────────────────────────────────────────────────
    mode = parser.add_argument_group('processing mode (mutually exclusive with legacy options)')
    mode.add_argument(
        '--service', action='store_true', default=False,
        help=(
            'Use the v2 OcrService path (batched GPU OCR, assemblers). '
            'Ignores --extractor / --use-bw / --extractor-params / --workers.'
        ),
    )

    # ── Legacy extractor options ───────────────────────────────────────────
    legacy = parser.add_argument_group('legacy extractor options (ignored when --service is set)')
    legacy.add_argument(
        '--extractor',
        choices=['rapid', 'rapid_coord', 'tesser', 'tesser_coord'],
        default='rapid_coord',
        help='Stat extractor (default: rapid_coord).',
    )
    legacy.add_argument(
        '--use-bw', action='store_true', default=False,
        help='Force B/W pre-processing before OCR (always on for Tesseract extractors).',
    )
    legacy.add_argument(
        '--extractor-params', default='{}', metavar='JSON',
        help='JSON object of keyword arguments for the extractor constructor.',
    )
    legacy.add_argument(
        '--workers', type=int, default=None, metavar='N',
        help=(
            f'Parallel OCR worker threads (default: CPU count = {os.cpu_count() or 4}). '
            'Use --workers 1 to disable multi-threading.'
        ),
    )
    legacy.add_argument(
        '--echo-id-range', metavar='START,END',
        help=(
            'Range of echo scan IDs to reprocess (e.g. 0,100). '
            'IDs are zero-padded four-digit numbers (e.g. 0001). '
        ),
    )
    legacy.add_argument(
        '--echo-ids', metavar='ID[,ID,...]',
        help=(
            'Comma-separated echo scan IDs to reprocess (e.g. 0111,0231). '
            'All other scans are skipped.'
        ),
    )

    args = parser.parse_args()
    _configure_logging(args.log_level)

    # Import project config (from the existing non-src package for now)
    # This is intentionally kept as a lazy import so that the module can be
    # imported in test environments without a config file present.
    try:
        from properties.app_config import app_config
        export_folder: str = app_config.exportFolder
    except Exception:
        app_config = None  # type: ignore[assignment]
        export_folder = 'export'

    if app_config is not None:
        if args.min_rarity is not None:
            app_config.echoMinRarity = args.min_rarity
        if args.min_level is not None:
            app_config.echoMinLevel = args.min_level

    min_rarity: int = (app_config.echoMinRarity if app_config else 1) if args.min_rarity is None else args.min_rarity
    min_level:  int = (app_config.echoMinLevel  if app_config else 0) if args.min_level  is None else args.min_level

    export_dir = Path(args.export_dir) if args.export_dir else Path(export_folder)

    # ── --list ─────────────────────────────────────────────────────────────
    if args.list:
        sessions = _find_sessions(export_dir)
        if not sessions:
            print(f'No sessions with raw scan data found in: {export_dir}')
        else:
            print(f'Sessions in {export_dir} (newest first):')
            for s in sessions:
                echo_count = len(list((s / 'raw').glob('echo_*/')))
                print(f'  {s.name}  ({echo_count} raw scan{"s" if echo_count != 1 else ""})')
        sys.exit(0)

    # ── Resolve paths ──────────────────────────────────────────────────────
    raw_dir    = _resolve_raw_dir(args, export_folder)
    session_id = raw_dir.parent.name
    output_dir = Path(args.output_dir) if args.output_dir else raw_dir.parent

    logger.info('Session   : %s', session_id)
    logger.info('Raw dir   : %s', raw_dir)
    logger.info('Output dir: %s', output_dir)
    logger.info('Min rarity: %d', min_rarity)
    logger.info('Min level : %d', min_level)

    # ── Load raw scans ─────────────────────────────────────────────────────
    from scraping.utils.common import loadRawScans
    from scraping.models.rawScan import RawEchoScan

    scans: list[RawEchoScan] = loadRawScans(raw_dir)
    if not scans:
        logger.error('No raw scans found in %s', raw_dir)
        sys.exit(1)
    logger.info('Loaded %d raw scan(s)', len(scans))

    # ── Filter by --echo-ids ───────────────────────────────────────────────
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
        logger.info(
            'Filtered to %d scan(s): %s',
            len(scans), ', '.join(f'{s.index:04d}' for s in scans),
        )

    # ── Filter by --echo-id-range ───────────────────────────────────────────
    if args.echo_id_range:
        try:
            start_str, end_str = args.echo_id_range.split(',')
            start_id, end_id = int(start_str.strip()), int(end_str.strip())
        except ValueError:
            logger.error('Invalid --echo-id-range format: %r. Expected START,END (e.g. 0,100).', args.echo_id_range)
            sys.exit(1)
        if start_id > end_id:
            logger.error('Invalid --echo-id-range: START must be <= END.')
            sys.exit(1)
        scans = [s for s in scans if start_id <= s.index < end_id]
        if not scans:
            logger.error('No scans remain after applying --echo-id-range filter.')
            sys.exit(1)
        logger.info(
            'Filtered to %d scan(s) in range %d–%d.',
            len(scans), start_id, end_id - 1,
        )

    # ── Process ────────────────────────────────────────────────────────────
    if args.service:
        providers = _PROVIDER_MAP[args.provider] if args.provider else ['CPUExecutionProvider']
        logger.info('Mode      : OcrService (v2)  providers=%s', providers)
        echoes = _run_service(
            scans, raw_dir, session_id,
            providers=providers,
            min_rarity=min_rarity,
            min_level=min_level,
            write_debug=args.write_debug,
        )
    else:
        # Parse extractor params JSON
        try:
            extractor_params = json.loads(args.extractor_params)
        except json.JSONDecodeError as exc:
            logger.error('--extractor-params is not valid JSON: %s', exc)
            sys.exit(1)

        extractor = _build_legacy_extractor(args, extractor_params)
        workers: int = args.workers if args.workers is not None else (os.cpu_count() or 4)
        logger.info('Mode      : legacy extractor  workers=%d', workers)

        from scraping.processing.echoesProcessor import echoProcessor
        echoes = echoProcessor(
            scans, session_id, raw_dir,
            workers=workers,
            write_debug=args.write_debug,
            extractor=extractor,
        )

    logger.info('Accepted %d / %d echo(es)', len(echoes), len(scans))

    # ── Write output ───────────────────────────────────────────────────────
    out_path = _write_output(echoes, output_dir)
    logger.info('Saved → %s', out_path)
    print(f'Done. {len(echoes)} echo(es) written to {out_path}')


if __name__ == '__main__':
    main()
