"""
wuwa_inventory_kamera.scraping.ocr.region_specs
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Declarative OCR-region descriptors that pair a ROI key with its
preprocessing recipe and cache-signature strategy.

Each :class:`OcrRegionSpec` describes:

* **colour masking** — exact or range-based text/background colour
  filters, optionally keyed by item rarity;
* **threshold pipeline** — floor / Otsu / none;
* **morphology** — optional closing pass;
* **cache tier** — none / transient / persistent;
* **signature parameters** — how to fingerprint a crop for cache keying.

The spec registry is loaded from ``config/ocr_region_specs.toml`` at
startup so that colour calibration changes after a game patch require
only a config edit, not a code change.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Literal

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Type aliases for colour ranges: list of (lo, hi) inclusive bounds.
# Each bound is a 3-tuple (or 1-tuple for gray).  Exact mode is lo == hi.
ColorRange = tuple[tuple[int, ...], tuple[int, ...]]
ColorRangeList = list[ColorRange]


@dataclass(frozen=True, slots=True)
class OcrRegionSpec:
    """Declares how to preprocess and fingerprint a specific OCR region."""

    roi_key: str  # e.g. "echoes.echoName"

    # ---- Preprocessing ----
    color_space: Literal["hsv", "rgb", "bgr", "gray"] = "gray"

    text_color_ranges: ColorRangeList | None = None

    text_color_ranges_by_rarity: dict[int, ColorRangeList] | None = None

    background_color_ranges: ColorRangeList | None = None

    invert: bool = False

    # Regions that are guaranteed to contain one text line can opt into a
    # tiny horizontal close pass to repair anti-aliased pinholes in thin
    # glyphs before OCR.
    single_line: bool = False

    threshold_mode: Literal["otsu", "floor", "none"] = "none"
    floor_value: int = 100

    morphology: Literal["close", "none"] = "none"
    allowed_chars: str | None = None

    # ---- Cache tier ----
    cache_mode: Literal["none", "transient", "persistent"] = "persistent"

    # ---- Signature parameters ----
    sig_text_floor: int = 200
    sig_max_spread: int = 32
    sig_downscale: tuple[int, int] = (64, 64)
    sig_from_preprocessed: bool = False

    # ---- Version tag (incorporated into cache keys) ----
    spec_version: str = ""

    # ---- Fallback colour-space override ----
    # When a TOML ``fallback`` section uses a different ``color_space`` than
    # the parent spec, that colour space is stored here.  It is used ONLY
    # when evaluating the base ``text_color_ranges`` (i.e. when rarity is
    # unknown or has no matching override).  Per-rarity overrides always use
    # the parent ``color_space``.
    fallback_color_space: str | None = None

    # ------------------------------------------------------------------
    # Preprocessing
    # ------------------------------------------------------------------

    def preprocess(
        self,
        bgr: np.ndarray,
        rarity: int | None = None,
    ) -> np.ndarray:
        """Apply the declared pipeline, returning a cleaned image for OCR.

        Parameters
        ----------
        bgr:
            Raw crop in BGR uint8.
        rarity:
            If supplied and ``text_color_ranges_by_rarity`` contains a
            matching entry, that entry replaces the base
            ``text_color_ranges``.

        Returns
        -------
        np.ndarray
            A single-channel (H, W) uint8 image ready for the OCR engine
            (converted to 3-channel RGB by ``format_for_ocr``).
        """
        used_rarity_override = (
            rarity is not None
            and self.text_color_ranges_by_rarity is not None
            and rarity in self.text_color_ranges_by_rarity
        )
        if used_rarity_override:
            effective_ranges = self.text_color_ranges_by_rarity[rarity]  # type: ignore[index]
            effective_cs = self.color_space
        else:
            effective_ranges = self.text_color_ranges
            effective_cs = self.fallback_color_space or self.color_space

        color_view = _convert_color_space(bgr, effective_cs)

        reject_mask = _mask_from_ranges(color_view, self.background_color_ranges)

        if effective_ranges is not None:
            include_mask = _mask_from_ranges(color_view, effective_ranges)
            if reject_mask is not None:
                include_mask = include_mask & ~reject_mask
            plane = np.where(include_mask, np.uint8(255), np.uint8(0))
        else:
            if reject_mask is not None:
                bgr = _zero_masked(bgr, reject_mask)
            plane = _to_gray(bgr)
            plane = _apply_threshold(plane, self.threshold_mode, self.floor_value)

        if self.single_line:
            plane = _repair_single_line_glyphs(plane)

        plane = _apply_morphology(plane, self.morphology)
        if self.invert:
            plane = np.bitwise_not(plane)

        return _format_for_ocr(plane)

    # ------------------------------------------------------------------
    # Signature generation
    # ------------------------------------------------------------------

    def make_signature(
        self,
        bgr: np.ndarray,
        rarity: int | None = None,
    ) -> str:
        """Compute a stable hex-string fingerprint for cache keying."""
        sig_image = self._image_for_signature(bgr, rarity)
        digest = hashlib.blake2b(digest_size=20)
        digest.update(self.spec_version.encode("ascii"))
        digest.update(b"|")
        digest.update(self.roi_key.encode("ascii"))
        digest.update(b"|")
        digest.update(str(sig_image.shape).encode("ascii"))
        digest.update(b"|")
        digest.update(sig_image.dtype.str.encode("ascii"))
        digest.update(b"|")
        digest.update(np.ascontiguousarray(sig_image).tobytes())
        return digest.hexdigest()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_text_ranges(self, rarity: int | None) -> ColorRangeList | None:
        if rarity is not None and self.text_color_ranges_by_rarity:
            override = self.text_color_ranges_by_rarity.get(rarity)
            if override is not None:
                return override
        return self.text_color_ranges

    def _image_for_signature(
        self,
        bgr: np.ndarray,
        rarity: int | None,
    ) -> np.ndarray:
        if self.sig_from_preprocessed:
            preprocessed = self.preprocess(bgr, rarity)
            # preprocess returns RGB; convert back to single-channel for
            # signature hashing.
            if preprocessed.ndim == 3:
                preprocessed = cv2.cvtColor(preprocessed, cv2.COLOR_RGB2GRAY)
            return _downscale(preprocessed, self.sig_downscale)

        # Raw-signature path
        color_view = _convert_color_space(bgr, self.color_space)
        reject_mask = _mask_from_ranges(color_view, self.background_color_ranges)
        if reject_mask is not None:
            bgr = _zero_masked(bgr, reject_mask)

        normalized = _normalize_for_signature(
            bgr,
            floor=self.sig_text_floor,
            max_spread=self.sig_max_spread,
        )
        return _downscale(normalized, self.sig_downscale)


# ======================================================================
# Module-level helper functions
# ======================================================================

def _convert_color_space(bgr: np.ndarray, space: str) -> np.ndarray:
    if space == "bgr":
        return bgr
    if space == "rgb":
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    if space == "hsv":
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    if space == "gray":
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    raise ValueError(f"Unknown color_space: {space!r}")


def _mask_from_ranges(
    image: np.ndarray,
    ranges: ColorRangeList | None,
) -> np.ndarray | None:
    """Build a boolean OR-mask from a list of (lo, hi) inclusive ranges."""
    if not ranges:
        return None

    combined: np.ndarray | None = None
    for lo, hi in ranges:
        lo_arr = np.array(lo, dtype=np.uint8)
        hi_arr = np.array(hi, dtype=np.uint8)
        mask = cv2.inRange(image, lo_arr, hi_arr) > 0
        if combined is None:
            combined = mask
        else:
            combined = combined | mask
    return combined


def _zero_masked(bgr: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Zero out pixels where *mask* is True."""
    out = bgr.copy()
    out[mask] = 0
    return out


