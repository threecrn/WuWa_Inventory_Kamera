"""
properties.app_config -- re-export shim.

The canonical implementation lives in `wuwa_inventory_kamera.config.app_config`.
"""
from wuwa_inventory_kamera.config.app_config import (  # noqa: F401
    basePATH,
    PROCESS_NAME,
    WINDOW_NAME,
    INVENTORY,
    FAILED,
    AppConfig,
    app_config,
)
