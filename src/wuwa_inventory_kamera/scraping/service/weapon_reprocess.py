"""
wuwa_inventory_kamera.scraping.service.weapon_reprocess
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Shared service-mode reprocessing for previously captured raw weapon scans.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from .echo_capture_utils import ensure_bgr_image

logger = logging.getLogger('wuwa.weapon_reprocess')


def _resolve_debug_dir(scan, raw_base: str | Path | None) -> Path | None:
    full_path = getattr(scan, 'full_path', None)
    if full_path is not None:
        return Path(full_path).parent / 'debug'
    if raw_base is not None:
        return Path(raw_base) / f'weapon_{scan.index:04d}' / 'debug'
    return None


def _crop_roi(image: np.ndarray, roi) -> np.ndarray:
    return image[
        int(roi.y): int(roi.y + roi.h),
        int(roi.x): int(roi.x + roi.w),
    ]


def _placeholder_rank_crop() -> np.ndarray:
    # Preserve weapon routing in OcrService when the current layout does not
    # expose a measured rank ROI yet.
    return np.zeros((1, 1, 3), dtype=np.uint8)


def _write_weapon_debug_artifacts(
    scan,
    *,
    raw_base: str | Path | None,
    detected_rarity: int | None,
    name: np.ndarray,
    value: np.ndarray,
    rank: np.ndarray | None,
    equipped: np.ndarray | None,
) -> None:
    from .echo_reprocess import _write_region_debug_artifacts

    debug_dir = _resolve_debug_dir(scan, raw_base)
    if debug_dir is None:
        logger.warning(
            'Scan %d — write_debug requested but no raw directory could be resolved.',
            scan.index,
        )
        return

    _write_region_debug_artifacts(
        debug_dir,
        basename='name',
        roi_key='weapons.name',
        raw_bgr=name,
        rarity=detected_rarity,
    )
    _write_region_debug_artifacts(
        debug_dir,
        basename='level',
        roi_key='weapons.level',
        raw_bgr=value,
        rarity=None,
    )
    if rank is not None:
        _write_region_debug_artifacts(
            debug_dir,
            basename='rank',
            roi_key='weapons.rank',
            raw_bgr=rank,
            rarity=None,
        )
    if equipped is not None:
        _write_region_debug_artifacts(
            debug_dir,
            basename='equipped',
            roi_key='weapons.equipped',
            raw_bgr=equipped,
            rarity=None,
        )


def reprocess_weapon_scans_with_service(
    scans,
    providers: list[str],
    min_rarity: int,
    min_level: int,
    write_debug: bool,
    max_batch_size: int = 8,
    ocr_cache_path: str | Path | None = None,
    raw_base: str | Path | None = None,
) -> list[dict]:
    """Process raw weapon scans through the v2 OcrService pipeline."""
    from ...game.screen_info import ScreenInfo
    from ..scanning.echo_workflow import _rarity_from_capture_pixel
    from .captures import WeaponCapture
    from .ocr_service import OcrService

    weapons: list[dict] = []

    resolution = (
        f'{scans[0].screen_width}x{scans[0].screen_height}'
        if scans else None
    )
    with OcrService(
        providers=providers,
        min_rarity=min_rarity,
        min_level=min_level,
        weapon_min_rarity=min_rarity,
        weapon_min_level=min_level,
        max_batch_size=max_batch_size,
        ocr_cache_path=(
            str(ocr_cache_path)
            if ocr_cache_path is not None else None
        ),
        resolution=resolution,
        det_limit_side_len=32 * 8,
    ) as svc:
        futures = []
        for scan in scans:
            try:
                scan.load_images()
            except FileNotFoundError as exc:
                logger.error('Scan %d — images missing, skipping: %s', scan.index, exc)
                continue

            wi = ScreenInfo(scan.screen_width, scan.screen_height).weapons
            full_rgb = scan.full_screenshot

            name = ensure_bgr_image(_crop_roi(full_rgb, wi.name), source_space='rgb')
            value = ensure_bgr_image(_crop_roi(full_rgb, wi.level), source_space='rgb')

            detected_rarity: int | None = None
            if hasattr(wi, 'rarityColorPick'):
                rcp = wi.rarityColorPick
                detected_rarity, _, _ = _rarity_from_capture_pixel(
                    full_rgb[int(rcp.y), int(rcp.x)]
                )

            rank = None
            if hasattr(wi, 'rank'):
                rank = ensure_bgr_image(_crop_roi(full_rgb, wi.rank), source_space='rgb')
            else:
                rank = _placeholder_rank_crop()

            equipped = None
            if hasattr(wi, 'equipped'):
                equipped = ensure_bgr_image(
                    _crop_roi(full_rgb, wi.equipped),
                    source_space='rgb',
                )

            if write_debug:
                _write_weapon_debug_artifacts(
                    scan,
                    raw_base=raw_base,
                    detected_rarity=detected_rarity,
                    name=name,
                    value=value,
                    rank=rank,
                    equipped=equipped,
                )

            capture = WeaponCapture(
                index=scan.index,
                name=name,
                value=value,
                rank=rank,
                equipped=equipped,
                detected_rarity=detected_rarity,
            )
            futures.append((scan.index, svc.submit(capture)))

        for scan_index, future in futures:
            try:
                result = future.result()
            except Exception as exc:
                logger.exception(
                    'Scan %d — service error (%s): %r',
                    scan_index,
                    type(exc).__name__,
                    exc,
                )
                continue
            if result.data is not None:
                weapons.append(result.data)
            else:
                logger.debug('Scan %d — rejected', scan_index)

    return weapons