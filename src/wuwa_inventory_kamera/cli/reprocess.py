"""
wuwa_inventory_kamera.cli.reprocess
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Offline re-processing of raw WuWa inventory scan sessions — shipped as the
``wuwa-reprocess`` console script entry point.

Usage
-----
List available sessions::

    wuwa-reprocess --list [--export-dir export]

Reprocess a session (reads config from config/config.json in cwd)::

    wuwa-reprocess --session-id 2026-02-28_14-30-00
    wuwa-reprocess --raw-dir export/2026-02-28_14-30-00/raw

OcrService path (batched GPU OCR)::

    wuwa-reprocess --raw-dir ./raw
    wuwa-reprocess --raw-dir ./raw --provider dml
    wuwa-reprocess --raw-dir ./raw --max-batch-size 8

Quality filters and output location::

    wuwa-reprocess --raw-dir ./raw --min-rarity 4 --min-level 10
    wuwa-reprocess --session-id 2026-02-28_14-30-00 --output-dir ./out
    wuwa-reprocess --raw-dir ./raw --scan-ids 0111,0231

Weapon sessions::

    wuwa-reprocess --raw-dir captures/weapons-session/raw

Output
------
The matching inventory export file is written into *output-dir*, which
defaults to the session folder (the parent of the ``raw/`` directory).
"""
from __future__ import annotations

import json
import io
import logging
import os
import sys
from pathlib import Path


_RAW_SESSION_TYPES: dict[str, dict[str, str]] = {
    'echoes': {
        'directory_prefix': 'echo_',
        'item_label': 'echo',
        'output_filename': 'echoes_wuwainventorykamera.json',
    },
    'weapons': {
        'directory_prefix': 'weapon_',
        'item_label': 'weapon',
        'output_filename': 'weapons_wuwainventorykamera.json',
    },
    'characters': {
        'directory_prefix': 'char_',
        'item_label': 'character',
        'output_filename': 'characters_wuwainventorykamera.json',
    },
}

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
            if os.name == 'nt' and isinstance(h, logging.StreamHandler):
                stream = getattr(h, 'stream', None)
                if isinstance(stream, io.TextIOWrapper):
                    stream.reconfigure(encoding='utf-8', errors='replace')
    else:
        if os.name == 'nt' and isinstance(sys.stdout, io.TextIOWrapper):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
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


def _auto_providers() -> list[str]:
    """Return the best available ONNX Runtime provider list."""
    try:
        import onnxruntime as ort
        if 'DmlExecutionProvider' in ort.get_available_providers():
            return ['DmlExecutionProvider', 'CPUExecutionProvider']
    except Exception:
        pass
    return ['CPUExecutionProvider']

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


