from __future__ import annotations

from types import SimpleNamespace

import numpy as np

import wuwa_inventory_kamera.scraping.scanning.echo_workflow as echo_workflow_module
from wuwa_inventory_kamera.scraping.scanning.echo_workflow import (
    EchoWorkflow,
    _RARITY_PIXEL_COLORS_BGR,
    _rarity_from_capture_pixel,
    _rarity_from_bgr_pixel,
    _rarity_from_rgb_pixel,
)
from wuwa_inventory_kamera.scraping.service.echo_capture_utils import decide_echo_level


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


def test_decide_echo_level_uses_parsed_digits_for_slot_selection() -> None:
    decision = decide_echo_level(level_text='25.')

    assert decision.detected_level == 25
    assert decision.two_digits is True
    assert decision.level_text == '25.'


def test_capture_echo_reuses_prefetched_level_without_second_ocr(monkeypatch) -> None:
    image = np.arange(6 * 6 * 3, dtype=np.uint8).reshape(6, 6, 3)

    monkeypatch.setattr(echo_workflow_module, 'capture_full', lambda *args, **kwargs: image)

    layout = SimpleNamespace(
        width=6,
        height=6,
        monitor=1,
        echoes=SimpleNamespace(
            rarityColorPick=SimpleNamespace(x=0, y=0),
            echoCard=SimpleNamespace(x=0, y=0, w=2, h=2),
            fullStatsName=SimpleNamespace(x=2, y=0, w=2, h=2),
            fullStatsValue=SimpleNamespace(x=0, y=2, w=2, h=2),
            echoName=SimpleNamespace(x=2, y=2, w=2, h=2),
            level=SimpleNamespace(x=4, y=0, w=2, h=2),
            sonataIcon=SimpleNamespace(
                radius=1.0,
                level_X=SimpleNamespace(
                    circle=SimpleNamespace(x=1.0, y=1.0),
                    icon=SimpleNamespace(x=0, y=4, w=2, h=2),
                ),
                level_XX=SimpleNamespace(
                    circle=SimpleNamespace(x=1.0, y=1.0),
                    icon=SimpleNamespace(x=2, y=4, w=2, h=2),
                ),
            ),
        ),
    )

    class _FakeOcrService:
        def __init__(self) -> None:
            self.submitted = []
            self.ocr_calls = 0

        def ocr_adhoc_text(self, _image, _roi_key: str) -> str:
            self.ocr_calls += 1
            return '25'

        def submit(self, capture):
            self.submitted.append(capture)
            return SimpleNamespace(capture=capture)

    ocr = _FakeOcrService()
    workflow = EchoWorkflow(
        nav=SimpleNamespace(layout=layout, gw=None),
        ocr_service=ocr,
        session=SimpleNamespace(),
    )

    workflow._capture_echo(SimpleNamespace(scan_index=7), detected_level=25)

    assert ocr.ocr_calls == 0
    assert ocr.submitted[0].detected_level == 25
    np.testing.assert_array_equal(ocr.submitted[0].sonata_icon, image[4:6, 2:4])