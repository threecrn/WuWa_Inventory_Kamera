"""
wuwa_inventory_kamera.ui.home
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Home interface — scanner control panel + result-viewer guidance.

The V2 scan path uses :class:`SessionOrchestrator` running on a
``QThread`` instead of the removed V1 scraper pipeline.
"""
from __future__ import annotations

import os
import logging
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtWidgets import (
    QHBoxLayout, QVBoxLayout, QFrame,
    QFileDialog, QMessageBox, QWidget,
)
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import (
    PushButton, PrimaryPushButton, CheckBox,
    BodyLabel, SpinBox,
    InfoBar, InfoBarPosition,
    ProgressBar,
)

from ..output_serialization import build_standalone_exports
from .config import cfg
from ..scraping.utils.common import savingScraped

logger = logging.getLogger('HomeInterface')


# ---------------------------------------------------------------------------
# Background scan thread (V2)
# ---------------------------------------------------------------------------

class ScanThread(QThread):
    """Runs a :class:`SessionOrchestrator` session on a background thread.

    Signals
    -------
    progress(str, int, int)
        ``(step, scanned, total)`` forwarded from the orchestrator.
    finished(object)
        The result dict returned by ``SessionOrchestrator.run()``.
    error(str)
        Human-readable error if the scan crashes.
    """

    progress = Signal(str, int, int)
    finished = Signal(object)
    error = Signal(str)

    def __init__(
        self,
        scrapers: list[str],
        ocr_providers: list[str] | None,
        min_rarity: int,
        min_level: int,
        weapon_min_rarity: int | None,
        weapon_min_level: int | None,
        inventory_key: str,
        export_folder: str,
        ocr_cache_path: str | None = None,
        save_raw: bool = False,
        max_batch_size: int = 8,
        windowed: bool = False,
        write_debug: bool = False,
    ):
        super().__init__()
        self._scrapers = scrapers
        self._ocr_providers = ocr_providers
        self._min_rarity = min_rarity
        self._min_level = min_level
        self._weapon_min_rarity = min_rarity if weapon_min_rarity is None else weapon_min_rarity
        self._weapon_min_level = min_level if weapon_min_level is None else weapon_min_level
        self._inventory_key = inventory_key
        self._export_folder = export_folder
        self._ocr_cache_path = ocr_cache_path
        self._save_raw = save_raw
        self._max_batch_size = max_batch_size
        self._windowed = windowed
        self._write_debug = write_debug

    def run(self):  # noqa: D102  (QThread override)
        from ..scraping.scanning.session_orchestrator import SessionOrchestrator
        from ..game.navigation import SortOrder

        try:
            orch = SessionOrchestrator(
                scrapers=self._scrapers,
                ocr_providers=self._ocr_providers,
                min_rarity=self._min_rarity,
                min_level=self._min_level,
                weapon_min_rarity=self._weapon_min_rarity,
                weapon_min_level=self._weapon_min_level,
                sort_order=SortOrder.LEVEL,
                save_raw=Path(self._export_folder) if self._save_raw else None,
                inventory_key=self._inventory_key,
                max_batch_size=self._max_batch_size,
                on_progress=lambda step, s, t: self.progress.emit(step, s, t),
                windowed=self._windowed,
                ocr_cache_path=(
                    Path(self._ocr_cache_path)
                    if self._ocr_cache_path else None
                ),
                write_debug=self._write_debug,
            )
            result = orch.run()
            self.finished.emit(result)
        except Exception as exc:
            logger.exception("Scan thread crashed")
            self.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Home interface
# ---------------------------------------------------------------------------

class HomeInterface(QWidget):
    """Main widget with scan controls on the left and guidance on the right."""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("homeUI")

        self.lControlPanel = LControlPanel(self)
        self.lControlPanel.signalNotifier.connect(self.showNotification)

        self.tControlPanel = TControlPanel(self)
        self.rightWidget = QWidget(self)

        self.rightSide = QVBoxLayout()
        self.rightSide.addWidget(self.tControlPanel)
        self.rightSide.addWidget(self.rightWidget)

        self.hBoxLayout = QHBoxLayout(self)
        self.hBoxLayout.addWidget(self.lControlPanel, 0)
        self.hBoxLayout.addLayout(self.rightSide, 1)

        self._initViewerGuidance()

    def _initViewerGuidance(self) -> None:
        container = QVBoxLayout(self.rightWidget)
        container.setContentsMargins(0, 0, 0, 0)
        container.addStretch(1)

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    def showNotification(self, _type: str, title: str, content: str):
        bar_fn = {
            'success': InfoBar.success,
            'warning': InfoBar.warning,
            'error': InfoBar.error,
            'failed': InfoBar.warning,
        }.get(_type, InfoBar.info)

        bar_fn(
            title=title,
            content=content,
            orient=Qt.Orientation.Vertical,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=-1,
            parent=self,
        )


