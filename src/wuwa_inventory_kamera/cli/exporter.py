"""
wuwa_inventory_kamera.cli.exporter
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

CLI wrapper for WutheringTools export conversion.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from ..exporter.wutheringtools import (
    _LocalizationMaps,
    _extract_payload,
    _resolve_sonata,
    _stat_token,
    build_wutheringtools_export,
    write_wutheringtools_export,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='wuwa-exporter',
        description=(
            'Convert WuWa Inventory Kamera character + echo exports into '
            'WutheringTools export format.'
        ),
    )
    parser.add_argument(
        '--characters',
        required=True,
        metavar='PATH',
        help='Path to character export JSON (characters_wuwainventorykamera.json or scan_result.json).',
    )
    parser.add_argument(
        '--echoes',
        required=True,
        metavar='PATH',
        help='Path to echo export JSON (echoes_wuwainventorykamera.json or scan_result.json).',
    )
    parser.add_argument(
        '--output',
        metavar='PATH',
        default='wutheringtools_export.json',
        help='Output JSON path (default: wutheringtools_export.json).',
    )
    parser.add_argument(
        '--language',
        default='en',
        help='Localization language code for display-name mapping (default: en).',
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    output_path = write_wutheringtools_export(
        characters_path=Path(args.characters),
        echoes_path=Path(args.echoes),
        output_path=Path(args.output),
        language=str(args.language),
    )

    print(f'Wrote WutheringTools export to {output_path}')


if __name__ == '__main__':
    main()
