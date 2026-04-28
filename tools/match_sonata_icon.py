"""
wuwa_inventory_kamera.cli.match_sonata_icon
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Match a scanned in-game sonata icon against the reference wiki PNGs
in ``assets/IconS/``.

The sonata icons are circular.  Scanned templates contain noise outside
the circle, and may be stored with BGR byte order (non-standard for PNG).

Pipeline
--------
1. Load the per-resolution config from ``config/sonata_icon_resolutions.json``
   (falls back to proportional scaling from the built-in 1920×1080 values).
2. Load the scanned icon (e.g. 23×24 px at 1080p).
3. Use saved or freshly-detected sub-pixel circle parameters (centre, radius).
   Detection uses upscaled Canny edges and iterative least-squares circle fit.
4. Create a smooth circular mask from the circle parameters.
5. Load RGBA reference icons from ``assets/IconS/``.
6. Scale each reference down to the scan dimensions (``INTER_AREA``).
7. Convert colour spaces so that both images are BGR before comparison.
   Both channel orderings are tried automatically and the better one wins.
8. Compare using a combined score: NCC − λ × normalised mean-colour distance.
   NCC alone is colour-blind (it subtracts the global mean before
   correlating). Adding a colour-distance penalty prevents near-grayscale
   references from winning via spurious structure correlation, and
   disambiguates structurally similar icons by their actual hue.

Resolution config
-----------------
Icon crop bounds and circle parameters vary by screen resolution.  The file
``config/sonata_icon_resolutions.json`` stores one entry per ``"WxH"`` key::

    {
      "1920x1080": {
        "x1": 1442, "y1": 316, "x2": 1465, "y2": 340,
        "circle_cx": 10.66, "circle_cy": 12.35, "circle_radius": 9.23
      }
    }

Calibrate a new resolution by running with ``--save-circle`` once.

Usage
-----
::

    uv run python -m wuwa_inventory_kamera.cli.match_sonata_icon \\
        --resolution 1920x1080 \\
        --icon captures/echo_0000/sonata.png

    # Detect circle and save it to the config for future runs:
    uv run python -m wuwa_inventory_kamera.cli.match_sonata_icon \\
        --resolution 2560x1440 \\
        --icon captures/echo_0000/sonata.png \\
        --save-circle

If the supplied image matches the full screen resolution it is auto-cropped
to the icon region.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_REFS_DIR = _PROJECT_ROOT / "assets" / "IconS"
_CONFIG_FILE = _PROJECT_ROOT / "config" / "sonata_icon_resolutions.json"

logger = logging.getLogger("wuwa.match_sonata_icon")

# How strongly mean-colour distance penalises the NCC score.
# score = NCC − _COLOR_PENALTY × colour_dist_norm
# where colour_dist_norm = ||mean_scan − mean_ref||₂ / (√3 × 255) ∈ [0, 1].
# Calibrated so that ≈20-unit cross-channel mean difference cancels a 0.07
# NCC advantage (the observed moonlitclouds / pactofneonlightleap failure
# margin).
_COLOR_PENALTY: float = 1.5


# ---------------------------------------------------------------------------
# Per-resolution configuration
# ---------------------------------------------------------------------------

@dataclass
class ResolutionConfig:
    """Icon-region and circle parameters for one screen resolution.

    Crop bounds (x1, y1, x2, y2) are in screen pixels.
    Circle parameters are in *crop-image* pixels; any field set to None
    means the value should be detected from the scan at runtime.
    """
    x1: int
    y1: int
    x2: int
    y2: int
    circle_cx: float | None = None
    circle_cy: float | None = None
    circle_radius: float | None = None

    @property
    def crop_w(self) -> int:
        return self.x2 - self.x1

    @property
    def crop_h(self) -> int:
        return self.y2 - self.y1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ResolutionConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# Built-in reference config at 1920×1080.  Other resolutions fall back to
# proportional scaling from this entry when not found in the config file.
_REF_KEY = "1920x1080"
_BUILTIN_CONFIGS: dict[str, ResolutionConfig] = {
    "1920x1080": ResolutionConfig(
        x1=1442, y1=316, x2=1465, y2=340,
        circle_cx=12.55, circle_cy=12.65, circle_radius=11.05,
    ),
}


def _res_key(w: int, h: int) -> str:
    return f"{w}x{h}"


def _scale_config(base: ResolutionConfig, base_w: int, base_h: int,
                  target_w: int, target_h: int) -> ResolutionConfig:
    """Proportionally scale crop bounds from *base* to *target* dimensions.

    Circle parameters are not scaled because they live in crop-image space
    and should be measured (or loaded) per resolution.
    """
    sx = target_w / base_w
    sy = target_h / base_h
    return ResolutionConfig(
        x1=round(base.x1 * sx),
        y1=round(base.y1 * sy),
        x2=round(base.x2 * sx),
        y2=round(base.y2 * sy),
    )


def load_resolution_config(
    screen_w: int,
    screen_h: int,
    *,
    config_file: Path = _CONFIG_FILE,
) -> ResolutionConfig:
    """Return the ``ResolutionConfig`` for the given screen resolution.

    Lookup order:
    1. ``config_file`` (user-saved overrides / circle params).
    2. Built-in table (currently only 1920×1080).
    3. Proportional scaling from the 1920×1080 reference.
    """
    key = _res_key(screen_w, screen_h)

    # 1 – user JSON file
    if config_file.is_file():
        try:
            with config_file.open(encoding="utf-8") as fh:
                data: dict[str, Any] = json.load(fh)
            if key in data:
                cfg = ResolutionConfig.from_dict(data[key])
                logger.debug("Loaded resolution config from %s: %s", config_file, cfg)
                return cfg
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read %s: %s", config_file, exc)

    # 2 – built-in table
    if key in _BUILTIN_CONFIGS:
        cfg = _BUILTIN_CONFIGS[key]
        logger.debug("Using built-in resolution config for %s.", key)
        return cfg

    # 3 – scale from reference
    ref_key, ref_w, ref_h = _REF_KEY, 1920, 1080
    base = _BUILTIN_CONFIGS[ref_key]
    cfg = _scale_config(base, ref_w, ref_h, screen_w, screen_h)
    logger.warning(
        "No config for %s — scaled proportionally from %s. "
        "Run with --save-circle to calibrate.",
        key, ref_key,
    )
    return cfg


def save_circle_to_config(
    screen_w: int,
    screen_h: int,
    cx: float,
    cy: float,
    radius: float,
    *,
    config_file: Path = _CONFIG_FILE,
) -> None:
    """Persist the detected circle parameters for *WxH* to ``config_file``.

    Merges with any existing crop-bound values already in the file so that
    previously saved data is never lost.
    """
    key = _res_key(screen_w, screen_h)

    data: dict[str, Any] = {}
    if config_file.is_file():
        try:
            with config_file.open(encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read %s for update: %s", config_file, exc)

    # Start from the current config so crop bounds are preserved.
    existing = load_resolution_config(screen_w, screen_h, config_file=config_file)
    entry = existing.to_dict()
    entry["circle_cx"] = round(cx, 4)
    entry["circle_cy"] = round(cy, 4)
    entry["circle_radius"] = round(radius, 4)
    data[key] = entry

    config_file.parent.mkdir(parents=True, exist_ok=True)
    with config_file.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    logger.info("Saved circle config for %s → %s", key, config_file)


# ---------------------------------------------------------------------------
# Circle detection
# ---------------------------------------------------------------------------

def _fit_circle_kasa(points: np.ndarray) -> tuple[float, float, float]:
    """Algebraic least-squares circle fit (Kåsa method).

    *points*: N×2 ``(x, y)`` array.
    Returns ``(cx, cy, radius)``.
    """
    x, y = points[:, 0], points[:, 1]
    A = np.column_stack([x, y, np.ones(len(x))])
    b = x ** 2 + y ** 2
    result, _, _, _ = np.linalg.lstsq(A, b, rcond=None)
    cx = result[0] / 2.0
    cy = result[1] / 2.0
    r = np.sqrt(max(result[2] + cx ** 2 + cy ** 2, 0.0))
    return cx, cy, r


def detect_circle(
    image_bgr: np.ndarray,
    *,
    upscale: int = 8,
) -> tuple[float, float, float]:
    """Detect the circular icon region with sub-pixel precision.

    The image is upscaled by *upscale*, edges are extracted with Canny,
    and a least-squares circle is fitted with iterative outlier rejection
    to ignore interior texture edges.

    Returns ``(cx, cy, radius)`` in the **original** pixel space.
    """
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape

    big = cv2.resize(
        gray, (w * upscale, h * upscale), interpolation=cv2.INTER_CUBIC,
    )
    blurred = cv2.GaussianBlur(big, (3, 3), 0.8)
    edges = cv2.Canny(blurred, 30, 90)

    # (x, y) coordinates of edge pixels in upscaled space.
    pts = np.column_stack(np.where(edges > 0))[:, ::-1].astype(np.float64)

    if len(pts) < 10:
        logger.warning(
            "Few edge points (%d); falling back to centred circle.", len(pts),
        )
        return w / 2.0, h / 2.0, min(w, h) / 2.0 - 0.5

    # Iterative circle fit: reject points that are not on the boundary ring.
    for _ in range(3):
        cx, cy, r = _fit_circle_kasa(pts)
        dist = np.sqrt((pts[:, 0] - cx) ** 2 + (pts[:, 1] - cy) ** 2)
        # Keep only points within 2 original-space pixels of the fitted ring.
        inliers = np.abs(dist - r) < 2.0 * upscale
        if inliers.sum() < 10:
            break
        pts = pts[inliers]

    cx /= upscale
    cy /= upscale
    r /= upscale
    # Sanity-clamp so the radius doesn't exceed the image.
    r = min(r, np.hypot(w, h) / 2.0)

    logger.debug("Detected circle: centre=(%.2f, %.2f) radius=%.2f", cx, cy, r)
    return cx, cy, r


# ---------------------------------------------------------------------------
# Circular masking
# ---------------------------------------------------------------------------

def make_circle_mask(
    h: int,
    w: int,
    cx: float,
    cy: float,
    r: float,
) -> np.ndarray:
    """Smooth (anti-aliased) circle mask with ~1 px soft edge.

    Returns a ``uint8`` image in [0, 255].
    """
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    dist = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2)
    mask = np.clip(r - dist + 0.5, 0.0, 1.0)
    return (mask * 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# Reference loading
# ---------------------------------------------------------------------------

def load_references(
    refs_dir: Path,
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Load reference RGBA icons.

    Returns ``{stem: (bgr, alpha)}`` where *bgr* is 3-channel and *alpha*
    is single-channel ``uint8``.  Icons without alpha get a solid mask.
    """
    refs: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for p in sorted(refs_dir.glob("*.png")):
        img = cv2.imread(str(p), cv2.IMREAD_UNCHANGED)
        if img is None or img.ndim != 3:
            continue
        if img.shape[2] == 4:
            # cv2 loads RGBA PNGs as BGRA.
            bgr = img[:, :, :3]
            alpha = img[:, :, 3]
        else:
            bgr = img
            alpha = np.full(img.shape[:2], 255, dtype=np.uint8)
        refs[p.stem] = (bgr, alpha)
    if not refs:
        logger.error("No reference PNGs found in %s", refs_dir)
        sys.exit(1)
    logger.info("Loaded %d reference icons from %s", len(refs), refs_dir)
    return refs


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def _ncc_masked(
    img1: np.ndarray,
    img2: np.ndarray,
    mask: np.ndarray,
) -> float:
    """Normalised cross-correlation computed only over masked pixels.

    Both images must have the same shape.  *mask* is single-channel uint8;
    pixels with ``mask > 127`` are included.
    """
    m = mask.ravel() > 127
    if not m.any():
        return 0.0
    c = img1.shape[2] if img1.ndim == 3 else 1
    a = img1.reshape(-1, c)[m].ravel().astype(np.float64)
    b = img2.reshape(-1, c)[m].ravel().astype(np.float64)
    a -= a.mean()
    b -= b.mean()
    denom = np.sqrt(np.dot(a, a) * np.dot(b, b))
    if denom < 1e-10:
        return 0.0
    return float(np.dot(a, b) / denom)


