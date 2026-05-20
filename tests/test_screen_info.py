from __future__ import annotations

import pytest

from wuwa_inventory_kamera.game.game_roi import COORDINATES
from wuwa_inventory_kamera.game.screen_info import ScreenInfo


@pytest.mark.parametrize(
    ('resolution', 'reference_resolution', 'ratio'),
    [
        ((2560, 1440), (1920, 1080), (16, 9)),
        ((2560, 1600), (1920, 1200), (8, 5)),
    ],
)
def test_screen_info_scales_matching_aspect_ratios(resolution, reference_resolution, ratio):
    screen_info = ScreenInfo(*resolution)
    reference = COORDINATES[ratio][reference_resolution]
    width_scale = resolution[0] / reference_resolution[0]
    height_scale = resolution[1] / reference_resolution[1]

    assert screen_info.echoes.echoCard.x == pytest.approx(reference['echoes']['echoCard'].x * width_scale)
    assert screen_info.echoes.echoCard.y == pytest.approx(reference['echoes']['echoCard'].y * height_scale)
    assert screen_info.echoes.echoCard.w == pytest.approx(reference['echoes']['echoCard'].w * width_scale)
    assert screen_info.echoes.echoCard.h == pytest.approx(reference['echoes']['echoCard'].h * height_scale)
    assert screen_info.echoes.sonataIcon.radius == pytest.approx(reference['echoes']['sonataIcon']['radius'] * width_scale)


def test_screen_info_preserves_scroll_deltas_when_scaling():
    screen_info = ScreenInfo(2560, 1440)
    reference = COORDINATES[(16, 9)][(1920, 1080)]['scroll']

    assert screen_info.scroll.page.y == reference['page'].y
    assert screen_info.scroll.characters.y == reference['characters'].y
    assert screen_info.scroll.sonata.y == reference['sonata'].y


def test_screen_info_rejects_unknown_aspect_ratios():
    with pytest.raises(ValueError, match='Unsupported WuWa resolution 3440x1440'):
        ScreenInfo(3440, 1440)