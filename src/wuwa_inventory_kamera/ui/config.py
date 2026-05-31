"""
wuwa_inventory_kamera.ui.config
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Qt / qfluentwidgets configuration layer.

This module wraps ``QConfig`` from PySide6-Fluent-Widgets and syncs values
into the Qt-free :data:`~wuwa_inventory_kamera.config.app_config.app_config`
singleton so that all non-UI code can read settings without a Qt dependency.

Usage::

    from wuwa_inventory_kamera.ui.config import cfg, basePATH
"""
from __future__ import annotations

import json
import string
from pathlib import Path
from typing import Any

from qfluentwidgets import (
    qconfig, QConfig, ConfigValidator,
    ConfigItem, OptionsConfigItem, BoolValidator,
    FolderValidator, OptionsValidator, RangeValidator,
    Signal, Theme, EnumSerializer,
)

from ..config.app_config import (
    app_config,
    basePATH,
    default_ocr_cache_path,
    PROCESS_NAME, WINDOW_NAME,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

maxLength = 12

try:
    LANGUAGES: dict[str, str] = json.load(
        open(basePATH / 'data' / 'languages.json', 'r', encoding='utf-8')
    )
except Exception:
    LANGUAGES = {'English': 'en'}

HELP_URL = "https://discord.gg/y6b2kMqs"
FEEDBACK_URL = "https://github.com/Psycho-Marcus/WuWa_Inventory_Kamera/issues"
RELEASE_URL = "https://github.com/Psycho-Marcus/WuWa_Inventory_Kamera/releases/latest"


def alphabethList() -> list[str]:
    """Generate a list of uppercase letters, digits, and punctuation."""
    return list(string.ascii_uppercase + string.digits + string.punctuation)


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------

class TextValidator(ConfigValidator):
    """Text validator with optional length constraint."""

    def __init__(self, max_length: int | None = None):
        if max_length is not None and max_length <= 0:
            raise ValueError("The `max_length` must be a positive integer.")
        self.max_length = max_length

    def validate(self, value: str) -> Any:
        if not value:
            return False
        if self.max_length is not None and len(value) > self.max_length:
            return False
        return True

    def correct(self, value: str) -> str:
        if not value:
            value = 'Rover'
        if self.max_length is not None:
            value = value[:self.max_length]
        return value


class PathValidator(ConfigValidator):
    """Validator for non-empty filesystem paths."""

    def __init__(self, default_path: str):
        self.default_path = default_path

    def validate(self, value: str) -> Any:
        return isinstance(value, str) and bool(value.strip())

    def correct(self, value: str) -> str:
        return value.strip() or self.default_path


# ---------------------------------------------------------------------------
# Config class
# ---------------------------------------------------------------------------

class Config(QConfig):
    """Application Qt configuration."""

    configChanged = Signal()
    themeMode = OptionsConfigItem(
        'QFluentWidgets', 'ThemeMode', Theme.DARK,
        OptionsValidator(Theme), EnumSerializer(Theme),
    )

    def save(self):
        super().save()
        self.configChanged.emit()

    # Personalization
    exportFolder = ConfigItem("Folders", "Export", "export", FolderValidator())
    checkUpdateAtStartUp = ConfigItem("Update", "CheckUpdateAtStartUp", True, BoolValidator())
    dataSource = OptionsConfigItem(
        'Update', 'DataSource', 'Arikatsu',
        OptionsValidator(['Arikatsu', 'Dimbreath']),
    )

    # In-Game
    gameLanguage = OptionsConfigItem('InGame', 'Language', 'English', OptionsValidator(list(LANGUAGES)))
    inventoryKeybind = OptionsConfigItem('InGame', 'InventoryKeybind', 'B', OptionsValidator(alphabethList()))
    resonatorKeybind = OptionsConfigItem('InGame', 'ResonatorKeybind', 'C', OptionsValidator(alphabethList()))
    roverName = ConfigItem('InGame', 'RoverName', 'Rover', TextValidator(max_length=maxLength))

    # Scanner toggles
    scanCharacters = ConfigItem("Scanner", "ScanCharacters", False, BoolValidator())
    scanWeapons = ConfigItem("Scanner", "ScanWeapons", False, BoolValidator())
    scanEchoes = ConfigItem("Scanner", "ScanEchoes", False, BoolValidator())
    scanDevItems = ConfigItem("Scanner", "ScanDevItems", False, BoolValidator())
    scanResources = ConfigItem("Scanner", "ScanResources", False, BoolValidator())
    scanAchievements = ConfigItem("Scanner", "scanAchievements", False, BoolValidator())
    showScanStartDialog = ConfigItem("Scanner", "ShowScanStartDialog", True, BoolValidator())

    # Scanner thresholds
    echoMinRarity = ConfigItem("Scanner", "EchoMinRarity", 1, RangeValidator(1, 5))
    echoMinLevel = ConfigItem("Scanner", "EchoMinLevel", 0, RangeValidator(0, 25))
    weaponsMinRarity = ConfigItem("Scanner", "WeaponsMinRarity", 1, RangeValidator(1, 5))
    weaponsMinLevel = ConfigItem("Scanner", "WeaponsMinLevel", 0, RangeValidator(0, 90))

    # OCR backend
    ocrBackend = OptionsConfigItem(
        "OCR", "Backend", "DML+CPU",
        OptionsValidator(["DML+CPU", "CPU only"]),
    )
    ocrBatchSize = ConfigItem("OCR", "BatchSize", 8, RangeValidator(1, 64))
    ocrCachePath = ConfigItem(
        'OCR', 'OcrCachePath', default_ocr_cache_path(),
        PathValidator(default_ocr_cache_path()),
    )

    # Display mode
    gameFullscreen = ConfigItem("Advanced", "GameFullscreenMode", False, BoolValidator())
    # Legacy key retained for backward compatibility with old config files.
    windowed = ConfigItem("Advanced", "WindowedMode", False, BoolValidator())

    # Advanced / debug
    logLevel = OptionsConfigItem(
        "Advanced", "LogLevel", "INFO",
        OptionsValidator(["DEBUG", "INFO", "WARNING", "ERROR"]),
    )
    saveRaw = ConfigItem("Advanced", "SaveRaw", False, BoolValidator())
    writeDebug = ConfigItem("Advanced", "WriteDebug", False, BoolValidator())


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

cfg = Config()
qconfig.load('config/config.json', cfg)
app_config.sync_from_qconfig(cfg)
cfg.configChanged.connect(lambda: app_config.sync_from_qconfig(cfg))
