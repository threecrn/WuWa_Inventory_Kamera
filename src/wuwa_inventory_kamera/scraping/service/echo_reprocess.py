"""
wuwa_inventory_kamera.scraping.service.echo_reprocess
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Shared service-mode reprocessing for previously captured raw echo scans.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from .echo_capture_utils import (
    build_echo_capture,
    decide_echo_level,
    ensure_bgr_image,
)
from .shared_scan_helpers import _write_echo_debug_artifacts

logger = logging.getLogger('wuwa.echo_reprocess')


def _crop_roi(image: np.ndarray, roi) -> np.ndarray:
    return image[
        int(roi.y): int(roi.y + roi.h),
        int(roi.x): int(roi.x + roi.w),
    ]


def reprocess_echo_scans_with_service(
    scans,
    providers: list[str],
    min_rarity: int,
    min_level: int,
    write_debug: bool,
    max_batch_size: int = 8,
    ocr_cache_path: str | Path | None = None,
    raw_base: str | Path | None = None,
) -> list[dict]:
    """Process raw echo scans through the v2 OcrService pipeline."""
    from ...game.screen_info import ScreenInfo
    from .captures import EchoCapture
    from .ocr_service import OcrService
    from .shared_scan_helpers import _rarity_from_rgb_pixel

    echoes: list[dict] = []

    resolution = (
        f'{scans[0].screen_width}x{scans[0].screen_height}'
        if scans else None
    )
    with OcrService(
        providers=providers,
        min_rarity=min_rarity,
        min_level=min_level,
        max_batch_size=max_batch_size,
        ocr_cache_path=(
            str(ocr_cache_path)
            if ocr_cache_path is not None else None
        ),
        resolution=resolution,
        det_limit_side_len=32*8, #32*12,
        #det_limit_type='max',
    ) as svc:
        futures = []
        for scan in scans:
            try:
                scan.load_images()
            except FileNotFoundError as exc:
                logger.error('Scan %d — images missing, skipping: %s', scan.index, exc)
                continue

            si = ScreenInfo(scan.screen_width, scan.screen_height).echoes

            stats_name = _crop_roi(scan.full_screenshot, si.fullStatsName)
            stats_value = _crop_roi(scan.full_screenshot, si.fullStatsValue)

            level_crop = _crop_roi(scan.full_screenshot, si.level)
            level_crop_bgr = ensure_bgr_image(level_crop, source_space='rgb')
            level_decision = decide_echo_level(
                level_text=svc.ocr_adhoc_text(level_crop_bgr, 'echoes.level')
            )

            detected_rarity: int | None = None
            if hasattr(si, 'rarityColorPick'):
                rcp = si.rarityColorPick
                detected_rarity = _rarity_from_rgb_pixel(
                    scan.full_screenshot[int(rcp.y), int(rcp.x)]
                )

            capture = build_echo_capture(
                echo_index=scan.index,
                full_frame=scan.full_screenshot,
                echoes_layout=si,
                source_space='rgb',
                level_decision=level_decision,
                detected_rarity=detected_rarity,
                full_screenshot=scan.full_screenshot if write_debug else None,
            )

            if write_debug:
                _write_echo_debug_artifacts(
                    scan,
                    raw_base=raw_base,
                    full_screenshot_space='rgb',
                    detected_rarity=detected_rarity,
                    echo_name=capture.echo_name,
                    equipped=capture.equipped,
                    level=level_crop,
                    stats_name=stats_name,
                    stats_value=stats_value,
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
                echoes.append(result.data)
                for warning in result.warnings:
                    logger.warning('Scan %d — %s', scan_index, warning)
            else:
                logger.debug('Scan %d — rejected', scan_index)

    return echoes