def match_icon(
    scan_bgr: np.ndarray,
    scan_mask: np.ndarray,
    references: dict[str, tuple[np.ndarray, np.ndarray]],
) -> list[tuple[str, float]]:
    """Compare *scan_bgr* against every reference icon.

    Each reference is scaled to the scan dimensions.  The combined mask is
    the intersection of *scan_mask* (from circle detection) and the
    reference's alpha channel.

    The combined score is::

        score = NCC − _COLOR_PENALTY × colour_dist_norm

    where ``colour_dist_norm = ||mean(scan_masked) − mean(ref_masked)||₂
    / (√3 × 255)`` is in [0, 1].  This prevents near-grayscale or
    hue-different references from winning on structure alone.

    Returns a list of ``(name, score)`` sorted best-first.
    """
    h, w = scan_bgr.shape[:2]
    scan_mask_bin = np.where(scan_mask > 127, np.uint8(255), np.uint8(0))

    results: list[tuple[str, float]] = []
    for name, (ref_bgr, ref_alpha) in references.items():
        # Scale reference to scan dimensions.
        ref_scaled = cv2.resize(ref_bgr, (w, h), interpolation=cv2.INTER_AREA)
        alpha_scaled = cv2.resize(ref_alpha, (w, h), interpolation=cv2.INTER_AREA)

        # Combined mask: intersection of scan circle and reference alpha.
        ref_mask_bin = np.where(alpha_scaled > 127, np.uint8(255), np.uint8(0))
        combined = cv2.bitwise_and(scan_mask_bin, ref_mask_bin)

        ncc = _ncc_masked(scan_bgr, ref_scaled, combined)

        # Colour-distance penalty — orthogonal information to NCC.
        m = combined.ravel() > 127
        if m.any():
            scan_mean = scan_bgr.reshape(-1, 3)[m].mean(axis=0).astype(np.float64)
            ref_mean = ref_scaled.reshape(-1, 3)[m].mean(axis=0).astype(np.float64)
            colour_dist_norm = np.linalg.norm(scan_mean - ref_mean) / (np.sqrt(3) * 255)
        else:
            colour_dist_norm = 0.0

        score = ncc - _COLOR_PENALTY * colour_dist_norm
        results.append((name, score))

    results.sort(key=lambda x: x[1], reverse=True)
    return results


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

