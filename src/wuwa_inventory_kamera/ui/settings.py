"""
wuwa_inventory_kamera.ui.settings
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Settings interface — theme, export folder, in-game settings, about.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import QWidget, QFileDialog

from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import (
    SettingCardGroup, SwitchSettingCard, PushSettingCard,
    HyperlinkCard, PrimaryPushSettingCard, ScrollArea,
    ExpandLayout, InfoBar, ComboBoxSettingCard, BodyLabel,
    OptionsSettingCard, Theme, setTheme,
)

from .custom_widgets import FieldSettingCard, SpinBoxSettingCard
from .config import (
    cfg, alphabethList, maxLength,
    HELP_URL, FEEDBACK_URL, LANGUAGES,
)

logger = logging.getLogger('SettingInterface')


class SettingInterface(ScrollArea):
    """Settings interface for application configuration."""

    checkUpdateSig = Signal()
    exportFolderChanged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("settingsUI")
        self.setStyleSheet("""
            QScrollArea { background: transparent; }
            QScrollArea > QWidget > QWidget { background: transparent; }
            QScrollArea > QScrollBar { background: transparent; }
        """)

        self.scrollWidget = QWidget()
        self.scrollWidget.setStyleSheet("background: transparent;")
        self.expandLayout = ExpandLayout(self.scrollWidget)

        self.__initializeWidgets()
        self.__initializeLayout()
        self.__connectSignals()

    def __initializeWidgets(self):
        self.settingLabel = BodyLabel(self.tr("Settings"), self)
        self.settingLabel.setFont(QFont('Microsoft YaHei Light', 30, QFont.Weight.Bold))

        # Personalization
        self.personalizationGroup = SettingCardGroup(self.tr("Personalization"), self.scrollWidget)
        self.themeCard = OptionsSettingCard(
            cfg.themeMode,
            FIF.BRUSH,
            self.tr('Application theme'),
            self.tr("Change the appearance of your application"),
            texts=[self.tr('Light'), self.tr('Dark'), self.tr('Use system setting')],
            parent=self.personalizationGroup,
        )
        self.exportFolderCard = PushSettingCard(
            self.tr('Choose folder'),
            FIF.DOWNLOAD,
            self.tr("Export directory"),
            cfg.get(cfg.exportFolder),
            self.personalizationGroup,
        )

        # In-Game settings
        self.inGameGroup = SettingCardGroup(self.tr("In-Game settings"), self.scrollWidget)
        self.roverName = FieldSettingCard(
            cfg.roverName,
            FIF.FONT_SIZE,
            self.tr('Rover Name'),
            self.tr('Insert your rover name'),
            max_length=maxLength,
            parent=self.inGameGroup,
        )
        self.languageGame = ComboBoxSettingCard(
            cfg.gameLanguage,
            FIF.LANGUAGE,
            self.tr('Language'),
            self.tr('Set the language you use in game'),
            list(LANGUAGES),
            self.inGameGroup,
        )
        self.inventoryKey = ComboBoxSettingCard(
            cfg.inventoryKeybind,
            FIF.FONT_SIZE,
            self.tr('Inventory Keybind'),
            self.tr('Select the keybind you use in game to open the inventory'),
            alphabethList(),
            self.inGameGroup,
        )
        self.resonatorKey = ComboBoxSettingCard(
            cfg.resonatorKeybind,
            FIF.FONT_SIZE,
            self.tr('Characters Keybind'),
            self.tr('Select the keybind you use in game to open the resonators'),
            alphabethList(),
            self.inGameGroup,
        )
        self.windowedCard = SwitchSettingCard(
            FIF.MINIMIZE,
            self.tr('Windowed mode'),
            self.tr('Enable if the game runs in a window instead of fullscreen. Screenshots and input coordinates will be relative to the game window.'),
            configItem=cfg.windowed,
            parent=self.inGameGroup,
        )

        # OCR
        self.ocrGroup = SettingCardGroup(self.tr('OCR'), self.scrollWidget)
        self.ocrBackendCard = ComboBoxSettingCard(
            cfg.ocrBackend,
            FIF.SPEED_HIGH,
            self.tr('OCR Backend'),
            self.tr('DML+CPU uses the GPU (DirectML) for faster OCR; CPU only avoids GPU memory pressure when the game is running'),
            ['DML+CPU', 'CPU only'],
            self.ocrGroup,
        )
        self.ocrBatchSizeCard = SpinBoxSettingCard(
            cfg.ocrBatchSize,
            FIF.TILES,
            self.tr('OCR Batch Size'),
            self.tr('Number of images processed per GPU forward pass. Lower values reduce GPU memory usage (default: 8)'),
            min_value=1,
            max_value=64,
            parent=self.ocrGroup,
        )

        # Advanced
        self.advancedGroup = SettingCardGroup(self.tr('Advanced'), self.scrollWidget)
        self.logLevelCard = ComboBoxSettingCard(
            cfg.logLevel,
            FIF.DEVELOPER_TOOLS,
            self.tr('Console log level'),
            self.tr('Verbosity of the console output (takes effect on next launch). Log files always capture DEBUG.'),
            ['DEBUG', 'INFO', 'WARNING', 'ERROR'],
            self.advancedGroup,
        )
        self.saveRawCard = SwitchSettingCard(
            FIF.SAVE,
            self.tr('Save raw screenshots'),
            self.tr('Save a copy of every raw screenshot to the export folder for offline reprocessing (increases disk usage).'),
            configItem=cfg.saveRaw,
            parent=self.advancedGroup,
        )
        self.dataSourceCard = ComboBoxSettingCard(
            cfg.dataSource,
            FIF.UPDATE,
            self.tr('Game data source'),
            self.tr('Choose which upstream repository provides ItemInfo, WeaponConf, and MultiText updates.'),
            ['Dimbreath', 'Arikatsu'],
            self.advancedGroup,
        )

        # Software update
        self.updateSoftwareGroup = SettingCardGroup(self.tr("Software update"), self.scrollWidget)
        self.updateOnStartUpCard = SwitchSettingCard(
            FIF.UPDATE,
            self.tr('Check for updates when the application starts'),
            self.tr('The new version will be more stable and have more features'),
            configItem=cfg.checkUpdateAtStartUp,
            parent=self.updateSoftwareGroup,
        )

        # About
        self.aboutGroup = SettingCardGroup(self.tr('About'), self.scrollWidget)
        self.helpCard = HyperlinkCard(
            HELP_URL,
            self.tr('Open help page'),
            FIF.HELP,
            self.tr('Help'),
            self.tr('Ask something'),
            self.aboutGroup,
        )
        self.feedbackCard = PrimaryPushSettingCard(
            self.tr('Provide feedback'),
            FIF.FEEDBACK,
            self.tr('Provide feedback'),
            self.tr('Report bug and issues'),
            self.aboutGroup,
        )

    def __initializeLayout(self):
        self.resize(1000, 800)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setViewportMargins(0, 120, 0, 20)
        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)

        self.settingLabel.move(60, 63)

        self.personalizationGroup.addSettingCard(self.themeCard)
        self.personalizationGroup.addSettingCard(self.exportFolderCard)
        self.inGameGroup.addSettingCard(self.roverName)
        self.inGameGroup.addSettingCard(self.languageGame)
        self.inGameGroup.addSettingCard(self.inventoryKey)
        self.inGameGroup.addSettingCard(self.resonatorKey)
        self.inGameGroup.addSettingCard(self.windowedCard)
        self.ocrGroup.addSettingCard(self.ocrBackendCard)
        self.ocrGroup.addSettingCard(self.ocrBatchSizeCard)
        self.advancedGroup.addSettingCard(self.logLevelCard)
        self.advancedGroup.addSettingCard(self.saveRawCard)
        self.advancedGroup.addSettingCard(self.dataSourceCard)
        self.updateSoftwareGroup.addSettingCard(self.updateOnStartUpCard)
        self.aboutGroup.addSettingCard(self.helpCard)
        self.aboutGroup.addSettingCard(self.feedbackCard)

        self.expandLayout.setSpacing(28)
        self.expandLayout.setContentsMargins(60, 10, 60, 0)
        self.expandLayout.addWidget(self.personalizationGroup)
        self.expandLayout.addWidget(self.inGameGroup)
        self.expandLayout.addWidget(self.ocrGroup)
        self.expandLayout.addWidget(self.advancedGroup)
        self.expandLayout.addWidget(self.updateSoftwareGroup)
        self.expandLayout.addWidget(self.aboutGroup)

    def __showRestartTooltip(self):
        InfoBar.warning(
            '',
            self.tr('Configuration takes effect after restart'),
            duration=2500,
            parent=self.window(),
        )

    def __onExportFolderCardClicked(self):
        folder = QFileDialog.getExistingDirectory(self, self.tr("Choose folder"), "./")
        if folder and cfg.get(cfg.exportFolder) != folder:
            cfg.set(cfg.exportFolder, folder)
            self.exportFolderCard.setContent(folder)

    def __onThemeChanged(self, theme: Theme):
        setTheme(theme)

    def __connectSignals(self):
        cfg.gameLanguage.valueChanged.connect(self.__showRestartTooltip)
        cfg.dataSource.valueChanged.connect(self.__showRestartTooltip)
        cfg.themeChanged.connect(self.__onThemeChanged)
        self.exportFolderCard.clicked.connect(self.__onExportFolderCardClicked)
        self.feedbackCard.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(FEEDBACK_URL))
        )
