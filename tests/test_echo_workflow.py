from __future__ import annotations

import numpy as np

from wuwa_inventory_kamera.scraping.scanning.echo_workflow import (
    _RARITY_PIXEL_COLORS_BGR,
    _rarity_from_bgr_pixel,
    _rarity_from_rgb_pixel,
)


def test_rarity_helpers_match_reference_palette() -> None:
    for rarity, bgr in _RARITY_PIXEL_COLORS_BGR.items():
        rgb = np.asarray(bgr[::-1], dtype=np.uint8)

        assert _rarity_from_bgr_pixel(bgr) == rarity
        assert _rarity_from_rgb_pixel(rgb) == rarity