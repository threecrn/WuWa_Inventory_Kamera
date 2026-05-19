"""
wuwa_inventory_kamera.scraping.service.echo_capture_utils
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Shared helpers for building ``EchoCapture`` objects from live and reprocess
frames.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np


@dataclass(frozen=True, slots=True)
class EchoLevelDecision:
    """Normalized level OCR result used for capture preparation."""

    level_text: str
    detected_level: int | None
    two_digits: bool


def ensure_bgr_image(image: np.ndarray, *, source_space: str) -> np.ndarray:
    """Normalize a capture crop to BGR before OCR/cache processing."""

    if image.ndim == 2 or source_space == 'bgr':
        return image.copy()
    if source_space == 'rgb':
        return cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
    raise ValueError(f'Unsupported source_space: {source_space!r}')


def ensure_rgb_image(image: np.ndarray, *, source_space: str) -> np.ndarray:
    """Normalize a capture crop to RGB before OCR/cache processing."""

    if image.ndim == 2 or source_space == 'rgb':
        return image.copy()
    if source_space == 'bgr':
        return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    raise ValueError(f'Unsupported source_space: {source_space!r}')


def parse_echo_level_text(level_text: str) -> int | None:
    """Return the first integer parsed from an OCR level string."""

    normalized = level_text.strip()
    if normalized.isdigit():
        return int(normalized)

    match = re.search(r'\d+', normalized)
    if match:
        return int(match.group())
    return None


def decide_echo_level(
    *,
    level_text: str | None = None,
    detected_level: int | None = None,
) -> EchoLevelDecision:
    """Normalize raw OCR text or a pre-read level for capture logic."""

    normalized = '' if level_text is None else level_text.strip()
    if detected_level is None:
        detected_level = parse_echo_level_text(normalized)

    if detected_level is not None:
        detected_level = min(25, detected_level)
        two_digits = detected_level >= 10
        if not normalized:
            normalized = str(detected_level)
    else:
        # Preserve the existing fallback when OCR returned ambiguous text.
        two_digits = len(normalized) == 2

    return EchoLevelDecision(
        level_text=normalized,
        detected_level=detected_level,
        two_digits=two_digits,
    )


def select_level_dependent_sonata_slot(
    sonata_icon_layout: Any,
    *,
    two_digits: bool,
) -> Any:
    """Return the supported sonata icon ROI variant for the level width."""

    return sonata_icon_layout.level_XX if two_digits else sonata_icon_layout.level_X