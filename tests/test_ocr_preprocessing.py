from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from wuwa_inventory_kamera.scraping.ocr.region_specs import OcrRegionSpec, load_specs_from_toml
from wuwa_inventory_kamera.scraping.service.ocr_cache import OcrCache


def _gray_from_rgb(image: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)


def _bgr_from_hsv(h: int, s: int, v: int) -> tuple[int, int, int]:
    pixel = np.array([[[h, s, v]]], dtype=np.uint8)
    converted = cv2.cvtColor(pixel, cv2.COLOR_HSV2BGR)[0, 0]
    return tuple(int(value) for value in converted.tolist())


def _bbox() -> np.ndarray:
    return np.asarray([[0, 0], [5, 0], [5, 1], [0, 1]], dtype=np.float32)


def test_preprocess_prefers_rarity_override_over_fallback_ranges() -> None:
    spec = OcrRegionSpec(
        roi_key="echoes.echoName",
        color_space="bgr",
        text_color_ranges=[((20, 60, 150), (32, 255, 255))],
        text_color_ranges_by_rarity={5: [((5, 5, 250), (5, 5, 250))]},
        fallback_color_space="hsv",
    )
    image = np.zeros((6, 6, 3), dtype=np.uint8)
    image[2, 2] = np.asarray([5, 5, 250], dtype=np.uint8)
    image[2, 3] = np.asarray(_bgr_from_hsv(26, 220, 220), dtype=np.uint8)

    processed = _gray_from_rgb(spec.preprocess(image, rarity=5))

    assert processed[2, 2] == 255
    assert processed[2, 3] == 0


def test_preprocess_uses_fallback_color_space_when_rarity_missing() -> None:
    spec = OcrRegionSpec(
        roi_key="echoes.echoName",
        color_space="bgr",
        text_color_ranges=[((20, 60, 150), (32, 255, 255))],
        text_color_ranges_by_rarity={5: [((5, 5, 250), (5, 5, 250))]},
        fallback_color_space="hsv",
    )
    image = np.zeros((6, 6, 3), dtype=np.uint8)
    image[2, 2] = np.asarray([5, 5, 250], dtype=np.uint8)
    image[2, 3] = np.asarray(_bgr_from_hsv(26, 220, 220), dtype=np.uint8)

    processed = _gray_from_rgb(spec.preprocess(image, rarity=None))

    assert processed[2, 2] == 0
    assert processed[2, 3] == 255


def test_background_suppression_happens_before_threshold() -> None:
    spec = OcrRegionSpec(
        roi_key="characters.resonatorName",
        color_space="hsv",
        background_color_ranges=[((100, 40, 40), (125, 255, 255))],
        threshold_mode="floor",
        floor_value=120,
    )
    image = np.full((12, 12, 3), _bgr_from_hsv(110, 220, 240), dtype=np.uint8)
    image[3:9, 5:7] = np.asarray([245, 245, 245], dtype=np.uint8)

    processed = _gray_from_rgb(spec.preprocess(image))

    assert processed[1, 1] == 0
    assert processed[5, 5] > 0


def test_morphology_close_bridges_small_gap() -> None:
    spec = OcrRegionSpec(
        roi_key="echoes.fullStatsValue",
        threshold_mode="floor",
        floor_value=200,
        morphology="close",
    )
    image = np.zeros((7, 7, 3), dtype=np.uint8)
    image[2:5, 2] = 255
    image[2:5, 4] = 255

    processed = _gray_from_rgb(spec.preprocess(image))

    assert processed[3, 3] > 0


def test_single_line_repair_bridges_tiny_horizontal_hole() -> None:
    spec = OcrRegionSpec(
        roi_key="echoes.echoName",
        color_space="bgr",
        text_color_ranges=[((255, 255, 255), (255, 255, 255))],
        single_line=True,
    )
    image = np.zeros((6, 10, 3), dtype=np.uint8)
    image[3, 2] = np.asarray([255, 255, 255], dtype=np.uint8)
    image[3, 4] = np.asarray([255, 255, 255], dtype=np.uint8)

    processed = _gray_from_rgb(spec.preprocess(image))

    assert processed[3, 3] > 0