def _to_gray(bgr: np.ndarray) -> np.ndarray:
    if bgr.ndim == 2:
        return bgr
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)


def _apply_threshold(
    gray: np.ndarray,
    mode: str,
    floor_value: int,
) -> np.ndarray:
    if mode == "none":
        return gray
    if mode == "floor":
        lut = np.zeros(256, dtype=np.uint8)
        for i in range(floor_value, 256):
            lut[i] = int((i - floor_value) * (255 / max(1, 255 - floor_value)))
        return cv2.LUT(gray, lut)
    if mode == "otsu":
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return binary
    raise ValueError(f"Unknown threshold_mode: {mode!r}")


def _apply_morphology(plane: np.ndarray, mode: str) -> np.ndarray:
    if mode == "none":
        return plane
    if mode == "close":
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        return cv2.morphologyEx(plane, cv2.MORPH_CLOSE, kernel)
    raise ValueError(f"Unknown morphology: {mode!r}")


def _repair_single_line_glyphs(plane: np.ndarray) -> np.ndarray:
    """Bridge tiny horizontal gaps common in shrunk single-line text."""
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 1))
    return cv2.morphologyEx(plane, cv2.MORPH_CLOSE, kernel)


def _format_for_ocr(plane: np.ndarray) -> np.ndarray:
    """Convert a single-channel image to 3-channel RGB for the OCR backend."""
    if plane.ndim == 2:
        return cv2.cvtColor(plane, cv2.COLOR_GRAY2RGB)
    return plane


