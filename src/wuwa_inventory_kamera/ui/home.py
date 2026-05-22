"""
wuwa_inventory_kamera.ui.home
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Home interface — scanner control panel + manual recognition UI.

The V2 scan path uses :class:`SessionOrchestrator` running on a
``QThread`` instead of the removed V1 scraper pipeline.
"""
from __future__ import annotations

import os
import logging
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtGui import QPixmap, QImage
from PySide6.QtWidgets import (
    QHBoxLayout, QVBoxLayout, QFrame,
    QFileDialog, QWidget,
)
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import (
    PushButton, PrimaryPushButton, CheckBox,
    BodyLabel, LineEdit, SpinBox,
    InfoBar, InfoBarPosition,
    ListWidget, PixmapLabel,
    ProgressBar,
)

from .config import cfg, INVENTORY, FAILED
from ..scraping.utils.common import itemsID, savingScraped

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
    finished(dict)
        The result dict returned by ``SessionOrchestrator.run()``.
    error(str)
        Human-readable error if the scan crashes.
    """

    progress = Signal(str, int, int)
    finished = Signal(dict)
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
    """Main widget with Control Panel on the left and item recognition on the right."""

    updateUISignal = Signal()

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

        self.updateUISignal.connect(self.itemsManualRecognition)
        self.itemsManualRecognition()

    # ------------------------------------------------------------------
    # Manual recognition for failed items
    # ------------------------------------------------------------------

    def itemsManualRecognition(self):
        if self.rightWidget.layout():
            QWidget().setLayout(self.rightWidget.layout())

        container = QVBoxLayout()
        if FAILED:
            container.setContentsMargins(0, 0, 0, 0)
            container.addWidget(BodyLabel('Recognition failed, manual update:'))

            mainLayout = QHBoxLayout()

            image_label = PixmapLabel()
            try:
                image = QImage(FAILED[0]['image'])
            except Exception:
                image = QImage('')
            pixmap = QPixmap.fromImage(image)
            image_label.setPixmap(pixmap)
            image_label.setScaledContents(True)
            image_label.setFixedSize(279, 407)
            mainLayout.addWidget(image_label)

            middle_layout = QVBoxLayout()
            middle_layout.addStretch(1)

            owned_layout = QVBoxLayout()
            owned_label = BodyLabel("Owned")
            self.owned_spinbox = SpinBox()
            self.owned_spinbox.setRange(1, 9999)
            self.owned_spinbox.setValue(FAILED[0]['owned'])
            owned_layout.addWidget(owned_label)
            owned_layout.addWidget(self.owned_spinbox)
            middle_layout.addLayout(owned_layout)

            skip_button = PushButton("Skip")
            change_button = PushButton("Update")
            skip_button.clicked.connect(self.onSkipButtonClicked)
            change_button.clicked.connect(self.onChangeButtonClicked)
            middle_layout.addWidget(skip_button)
            middle_layout.addWidget(change_button)
            middle_layout.addStretch(1)
            mainLayout.addLayout(middle_layout)

            right_layout = QVBoxLayout()
            self.search_bar = LineEdit()
            self.search_bar.setPlaceholderText("Search...")
            self.search_bar.textChanged.connect(self.filter_list)

            self.list_widget = ListWidget()
            self.list_widget.addItems(
                [itemsID[item]['name'] for item in sorted(itemsID)]
            )

            right_layout.addWidget(self.search_bar)
            right_layout.addWidget(self.list_widget)

            mainLayout.addLayout(right_layout)
            mainLayout.setStretch(0, 1)
            mainLayout.setStretch(1, 1)
            mainLayout.setStretch(2, 3)

            container.addLayout(mainLayout)
        else:
            container.addWidget(BodyLabel(''))
        container.setStretch(0, 1)

        self.rightWidget.setLayout(container)
        self.rightWidget.setVisible(True)
        self.rightWidget.update()

    def onSkipButtonClicked(self):
        if FAILED:
            Path(FAILED[0]['image']).unlink(missing_ok=True)
            FAILED.pop(0)
            self.updateUISignal.emit()

    def onChangeButtonClicked(self):
        selected_item = self.list_widget.currentItem()
        if selected_item:
            item_id = itemsID.get(selected_item.text().lower().replace(' ', ''))['id']
            INVENTORY['items'][item_id] = self.owned_spinbox.value()
            savingScraped(START_DATE=INVENTORY['date'])

            if FAILED:
                FAILED.pop(0)

            self.updateUISignal.emit()
        else:
            self.showNotification(
                'warning', 'Warning',
                'Select the item name from the list on the right side.',
            )

    def filter_list(self, text):
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            item.setHidden(text.lower() not in item.text().lower())

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

        if _type == 'failed':
            self.updateUISignal.emit()


# ---------------------------------------------------------------------------
# Left control panel
# ---------------------------------------------------------------------------

class LControlPanel(QFrame):
    """Scanner checkboxes + Start / Export / Reprocess buttons."""

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

        self.reprocessSessionBtn = PushButton('Reprocess Session', icon=FIF.SYNC, parent=self)
        self.reprocessSessionBtn.clicked.connect(self.runReprocessSession)

        self.startScanning = PrimaryPushButton(FIF.PLAY, 'Start Scanning', self)
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
        self.panelLayout.addWidget(self.reprocessSessionBtn)
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
            windowed=cfg.windowed.value,
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

        # Save results
        scan_data: dict[str, tuple] = {}
        session_id = result.get('date', '')

        for key in ('echoes', 'weapons', 'devItems', 'resources'):
            data = result.get(key)
            if isinstance(data, list) and data:
                filename = f'{key}_wuwainventorykamera.json'
                scan_data[filename] = (data, list)

        characters = result.get('characters')
        if isinstance(characters, dict) and characters and 'error' not in characters:
            scan_data['characters_wuwainventorykamera.json'] = (characters, dict)

        if scan_data:
            savingScraped(scan_data, START_DATE=session_id)

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
            savingScraped(
                {'echoes_wuwainventorykamera.json': (echoes, list)},
                session_id,
            )
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
