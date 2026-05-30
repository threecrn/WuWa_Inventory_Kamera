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

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QWidget, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLayout,
    QScrollArea, QSizePolicy, QVBoxLayout, QComboBox,
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
    CharacterDisplayData,
    EchoDisplayData,
    InventoryDocument,
    InventoryRow,
    InventorySection,
    WeaponDisplayData,
    filter_section_rows,
    load_inventory_file,
    load_inventory_session,
)
from ..updater import assets as _assets

logger = logging.getLogger('InventoryInterface')

_LAZY_DOWNLOAD_FAILURE_BACKOFF_SECONDS = 15.0
_LAZY_DOWNLOAD_MAX_WORKERS = 4
_DETAILS_PANE_WIDTH = 320
_GRID_TILE_WIDTH = 144
_GRID_TILE_RARITY_COLORS: dict[int, QColor] = {
    5: QColor(255, 250, 176),
    4: QColor(232, 161, 255),
    3: QColor(153, 153, 255),
    2: QColor(153, 255, 153),
    1: QColor(218, 222, 225),
}


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


def _coerce_int_value(value: object) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _grid_tile_rarity_color(rarity: object) -> QColor:
    rarity_value = _coerce_int_value(rarity)
    if rarity_value is None:
        return _GRID_TILE_RARITY_COLORS[1]
    return _GRID_TILE_RARITY_COLORS.get(rarity_value, _GRID_TILE_RARITY_COLORS[1])


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


class TileCard(CardWidget):
    """Compact icon + name + count tile for resources and inventory items."""

    TILE_WIDTH = _GRID_TILE_WIDTH
    TILE_HEIGHT = 140
    ICON_SIZE = 64

    clicked = Signal()

    def __init__(self, row: InventoryRow, parent=None):
        super().__init__(parent)
        self.row = row
        self._selected = False
        self._image_path = row.image_path
        self._lazyDownloadPending = False

        self.imageLabel = BodyLabel(self)
        self.nameLabel = BodyLabel(self)
        count_text = row.body_lines[0] if row.body_lines else ''
        self.countLabel = BodyLabel(count_text, self)

        _get_game_icon_lazy_downloader().downloadFinished.connect(self._onLazyImageDownloaded)
        self.setupImage(row.image_path)
        self.setupLayout()
        self.setFixedSize(self.TILE_WIDTH, self.TILE_HEIGHT)
        self.setToolTip(row.title)

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
            self.ICON_SIZE,
            self.ICON_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.imageLabel.setPixmap(scaled_pixmap)
        self.imageLabel.setFixedSize(self.ICON_SIZE, self.ICON_SIZE)
        self.imageLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.imageLabel.show()
        return True

    def _onLazyImageDownloaded(self, image_path: str, success: bool) -> None:
        if image_path != self._image_path:
            return

        self._lazyDownloadPending = False
        if not success:
            return

        self._applyImagePixmap(image_path)

    def setupLayout(self):
        vBoxLayout = QVBoxLayout(self)
        vBoxLayout.setSpacing(4)
        vBoxLayout.setContentsMargins(8, 8, 8, 8)

        self.imageLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vBoxLayout.addWidget(self.imageLabel, alignment=Qt.AlignmentFlag.AlignHCenter)

        available_width = self.TILE_WIDTH - 20
        fm = self.nameLabel.fontMetrics()
        elided = fm.elidedText(self.row.title, Qt.TextElideMode.ElideRight, available_width)
        self.nameLabel.setText(elided)
        self.nameLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vBoxLayout.addWidget(self.nameLabel)

        if self.row.body_lines:
            self.countLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.countLabel.setStyleSheet(
                'QLabel { background: rgba(0,0,0,50); border-radius: 4px; padding: 2px 4px; }'
            )
            vBoxLayout.addWidget(self.countLabel)

        vBoxLayout.addStretch()

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


