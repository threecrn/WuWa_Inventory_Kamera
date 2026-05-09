from __future__ import annotations

import numpy as np

from wuwa_inventory_kamera.scraping.scanning.echo_workflow import (
    _RARITY_PIXEL_COLORS_BGR,
    _rarity_from_capture_pixel,
    _rarity_from_bgr_pixel,
    _rarity_from_rgb_pixel,
)


def test_rarity_helpers_match_reference_palette() -> None:
    for rarity, bgr in _RARITY_PIXEL_COLORS_BGR.items():
        rgb = np.asarray(bgr[::-1], dtype=np.uint8)

        assert _rarity_from_bgr_pixel(bgr) == rarity
        assert _rarity_from_rgb_pixel(rgb) == rarity


def test_capture_rarity_helper_prefers_bgr_for_live_gold_pixel() -> None:
    rarity, channel_order, dist = _rarity_from_capture_pixel(
        np.asarray([175, 247, 252], dtype=np.uint8)
    )

    assert rarity == 5
    assert channel_order == 'BGR'
    assert dist < 100.0


def test_capture_rarity_helper_recovers_rgb_ordered_gold_pixel() -> None:
    rarity, channel_order, dist = _rarity_from_capture_pixel(
        np.asarray([252, 247, 175], dtype=np.uint8)
    )

    assert rarity == 5
    assert channel_order == 'RGB'
    assert dist < 100.0