# ---------------------------------------------------------------------------
# Left control panel
# ---------------------------------------------------------------------------

class LControlPanel(QFrame):
    """Scanner checkboxes + Scan / Export buttons."""

    signalNotifier = Signal(str, str, str)

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self._scan_thread: ScanThread | None = None
        self.__initUI()

    def __initUI(self):
        self.scannerLabel = BodyLabel('Scanner', self)
        self.scanCharacters = CheckBox('Characters', self)
        self.scanWeapons = CheckBox('Weapons', self)
        self.scanEchoes = CheckBox('Echoes', self)
        self.scanDevItems = CheckBox('Development Items', self)
        self.scanResources = CheckBox('Resources', self)
        self.scanAchievements = CheckBox('Achievements', self)

        self.closeLabel = BodyLabel("Press 'ENTER' to cancel the scan.")

        self.scanProgressLabel = BodyLabel('Scan: —', self)
        self.scanProgressBar = ProgressBar(self)
        self.scanProgressBar.setRange(0, 100)
        self.scanProgressBar.setValue(0)

        self.processProgressLabel = BodyLabel('Processing: —', self)
        self.processProgressBar = ProgressBar(self)
        self.processProgressBar.setRange(0, 100)
        self.processProgressBar.setValue(0)

        self.openExportFolder = PushButton('Export Folder', icon=FIF.FOLDER, parent=self)
        self.openExportFolder.clicked.connect(self.openFolder)

        self.startScanning = PrimaryPushButton(FIF.PLAY, 'Scan', self)
        self.startScanning.clicked.connect(self.runScraper)

        self.panelLayout = QVBoxLayout(self)
        self.__setupLayout()
        self.__connectSignals()

    def __setupLayout(self):
        self.panelLayout.setSpacing(8)
        self.panelLayout.setContentsMargins(14, 16, 14, 14)
        self.panelLayout.setAlignment(Qt.AlignTop)

        self.panelLayout.addWidget(self.scannerLabel)
        self.panelLayout.addWidget(self.scanCharacters)
        self.panelLayout.addWidget(self.scanWeapons)
        self.panelLayout.addWidget(self.scanEchoes)
        self.panelLayout.addWidget(self.scanDevItems)
        self.panelLayout.addWidget(self.scanResources)
        self.panelLayout.addWidget(self.scanAchievements)

        self.panelLayout.addStretch()
        self.panelLayout.addWidget(self.closeLabel)
        self.panelLayout.addWidget(self.scanProgressLabel)
        self.panelLayout.addWidget(self.scanProgressBar)
        self.panelLayout.addWidget(self.processProgressLabel)
        self.panelLayout.addWidget(self.processProgressBar)
        self.panelLayout.addWidget(self.openExportFolder)
        self.panelLayout.addWidget(self.startScanning)

        self.__setInitialValues()

    def __setInitialValues(self):
        self.scanCharacters.setChecked(cfg.scanCharacters.value)
        self.scanWeapons.setChecked(cfg.scanWeapons.value)
        self.scanEchoes.setChecked(cfg.scanEchoes.value)
        self.scanDevItems.setChecked(cfg.scanDevItems.value)
        self.scanResources.setChecked(cfg.scanResources.value)
        self.scanAchievements.setChecked(cfg.scanAchievements.value)
        self.onValueChanged()

    def __connectSignals(self):
        self.scanResources.stateChanged.connect(self.onValueChanged)
        self.scanDevItems.stateChanged.connect(self.onValueChanged)
        self.scanEchoes.stateChanged.connect(self.onValueChanged)
        self.scanCharacters.stateChanged.connect(self.onValueChanged)
        self.scanWeapons.stateChanged.connect(self.onValueChanged)
        self.scanAchievements.stateChanged.connect(self.onAchievementsToggled)

    # ------------------------------------------------------------------
    # Checkbox logic
    # ------------------------------------------------------------------

    def onValueChanged(self):
        cfg.scanResources.value = self.scanResources.isChecked()
        cfg.scanDevItems.value = self.scanDevItems.isChecked()
        cfg.scanEchoes.value = self.scanEchoes.isChecked()
        cfg.scanCharacters.value = self.scanCharacters.isChecked()
        cfg.scanWeapons.value = self.scanWeapons.isChecked()

        if any([
            self.scanCharacters.isChecked(),
            self.scanWeapons.isChecked(),
            self.scanEchoes.isChecked(),
            self.scanDevItems.isChecked(),
            self.scanResources.isChecked(),
        ]):
            self.scanAchievements.setChecked(False)
            self.scanAchievements.setDisabled(True)
        else:
            self.onAchievementsToggled()
            self.scanAchievements.setDisabled(False)

        cfg.save()

    def onAchievementsToggled(self):
        if self.scanAchievements.isChecked():
            self.setOtherCheckboxesEnabled(False)
        else:
            self.setOtherCheckboxesEnabled(True)
        cfg.scanAchievements.value = self.scanAchievements.isChecked()
        cfg.save()

    def setOtherCheckboxesEnabled(self, enabled):
        self.scanCharacters.setDisabled(not enabled)
        self.scanWeapons.setDisabled(not enabled)
        self.scanEchoes.setDisabled(not enabled)
        self.scanDevItems.setDisabled(not enabled)
        self.scanResources.setDisabled(not enabled)

    # ------------------------------------------------------------------
    # Scan (V2 — SessionOrchestrator on QThread)
    # ------------------------------------------------------------------

    def _build_scraper_list(self) -> list[str]:
        """Return the list of scraper names selected by the user."""
        scrapers: list[str] = []
        if self.scanCharacters.isChecked():
            scrapers.append('characters')
        if self.scanEchoes.isChecked():
            scrapers.append('echoes')
        if self.scanWeapons.isChecked():
            scrapers.append('weapons')
        if self.scanDevItems.isChecked():
            scrapers.append('devItems')
        if self.scanResources.isChecked():
            scrapers.append('resources')
        if self.scanAchievements.isChecked():
            scrapers.append('achievements')
        return scrapers

    def runScraper(self):
        scrapers = self._build_scraper_list()
        if not scrapers:
            self.signalNotifier.emit(
                'warning', 'Warning',
                'Select at least one scanner checkbox.',
            )
            return

        if self._scan_thread is not None and self._scan_thread.isRunning():
            self.signalNotifier.emit(
                'warning', 'Scan in progress',
                'A scan is already running. Press ENTER in-game to cancel it.',
            )
            return

        dialog = QMessageBox(self)
        dialog.setWindowTitle('Start Scan')
        dialog.setText('Ready to start scanning?')
        dialog.setInformativeText(
            "To cancel the scan, press 'ENTER'\n"
            'Do not move the mouse during the scan.'
        )
        start_button = dialog.addButton('Start', QMessageBox.ButtonRole.AcceptRole)
        dialog.addButton(QMessageBox.StandardButton.Cancel)
        dialog.setDefaultButton(start_button)
        dialog.exec()
        if dialog.clickedButton() is not start_button:
            return

        self.startScanning.setEnabled(False)

        backend = cfg.ocrBackend.value
        if backend == 'CPU only':
            ocr_providers = ['CPUExecutionProvider']
        else:
            ocr_providers = ['DmlExecutionProvider', 'CPUExecutionProvider']

        self._scan_thread = ScanThread(
            scrapers=scrapers,
            ocr_providers=ocr_providers,
            min_rarity=cfg.echoMinRarity.value,
            min_level=cfg.echoMinLevel.value,
            weapon_min_rarity=cfg.weaponsMinRarity.value,
            weapon_min_level=cfg.weaponsMinLevel.value,
            inventory_key=cfg.get(cfg.inventoryKeybind).lower(),
            export_folder=cfg.get(cfg.exportFolder),
            ocr_cache_path=cfg.get(cfg.ocrCachePath),
            save_raw=cfg.saveRaw.value,
            max_batch_size=cfg.ocrBatchSize.value,
            windowed=not cfg.gameFullscreen.value,
            write_debug=cfg.writeDebug.value,
        )
        self._scan_thread.progress.connect(self._onScanProgress)
        self._scan_thread.finished.connect(self._onScanFinished)
        self._scan_thread.error.connect(self._onScanError)
        self._scan_thread.start()

    def _onScanProgress(self, step: str, scanned: int, total: int):
        logger.debug("Scan progress: %s %d/%d", step, scanned, total)
        pct = int(scanned * 100 / total) if total > 0 else 0
        if step in ('echoes', 'weapons', 'devItems', 'resources', 'characters', 'achievements'):
            self.scanProgressLabel.setText(f'Scan ({step}): {scanned}/{total}')
            self.scanProgressBar.setValue(pct)
        elif step == 'echoes:processing':
            self.processProgressLabel.setText(f'Processing: {scanned}/{total}')
            self.processProgressBar.setValue(pct)

    def _onScanFinished(self, result: dict):
        self.startScanning.setEnabled(True)
        self._scan_thread = None
        self.scanProgressLabel.setText('Scan: —')
        self.scanProgressBar.setValue(0)
        self.processProgressLabel.setText('Processing: —')
        self.processProgressBar.setValue(0)

        if 'error' in result:
            self.signalNotifier.emit('error', 'Scan Error', result['error'])
            return

        session_id = result.get('date', '')

        exports = build_standalone_exports(result)
        if exports:
            savingScraped(exports, START_DATE=session_id)

        characters = result.get('characters')

        cancelled = result.get('cancelled', False)
        summary_parts = []
        for key in ('echoes', 'weapons', 'devItems', 'resources'):
            data = result.get(key)
            if isinstance(data, list):
                summary_parts.append(f"{key}: {len(data)}")

        if isinstance(characters, dict) and 'error' not in characters:
            summary_parts.append(f'characters: {len(characters)}')

        summary = ', '.join(summary_parts) if summary_parts else 'No items scanned'

        if cancelled:
            self.signalNotifier.emit('warning', 'Scan Cancelled', summary)
        else:
            self.signalNotifier.emit('success', 'Scan Complete', summary)

    def _onScanError(self, message: str):
        self.startScanning.setEnabled(True)
        self._scan_thread = None
        self.scanProgressLabel.setText('Scan: —')
        self.scanProgressBar.setValue(0)
        self.processProgressLabel.setText('Processing: —')
        self.processProgressBar.setValue(0)
        self.signalNotifier.emit('error', 'Scan Failed', message)

    # ------------------------------------------------------------------
    # Export folder
    # ------------------------------------------------------------------

    def openFolder(self):
        path = Path(cfg.get(cfg.exportFolder))
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)

    # ------------------------------------------------------------------
    # Reprocess session
    # ------------------------------------------------------------------

    def runReprocessSession(self):
        from ..scraping.service.echo_reprocess import reprocess_echo_scans_with_service
        from ..scraping.utils.common import loadRawScans

        folder = QFileDialog.getExistingDirectory(
            self,
            self.tr('Choose session folder to reprocess'),
            cfg.get(cfg.exportFolder),
        )
        if not folder:
            return

        session_id = Path(folder).name
        raw_base = Path(folder) / 'raw'

        if not raw_base.exists():
            self.signalNotifier.emit(
                'warning', 'Warning',
                f'No raw scan data found in the selected folder.\nExpected: {raw_base}',
            )
            return

        try:
            scans = loadRawScans(raw_base)
            if not scans:
                self.signalNotifier.emit(
                    'warning', 'Warning',
                    f'No raw scans found in {raw_base}.',
                )
                return

            backend = cfg.ocrBackend.value
            if backend == 'CPU only':
                ocr_providers = ['CPUExecutionProvider']
            else:
                ocr_providers = ['DmlExecutionProvider', 'CPUExecutionProvider']

            echoes = reprocess_echo_scans_with_service(
                scans,
                providers=ocr_providers,
                min_rarity=cfg.echoMinRarity.value,
                min_level=cfg.echoMinLevel.value,
                write_debug=cfg.writeDebug.value,
                ocr_cache_path=cfg.get(cfg.ocrCachePath),
                raw_base=raw_base,
            )
            savingScraped(build_standalone_exports({'echoes': echoes}), session_id)
            self.signalNotifier.emit(
                'success', 'Reprocess Complete',
                f'Saved {len(echoes)} echoes for session "{session_id}".',
            )
        except Exception as e:
            logger.error('Reprocess session failed: %s', e, exc_info=True)
            self.signalNotifier.emit('error', 'Reprocess Failed', str(e))


