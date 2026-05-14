import pytest

from wuwa_inventory_kamera.scraping.processing.echoes_processor import _extractStats
from wuwa_inventory_kamera.game.screen_info import ScreenInfo
import numpy as np
from PIL import Image
import logging

class TestExtractStats:
    def test_extract_stats(self):
        monitor_resolution = (1920, 1080)

        im_frame = Image.open('tests/data/echo_0001_full.png')
        full_image: np.ndarray = np.array(im_frame)
        screenInfo:ScreenInfo = ScreenInfo(*monitor_resolution)
        cache = {}
        scan_index:int = 0
        (tuneLevel, stats, _) = _extractStats(full_image, screenInfo, cache, scan_index)
        logging.debug(f"Extracted stats: {tuneLevel=}, {stats=}")
        assert tuneLevel == 5
        assert stats == {
            'main': { 'cd%': 44.0, 'atk': 150, },
            'sub': {'atk%': 10.9, 'cd%': 13.8, 'hp': 360, 'cr%': 6.9, 'hp%': 7.1, },
        }



#_extractStats