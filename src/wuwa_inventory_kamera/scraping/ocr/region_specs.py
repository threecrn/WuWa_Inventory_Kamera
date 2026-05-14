"""
wuwa_inventory_kamera.scraping.ocr.region_specs
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Declarative OCR-region descriptors that pair a ROI key with its
preprocessing recipe and cache-signature strategy.

Each :class:`OcrRegionSpec` describes:

* **stage scaling** — optional pre/post resize bounds for OCR input;
* **colour masking** — exact or range-based text/background colour
    filters, optionally keyed by item rarity;
* **threshold pipeline** — floor / Otsu / none;
* **morphology** — optional closing pass;
* **cache tier** — none / transient / persistent;
* **signature parameters** — how to fingerprint a crop for cache keying.

The spec registry is loaded from the package-owned
``src/wuwa_inventory_kamera/config/ocr_region_specs.toml`` at startup so
that colour calibration changes after a game patch require only a file
edit, not a code change.
"""
from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass, field

# --- Phase 1: Color Preprocessing Migration ---
@dataclass(frozen=True, slots=True)
class OcrPreprocessResult:
    ocr_rgb: np.ndarray
    signature_image: np.ndarray
    text_mask: np.ndarray | None = None
    debug_steps: dict[str, np.ndarray] = field(default_factory=dict)

from pathlib import Path
from typing import Literal

import cv2
import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_SPECS_PATH = Path(__file__).resolve().parents[2] / "config" / "ocr_region_specs.toml"

# Type aliases for colour ranges: list of (lo, hi) inclusive bounds.
# Each bound is a 3-tuple (or 1-tuple for gray).  Exact mode is lo == hi.
ColorRange = tuple[tuple[int, ...], tuple[int, ...]]
ColorRangeList = list[ColorRange]
Size2D = tuple[int, int]

_DEFAULT_SIGNATURE_POST_DOWNSCALE: Size2D = (512, 256)