def _downscale(image: np.ndarray, max_size: tuple[int, int]) -> np.ndarray:
    """Resize *image* to fit within *max_size*, preserving aspect ratio."""
    h, w = image.shape[:2]
    max_w, max_h = max_size
    if w <= max_w and h <= max_h:
        return np.ascontiguousarray(image)
    scale = min(max_w / w, max_h / h)
    new_w = max(1, int(w * scale))
    new_h = max(1, int(h * scale))
    resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)
    # Re-binarize if input was binary
    if _is_binary(image):
        resized = np.where(resized >= 32, np.uint8(255), np.uint8(0))
    return np.ascontiguousarray(resized)


def _is_binary(image: np.ndarray) -> bool:
    return bool(np.all((image == 0) | (image == 255)))


def _normalize_for_signature(
    bgr: np.ndarray,
    *,
    floor: int,
    max_spread: int,
) -> np.ndarray:
    """Normalize a raw crop into a stable binary mask for cache keying.

    This mirrors the existing ``EchoOcrCache._normalize_for_hash`` logic:
    isolate near-white text pixels, fall back to gray thresholding.
    """
    if bgr.ndim == 2:
        return _threshold_plane(bgr, floor=floor, margin=24)

    if bgr.ndim == 3 and bgr.shape[2] >= 3:
        rgb = bgr[..., :3].astype(np.int16, copy=False)
        darkest = rgb.min(axis=2)
        spread = rgb.max(axis=2) - darkest
        threshold = max(floor, int(np.max(darkest)) - 24)
        mask = (darkest >= threshold) & (spread <= max_spread)
        if np.any(mask):
            return np.ascontiguousarray(
                np.where(mask, np.uint8(255), np.uint8(0))
            )
        # Fallback: gray projection
        gray = ((77 * rgb[..., 0] + 150 * rgb[..., 1] + 29 * rgb[..., 2]) >> 8).astype(np.uint8)
        return _threshold_plane(gray, floor=175, margin=48)

    return bgr


def _threshold_plane(
    plane: np.ndarray,
    *,
    floor: int,
    margin: int,
) -> np.ndarray:
    threshold = max(floor, int(np.max(plane)) - margin)
    binary = np.where(plane >= threshold, np.uint8(255), np.uint8(0))
    return np.ascontiguousarray(binary)


# ======================================================================
# TOML config loader
# ======================================================================

def load_specs_from_toml(path: str | None = None) -> dict[str, OcrRegionSpec]:
    """Load an ``OcrRegionSpec`` registry from a TOML file.

    Parameters
    ----------
    path:
        Path to the TOML config.  If ``None``, uses the default
        ``config/ocr_region_specs.toml`` relative to the repo root.

    Returns
    -------
    dict[str, OcrRegionSpec]
        Mapping from ``roi_key`` to its spec.
    """
    import tomllib
    from pathlib import Path

    if path is None:
        path = str(
            Path(__file__).resolve().parents[4] / "config" / "ocr_region_specs.toml"
        )

    with open(path, "rb") as f:
        raw = tomllib.load(f)

    spec_version = raw.get("spec_version", "")
    specs: dict[str, OcrRegionSpec] = {}

    for section_key, section in raw.items():
        if section_key == "spec_version":
            continue
        if not isinstance(section, dict):
            continue
        # section_key is a dotted path like "echoes" containing sub-keys
        # like "echoName", or a flat "echoes.echoName" key.
        _parse_section(section_key, section, spec_version, specs)

    return specs


