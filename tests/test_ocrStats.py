import pytest

from scraping.processing.echoesProcessor import _extractStats
from game.screenInfo import ScreenInfo
import numpy as np
from PIL import Image
import logging

class TestExtractStats:
    def test_extract_stats(self):

        # load test cases from test_ocrStats.yaml
        with open('tests/data/test_ocrStats.yaml', 'r') as f:
            import yaml
            test_cases = yaml.safe_load(f)['test_data']['test_extract_stats']['test_cases']

        monitor_resolution = (1920, 1080)

        # Run the test cases
        for case in test_cases:
            im_frame = Image.open("tests/data/" + case['input']['full_image'])
            full_image: np.ndarray = np.array(im_frame)
            screenInfo:ScreenInfo = ScreenInfo(*monitor_resolution)
            cache = {}
            scan_index:int = 0
            (tuneLevel, stats, _) = _extractStats(full_image, screenInfo, cache, scan_index)
            logging.debug(f"Extracted stats for {case['input']['full_image']}: {tuneLevel=}, {stats=}")
            assert tuneLevel == case['expected']['tuneLevel']
            assert stats == case['expected']['stats']
