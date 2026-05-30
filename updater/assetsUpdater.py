"""
updater.assetsUpdater — compatibility module.

``BaseAssetsUpdater`` is re-exported from
``wuwa_inventory_kamera.updater.assets``.
``AssetsUpdater`` (Qt-dependent) remains here.
"""
from wuwa_inventory_kamera.updater.assets import BaseAssetsUpdater  # noqa: F401

from PySide6.QtCore import QObject, Signal


class AssetsUpdater(QObject, BaseAssetsUpdater):
    updateProgress = Signal(int, str)
    updateFinished = Signal()

    def __init__(self, *, force: bool = False):
        QObject.__init__(self)
        BaseAssetsUpdater.__init__(self, force=force)

    def _onProgress(self, file_name: str, percent: float) -> None:
        self.updateProgress.emit(int(percent), file_name)

    def _onFinished(self) -> None:
        self.updateFinished.emit()
