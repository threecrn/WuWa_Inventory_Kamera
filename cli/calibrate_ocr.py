"""
calibrate_ocr.py — OCR region-spec calibration helper
=====================================================

Developer-facing tool for inspecting OCR preprocessing, sampling colour
values from crops, and updating ``config/ocr_region_specs.toml``.

The tool operates on already-cropped images. Typical workflow:

1. Preview a spec against one or more crops.
2. Sample exact text colours from known-good pixels.
3. Write updated ranges or thresholds back to the TOML.
4. Re-run preview or compare_ocr.py to verify the change.

Examples
--------
Preview the current spec and save side-by-side panels::

    uv run cli/calibrate_ocr.py preview \
        --spec echoes.echoName \
        --image export/session/raw/echo_0000/debug/echo_name.png \
        --rarity 5

Sample exact BGR / RGB / HSV values from a crop::

    uv run cli/calibrate_ocr.py sample \
        --image export/session/raw/echo_0000/debug/echo_name.png \
        --point 14,9 --point 21,9

Write a new exact per-rarity colour range from sampled pixels::

    uv run cli/calibrate_ocr.py write \
        --spec echoes.echoName \
        --target rarity \
        --rarity 5 \
        --sample-image export/session/raw/echo_0000/debug/echo_name.png \
        --point 14,9 --point 21,9

Adjust a threshold-based region and keep a backup of the TOML::

    uv run cli/calibrate_ocr.py write \
        --spec echoes.fullStatsValue \
        --threshold-mode floor \
        --floor-value 110
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from pathlib import Path
from typing import Any

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

from wuwa_inventory_kamera.scraping.ocr import get_backend, tokens_to_string  # noqa: E402
from wuwa_inventory_kamera.scraping.ocr.region_specs import (  # noqa: E402
    OcrRegionSpec,
    load_specs_from_toml,
)

logger = logging.getLogger("wuwa.calibrate_ocr")

_PROVIDER_MAP: dict[str, list[str]] = {
    "cpu": ["CPUExecutionProvider"],
    "dml": ["DmlExecutionProvider", "CPUExecutionProvider"],
}


# ---------------------------------------------------------------------------
# Argument parsing helpers
# ---------------------------------------------------------------------------

def _parse_point(raw: str) -> tuple[int, int]:
    try:
        x_str, y_str = raw.split(",", maxsplit=1)
        return int(x_str), int(y_str)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Point must be X,Y but got {raw!r}"
        ) from exc


def _parse_range(raw: str) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Parse ``lo0,lo1,lo2:hi0,hi1,hi2`` or ``v0,v1,v2`` exact form."""
    raw = raw.strip()
    if ":" in raw:
        lo_raw, hi_raw = raw.split(":", maxsplit=1)
    else:
        lo_raw = hi_raw = raw

    lo = tuple(int(part.strip()) for part in lo_raw.split(",") if part.strip())
    hi = tuple(int(part.strip()) for part in hi_raw.split(",") if part.strip())
    if len(lo) != len(hi) or not lo:
        raise argparse.ArgumentTypeError(
            f"Range must have equal lo/hi lengths but got {raw!r}"
        )
    return lo, hi


