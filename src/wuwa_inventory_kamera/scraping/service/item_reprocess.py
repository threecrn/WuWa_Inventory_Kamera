"""
wuwa_inventory_kamera.scraping.service.item_reprocess
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Shared service-mode reprocessing for previously captured raw item scans.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

import numpy as np

from ...game.navigation import InventoryTab
from .echo_capture_utils import ensure_bgr_image
from .shared_scan_helpers import _write_region_debug_artifacts

logger = logging.getLogger('wuwa.item_reprocess')


def _prefix_for_tab(tab: InventoryTab) -> str:
    if tab == InventoryTab.DEV_ITEMS:
        return 'devItem'
    if tab == InventoryTab.RESOURCES:
        return 'resource'
    raise ValueError(f'Unsupported item tab: {tab!r}')


def _resolve_debug_dir(scan, raw_base: str | Path | None, *, tab: InventoryTab) -> Path | None:
    full_path = getattr(scan, 'full_path', None)
    if full_path is not None:
        return Path(full_path).parent / 'debug'
    if raw_base is not None:
        return Path(raw_base) / f'{_prefix_for_tab(tab)}_{scan.index:04d}' / 'debug'
    return None


def _crop_roi(image: np.ndarray, roi) -> np.ndarray:
    return image[
        int(roi.y): int(roi.y + roi.h),
        int(roi.x): int(roi.x + roi.w),
    ]


def _write_item_debug_artifacts(
    scan,
    *,
    raw_base: str | Path | None,
    tab: InventoryTab,
    detected_rarity: int | None,
    name: np.ndarray,
    value: np.ndarray,
) -> None:
    debug_dir = _resolve_debug_dir(scan, raw_base, tab=tab)
    if debug_dir is None:
        logger.warning(
            'Scan %d — write_debug requested but no raw directory could be resolved.',
            scan.index,
        )
        return

    _write_region_debug_artifacts(
        debug_dir,
        basename='name',
        roi_key='items.name',
        raw_bgr=name,
        rarity=detected_rarity,
    )
    _write_region_debug_artifacts(
        debug_dir,
        basename='value',
        roi_key='items.value',
        raw_bgr=value,
        rarity=None,
    )


def reprocess_item_scans_with_service(
    scans,
    providers: list[str],
    min_rarity: int,
    min_level: int,
    write_debug: bool,
    *,
    tab: InventoryTab,
    max_batch_size: int = 8,
    ocr_cache_path: str | Path | None = None,
    raw_base: str | Path | None = None,
) -> list[dict]:
    """Process raw dev-item/resource scans through the v2 OcrService pipeline."""
    from ...game.screen_info import ScreenInfo
    from .captures import WeaponCapture
    from .ocr_service import OcrService
    from .shared_scan_helpers import _rarity_from_capture_pixel

    items: list[dict] = []

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

            items_layout = cast(Any, ScreenInfo(scan.screen_width, scan.screen_height).items)
            full_rgb = scan.full_screenshot

            name = ensure_bgr_image(_crop_roi(full_rgb, items_layout.name), source_space='rgb')
            value = ensure_bgr_image(_crop_roi(full_rgb, items_layout.value), source_space='rgb')

            detected_rarity: int | None = None
            if hasattr(items_layout, 'rarityColorPick'):
                rcp = items_layout.rarityColorPick
                detected_rarity, _, _ = _rarity_from_capture_pixel(
                    full_rgb[int(rcp.y), int(rcp.x)]
                )

            if write_debug:
                _write_item_debug_artifacts(
                    scan,
                    raw_base=raw_base,
                    tab=tab,
                    detected_rarity=detected_rarity,
                    name=name,
                    value=value,
                )

            capture = WeaponCapture(
                index=scan.index,
                name=name,
                value=value,
                rank=None,
                equipped=None,
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
                items.append(result.data)
            else:
                logger.debug('Scan %d — rejected', scan_index)

    return items