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

from pathlib import Path

# ---------------------------------------------------------------------------
# Project root
# ---------------------------------------------------------------------------

#: Repository / install root — resolates to the directory containing
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

# ---------------------------------------------------------------------------
# AppConfig singleton
# ---------------------------------------------------------------------------

class AppConfig:
    """Qt-free configuration object.

    Attributes are set to sensible defaults and can be overridden at runtime
    by the Qt config layer via :meth:`sync_from_qconfig`, or directly by CLI
    tools.
    """

    def __init__(self) -> None:
        self.exportFolder: str = 'export'
        self.gameLanguage: str = 'English'
        self.inventoryKeybind: str = 'B'
        self.resonatorKeybind: str = 'C'
        self.roverName: str = 'Rover'
        self.checkUpdateAtStartUp: bool = True

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

        self.exportFolder = get(qcfg.exportFolder)
        self.gameLanguage = get(qcfg.gameLanguage)
        self.inventoryKeybind = get(qcfg.inventoryKeybind)
        self.resonatorKeybind = get(qcfg.resonatorKeybind)
        self.roverName = get(qcfg.roverName)
        self.checkUpdateAtStartUp = get(qcfg.checkUpdateAtStartUp)

        self.echoMinRarity = qcfg.echoMinRarity.value
        self.echoMinLevel = qcfg.echoMinLevel.value
        self.weaponsMinRarity = qcfg.weaponsMinRarity.value
        self.weaponsMinLevel = qcfg.weaponsMinLevel.value

        self.scanCharacters = qcfg.scanCharacters.value
        self.scanWeapons = qcfg.scanWeapons.value
        self.scanEchoes = qcfg.scanEchoes.value
        self.scanDevItems = qcfg.scanDevItems.value
        self.scanResources = qcfg.scanResources.value
        self.scanAchievements = qcfg.scanAchievements.value


app_config = AppConfig()