@dataclass(frozen=True, slots=True)
class SignaturePreprocessSpec:
    """Optional preprocessing and scaling overrides used for signature hashing."""

    color_space: Literal["hsv", "rgb", "bgr", "gray"] | None = None
    text_color_ranges: ColorRangeList | None = None
    text_color_ranges_by_rarity: dict[int, ColorRangeList] | None = None
    background_color_ranges: ColorRangeList | None = None
    invert: bool | None = None
    single_line: bool | None = None
    threshold_mode: Literal["otsu", "floor", "none"] | None = None
    floor_value: int | None = None
    morphology: Literal["close", "none"] | None = None
    fallback_color_space: str | None = None
    pre_upscale: Size2D | None = None
    pre_downscale: Size2D | None = None
    post_upscale: Size2D | None = None
    post_downscale: Size2D | None = None

    def has_preprocess_overrides(self) -> bool:
        return any(
            value is not None
            for value in (
                self.color_space,
                self.text_color_ranges,
                self.text_color_ranges_by_rarity,
                self.background_color_ranges,
                self.invert,
                self.single_line,
                self.threshold_mode,
                self.floor_value,
                self.morphology,
                self.fallback_color_space,
            )
        )


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

    # Resize bounds applied around OCR preprocessing. Each tuple is
    # (max_width, max_height) or (min_width, min_height) as documented by
    # the field name, with aspect ratio preserved.
    pre_upscale: Size2D | None = None
    pre_downscale: Size2D | None = None
    post_upscale: Size2D | None = None
    post_downscale: Size2D | None = None

    # ---- OCR render mode ----
    # Controls how the preprocessed mask or color information is turned into
    # the final RGB image passed to the OCR engine.
    #
    #  legacy_binary_rgb : reproduce the old grayscale/binary pipeline; default
    #                      for backward compatibility.
    #  raw_passthrough   : skip all processing; convert BGR → RGB only.
    #  masked_color      : keep text pixels in their original colour; set all
    #                      non-text pixels to black.  Good for rarity-tinted
    #                      names where the colour itself carries signal.
    #  neutral_bg_color  : place text pixels on a neutral dark background.
    #                      Suppresses complex backgrounds while preserving the
    #                      3-channel structure RapidOCR benefits from.
    #  luma_boost_color  : contrast-stretch the luma channel while leaving
    #                      chroma intact.  Good for white/off-white text on
    #                      stable dark panels.
    render_mode: Literal[
        "legacy_binary_rgb",
        "raw_passthrough",
        "masked_color",
        "neutral_bg_color",
        "luma_boost_color",
    ] = "legacy_binary_rgb"

    # ---- Cache tier ----
    cache_mode: Literal["none", "transient", "persistent"] = "persistent"

    # ---- Signature parameters ----
    sig_text_floor: int = 200
    sig_max_spread: int = 32

    # Optional signature-only preprocessing recipe. If present and it declares
    # preprocess overrides, it is used to derive the image hashed for cache
    # keying.
    signature_preprocess: SignaturePreprocessSpec | None = None

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
    ) -> OcrPreprocessResult:
        """
        Apply the declared pipeline, returning a color-aware OCR input and debug artifacts.
        """
        # 1. Pre-scaling
        scaled_bgr = _apply_scaling_stage(
            bgr,
            upscale_min=self.pre_upscale,
            downscale_max=self.pre_downscale,
        )
        # 2. Build text mask
        text_mask = self.build_text_mask(scaled_bgr, rarity)
        # 3. Render for OCR (currently legacy path, will expand)
        rendered_rgb = self.render_for_ocr(scaled_bgr, text_mask)
        # 4. Post-scaling
        rendered_rgb = _apply_scaling_stage(
            rendered_rgb,
            upscale_min=self.post_upscale,
            downscale_max=self.post_downscale,
        )
        # 5. Signature image (legacy path)
        signature_image = self._image_for_signature(bgr, rarity)
        debug_steps = {
            "scaled_bgr": scaled_bgr,
            "text_mask": text_mask if text_mask is not None else np.zeros_like(scaled_bgr[...,0]),
            "rendered_rgb": rendered_rgb,
            "signature": signature_image,
        }
        return OcrPreprocessResult(rendered_rgb, signature_image, text_mask, debug_steps)

    def build_text_mask(
        self,
        bgr: np.ndarray,
        rarity: int | None = None,
    ) -> np.ndarray | None:
        """
        Build a boolean mask of likely text pixels using color ranges and thresholding.
        """
        used_rarity_override = (
            rarity is not None
            and self.text_color_ranges_by_rarity is not None
            and rarity in self.text_color_ranges_by_rarity
        )
        if used_rarity_override:
            effective_ranges: ColorRangeList | None = self.text_color_ranges_by_rarity[rarity]  # type: ignore[index]
            effective_cs: str = self.color_space
        else:
            effective_ranges = self.text_color_ranges
            # When no rarity override, evaluate base ranges in fallback_color_space
            # if specified (e.g. HSV band for echo name without rarity context).
            effective_cs = self.fallback_color_space or self.color_space

        color_view = _convert_color_space(bgr, effective_cs)
        reject_mask = _mask_from_ranges(color_view, self.background_color_ranges)

        if effective_ranges is not None:
            include_mask = _mask_from_ranges(color_view, effective_ranges)
            if include_mask is not None:
                if reject_mask is not None:
                    include_mask = include_mask & ~reject_mask
                if self.single_line:
                    include_mask = _repair_single_line_glyphs(
                        np.where(include_mask, np.uint8(255), np.uint8(0))
                    ) > 0
                return include_mask

        # Fallback: threshold on grayscale, with background suppression applied first.
        work_bgr = _zero_masked(bgr, reject_mask) if reject_mask is not None else bgr
        gray = _to_gray(work_bgr)
        plane = _apply_threshold(gray, self.threshold_mode, self.floor_value)
        if self.single_line:
            plane = _repair_single_line_glyphs(plane)
        return plane > 0

    def render_for_ocr(
        self,
        bgr: np.ndarray,
        text_mask: np.ndarray | None,
    ) -> np.ndarray:
        """
        Render a 3-channel RGB image for OCR using the text mask and original crop.

        Dispatch is based on ``self.render_mode``.

        *  ``legacy_binary_rgb`` — convert the mask (or gray threshold) to a
           binary plane, apply morphology / invert, then promote to 3-channel RGB.
        *  ``raw_passthrough`` — BGR → RGB; no further processing.
        *  ``masked_color`` — keep original text pixels in colour; black elsewhere.
        *  ``neutral_bg_color`` — keep text pixels on a neutral dark background.
        *  ``luma_boost_color`` — contrast-stretch the luma channel; keep chroma.
        """
        mode = self.render_mode

        if mode == "raw_passthrough":
            return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

        if mode == "luma_boost_color":
            return _render_luma_boost_color(bgr, self.floor_value)

        # All remaining modes derive a refined boolean mask.
        if text_mask is None:
            # Build a minimal binary mask from threshold as fallback.
            gray = _to_gray(bgr)
            plane = _apply_threshold(gray, self.threshold_mode, self.floor_value)
            text_mask = plane > 0

        if mode == "masked_color":
            return _render_masked_color(bgr, text_mask, self.morphology)

        if mode == "neutral_bg_color":
            return _render_neutral_bg_color(bgr, text_mask, self.morphology)

        # Default: "legacy_binary_rgb" — binary plane promoted to RGB.
        plane = np.where(text_mask, np.uint8(255), np.uint8(0))
        plane = _apply_morphology(plane, self.morphology)
        if self.invert:
            plane = np.bitwise_not(plane)
        return _format_for_ocr(plane)

    def _preprocess_plane(
        self,
        bgr: np.ndarray,
        rarity: int | None,
        *,
        color_space: Literal["hsv", "rgb", "bgr", "gray"],
        text_color_ranges: ColorRangeList | None,
        text_color_ranges_by_rarity: dict[int, ColorRangeList] | None,
        background_color_ranges: ColorRangeList | None,
        invert: bool,
        single_line: bool,
        threshold_mode: Literal["otsu", "floor", "none"],
        floor_value: int,
        morphology: Literal["close", "none"],
        fallback_color_space: str | None,
    ) -> np.ndarray:
        used_rarity_override = (
            rarity is not None
            and text_color_ranges_by_rarity is not None
            and rarity in text_color_ranges_by_rarity
        )
        if used_rarity_override:
            effective_ranges = text_color_ranges_by_rarity[rarity]  # type: ignore[index]
            effective_cs = color_space
        else:
            effective_ranges = text_color_ranges
            effective_cs = fallback_color_space or color_space

        color_view = _convert_color_space(bgr, effective_cs)
        reject_mask = _mask_from_ranges(color_view, background_color_ranges)

        if effective_ranges is not None:
            include_mask = _mask_from_ranges(color_view, effective_ranges)
            assert include_mask is not None
            if reject_mask is not None:
                include_mask = include_mask & ~reject_mask
            plane = np.where(include_mask, np.uint8(255), np.uint8(0))
        else:
            if reject_mask is not None:
                bgr = _zero_masked(bgr, reject_mask)
            plane = _to_gray(bgr)
            plane = _apply_threshold(plane, threshold_mode, floor_value)

        if single_line:
            plane = _repair_single_line_glyphs(plane)

        plane = _apply_morphology(plane, morphology)
        if invert:
            plane = np.bitwise_not(plane)
        return plane

    def _preprocess_for_signature(
        self,
        bgr: np.ndarray,
        rarity: int | None,
    ) -> np.ndarray:
        sig = self.signature_preprocess
        scaled_bgr = _apply_scaling_stage(
            bgr,
            upscale_min=(sig.pre_upscale if sig is not None else None),
            downscale_max=(sig.pre_downscale if sig is not None else None),
        )
        plane = self._preprocess_plane(
            scaled_bgr,
            rarity,
            color_space=(sig.color_space if sig is not None and sig.color_space is not None else self.color_space),
            text_color_ranges=(
                sig.text_color_ranges
                if sig is not None and sig.text_color_ranges is not None
                else self.text_color_ranges
            ),
            text_color_ranges_by_rarity=(
                sig.text_color_ranges_by_rarity
                if sig is not None and sig.text_color_ranges_by_rarity is not None
                else self.text_color_ranges_by_rarity
            ),
            background_color_ranges=(
                sig.background_color_ranges
                if sig is not None and sig.background_color_ranges is not None
                else self.background_color_ranges
            ),
            invert=(sig.invert if sig is not None and sig.invert is not None else self.invert),
            single_line=(
                sig.single_line
                if sig is not None and sig.single_line is not None
                else self.single_line
            ),
            threshold_mode=(
                sig.threshold_mode
                if sig is not None and sig.threshold_mode is not None
                else self.threshold_mode
            ),
            floor_value=(
                sig.floor_value
                if sig is not None and sig.floor_value is not None
                else self.floor_value
            ),
            morphology=(
                sig.morphology
                if sig is not None and sig.morphology is not None
                else self.morphology
            ),
            fallback_color_space=(
                sig.fallback_color_space
                if sig is not None and sig.fallback_color_space is not None
                else self.fallback_color_space
            ),
        )
        plane = _apply_scaling_stage(
            plane,
            upscale_min=(sig.post_upscale if sig is not None else None),
            downscale_max=(
                sig.post_downscale
                if sig is not None and sig.post_downscale is not None
                else _DEFAULT_SIGNATURE_POST_DOWNSCALE
            ),
        )
        return plane

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
        if self._signature_uses_preprocessed_source():
            preprocessed = self._preprocess_for_signature(bgr, rarity)
            # If preprocessing collapses to a constant image, hashing it will
            # make unrelated crops collide in the OCR cache. Fall back to a
            # normalized raw-image signature in that case.
            if np.ptp(preprocessed) != 0:
                normalized = _normalize_preprocessed_for_signature(
                    preprocessed,
                    floor=self.sig_text_floor,
                )
                if np.ptp(normalized) != 0:
                    return self._finalize_signature_image(normalized)
                return self._finalize_signature_image(preprocessed)

        # Raw-signature path
        sig = self.signature_preprocess
        scaled_bgr = _apply_scaling_stage(
            bgr,
            upscale_min=(sig.pre_upscale if sig is not None else None),
            downscale_max=(sig.pre_downscale if sig is not None else None),
        )
        color_view = _convert_color_space(scaled_bgr, self.color_space)
        reject_mask = _mask_from_ranges(color_view, self.background_color_ranges)
        if reject_mask is not None:
            scaled_bgr = _zero_masked(scaled_bgr, reject_mask)

        normalized = _normalize_for_signature(
            scaled_bgr,
            floor=self.sig_text_floor,
            max_spread=self.sig_max_spread,
        )
        return self._finalize_signature_image(normalized)

    def _signature_uses_preprocessed_source(self) -> bool:
        return (
            self.signature_preprocess is not None
            and self.signature_preprocess.has_preprocess_overrides()
        )

    def _finalize_signature_image(self, image: np.ndarray) -> np.ndarray:
        sig = self.signature_preprocess
        return _apply_scaling_stage(
            image,
            upscale_min=(sig.post_upscale if sig is not None else None),
            downscale_max=(
                sig.post_downscale
                if sig is not None and sig.post_downscale is not None
                else _DEFAULT_SIGNATURE_POST_DOWNSCALE
            ),
        )


