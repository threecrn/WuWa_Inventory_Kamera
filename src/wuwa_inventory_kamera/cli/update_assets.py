"""
wuwa_inventory_kamera.cli.update_assets
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Inspect and repair the managed local asset cache.

Usage
-----
Show per-family status::

    wuwa-assets status

Download missing managed assets::

    wuwa-assets update

Redownload managed assets even when they already exist::

    wuwa-assets update --force
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Sequence

from ..config.app_config import app_config, basePATH
from ..updater.assets import AssetAuditResult, AssetFamilyStatus, BaseAssetsUpdater

_DEFAULT_SOURCE_MANIFEST = (
    basePATH / 'scratchpad' / 'asset-repo' / 'Wuthering-Waves-GameAssets' / 'ls-files-t'
)


def _configure_logging(level_name: str) -> None:
    level = getattr(logging, str(level_name).upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        logging.basicConfig(
            level=level,
            format='%(levelname)s | %(name)s | %(message)s',
        )
        return
    for handler in root.handlers:
        handler.setLevel(level)


class _ConsoleAssetsUpdater(BaseAssetsUpdater):
    def _onProgress(self, file_name: str, percent: float) -> None:
        print(f'{percent:6.2f}% {file_name}')

    def _onFinished(self) -> None:
        print('Asset update finished.')


def _print_status(statuses: tuple[AssetFamilyStatus, ...]) -> None:
    if not statuses:
        print('No managed assets were discovered.')
        return

    for status in statuses:
        print(
            f'{status.family}: {status.existing}/{status.total} present, '
            f'{status.missing} missing'
        )


def _print_audit(result: AssetAuditResult, *, list_missing: bool) -> None:
    print(f'Checked {result.checked} catalog path(s) against {result.manifest_path}')
    print(f'Present: {result.present}; missing: {len(result.missing)}')
    if list_missing and result.missing:
        for path in result.missing:
            print(path)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='wuwa-assets',
        description='Inspect and repair the managed WuWa asset cache.',
    )
    parser.add_argument(
        '--log-level',
        default=app_config.logLevel,
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        help='Logging level for asset preparation and downloads.',
    )

    subparsers = parser.add_subparsers(dest='command')

    subparsers.add_parser(
        'status',
        help='Show per-family managed asset status without downloading files.',
    )

    update_parser = subparsers.add_parser(
        'update',
        help='Download missing managed assets.',
    )
    update_parser.add_argument(
        '--force',
        action='store_true',
        help='Redownload managed assets even when they already exist locally.',
    )

    audit_parser = subparsers.add_parser(
        'audit',
        help='Compare catalog image references against a source-manifest file such as ls-files-t.',
    )
    audit_parser.add_argument(
        '--source-manifest',
        default=str(_DEFAULT_SOURCE_MANIFEST),
        metavar='PATH',
        help='Path to a source manifest file. Defaults to the local scratchpad ls-files-t copy.',
    )
    audit_parser.add_argument(
        '--list-missing',
        action='store_true',
        help='Print each missing source path on its own line.',
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    _configure_logging(args.log_level)

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == 'status':
        _print_status(BaseAssetsUpdater().collect_status())
        return 0

    if args.command == 'update':
        _ConsoleAssetsUpdater(force=bool(args.force)).run()
        return 0

    if args.command == 'audit':
        manifest_path = Path(args.source_manifest)
        try:
            result = BaseAssetsUpdater().audit_game_asset_source_manifest(manifest_path)
        except FileNotFoundError:
            print(f'Source manifest not found: {manifest_path}')
            return 1
        _print_audit(result, list_missing=bool(args.list_missing))
        return 1 if result.missing else 0

    parser.error(f'Unsupported command: {args.command}')
    return 2


if __name__ == '__main__':
    raise SystemExit(main())