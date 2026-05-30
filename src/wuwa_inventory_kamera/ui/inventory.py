"""
wuwa_inventory_kamera.ui.inventory
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Inventory viewer — load and inspect JSON result files.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import logging
import os
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QWidget, QFileDialog, QGridLayout, QHBoxLayout, QLayout,
    QVBoxLayout, QComboBox,
)

from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import (
    SettingCardGroup, ScrollArea, CardWidget,
    StrongBodyLabel, BodyLabel, LineEdit,
)

from .custom_widgets import MultiplePushSettingCard
from .config import cfg
from ..config.app_config import basePATH
from .inventory_models import (
    InventoryDocument,
    InventoryRow,
    InventorySection,
    filter_section_rows,
    load_inventory_file,
    load_inventory_session,
)
from ..updater import assets as _assets

logger = logging.getLogger('InventoryInterface')

_LAZY_DOWNLOAD_FAILURE_BACKOFF_SECONDS = 15.0
_LAZY_DOWNLOAD_MAX_WORKERS = 4


class _LazyGameIconDownloader(QObject):
    downloadFinished = Signal(str, bool)

    def __init__(self) -> None:
        super().__init__()
        self._in_flight: set[str] = set()
        self._failed_at: dict[str, float] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=_LAZY_DOWNLOAD_MAX_WORKERS,
            thread_name_prefix='wuwa-game-icon',
        )

    def request(self, image_path: str) -> None:
        with self._lock:
            if image_path in self._in_flight:
                return
            failed_at = self._failed_at.get(image_path)
            if failed_at is not None:
                elapsed = time.monotonic() - failed_at
                if elapsed < _LAZY_DOWNLOAD_FAILURE_BACKOFF_SECONDS:
                    return
            self._in_flight.add(image_path)

        self._executor.submit(self._download, image_path)

    def _download(self, image_path: str) -> None:
        success = False
        try:
            cached_path = _assets.ensure_game_asset_cached(image_path)
            success = cached_path.is_file()
        except Exception as exc:
            logger.warning('Failed lazy thumbnail download for %s: %s', image_path, exc)
        finally:
            with self._lock:
                self._in_flight.discard(image_path)
                if success:
                    self._failed_at.pop(image_path, None)
                else:
                    self._failed_at[image_path] = time.monotonic()
            self.downloadFinished.emit(image_path, success)


_lazy_game_icon_downloader: _LazyGameIconDownloader | None = None


def _get_game_icon_lazy_downloader() -> _LazyGameIconDownloader:
    global _lazy_game_icon_downloader
    if _lazy_game_icon_downloader is None:
        _lazy_game_icon_downloader = _LazyGameIconDownloader()
    return _lazy_game_icon_downloader


class ResultCard(CardWidget):
    """Text-first result card with optional thumbnail."""

    clicked = Signal()

    def __init__(self, row: InventoryRow, parent=None):
        super().__init__(parent)
        self.row = row
        self._selected = False
        self._image_path = row.image_path
        self._lazyDownloadPending = False

        self.imageLabel = BodyLabel(self)
        self.titleLabel = StrongBodyLabel(row.title, self)
        self.subtitleLabel = BodyLabel(row.subtitle, self)
        self.bodyLabels = [BodyLabel(line, self) for line in row.body_lines]

        _get_game_icon_lazy_downloader().downloadFinished.connect(self._onLazyImageDownloaded)
        self.setupImage(row.image_path)
        self.setupLayout()

    def setupImage(self, image_path: str | None):
        if not image_path:
            self.imageLabel.hide()
            return

        if self._applyImagePixmap(image_path):
            self._lazyDownloadPending = False
            return

        self.imageLabel.hide()
        if not self._lazyDownloadPending:
            self._lazyDownloadPending = True
            _get_game_icon_lazy_downloader().request(image_path)

    def _applyImagePixmap(self, image_path: str) -> bool:
        pixmap = QPixmap(str(basePATH / 'assets' / Path(image_path)))
        if pixmap.isNull():
            return False

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
        return True

    def _onLazyImageDownloaded(self, image_path: str, success: bool) -> None:
        if image_path != self._image_path:
            return

        self._lazyDownloadPending = False
        if not success:
            return

        if self._applyImagePixmap(image_path):
            self.updateGeometry()
            layout = self.layout()
            if layout is not None:
                layout.invalidate()

    def setupLayout(self):
        vBoxLayout = QVBoxLayout(self)
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

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def setSelected(self, selected: bool):
        if selected == self._selected:
            return

        self._selected = selected
        self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        if not self._selected:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor(0, 120, 212, 230))
        pen.setWidth(1)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -2, -2), 8, 8)
        painter.end()


class InventoryInterface(ScrollArea):
    """Scrollable result grid."""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self._currentSectionIndex = 0
        self._currentRowIndex = 0
        self._currentSearchText = ''
        self._searchBox: LineEdit | None = None
        self._resultsLayout: QVBoxLayout | None = None
        self._resultCards: list[ResultCard] = []
        self._detailsCard: CardWidget | None = None
        self._detailsLayout: QVBoxLayout | None = None
        self._visibleRows: tuple[InventoryRow, ...] = ()
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
        self._currentRowIndex = 0
        self._currentSearchText = ''
        self.__prefetchDocumentImages(document)
        self.__renderCurrentDocument()

    def __prefetchDocumentImages(self, document: InventoryDocument):
        downloader = _get_game_icon_lazy_downloader()
        queued_paths: set[str] = set()
        assets_dir = basePATH / 'assets'

        for section in document.sections:
            for row in section.rows:
                image_path = row.image_path
                if not image_path or image_path in queued_paths:
                    continue
                if (assets_dir / Path(image_path)).is_file():
                    continue
                queued_paths.add(image_path)
                downloader.request(image_path)

    def __renderCurrentDocument(self):
        document = self._currentDocument
        section_count = len(document.sections)
        selected_index = min(self._currentSectionIndex, max(section_count - 1, 0))
        self._currentSectionIndex = selected_index

        self._searchBox = None
        self._resultsLayout = None
        self._resultCards = []
        self._detailsCard = None
        self._detailsLayout = None
        self._visibleRows = ()
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

            self.__addSearchBox()

            resultsWidget = QWidget(self.contentWidget)
            resultsLayout = QVBoxLayout(resultsWidget)
            resultsLayout.setContentsMargins(0, 0, 0, 0)
            resultsLayout.setSpacing(16)
            self._resultsLayout = resultsLayout
            self.contentLayout.addWidget(resultsWidget)

            self.__renderCurrentSectionContent()
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
        self._currentRowIndex = 0
        self.__renderCurrentSectionContent()

    def __addSearchBox(self) -> LineEdit:
        searchBox = LineEdit(self.contentWidget)
        searchBox.setPlaceholderText('Search current section')
        searchBox.setText(self._currentSearchText)
        searchBox.textChanged.connect(self.__onSearchChanged)
        self.contentLayout.addWidget(searchBox)
        self._searchBox = searchBox
        return searchBox

    def __onSearchChanged(self, text: str):
        if text == self._currentSearchText:
            return

        self._currentSearchText = text
        self._currentRowIndex = 0
        self.__renderCurrentSectionContent()

    def __renderCurrentSectionContent(self):
        if self._resultsLayout is None:
            return

        document = self._currentDocument
        if not document.sections:
            self._resultCards = []
            self._detailsCard = None
            self._detailsLayout = None
            self._visibleRows = ()
            self.__clearLayout(self._resultsLayout)
            return

        selected_index = min(self._currentSectionIndex, max(len(document.sections) - 1, 0))
        self._currentSectionIndex = selected_index
        filtered_section = filter_section_rows(document.sections[selected_index], self._currentSearchText)
        self._visibleRows = filtered_section.rows
        selected_row_index = min(self._currentRowIndex, max(len(filtered_section.rows) - 1, 0))
        self._currentRowIndex = selected_row_index

        self.__clearLayout(self._resultsLayout)
        self._resultCards = []
        self._detailsCard = None
        self._detailsLayout = None
        self.__addSection(
            self._resultsLayout,
            filtered_section,
            show_title=len(document.sections) == 1,
        )

        if self._visibleRows:
            self.__addDetailsPane(self._resultsLayout)
            self.__applyRowSelection(selected_row_index)

        if self._currentSearchText and not self._visibleRows:
            label = BodyLabel('No rows match the current search.', self.contentWidget)
            label.setWordWrap(True)
            self._resultsLayout.addWidget(label)

    def __addSection(self, layout: QVBoxLayout, section: InventorySection, *, show_title: bool):
        if show_title:
            title = StrongBodyLabel(f'{section.title} ({len(section.rows)})', self.contentWidget)
            layout.addWidget(title)

        sectionWidget = QWidget(self.contentWidget)
        sectionGrid = QGridLayout(sectionWidget)
        sectionGrid.setContentsMargins(0, 0, 0, 0)
        sectionGrid.setSpacing(10)

        columns = 3
        for index, row in enumerate(section.rows):
            card = ResultCard(row, sectionWidget)
            card.clicked.connect(lambda idx=index: self.__onRowSelected(idx))
            self._resultCards.append(card)
            sectionGrid.addWidget(card, index // columns, index % columns)

        layout.addWidget(sectionWidget)

    def __onRowSelected(self, index: int):
        if index < 0 or index == self._currentRowIndex or index >= len(self._visibleRows):
            return

        self._currentRowIndex = index
        self.__applyRowSelection(index)

    def __applyRowSelection(self, selected_row_index: int):
        if not self._visibleRows:
            return

        for index, card in enumerate(self._resultCards):
            card.setSelected(index == selected_row_index)

        self.__updateDetailsPane(self._visibleRows[selected_row_index])

    def __addDetailsPane(self, layout: QVBoxLayout):
        detailsCard = CardWidget(self.contentWidget)
        detailsLayout = QVBoxLayout(detailsCard)
        detailsLayout.setContentsMargins(12, 12, 12, 12)
        detailsLayout.setSpacing(6)

        detailsTitle = StrongBodyLabel('Details', detailsCard)
        detailsLayout.addWidget(detailsTitle)

        self._detailsCard = detailsCard
        self._detailsLayout = detailsLayout
        layout.addWidget(detailsCard)

    def __updateDetailsPane(self, row: InventoryRow):
        if self._detailsCard is None or self._detailsLayout is None:
            return

        while self._detailsLayout.count() > 1:
            item = self._detailsLayout.takeAt(1)
            if item is None:
                continue

            widget = item.widget()
            child_layout = item.layout()

            if child_layout is not None:
                self.__clearLayout(child_layout)
                child_layout.deleteLater()

            if widget is not None:
                widget.deleteLater()

        for line in row.details_lines or (row.subtitle, *row.body_lines):
            if not line:
                continue
            label = BodyLabel(line, self._detailsCard)
            label.setWordWrap(True)
            self._detailsLayout.addWidget(label)

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
