"""
wuwa_inventory_kamera.scraping.service.character_reprocess
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Shared service-mode reprocessing for previously captured raw character scans.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path

import numpy as np

from .echo_capture_utils import ensure_bgr_image

logger = logging.getLogger('wuwa.character_reprocess')


_REQUIRED_SECTIONS: tuple[int, ...] = (0, 1, 3, 4)
_OVERVIEW_DEBUG_ROI_KEYS = {
    'name': 'characters.resonatorName',
    'level': 'characters.resonatorLevel',
}
_WEAPON_DEBUG_ROI_KEYS = {
    'weaponName': 'characters.weaponName',
    'weaponLevel': 'characters.weaponLevel',
    'weaponRank': 'characters.weaponRank',
}
_PASSIVE_SKILL_KEYS = ('stats0', 'stats1', 'inherent', 'stats3', 'stats4')


def _passive_skill_crop_key(skill_key: str, tier: int) -> str:
    return f'passive_{skill_key}_{tier}'


def _crop_roi(image: np.ndarray, roi) -> np.ndarray:
    return image[
        int(roi.y): int(roi.y + roi.h),
        int(roi.x): int(roi.x + roi.w),
    ]


def _resolve_debug_dir(scan, section: int, raw_base: str | Path | None) -> Path | None:
    base_path = getattr(scan, 'base_path', None)
    if base_path is not None:
        return Path(base_path) / f'section_{section}' / 'debug'
    if raw_base is not None:
        return Path(raw_base) / f'char_{scan.index:04d}' / f'section_{section}' / 'debug'
    return None


def _write_character_debug_artifacts(
    scan,
    *,
    raw_base: str | Path | None,
    crops_by_section: dict[int, dict[str, np.ndarray]],
) -> None:
    from .echo_reprocess import _write_region_debug_artifacts

    roi_key_maps = {
        0: _OVERVIEW_DEBUG_ROI_KEYS,
        1: _WEAPON_DEBUG_ROI_KEYS,
        3: {
            _passive_skill_crop_key(skill_key, tier): 'characters.skillButton'
            for skill_key in _PASSIVE_SKILL_KEYS
            for tier in (1, 2)
        },
    }

    for section, crops in crops_by_section.items():
        debug_dir = _resolve_debug_dir(scan, section, raw_base)
        if debug_dir is None:
            logger.warning(
                'Character %d section %d — write_debug requested but no raw directory could be resolved.',
                scan.index,
                section,
            )
            continue

        for basename, raw_bgr in crops.items():
            roi_key = roi_key_maps.get(section, {}).get(basename, f'characters.{basename}')
            _write_region_debug_artifacts(
                debug_dir,
                basename=basename,
                roi_key=roi_key,
                raw_bgr=raw_bgr,
                rarity=None,
            )


def _build_character_output(fields: dict) -> dict:
    ascension_levels = [20, 40, 50, 60, 70, 80, 90]

    level = fields.get('level', 0)
    weapon_max = fields.get('weaponMaxLevel', 0)

    try:
        weapon_ascension = ascension_levels.index(weapon_max)
    except ValueError:
        weapon_ascension = 0

    raw_skills = fields.get('skills', {})
    skills_out: dict = defaultdict(int, {
        'normal': raw_skills.get('skill_0', 1),
        'resonance': raw_skills.get('skill_1', 1),
        'forte': raw_skills.get('skill_2', 1),
        'liberation': raw_skills.get('skill_3', 1),
        'intro': raw_skills.get('skill_4', 1),
        'stats0': raw_skills.get('stats0', 0),
        'stats1': raw_skills.get('stats1', 0),
        'inherent': raw_skills.get('inherent', 0),
        'stats3': raw_skills.get('stats3', 0),
        'stats4': raw_skills.get('stats4', 0),
    })

    raw_chain = fields.get('chain', {})
    chain_count = sum(1 for value in raw_chain.values() if value)

    return {
        '_name': fields.get('name', ''),
        'level': level,
        'ascension': fields.get('ascension', 0),
        'weapon': {
            'id': fields.get('weaponId', fields.get('weaponName', '')),
            'level': fields.get('weaponLevel', 1),
            'ascension': weapon_ascension,
            'rank': fields.get('weaponRank', 1),
        },
        'echoes': {},
        'skills': dict(skills_out),
        'chain': chain_count,
    }


def _character_crops(scan) -> dict[int, dict[str, np.ndarray]]:
    from ...game.screen_info import ScreenInfo

    layout = ScreenInfo(scan.screen_width, scan.screen_height).characters

    overview_full = scan.load_section_images(0)['full']
    weapon_full = scan.load_section_images(1)['full']

    overview = {
        'name': ensure_bgr_image(
            _crop_roi(overview_full, layout.resonatorName),
            source_space='rgb',
        ),
        'level': ensure_bgr_image(
            _crop_roi(overview_full, layout.resonatorLevel),
            source_space='rgb',
        ),
    }
    weapon = {
        'weaponName': ensure_bgr_image(
            _crop_roi(weapon_full, layout.weaponName),
            source_space='rgb',
        ),
        'weaponLevel': ensure_bgr_image(
            _crop_roi(weapon_full, layout.weaponLevel),
            source_space='rgb',
        ),
        'weaponRank': ensure_bgr_image(
            _crop_roi(weapon_full, layout.weaponRank),
            source_space='rgb',
        ),
    }
    skills = {
        key: ensure_bgr_image(image, source_space='rgb')
        for key, image in scan.load_section_images(3).items()
    }
    chain = {
        key: ensure_bgr_image(image, source_space='rgb')
        for key, image in scan.load_section_images(4).items()
    }

    return {
        0: overview,
        1: weapon,
        3: skills,
        4: chain,
    }


def reprocess_character_scans_with_service(
    scans,
    providers: list[str],
    write_debug: bool,
    max_batch_size: int = 8,
    ocr_cache_path: str | Path | None = None,
    raw_base: str | Path | None = None,
) -> dict[str, dict]:
    """Process raw character scans through the v2 OcrService pipeline."""
    from .captures import CharCapture
    from .ocr_service import OcrService

    characters: dict[str, dict] = {}

    resolution = (
        f'{scans[0].screen_width}x{scans[0].screen_height}'
        if scans else None
    )
    with OcrService(
        providers=providers,
        min_rarity=1,
        min_level=0,
        max_batch_size=max_batch_size,
        ocr_cache_path=(
            str(ocr_cache_path)
            if ocr_cache_path is not None else None
        ),
        resolution=resolution,
        det_limit_side_len=32 * 8,
    ) as svc:
        submitted: list[tuple[int, list[tuple[int, object]]]] = []

        for scan in scans:
            try:
                crops_by_section = _character_crops(scan)
            except FileNotFoundError as exc:
                logger.error('Character %d — images missing, skipping: %s', scan.index, exc)
                continue

            if write_debug:
                _write_character_debug_artifacts(
                    scan,
                    raw_base=raw_base,
                    crops_by_section=crops_by_section,
                )

            section_futures: list[tuple[int, object]] = []
            for section in _REQUIRED_SECTIONS:
                capture = CharCapture(
                    char_index=scan.index,
                    section=section,
                    crops=crops_by_section[section],
                )
                section_futures.append((section, svc.submit(capture)))
            submitted.append((scan.index, section_futures))

        for scan_index, section_futures in submitted:
            final_result = None
            failed = False
            for section, future in section_futures:
                try:
                    result = future.result()
                except Exception as exc:
                    logger.exception(
                        'Character %d section %d — service error (%s): %r',
                        scan_index,
                        section,
                        type(exc).__name__,
                        exc,
                    )
                    failed = True
                    break
                if section == 4:
                    final_result = result

            if failed or final_result is None:
                continue

            fields = final_result.fields
            char_id = fields.get('char_id') or fields.get('name') or str(scan_index)
            characters[char_id] = _build_character_output(fields)

    return characters