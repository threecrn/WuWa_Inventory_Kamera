"""
wuwa_inventory_kamera.cli.scan
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

CLI entry point for running a live inventory scan against the game.

This is the **headless alternative** to the Qt UI — it uses the same
game manipulation layer, OcrService, and scanning workflows but is
driven entirely from the command line.

Usage::

    # Scan echoes only, GPU-accelerated
    wuwa-scan --scrapers echoes --provider dml

    # Scan everything with level/rarity filters
    wuwa-scan --scrapers echoes weapons devItems resources \\
              --min-rarity 4 --min-level 10 --provider dml

    # Save raw screenshots for later reprocessing
    wuwa-scan --scrapers echoes --save-raw --provider dml

    # Set a specific echo sort order
    wuwa-scan --scrapers echoes --sort-order level_desc

Entry point
-----------
Registered as ``wuwa-scan`` console script in ``pyproject.toml``.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger('wuwa.scan')


# ---------------------------------------------------------------------------
# Provider map
# ---------------------------------------------------------------------------

_PROVIDER_MAP: dict[str, list[str]] = {
    'cpu': ['CPUExecutionProvider'],
    'dml': ['DmlExecutionProvider', 'CPUExecutionProvider'],
}


# ---------------------------------------------------------------------------
# Logging
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
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(fmt)
        root.addHandler(handler)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog='wuwa-scan',
        description=(
            'Run a live inventory scan against the WuWa game window.\n'
            'Requires the game to be running and on the main menu.'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        '--scrapers', nargs='+',
        choices=['echoes', 'weapons', 'devItems', 'resources'],
        default=['echoes'],
        help='Which inventory sections to scan (default: echoes).',
    )
    parser.add_argument(
        '--provider', choices=['cpu', 'dml'], default='cpu',
        help='ONNX execution provider (default: cpu).',
    )
    parser.add_argument(
        '--min-rarity', type=int, choices=range(1, 6), metavar='1-5', default=1,
        help='Minimum echo/weapon rarity to include (default: 1).',
    )
    parser.add_argument(
        '--min-level', type=int, choices=range(0, 26), metavar='0-25', default=0,
        help='Minimum echo/weapon level to include (default: 0).',
    )
    parser.add_argument(
        '--sort-order',
        choices=['newest', 'oldest', 'quality_desc', 'quality_asc',
                 'level_desc', 'level_asc'],
        default=None,
        help='Set inventory sort order before scanning.',
    )
    parser.add_argument(
        '--save-raw', action='store_true', default=False,
        help='Save raw screenshots for offline reprocessing.',
    )
    parser.add_argument(
        '--output-dir', metavar='PATH', default='export',
        help='Directory for output JSON (default: export).',
    )
    parser.add_argument(
        '--inventory-key', default='b',
        help='Keybind to open the game inventory (default: b).',
    )
    parser.add_argument(
        '--log-level', default='INFO',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        help='Logging verbosity (default: INFO).',
    )
    parser.add_argument(
        '--windowed', action='store_true', default=False,
        help='Enable windowed-mode capture (PrintWindow). '
             'Use when the game is not running fullscreen.',
    )
    parser.add_argument(
        '--echo-stat-cache', metavar='PATH', default=None,
        help=(
            'SQLite cache path for persistent echo stat-name/value OCR results. '
            'When set, repeated echo scans can skip OCR for identical stat crops.'
        ),
    )

    args = parser.parse_args()
    _configure_logging(args.log_level)

    try:
        from ..config.app_config import app_config
        default_echo_stat_cache = Path(app_config.echoStatCachePath)
        default_ocr_cache = Path(app_config.ocrCachePath)
    except Exception:
        default_echo_stat_cache = None
        default_ocr_cache = None

    # Delayed imports so --help is fast
    from ..game.navigation import SortOrder
    from ..scraping.scanning.session_orchestrator import (
        SessionOrchestrator,
    )

    sort_order = None
    if args.sort_order:
        sort_order = SortOrder[args.sort_order.upper()]

    save_raw = Path(args.output_dir) if args.save_raw else None
    echo_stat_cache_path = (
        Path(args.echo_stat_cache)
        if args.echo_stat_cache else default_echo_stat_cache
    )

    def on_progress(step: str, scanned: int, total: int) -> None:
        pct = (scanned / total * 100) if total else 0
        print(f'\r  [{step}] {scanned}/{total} ({pct:.0f}%)', end='', flush=True)

    orchestrator = SessionOrchestrator(
        scrapers=args.scrapers,
        ocr_providers=_PROVIDER_MAP[args.provider],
        min_rarity=args.min_rarity,
        min_level=args.min_level,
        sort_order=sort_order,
        save_raw=save_raw,
        inventory_key=args.inventory_key,
        on_progress=on_progress,
        windowed=args.windowed,
        echo_stat_cache_path=echo_stat_cache_path,
        ocr_cache_path=default_ocr_cache,
    )

    logger.info('Starting scan — scrapers=%s provider=%s', args.scrapers, args.provider)
    print('  Press Enter at any time to stop the scan early (results collected so far will be saved).')
    result = orchestrator.run()

    if 'error' in result:
        print(f'\nError: {result["error"]}')
        sys.exit(1)

    # Write output
    output_dir = Path(args.output_dir) / result.get('date', 'unknown')
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / 'scan_result.json'
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False, default=str)

    total_items = sum(
        len(v) for k, v in result.items()
        if isinstance(v, list)
    )
    if result.get('cancelled'):
        print(f'\nScan stopped early. {total_items} item(s) written to {out_path}')
    else:
        print(f'\nDone. {total_items} item(s) written to {out_path}')


if __name__ == '__main__':
    main()
