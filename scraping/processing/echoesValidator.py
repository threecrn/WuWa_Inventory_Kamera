"""
scraping.processing.echoesValidator -- re-export shim.

The canonical implementation lives in `wuwa_inventory_kamera.scraping.processing.echoesValidator`.
"""
from wuwa_inventory_kamera.scraping.processing.echoesValidator import (  # noqa: F401
    ValidationResult,
    validate_echo_stats,
    infer_cost,
    expected_sub_count,
)