# ======================================================================
# Render-mode helpers
# ======================================================================

# Neutral dark background colour (BGR) used by masked_color / neutral_bg_color.
_NEUTRAL_BG_BGR: tuple[int, int, int] = (16, 16, 16)


def _render_masked_color(
    bgr: np.ndarray,
    text_mask: np.ndarray,
    morphology: str,
) -> np.ndarray:
    """Keep original text pixels in colour; black elsewhere."""
    # Apply morphology to the mask before selecting pixels.
    mask_plane = np.where(text_mask, np.uint8(255), np.uint8(0))
    mask_plane = _apply_morphology(mask_plane, morphology)
    refined_mask = mask_plane > 0

    out = np.zeros_like(bgr)
    out[refined_mask] = bgr[refined_mask]
    return cv2.cvtColor(out, cv2.COLOR_BGR2RGB)


def _render_neutral_bg_color(
    bgr: np.ndarray,
    text_mask: np.ndarray,
    morphology: str,
) -> np.ndarray:
    """Place text pixels on a flat neutral dark background.

    Similar to ``masked_color`` but the non-text fill is a dark grey rather
    than pure black, which avoids hard-zero artefacts when the OCR engine
    uses the absolute pixel value distribution.
    """
    mask_plane = np.where(text_mask, np.uint8(255), np.uint8(0))
    mask_plane = _apply_morphology(mask_plane, morphology)
    refined_mask = mask_plane > 0

    out = np.full_like(bgr, _NEUTRAL_BG_BGR)
    out[refined_mask] = bgr[refined_mask]
    return cv2.cvtColor(out, cv2.COLOR_BGR2RGB)


