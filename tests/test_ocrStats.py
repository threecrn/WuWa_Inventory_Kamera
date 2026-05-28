import numpy as np
from PIL import Image

from wuwa_inventory_kamera.game.screen_info import ScreenInfo
from wuwa_inventory_kamera.scraping.processing.echoes_processor import _extractStats
from wuwa_inventory_kamera.scraping.processing.stats_extractor import StatsExtractor


class _FakeStatsExtractor(StatsExtractor):
    def __init__(self, names: list[str], values: list[str], trace: dict) -> None:
        super().__init__()
        self._names = names
        self._values = values
        self._trace = trace
        self.calls: list[tuple[tuple[int, int], tuple[int, int], int]] = []

    def _ocr_and_pair(
        self,
        name_crop: np.ndarray,
        value_crop: np.ndarray,
        scan_index: int,
    ) -> tuple[list[str], list[str], dict]:
        self.calls.append((name_crop.shape[:2], value_crop.shape[:2], scan_index))
        return self._names, self._values, self._trace


def test_extract_stats_assembles_main_and_sub_stats_from_extractor_output() -> None:
    screen_info = ScreenInfo(1920, 1080)
    full_image = np.zeros((1080, 1920, 3), dtype=np.uint8)
    trace = {
        'raw_names_ocr': ['critdmg', 'atk', 'atk', 'critdmg', 'hp', 'critrate', 'hp'],
        'matched_names': ['critdmg', 'atk', 'atk', 'critdmg', 'hp', 'critrate', 'hp'],
        'raw_values_ocr': ['44.0%', '150', '10.9%', '13.8%', '360', '6.9%', '7.1%'],
    }
    extractor = _FakeStatsExtractor(
        ['critdmg', 'atk', 'atk', 'critdmg', 'hp', 'critrate', 'hp'],
        ['44.0%', '150', '10.9%', '13.8%', '360', '6.9%', '7.1%'],
        trace,
    )

    tune_level, stats, returned_trace = _extractStats(
        full_image,
        screen_info,
        {},
        scan_index=7,
        extractor=extractor,
    )

    assert tune_level == 5
    assert stats == {
        'main': {'cd%': 44.0, 'atk': 150},
        'sub': {'atk%': 10.9, 'cd%': 13.8, 'hp': 360, 'cr%': 6.9, 'hp%': 7.1},
    }
    assert returned_trace == trace
    assert extractor.calls == [
        (
            (
                int(screen_info.echoes.fullStatsName.h),
                int(screen_info.echoes.fullStatsName.w),
            ),
            (
                int(screen_info.echoes.fullStatsValue.h),
                int(screen_info.echoes.fullStatsValue.w),
            ),
            7,
        )
    ]

    assert list(stats['main'].items()) == [('cd%', 44.0), ('atk', 150)]
    assert list(stats['sub'].items()) == [
        ('atk%', 10.9),
        ('cd%', 13.8),
        ('hp', 360),
        ('cr%', 6.9),
        ('hp%', 7.1),
    ]


def test_extract_stats_real_fixture_returns_traceable_fast_pass() -> None:
    full_image = np.array(Image.open('tests/data/echo_0001_full.png'))

    tune_level, stats, trace = _extractStats(
        full_image,
        ScreenInfo(1920, 1080),
        {},
        scan_index=0,
    )

    assert tune_level >= 0
    assert set(trace) == {'raw_names_ocr', 'matched_names', 'raw_values_ocr'}
    assert trace['matched_names']
    assert trace['raw_values_ocr']
    assert sum(len(bucket) for bucket in stats.values()) >= 3
