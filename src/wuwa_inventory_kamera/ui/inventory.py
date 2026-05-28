"""
wuwa_inventory_kamera.ui.inventory
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Inventory viewer — load and inspect JSON result files.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QWidget, QFileDialog, QGridLayout, QHBoxLayout, QLayout,
    QVBoxLayout, QComboBox,
)

from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import (
    SettingCardGroup, ScrollArea, CardWidget,
    StrongBodyLabel, BodyLabel,
)

from .custom_widgets import MultiplePushSettingCard
from .config import cfg
from ..config.app_config import basePATH
from .inventory_models import (
    InventoryDocument,
    InventoryRow,
    InventorySection,
    load_inventory_file,
    load_inventory_session,
)

logger = logging.getLogger('InventoryInterface')


class ResultCard(CardWidget):
    """Text-first result card with optional thumbnail."""

    def __init__(self, row: InventoryRow, parent=None):
        super().__init__(parent)
        self.row = row

        self.imageLabel = BodyLabel(self)
        self.titleLabel = StrongBodyLabel(row.title, self)
        self.subtitleLabel = BodyLabel(row.subtitle, self)
        self.bodyLabels = [BodyLabel(line, self) for line in row.body_lines]

        self.setupImage(row.image_path)
        self.setupLayout()

    def setupImage(self, image_path: str | None):
        if not image_path:
            self.imageLabel.hide()
            return

        pixmap = QPixmap(str(basePATH / 'assets' / Path(image_path)))
        if pixmap.isNull():
            self.imageLabel.hide()
            return

        scaled_pixmap = pixmap.scaled(
            64,
            64,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.imageLabel.setPixmap(scaled_pixmap)
        self.imageLabel.setFixedSize(64, 64)
        self.imageLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.imageLabel.show()

    def setupLayout(self):
        vBoxLayout = QVBoxLayout(self)
        if not self.imageLabel.isHidden():
            vBoxLayout.addWidget(self.imageLabel, alignment=Qt.AlignmentFlag.AlignCenter)

        self.titleLabel.setWordWrap(True)
        vBoxLayout.addWidget(self.titleLabel)

        if self.row.subtitle:
            self.subtitleLabel.setWordWrap(True)
            vBoxLayout.addWidget(self.subtitleLabel)

        for label in self.bodyLabels:
            label.setWordWrap(True)
            vBoxLayout.addWidget(label)

        vBoxLayout.setSpacing(6)
        vBoxLayout.setContentsMargins(10, 10, 10, 10)
        self.setToolTip(self.row.title)


class InventoryInterface(ScrollArea):
    """Scrollable result grid."""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self._currentSectionIndex = 0
        self._currentDocument = InventoryDocument(kind='empty', title='', message_lines=())
        self._currentSourcePath: Path | None = None
        self._currentSourceKind: str | None = None
        self.setObjectName("inventoryUI")
        self.setStyleSheet("""
            QScrollArea { background: transparent; }
            QScrollArea > QWidget > QWidget { background: transparent; }
            QScrollArea > QScrollBar { background: transparent; }
        """)

        self.scrollWidget = QWidget()
        self.scrollWidget.setStyleSheet("background: transparent;")
        self.mainLayout = QVBoxLayout(self.scrollWidget)

        self.inventoryGroup = SettingCardGroup(self.tr("Inventory"), self.scrollWidget)
        self.inventoryFileCard = MultiplePushSettingCard(
            [
                self.tr('Open file'),
                self.tr('Open session'),
                self.tr('Reload'),
                self.tr('Open folder'),
            ],
            FIF.DOWNLOAD,
            self.tr("Result source"),
            parent=self.inventoryGroup,
        )

        self.contentWidget = QWidget(self)
        self.contentLayout = QVBoxLayout(self.contentWidget)

        self.__initWidget()

    def __initWidget(self):
        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)
        self.__initLayout()
        self.__connectSignalToSlot()

    def __initLayout(self):
        self.inventoryGroup.addSettingCard(self.inventoryFileCard)
        self.mainLayout.setSpacing(28)
        self.mainLayout.setContentsMargins(60, 10, 60, 0)
        self.mainLayout.addWidget(self.inventoryGroup)
        self.mainLayout.addWidget(self.contentWidget)
        self.mainLayout.addStretch(1)
        self.contentLayout.setSpacing(16)
        self.contentLayout.setContentsMargins(0, 0, 0, 0)

        self.inventoryFileCard.buttons[2].setEnabled(False)
        self.inventoryFileCard.buttons[3].setEnabled(False)

        self.__setDocument(
            InventoryDocument(
                kind='empty',
                title='',
                message_lines=('Open a scan result JSON file to inspect it here.',),
            )
        )

    def __connectSignalToSlot(self):
        self.inventoryFileCard.buttonClicked.connect(self.__onInventoryFileCardClicked)

    def __onInventoryFileCardClicked(self, index):
        if index == 0:
            self.__loadInventoryFile()
        elif index == 1:
            self.__loadInventorySession()
        elif index == 2:
            self.__reloadCurrentSource()
        elif index == 3:
            self.__openContainingFolder()

    def __loadInventoryFile(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("Choose file to load"),
            cfg.get(cfg.exportFolder),
            "JSON Files (*.json)",
        )
        if file_path:
            self.__loadSource(Path(file_path), source_kind='file')

    def __loadInventorySession(self):
        folder_path = QFileDialog.getExistingDirectory(
            self,
            self.tr('Choose session folder to load'),
            cfg.get(cfg.exportFolder),
        )
        if folder_path:
            self.__loadSource(Path(folder_path), source_kind='session')

    def __loadSource(self, path: Path, *, source_kind: str):
        self._currentSourcePath = path
        self._currentSourceKind = source_kind
        self.inventoryFileCard.setContent(str(path))
        self.inventoryFileCard.buttons[2].setEnabled(True)
        self.inventoryFileCard.buttons[3].setEnabled(True)

        if source_kind == 'session':
            self.__setDocument(load_inventory_session(path))
        else:
            self.__setDocument(load_inventory_file(path))

    def __reloadCurrentSource(self):
        if self._currentSourcePath is None or self._currentSourceKind is None:
            return

        self.__loadSource(self._currentSourcePath, source_kind=self._currentSourceKind)

    def __openContainingFolder(self):
        if self._currentSourcePath is None:
            return

        target = self._currentSourcePath if self._currentSourceKind == 'session' else self._currentSourcePath.parent
        if target.exists():
            os.startfile(target)

    def __setDocument(self, document: InventoryDocument):
        self._currentDocument = document
        self._currentSectionIndex = 0
        self.__renderCurrentDocument()

    def __renderCurrentDocument(self):
        document = self._currentDocument
        section_count = len(document.sections)
        selected_index = min(self._currentSectionIndex, max(section_count - 1, 0))
        self._currentSectionIndex = selected_index

        self.__clearLayout(self.contentLayout)

        if document.title:
            title = StrongBodyLabel(document.title, self.contentWidget)
            self.contentLayout.addWidget(title)

        for message in document.message_lines:
            label = BodyLabel(message, self.contentWidget)
            label.setWordWrap(True)
            self.contentLayout.addWidget(label)

        if document.sections:
            if document.message_lines:
                self.contentLayout.addSpacing(8)

            if len(document.sections) > 1:
                self.__addSectionSelector(document.sections, selected_index)

            self.__addSection(document.sections[selected_index], show_title=len(document.sections) == 1)
        elif not document.message_lines:
            label = BodyLabel('No supported results were found in this file.', self.contentWidget)
            label.setWordWrap(True)
            self.contentLayout.addWidget(label)

        self.contentLayout.addStretch(1)

    def __addSectionSelector(self, sections: tuple[InventorySection, ...], selected_index: int):
        selectorWidget = QWidget(self.contentWidget)
        selectorLayout = QHBoxLayout(selectorWidget)
        selectorLayout.setContentsMargins(0, 0, 0, 0)
        selectorLayout.setSpacing(12)

        selectorLabel = StrongBodyLabel('Section', selectorWidget)
        selectorLayout.addWidget(selectorLabel)

        selector = QComboBox(selectorWidget)
        for section in sections:
            selector.addItem(f'{section.title} ({len(section.rows)})')
        selector.setCurrentIndex(selected_index)
        selector.currentIndexChanged.connect(self.__onSectionChanged)
        selectorLayout.addWidget(selector, 1)

        self.contentLayout.addWidget(selectorWidget)

    def __onSectionChanged(self, index: int):
        if index < 0 or index == self._currentSectionIndex:
            return

        self._currentSectionIndex = index
        self.__renderCurrentDocument()

    def __addSection(self, section: InventorySection, *, show_title: bool):
        if show_title:
            title = StrongBodyLabel(f'{section.title} ({len(section.rows)})', self.contentWidget)
            self.contentLayout.addWidget(title)

        sectionWidget = QWidget(self.contentWidget)
        sectionGrid = QGridLayout(sectionWidget)
        sectionGrid.setContentsMargins(0, 0, 0, 0)
        sectionGrid.setSpacing(10)

        columns = 3
        for index, row in enumerate(section.rows):
            card = ResultCard(row, sectionWidget)
            sectionGrid.addWidget(card, index // columns, index % columns)

        self.contentLayout.addWidget(sectionWidget)

    def __clearLayout(self, layout: QLayout):
        while layout.count():
            item = layout.takeAt(0)
            if item is None:
                continue

            widget = item.widget()
            child_layout = item.layout()

            if child_layout is not None:
                self.__clearLayout(child_layout)
                child_layout.deleteLater()

            if widget is not None:
                widget.deleteLater()