def _render_luma_boost_color(
    bgr: np.ndarray,
    floor_value: int,
) -> np.ndarray:
    """Contrast-stretch the luma channel while preserving chroma.

    Converts to LAB, applies a floor-stretch on L, converts back.  The
    chroma (A, B) channels are left untouched so RapidOCR retains the
    colour cues that distinguish, e.g., rarity-tinted glyphs.
    """
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    l_f32 = lab[..., 0].astype(np.float32)
    stretch = 255.0 / max(1, 255 - floor_value)
    boosted = np.clip((l_f32 - floor_value) * stretch, 0.0, 255.0).astype(np.uint8)
    lab[..., 0] = boosted
    out_bgr = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    return cv2.cvtColor(out_bgr, cv2.COLOR_BGR2RGB)


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


def _apply_scaling_stage(
    image: np.ndarray,
    *,
    upscale_min: Size2D | None,
    downscale_max: Size2D | None,
) -> np.ndarray:
    scaled = image
    if upscale_min is not None:
        scaled = _upscale_to_min(scaled, upscale_min)
    if downscale_max is not None:
        scaled = _downscale(scaled, downscale_max)
    if scaled.flags.c_contiguous:
        return scaled
    return np.ascontiguousarray(scaled)


def _upscale_to_min(image: np.ndarray, min_size: Size2D) -> np.ndarray:
    """Resize *image* up so both dimensions meet *min_size*."""
    h, w = image.shape[:2]
    min_w, min_h = min_size
    if w >= min_w and h >= min_h:
        return image if image.flags.c_contiguous else np.ascontiguousarray(image)

    scale = max(min_w / max(1, w), min_h / max(1, h))
    new_w = max(1, math.ceil(w * scale))
    new_h = max(1, math.ceil(h * scale))
    interpolation = cv2.INTER_NEAREST if _is_binary(image) else cv2.INTER_LINEAR
    return _resize_preserving_binary(
        image,
        (new_w, new_h),
        interpolation=interpolation,
    )


