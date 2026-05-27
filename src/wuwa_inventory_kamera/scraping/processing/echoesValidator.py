"""
wuwa_inventory_kamera.scraping.processing.echoesValidator
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Compatibility wrapper for historical echo validation imports.

The canonical implementation lives in
``wuwa_inventory_kamera.scraping.service.echo_validation``.
"""

from __future__ import annotations

from ..service.echo_validation import (
    ValidationResult,
    expected_sub_count,
    infer_cost,
    validate_echo_stats,
)

__all__ = [
    'ValidationResult',
    'expected_sub_count',
    'infer_cost',
    'validate_echo_stats',
]
