"""
scraping.utils.common -- re-export shim.

The canonical implementation lives in `wuwa_inventory_kamera.scraping.utils.common`.
"""
from wuwa_inventory_kamera.scraping.utils.common import (  # noqa: F401
    savingScraped,
    screenshot,
    convertToBlackWhite,
    darken_background_preserve_edges_ndarray,
    copyToClipboard,
    isUserAdmin,
    saveRawScan,
    loadRawScans,
    definedText,
    LEVEL_TRACE,
    _trace,
    _logger,
)
# Re-export data symbols that legacy code expects from scraping.utils.common
from wuwa_inventory_kamera.scraping.data import itemsID  # noqa: F401
