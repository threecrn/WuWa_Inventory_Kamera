#!/usr/bin/env python3
"""
CLI script to update game data files without GUI dependencies.
Downloads data from WutheringData repository and processes it.
"""

import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Import the BaseDataUpdater from ../updater/DatabaseUpdater.py
import sys
from pathlib import Path
# Add the parent directory to sys.path to import the updater module
sys.path.append(str(Path(__file__).resolve().parent.parent))
from updater.databaseUpdater import BaseDataUpdater

def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(description='Update WuWa game data files')
    parser.add_argument(
        '-l', '--lang',
        type=str,
        default=None,
        help='Language display name (e.g. English, Chinese). Leave empty to auto-detect.',
    )
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose output',
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    updater = BaseDataUpdater(lang=args.lang)
    updater.run()


if __name__ == '__main__':
    main()
