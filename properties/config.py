"""
properties.config — re-export shim.

The canonical implementation lives in ``wuwa_inventory_kamera.ui.config``.
"""
from wuwa_inventory_kamera.ui.config import (  # noqa: F401
    cfg, Config,
    TextValidator,
    alphabethList, maxLength,
    HELP_URL, FEEDBACK_URL, RELEASE_URL,
    LANGUAGES,
)

from wuwa_inventory_kamera.config.app_config import (  # noqa: F401
    basePATH,
    PROCESS_NAME, WINDOW_NAME,
    INVENTORY, FAILED,
)