def test_invert_flips_thresholded_foreground_and_background() -> None:
    spec = OcrRegionSpec(
        roi_key="echoes.level",
        threshold_mode="floor",
        floor_value=200,
        invert=True,
    )
    image = np.zeros((5, 5, 3), dtype=np.uint8)
    image[2, 2] = np.asarray([255, 255, 255], dtype=np.uint8)

    processed = _gray_from_rgb(spec.preprocess(image))

    assert processed[2, 2] == 0
    assert processed[0, 0] == 255


def test_signature_ignores_background_when_signing_preprocessed_image() -> None:
    spec = OcrRegionSpec(
        roi_key="echoes.echoName",
        color_space="bgr",
        text_color_ranges=[((5, 5, 250), (5, 5, 250))],
        sig_from_preprocessed=True,
    )
    image_a = np.full((16, 16, 3), (30, 40, 60), dtype=np.uint8)
    image_b = np.full((16, 16, 3), (90, 140, 180), dtype=np.uint8)
    image_a[4:12, 5:11] = np.asarray([5, 5, 250], dtype=np.uint8)
    image_b[4:12, 5:11] = np.asarray([5, 5, 250], dtype=np.uint8)

    assert spec.make_signature(image_a) == spec.make_signature(image_b)


def test_signature_stable_across_minor_shift() -> None:
    spec = OcrRegionSpec(
        roi_key="echoes.fullStatsValue",
        threshold_mode="floor",
        floor_value=200,
        sig_from_preprocessed=True,
        sig_downscale=(8, 8),
    )
    base = np.zeros((128, 128, 3), dtype=np.uint8)
    shifted = np.zeros((128, 128, 3), dtype=np.uint8)
    base[48:80, 48:80] = 255
    shifted[48:80, 49:81] = 255

    assert spec.make_signature(base) == spec.make_signature(shifted)


def test_load_specs_from_toml_parses_rarity_and_fallback_color_space(tmp_path: Path) -> None:
    config_path = tmp_path / "ocr_region_specs.toml"
    config_path.write_text(
        "\n".join(
            [
                'spec_version = "test-spec"',
                '',
                '[echoes.echoName]',
                'color_space = "bgr"',
                'sig_from_preprocessed = true',
                'single_line = true',
                '',
                '[echoes.echoName.rarity_overrides."5"]',
                'text_color_ranges = [',
                '    [[5, 5, 250], [5, 5, 250]],',
                ']',
                '',
                '[echoes.echoName.fallback]',
                'color_space = "hsv"',
                'text_color_ranges = [',
                '    [[20, 60, 150], [32, 255, 255]],',
                ']',
                '',
                '[echoes.fullStatsValue]',
                'threshold_mode = "floor"',
                'floor_value = 100',
                'sig_downscale = [32, 16]',
            ]
        ),
        encoding="utf-8",
    )

    specs = load_specs_from_toml(str(config_path))

    echo_name = specs["echoes.echoName"]
    assert echo_name.spec_version == "test-spec"
    assert echo_name.color_space == "bgr"
    assert echo_name.single_line is True
    assert echo_name.fallback_color_space == "hsv"
    assert echo_name.text_color_ranges == [((20, 60, 150), (32, 255, 255))]
    assert echo_name.text_color_ranges_by_rarity == {5: [((5, 5, 250), (5, 5, 250))]}

    stats_value = specs["echoes.fullStatsValue"]
    assert stats_value.threshold_mode == "floor"
    assert stats_value.floor_value == 100
    assert stats_value.sig_downscale == (32, 16)


def test_ocr_cache_round_trip_with_region_spec(tmp_path: Path) -> None:
    spec = OcrRegionSpec(
        roi_key="echoes.fullStatsValue",
        threshold_mode="floor",
        floor_value=100,
        cache_mode="persistent",
        spec_version="cache-test",
    )
    image = np.zeros((16, 16, 3), dtype=np.uint8)
    image[4:12, 4:12] = 255
    expected = [("ATK", 0.95, _bbox())]

    cache = OcrCache(tmp_path / "ocr-cache.sqlite3")
    try:
        cache.store(spec, image, expected)
        cached = cache.lookup(spec, image)
    finally:
        cache.close()

    assert cached is not None
    assert cached[0][0] == "ATK"
    assert cached[0][1] == 0.95
    np.testing.assert_array_equal(cached[0][2], expected[0][2])