def _parse_resolution(s: str) -> tuple[int, int]:
    parts = s.lower().split("x")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(f"Expected WIDTHxHEIGHT, got {s!r}")
    try:
        return int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Expected WIDTHxHEIGHT, got {s!r}"
        ) from exc


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="match_sonata_icon",
        description="Match a scanned sonata icon against reference wiki PNGs.",
    )
    parser.add_argument(
        "--resolution",
        required=True,
        type=_parse_resolution,
        metavar="WxH",
        help="Screen resolution when the icon was captured (e.g. 1920x1080).",
    )
    parser.add_argument(
        "--icon",
        required=True,
        type=Path,
        metavar="PATH",
        help="Path to the scanned sonata icon PNG (or a full screenshot).",
    )
    parser.add_argument(
        "--refs-dir",
        type=Path,
        default=_REFS_DIR,
        metavar="PATH",
        help="Directory with reference PNGs (default: assets/IconS).",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=5,
        metavar="N",
        help="Number of top matches to display (default: 5).",
    )
    parser.add_argument(
        "--save-circle",
        action="store_true",
        help="Detect circle parameters from this scan and save them to the "
             "resolution config file for future use.",
    )
    parser.add_argument(
        "--config-file",
        type=Path,
        default=_CONFIG_FILE,
        metavar="PATH",
        help=f"Resolution config JSON file (default: {_CONFIG_FILE}).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s: %(message)s",
    )

    icon_path: Path = args.icon
    if not icon_path.is_file():
        logger.error("Icon not found: %s", icon_path)
        sys.exit(1)

    screen_w, screen_h = args.resolution
    res_cfg = load_resolution_config(screen_w, screen_h, config_file=args.config_file)
    logger.info(
        "Resolution %dx%d → icon crop (%d,%d)-(%d,%d)  [%d×%d px]",
        screen_w, screen_h,
        res_cfg.x1, res_cfg.y1, res_cfg.x2, res_cfg.y2,
        res_cfg.crop_w, res_cfg.crop_h,
    )

    # --- Load scan --------------------------------------------------------
    # cv2.imread returns BGR for standard PNGs.
    scan = cv2.imread(str(icon_path), cv2.IMREAD_COLOR)
    if scan is None:
        logger.error("Could not read: %s", icon_path)
        sys.exit(1)

    sh, sw = scan.shape[:2]

    # Auto-crop if the input looks like a full screenshot.
    if abs(sw - screen_w) <= 2 and abs(sh - screen_h) <= 2:
        scan = scan[res_cfg.y1:res_cfg.y2, res_cfg.x1:res_cfg.x2]
        sh, sw = scan.shape[:2]
        logger.info("Auto-cropped icon from full screenshot: %d×%d", sw, sh)

    logger.info(
        "Scan: %d×%d  (expected ~%d×%d at %dx%d)",
        sw, sh, res_cfg.crop_w, res_cfg.crop_h, screen_w, screen_h,
    )

    # --- Determine circle parameters -------------------------------------
    if res_cfg.circle_cx is not None and not args.save_circle:
        # Use calibrated values from config.
        cx = res_cfg.circle_cx
        cy = res_cfg.circle_cy
        radius = res_cfg.circle_radius
        logger.info(
            "Using saved circle: centre=(%.2f, %.2f)  radius=%.2f", cx, cy, radius,
        )
    else:
        # Detect from this scan image.
        cx, cy, radius = detect_circle(scan)
        if args.save_circle:
            save_circle_to_config(
                screen_w, screen_h, cx, cy, radius,
                config_file=args.config_file,
            )

    print(f"Circle: centre=({cx:.2f}, {cy:.2f})  radius={radius:.2f}")
    print(f"  Offset from crop centre: dx={cx - sw / 2:.2f}  dy={cy - sh / 2:.2f}")

    # --- Create mask ------------------------------------------------------
    mask = make_circle_mask(sh, sw, cx, cy, radius)

    # --- Load references --------------------------------------------------
    references = load_references(args.refs_dir)

    # --- Match (try both channel orderings) -------------------------------
    # Scanned PNGs from the capture pipeline may be stored with BGR byte
    # order on disk (non-standard).  When cv2.imread loads such a file it
    # interprets the bytes as RGB and swaps to BGR, effectively producing
    # an RGB image in memory.  Reference wiki PNGs are standard and load
    # correctly as BGR.  We try both orderings and pick the one that gives
    # a higher best score.
    results_native = match_icon(scan, mask, references)
    scan_swapped = scan[:, :, ::-1].copy()
    results_swapped = match_icon(scan_swapped, mask, references)

    if results_swapped[0][1] > results_native[0][1]:
        results = results_swapped
        logger.info(
            "Channel swap (BGR<->RGB) improved matching — using swapped channels."
        )
    else:
        results = results_native

    # --- Display ----------------------------------------------------------
    top_n = min(args.top_n, len(results))
    print(f"\nTop {top_n} matches:")
    for i, (name, score) in enumerate(results[:top_n], 1):
        marker = "  <-- best" if i == 1 else ""
        print(f"  {i:2d}. {name:<32s}  {score:.4f}{marker}")

    best_name, best_score = results[0]
    print(f"\nResult: {best_name} (score={best_score:.4f})")


if __name__ == "__main__":
    main()