def _configure_logging(level_name: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level_name.upper(), logging.INFO),
        format="%(levelname)s: %(message)s",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Preview, sample, and update OCR region preprocessing specs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--config",
        default=str(_PROJECT_ROOT / "config" / "ocr_region_specs.toml"),
        help="Path to OCR region spec TOML.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity.",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    preview = sub.add_parser(
        "preview",
        help="Render raw/preprocessed/signature previews for one or more crops.",
    )
    _add_spec_arg(preview)
    _add_image_source_args(preview)
    preview.add_argument("--rarity", type=int, default=None, help="Optional rarity override.")
    preview.add_argument(
        "--provider",
        default="cpu",
        choices=sorted(_PROVIDER_MAP),
        help="ONNX Runtime provider for OCR preview.",
    )
    preview.add_argument(
        "--ocr",
        action="store_true",
        help="Run OCR on both raw and preprocessed images and print the text.",
    )
    preview.add_argument(
        "--save-dir",
        default=None,
        help="Directory for side-by-side preview PNGs. Defaults to calibration_output/<spec>/.",
    )
    preview.add_argument(
        "--show",
        action="store_true",
        help="Show each preview in an OpenCV window.",
    )
    preview.add_argument(
        "--point",
        dest="points",
        type=_parse_point,
        action="append",
        default=[],
        help="Optional X,Y sample point(s) to annotate in the preview.",
    )
    preview.add_argument(
        "--max-images",
        type=int,
        default=None,
        help="Optional limit for number of input images.",
    )

    sample = sub.add_parser(
        "sample",
        help="Print exact BGR / RGB / HSV / gray values at selected points.",
    )
    sample.add_argument("--image", required=True, help="Path to a single crop image.")
    sample.add_argument(
        "--point",
        dest="points",
        type=_parse_point,
        action="append",
        required=True,
        help="X,Y pixel coordinate to sample. Repeatable.",
    )
    sample.add_argument(
        "--radius",
        type=int,
        default=0,
        help="Neighborhood radius for median sampling (default: exact pixel).",
    )
    sample.add_argument(
        "--save",
        default=None,
        help="Optional annotated copy of the sampled image.",
    )

    write = sub.add_parser(
        "write",
        help="Update one region spec inside the TOML.",
    )
    _add_spec_arg(write)
    write.add_argument(
        "--target",
        default="base",
        choices=["base", "fallback", "rarity"],
        help="Which sub-section to update.",
    )
    write.add_argument(
        "--rarity",
        type=int,
        default=None,
        help="Required when --target rarity is used.",
    )
    write.add_argument(
        "--sample-image",
        default=None,
        help="Optional crop image used to derive text ranges from sampled pixels.",
    )
    write.add_argument(
        "--point",
        dest="points",
        type=_parse_point,
        action="append",
        default=[],
        help="Point(s) sampled from --sample-image to build text_color_ranges.",
    )
    write.add_argument(
        "--sample-space",
        default=None,
        choices=["bgr", "rgb", "hsv", "gray"],
        help="Colour space used when sampling points. Defaults to the target section's space.",
    )
    write.add_argument(
        "--sample-radius",
        type=int,
        default=0,
        help="Neighborhood radius for median sampling.",
    )
    write.add_argument(
        "--tolerance",
        type=int,
        default=0,
        help="Expand each sampled value into +/- tolerance.",
    )
    write.add_argument(
        "--text-range",
        dest="text_ranges",
        type=_parse_range,
        action="append",
        default=[],
        help="Explicit text color range (repeatable). Format: lo:hi or exact value.",
    )
    write.add_argument(
        "--background-range",
        dest="background_ranges",
        type=_parse_range,
        action="append",
        default=[],
        help="Explicit background reject range (repeatable).",
    )
    write.add_argument(
        "--color-space",
        default=None,
        choices=["bgr", "rgb", "hsv", "gray"],
        help="Base or fallback color space to write.",
    )
    write.add_argument(
        "--threshold-mode",
        default=None,
        choices=["none", "floor", "otsu"],
        help="Base threshold mode.",
    )
    write.add_argument("--floor-value", type=int, default=None, help="Base floor threshold.")
    write.add_argument(
        "--morphology",
        default=None,
        choices=["none", "close"],
        help="Base morphology mode.",
    )
    invert_group = write.add_mutually_exclusive_group()
    invert_group.add_argument("--invert", action="store_true", help="Set invert=true.")
    invert_group.add_argument("--no-invert", action="store_true", help="Set invert=false.")

    write.add_argument(
        "--allowed-chars",
        default=None,
        help="Set allowed_chars on the base section.",
    )
    write.add_argument(
        "--clear-allowed-chars",
        action="store_true",
        help="Remove allowed_chars from the base section.",
    )
    write.add_argument(
        "--cache-mode",
        default=None,
        choices=["none", "transient", "persistent"],
        help="Base cache mode.",
    )
    sig_group = write.add_mutually_exclusive_group()
    sig_group.add_argument(
        "--sig-from-preprocessed",
        action="store_true",
        help="Set sig_from_preprocessed=true on the base section.",
    )
    sig_group.add_argument(
        "--no-sig-from-preprocessed",
        action="store_true",
        help="Set sig_from_preprocessed=false on the base section.",
    )
    write.add_argument("--sig-text-floor", type=int, default=None, help="Base signature text floor.")
    write.add_argument("--sig-max-spread", type=int, default=None, help="Base signature max spread.")
    write.add_argument(
        "--sig-downscale",
        default=None,
        help="Base signature downscale as W,H.",
    )
    write.add_argument(
        "--backup-suffix",
        default=".bak",
        help="Backup suffix written before the TOML is replaced.",
    )
    write.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the updated section but do not modify the TOML.",
    )

    return parser


