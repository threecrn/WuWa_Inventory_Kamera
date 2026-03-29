"""
wuwa_inventory_kamera.cli.detect_sonata_icon
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Detect the sonata set of an echo by matching the small circular sonata icon
on the echo card against pre-built reference templates.

Two modes
---------
``build``
    Extract median reference templates from a directory of labeled raw echo
    screenshots (each ``echo_NNNN/`` folder must contain ``full.png`` and
    ``debug/ocr.json`` with a ``sonata.matched`` field).  Saves one PNG
    per sonata key into *--templates-dir*.

``detect`` *(default)*
    Load the pre-built templates from *--templates-dir*, crop the sonata
    icon region from one or more screenshots, and print the best match.

Icon region (1920 x 1080)
--------------------------
The sonata icon is a small circle at pixel coordinates
**(1442, 316)** → **(1465, 340)** (23 × 24 px).  For other resolutions
the coordinates are scaled proportionally.

Usage examples
--------------
Build templates from labeled data::

    python -m wuwa_inventory_kamera.cli.detect_sonata_icon build \\
        --raw-dir K:/wuwa/export/current/raw

Detect a single screenshot::

    python -m wuwa_inventory_kamera.cli.detect_sonata_icon detect \\
        --screenshot path/to/full.png

Detect every echo in a raw directory::

    python -m wuwa_inventory_kamera.cli.detect_sonata_icon detect \\
        --raw-dir K:/wuwa/export/current/raw
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

# ---------------------------------------------------------------------------
# Bootstrap: make sure the project root is on sys.path so that legacy
# packages (``scraping``, ``properties``, …) are importable when running
# as  ``python -m wuwa_inventory_kamera.cli.detect_sonata_icon``  or
# directly as  ``python src/wuwa_inventory_kamera/cli/detect_sonata_icon.py``.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
_ASSETS_DIR = _PROJECT_ROOT / "assets" / "IconS"
_TEMPLATES_DIR = _ASSETS_DIR / "templates"

# Reference icon crop coordinates at 1920 × 1080.
_REF_WIDTH = 1920
_REF_HEIGHT = 1080
_ICON_X1 = 1442
_ICON_Y1 = 316
_ICON_X2 = 1465
_ICON_Y2 = 340

logger = logging.getLogger("wuwa.detect_sonata_icon")


# ───────────────────────────── helpers ─────────────────────────────────────

def _normalize_key(name: str) -> str:
    """Lowercase, strip underscores / spaces / apostrophes."""
    return re.sub(r"[_\s']", "", name).lower()


def _icon_roi(width: int, height: int) -> tuple[int, int, int, int]:
    """Return (y1, y2, x1, x2) scaled to the given resolution."""
    sx = width / _REF_WIDTH
    sy = height / _REF_HEIGHT
    return (
        round(_ICON_Y1 * sy),
        round(_ICON_Y2 * sy),
        round(_ICON_X1 * sx),
        round(_ICON_X2 * sx),
    )


def _circular_mask(h: int, w: int) -> np.ndarray:
    mask = np.zeros((h, w), dtype=np.uint8)
    cv2.circle(mask, (w // 2, h // 2), min(w // 2, h // 2), 255, -1)
    return mask


# ───────────────────────── template loading ────────────────────────────────

def load_templates(templates_dir: Path) -> dict[str, np.ndarray]:
    """Load ``{sonata_key: bgr_image}`` from *templates_dir*."""
    templates: dict[str, np.ndarray] = {}
    for p in sorted(templates_dir.glob("*.png")):
        img = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if img is not None:
            templates[p.stem] = img
    if not templates:
        logger.error("No template PNGs found in %s", templates_dir)
        sys.exit(1)
    logger.info("Loaded %d reference templates from %s", len(templates), templates_dir)
    return templates


# ──────────────────────── matching engine ──────────────────────────────────

def match_icon(
    crop: np.ndarray,
    templates: dict[str, np.ndarray],
    mask: np.ndarray | None = None,
) -> tuple[str, float]:
    """
    Return ``(best_sonata_key, score)`` by comparing *crop* against every
    template with normalised cross-correlation (TM_CCOEFF_NORMED).
    """
    h, w = crop.shape[:2]
    if mask is None:
        mask = _circular_mask(h, w)
    crop_masked = cv2.bitwise_and(crop, crop, mask=mask)

    best_name: str | None = None
    best_score = -1.0
    for name, tmpl in templates.items():
        # Resize template to match crop if needed
        if tmpl.shape[:2] != (h, w):
            tmpl = cv2.resize(tmpl, (w, h), interpolation=cv2.INTER_AREA)
        tmpl_masked = cv2.bitwise_and(tmpl, tmpl, mask=mask)
        score: float = cv2.matchTemplate(
            crop_masked, tmpl_masked, cv2.TM_CCOEFF_NORMED,
        )[0][0]
        if score > best_score:
            best_score = score
            best_name = name

    assert best_name is not None
    return best_name, best_score


def detect_from_screenshot(
    screenshot: np.ndarray,
    templates: dict[str, np.ndarray],
    width: int | None = None,
    height: int | None = None,
) -> tuple[str, float]:
    """
    Crop the sonata icon from a full BGR *screenshot* and return the best
    matching ``(sonata_key, score)`` pair.
    """
    h = height if height is not None else screenshot.shape[0]
    w = width if width is not None else screenshot.shape[1]
    y1, y2, x1, x2 = _icon_roi(w, h)
    crop = screenshot[y1:y2, x1:x2]
    return match_icon(crop, templates)


# ──────────────────────── BUILD sub-command ────────────────────────────────

def cmd_build(args: argparse.Namespace) -> None:
    raw_dir = Path(args.raw_dir)
    templates_dir = Path(args.templates_dir)

    if not raw_dir.is_dir():
        logger.error("--raw-dir does not exist: %s", raw_dir)
        sys.exit(1)

    crops_by_sonata: dict[str, list[np.ndarray]] = defaultdict(list)

    for echo_dir in sorted(raw_dir.iterdir()):
        if not echo_dir.is_dir() or not echo_dir.name.startswith("echo_"):
            continue
        full_path = echo_dir / "full.png"
        ocr_path = echo_dir / "debug" / "ocr.json"
        if not full_path.exists() or not ocr_path.exists():
            continue

        with open(ocr_path, encoding="utf-8") as f:
            ocr = json.load(f)
        sonata_data = ocr.get("sonata", {})
        matched = sonata_data.get("matched") if isinstance(sonata_data, dict) else None
        if not matched:
            continue

        full = cv2.imread(str(full_path), cv2.IMREAD_COLOR)
        if full is None:
            continue
        h, w = full.shape[:2]
        y1, y2, x1, x2 = _icon_roi(w, h)
        crop = full[y1:y2, x1:x2]
        key = _normalize_key(matched)
        crops_by_sonata[key].append(crop)

    if not crops_by_sonata:
        logger.error("No labeled echo screenshots found in %s", raw_dir)
        sys.exit(1)

    templates_dir.mkdir(parents=True, exist_ok=True)
    for name, crops in sorted(crops_by_sonata.items()):
        stack = np.stack(crops, axis=0).astype(np.float32)
        median = np.median(stack, axis=0).astype(np.uint8)
        dest = templates_dir / f"{name}.png"
        cv2.imwrite(str(dest), median)

    logger.info(
        "Built %d templates (%d total samples) → %s",
        len(crops_by_sonata),
        sum(len(c) for c in crops_by_sonata.values()),
        templates_dir,
    )
    for name, crops in sorted(crops_by_sonata.items()):
        logger.info("  %-30s %4d samples", name, len(crops))


# ─────────────────────── DETECT sub-command ────────────────────────────────

def cmd_detect(args: argparse.Namespace) -> None:
    templates = load_templates(Path(args.templates_dir))

    # Collect screenshots to process
    screenshots: list[tuple[str, Path]] = []

    if args.screenshot:
        p = Path(args.screenshot)
        if not p.is_file():
            logger.error("Screenshot not found: %s", p)
            sys.exit(1)
        screenshots.append((p.stem, p))

    elif args.raw_dir:
        raw_dir = Path(args.raw_dir)
        if not raw_dir.is_dir():
            logger.error("--raw-dir does not exist: %s", raw_dir)
            sys.exit(1)
        for echo_dir in sorted(raw_dir.iterdir()):
            if not echo_dir.is_dir() or not echo_dir.name.startswith("echo_"):
                continue
            full_path = echo_dir / "full.png"
            if full_path.exists():
                screenshots.append((echo_dir.name, full_path))

    if not screenshots:
        logger.error("No screenshots to process.")
        sys.exit(1)

    # Optionally load ground truth for accuracy reporting
    correct = total = 0
    has_truth = False

    for label, path in screenshots:
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if img is None:
            logger.warning("Could not read %s — skipping.", path)
            continue

        name, score = detect_from_screenshot(img, templates)

        # Check ground truth if available
        ocr_path = path.parent / "debug" / "ocr.json"
        expected = None
        if ocr_path.exists():
            with open(ocr_path, encoding="utf-8") as f:
                ocr = json.load(f)
            sonata_data = ocr.get("sonata", {})
            expected_raw = sonata_data.get("matched") if isinstance(sonata_data, dict) else None
            if expected_raw:
                expected = _normalize_key(expected_raw)
                has_truth = True

        if expected:
            ok = name == expected
            if ok:
                correct += 1
            total += 1
            tag = "OK" if ok else "MISMATCH"
            print(f"{label}: detected={name:30s} expected={expected:30s} score={score:.4f}  [{tag}]")
        else:
            print(f"{label}: detected={name:30s} score={score:.4f}")

    if has_truth and total > 0:
        print(f"\nAccuracy: {correct}/{total} ({100 * correct / total:.1f}%)")


# ──────────────────────── CLI entry point ──────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="detect_sonata_icon",
        description="Detect echo sonata set from the small icon on the echo card.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )

    sub = parser.add_subparsers(dest="command")

    # ── build ──────────────────────────────────────────────────────────────
    build_p = sub.add_parser(
        "build",
        help="Build median reference templates from labeled screenshots.",
    )
    build_p.add_argument(
        "--raw-dir", required=True, metavar="PATH",
        help="Directory containing echo_NNNN/ folders with full.png + debug/ocr.json.",
    )
    build_p.add_argument(
        "--templates-dir", default=str(_TEMPLATES_DIR), metavar="PATH",
        help=f"Output directory for template PNGs (default: {_TEMPLATES_DIR}).",
    )

    # ── detect ─────────────────────────────────────────────────────────────
    detect_p = sub.add_parser(
        "detect",
        help="Detect sonata from screenshot(s) using pre-built templates.",
    )
    source = detect_p.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--screenshot", metavar="PATH",
        help="Path to a single full screenshot (BGR PNG).",
    )
    source.add_argument(
        "--raw-dir", metavar="PATH",
        help="Directory containing echo_NNNN/ folders to batch-detect.",
    )
    detect_p.add_argument(
        "--templates-dir", default=str(_TEMPLATES_DIR), metavar="PATH",
        help=f"Directory containing template PNGs (default: {_TEMPLATES_DIR}).",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s: %(message)s",
    )

    if args.command == "build":
        cmd_build(args)
    elif args.command == "detect":
        cmd_detect(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
