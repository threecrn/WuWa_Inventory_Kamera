"""
wuwa_inventory_kamera.app
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Qt application bootstrap — creates QApplication and shows the loading screen.
"""
from __future__ import annotations

import sys
import logging
import multiprocessing
from pathlib import Path
from logging.handlers import TimedRotatingFileHandler

from PySide6.QtWidgets import QApplication

from .ui.loading import LoadingScreen

logger = logging.getLogger('WuWaInventoryKamera')


def configure_logging() -> None:
    """Set up file + console logging."""
    Path('logs').mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        fmt='%(asctime)s|%(levelname)s|%(name)s|%(message)s'
    )

    debug_handler = TimedRotatingFileHandler(
        filename='./logs/WuWaInventoryKamera.debug.log',
        when='midnight', interval=1, backupCount=4, encoding='utf-8',
    )
    debug_handler.setFormatter(formatter)
    debug_handler.setLevel(logging.DEBUG)

    info_handler = TimedRotatingFileHandler(
        filename='./logs/WuWaInventoryKamera.log',
        when='midnight', interval=1, backupCount=4, encoding='utf-8',
    )
    info_handler.setFormatter(formatter)
    info_handler.setLevel(logging.INFO)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.DEBUG)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(console_handler)
    root.addHandler(debug_handler)
    root.addHandler(info_handler)


def start() -> None:
    """Launch the Qt application."""
    app = QApplication(sys.argv)
    loading_screen = LoadingScreen()
    loading_screen.show()
    app.exec()


def main() -> None:
    """Application entry point (called by ``wuwa-app`` or ``main.py``)."""
    multiprocessing.freeze_support()
    configure_logging()
    logger.info("WuWa Inventory Kamera initialized")
    try:
        start()
    except Exception:
        logger.critical("Main application crashed", exc_info=True)
    logger.info("Application closed")