def _raw_session_counts(raw_dir: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for session_type, config in _RAW_SESSION_TYPES.items():
        prefix = config['directory_prefix']
        counts[session_type] = len(
            [path for path in raw_dir.glob(f'{prefix}*/') if path.is_dir()]
        )
    return counts


def _detect_raw_session_kind(raw_dir: Path) -> str | None:
    counts = _raw_session_counts(raw_dir)
    present = [session_type for session_type, count in counts.items() if count > 0]
    if len(present) == 1:
        return present[0]
    return None


def _describe_raw_session(raw_dir: Path) -> str:
    counts = _raw_session_counts(raw_dir)
    present = [
        (session_type, count)
        for session_type, count in counts.items()
        if count > 0
    ]
    if not present:
        return 'no supported raw scans'
    if len(present) == 1:
        session_type, count = present[0]
        label = _RAW_SESSION_TYPES[session_type]['item_label']
        return f'{count} raw {label} scan{"s" if count != 1 else ""}'
    return 'mixed raw scans (' + ', '.join(
        f'{count} {session_type}' for session_type, count in present
    ) + ')'


def _write_output(records: list[dict] | dict[str, dict], output_dir: Path, filename: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / filename
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(records, f, indent=2, ensure_ascii=False)
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


def _load_session_scans(raw_dir: Path, session_kind: str) -> list:
    from ..scraping.utils.common import (
        loadCharacterRawScans,
        loadRawScans,
        loadWeaponRawScans,
    )

    if session_kind == 'echoes':
        return loadRawScans(raw_dir)
    if session_kind == 'weapons':
        return loadWeaponRawScans(raw_dir)
    if session_kind == 'characters':
        return loadCharacterRawScans(raw_dir)
    raise ValueError(f'Unsupported raw session kind: {session_kind!r}')


def _filter_scans(
    scans: list,
    *,
    scan_ids: str | None,
    scan_id_range: str | None,
    scan_label: str,
) -> list:
    if scan_ids:
        requested: set[int] = set()
        for token in scan_ids.split(','):
            token = token.strip()
            if not token:
                continue
            try:
                requested.add(int(token))
            except ValueError:
                logger.error('Invalid scan ID %r — must be a number (e.g. 0111).', token)
                sys.exit(1)
        scans = [scan for scan in scans if scan.index in requested]
        missing = requested - {scan.index for scan in scans}
        if missing:
            logger.warning(
                '%s scan IDs not found in session: %s',
                scan_label.capitalize(),
                ', '.join(f'{index:04d}' for index in sorted(missing)),
            )
        if not scans:
            logger.error('No scans remain after applying --scan-ids filter.')
            sys.exit(1)
        logger.info(
            'Filtered to %d %s scan(s): %s',
            len(scans),
            scan_label,
            ', '.join(f'{scan.index:04d}' for scan in scans),
        )

    if scan_id_range:
        try:
            start_str, end_str = scan_id_range.split(',')
            start_id, end_id = int(start_str.strip()), int(end_str.strip())
        except ValueError:
            logger.error(
                'Invalid --scan-id-range format: %r. Expected START,END (e.g. 0,100).',
                scan_id_range,
            )
            sys.exit(1)
        if start_id > end_id:
            logger.error('Invalid --scan-id-range: START must be <= END.')
            sys.exit(1)
        scans = [scan for scan in scans if start_id <= scan.index < end_id]
        if not scans:
            logger.error('No scans remain after applying --scan-id-range filter.')
            sys.exit(1)
        logger.info(
            'Filtered to %d %s scan(s) in range %d–%d.',
            len(scans),
            scan_label,
            start_id,
            end_id - 1,
        )

    return scans


# ---------------------------------------------------------------------------
# v2 path: OcrService + assemblers
# ---------------------------------------------------------------------------

def _run_echo_service(
    scans,
    raw_dir: Path,
    providers: list[str],
    min_rarity: int,
    min_level: int,
    write_debug: bool,
    max_batch_size: int,
    ocr_cache_path: Path | None,
) -> list[dict]:
    """
    Process *scans* using the new
    :class:`~wuwa_inventory_kamera.scraping.service.ocr_service.OcrService`.

    Each :class:`~scraping.models.rawScan.RawEchoScan` is converted to an
    :class:`~wuwa_inventory_kamera.scraping.service.captures.EchoCapture`
    by cropping the stored images using the scan's ``screenInfo``, submitted
    to the service, then collected in order.
    """
    from ..scraping.service.echo_reprocess import reprocess_echo_scans_with_service

    return reprocess_echo_scans_with_service(
        scans,
        providers=providers,
        min_rarity=min_rarity,
        min_level=min_level,
        write_debug=write_debug,
        max_batch_size=max_batch_size,
        ocr_cache_path=ocr_cache_path,
        raw_base=raw_dir,
    )


def _run_weapon_service(
    scans,
    raw_dir: Path,
    providers: list[str],
    min_rarity: int,
    min_level: int,
    write_debug: bool,
    max_batch_size: int,
    ocr_cache_path: Path | None,
) -> list[dict]:
    from ..scraping.service.weapon_reprocess import reprocess_weapon_scans_with_service

    return reprocess_weapon_scans_with_service(
        scans,
        providers=providers,
        min_rarity=min_rarity,
        min_level=min_level,
        write_debug=write_debug,
        max_batch_size=max_batch_size,
        ocr_cache_path=ocr_cache_path,
        raw_base=raw_dir,
    )


def _run_character_service(
    scans,
    raw_dir: Path,
    providers: list[str],
    write_debug: bool,
    max_batch_size: int,
    ocr_cache_path: Path | None,
) -> dict[str, dict]:
    from ..scraping.service.character_reprocess import reprocess_character_scans_with_service

    return reprocess_character_scans_with_service(
        scans,
        providers=providers,
        write_debug=write_debug,
        max_batch_size=max_batch_size,
        ocr_cache_path=ocr_cache_path,
        raw_base=raw_dir,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog='wuwa-reprocess',
        description=(
            'Re-run OCR processing on a previously captured WuWa inventory scan session.\n'
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
            'Directory for the generated *_wuwainventorykamera.json export. '
            'Defaults to the session folder (parent of raw/).'
        ),
    )
    parser.add_argument(
        '--min-rarity', type=int, choices=range(1, 6), metavar='1-5', default=None,
        help='Minimum rarity to include (overrides the session-type config default).',
    )
    parser.add_argument(
        '--min-level', type=int, metavar='LEVEL', default=None,
        help='Minimum level to include (echo sessions: 0-25; weapon sessions: 0-90).',
    )
    parser.add_argument(
        '--provider', choices=['cpu', 'dml'], default=None,
        help=(
            '"dml" = DirectML GPU (Windows, DirectX 12); "cpu" = CPU only. '
            'Applies to the OCR service backend.'
        ),
    )
    parser.add_argument(
        '--log-level', default='INFO',
        choices=['TRACE', 'DEBUG', 'INFO', 'WARNING', 'ERROR'],
        help='Logging verbosity (default: INFO).',
    )
    parser.add_argument(
        '--write-debug', action='store_true', default=False,
        help='Write debug crop images and OCR trace files for every scan.',
    )
    parser.add_argument(
        '--ocr-cache', metavar='PATH', default=None,
        help=(
            'SQLite cache path for the generalized OCR cache used by region specs. '
            'Defaults to the configured app cache path when available.'
        ),
    )

    parser.add_argument(
        '--max-batch-size', type=int, default=8, metavar='N',
        help=(
            'Maximum captures drained per OCR batch '
            '(default: 8).'
        ),
    )
    parser.add_argument(
        '--scan-id-range', '--echo-id-range', dest='scan_id_range', metavar='START,END',
        help=(
            'Range of scan IDs to reprocess (e.g. 0,100). '
            'IDs are zero-padded four-digit numbers (e.g. 0001). '
        ),
    )
    parser.add_argument(
        '--scan-ids', '--echo-ids', dest='scan_ids', metavar='ID[,ID,...]',
        help=(
            'Comma-separated scan IDs to reprocess (e.g. 0111,0231). '
            'All other scans are skipped.'
        ),
    )

    args = parser.parse_args()
    _configure_logging(args.log_level)

    if args.max_batch_size < 1:
        logger.error('--max-batch-size must be >= 1.')
        sys.exit(1)

    # Import project config (from the existing non-src package for now)
    # This is intentionally kept as a lazy import so that the module can be
    # imported in test environments without a config file present.
    try:
        from ..config.app_config import app_config
        export_folder: str = app_config.exportFolder
        default_ocr_cache = Path(app_config.ocrCachePath)
    except Exception:
        app_config = None  # type: ignore[assignment]
        export_folder = 'export'
        default_ocr_cache = None

    ocr_cache_path = Path(args.ocr_cache) if args.ocr_cache else default_ocr_cache

    export_dir = Path(args.export_dir) if args.export_dir else Path(export_folder)

    # ── --list ─────────────────────────────────────────────────────────────
    if args.list:
        sessions = _find_sessions(export_dir)
        if not sessions:
            print(f'No sessions with raw scan data found in: {export_dir}')
        else:
            print(f'Sessions in {export_dir} (newest first):')
            for s in sessions:
                print(f'  {s.name}  ({_describe_raw_session(s / "raw")})')
        sys.exit(0)

    # ── Resolve paths ──────────────────────────────────────────────────────
    raw_dir    = _resolve_raw_dir(args, export_folder)
    session_id = raw_dir.parent.name
    output_dir = Path(args.output_dir) if args.output_dir else raw_dir.parent
    session_kind = _detect_raw_session_kind(raw_dir)
    session_counts = _raw_session_counts(raw_dir)

    if session_kind is None:
        present = [
            f'{count} {kind}'
            for kind, count in session_counts.items()
            if count > 0
        ]
        if present:
            logger.error(
                'Ambiguous raw session type in %s: found %s. Expected a single supported session type.',
                raw_dir,
                ', '.join(present),
            )
        else:
            logger.error(
                'No supported raw scans found in %s. Expected echo_XXXX/, weapon_XXXX/, or char_XXXX/ directories.',
                raw_dir,
            )
        sys.exit(1)

    if session_kind == 'weapons':
        config_min_rarity = app_config.weaponsMinRarity if app_config else 1
        config_min_level = app_config.weaponsMinLevel if app_config else 0
        min_rarity: int = config_min_rarity if args.min_rarity is None else args.min_rarity
        min_level: int = config_min_level if args.min_level is None else args.min_level
        max_level = 90
    elif session_kind == 'echoes':
        config_min_rarity = app_config.echoMinRarity if app_config else 1
        config_min_level = app_config.echoMinLevel if app_config else 0
        min_rarity = config_min_rarity if args.min_rarity is None else args.min_rarity
        min_level = config_min_level if args.min_level is None else args.min_level
        max_level = 25
    else:
        if args.min_rarity is not None:
            logger.warning('--min-rarity is ignored for character sessions.')
        if args.min_level is not None:
            logger.warning('--min-level is ignored for character sessions.')
        min_rarity = 1
        min_level = 0
        max_level = 90

    scan_label = _RAW_SESSION_TYPES[session_kind]['item_label']

    if session_kind != 'characters' and (min_level < 0 or min_level > max_level):
        logger.error(
            '--min-level must be between 0 and %d for %s sessions.',
            max_level,
            session_kind,
        )
        sys.exit(1)

    logger.info('Session   : %s', session_id)
    logger.info('Type      : %s', session_kind)
    logger.info('Raw dir   : %s', raw_dir)
    logger.info('Output dir: %s', output_dir)
    if session_kind == 'characters':
        logger.info('Filters   : none (character sessions are unfiltered)')
    else:
        logger.info('Min rarity: %d', min_rarity)
        logger.info('Min level : %d', min_level)

    # ── Load raw scans ─────────────────────────────────────────────────────
    scans = _load_session_scans(raw_dir, session_kind)
    if not scans:
        logger.error('No raw scans found in %s', raw_dir)
        sys.exit(1)
    logger.info('Loaded %d raw scan(s)', len(scans))

    scans = _filter_scans(
        scans,
        scan_ids=args.scan_ids,
        scan_id_range=args.scan_id_range,
        scan_label=scan_label,
    )

    # ── Process ────────────────────────────────────────────────────────────
    providers = _PROVIDER_MAP[args.provider] if args.provider else _auto_providers()
    logger.info(
        'Mode      : OcrService  providers=%s  max_batch_size=%d',
        providers,
        args.max_batch_size,
    )
    if session_kind == 'weapons':
        records = _run_weapon_service(
            scans,
            raw_dir,
            providers=providers,
            min_rarity=min_rarity,
            min_level=min_level,
            write_debug=args.write_debug,
            max_batch_size=args.max_batch_size,
            ocr_cache_path=ocr_cache_path,
        )
    elif session_kind == 'characters':
        records = _run_character_service(
            scans,
            raw_dir,
            providers=providers,
            write_debug=args.write_debug,
            max_batch_size=args.max_batch_size,
            ocr_cache_path=ocr_cache_path,
        )
    else:
        records = _run_echo_service(
            scans,
            raw_dir,
            providers=providers,
            min_rarity=min_rarity,
            min_level=min_level,
            write_debug=args.write_debug,
            max_batch_size=args.max_batch_size,
            ocr_cache_path=ocr_cache_path,
        )

    logger.info('Accepted %d / %d %s scan(s)', len(records), len(scans), scan_label)

    # ── Write output ───────────────────────────────────────────────────────
    out_path = _write_output(
        records,
        output_dir,
        _RAW_SESSION_TYPES[session_kind]['output_filename'],
    )
    logger.info('Saved → %s', out_path)
    print(f'Done. {len(records)} {scan_label}(s) written to {out_path}')


if __name__ == '__main__':
    main()