def _downscale(image: np.ndarray, max_size: Size2D) -> np.ndarray:
    """Resize *image* to fit within *max_size*, preserving aspect ratio."""
    h, w = image.shape[:2]
    max_w, max_h = max_size
    if w <= max_w and h <= max_h:
        return image if image.flags.c_contiguous else np.ascontiguousarray(image)
    scale = min(max_w / w, max_h / h)
    new_w = max(1, int(w * scale))
    new_h = max(1, int(h * scale))
    return _resize_preserving_binary(
        image,
        (new_w, new_h),
        interpolation=cv2.INTER_AREA,
    )


def _resize_preserving_binary(
    image: np.ndarray,
    size: Size2D,
    *,
    interpolation: int,
) -> np.ndarray:
    resized = cv2.resize(image, size, interpolation=interpolation)
    # Re-binarize if input was binary
    if _is_binary(image):
        resized = np.where(resized >= 32, np.uint8(255), np.uint8(0))
    return np.ascontiguousarray(resized)


def _is_binary(image: np.ndarray) -> bool:
    return bool(np.all((image == 0) | (image == 255)))


def _normalize_preprocessed_for_signature(
    image: np.ndarray,
    *,
    floor: int,
) -> np.ndarray:
    """Stabilize a preprocessed OCR plane before hashing.

    Floor-thresholded OCR preprocess often preserves a dim background ramp.
    Remove that residual background before downscaling so cache keys stay
    stable across small lighting and backdrop shifts.
    """
    gray = image
    if image.ndim == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)

    if _is_binary(gray):
        return np.ascontiguousarray(gray)

    return _threshold_plane(gray, floor=floor, margin=24)


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

