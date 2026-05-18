from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from wuwa_inventory_kamera.scraping.ocr.region_specs import (
    OcrRegionSpec,
    SignaturePreprocessSpec,
    get_spec,
    load_specs_from_toml,
)
from wuwa_inventory_kamera.scraping.service.ocr_cache import OcrCache


def _gray_from_rgb(image: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)


def _bgr_from_hsv(h: int, s: int, v: int) -> tuple[int, int, int]:
    pixel = np.array([[[h, s, v]]], dtype=np.uint8)
    converted = cv2.cvtColor(pixel, cv2.COLOR_HSV2BGR)[0, 0]
    return int(converted[0]), int(converted[1]), int(converted[2])


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

    processed = _gray_from_rgb(spec.preprocess(image, rarity=5).ocr_rgb)

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

    processed = _gray_from_rgb(spec.preprocess(image, rarity=None).ocr_rgb)

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

    processed = _gray_from_rgb(spec.preprocess(image).ocr_rgb)

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

    processed = _gray_from_rgb(spec.preprocess(image).ocr_rgb)

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

    processed = _gray_from_rgb(spec.preprocess(image).ocr_rgb)

    assert processed[3, 3] > 0


def test_normalized_anchor_contrast_spreads_anchor_range_to_full_scale() -> None:
    spec = OcrRegionSpec(
        roi_key="echoes.level",
        render_mode="normalized_anchor_contrast",
        text_color_ranges=[((180, 220, 220), (180, 220, 220))],
        background_color_ranges=[((120, 80, 80), (120, 80, 80))],
    )
    image = np.zeros((1, 3, 3), dtype=np.uint8)
    image[0, 0] = np.asarray([120, 80, 80], dtype=np.uint8)
    image[0, 1] = np.asarray([150, 150, 150], dtype=np.uint8)
    image[0, 2] = np.asarray([180, 220, 220], dtype=np.uint8)

    processed = spec.preprocess(image).ocr_rgb
    gray = _gray_from_rgb(processed)

    assert gray[0, 0] == 0
    assert gray[0, 1] == 128
    assert gray[0, 2] == 255


def test_packaged_echo_level_spec_uses_single_line_ocr() -> None:
    spec = get_spec("echoes.level")

    assert spec is not None
    assert spec.single_line is True
    assert spec.cache_mode == "transient"


def test_packaged_echo_level_signature_preprocess_expects_bgr_badge_pixels() -> None:
    spec = get_spec("echoes.level")

    assert spec is not None

    image_bgr = np.full((24, 32, 3), (44, 32, 22), dtype=np.uint8)
    image_bgr[4:20, 8:11] = np.asarray([187, 183, 167], dtype=np.uint8)
    image_bgr[10:13, 8:18] = np.asarray([187, 183, 167], dtype=np.uint8)

    preprocessed_bgr = spec._preprocess_for_signature(image_bgr, None)
    preprocessed_rgb = spec._preprocess_for_signature(
        cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB),
        None,
    )

    assert np.ptp(preprocessed_bgr) != 0
    assert np.ptp(preprocessed_rgb) == 0


def test_post_scaling_resizes_ocr_output() -> None:
    spec = OcrRegionSpec(
        roi_key="echoes.fullStatsValue",
        threshold_mode="floor",
        floor_value=200,
        pre_upscale=(24, 24),
        post_downscale=(12, 12),
    )
    image = np.zeros((8, 8, 3), dtype=np.uint8)
    image[2:6, 3:5] = 255

    processed = spec.preprocess(image)

    assert processed.ocr_rgb.shape == (12, 12, 3)


def test_invert_flips_thresholded_foreground_and_background() -> None:
    spec = OcrRegionSpec(
        roi_key="echoes.level",
        threshold_mode="floor",
        floor_value=200,
        invert=True,
    )
    image = np.zeros((5, 5, 3), dtype=np.uint8)
    image[2, 2] = np.asarray([255, 255, 255], dtype=np.uint8)

    processed = _gray_from_rgb(spec.preprocess(image).ocr_rgb)

    assert processed[2, 2] == 0
    assert processed[0, 0] == 255


def test_signature_ignores_background_when_signing_preprocessed_image() -> None:
    spec = OcrRegionSpec(
        roi_key="echoes.echoName",
        color_space="bgr",
        text_color_ranges=[((5, 5, 250), (5, 5, 250))],
        signature_preprocess=SignaturePreprocessSpec(color_space="bgr"),
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
        signature_preprocess=SignaturePreprocessSpec(
            color_space="gray",
            post_downscale=(8, 8),
        ),
    )
    base = np.zeros((128, 128, 3), dtype=np.uint8)
    shifted = np.zeros((128, 128, 3), dtype=np.uint8)
    base[48:80, 48:80] = 255
    shifted[48:80, 49:81] = 255

    assert spec.make_signature(base) == spec.make_signature(shifted)


