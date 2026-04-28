"""
wuwa_inventory_kamera.ui.inventory
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Inventory viewer — load / save / edit JSON inventory files.
"""
from __future__ import annotations

import json
import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QIntValidator
from PySide6.QtWidgets import (
    QWidget, QFileDialog, QGridLayout,
    QVBoxLayout,
)

from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import (
    SettingCardGroup, ScrollArea, CardWidget,
    StrongBodyLabel, BodyLabel, LineEdit,
)

from .custom_widgets import MultiplePushSettingCard
from .config import cfg
from ..config.app_config import basePATH
from ..scraping.utils.common import itemsID

logger = logging.getLogger('InventoryInterface')


class ItemCard(CardWidget):
    """An item with image, name, and editable quantity."""

    def __init__(self, image_path, name, quantity, parent=None):
        super().__init__(parent)
        self.itemName = name
        self.quantity = quantity

        self.imageLabel = BodyLabel(self)
        self.nameLabel = StrongBodyLabel(
            name if len(name) < 19 else name[:16] + '...', self,
        )
        self.quantityLineEdit = LineEdit(self)

        self.setupQuantityLineEdit(quantity)
        self.setupImage(image_path)
        self.setupLayout()

    def setupQuantityLineEdit(self, quantity):
        self.quantityLineEdit.setText(str(quantity))
        self.quantityLineEdit.setValidator(QIntValidator(0, 999999999, self))
        self.quantityLineEdit.setAlignment(Qt.AlignCenter)

    def setupImage(self, image_path):
        pixmap = QPixmap(image_path)
        scaled_pixmap = pixmap.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.imageLabel.setPixmap(scaled_pixmap)
        self.imageLabel.setFixedSize(64, 64)
        self.imageLabel.setAlignment(Qt.AlignCenter)

    def setupLayout(self):
        vBoxLayout = QVBoxLayout(self)
        vBoxLayout.addWidget(self.imageLabel, alignment=Qt.AlignCenter)
        vBoxLayout.addWidget(self.nameLabel, alignment=Qt.AlignCenter)
        vBoxLayout.addWidget(self.quantityLineEdit, alignment=Qt.AlignCenter)
        vBoxLayout.setSpacing(5)
        vBoxLayout.setContentsMargins(5, 5, 5, 5)
        self.setToolTip(self.itemName)

    def getItemName(self):
        return self.itemName

    def getQuantity(self):
        try:
            return int(self.quantityLineEdit.text())
        except ValueError:
            return 0


class InventoryInterface(ScrollArea):
    """Scrollable inventory item grid."""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
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
            [self.tr('Load file'), self.tr('Save file')],
            FIF.DOWNLOAD,
            self.tr("Inventory file"),
            parent=self.inventoryGroup,
        )

        self.gridWidget = QWidget(self)
        self.gridLayout = QGridLayout(self.gridWidget)

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
        self.mainLayout.addWidget(self.gridWidget)
        self.mainLayout.addStretch(1)
        self.gridLayout.setSpacing(10)

    def __connectSignalToSlot(self):
        self.inventoryFileCard.buttonClicked.connect(self.__onInventoryFileCardClicked)

    def __onInventoryFileCardClicked(self, index):
        if index == 0:
            self.__loadInventoryFile()
        elif index == 1:
            self.__saveInventoryFile()

    def __loadInventoryFile(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("Choose file to load"),
            cfg.get(cfg.exportFolder),
            "JSON Files (*.json)",
        )
        if file_path:
            self.inventoryFileCard.setContent(file_path)
            with open(file_path, 'r', encoding='utf-8') as file:
                try:
                    data = json.load(file)
                    self.__populateGrid(data)
                except json.JSONDecodeError as e:
                    logger.error("Error loading JSON file: %s", e, exc_info=True)

    def __saveInventoryFile(self):
        file_path = self.inventoryFileCard.getContent()
        if file_path:
            inventory_data = {}
            for i in range(self.gridLayout.count()):
                widget = self.gridLayout.itemAt(i).widget()
                if isinstance(widget, ItemCard):
                    item_name = widget.getItemName()
                    quantity = widget.getQuantity()
                    item_id = itemsID.get(item_name, {}).get('id', None)
                    if item_id is not None:
                        inventory_data[item_id] = quantity

            with open(file_path, 'w', encoding='utf-8') as file:
                json.dump(inventory_data, file, ensure_ascii=False, indent=4)

    def __populateGrid(self, inventory_file):
        columns = 6
        for i in reversed(range(self.gridLayout.count())):
            widget = self.gridLayout.itemAt(i).widget()
            if widget:
                widget.setParent(None)

        for index, item_id in enumerate(inventory_file):
            image, name = self._getItemInfoByID(item_id)
            card = ItemCard(
                str(basePATH / 'assets' / image), name, inventory_file[item_id],
            )
            self.gridLayout.addWidget(card, index // columns, index % columns)

    def _getItemInfoByID(self, item_id: int):
        for _, info in itemsID.items():
            if info['id'] == int(item_id):
                return info['image'], info['name']
        return 'None', 'None'