def default_specs_path() -> Path:
    """Return the packaged default OCR region-spec TOML path."""
    return DEFAULT_SPECS_PATH


def load_specs_from_toml(path: str | Path | None = None) -> dict[str, OcrRegionSpec]:
    """Load an ``OcrRegionSpec`` registry from a TOML file.

    Parameters
    ----------
    path:
        Path to the TOML config.  If ``None``, uses the packaged default
        ``src/wuwa_inventory_kamera/config/ocr_region_specs.toml``.

    Returns
    -------
    dict[str, OcrRegionSpec]
        Mapping from ``roi_key`` to its spec.
    """
    import tomllib

    if path is None:
        path = default_specs_path()

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
    # Detect if this section is a spec (has known keys) or a namespace.
    # "ocr" and "signature" are recognised as spec-level sub-tables, not as
    # namespace recursion targets.
    spec_keys = {
        "color_space", "text_color_ranges", "threshold_mode",
        "floor_value", "morphology", "allowed_chars", "cache_mode",
        "sig_text_floor", "sig_max_spread", "sig_downscale",
        "invert", "background_color_ranges",
        "rarity_source", "rarity_overrides", "fallback", "single_line",
        "signature", "pre_upscale", "pre_downscale",
        "post_upscale", "post_downscale",
        "spec_version",
        "ocr",        # new split-format OCR sub-section
        "render_mode",
    }
    has_spec_keys = any(k in spec_keys for k in section)
    # "ocr" is now treated like "signature" — consumed by _build_spec, not
    # recursed into for generating child specs.
    _NO_RECURSE = {"rarity_overrides", "fallback", "signature", "ocr"}
    has_sub_tables = any(
        isinstance(v, dict) and k not in _NO_RECURSE
        for k, v in section.items()
    )

    if has_spec_keys:
        spec = _build_spec(prefix, section, spec_version)
        out[prefix] = spec
    if has_sub_tables:
        for k, v in section.items():
            if isinstance(v, dict) and k not in _NO_RECURSE:
                _parse_section(f"{prefix}.{k}", v, spec_version, out)


