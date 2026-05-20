"""
tune_region_spec.py - ad hoc OCR/signature region-spec preview tool
==================================================================

Render a cropped region through the existing OCR or signature preprocessing
pipeline and save the result as an RGB PNG.

The input image is expected to be a previously captured crop. The file itself
may have been produced from an RGB array; OpenCV loads it into the BGR layout
expected by the region-spec pipeline.

Examples
--------
Render an OCR preview from scratch::

    uv run cli/tune_region_spec.py crop.png \
        --output crop.ocr.png \
        --type ocr \
        --render-mode normalized_anchor_contrast \
        --color-space hsv \
        --text-color-ranges 255,255,249 \
        --post-upscale 512,128

Start from an existing packaged spec and override one field::

    uv run cli/tune_region_spec.py crop.png \
        --output crop.preview.png \
        --type ocr \
        --base-spec echoes.echoName \
        --render-mode masked_color \
        --rarity 5

Render the signature image used for cache-keying::

    uv run cli/tune_region_spec.py crop.png \
        --output crop.signature.png \
        --type signature \
        --base-spec echoes.level \
        --signature-post-downscale 128,64
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Sequence

import cv2
import numpy as np

# ---------------------------------------------------------------------------
# Project path bootstrap
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SRC_ROOT = _PROJECT_ROOT / "src"
for _path in (_PROJECT_ROOT, _SRC_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from wuwa_inventory_kamera.scraping.ocr.region_specs import (  # noqa: E402
    OcrRegionSpec,
    SignaturePreprocessSpec,
    default_specs_path,
    load_specs_from_toml,
)


_COLOR_SPACE_CHOICES = ("bgr", "rgb", "hsv", "gray")
_THRESHOLD_MODE_CHOICES = ("none", "floor", "otsu")
_MORPHOLOGY_CHOICES = ("none", "close")
_RENDER_MODE_CHOICES = (
    "legacy_binary_rgb",
    "raw_passthrough",
    "masked_color",
    "neutral_bg_color",
    "luma_boost_color",
    "anchor_contrast",
    "normalized_anchor_contrast",
    "masked_normalized_anchor_contrast",
    "normalized_anchor_color",
)


def _parse_size(raw: str) -> tuple[int, int]:
    try:
        width_raw, height_raw = raw.split(",", maxsplit=1)
        width = int(width_raw.strip())
        height = int(height_raw.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Size must be W,H but got {raw!r}"
        ) from exc

    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError(
            f"Size values must be positive but got {raw!r}"
        )
    return width, height


def _parse_range(raw: str) -> tuple[tuple[int, ...], tuple[int, ...]]:
    raw = raw.strip()
    if ":" in raw:
        lo_raw, hi_raw = raw.split(":", maxsplit=1)
    else:
        lo_raw = hi_raw = raw

    try:
        lo = tuple(int(part.strip()) for part in lo_raw.split(",") if part.strip())
        hi = tuple(int(part.strip()) for part in hi_raw.split(",") if part.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Range must contain integers but got {raw!r}"
        ) from exc

    if len(lo) != len(hi) or len(lo) not in (1, 3):
        raise argparse.ArgumentTypeError(
            "Range must use matching 1-channel or 3-channel bounds"
        )
    return lo, hi


def _add_optional_bool_flag(
    parser: argparse.ArgumentParser,
    *,
    flag_name: str,
    dest: str,
    help_text: str,
) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        f"--{flag_name}",
        dest=dest,
        action="store_const",
        const=True,
        default=None,
        help=f"Enable {help_text}.",
    )
    group.add_argument(
        f"--no-{flag_name}",
        dest=dest,
        action="store_const",
        const=False,
        help=f"Disable {help_text}.",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Render one cropped region through the OCR or signature preprocessing "
            "pipeline and save the result as an RGB PNG."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("image", help="Path to the captured crop PNG.")
    parser.add_argument(
        "--output",
        required=True,
        help="Path to the output PNG written in RGB color order.",
    )
    parser.add_argument(
        "--type",
        dest="output_type",
        required=True,
        choices=["ocr", "signature"],
        help="Which processed image to export.",
    )
    parser.add_argument(
        "--base-spec",
        default=None,
        help="Optional packaged ROI key to start from before applying overrides.",
    )
    parser.add_argument(
        "--config",
        default=str(default_specs_path()),
        help="Path to the OCR region spec TOML used by --base-spec.",
    )
    parser.add_argument(
        "--roi-key",
        default="cli.tune",
        help="ROI key used when constructing a spec from scratch.",
    )
    parser.add_argument(
        "--rarity",
        type=int,
        default=None,
        help="Optional rarity override used when preprocessing the crop.",
    )

    parser.add_argument(
        "--color-space",
        default=None,
        choices=_COLOR_SPACE_CHOICES,
        help="Base preprocessing color space.",
    )
    parser.add_argument(
        "--fallback-color-space",
        default=None,
        choices=_COLOR_SPACE_CHOICES,
        help="Optional fallback color space for base text ranges.",
    )
    parser.add_argument(
        "--text-color-ranges",
        "--text-color-range",
        dest="text_color_ranges",
        type=_parse_range,
        action="append",
        default=None,
        help=(
            "Repeatable text color range in lo:hi or exact form, for example "
            "255,255,249 or 20,60,150:32,255,255."
        ),
    )
    parser.add_argument(
        "--background-color-ranges",
        "--background-color-range",
        dest="background_color_ranges",
        type=_parse_range,
        action="append",
        default=None,
        help="Repeatable background reject range in lo:hi or exact form.",
    )
    parser.add_argument(
        "--threshold-mode",
        default=None,
        choices=_THRESHOLD_MODE_CHOICES,
        help="Base threshold mode.",
    )
    parser.add_argument(
        "--floor-value",
        type=int,
        default=None,
        help="Base floor threshold used when threshold-mode=floor.",
    )
    parser.add_argument(
        "--morphology",
        default=None,
        choices=_MORPHOLOGY_CHOICES,
        help="Base morphology mode.",
    )
    parser.add_argument(
        "--render-mode",
        default=None,
        choices=_RENDER_MODE_CHOICES,
        help="OCR render mode.",
    )
    parser.add_argument(
        "--anchor-contrast-sharpness",
        type=float,
        default=None,
        help="Sharpness used by anchor-based render modes.",
    )
    parser.add_argument(
        "--pre-upscale",
        type=_parse_size,
        default=None,
        help="Base OCR pre-upscale minimum as W,H.",
    )
    parser.add_argument(
        "--pre-downscale",
        type=_parse_size,
        default=None,
        help="Base OCR pre-downscale maximum as W,H.",
    )
    parser.add_argument(
        "--post-upscale",
        type=_parse_size,
        default=None,
        help="Base OCR post-upscale minimum as W,H.",
    )
    parser.add_argument(
        "--post-downscale",
        type=_parse_size,
        default=None,
        help="Base OCR post-downscale maximum as W,H.",
    )
    parser.add_argument(
        "--sig-text-floor",
        type=int,
        default=None,
        help="Signature normalization text floor.",
    )
    parser.add_argument(
        "--sig-max-spread",
        type=int,
        default=None,
        help="Signature normalization max spread.",
    )
    _add_optional_bool_flag(
        parser,
        flag_name="invert",
        dest="invert",
        help_text="base inversion",
    )
    _add_optional_bool_flag(
        parser,
        flag_name="single-line",
        dest="single_line",
        help_text="single-line glyph repair",
    )

    parser.add_argument(
        "--clear-signature-preprocess",
        action="store_true",
        help="Discard the nested signature preprocess config from --base-spec.",
    )
    parser.add_argument(
        "--signature-color-space",
        default=None,
        choices=_COLOR_SPACE_CHOICES,
        help="Signature-only preprocessing color space.",
    )
    parser.add_argument(
        "--signature-fallback-color-space",
        default=None,
        choices=_COLOR_SPACE_CHOICES,
        help="Signature-only fallback color space.",
    )
    parser.add_argument(
        "--signature-text-color-ranges",
        "--signature-text-color-range",
        dest="signature_text_color_ranges",
        type=_parse_range,
        action="append",
        default=None,
        help="Repeatable signature text color range in lo:hi or exact form.",
    )
    parser.add_argument(
        "--signature-background-color-ranges",
        "--signature-background-color-range",
        dest="signature_background_color_ranges",
        type=_parse_range,
        action="append",
        default=None,
        help="Repeatable signature background reject range in lo:hi or exact form.",
    )
    parser.add_argument(
        "--signature-threshold-mode",
        default=None,
        choices=_THRESHOLD_MODE_CHOICES,
        help="Signature-only threshold mode.",
    )
    parser.add_argument(
        "--signature-floor-value",
        type=int,
        default=None,
        help="Signature-only floor threshold.",
    )
    parser.add_argument(
        "--signature-morphology",
        default=None,
        choices=_MORPHOLOGY_CHOICES,
        help="Signature-only morphology mode.",
    )
    parser.add_argument(
        "--signature-pre-upscale",
        type=_parse_size,
        default=None,
        help="Signature pre-upscale minimum as W,H.",
    )
    parser.add_argument(
        "--signature-pre-downscale",
        type=_parse_size,
        default=None,
        help="Signature pre-downscale maximum as W,H.",
    )
    parser.add_argument(
        "--signature-post-upscale",
        type=_parse_size,
        default=None,
        help="Signature post-upscale minimum as W,H.",
    )
    parser.add_argument(
        "--signature-post-downscale",
        type=_parse_size,
        default=None,
        help="Signature post-downscale maximum as W,H.",
    )
    _add_optional_bool_flag(
        parser,
        flag_name="signature-invert",
        dest="signature_invert",
        help_text="signature inversion",
    )
    _add_optional_bool_flag(
        parser,
        flag_name="signature-single-line",
        dest="signature_single_line",
        help_text="signature single-line glyph repair",
    )
    return parser


def _read_bgr(path: Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(path)
    return image


def _write_rgb(path: Path, image: np.ndarray) -> None:
    rgb = _ensure_rgb(image)
    path.parent.mkdir(parents=True, exist_ok=True)
    ok = cv2.imwrite(str(path), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    if not ok:
        raise OSError(f"Failed to write output image: {path}")


def _ensure_rgb(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
    if image.ndim == 3 and image.shape[2] == 3:
        return np.ascontiguousarray(image)
    raise ValueError(f"Unsupported image shape for RGB export: {image.shape}")


def _load_base_spec(config_path: Path, roi_key: str) -> OcrRegionSpec:
    specs = load_specs_from_toml(config_path)
    if roi_key not in specs:
        available = ", ".join(sorted(specs))
        raise KeyError(f"Unknown spec {roi_key!r}. Available: {available}")
    return specs[roi_key]


def _build_signature_preprocess(
    args: argparse.Namespace,
    base_signature: SignaturePreprocessSpec | None,
) -> SignaturePreprocessSpec | None:
    signature = None if args.clear_signature_preprocess else base_signature

    overrides: dict[str, Any] = {}
    if args.signature_color_space is not None:
        overrides["color_space"] = args.signature_color_space
    if args.signature_fallback_color_space is not None:
        overrides["fallback_color_space"] = args.signature_fallback_color_space
    if args.signature_text_color_ranges is not None:
        overrides["text_color_ranges"] = args.signature_text_color_ranges
    if args.signature_background_color_ranges is not None:
        overrides["background_color_ranges"] = args.signature_background_color_ranges
    if args.signature_threshold_mode is not None:
        overrides["threshold_mode"] = args.signature_threshold_mode
    if args.signature_floor_value is not None:
        overrides["floor_value"] = args.signature_floor_value
    if args.signature_morphology is not None:
        overrides["morphology"] = args.signature_morphology
    if args.signature_invert is not None:
        overrides["invert"] = args.signature_invert
    if args.signature_single_line is not None:
        overrides["single_line"] = args.signature_single_line
    if args.signature_pre_upscale is not None:
        overrides["pre_upscale"] = args.signature_pre_upscale
    if args.signature_pre_downscale is not None:
        overrides["pre_downscale"] = args.signature_pre_downscale
    if args.signature_post_upscale is not None:
        overrides["post_upscale"] = args.signature_post_upscale
    if args.signature_post_downscale is not None:
        overrides["post_downscale"] = args.signature_post_downscale

    if signature is None and not overrides:
        return None
    if signature is None:
        signature = SignaturePreprocessSpec()
    if not overrides:
        return signature
    return replace(signature, **overrides)


def _build_region_spec(args: argparse.Namespace) -> OcrRegionSpec:
    base_spec = (
        _load_base_spec(Path(args.config), args.base_spec)
        if args.base_spec is not None
        else None
    )

    signature_preprocess = _build_signature_preprocess(
        args,
        base_spec.signature_preprocess if base_spec is not None else None,
    )

    overrides: dict[str, Any] = {}
    if args.color_space is not None:
        overrides["color_space"] = args.color_space
    if args.fallback_color_space is not None:
        overrides["fallback_color_space"] = args.fallback_color_space
    if args.text_color_ranges is not None:
        overrides["text_color_ranges"] = args.text_color_ranges
    if args.background_color_ranges is not None:
        overrides["background_color_ranges"] = args.background_color_ranges
    if args.threshold_mode is not None:
        overrides["threshold_mode"] = args.threshold_mode
    if args.floor_value is not None:
        overrides["floor_value"] = args.floor_value
    if args.morphology is not None:
        overrides["morphology"] = args.morphology
    if args.render_mode is not None:
        overrides["render_mode"] = args.render_mode
    if args.anchor_contrast_sharpness is not None:
        overrides["anchor_contrast_sharpness"] = args.anchor_contrast_sharpness
    if args.invert is not None:
        overrides["invert"] = args.invert
    if args.single_line is not None:
        overrides["single_line"] = args.single_line
    if args.pre_upscale is not None:
        overrides["pre_upscale"] = args.pre_upscale
    if args.pre_downscale is not None:
        overrides["pre_downscale"] = args.pre_downscale
    if args.post_upscale is not None:
        overrides["post_upscale"] = args.post_upscale
    if args.post_downscale is not None:
        overrides["post_downscale"] = args.post_downscale
    if args.sig_text_floor is not None:
        overrides["sig_text_floor"] = args.sig_text_floor
    if args.sig_max_spread is not None:
        overrides["sig_max_spread"] = args.sig_max_spread
    if signature_preprocess is not None:
        overrides["signature_preprocess"] = signature_preprocess
    elif base_spec is not None and args.clear_signature_preprocess:
        overrides["signature_preprocess"] = None

    if base_spec is not None:
        roi_key = args.roi_key if args.roi_key != "cli.tune" else base_spec.roi_key
        return replace(base_spec, roi_key=roi_key, **overrides)

    return OcrRegionSpec(roi_key=args.roi_key, **overrides)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        input_path = Path(args.image)
        output_path = Path(args.output)
        image_bgr = _read_bgr(input_path)
        spec = _build_region_spec(args)
        processed = spec.preprocess(image_bgr, rarity=args.rarity)
        output_image = (
            processed.ocr_rgb
            if args.output_type == "ocr"
            else processed.signature_image
        )
        _write_rgb(output_path, output_image)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Input     : {input_path}")
    print(f"Output    : {output_path}")
    print(f"Type      : {args.output_type}")
    print(f"Spec      : {spec.roi_key}")
    print(f"Shape     : {output_image.shape}")
    if args.base_spec is not None:
        print(f"Base spec : {args.base_spec}")
    if args.rarity is not None:
        print(f"Rarity    : {args.rarity}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())