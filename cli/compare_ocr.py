"""
compare_ocr.py — raw-vs-preprocessed OCR comparison
====================================================

Run the same crop(s) through:

1. raw OCR
2. spec.preprocess(...) + OCR

When a sidecar ``.txt`` file is available, the script reports whether the
region spec improves edit distance against the expected text.

Examples
--------
Compare one crop against its sibling ``.txt`` file::

    uv run cli/compare_ocr.py \
        --spec echoes.fullStatsValue \
        --image captures/echo_stats_value_01.png

Batch-compare a folder of crops::

    uv run cli/compare_ocr.py \
        --spec echoes.echoName \
        --glob "captures/echo_names/*.png" \
        --rarity 5
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

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
from wuwa_inventory_kamera.scraping.ocr.region_specs import default_specs_path, load_specs_from_toml  # noqa: E402

logger = logging.getLogger("wuwa.compare_ocr")

_PROVIDER_MAP: dict[str, list[str]] = {
    "cpu": ["CPUExecutionProvider"],
    "dml": ["DmlExecutionProvider", "CPUExecutionProvider"],
}


def _configure_logging(level_name: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level_name.upper(), logging.INFO),
        format="%(levelname)s: %(message)s",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare raw OCR with spec-preprocessed OCR.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--config",
        default=str(default_specs_path()),
        help="Path to OCR region spec TOML.",
    )
    parser.add_argument("--spec", required=True, help="ROI key, e.g. echoes.fullStatsValue")
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
    parser.add_argument("--rarity", type=int, default=None, help="Optional rarity override.")
    parser.add_argument(
        "--provider",
        default="cpu",
        choices=sorted(_PROVIDER_MAP),
        help="ONNX Runtime provider.",
    )
    parser.add_argument(
        "--expected-suffix",
        default=".txt",
        help="Expected-text suffix relative to the image stem (default: .txt).",
    )
    parser.add_argument(
        "--save-dir",
        default=None,
        help="Optional directory for raw/preprocessed side-by-side panels.",
    )
    parser.add_argument(
        "--max-images",
        type=int,
        default=None,
        help="Optional limit for number of input images.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity.",
    )
    return parser


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


def _read_bgr(path: Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(path)
    return image


def _read_expected_text(path: Path, suffix: str) -> str | None:
    expected_path = path.with_suffix(suffix)
    if not expected_path.is_file():
        return None
    return expected_path.read_text(encoding="utf-8").strip()


def _make_backend(provider: str):
    return get_backend("rapidocr", onnx_providers=_PROVIDER_MAP[provider])


def _ocr_text(backend, rgb_image: np.ndarray, allowed_chars: str | None) -> str:
    tokens = backend.recognize(rgb_image)
    return tokens_to_string(tokens, allowedChars=allowed_chars).strip()


def _levenshtein(left: str, right: str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)

    prev = list(range(len(right) + 1))
    for i, left_ch in enumerate(left, start=1):
        current = [i]
        for j, right_ch in enumerate(right, start=1):
            insert_cost = current[j - 1] + 1
            delete_cost = prev[j] + 1
            replace_cost = prev[j - 1] + (left_ch != right_ch)
            current.append(min(insert_cost, delete_cost, replace_cost))
        prev = current
    return prev[-1]


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


def _panel_for_compare(raw_bgr: np.ndarray, processed_rgb: np.ndarray) -> np.ndarray:
    processed_bgr = cv2.cvtColor(processed_rgb, cv2.COLOR_RGB2BGR)
    if processed_bgr.shape[:2] != raw_bgr.shape[:2]:
        processed_bgr = cv2.resize(processed_bgr, (raw_bgr.shape[1], raw_bgr.shape[0]), interpolation=cv2.INTER_NEAREST)
    return np.hstack([
        _label_panel(raw_bgr, "raw"),
        _label_panel(processed_bgr, "preprocessed"),
    ])


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    _configure_logging(args.log_level)

    try:
        specs = load_specs_from_toml(args.config)
        spec = specs[args.spec]
    except KeyError as exc:
        logger.error("Unknown spec: %s", args.spec)
        raise SystemExit(1) from exc

    try:
        image_paths = _resolve_images(args.images, args.globs, max_images=args.max_images)
    except Exception as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from exc

    backend = _make_backend(args.provider)
    save_dir = Path(args.save_dir) if args.save_dir else None
    if save_dir is not None:
        save_dir.mkdir(parents=True, exist_ok=True)

    raw_total = 0
    processed_total = 0
    raw_exact = 0
    processed_exact = 0
    improved = 0
    worsened = 0
    unchanged = 0
    expected_count = 0

    print(f"spec={spec.roi_key} images={len(image_paths)} rarity={args.rarity}")
    for path in image_paths:
        bgr = _read_bgr(path)
        raw_rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        processed_rgb = spec.preprocess(bgr, rarity=args.rarity)

        raw_text = _ocr_text(backend, raw_rgb, spec.allowed_chars)
        processed_text = _ocr_text(backend, processed_rgb, spec.allowed_chars)
        expected = _read_expected_text(path, args.expected_suffix)

        print(f"\n{path.name}")
        print(f"  raw        : {raw_text!r}")
        print(f"  preprocessed: {processed_text!r}")

        if expected is not None:
            expected_count += 1
            raw_distance = _levenshtein(raw_text, expected)
            processed_distance = _levenshtein(processed_text, expected)
            raw_total += raw_distance
            processed_total += processed_distance
            raw_exact += int(raw_distance == 0)
            processed_exact += int(processed_distance == 0)

            if processed_distance < raw_distance:
                outcome = "improved"
                improved += 1
            elif processed_distance > raw_distance:
                outcome = "worsened"
                worsened += 1
            else:
                outcome = "unchanged"
                unchanged += 1

            print(f"  expected   : {expected!r}")
            print(
                f"  distances  : raw={raw_distance} preprocessed={processed_distance}  [{outcome}]"
            )

        if save_dir is not None:
            panel = _panel_for_compare(bgr, processed_rgb)
            out_path = save_dir / f"{path.stem}_compare.png"
            cv2.imwrite(str(out_path), panel)
            print(f"  preview    : {out_path}")

    if expected_count:
        print("\nSummary")
        print(f"  exact matches : raw={raw_exact}/{expected_count} preprocessed={processed_exact}/{expected_count}")
        print(f"  total distance: raw={raw_total} preprocessed={processed_total}")
        print(f"  outcomes      : improved={improved} worsened={worsened} unchanged={unchanged}")


if __name__ == "__main__":
    main()