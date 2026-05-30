"""
wuwa_inventory_kamera.ui.loading
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Loading screen — runs data + asset updaters in QThreads, then transitions
to the main application window.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QVBoxLayout, QWidget,
    QSpacerItem, QSizePolicy,
)
from qfluentwidgets import ProgressRing, BodyLabel

from ..config.app_config import basePATH
from ..updater.database import BaseDataUpdater
from ..updater.assets import BaseAssetsUpdater
from .config import cfg

logger = logging.getLogger('LoadingScreen')


# ---------------------------------------------------------------------------
# Updater threads
# ---------------------------------------------------------------------------

class DataUpdaterThread(QThread):
    updateProgress = Signal(int, str)
    updateFinished = Signal()

    def __init__(self):
        super().__init__()
        logger.debug("DataUpdaterThread initialized")

    def run(self):
        logger.info("Starting data update process")
        try:
            updater = _QtDataUpdater(
                lang=cfg.get(cfg.gameLanguage),
                source=cfg.get(cfg.dataSource),
                progress_signal=self.updateProgress,
                finished_signal=self.updateFinished,
            )
            updater.run()
            logger.info("Data update process completed successfully")
        except Exception as e:
            logger.error("Error during data update: %s", e, exc_info=True)
            self.updateFinished.emit()


class AssetsUpdaterThread(QThread):
    updateProgress = Signal(int, str)
    updateFinished = Signal()

    def __init__(self):
        super().__init__()
        logger.debug("AssetsUpdaterThread initialized")

    def run(self):
        logger.info("Starting assets update process")
        try:
            updater = _QtAssetsUpdater(
                progress_signal=self.updateProgress,
                finished_signal=self.updateFinished,
            )
            updater.run()
            logger.info("Assets update process completed successfully")
        except Exception as e:
            logger.error("Error during assets update: %s", e, exc_info=True)
            self.updateFinished.emit()


class _QtDataUpdater(BaseDataUpdater):
    """Thin adapter: emits Qt signals for progress / finished."""

    def __init__(self, lang, source, progress_signal, finished_signal):
        super().__init__(lang=lang, source=source)
        self._progress = progress_signal
        self._finished = finished_signal

    def _onProgress(self, file_name: str, percent: float) -> None:
        self._progress.emit(int(percent), file_name)

    def _onFinished(self) -> None:
        self._finished.emit()


class _QtAssetsUpdater(BaseAssetsUpdater):
    """Thin adapter: emits Qt signals for progress / finished."""

    def __init__(self, progress_signal, finished_signal, *, force: bool = False):
        super().__init__(force=force)
        self._progress = progress_signal
        self._finished = finished_signal

    def _onProgress(self, file_name: str, percent: float) -> None:
        self._progress.emit(int(percent), file_name)

    def _onFinished(self) -> None:
        self._finished.emit()


# ---------------------------------------------------------------------------
# Loading screen widget
# ---------------------------------------------------------------------------

class LoadingScreen(QWidget):
    def __init__(self):
        super().__init__()
        logger.debug("Initializing LoadingScreen")
        self.initWindow()
        self.setupUI()
        self.startDataUpdate()

    def initWindow(self):
        logger.debug("Setting up window properties")
        self.setFixedSize(1150, 700)
        self.setWindowIcon(QIcon(str(basePATH / 'assets' / 'icon.ico')))
        self.setWindowTitle('WuWa Inventory Kamera')

        desktop = QApplication.primaryScreen().availableGeometry()
        self.move(
            desktop.width() // 2 - self.width() // 2,
            desktop.height() // 2 - self.height() // 2,
        )
        logger.info("Window positioned at %d, %d", self.pos().x(), self.pos().y())

    def setupUI(self):
        logger.debug("Setting up UI components")
        self.vBoxLayout = QVBoxLayout(self)
        self.vBoxLayout.addSpacerItem(
            QSpacerItem(20, 40, QSizePolicy.Minimum, QSizePolicy.Expanding)
        )

        self.progress_ring = ProgressRing(self)
        self.progress_ring.setFixedSize(200, 200)
        self.progress_ring.setTextVisible(True)
        self.progress_ring.setValue(0)
        self.vBoxLayout.addWidget(self.progress_ring, 0, Qt.AlignHCenter)

        self.label = BodyLabel("Loading, please wait...", self)
        self.label.setStyleSheet("color: white; font-size: 18px;")
        self.label.setAlignment(Qt.AlignCenter)
        self.vBoxLayout.addWidget(self.label, 0, Qt.AlignHCenter)

        self.file_label = BodyLabel("", self)
        self.file_label.setStyleSheet("color: white; font-size: 14px;")
        self.file_label.setAlignment(Qt.AlignCenter)
        self.vBoxLayout.addWidget(self.file_label, 0, Qt.AlignHCenter)

        self.vBoxLayout.addSpacerItem(
            QSpacerItem(20, 40, QSizePolicy.Minimum, QSizePolicy.Expanding)
        )
        logger.info("UI setup completed")

    def startDataUpdate(self):
        if not bool(cfg.get(cfg.checkUpdateAtStartUp)):
            logger.info("Startup updates disabled by configuration")
            self.progress_ring.setValue(100)
            self.label.setText("Starting application...")
            self.file_label.setText("Startup updates disabled")
            QTimer.singleShot(0, self.on_updateFinished)
            return

        self.label.setText("Updating game data...")
        self.file_label.setText("")
        logger.info("Initializing and starting data update thread")
        self.dataUpdater_thread = DataUpdaterThread()
        self.dataUpdater_thread.updateProgress.connect(self.updateProgress)
        self.dataUpdater_thread.updateFinished.connect(self.startAssetsUpdate)
        self.dataUpdater_thread.start()

    def startAssetsUpdate(self):
        self.label.setText("Updating assets...")
        self.file_label.setText("")
        logger.info("Initializing and starting assets update thread")
        self.assetsUpdater_thread = AssetsUpdaterThread()
        self.assetsUpdater_thread.updateProgress.connect(self.updateProgress)
        self.assetsUpdater_thread.updateFinished.connect(self.on_updateFinished)
        self.assetsUpdater_thread.start()

    def updateProgress(self, value, file_name):
        self.progress_ring.setValue(value)
        self.label.setText("Updating resources...")
        self.file_label.setText(file_name)

    def on_updateFinished(self):
        logger.info("Data update finished, transitioning to main window")
        self.close()
        try:
            from .main_window import WuWaInventoryKamera
            self.main_window = WuWaInventoryKamera()
            self.main_window.show()
            logger.info("Main window displayed successfully")
        except Exception as e:
            logger.error("Error initializing main window: %s", e, exc_info=True)
