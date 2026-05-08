"""
wuwa_inventory_kamera.scraping.service.echo_reprocess
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Shared service-mode reprocessing for previously captured raw echo scans.
"""
from __future__ import annotations

import logging
from pathlib import Path

import cv2

logger = logging.getLogger('wuwa.echo_reprocess')


def reprocess_echo_scans_with_service(
    scans,
    providers: list[str],
    min_rarity: int,
    min_level: int,
    write_debug: bool,
    max_batch_size: int = 8,
    echo_stat_cache_path: str | Path | None = None,
    ocr_cache_path: str | Path | None = None,
) -> list[dict]:
    """Process raw echo scans through the v2 OcrService pipeline."""
    from ...game.screen_info import ScreenInfo
    from .captures import EchoCapture
    from .ocr_service import OcrService

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
        echo_stat_cache_path=(
            str(echo_stat_cache_path)
            if echo_stat_cache_path is not None else None
        ),
        ocr_cache_path=(
            str(ocr_cache_path)
            if ocr_cache_path is not None else None
        ),
        resolution=resolution,
    ) as svc:
        futures = []
        for scan in scans:
            try:
                scan.load_images()
            except FileNotFoundError as exc:
                logger.error('Scan %d — images missing, skipping: %s', scan.index, exc)
                continue

            si = ScreenInfo(scan.screen_width, scan.screen_height).echoes

            card = scan.full_screenshot[
                si.echoCard.y: si.echoCard.y + si.echoCard.h,
                si.echoCard.x: si.echoCard.x + si.echoCard.w,
            ]
            stats_name = scan.full_screenshot[
                si.fullStatsName.y: si.fullStatsName.y + si.fullStatsName.h,
                si.fullStatsName.x: si.fullStatsName.x + si.fullStatsName.w,
            ]
            stats_value = scan.full_screenshot[
                si.fullStatsValue.y: si.fullStatsValue.y + si.fullStatsValue.h,
                si.fullStatsValue.x: si.fullStatsValue.x + si.fullStatsValue.w,
            ]

            si_raw = si.sonataIcon
            sonata_icon_cx: float | None = None
            sonata_icon_cy: float | None = None
            sonata_icon_r: float | None = None
            detected_level: int | None = None
            if hasattr(si_raw, 'level_X'):
                from ..ocr import imageToString as _ocr_str

                level_crop = scan.full_screenshot[
                    int(si.level.y): int(si.level.y + si.level.h),
                    int(si.level.x): int(si.level.x + si.level.w),
                ]
                level_text = _ocr_str(level_crop, allowedChars='0123456789').strip()
                two_digits = len(level_text) == 2
                si_slot = si_raw.level_XX if two_digits else si_raw.level_X
                icon_roi = si_slot.icon
                sonata_icon_cx = si_slot.circle.x
                sonata_icon_cy = si_slot.circle.y
                sonata_icon_r = si_raw.radius
                if level_text.isdigit():
                    detected_level = min(25, int(level_text))
            else:
                icon_roi = si_raw
                if hasattr(si, 'sonataIconCircle'):
                    sic = si.sonataIconCircle
                    if hasattr(sic, 'circle'):
                        sonata_icon_cx = sic.circle.x
                        sonata_icon_cy = sic.circle.y
                    if hasattr(sic, 'radius'):
                        sonata_icon_r = sic.radius

            sonata_icon = scan.full_screenshot[
                int(icon_roi.y): int(icon_roi.y + icon_roi.h),
                int(icon_roi.x): int(icon_roi.x + icon_roi.w),
            ]

            echo_name = None
            if hasattr(si, 'echoName'):
                en = si.echoName
                echo_name_rgb = scan.full_screenshot[
                    int(en.y): int(en.y + en.h),
                    int(en.x): int(en.x + en.w),
                ]
                echo_name = cv2.cvtColor(echo_name_rgb, cv2.COLOR_RGB2BGR)

            detected_rarity: int | None = None
            if hasattr(si, 'rarityColorPick'):
                from ..scanning.echo_workflow import _rarity_from_bgr_pixel

                rcp = si.rarityColorPick
                detected_rarity = _rarity_from_bgr_pixel(
                    scan.full_screenshot[int(rcp.y), int(rcp.x)][::-1]
                )

            capture = EchoCapture(
                echo_index=scan.index,
                card=card,
                echo_name=echo_name,
                sonata_icon=sonata_icon,
                sonata_icon_cx=sonata_icon_cx,
                sonata_icon_cy=sonata_icon_cy,
                sonata_icon_r=sonata_icon_r,
                detected_level=detected_level,
                detected_rarity=detected_rarity,
                stats_name=stats_name,
                stats_value=stats_value,
                full_screenshot=scan.full_screenshot if write_debug else None,
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