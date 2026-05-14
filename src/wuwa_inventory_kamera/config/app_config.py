"""
wuwa_inventory_kamera.config.app_config
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Pure-Python application configuration singleton — **no Qt dependency**.

Exports
-------
- ``app_config`` — singleton :class:`AppConfig` instance
- ``basePATH``   — project root as :class:`~pathlib.Path`
- ``PROCESS_NAME``, ``WINDOW_NAME`` — game identification constants
- ``INVENTORY``, ``FAILED`` — mutable session state (legacy compat)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

# ---------------------------------------------------------------------------
# Project root
# ---------------------------------------------------------------------------

#: Repository / install root — resolves to the directory containing
#: ``assets/``, ``data/``, ``config/``, etc.
basePATH: Path = Path(__file__).resolve().parents[3]

# ---------------------------------------------------------------------------
# Game identification (re-exported for legacy callers)
# ---------------------------------------------------------------------------

PROCESS_NAME: str = 'Client-Win64-Shipping.exe'
WINDOW_NAME: str = 'Wuthering Waves'

# ---------------------------------------------------------------------------
# Mutable session state (legacy — shared between UI & scraping code)
# ---------------------------------------------------------------------------

INVENTORY: dict = {'items': {}, 'date': ''}
FAILED: list = []


def default_ocr_cache_path(export_folder: str | Path = 'export') -> str:
    """Return the default SQLite path for the generalized OCR cache."""
    return str(Path(export_folder) / 'ocr-cache.sqlite3')

# ---------------------------------------------------------------------------
# AppConfig singleton
# ---------------------------------------------------------------------------

class AppConfig:
    """Qt-free configuration object.

    Attributes are set to sensible defaults and can be overridden at runtime
    by the Qt config layer via :meth:`sync_from_qconfig`, or directly by CLI
    tools.  Call :meth:`load` to populate from ``config/config.json``.
    """

    def __init__(self) -> None:
        self.exportFolder: str = 'export'
        self.ocrCachePath: str = default_ocr_cache_path(self.exportFolder)
        self.gameLanguage: str = 'English'
        self.inventoryKeybind: str = 'B'
        self.resonatorKeybind: str = 'C'
        self.roverName: str = 'Rover'
        self.checkUpdateAtStartUp: bool = True
        self.dataSource: str = 'Dimbreath'

        # Scanner thresholds
        self.echoMinRarity: int = 1
        self.echoMinLevel: int = 0
        self.weaponsMinRarity: int = 1
        self.weaponsMinLevel: int = 1

        # Scanner toggles
        self.scanCharacters: bool = False
        self.scanWeapons: bool = False
        self.scanEchoes: bool = False
        self.scanDevItems: bool = False
        self.scanResources: bool = False
        self.scanAchievements: bool = False

        # Advanced / debug
        self.logLevel: str = 'INFO'
        self.saveRaw: bool = False
        self.writeDebug: bool = False

        # Window mode
        self.windowed: bool = False

    def load(self, path: str | Path = 'config/config.json') -> 'AppConfig':
        """Populate fields from *path* (QConfig JSON format).

        Silently ignores a missing or malformed file — defaults are kept.
        """
        _BOOL: dict = {
            True: True, False: False,
            'true': True, 'false': False,
            1: True, 0: False,
        }

        def _bool(v) -> bool:
            return _BOOL.get(v, bool(v))

        try:
            data = json.loads(Path(path).read_text(encoding='utf-8'))
        except Exception:
            return self

        folders = data.get('Folders', {})
        if 'Export' in folders:
            self.exportFolder = str(folders['Export'])

        ocr = data.get('OCR', {})
        self.ocrCachePath = default_ocr_cache_path(self.exportFolder)
        if 'OcrCachePath' in ocr:
            self.ocrCachePath = str(ocr['OcrCachePath'])

        update = data.get('Update', {})
        if 'CheckUpdateAtStartUp' in update:
            self.checkUpdateAtStartUp = _bool(update['CheckUpdateAtStartUp'])
        if 'DataSource' in update:
            self.dataSource = str(update['DataSource'])

        in_game = data.get('InGame', {})
        if 'Language' in in_game:
            self.gameLanguage = str(in_game['Language'])
        if 'InventoryKeybind' in in_game:
            self.inventoryKeybind = str(in_game['InventoryKeybind'])
        if 'ResonatorKeybind' in in_game:
            self.resonatorKeybind = str(in_game['ResonatorKeybind'])
        if 'RoverName' in in_game:
            self.roverName = str(in_game['RoverName'])

        scanner = data.get('Scanner', {})
        _flag_map = {
            'ScanCharacters':   'scanCharacters',
            'ScanWeapons':      'scanWeapons',
            'ScanEchoes':       'scanEchoes',
            'ScanDevItems':     'scanDevItems',
            'ScanResources':    'scanResources',
            'scanAchievements': 'scanAchievements',
        }
        for json_key, attr in _flag_map.items():
            if json_key in scanner:
                setattr(self, attr, _bool(scanner[json_key]))
        if 'EchoMinRarity' in scanner:
            self.echoMinRarity = int(scanner['EchoMinRarity'])
        if 'EchoMinLevel' in scanner:
            self.echoMinLevel = int(scanner['EchoMinLevel'])
        if 'WeaponsMinRarity' in scanner:
            self.weaponsMinRarity = int(scanner['WeaponsMinRarity'])
        if 'WeaponsMinLevel' in scanner:
            self.weaponsMinLevel = int(scanner['WeaponsMinLevel'])

        advanced = data.get('Advanced', {})
        _valid_levels = {'DEBUG', 'INFO', 'WARNING', 'ERROR'}
        if 'LogLevel' in advanced and str(advanced['LogLevel']).upper() in _valid_levels:
            self.logLevel = str(advanced['LogLevel']).upper()
        if 'SaveRaw' in advanced:
            self.saveRaw = _bool(advanced['SaveRaw'])
        if 'WriteDebug' in advanced:
            self.writeDebug = _bool(advanced['WriteDebug'])
        if 'WindowedMode' in advanced:
            self.windowed = _bool(advanced['WindowedMode'])

        return self

    def sync_from_qconfig(self, qcfg: object) -> None:
        """Pull values from a ``QConfig`` instance (Qt config layer).

        Parameters
        ----------
        qcfg:
            A ``properties.config.Config`` instance (has ``get`` and
            attribute-style ``ConfigItem`` access).
        """
        get = getattr(qcfg, 'get', None)
        if get is None:
            return

        qcfg_obj: Any = cast(Any, qcfg)

        self.exportFolder         = get(qcfg_obj.exportFolder)
        self.gameLanguage         = get(qcfg_obj.gameLanguage)
        self.inventoryKeybind     = get(qcfg_obj.inventoryKeybind)
        self.resonatorKeybind     = get(qcfg_obj.resonatorKeybind)
        self.roverName            = get(qcfg_obj.roverName)
        self.checkUpdateAtStartUp = get(qcfg_obj.checkUpdateAtStartUp)
        self.dataSource           = get(qcfg_obj.dataSource)

        self.echoMinRarity    = qcfg_obj.echoMinRarity.value
        self.echoMinLevel     = qcfg_obj.echoMinLevel.value
        self.weaponsMinRarity = qcfg_obj.weaponsMinRarity.value
        self.weaponsMinLevel  = qcfg_obj.weaponsMinLevel.value

        self.scanCharacters  = qcfg_obj.scanCharacters.value
        self.scanWeapons     = qcfg_obj.scanWeapons.value
        self.scanEchoes      = qcfg_obj.scanEchoes.value
        self.scanDevItems    = qcfg_obj.scanDevItems.value
        self.scanResources   = qcfg_obj.scanResources.value
        self.scanAchievements = qcfg_obj.scanAchievements.value

        self.ocrCachePath = get(qcfg_obj.ocrCachePath)
        self.logLevel  = get(qcfg_obj.logLevel)
        self.saveRaw   = get(qcfg_obj.saveRaw)
        self.writeDebug = get(qcfg_obj.writeDebug)
        self.windowed  = get(qcfg_obj.windowed)


app_config: AppConfig = AppConfig().load()