def _parse_section(
    prefix: str,
    section: dict,
    spec_version: str,
    out: dict[str, OcrRegionSpec],
) -> None:
    """Recursively parse TOML sections into OcrRegionSpec objects."""
    # Detect if this section is a spec (has known keys) or a namespace
    spec_keys = {
        "color_space", "text_color_ranges", "threshold_mode",
        "floor_value", "morphology", "allowed_chars", "cache_mode",
        "sig_text_floor", "sig_max_spread", "sig_downscale",
        "sig_from_preprocessed", "invert", "background_color_ranges",
        "rarity_source", "rarity_overrides", "fallback", "single_line",
    }
    has_spec_keys = any(k in spec_keys for k in section)
    has_sub_tables = any(
        isinstance(v, dict) and k not in {"rarity_overrides", "fallback"}
        for k, v in section.items()
    )

    if has_spec_keys:
        spec = _build_spec(prefix, section, spec_version)
        out[prefix] = spec
    if has_sub_tables:
        for k, v in section.items():
            if isinstance(v, dict) and k not in {"rarity_overrides", "fallback"}:
                _parse_section(f"{prefix}.{k}", v, spec_version, out)


def _build_spec(
    roi_key: str,
    data: dict,
    spec_version: str,
) -> OcrRegionSpec:
    """Construct an OcrRegionSpec from a parsed TOML section."""
    kwargs: dict = {"roi_key": roi_key, "spec_version": spec_version}

    for simple_key in (
        "color_space", "threshold_mode", "floor_value", "morphology",
        "allowed_chars", "cache_mode", "sig_text_floor", "sig_max_spread",
        "sig_from_preprocessed", "invert", "single_line",
    ):
        if simple_key in data:
            kwargs[simple_key] = data[simple_key]

    if "sig_downscale" in data:
        kwargs["sig_downscale"] = tuple(data["sig_downscale"])

    if "text_color_ranges" in data:
        kwargs["text_color_ranges"] = _parse_color_ranges(data["text_color_ranges"])

    if "background_color_ranges" in data:
        kwargs["background_color_ranges"] = _parse_color_ranges(
            data["background_color_ranges"]
        )

    if "rarity_overrides" in data:
        by_rarity: dict[int, ColorRangeList] = {}
        for rarity_str, rarity_data in data["rarity_overrides"].items():
            rarity_int = int(rarity_str)
            if "text_color_ranges" in rarity_data:
                by_rarity[rarity_int] = _parse_color_ranges(
                    rarity_data["text_color_ranges"]
                )
        if by_rarity:
            kwargs["text_color_ranges_by_rarity"] = by_rarity

    # Fallback ranges (stored in base text_color_ranges if no rarity override)
    if "fallback" in data:
        fallback = data["fallback"]
        if "text_color_ranges" in fallback:
            # Only set base ranges if not already set
            if "text_color_ranges" not in kwargs:
                kwargs["text_color_ranges"] = _parse_color_ranges(
                    fallback["text_color_ranges"]
                )
                # If the fallback uses a different color space, record it so
                # that preprocess() evaluates these ranges in the correct
                # space (e.g. HSV bands) rather than the parent's space (BGR).
                fallback_cs = fallback.get("color_space")
                parent_cs = kwargs.get("color_space", "gray")
                if fallback_cs and fallback_cs != parent_cs:
                    kwargs["fallback_color_space"] = fallback_cs

    return OcrRegionSpec(**kwargs)


def _parse_color_ranges(raw: list) -> ColorRangeList:
    """Convert TOML ``[[lo, lo, lo], [hi, hi, hi]]`` pairs to tuples."""
    result: ColorRangeList = []
    for pair in raw:
        lo = tuple(pair[0])
        hi = tuple(pair[1])
        result.append((lo, hi))
    return result


# ======================================================================
# Global spec registry
# ======================================================================

_REGISTRY: dict[str, OcrRegionSpec] | None = None


def get_spec(roi_key: str) -> OcrRegionSpec | None:
    """Look up a region spec by its ROI key."""
    global _REGISTRY
    if _REGISTRY is None:
        try:
            _REGISTRY = load_specs_from_toml()
        except FileNotFoundError:
            logger.warning("ocr_region_specs.toml not found; using empty registry")
            _REGISTRY = {}
    return _REGISTRY.get(roi_key)


def get_all_specs() -> dict[str, OcrRegionSpec]:
    """Return the full spec registry (loading from TOML if needed)."""
    global _REGISTRY
    if _REGISTRY is None:
        try:
            _REGISTRY = load_specs_from_toml()
        except FileNotFoundError:
            logger.warning("ocr_region_specs.toml not found; using empty registry")
            _REGISTRY = {}
    return dict(_REGISTRY)


def reload_specs(path: str | None = None) -> dict[str, OcrRegionSpec]:
    """Reload the spec registry from disk."""
    global _REGISTRY
    _REGISTRY = load_specs_from_toml(path)
    return dict(_REGISTRY)