# ---------------------------------------------------------------------------
# Top control panel
# ---------------------------------------------------------------------------

class TControlPanel(QFrame):
    """Echo / weapon minimum rarity & level spinboxes."""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.__initUI()

    def __initUI(self):
        self.echoMinimumLabel = BodyLabel('Echoes minimum:', self)
        self.echoMinRarity = SpinBox(self)
        self.echoMinLevel = SpinBox(self)

        self.weaponsMinimumLabel = BodyLabel('Weapons minimum:', self)
        self.weaponsMinRarity = SpinBox(self)
        self.weaponsMinLevel = SpinBox(self)

        self.panelLayout = QHBoxLayout(self)
        self.__setupLayout()
        self.__connectSignals()

    def __setupLayout(self):
        self.weaponsMinRarity.setRange(1, 5)
        self.echoMinRarity.setRange(1, 5)
        self.weaponsMinLevel.setRange(0, 90)
        self.echoMinLevel.setRange(0, 25)

        echoRarityLayout = QVBoxLayout()
        echoRarityLabel = BodyLabel('Rarity', self)
        echoRarityLayout.addWidget(echoRarityLabel)
        echoRarityLayout.addWidget(self.echoMinRarity)

        echoLevelLayout = QVBoxLayout()
        echoLevelLabel = BodyLabel('Level', self)
        echoLevelLayout.addWidget(echoLevelLabel)
        echoLevelLayout.addWidget(self.echoMinLevel)

        echoControlLayout = QVBoxLayout()
        echoControlLayout.addWidget(self.echoMinimumLabel)
        echoControlLayout.addLayout(echoRarityLayout)
        echoControlLayout.addLayout(echoLevelLayout)

        weaponsRarityLayout = QVBoxLayout()
        weaponsRarityLabel = BodyLabel('Rarity', self)
        weaponsRarityLayout.addWidget(weaponsRarityLabel)
        weaponsRarityLayout.addWidget(self.weaponsMinRarity)

        weaponsLevelLayout = QVBoxLayout()
        weaponsLevelLabel = BodyLabel('Level', self)
        weaponsLevelLayout.addWidget(weaponsLevelLabel)
        weaponsLevelLayout.addWidget(self.weaponsMinLevel)

        weaponsControlLayout = QVBoxLayout()
        weaponsControlLayout.addWidget(self.weaponsMinimumLabel)
        weaponsControlLayout.addLayout(weaponsRarityLayout)
        weaponsControlLayout.addLayout(weaponsLevelLayout)

        self.panelLayout.addSpacing(4)
        self.panelLayout.addLayout(echoControlLayout)
        self.panelLayout.addSpacing(10)
        self.panelLayout.addLayout(weaponsControlLayout)

        self.__setInitialValues()

    def __setInitialValues(self):
        self.echoMinRarity.setValue(cfg.echoMinRarity.value)
        self.echoMinLevel.setValue(cfg.echoMinLevel.value)
        self.weaponsMinRarity.setValue(cfg.weaponsMinRarity.value)
        self.weaponsMinLevel.setValue(cfg.weaponsMinLevel.value)

    def __connectSignals(self):
        self.echoMinLevel.valueChanged.connect(self.onValueChanged)
        self.echoMinRarity.valueChanged.connect(self.onValueChanged)
        self.weaponsMinLevel.valueChanged.connect(self.onValueChanged)
        self.weaponsMinRarity.valueChanged.connect(self.onValueChanged)

    def onValueChanged(self):
        cfg.echoMinLevel.value = self.echoMinLevel.value()
        cfg.echoMinRarity.value = self.echoMinRarity.value()
        cfg.weaponsMinLevel.value = self.weaponsMinLevel.value()
        cfg.weaponsMinRarity.value = self.weaponsMinRarity.value()
        cfg.save()
