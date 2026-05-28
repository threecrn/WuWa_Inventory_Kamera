"""
wuwa_inventory_kamera.updater.database
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Core data updater — no GUI/Qt dependencies.

Moved from ``updater/databaseUpdater.py`` (``BaseDataUpdater``).
The Qt-dependent ``DataUpdater`` remains in the legacy module.
"""
import re
import json
import urllib.parse
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
	local_file: Optional[str] = None

	def remote_path(self, lang: str) -> str:
		parts = [part.format(lang=lang) for part in self.folder]
		return '/'.join(parts + [self.file])

	@property
	def local_name(self) -> str:
		return self.local_file or self.file


@dataclass(frozen=True)
class SourceConfig:
	owner: str
	repo: str
	language_root: str
	files: tuple[FileConfig, ...]
	ref: Optional[str] = None


class BaseDataUpdater:
	"""
	Core data updater — no GUI/Qt dependencies.

	Subclass and override the ``_after*`` hooks to react to completed update
	steps (e.g. to refresh in-memory caches), and override ``_onProgress`` /
	``_onFinished`` to receive lifecycle notifications.
	"""

	API = 'https://api.github.com/repos/{owner}/{repo}/contents/{path}'
	DEFAULT_SOURCE = 'dimbreath'
	SOURCES = {
		'dimbreath': SourceConfig(
			owner='Dimbreath',
			repo='WutheringData',
			language_root='TextMap',
			files=(
				FileConfig(['TextMap', '{lang}'], 'MultiText.json'),
				FileConfig(['ConfigDB'], 'ItemInfo.json'),
				FileConfig(['ConfigDB'], 'WeaponConf.json'),
			),
		),
		'arikatsu': SourceConfig(
			owner='Arikatsu',
			repo='WutheringWaves_Data',
			language_root='Textmaps',
			files=(
				FileConfig(['Textmaps', '{lang}', 'multi_text'], 'MultiText.json'),
				FileConfig(['BinData', 'item'], 'iteminfo.json', 'ItemInfo.json'),
				FileConfig(['BinData', 'weapon'], 'weaponconf.json', 'WeaponConf.json'),
			),
		),
	}

	def __init__(self, lang: Optional[str] = None, source: Optional[str] = None):
		self.source = self._getSource(source)
		self.source_config = self.SOURCES[self.source]
		self.author = self.source_config.owner
		self.repo = self.source_config.repo
		self.ref = self.source_config.ref
		self.lang = self._getLanguage(lang)
		self.makeFolder(self.lang)
		self.files = list(self.source_config.files)
		self.state_path = Path('data') / self.lang / '.updater_state.json'
		self.state = self._loadUpdaterState()
		self.updated = False
		self._update_failed = False
		self._catalog_text_key_cache: dict[str, dict[str, str]] = {}

	def _getSource(self, preferred: Optional[str] = None) -> str:
		source = (preferred or self.DEFAULT_SOURCE).strip().lower()
		if source not in self.SOURCES:
			valid = ', '.join(sorted(self.SOURCES))
			raise ValueError(f'Unsupported updater source: {preferred!r}. Expected one of: {valid}')
		return source

	def _buildContentsUrl(self, path: str) -> str:
		url = self.API.format(owner=self.author, repo=self.repo, path=path)
		if self.ref:
			return f'{url}?ref={urllib.parse.quote(self.ref, safe="")}'
		return url

	def _baseUpdaterState(self) -> dict[str, Any]:
		return {
			'source': self.source,
			'ref': self.ref or '',
			'files': {},
		}

	def _loadUpdaterState(self) -> dict[str, Any]:
		try:
			with open(self.state_path, 'r', encoding='utf-8') as f:
				return json.load(f)
		except (FileNotFoundError, json.JSONDecodeError):
			return {}

	def _saveUpdaterState(self, state: dict[str, Any]) -> None:
		try:
			with open(self.state_path, 'w', encoding='utf-8') as f:
				json.dump(state, f, indent=4, ensure_ascii=False)
		except Exception as e:
			logger.error('Failed to save updater state: %s', e)

	def _dataRoot(self) -> Path:
		return Path('data')

	def _compatDir(self, lang: Optional[str] = None) -> Path:
		return self._dataRoot() / (lang or self.lang)

	def _catalogDir(self) -> Path:
		return self._dataRoot() / 'catalog'

	def _localeDir(self, lang: Optional[str] = None) -> Path:
		return self._dataRoot() / 'locale' / (lang or self.lang)

	def _localeLookupDir(self, lang: Optional[str] = None) -> Path:
		return self._localeDir(lang) / 'lookup'

	def _isCanonicalLanguage(self) -> bool:
		return self.lang == 'en'

	def _stateMatchesSource(self, state: dict[str, Any]) -> bool:
		return state.get('source') == self.source and state.get('ref', '') == (self.ref or '')

	def _normalizeText(self, text: str, *, remove_chars: str = ' ') -> str:
		normalized = text.strip().lower()
		if remove_chars:
			normalized = normalized.translate(str.maketrans('', '', remove_chars))
		return normalized

	def _loadJsonPath(self, path: Path, filename: Optional[str] = None) -> Any:
		try:
			with open(path, 'r', encoding='utf-8') as f:
				return self._normalizeJson(filename or path.name, json.load(f))
		except (FileNotFoundError, json.JSONDecodeError):
			return {}

	def _saveJsonPath(self, data: Any, path: Path, filename: Optional[str] = None) -> None:
		try:
			path.parent.mkdir(parents=True, exist_ok=True)
			with open(path, 'w', encoding='utf-8') as f:
				json.dump(data, f, indent=4, ensure_ascii=False)
		except Exception as e:
			logger.error('Failed to save %s: %s', filename or path.name, e)

	def _loadInfoText(self) -> dict[str, str]:
		info_text = self.loadJson('MultiText.json')
		if not isinstance(info_text, dict) or not info_text:
			logger.error('MultiText.json not found or empty')
			return {}
		return {
			str(key): value
			for key, value in info_text.items()
			if isinstance(key, str) and isinstance(value, str)
		}

	def _buildLocaleRecord(self, display_name: str, *, normalized: str) -> dict[str, Any]:
		aliases: list[str] = []
		for alias in (normalized,):
			if alias and alias not in aliases:
				aliases.append(alias)
		return {
			'display_name': display_name,
			'normalized': normalized,
			'aliases': aliases,
		}

	def _buildLookupEntries(self, entries: dict[str, dict[str, Any]], *, label: str) -> dict[str, str]:
		lookup: dict[str, str] = {}
		for canonical_key, record in entries.items():
			if not isinstance(record, dict):
				continue
			candidates: list[str] = []
			normalized = record.get('normalized')
			if isinstance(normalized, str) and normalized:
				candidates.append(normalized)
			aliases = record.get('aliases')
			if isinstance(aliases, list):
				for alias in aliases:
					if isinstance(alias, str) and alias and alias not in candidates:
						candidates.append(alias)
			for candidate in candidates:
				owner = lookup.get(candidate)
				if owner is not None and owner != canonical_key:
					logger.warning(
						'Collision in %s lookup for %r: %s vs %s',
						label,
						candidate,
						owner,
						canonical_key,
					)
					continue
				lookup[candidate] = canonical_key
		return lookup

	def _saveCatalogEntries(self, filename: str, entries: dict[str, dict[str, Any]]) -> None:
		self._saveJsonPath(entries, self._catalogDir() / filename, filename)
		self._catalog_text_key_cache[filename] = {
			info['text_key']: canonical_key
			for canonical_key, info in entries.items()
			if isinstance(info, dict) and isinstance(info.get('text_key'), str)
		}

	def _saveLocaleEntries(self, filename: str, entries: dict[str, dict[str, Any]]) -> None:
		self._saveJsonPath(entries, self._localeDir() / filename, filename)
		self._saveJsonPath(
			self._buildLookupEntries(entries, label=filename),
			self._localeLookupDir() / filename,
			f'lookup/{filename}',
		)

	def _catalogKeyByTextKey(self, filename: str) -> dict[str, str]:
		cached = self._catalog_text_key_cache.get(filename)
		if cached is not None:
			return cached

		entries = self._loadJsonPath(self._catalogDir() / filename, filename)
		mapping = {
			info['text_key']: canonical_key
			for canonical_key, info in entries.items()
			if isinstance(info, dict) and isinstance(info.get('text_key'), str)
		} if isinstance(entries, dict) else {}
		self._catalog_text_key_cache[filename] = mapping
		return mapping

	def _resolveCanonicalKeyFromTextKey(
		self,
		catalog_filename: str,
		text_key: str,
		english_key: Optional[str],
	) -> Optional[str]:
		if self._isCanonicalLanguage():
			return english_key

		canonical_key = self._catalogKeyByTextKey(catalog_filename).get(text_key)
		if canonical_key is None:
			logger.warning(
				'Skipping %s locale entry for %s because no canonical catalog entry exists',
				catalog_filename,
				text_key,
			)
		return canonical_key

	def _updatePatternCategory(
		self,
		*,
		compat_filename: str,
		catalog_filename: str,
		locale_filename: str,
		pattern: str,
		compat_key_builder,
		canonical_key_builder,
		normalized_builder,
	) -> dict:
		logger.info('Generating %s...', compat_filename)
		try:
			info_text = self._loadInfoText()
			if not info_text:
				return {}

			compiled_pattern = re.compile(pattern)
			compat_data: dict[str, int] = {}
			catalog_data: dict[str, dict[str, Any]] = {}
			locale_data: dict[str, dict[str, Any]] = {}

			for text_key, display_name in info_text.items():
				match = compiled_pattern.match(text_key)
				if match is None:
					continue

				compat_key = compat_key_builder(display_name, match)
				if compat_key is None:
					continue

				identifier = int(match.group(1))
				compat_data[compat_key] = identifier

				english_key = canonical_key_builder(display_name, match)
				canonical_key = self._resolveCanonicalKeyFromTextKey(
					catalog_filename,
					text_key,
					english_key,
				)
				if canonical_key is None:
					continue

				if self._isCanonicalLanguage():
					catalog_data[canonical_key] = {
						'id': identifier,
						'text_key': text_key,
					}

				locale_data[canonical_key] = self._buildLocaleRecord(
					display_name,
					normalized=normalized_builder(display_name, match),
				)

			self.saveJson(compat_data, compat_filename)
			if self._isCanonicalLanguage():
				self._saveCatalogEntries(catalog_filename, catalog_data)
			if self._isCanonicalLanguage() or locale_data:
				self._saveLocaleEntries(locale_filename, locale_data)

			logger.info('Generated %s with %d entries', compat_filename, len(compat_data))
			return compat_data
		except Exception as e:
			logger.error('Failed to generate %s: %s', compat_filename, e, exc_info=True)
			return {}

	def _normalizeJson(self, filename: str, data: Any) -> Any:
		if filename != 'MultiText.json' or not isinstance(data, list):
			return data

		return {
			str(entry['Id']): entry.get('Content', '')
			for entry in data
			if isinstance(entry, dict) and entry.get('Id') is not None and 'Content' in entry
		}

	def _normalizeDownloadedFile(self, file_path: Path, filename: str) -> None:
		try:
			with open(file_path, 'r', encoding='utf-8') as f:
				data = json.load(f)
			normalized = self._normalizeJson(filename, data)
			if normalized != data:
				with open(file_path, 'w', encoding='utf-8') as f:
					json.dump(normalized, f, indent=2, ensure_ascii=False)
		except Exception as e:
			self._update_failed = True
			logger.error('Failed to normalize %s: %s', filename, e, exc_info=True)

	def _getLanguage(self, preferred: Optional[str] = None) -> str:
		self.makeFolder()
		url = self._buildContentsUrl(self.source_config.language_root)
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
			if preferred in languages:
				return languages[preferred]
			if preferred in languages.values():
				return preferred

		return languages.get('English', 'en')

	def makeFolder(self, lang=None) -> None:
		if lang:
			dir = self._compatDir(lang)
		else:
			dir = self._dataRoot()
		dir.mkdir(parents=True, exist_ok=True)

	def _getLanguageName(self, code: str) -> str:
		parts = code.split('-')
		locale = Locale(parts[0], script=parts[1] if len(parts) > 1 else None)
		try:
			display_name = locale.get_display_name()
			return display_name.capitalize() if display_name else code
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

	def loadJson(self, filename: str) -> Any:
		if filename == 'languages.json':
			return self._loadJsonPath(self._dataRoot() / filename, filename)
		return self._loadJsonPath(self._compatDir() / filename, filename)

	def saveJson(self, data: Any, filename: str) -> None:
		if filename == 'languages.json':
			self._saveJsonPath(data, self._dataRoot() / filename, filename)
			return
		self._saveJsonPath(data, self._compatDir() / filename, filename)

	# ------------------------------------------------------------------
	# Update steps
	# ------------------------------------------------------------------

	def updateFiles(self) -> None:
		"""Download remote files that have changed since the last run."""
		self.updated = False
		self._update_failed = False
		source_changed = not self._stateMatchesSource(self.state)
		next_state = self._baseUpdaterState()

		if source_changed:
			logger.info('Updater source changed to %s, refreshing cached source files', self.source)

		for fileConfig in self.files:
			remote_path = fileConfig.remote_path(self.lang)
			url = self._buildContentsUrl(remote_path)
			local_name = fileConfig.local_name
			logger.info('Checking for updates on file: %s', local_name)
			try:
				data = self.fetchFileData(url)
				filePath = Path('data') / self.lang / local_name

				if not isinstance(data, dict) or not data.get('download_url'):
					logger.warning('No downloadable data received for %s', local_name)
					self._update_failed = True
					continue

				previous = self.state.get('files', {}).get(local_name, {})
				needs_download = (
					source_changed
					or not filePath.is_file()
					or previous.get('sha') != data.get('sha')
				)

				if needs_download:
					logger.info('Downloading updated version of %s...', local_name)
					urllib.request.urlretrieve(
						data['download_url'],
						filePath,
						reporthook=lambda bn, bs, ts, _fn=local_name: (
							self._onProgress(_fn, (bn * bs / ts) * 100 if ts > 0 else 0)
						),
					)
					self._normalizeDownloadedFile(filePath, local_name)
					self.updated = True
					logger.info('File updated: %s (%d bytes)', local_name, data.get('size', 0))
				else:
					logger.info('%s is up to date', local_name)

				next_state['files'][local_name] = {
					'sha': data.get('sha'),
					'path': remote_path,
				}
			except Exception as e:
				self._update_failed = True
				logger.error('Failed to process %s: %s', local_name, e)

		if not self._update_failed:
			self.state = next_state
			self._saveUpdaterState(next_state)

	def updateItems(self) -> None:
		"""Generate items.json and weapons.json from downloaded data."""
		logger.info('Generating items.json and weapons.json...')
		try:
			infoText = self._loadInfoText()
			itemInfo = self.loadJson('ItemInfo.json')
			weaponInfo = self.loadJson('WeaponConf.json')

			if not all([infoText, itemInfo, weaponInfo]):
				logger.error('Missing required data files for item generation')
				return

			items: dict[str, dict[str, Any]] = {}
			item_catalog: dict[str, dict[str, Any]] = {}
			item_locale: dict[str, dict[str, Any]] = {}
			for item in itemInfo:
				text_key = item.get('Name')
				if not isinstance(text_key, str) or text_key not in infoText:
					continue

				display_name = infoText[text_key]
				compat_key = self._normalizeText(display_name)
				image = item['Icon'].split('/Image/')[1].rsplit('.', 1)[0] + '.png'
				items[compat_key] = {
					'id': item['Id'],
					'name': display_name,
					'image': image,
				}

				canonical_key = self._resolveCanonicalKeyFromTextKey('items.json', text_key, compat_key)
				if canonical_key is None:
					continue

				if self._isCanonicalLanguage():
					item_catalog[canonical_key] = {
						'id': item['Id'],
						'text_key': text_key,
						'image': image,
					}
				item_locale[canonical_key] = self._buildLocaleRecord(display_name, normalized=compat_key)

			weapons: dict[str, dict[str, Any]] = {}
			weapon_catalog: dict[str, dict[str, Any]] = {}
			weapon_locale: dict[str, dict[str, Any]] = {}
			for weapon in weaponInfo:
				text_key = weapon.get('WeaponName')
				if not isinstance(text_key, str) or text_key not in infoText:
					continue

				display_name = infoText[text_key]
				compat_key = self._normalizeText(display_name)
				image = weapon['Icon'].split('/Image/')[1].rsplit('.', 1)[0] + '.png'
				weapons[compat_key] = {
					'id': weapon['ModelId'],
					'name': display_name,
					'rarity': weapon['QualityId'],
					'image': image,
				}

				canonical_key = self._resolveCanonicalKeyFromTextKey('weapons.json', text_key, compat_key)
				if canonical_key is None:
					continue

				if self._isCanonicalLanguage():
					weapon_catalog[canonical_key] = {
						'id': weapon['ModelId'],
						'text_key': text_key,
						'rarity': weapon['QualityId'],
						'image': image,
					}
				weapon_locale[canonical_key] = self._buildLocaleRecord(display_name, normalized=compat_key)

			self.saveJson(items, 'items.json')
			self.saveJson(weapons, 'weapons.json')
			if self._isCanonicalLanguage():
				self._saveCatalogEntries('items.json', item_catalog)
				self._saveCatalogEntries('weapons.json', weapon_catalog)
			if self._isCanonicalLanguage() or item_locale:
				self._saveLocaleEntries('items.json', item_locale)
			if self._isCanonicalLanguage() or weapon_locale:
				self._saveLocaleEntries('weapons.json', weapon_locale)
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
		data = self._updatePatternCategory(
			compat_filename='characters.json',
			catalog_filename='characters.json',
			locale_filename='characters.json',
			pattern=r'^RoleInfo_(\d+)_Name$',
			compat_key_builder=lambda text, match: self._normalizeText(text) if int(match.group(1)) < 5000 else None,
			canonical_key_builder=lambda text, match: self._normalizeText(text) if int(match.group(1)) < 5000 else None,
			normalized_builder=lambda text, _: self._normalizeText(text),
		)
		self._afterUpdateCharacters(data)

	def updateEcho(self) -> None:
		data = self._updatePatternCategory(
			compat_filename='echoes.json',
			catalog_filename='echoes.json',
			locale_filename='echoes.json',
			pattern=r'^MonsterInfo_(\d+)_Name$',
			compat_key_builder=lambda text, match: self._normalizeText(text) if int(match.group(1)) < 350000000 else None,
			canonical_key_builder=lambda text, match: self._normalizeText(text) if int(match.group(1)) < 350000000 else None,
			normalized_builder=lambda text, _: self._normalizeText(text),
		)
		self._afterUpdateEchoes(data)

	def updateAchievements(self) -> None:
		data = self._updatePatternCategory(
			compat_filename='achievements.json',
			catalog_filename='achievements.json',
			locale_filename='achievements.json',
			pattern=r'^Achievement_(\d+)_Name$',
			compat_key_builder=lambda text, _: text,
			canonical_key_builder=lambda text, _: self._normalizeText(text),
			normalized_builder=lambda text, _: self._normalizeText(text),
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
			infoText = self._loadInfoText()
			if not infoText:
				return

			stats: dict[str, str] = {}
			catalog_data: dict[str, dict[str, Any]] = {}
			locale_data: dict[str, dict[str, Any]] = {}
			for text_key, canonical_key in statsKey.items():
				if text_key not in infoText:
					continue
				display_name = infoText[text_key]
				normalized = self._normalizeText(display_name, remove_chars=' .')
				stats[normalized] = canonical_key
				if self._isCanonicalLanguage():
					catalog_data[canonical_key] = {'text_key': text_key}
				locale_data[canonical_key] = self._buildLocaleRecord(display_name, normalized=normalized)

			self.saveJson(stats, 'echoStats.json')
			if self._isCanonicalLanguage():
				self._saveCatalogEntries('stats.json', catalog_data)
			if self._isCanonicalLanguage() or locale_data:
				self._saveLocaleEntries('stats.json', locale_data)
			logger.info('Generated echoStats.json with %d entries', len(stats))
			self._afterUpdateEchoStats(stats)
		except Exception as e:
			logger.error('Failed to generate echoStats.json: %s', e, exc_info=True)

	def updateSonata(self) -> None:
		data = self._updatePatternCategory(
			compat_filename='sonataName.json',
			catalog_filename='sonatas.json',
			locale_filename='sonatas.json',
			pattern=r'^PhantomFetter_(\d+)_Name$',
			compat_key_builder=lambda text, _: self._normalizeText(text),
			canonical_key_builder=lambda text, _: self._normalizeText(text),
			normalized_builder=lambda text, _: self._normalizeText(text),
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
			infoText = self._loadInfoText()
			if not infoText:
				return

			stats: dict[str, str] = {}
			locale_data: dict[str, dict[str, Any]] = {}
			for key in textKey:
				if key not in infoText:
					continue
				display_text = infoText[key]
				normalized = self._normalizeText(display_text, remove_chars=' -')
				stats[key] = normalized
				locale_data[key] = {
					'display_text': display_text,
					'normalized': normalized,
					'aliases': [normalized] if normalized else [],
				}

			self.saveJson(stats, 'definedText.json')
			if self._isCanonicalLanguage() or locale_data:
				self._saveLocaleEntries('definedText.json', locale_data)
			logger.info('Generated definedText.json')
			self._afterUpdateDefinedText(stats)
		except Exception as e:
			logger.error('Failed to generate definedText.json: %s', e, exc_info=True)

	def run(self) -> None:
		logger.info('Starting data update...')
		logger.info('Using language: %s', self.lang)
		logger.info('Using data source: %s (%s/%s)', self.source, self.author, self.repo)
		self.updateFiles()
		if self._update_failed:
			logger.warning('Skipping derived file regeneration because one or more source files failed to update')
		elif self.updated:
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
