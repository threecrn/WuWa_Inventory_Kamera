import re
import json
import urllib.request
import logging
from babel import Locale
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger('DatabaseManager')


@dataclass
class FileConfig:
	folder: list[str]
	file: str


class BaseDataUpdater:
	"""
	Core data updater — no GUI/Qt dependencies.

	Subclass and override the ``_after*`` hooks to react to completed update
	steps (e.g. to refresh in-memory caches), and override ``_onProgress`` /
	``_onFinished`` to receive lifecycle notifications.
	"""

	API = 'https://api.github.com/repos/{owner}/{repo}/contents/{path}'

	def __init__(self, lang: Optional[str] = None):
		self.author = 'Dimbreath'
		self.repo = 'WutheringData'
		self.lang = self._getLanguage(lang)
		self.makeFolder(self.lang)
		self.files = [
			FileConfig(['TextMap', self.lang], 'MultiText.json'),
			FileConfig(['ConfigDB'], 'ItemInfo.json'),
			FileConfig(['ConfigDB'], 'WeaponConf.json'),
		]
		self.updated = False

	def _getLanguage(self, preferred: Optional[str] = None) -> str:
		"""
		Resolve the TextMap directory name to use.

		If *preferred* is a known display name (e.g. ``'English'``) it is
		looked up in ``languages.json``; if it already looks like a folder name
		(e.g. ``'en-US'``) it is used directly.  Falls back to ``'English'``
		when nothing matches.
		"""
		self.makeFolder()
		url = self.API.format(
			owner=self.author,
			repo=self.repo,
			path='TextMap',
		)
		languages = self.loadJson('languages.json')

		if not languages:
			logger.info('Fetching available languages...')
			try:
				items = self.fetchFileData(url)
				languages = {
					self._getLanguageName(item['name']): item['name']
					for item in items if item.get('type') == 'dir'
				}
				self.saveJson(languages, 'languages.json')
				logger.info('Available languages: %s', ', '.join(languages.keys()))
			except Exception as e:
				logger.error('Failed to fetch languages: %s', e)
				return 'en'

		if preferred:
			# Try as display name first, then as a raw folder name.
			if preferred in languages:
				return languages[preferred]
			if preferred in languages.values():
				return preferred

		return languages.get('English', 'en')

	def makeFolder(self, lang=None) -> None:
		if lang:
			dir = (Path('data') / lang)
		else:
			dir = Path('data')
		dir.mkdir(parents=True, exist_ok=True)

	def _getLanguageName(self, code: str) -> str:
		parts = code.split('-')
		locale = Locale(parts[0], script=parts[1] if len(parts) > 1 else None)
		try:
			return locale.get_display_name().capitalize()
		except Exception:
			return code

	def fetchFileData(self, url: str) -> Any:
		try:
			with urllib.request.urlopen(urllib.request.Request(url)) as response:
				return json.loads(response.read().decode())
		except Exception as e:
			logger.error('Failed to fetch data from %s: %s', url, e)
			return {}

	# ------------------------------------------------------------------
	# Subclass hooks
	# ------------------------------------------------------------------

	def _onProgress(self, file_name: str, percent: float) -> None:
		"""Called during file downloads with the current progress percent."""

	def _onFinished(self) -> None:
		"""Called at the end of :meth:`run`."""

	def _afterUpdateItems(self, items: dict, weapons: dict) -> None:
		"""Called after items.json / weapons.json are written."""

	def _afterUpdateCharacters(self, data: dict) -> None:
		"""Called after characters.json is written."""

	def _afterUpdateEchoes(self, data: dict) -> None:
		"""Called after echoes.json is written."""

	def _afterUpdateAchievements(self, data: dict) -> None:
		"""Called after achievements.json is written."""

	def _afterUpdateEchoStats(self, stats: dict) -> None:
		"""Called after echoStats.json is written."""

	def _afterUpdateSonata(self, data: dict) -> None:
		"""Called after sonataName.json is written."""

	def _afterUpdateDefinedText(self, stats: dict) -> None:
		"""Called after definedText.json is written."""

	# ------------------------------------------------------------------
	# I/O helpers
	# ------------------------------------------------------------------

	def loadJson(self, filename: str) -> dict:
		dir = Path('data') / self.lang if filename != 'languages.json' else Path('data')
		try:
			with open(dir / filename, 'r', encoding='utf-8') as f:
				return json.load(f)
		except (FileNotFoundError, json.JSONDecodeError):
			return {}

	def saveJson(self, data: dict, filename: str) -> None:
		dir = Path('data') / self.lang if filename != 'languages.json' else Path('data')
		try:
			with open(dir / filename, 'w', encoding='utf-8') as f:
				json.dump(data, f, indent=4, ensure_ascii=False)
		except Exception as e:
			logger.error('Failed to save %s: %s', filename, e)

	# ------------------------------------------------------------------
	# Update steps
	# ------------------------------------------------------------------

	def updateFiles(self) -> None:
		"""Download remote files that have changed since the last run."""
		return
		for fileConfig in self.files:
			url = self.API.format(
				owner=self.author,
				repo=self.repo,
				path='/'.join(fileConfig.folder + [fileConfig.file]),
			)
			logger.info('Checking for updates on file: %s', fileConfig.file)
			try:
				data = self.fetchFileData(url)
				filePath = Path('data') / self.lang / fileConfig.file

				if not data:
					logger.warning('No data received for %s', fileConfig.file)
					continue

				currentSize = filePath.stat().st_size if filePath.is_file() else 0

				if data.get('size', 0) != currentSize:
					logger.info('Downloading updated version of %s...', fileConfig.file)
					urllib.request.urlretrieve(
						data['download_url'],
						filePath,
						reporthook=lambda bn, bs, ts, _fn=fileConfig.file: (
							self._onProgress(_fn, (bn * bs / ts) * 100 if ts > 0 else 0)
						),
					)
					self.updated = True
					logger.info('File updated: %s (%d bytes)', fileConfig.file, data.get('size', 0))
				else:
					logger.info('%s is up to date', fileConfig.file)
			except Exception as e:
				logger.error('Failed to process %s: %s', fileConfig.file, e)

	def updateItems(self) -> None:
		"""Generate items.json and weapons.json from downloaded data."""

		dir = Path('data') / self.lang

		if (dir / 'items.json').is_file():
			logger.info('items.json already exists, skipping generation')
			return

		logger.info('Generating items.json and weapons.json...')
		try:
			infoText = self.loadJson('MultiText.json')
			itemInfo = self.loadJson('ItemInfo.json')
			weaponInfo = self.loadJson('WeaponConf.json')

			if not all([infoText, itemInfo, weaponInfo]):
				logger.error('Missing required data files for item generation')
				return

			items = {
				infoText[item['Name']].lower().replace(' ', ''): {
					'id': item['Id'],
					'name': infoText[item['Name']],
					'image': item['Icon'].split('/Image/')[1].rsplit('.', 1)[0] + '.png',
				}
				for item in itemInfo if item['Name'] in infoText
			}
			weapons = {
				infoText[weapon['WeaponName']].lower().replace(' ', ''): {
					'id': weapon['ModelId'],
					'name': infoText[weapon['WeaponName']],
					'rarity': weapon['QualityId'],
					'image': weapon['Icon'].split('/Image/')[1].rsplit('.', 1)[0] + '.png',
				}
				for weapon in weaponInfo if weapon['WeaponName'] in infoText
			}

			self.saveJson(items, 'items.json')
			self.saveJson(weapons, 'weapons.json')
			logger.info('Generated items.json (%d items) and weapons.json (%d weapons)', len(items), len(weapons))
			self._afterUpdateItems(items, weapons)

		except Exception as e:
			logger.error('Failed to generate items data: %s', e, exc_info=True)

	def updateJsonFromPattern(self, fileName: str, pattern: str, transformFunc) -> dict:
		"""Extract data from MultiText.json using a regex pattern."""
		logger.info('Generating %s...', fileName)
		try:
			infoText = self.loadJson('MultiText.json')
			if not infoText:
				logger.error('MultiText.json not found or empty')
				return {}

			data = {}
			compiledPattern = re.compile(pattern)
			for key in infoText:
				if match := compiledPattern.match(key):
					transformed = transformFunc(infoText[key], match)
					if transformed is not None:
						data[transformed] = int(match.group(1))

			self.saveJson(data, fileName)
			logger.info('Generated %s with %d entries', fileName, len(data))
			return data
		except Exception as e:
			logger.error('Failed to generate %s: %s', fileName, e, exc_info=True)
			return {}

	def updateCharacters(self) -> None:
		data = self.updateJsonFromPattern(
			'characters.json',
			r'^RoleInfo_(\d+)_Name$',
			lambda text, match: text.lower().replace(' ', '') if int(match.group(1)) < 5000 else None,
		)
		self._afterUpdateCharacters(data)

	def updateEcho(self) -> None:
		data = self.updateJsonFromPattern(
			'echoes.json',
			r'^MonsterInfo_(\d+)_Name$',
			lambda text, match: text.lower().replace(' ', '') if int(match.group(1)) < 350000000 else None,
		)
		self._afterUpdateEchoes(data)

	def updateAchievements(self) -> None:
		data = self.updateJsonFromPattern(
			'achievements.json',
			r'^Achievement_(\d+)_Name$',
			lambda text, _: text,
		)
		self._afterUpdateAchievements(data)

	def updateEchoStats(self) -> None:
		statsKey = {
			'PropertyIndex_10003_Name': 'hp',
			'PropertyIndex_10007_Name': 'atk',
			'PropertyIndex_10008_Name': 'cr',
			'PropertyIndex_10009_Name': 'cd',
			'PropertyIndex_10010_Name': 'def',
			'PropertyIndex_10011_Name': 'er',
			'PropertyIndex_10014_Name': 'skillDmg',
			'PropertyIndex_10017_Name': 'basicAttack',
			'PropertyIndex_10018_Name': 'heavyAttack',
			'PropertyIndex_10019_Name': 'liberationDmg',
			'PropertyIndex_10022_Name': 'glacio',
			'PropertyIndex_10023_Name': 'fusion',
			'PropertyIndex_10024_Name': 'electro',
			'PropertyIndex_10025_Name': 'aero',
			'PropertyIndex_10026_Name': 'spectro',
			'PropertyIndex_10027_Name': 'havoc',
			'PropertyIndex_10035_Name': 'healing',
		}
		logger.info('Generating echoStats.json...')
		try:
			infoText = self.loadJson('MultiText.json')
			if not infoText:
				logger.error('MultiText.json not found or empty')
				return

			stats = {
				infoText[key].lower().replace(' ', '').replace('.', ''): value
				for key, value in statsKey.items() if key in infoText
			}
			self.saveJson(stats, 'echoStats.json')
			logger.info('Generated echoStats.json with %d entries', len(stats))
			self._afterUpdateEchoStats(stats)
		except Exception as e:
			logger.error('Failed to generate echoStats.json: %s', e, exc_info=True)

	def updateSonata(self) -> None:
		data = self.updateJsonFromPattern(
			'sonataName.json',
			r'^PhantomFetter_(\d+)_Name$',
			lambda text, _: text.lower().replace(' ', ''),
		)
		self._afterUpdateSonata(data)

	def updateDefinedText(self) -> None:
		textKey = [
			'PrefabTextItem_1547656443_Text',  # Terminal
			'PrefabTextItem_128820487_Text',   # Claim
			'PrefabTextItem_3963945691_Text',  # Activated
		]
		logger.info('Generating definedText.json...')
		try:
			infoText = self.loadJson('MultiText.json')
			if not infoText:
				logger.error('MultiText.json not found or empty')
				return

			stats = {
				key: infoText[key].lower().replace(' ', '').replace('-', '').strip()
				for key in textKey if key in infoText
			}
			self.saveJson(stats, 'definedText.json')
			logger.info('Generated definedText.json')
			self._afterUpdateDefinedText(stats)
		except Exception as e:
			logger.error('Failed to generate definedText.json: %s', e, exc_info=True)

	def run(self) -> None:
		logger.info('Starting data update...')
		logger.info('Using language: %s', self.lang)
		self.updateFiles()
		if self.updated:
			logger.info('Files were updated, regenerating derived files...')
			self.updateItems()
			self.updateEchoStats()
			self.updateSonata()
			self.updateDefinedText()
			self.updateAchievements()
			self.updateCharacters()
			self.updateEcho()
		else:
			logger.info('All files are up to date')
		logger.info('Update process completed')
		self._onFinished()


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