def _add_spec_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--spec", required=True, help="ROI key, e.g. echoes.echoName")


def _add_image_source_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--image",
        dest="images",
        action="append",
        default=[],
        help="Direct crop image path. Repeatable.",
    )
    parser.add_argument(
        "--glob",
        dest="globs",
        action="append",
        default=[],
        help="Glob pattern relative to the current working directory. Repeatable.",
    )


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def _read_bgr(path: Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(path)
    return image


def _load_specs(config_path: Path) -> dict[str, OcrRegionSpec]:
    return load_specs_from_toml(str(config_path))


def _resolve_spec(config_path: Path, roi_key: str) -> OcrRegionSpec:
    specs = _load_specs(config_path)
    if roi_key not in specs:
        available = ", ".join(sorted(specs))
        raise KeyError(f"Unknown spec {roi_key!r}. Available: {available}")
    return specs[roi_key]


def _resolve_images(images: list[str], globs: list[str], *, max_images: int | None = None) -> list[Path]:
    paths: list[Path] = []
    for raw in images:
        path = Path(raw)
        if not path.is_file():
            raise FileNotFoundError(path)
        paths.append(path)

    for pattern in globs:
        matches = sorted(Path.cwd().glob(pattern))
        if not matches:
            logger.warning("No files matched glob: %s", pattern)
        paths.extend(path for path in matches if path.is_file())

    deduped = list(dict.fromkeys(path.resolve() for path in paths))
    if max_images is not None:
        deduped = deduped[:max_images]
    if not deduped:
        raise ValueError("No input images provided.")
    return deduped


def _ensure_preview_size(image: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    target_w, target_h = size
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if image.shape[1] == target_w and image.shape[0] == target_h:
        return image
    interpolation = cv2.INTER_NEAREST if _is_binary(image) else cv2.INTER_AREA
    return cv2.resize(image, (target_w, target_h), interpolation=interpolation)


def _is_binary(image: np.ndarray) -> bool:
    if image.ndim == 3:
        plane = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        plane = image
    return bool(np.all((plane == 0) | (plane == 255)))


def _ensure_bgr_for_preview(image: np.ndarray, *, input_space: str) -> np.ndarray:
    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if input_space == "bgr":
        return image.copy()
    return cv2.cvtColor(image, cv2.COLOR_RGB2BGR)


def _annotate_points(image: np.ndarray, points: list[tuple[int, int]]) -> np.ndarray:
    out = image.copy()
    for idx, (x, y) in enumerate(points, start=1):
        cv2.circle(out, (x, y), 3, (0, 0, 255), -1)
        cv2.putText(
            out,
            str(idx),
            (x + 5, y - 5),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 0, 255),
            1,
            cv2.LINE_AA,
        )
    return out


def _label_panel(image: np.ndarray, label: str) -> np.ndarray:
    banner = np.full((28, image.shape[1], 3), 24, dtype=np.uint8)
    cv2.putText(
        banner,
        label,
        (8, 19),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (235, 235, 235),
        1,
        cv2.LINE_AA,
    )
    return np.vstack([banner, image])


def _build_preview_panel(
    raw_bgr: np.ndarray,
    processed_rgb: np.ndarray,
    signature: np.ndarray,
    points: list[tuple[int, int]],
) -> np.ndarray:
    height, width = raw_bgr.shape[:2]
    processed_bgr = _ensure_bgr_for_preview(processed_rgb, input_space="rgb")
    signature_bgr = _ensure_bgr_for_preview(signature, input_space="gray")

    raw_vis = _annotate_points(raw_bgr, points) if points else raw_bgr
    processed_vis = _annotate_points(processed_bgr, points) if points else processed_bgr
    signature_vis = _ensure_preview_size(signature_bgr, (width, height))

    panels = [
        _label_panel(raw_vis, "raw"),
        _label_panel(processed_vis, "preprocessed"),
        _label_panel(signature_vis, "signature"),
    ]
    return np.hstack(panels)


def _to_gray_preview(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def _make_backend(provider: str):
    return get_backend("rapidocr", onnx_providers=_PROVIDER_MAP[provider])


def _ocr_text(backend, rgb_image: np.ndarray, allowed_chars: str | None) -> str:
    tokens = backend.recognize(rgb_image)
    return tokens_to_string(tokens, allowedChars=allowed_chars)


def _quote_toml_string(value: str) -> str:
    return json.dumps(value)


def _format_toml_value(value: Any, indent: int = 0) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return _quote_toml_string(value)
    if isinstance(value, tuple):
        value = list(value)
    if isinstance(value, list):
        return _format_toml_list(value, indent)
    raise TypeError(f"Unsupported TOML value type: {type(value)!r}")


def _format_toml_list(values: list[Any], indent: int = 0) -> str:
    if not values:
        return "[]"
    if all(not isinstance(item, (list, tuple, dict)) for item in values):
        return "[" + ", ".join(_format_toml_value(item, indent) for item in values) + "]"

    inner_indent = " " * (indent + 4)
    closing_indent = " " * indent
    lines = ["["]
    for item in values:
        formatted = _format_toml_value(item, indent + 4)
        if "\n" in formatted:
            formatted = "\n".join(inner_indent + line for line in formatted.splitlines())
            lines.append(f"{formatted},")
        else:
            lines.append(f"{inner_indent}{formatted},")
    lines.append(f"{closing_indent}]")
    return "\n".join(lines)


def _write_toml(path: Path, data: dict[str, Any]) -> None:
    lines: list[str] = []

    for key, value in data.items():
        if not isinstance(value, dict):
            lines.append(f"{key} = {_format_toml_value(value)}")
    if lines:
        lines.append("")

    for key, value in data.items():
        if isinstance(value, dict):
            _emit_toml_table(lines, key, value)

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _emit_toml_table(lines: list[str], prefix: str, data: dict[str, Any]) -> None:
    lines.append(f"[{prefix}]")
    for key, value in data.items():
        if not isinstance(value, dict):
            lines.append(f"{key} = {_format_toml_value(value)}")
    lines.append("")
    for key, value in data.items():
        if isinstance(value, dict):
            _emit_toml_table(lines, f"{prefix}.{key}", value)


def _ranges_to_toml_value(
    ranges: list[tuple[tuple[int, ...], tuple[int, ...]]],
) -> list[list[list[int]]]:
    return [[list(lo), list(hi)] for lo, hi in ranges]


def _parse_wh(value: str) -> list[int]:
    try:
        w_raw, h_raw = value.split(",", maxsplit=1)
        return [int(w_raw), int(h_raw)]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"sig_downscale must be W,H but got {value!r}"
        ) from exc


# ---------------------------------------------------------------------------
# Sampling helpers
# ---------------------------------------------------------------------------

def _convert_color_space(bgr: np.ndarray, color_space: str) -> np.ndarray:
    if color_space == "bgr":
        return bgr
    if color_space == "rgb":
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    if color_space == "hsv":
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    if color_space == "gray":
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    raise ValueError(f"Unsupported color_space: {color_space!r}")


def _sample_value(image: np.ndarray, point: tuple[int, int], radius: int) -> tuple[int, ...]:
    x, y = point
    height, width = image.shape[:2]
    if x < 0 or y < 0 or x >= width or y >= height:
        raise ValueError(f"Point {(x, y)} outside image bounds {(width, height)}")

    x0 = max(0, x - radius)
    x1 = min(width, x + radius + 1)
    y0 = max(0, y - radius)
    y1 = min(height, y + radius + 1)
    window = image[y0:y1, x0:x1]
    if image.ndim == 2:
        return (int(np.median(window)),)
    median = np.median(window.reshape(-1, window.shape[2]), axis=0)
    return tuple(int(round(value)) for value in median.tolist())


def _sample_ranges_from_points(
    bgr: np.ndarray,
    *,
    points: list[tuple[int, int]],
    color_space: str,
    radius: int,
    tolerance: int,
) -> list[tuple[tuple[int, ...], tuple[int, ...]]]:
    image = _convert_color_space(bgr, color_space)
    ranges = []
    for point in points:
        sampled = _sample_value(image, point, radius)
        lo = tuple(max(0, value - tolerance) for value in sampled)
        hi = tuple(min(255, value + tolerance) for value in sampled)
        ranges.append((lo, hi))
    return ranges


def _print_sample_report(bgr: np.ndarray, points: list[tuple[int, int]], radius: int) -> None:
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    for idx, point in enumerate(points, start=1):
        bgr_value = _sample_value(bgr, point, radius)
        rgb_value = _sample_value(rgb, point, radius)
        hsv_value = _sample_value(hsv, point, radius)
        gray_value = _sample_value(gray, point, radius)
        print(
            f"[{idx}] point={point}  "
            f"BGR={bgr_value}  RGB={rgb_value}  HSV={hsv_value}  GRAY={gray_value[0]}"
        )


# ---------------------------------------------------------------------------
# TOML update helpers
# ---------------------------------------------------------------------------

def _load_toml_data(path: Path) -> dict[str, Any]:
    import tomllib

    with path.open("rb") as handle:
        return tomllib.load(handle)


def _ensure_base_section(data: dict[str, Any], roi_key: str) -> dict[str, Any]:
    node = data
    for part in roi_key.split("."):
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    return node


def _ensure_target_section(
    base_section: dict[str, Any],
    *,
    target: str,
    rarity: int | None,
) -> dict[str, Any]:
    if target == "base":
        return base_section
    if target == "fallback":
        fallback = base_section.get("fallback")
        if not isinstance(fallback, dict):
            fallback = {}
            base_section["fallback"] = fallback
        return fallback
    if target == "rarity":
        if rarity is None:
            raise ValueError("--rarity is required when --target rarity is used")
        rarity_overrides = base_section.get("rarity_overrides")
        if not isinstance(rarity_overrides, dict):
            rarity_overrides = {}
            base_section["rarity_overrides"] = rarity_overrides
        rarity_section = rarity_overrides.get(str(rarity))
        if not isinstance(rarity_section, dict):
            rarity_section = {}
            rarity_overrides[str(rarity)] = rarity_section
        return rarity_section
    raise ValueError(f"Unsupported target: {target!r}")


def _infer_sampling_space(
    base_section: dict[str, Any],
    target_section: dict[str, Any],
    *,
    target: str,
    explicit: str | None,
) -> str:
    if explicit is not None:
        return explicit
    if target == "fallback":
        return str(target_section.get("color_space", base_section.get("color_space", "gray")))
    return str(base_section.get("color_space", "gray"))


def _set_or_delete(target: dict[str, Any], key: str, value: Any | None) -> None:
    if value is None:
        target.pop(key, None)
    else:
        target[key] = value


def _print_section_preview(
    roi_key: str,
    target: str,
    rarity: int | None,
    base_section: dict[str, Any],
    target_section: dict[str, Any],
) -> None:
    if target == "rarity":
        header = f"[{roi_key}.rarity_overrides.{rarity!s}]"
    elif target == "fallback":
        header = f"[{roi_key}.fallback]"
    else:
        header = f"[{roi_key}]"
    print(header)
    for key, value in target_section.items():
        if isinstance(value, dict):
            continue
        print(f"{key} = {_format_toml_value(value)}")
    if target != "base":
        print("\n[base scalars]")
        for key in (
            "color_space",
            "threshold_mode",
            "floor_value",
            "morphology",
            "invert",
            "allowed_chars",
            "cache_mode",
            "sig_from_preprocessed",
            "sig_text_floor",
            "sig_max_spread",
            "sig_downscale",
        ):
            if key in base_section:
                print(f"{key} = {_format_toml_value(base_section[key])}")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_preview(args: argparse.Namespace) -> None:
    config_path = Path(args.config)
    spec = _resolve_spec(config_path, args.spec)
    image_paths = _resolve_images(args.images, args.globs, max_images=args.max_images)

    if args.ocr:
        backend = _make_backend(args.provider)
    else:
        backend = None

    save_dir = (
        Path(args.save_dir)
        if args.save_dir is not None
        else _PROJECT_ROOT / "calibration_output" / args.spec.replace(".", "_")
    )
    save_dir.mkdir(parents=True, exist_ok=True)

    print(f"spec={spec.roi_key} cache_mode={spec.cache_mode} allowed_chars={spec.allowed_chars!r}")
    print(f"images={len(image_paths)} save_dir={save_dir}")

    for path in image_paths:
        raw_bgr = _read_bgr(path)
        processed_rgb = spec.preprocess(raw_bgr, rarity=args.rarity)
        signature = spec._image_for_signature(raw_bgr, args.rarity)
        panel = _build_preview_panel(raw_bgr, processed_rgb, signature, args.points)

        out_path = save_dir / f"{path.stem}_preview.png"
        cv2.imwrite(str(out_path), panel)
        print(f"{path.name}: preview -> {out_path}")

        if backend is not None:
            raw_rgb = cv2.cvtColor(raw_bgr, cv2.COLOR_BGR2RGB)
            raw_text = _ocr_text(backend, raw_rgb, spec.allowed_chars)
            processed_text = _ocr_text(backend, processed_rgb, spec.allowed_chars)
            print(f"  raw        : {raw_text!r}")
            print(f"  preprocessed: {processed_text!r}")

        if args.show:
            cv2.imshow(path.name, panel)
            key = cv2.waitKey(0)
            cv2.destroyWindow(path.name)
            if key in {27, ord("q"), ord("Q")}:
                break


def cmd_sample(args: argparse.Namespace) -> None:
    path = Path(args.image)
    bgr = _read_bgr(path)
    _print_sample_report(bgr, args.points, args.radius)

    if args.save:
        annotated = _annotate_points(bgr, args.points)
        out_path = Path(args.save)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out_path), annotated)
        print(f"annotated -> {out_path}")