def _build_spec(
    roi_key: str,
    data: dict,
    spec_version: str,
) -> OcrRegionSpec:
    """Construct an OcrRegionSpec from a parsed TOML section.

    Supports two TOML layouts:

    * **Legacy flat** — all fields at the section level::

        [echoes.level]
        single_line = true
        cache_mode  = "transient"

    * **Split OCR/signature** — OCR settings under ``[section.ocr]`` and
      cache/signature settings under ``[section.signature]``::

        [echoes.level.ocr]
        single_line  = true
        post_upscale = [64, 64]

        [echoes.level.signature]
        cache_mode = "transient"
    """
    kwargs: dict = {
        "roi_key": roi_key,
        "spec_version": data.get("spec_version", spec_version),
    }

    # Determine the source for OCR-side parameters.  In the split format the
    # "ocr" sub-dict owns them; in the legacy format they live at the top level.
    ocr_source: dict = data.get("ocr") if isinstance(data.get("ocr"), dict) else data  # type: ignore[assignment]

    for simple_key in (
        "color_space", "threshold_mode", "floor_value", "morphology",
        "allowed_chars", "sig_text_floor", "sig_max_spread",
        "invert", "single_line", "render_mode",
    ):
        if simple_key in ocr_source:
            kwargs[simple_key] = ocr_source[simple_key]

    for scale_key in (
        "pre_upscale",
        "pre_downscale",
        "post_upscale",
        "post_downscale",
    ):
        if scale_key in ocr_source:
            kwargs[scale_key] = _parse_size(ocr_source[scale_key], key=scale_key)

    if "text_color_ranges" in ocr_source:
        kwargs["text_color_ranges"] = _parse_color_ranges(ocr_source["text_color_ranges"])

    if "background_color_ranges" in ocr_source:
        kwargs["background_color_ranges"] = _parse_color_ranges(
            ocr_source["background_color_ranges"]
        )

    if "rarity_overrides" in ocr_source:
        by_rarity: dict[int, ColorRangeList] = {}
        for rarity_str, rarity_data in ocr_source["rarity_overrides"].items():
            rarity_int = int(rarity_str)
            if "text_color_ranges" in rarity_data:
                by_rarity[rarity_int] = _parse_color_ranges(
                    rarity_data["text_color_ranges"]
                )
        if by_rarity:
            kwargs["text_color_ranges_by_rarity"] = by_rarity

    # Fallback ranges (stored in base text_color_ranges when rarity is absent).
    if "fallback" in ocr_source:
        fallback = ocr_source["fallback"]
        if "text_color_ranges" in fallback:
            if "text_color_ranges" not in kwargs:
                kwargs["text_color_ranges"] = _parse_color_ranges(
                    fallback["text_color_ranges"]
                )
                # Record a different fallback colour space if declared, so that
                # preprocess() evaluates base ranges in the correct space.
                fallback_cs = fallback.get("color_space")
                parent_cs = kwargs.get("color_space", "gray")
                if fallback_cs and fallback_cs != parent_cs:
                    kwargs["fallback_color_space"] = fallback_cs

    # cache_mode: top-level (legacy) or hoisted from [section.signature].
    if "cache_mode" in data:
        kwargs["cache_mode"] = data["cache_mode"]
    else:
        sig_raw = data.get("signature")
        if isinstance(sig_raw, dict) and "cache_mode" in sig_raw:
            kwargs["cache_mode"] = sig_raw["cache_mode"]

    raw_signature = data.get("signature")
    signature_data: dict = raw_signature if isinstance(raw_signature, dict) else {}
    legacy_sig_downscale = data.get("sig_downscale")
    if signature_data or legacy_sig_downscale is not None:
        signature = _build_signature_preprocess(
            signature_data,
            legacy_post_downscale=legacy_sig_downscale,
        )
        if signature is not None:
            kwargs["signature_preprocess"] = signature

    return OcrRegionSpec(**kwargs)


def _build_signature_preprocess(
    data: dict,
    *,
    legacy_post_downscale: list[int] | tuple[int, int] | None = None,
) -> SignaturePreprocessSpec | None:
    kwargs: dict = {}

    for simple_key in (
        "color_space", "threshold_mode", "floor_value", "morphology",
        "invert", "single_line",
    ):
        if simple_key in data:
            kwargs[simple_key] = data[simple_key]

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

    if "fallback" in data and isinstance(data["fallback"], dict):
        fallback = data["fallback"]
        if "text_color_ranges" in fallback and "text_color_ranges" not in kwargs:
            kwargs["text_color_ranges"] = _parse_color_ranges(
                fallback["text_color_ranges"]
            )
        fallback_cs = fallback.get("color_space")
        if fallback_cs is not None:
            kwargs["fallback_color_space"] = fallback_cs

    for scale_key in (
        "pre_upscale",
        "pre_downscale",
        "post_upscale",
        "post_downscale",
    ):
        if scale_key in data:
            kwargs[scale_key] = _parse_size(data[scale_key], key=scale_key)

    if legacy_post_downscale is not None and "post_downscale" not in kwargs:
        kwargs["post_downscale"] = _parse_size(
            legacy_post_downscale,
            key="sig_downscale",
        )

    if not kwargs:
        return None
    return SignaturePreprocessSpec(**kwargs)


def _parse_size(raw: list[int] | tuple[int, int], *, key: str) -> Size2D:
    if len(raw) != 2:
        raise ValueError(f"{key} must contain exactly two integers")
    width = int(raw[0])
    height = int(raw[1])
    if width <= 0 or height <= 0:
        raise ValueError(f"{key} values must be positive integers")
    return width, height


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
