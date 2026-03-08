"""properties.app_config
~~~~~~~~~~~~~~~~~~~~~

Plain-Python configuration singleton — no Qt, no qfluentwidgets required.

All scraping, processing, and CLI code should import configuration values
from here.  The Qt UI layers (``properties.config``) wrap these values with
QConfig/qfluentwidgets bindings and call :meth:`AppConfig.sync_from_qconfig`
after loading so the two stay in sync.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Process-level constants
# ---------------------------------------------------------------------------

basePATH: Path = Path(sys.executable if getattr(sys, 'frozen', False) else str()).parent
PROCESS_NAME: str = 'Client-Win64-Shipping.exe'
WINDOW_NAME: str = 'Wuthering Waves'

# ---------------------------------------------------------------------------
# Mutable scan-session state  (module-level singletons, one per process)
# ---------------------------------------------------------------------------

INVENTORY: dict = {'date': '', 'items': {}}
FAILED: list = []

# ---------------------------------------------------------------------------
# Plain-Python config class
# ---------------------------------------------------------------------------

class AppConfig:
    """
    Mirrors every field in the QConfig ``Config`` class without Qt bindings.

    Defaults match those defined in ``properties.config.Config``.
    Call :meth:`load` to populate from ``config/config.json``.
    Call :meth:`sync_from_qconfig` to pull values from a live QConfig
    instance (done automatically by ``properties.config`` on startup and on
    every save).
    """

    # --- folder ---
    exportFolder: str = 'export'

    # --- update ---
    checkUpdateAtStartUp: bool = True

    # --- in-game ---
    gameLanguage: str = 'English'
    inventoryKeybind: str = 'B'
    resonatorKeybind: str = 'C'
    roverName: str = 'Rover'

    # --- scan toggles ---
    scanCharacters: bool = False
    scanWeapons: bool = False
    scanEchoes: bool = False
    scanDevItems: bool = False
    scanResources: bool = False
    scanAchievements: bool = False

    # --- scan thresholds ---
    echoMinRarity: int = 1
    echoMinLevel: int = 0
    weaponsMinRarity: int = 1
    weaponsMinLevel: int = 1

    def load(self, path: str | Path = 'config/config.json') -> 'AppConfig':
        """
        Populate fields from *path* (QConfig JSON format).

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

        update = data.get('Update', {})
        if 'CheckUpdateAtStartUp' in update:
            self.checkUpdateAtStartUp = _bool(update['CheckUpdateAtStartUp'])

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

        return self

    def sync_from_qconfig(self, qcfg) -> None:
        """
        Copy all values from a live ``properties.config.Config`` (QConfig) instance.

        Called by ``properties.config`` immediately after ``qconfig.load(...)``
        and whenever the user saves settings, so this plain-Python singleton stays
        current with the Qt in-memory state.
        """
        self.exportFolder         = qcfg.get(qcfg.exportFolder)
        self.checkUpdateAtStartUp = qcfg.get(qcfg.checkUpdateAtStartUp)
        self.gameLanguage         = qcfg.get(qcfg.gameLanguage)
        self.inventoryKeybind     = qcfg.get(qcfg.inventoryKeybind)
        self.resonatorKeybind     = qcfg.get(qcfg.resonatorKeybind)
        self.roverName            = qcfg.get(qcfg.roverName)
        self.scanCharacters       = qcfg.get(qcfg.scanCharacters)
        self.scanWeapons          = qcfg.get(qcfg.scanWeapons)
        self.scanEchoes           = qcfg.get(qcfg.scanEchoes)
        self.scanDevItems         = qcfg.get(qcfg.scanDevItems)
        self.scanResources        = qcfg.get(qcfg.scanResources)
        self.scanAchievements     = qcfg.get(qcfg.scanAchievements)
        self.echoMinRarity        = qcfg.get(qcfg.echoMinRarity)
        self.echoMinLevel         = qcfg.get(qcfg.echoMinLevel)
        self.weaponsMinRarity     = qcfg.get(qcfg.weaponsMinRarity)
        self.weaponsMinLevel      = qcfg.get(qcfg.weaponsMinLevel)


# ---------------------------------------------------------------------------
# Global singleton — populated at import time from disk
# ---------------------------------------------------------------------------

app_config: AppConfig = AppConfig().load()