def cmd_write(args: argparse.Namespace) -> None:
    config_path = Path(args.config)
    data = _load_toml_data(config_path)
    base_section = _ensure_base_section(data, args.spec)
    target_section = _ensure_target_section(base_section, target=args.target, rarity=args.rarity)

    if args.color_space is not None:
        if args.target == "rarity":
            raise ValueError("--color-space cannot be written under a rarity override")
        target_section["color_space"] = args.color_space

    if args.sample_image is not None:
        if not args.points:
            raise ValueError("At least one --point is required with --sample-image")
        sample_bgr = _read_bgr(Path(args.sample_image))
        sample_space = _infer_sampling_space(
            base_section,
            target_section,
            target=args.target,
            explicit=args.sample_space,
        )
        sampled_ranges = _sample_ranges_from_points(
            sample_bgr,
            points=args.points,
            color_space=sample_space,
            radius=args.sample_radius,
            tolerance=args.tolerance,
        )
        target_section["text_color_ranges"] = _ranges_to_toml_value(sampled_ranges)

    if args.text_ranges:
        target_section["text_color_ranges"] = _ranges_to_toml_value(args.text_ranges)

    if args.background_ranges:
        if args.target == "rarity":
            raise ValueError("background_color_ranges are only valid on base or fallback sections")
        target_section["background_color_ranges"] = _ranges_to_toml_value(args.background_ranges)

    if args.threshold_mode is not None:
        base_section["threshold_mode"] = args.threshold_mode
    if args.floor_value is not None:
        base_section["floor_value"] = args.floor_value
    if args.morphology is not None:
        base_section["morphology"] = args.morphology
    if args.invert:
        base_section["invert"] = True
    if args.no_invert:
        base_section["invert"] = False

    if args.allowed_chars is not None:
        base_section["allowed_chars"] = args.allowed_chars
    if args.clear_allowed_chars:
        base_section.pop("allowed_chars", None)

    if args.cache_mode is not None:
        base_section["cache_mode"] = args.cache_mode
    if args.sig_from_preprocessed:
        base_section["sig_from_preprocessed"] = True
    if args.no_sig_from_preprocessed:
        base_section["sig_from_preprocessed"] = False
    if args.sig_text_floor is not None:
        base_section["sig_text_floor"] = args.sig_text_floor
    if args.sig_max_spread is not None:
        base_section["sig_max_spread"] = args.sig_max_spread
    if args.sig_downscale is not None:
        base_section["sig_downscale"] = _parse_wh(args.sig_downscale)

    _print_section_preview(args.spec, args.target, args.rarity, base_section, target_section)

    if args.dry_run:
        print("\ndry-run: TOML not modified")
        return

    backup_path = config_path.with_name(config_path.name + args.backup_suffix)
    shutil.copy2(config_path, backup_path)
    _write_toml(config_path, data)
    print(f"\nupdated -> {config_path}")
    print(f"backup  -> {backup_path}")


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    _configure_logging(args.log_level)

    try:
        if args.command == "preview":
            cmd_preview(args)
        elif args.command == "sample":
            cmd_sample(args)
        elif args.command == "write":
            cmd_write(args)
        else:
            parser.error(f"Unsupported command: {args.command}")
    except Exception as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()