"""
wuwa_inventory_kamera.scraping.service.echo_reprocess
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Shared service-mode reprocessing for previously captured raw echo scans.
"""
from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

from .echo_capture_utils import (
    build_echo_capture,
    decide_echo_level,
    ensure_bgr_image,
)

logger = logging.getLogger('wuwa.echo_reprocess')


_DEBUG_REGION_SPECS: tuple[tuple[str, str], ...] = (
    ('echoes.echoName', 'echo_name'),
    ('echoes.fullStatsName', 'stats_name'),
    ('echoes.fullStatsValue', 'stats_value'),
    ('echoes.equipped', 'equipped'),
    ('echoes.level', 'level'),
)


def _resolve_debug_dir(scan, raw_base: str | Path | None) -> Path | None:
    full_path = getattr(scan, 'full_path', None)
    if full_path is not None:
        return Path(full_path).parent / 'debug'
    if raw_base is not None:
        return Path(raw_base) / f'echo_{scan.index:04d}' / 'debug'
    return None


def _to_debug_image(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_RGB2BGR)


def _crop_roi(image: np.ndarray, roi) -> np.ndarray:
    return image[
        int(roi.y): int(roi.y + roi.h),
        int(roi.x): int(roi.x + roi.w),
    ]


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
    cv2.imwrite(str(debug_dir / f'{basename}.png'), raw_bgr)

    spec = get_spec(roi_key)
    if spec is None:
        return

    preprocessed = spec.preprocess(raw_bgr, rarity=rarity)
    signature = spec._image_for_signature(raw_bgr, rarity)

    cv2.imwrite(
        str(debug_dir / f'{basename}_preprocessed.png'),
        _to_debug_image(preprocessed.ocr_rgb),
    )
    cv2.imwrite(str(debug_dir / f'{basename}_signature.png'), signature)


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
    debug_dir = _resolve_debug_dir(scan, raw_base)
    if debug_dir is None:
        logger.warning(
            'Scan %d — write_debug requested but no raw directory could be resolved.',
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
                from ..scanning.echo_workflow import _rarity_from_rgb_pixel

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