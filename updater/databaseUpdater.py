"""
updater.databaseUpdater — compatibility module.

``BaseDataUpdater`` is re-exported from
``wuwa_inventory_kamera.updater.database``.
``DataUpdater`` (Qt-dependent) remains here.
"""
from wuwa_inventory_kamera.updater.database import BaseDataUpdater, FileConfig  # noqa: F401


# ---------------------------------------------------------------------------
# Qt-aware subclass (GUI use only — requires PySide6)
# ---------------------------------------------------------------------------

from PySide6.QtCore import QObject, Signal

from properties.config import cfg
from scraping.data import (
	itemsID, charactersID, weaponsID,
	echoesID, achievementsID, echoStats,
	definedText, sonataName,
)


class DataUpdater(QObject, BaseDataUpdater):
	updateProgress = Signal(int, str)
	updateFinished = Signal()

	def __init__(self):
		QObject.__init__(self)
		BaseDataUpdater.__init__(self, lang=cfg.get(cfg.gameLanguage))

	# ------------------------------------------------------------------
	# Lifecycle hooks → Qt signals
	# ------------------------------------------------------------------

	def _onProgress(self, file_name: str, percent: float) -> None:
		self.updateProgress.emit(int(percent), file_name)

	def _onFinished(self) -> None:
		self.updateFinished.emit()

	# ------------------------------------------------------------------
	# Post-update hooks → in-memory cache refresh
	# ------------------------------------------------------------------

	def _afterUpdateItems(self, items: dict, weapons: dict) -> None:
		itemsID.update(items)
		weaponsID.update(weapons)

	def _afterUpdateCharacters(self, data: dict) -> None:
		if data:
			charactersID.update(data)

	def _afterUpdateEchoes(self, data: dict) -> None:
		if data:
			echoesID.update(data)

	def _afterUpdateAchievements(self, data: dict) -> None:
		if data:
			achievementsID.update(data)

	def _afterUpdateEchoStats(self, stats: dict) -> None:
		echoStats.update(stats)

	def _afterUpdateSonata(self, data: dict) -> None:
		if data:
			sonataName.extend(list(data))

	def _afterUpdateDefinedText(self, stats: dict) -> None:
		definedText.update(stats)