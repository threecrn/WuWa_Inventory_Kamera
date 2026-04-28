"""
wuwa_inventory_kamera.ui.main_window
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Main application window — QFluentWidgets MSFluentWindow with
Home, Inventory, and Settings interfaces.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from qfluentwidgets import (
    FluentIcon as FIF,
    NavigationItemPosition, MSFluentWindow,
    InfoBar, InfoBarPosition,
)

from .home import HomeInterface
from .settings import SettingInterface
from .inventory import InventoryInterface
from ..config.app_config import basePATH
from ..scraping.utils.common import isUserAdmin

logger = logging.getLogger('WuWaInventoryKamera')


class WuWaInventoryKamera(MSFluentWindow):
    """Main window for the WuWa Inventory Kamera application."""

    def __init__(self):
        super().__init__()
        self.initInterface()
        self.initNavigation()
        self.initWindow()
        self.warningInfoBar()

    def initInterface(self):
        self.homeInterface = HomeInterface(self)
        self.inventoryInterface = InventoryInterface(self)
        self.settingInterface = SettingInterface(self)

    def initNavigation(self):
        self.addSubInterface(self.homeInterface, FIF.HOME, 'Home', FIF.HOME_FILL)
        self.addSubInterface(self.inventoryInterface, FIF.DICTIONARY, 'Inventory')
        self.addSubInterface(
            self.settingInterface, FIF.SETTING, 'Settings',
            position=NavigationItemPosition.BOTTOM,
        )
        self.navigationInterface.setCurrentItem(self.homeInterface.objectName())

    def initWindow(self):
        self.setFixedSize(1150, 700)
        self.setWindowIcon(QIcon(str(basePATH / 'assets' / 'icon.ico')))
        self.setWindowTitle('WuWa Inventory Kamera')
        self.titleBar.maxBtn.setHidden(True)
        self.titleBar.maxBtn.setDisabled(True)
        self.titleBar.setDoubleClickEnabled(False)
        self.setResizeEnabled(False)

        desktop = QApplication.primaryScreen().availableGeometry()
        self.move(
            desktop.width() // 2 - self.width() // 2,
            desktop.height() // 2 - self.height() // 2,
        )

    def warningInfoBar(self):
        if not isUserAdmin():
            InfoBar.warning(
                title='Warning',
                content=(
                    "Administrator privileges not granted.\n"
                    "To use the scanner, administrator rights must be granted."
                ),
                orient=Qt.Vertical,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=-1,
                parent=self,
            )
            logger.warning("Administrator privileges not granted.")
