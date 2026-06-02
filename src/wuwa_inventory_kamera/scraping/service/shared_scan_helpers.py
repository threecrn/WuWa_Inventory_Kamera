"""
wuwa_inventory_kamera.scraping.service.shared_scan_helpers
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Shared low-level helpers used by both live scan workflows and raw-session
reprocess code paths.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from ... import imgio
from .echo_capture_utils import ensure_bgr_image

logger = logging.getLogger(__name__)


# Reference colors from game_roi.py comments, expressed in BGR order
# as used by OpenCV / mss captures. Rarity 1 is calibrated from the
# new UI's neutral-gray rarity pick because the original comments only
# cover rarities 2-5.
#   rarity 5: gold   - (R=1.00, G=0.98, B=0.69)
#   rarity 4: purple - (R=0.91, G=0.63, B=1.00)
#   rarity 3: blue   - (R=0.60, G=0.60, B=1.00)
#   rarity 2: green  - (R=0.60, G=1.00, B=0.60)
#   rarity 1: gray   - (R=218, G=222, B=225)
_RARITY_PIXEL_COLORS_BGR: dict[int, np.ndarray] = {
    5: np.array([176, 250, 255], dtype=np.int32),
    4: np.array([255, 161, 232], dtype=np.int32),
    3: np.array([255, 153, 153], dtype=np.int32),
    2: np.array([153, 255, 153], dtype=np.int32),
    1: np.array([225, 222, 218], dtype=np.int32),
}

_DEBUG_REGION_SPECS: tuple[tuple[str, str], ...] = (
    ('echoes.echoName', 'echo_name'),
    ('echoes.fullStatsName', 'stats_name'),
    ('echoes.fullStatsValue', 'stats_value'),
    ('echoes.equipped', 'equipped'),
    ('echoes.level', 'level'),
)


def _closest_rarity_from_bgr_pixel(pixel: np.ndarray) -> tuple[int, float]:
    """Return the closest rarity and squared distance for a BGR pixel."""
    px = pixel[:3].astype(np.int32)
    best_rarity = 1
    best_dist = float('inf')
    for rarity, ref in _RARITY_PIXEL_COLORS_BGR.items():
        dist = float(np.sum((px - ref) ** 2))
        if dist < best_dist:
            best_dist = dist
            best_rarity = rarity
    return best_rarity, best_dist


def _rarity_from_bgr_pixel(pixel: np.ndarray) -> int:
    """Return the rarity (1-5) whose reference color is closest to *pixel* (BGR)."""
    return _closest_rarity_from_bgr_pixel(pixel)[0]


def _rarity_from_rgb_pixel(pixel: np.ndarray) -> int:
    """Return the rarity for a pixel stored in RGB channel order."""
    return _rarity_from_bgr_pixel(pixel[::-1])


def _rarity_from_capture_pixel(pixel: np.ndarray) -> tuple[int, str, float]:
    """Return the closest rarity for a capture pixel, tolerating BGR or RGB input."""
    rarity_bgr, dist_bgr = _closest_rarity_from_bgr_pixel(pixel)
    rarity_rgb, dist_rgb = _closest_rarity_from_bgr_pixel(pixel[::-1])
    if dist_bgr <= dist_rgb:
        return rarity_bgr, 'BGR', dist_bgr
    return rarity_rgb, 'RGB', dist_rgb


def _resolve_echo_debug_dir(scan, raw_base: str | Path | None) -> Path | None:
    full_path = getattr(scan, 'full_path', None)
    if full_path is not None:
        return Path(full_path).parent / 'debug'
    if raw_base is not None:
        return Path(raw_base) / f'echo_{scan.index:04d}' / 'debug'
    return None


def _to_debug_image(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    return imgio.convert_color(image, imgio.ColorCode.RGB2BGR)


def _write_region_debug_artifacts(
    debug_dir: Path,
    *,
    basename: str,
    roi_key: str,
    raw_bgr: np.ndarray,
    rarity: int | None,
) -> None:
    from ..ocr.region_specs import get_spec

    debug_dir.mkdir(parents=True, exist_ok=True)
    imgio.imwrite(str(debug_dir / f'{basename}.png'), raw_bgr)

    spec = get_spec(roi_key)
    if spec is None:
        return

    preprocessed = spec.preprocess(raw_bgr, rarity=rarity)
    signature = spec._image_for_signature(raw_bgr, rarity)

    imgio.imwrite(
        str(debug_dir / f'{basename}_preprocessed.png'),
        _to_debug_image(preprocessed.ocr_rgb),
    )
    imgio.imwrite(str(debug_dir / f'{basename}_signature.png'), signature)


def _write_echo_debug_artifacts(
    scan,
    *,
    raw_base: str | Path | None,
    full_screenshot_space: str,
    detected_rarity: int | None,
    echo_name: np.ndarray | None,
    equipped: np.ndarray | None,
    level: np.ndarray,
    stats_name: np.ndarray,
    stats_value: np.ndarray,
) -> None:
    debug_dir = _resolve_echo_debug_dir(scan, raw_base)
    if debug_dir is None:
        logger.warning(
            'Scan %d - write_debug requested but no raw directory could be resolved.',
            scan.index,
        )
        return

    region_images: dict[str, np.ndarray | None] = {
        'echo_name': echo_name,
        'equipped': equipped,
        'level': level,
        'stats_name': stats_name,
        'stats_value': stats_value,
    }
    region_source_spaces = {
        'echo_name': 'bgr',
        'equipped': 'bgr',
        'level': full_screenshot_space,
        'stats_name': full_screenshot_space,
        'stats_value': full_screenshot_space,
    }

    for roi_key, basename in _DEBUG_REGION_SPECS:
        raw_image = region_images[basename]
        if raw_image is None:
            continue
        _write_region_debug_artifacts(
            debug_dir,
            basename=basename,
            roi_key=roi_key,
            raw_bgr=ensure_bgr_image(
                raw_image,
                source_space=region_source_spaces[basename],
            ),
            rarity=detected_rarity,
        )