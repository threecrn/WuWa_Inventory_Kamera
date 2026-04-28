"""
scraping.processing.statsExtractor -- re-export shim.

The canonical implementation lives in `wuwa_inventory_kamera.scraping.processing.stats_extractor`.
"""
from wuwa_inventory_kamera.scraping.processing.stats_extractor import (  # noqa: F401
    StatsExtractor,
    RapidOcrStatsExtractor,
    RapidOcrCoordStatsExtractor,
    TesserOcrStatsExtractor,
    TesserOcrCoordStatsExtractor,
)