class EchoTileCard(CardWidget):
    """Compact fixed-size echo tile for the inventory viewer."""

    TILE_WIDTH = _GRID_TILE_WIDTH
    TILE_HEIGHT = 210
    ICON_SIZE = 64
    SONATA_ICON_SIZE = 16

    clicked = Signal()

    def __init__(self, row: InventoryRow, parent=None):
        super().__init__(parent)
        self.row = row
        self._selected = False
        self._image_path = row.image_path
        self._lazyDownloadPending = False
        self._echoDisplay = row.echo_display
        self._hasSonataIcon = False

        self.imageLabel = BodyLabel(self)
        self.nameLabel = StrongBodyLabel(self)
        self.summaryRow = QWidget(self)
        self.sonataIconLabel = BodyLabel(self.summaryRow)
        self.summaryLabel = BodyLabel(self.summaryRow)
        self.rarityLine = QWidget(self)
        self.mainStatLabel = BodyLabel(self)
        self.equippedLabel = BodyLabel(self)

        _get_game_icon_lazy_downloader().downloadFinished.connect(self._onLazyImageDownloaded)
        self.setupImage(row.image_path)
        self._hasSonataIcon = self._applySonataIconPixmap(
            self._echoDisplay.sonata_icon_path if self._echoDisplay is not None else None
        )
        self.setupLayout()
        self.setFixedSize(self.TILE_WIDTH, self.TILE_HEIGHT)
        self.setToolTip(row.title)

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
            self.ICON_SIZE,
            self.ICON_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.imageLabel.setPixmap(scaled_pixmap)
        self.imageLabel.setFixedSize(self.ICON_SIZE, self.ICON_SIZE)
        self.imageLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.imageLabel.show()
        return True

    def _applySonataIconPixmap(self, image_path: str | None) -> bool:
        if not image_path:
            self.sonataIconLabel.hide()
            return False

        pixmap = QPixmap(str(basePATH / 'assets' / Path(image_path)))
        if pixmap.isNull():
            self.sonataIconLabel.hide()
            return False

        scaled_pixmap = pixmap.scaled(
            self.SONATA_ICON_SIZE,
            self.SONATA_ICON_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.sonataIconLabel.setPixmap(scaled_pixmap)
        self.sonataIconLabel.setFixedSize(self.SONATA_ICON_SIZE, self.SONATA_ICON_SIZE)
        self.sonataIconLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sonataIconLabel.setToolTip(
            self._echoDisplay.sonata_name if self._echoDisplay is not None else ''
        )
        self.sonataIconLabel.show()
        return True

    def _onLazyImageDownloaded(self, image_path: str, success: bool) -> None:
        if image_path != self._image_path:
            return

        self._lazyDownloadPending = False
        if not success:
            return

        self._applyImagePixmap(image_path)

    @staticmethod
    def _elide_text(label: BodyLabel | StrongBodyLabel, text: str, width: int) -> str:
        return label.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, width)

    @staticmethod
    def _summary_text(echo_display: EchoDisplayData | None) -> str:
        if echo_display is None:
            return ''

        level_text = '' if echo_display.level is None else f'+{echo_display.level}'
        if echo_display.cost is None:
            return level_text
        cost_text = f'({echo_display.cost})'
        if level_text:
            return f'{level_text} {cost_text}'
        return cost_text

    def setupLayout(self):
        vBoxLayout = QVBoxLayout(self)
        vBoxLayout.setSpacing(4)
        vBoxLayout.setContentsMargins(10, 10, 10, 10)

        available_width = self.TILE_WIDTH - 24

        self.imageLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vBoxLayout.addWidget(self.imageLabel, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.nameLabel.setText(self._elide_text(self.nameLabel, self.row.title, available_width))
        self.nameLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vBoxLayout.addWidget(self.nameLabel)

        summaryLayout = QHBoxLayout(self.summaryRow)
        summaryLayout.setContentsMargins(0, 0, 0, 0)
        summaryLayout.setSpacing(4)
        summaryLayout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        if self._hasSonataIcon:
            summaryLayout.addWidget(self.sonataIconLabel, alignment=Qt.AlignmentFlag.AlignCenter)

        summary_text = self._summary_text(self._echoDisplay)
        if summary_text:
            self.summaryLabel.setText(summary_text)
            self.summaryLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
            summaryLayout.addWidget(self.summaryLabel, alignment=Qt.AlignmentFlag.AlignCenter)
        else:
            self.summaryLabel.hide()

        if self._hasSonataIcon or summary_text:
            vBoxLayout.addWidget(self.summaryRow)
        else:
            self.summaryRow.hide()

        rarity_color = _grid_tile_rarity_color(self._echoDisplay.rarity if self._echoDisplay else None)
        self.rarityLine.setFixedHeight(4)
        self.rarityLine.setStyleSheet(
            f'background-color: {rarity_color.name()}; border-radius: 2px;'
        )
        vBoxLayout.addWidget(self.rarityLine)

        main_stat_text = self._echoDisplay.main_stat if self._echoDisplay is not None else ''
        if main_stat_text:
            self.mainStatLabel.setText(main_stat_text)
            self.mainStatLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.mainStatLabel.setWordWrap(True)
            vBoxLayout.addWidget(self.mainStatLabel)
        else:
            self.mainStatLabel.hide()

        equipped_name = self._echoDisplay.equipped if self._echoDisplay is not None else ''
        equipped_text = f'Equipped: {equipped_name}' if equipped_name else ''
        self.equippedLabel.setText(equipped_text or ' ')
        self.equippedLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if equipped_text:
            self.equippedLabel.setWordWrap(True)
            self.equippedLabel.setFixedHeight((self.equippedLabel.fontMetrics().height() * 2) + 4)
        else:
            self.equippedLabel.setWordWrap(False)
            self.equippedLabel.setFixedHeight(self.equippedLabel.fontMetrics().height() + 4)
        vBoxLayout.addWidget(self.equippedLabel)

        vBoxLayout.addStretch()

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


class WeaponTileCard(CardWidget):
    """Compact fixed-size weapon tile mirroring the in-app weapon grid."""

    TILE_WIDTH = _GRID_TILE_WIDTH
    TILE_HEIGHT = 180
    ICON_SIZE = 64

    clicked = Signal()

    def __init__(self, row: InventoryRow, parent=None):
        super().__init__(parent)
        self.row = row
        self._selected = False
        self._image_path = row.image_path
        self._lazyDownloadPending = False
        self._weaponDisplay = row.weapon_display

        self.imageLabel = BodyLabel(self)
        self.nameLabel = StrongBodyLabel(self)
        self.summaryLabel = BodyLabel(self)
        self.rarityLine = QWidget(self)
        self.equippedLabel = BodyLabel(self)

        _get_game_icon_lazy_downloader().downloadFinished.connect(self._onLazyImageDownloaded)
        self.setupImage(row.image_path)
        self.setupLayout()
        self.setFixedSize(self.TILE_WIDTH, self.TILE_HEIGHT)
        self.setToolTip(row.title)

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
            self.ICON_SIZE,
            self.ICON_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.imageLabel.setPixmap(scaled_pixmap)
        self.imageLabel.setFixedSize(self.ICON_SIZE, self.ICON_SIZE)
        self.imageLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.imageLabel.show()
        return True

    def _onLazyImageDownloaded(self, image_path: str, success: bool) -> None:
        if image_path != self._image_path:
            return

        self._lazyDownloadPending = False
        if not success:
            return

        self._applyImagePixmap(image_path)

    @staticmethod
    def _elide_text(label: BodyLabel | StrongBodyLabel, text: str, width: int) -> str:
        return label.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, width)

    @staticmethod
    def _summary_text(weapon_display: WeaponDisplayData | None) -> str:
        if weapon_display is None:
            return ''

        level_text = '' if weapon_display.level is None else str(weapon_display.level)
        max_level_text = '' if weapon_display.max_level is None else str(weapon_display.max_level)
        rank_text = '' if weapon_display.rank is None else str(weapon_display.rank)

        if level_text and max_level_text:
            summary = f'{level_text}/{max_level_text}'
        elif level_text:
            summary = level_text
        elif max_level_text:
            summary = f'?/{max_level_text}'
        else:
            summary = ''

        if rank_text:
            return f'{summary} ({rank_text})' if summary else f'({rank_text})'
        return summary

    def setupLayout(self):
        vBoxLayout = QVBoxLayout(self)
        vBoxLayout.setSpacing(4)
        vBoxLayout.setContentsMargins(10, 10, 10, 10)

        available_width = self.TILE_WIDTH - 24

        self.imageLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vBoxLayout.addWidget(self.imageLabel, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.nameLabel.setText(self._elide_text(self.nameLabel, self.row.title, available_width))
        self.nameLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vBoxLayout.addWidget(self.nameLabel)

        summary_text = self._summary_text(self._weaponDisplay)
        if summary_text:
            self.summaryLabel.setText(summary_text)
            self.summaryLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
            vBoxLayout.addWidget(self.summaryLabel)
        else:
            self.summaryLabel.hide()

        rarity_color = _grid_tile_rarity_color(self._weaponDisplay.rarity if self._weaponDisplay else None)
        self.rarityLine.setFixedHeight(4)
        self.rarityLine.setStyleSheet(
            f'background-color: {rarity_color.name()}; border-radius: 2px;'
        )
        vBoxLayout.addWidget(self.rarityLine)

        equipped_name = self._weaponDisplay.equipped if self._weaponDisplay is not None else ''
        equipped_text = f'Equipped: {equipped_name}' if equipped_name else ''
        if equipped_text:
            self.equippedLabel.setText(equipped_text)
            self.equippedLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.equippedLabel.setWordWrap(True)
            vBoxLayout.addWidget(self.equippedLabel)
        else:
            self.equippedLabel.hide()

        vBoxLayout.addStretch()

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


class CharacterTileCard(CardWidget):
    """Compact fixed-size character tile for the inventory viewer."""

    TILE_WIDTH = _GRID_TILE_WIDTH
    TILE_HEIGHT = 160
    ICON_SIZE = 64

    clicked = Signal()

    def __init__(self, row: InventoryRow, parent=None):
        super().__init__(parent)
        self.row = row
        self._selected = False
        self._image_path = row.image_path
        self._lazyDownloadPending = False
        self._characterDisplay = row.character_display

        self.imageLabel = BodyLabel(self)
        self.nameLabel = StrongBodyLabel(self)
        self.summaryLabel = BodyLabel(self)
        self.rarityLine = QWidget(self)

        _get_game_icon_lazy_downloader().downloadFinished.connect(self._onLazyImageDownloaded)
        self.setupImage(row.image_path)
        self.setupLayout()
        self.setFixedSize(self.TILE_WIDTH, self.TILE_HEIGHT)
        self.setToolTip(row.title)

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
            self.ICON_SIZE,
            self.ICON_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.imageLabel.setPixmap(scaled_pixmap)
        self.imageLabel.setFixedSize(self.ICON_SIZE, self.ICON_SIZE)
        self.imageLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.imageLabel.show()
        return True

    def _onLazyImageDownloaded(self, image_path: str, success: bool) -> None:
        if image_path != self._image_path:
            return

        self._lazyDownloadPending = False
        if not success:
            return

        self._applyImagePixmap(image_path)

    @staticmethod
    def _elide_text(label: BodyLabel | StrongBodyLabel, text: str, width: int) -> str:
        return label.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, width)

    @staticmethod
    def _summary_text(character_display: CharacterDisplayData | None) -> str:
        if character_display is None:
            return ''

        level_text = '' if character_display.level is None else str(character_display.level)
        max_level_text = '' if character_display.max_level is None else str(character_display.max_level)
        chain_text = '' if character_display.chain is None else str(character_display.chain)

        if level_text and max_level_text:
            summary = f'{level_text}/{max_level_text}'
        elif level_text:
            summary = level_text
        elif max_level_text:
            summary = f'?/{max_level_text}'
        else:
            summary = ''

        if chain_text:
            return f'{summary} ({chain_text})' if summary else f'({chain_text})'
        return summary

    def setupLayout(self):
        vBoxLayout = QVBoxLayout(self)
        vBoxLayout.setSpacing(4)
        vBoxLayout.setContentsMargins(10, 10, 10, 10)

        available_width = self.TILE_WIDTH - 24

        self.imageLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vBoxLayout.addWidget(self.imageLabel, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.nameLabel.setText(self._elide_text(self.nameLabel, self.row.title, available_width))
        self.nameLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vBoxLayout.addWidget(self.nameLabel)

        summary_text = self._summary_text(self._characterDisplay)
        if summary_text:
            self.summaryLabel.setText(summary_text)
            self.summaryLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
            vBoxLayout.addWidget(self.summaryLabel)
        else:
            self.summaryLabel.hide()

        rarity_color = _grid_tile_rarity_color(self._characterDisplay.rarity if self._characterDisplay else None)
        self.rarityLine.setFixedHeight(4)
        self.rarityLine.setStyleSheet(
            f'background-color: {rarity_color.name()}; border-radius: 2px;'
        )
        vBoxLayout.addWidget(self.rarityLine)
        vBoxLayout.addStretch()

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
        self._sectionScrollArea: QScrollArea | None = None
        self._resultCards: list[ResultCard | TileCard | EchoTileCard | WeaponTileCard | CharacterTileCard] = []
        self._detailsCard: CardWidget | None = None
        self._detailsLayout: QVBoxLayout | None = None
        self._detailsPaneHeightSyncPending = False
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
        self.contentWidget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.contentLayout = QVBoxLayout(self.contentWidget)

        self.__initWidget()

    def __initWidget(self):
        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.__initLayout()
        self.__connectSignalToSlot()

    def __initLayout(self):
        self.inventoryGroup.addSettingCard(self.inventoryFileCard)
        self.mainLayout.setSpacing(28)
        self.mainLayout.setContentsMargins(60, 10, 60, 0)
        self.mainLayout.addWidget(self.inventoryGroup)
        self.mainLayout.addWidget(self.contentWidget, 1)
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
        self._sectionScrollArea = None
        self._resultCards = []
        self._detailsCard = None
        self._detailsLayout = None
        self._visibleRows = ()
        self.__clearLayout(self.contentLayout)

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
            resultsWidget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            resultsLayout = QVBoxLayout(resultsWidget)
            resultsLayout.setContentsMargins(0, 0, 0, 0)
            resultsLayout.setSpacing(16)
            self._resultsLayout = resultsLayout
            self.contentLayout.addWidget(resultsWidget, 1)

            self.__renderCurrentSectionContent()
        elif not document.message_lines:
            label = BodyLabel('No supported results were found in this file.', self.contentWidget)
            label.setWordWrap(True)
            self.contentLayout.addWidget(label)

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
        self._sectionScrollArea = None
        self._detailsCard = None
        self._detailsLayout = None

        if self._visibleRows:
            contentRow = QWidget(self.contentWidget)
            contentRow.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            contentRowLayout = QHBoxLayout(contentRow)
            contentRowLayout.setContentsMargins(0, 0, 0, 0)
            contentRowLayout.setSpacing(16)

            sectionScrollArea = QScrollArea(contentRow)
            sectionScrollArea.setWidgetResizable(True)
            sectionScrollArea.setFrameShape(QFrame.Shape.NoFrame)
            sectionScrollArea.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            sectionScrollArea.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            sectionScrollArea.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            sectionScrollArea.setStyleSheet('background: transparent; border: none;')

            sectionColumn = QWidget(sectionScrollArea)
            sectionColumn.setStyleSheet('background: transparent;')
            sectionColumnLayout = QVBoxLayout(sectionColumn)
            sectionColumnLayout.setContentsMargins(0, 0, 0, 0)
            sectionColumnLayout.setSpacing(12)
            self.__addSection(
                sectionColumnLayout,
                filtered_section,
                show_title=len(document.sections) == 1,
            )
            sectionColumnLayout.addStretch(1)
            sectionScrollArea.setWidget(sectionColumn)
            contentRowLayout.addWidget(sectionScrollArea, 1)
            self._sectionScrollArea = sectionScrollArea

            self.__addDetailsPane(contentRowLayout)
            self._resultsLayout.addWidget(contentRow, 1)
            self.__applyRowSelection(selected_row_index)
        else:
            self.__addSection(
                self._resultsLayout,
                filtered_section,
                show_title=len(document.sections) == 1,
            )

        if self._currentSearchText and not self._visibleRows:
            label = BodyLabel('No rows match the current search.', self.contentWidget)
            label.setWordWrap(True)
            self._resultsLayout.addWidget(label)

    def __addSection(self, layout: QVBoxLayout, section: InventorySection, *, show_title: bool):
        if show_title:
            title = StrongBodyLabel(f'{section.title} ({len(section.rows)})', self.contentWidget)
            layout.addWidget(title)

        display_kind = section.rows[0].display_kind if section.rows else 'card'
        is_grid_tile = display_kind in {'tile', 'echo_tile', 'weapon_tile', 'character_tile'}
        columns = 6 if is_grid_tile else 3

        sectionWidget = QWidget(self.contentWidget)
        sectionGrid = QGridLayout(sectionWidget)
        sectionGrid.setContentsMargins(0, 0, 0, 0)
        sectionGrid.setSpacing(8 if is_grid_tile else 10)
        if is_grid_tile:
            for column in range(columns):
                sectionGrid.setColumnMinimumWidth(column, _GRID_TILE_WIDTH)
            sectionGrid.setColumnStretch(columns, 1)

        for index, row in enumerate(section.rows):
            if display_kind == 'echo_tile':
                card: ResultCard | TileCard | EchoTileCard | WeaponTileCard | CharacterTileCard = EchoTileCard(row, sectionWidget)
            elif display_kind == 'weapon_tile':
                card = WeaponTileCard(row, sectionWidget)
            elif display_kind == 'character_tile':
                card = CharacterTileCard(row, sectionWidget)
            elif display_kind == 'tile':
                card = TileCard(row, sectionWidget)
            else:
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

    def __addDetailsPane(self, layout: QHBoxLayout):
        detailsCard = CardWidget(self.contentWidget)
        detailsCard.setFixedWidth(_DETAILS_PANE_WIDTH)
        detailsLayout = QVBoxLayout(detailsCard)
        detailsLayout.setContentsMargins(12, 12, 12, 12)
        detailsLayout.setSpacing(6)

        detailsTitle = StrongBodyLabel('Details', detailsCard)
        detailsLayout.addWidget(detailsTitle)

        self._detailsCard = detailsCard
        self._detailsLayout = detailsLayout
        self.__scheduleDetailsPaneHeightSync()
        layout.addWidget(detailsCard, 0, Qt.AlignmentFlag.AlignTop)

    def __scheduleDetailsPaneHeightSync(self):
        if self._detailsPaneHeightSyncPending:
            return

        self._detailsPaneHeightSyncPending = True
        QTimer.singleShot(0, self.__syncDetailsPaneHeight)

    def __syncDetailsPaneHeight(self):
        self._detailsPaneHeightSyncPending = False
        if self._detailsCard is None or self._detailsLayout is None:
            return

        self._detailsLayout.activate()
        self._detailsCard.setFixedHeight(self._detailsCard.sizeHint().height())

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

        self.__scheduleDetailsPaneHeightSync()

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