def test_signature_thresholds_preprocessed_gray_before_hashing() -> None:
    spec = OcrRegionSpec(
        roi_key="echoes.fullStatsName",
        threshold_mode="floor",
        floor_value=100,
        sig_text_floor=230,
        signature_preprocess=SignaturePreprocessSpec(
            color_space="gray",
            post_downscale=(32, 32),
        ),
    )
    image_a = np.full((64, 64, 3), 110, dtype=np.uint8)
    image_b = np.full((64, 64, 3), 120, dtype=np.uint8)
    image_a[16:48, 20:44] = 255
    image_b[16:48, 20:44] = 255

    assert spec.make_signature(image_a) == spec.make_signature(image_b)


def test_signature_falls_back_to_raw_image_when_preprocessed_plane_is_constant() -> None:
    spec = OcrRegionSpec(
        roi_key="echoes.echoName",
        color_space="bgr",
        text_color_ranges=[((5, 5, 250), (5, 5, 250))],
        signature_preprocess=SignaturePreprocessSpec(color_space="bgr"),
    )
    image_a = np.zeros((24, 24, 3), dtype=np.uint8)
    image_b = np.zeros((24, 24, 3), dtype=np.uint8)
    image_a[4:12, 4:12] = np.asarray([255, 255, 255], dtype=np.uint8)
    image_b[12:20, 12:20] = np.asarray([255, 255, 255], dtype=np.uint8)

    assert spec.make_signature(image_a) != spec.make_signature(image_b)


def test_signature_can_use_separate_preprocess_spec() -> None:
    spec = OcrRegionSpec(
        roi_key="echoes.echoName",
        color_space="bgr",
        text_color_ranges=[((5, 5, 250), (5, 5, 250))],
        signature_preprocess=SignaturePreprocessSpec(
            color_space="bgr",
            text_color_ranges=[((7, 240, 7), (7, 240, 7))],
        ),
    )
    image_a = np.zeros((10, 10, 3), dtype=np.uint8)
    image_b = np.zeros((10, 10, 3), dtype=np.uint8)
    image_a[2:8, 2:8] = np.asarray([7, 240, 7], dtype=np.uint8)
    image_b[2:8, 2:8] = np.asarray([7, 240, 7], dtype=np.uint8)
    # Different OCR-target pixels should not affect signature when a
    # separate signature-preprocess recipe is configured.
    image_a[0, 0] = np.asarray([5, 5, 250], dtype=np.uint8)

    assert spec.make_signature(image_a) == spec.make_signature(image_b)


def test_load_specs_from_toml_parses_rarity_and_fallback_color_space(tmp_path: Path) -> None:
    config_path = tmp_path / "ocr_region_specs.toml"
    config_path.write_text(
        "\n".join(
            [
                'spec_version = "test-spec"',
                '',
                '[echoes.echoName]',
                'spec_version = "echo-name-spec"',
                'color_space = "bgr"',
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
                '[echoes.echoName.signature]',
                'color_space = "gray"',
                'threshold_mode = "floor"',
                'floor_value = 150',
                '',
                '[echoes.fullStatsValue]',
                'threshold_mode = "floor"',
                'floor_value = 100',
                'pre_upscale = [64, 48]',
                '',
                '[echoes.fullStatsValue.signature]',
                'post_downscale = [32, 16]',
            ]
        ),
        encoding="utf-8",
    )

    specs = load_specs_from_toml(str(config_path))

    echo_name = specs["echoes.echoName"]
    assert echo_name.spec_version == "echo-name-spec"
    assert echo_name.color_space == "bgr"
    assert echo_name.single_line is True
    assert echo_name.fallback_color_space == "hsv"
    assert echo_name.text_color_ranges == [((20, 60, 150), (32, 255, 255))]
    assert echo_name.text_color_ranges_by_rarity == {5: [((5, 5, 250), (5, 5, 250))]}
    assert echo_name.signature_preprocess is not None
    assert echo_name.signature_preprocess.color_space == "gray"
    assert echo_name.signature_preprocess.threshold_mode == "floor"
    assert echo_name.signature_preprocess.floor_value == 150

    stats_value = specs["echoes.fullStatsValue"]
    assert stats_value.spec_version == "test-spec"
    assert stats_value.threshold_mode == "floor"
    assert stats_value.floor_value == 100
    assert stats_value.pre_upscale == (64, 48)
    assert stats_value.signature_preprocess is not None
    assert stats_value.signature_preprocess.post_downscale == (32, 16)


def test_load_specs_from_toml_maps_legacy_sig_downscale_to_signature_post_downscale(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "ocr_region_specs.toml"
    config_path.write_text(
        "\n".join(
            [
                'spec_version = "test-spec"',
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

    stats_value = specs["echoes.fullStatsValue"]
    assert stats_value.signature_preprocess is not None
    assert stats_value.signature_preprocess.post_downscale == (32, 16)


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