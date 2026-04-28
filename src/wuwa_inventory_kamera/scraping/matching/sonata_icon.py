"""
wuwa_inventory_kamera.scraping.matching.sonata_icon
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Match a cropped in-game sonata icon against reference RGBA PNGs
in ``assets/IconS/``.

The matching algorithm is adapted from
``tools/match_sonata_icon/main.py``:

1. Load RGBA reference icons and split into (BGR, alpha).
2. Scale each reference to the scan icon dimensions (``INTER_AREA``).
3. Build a smooth circular mask from the icon crop dimensions
   (and optionally from calibrated circle parameters in the game ROI).
4. Combine the circular mask with each reference's alpha channel.
5. Compute the combined score: ``NCC − λ × colour_dist_norm``.
   NCC alone is colour-blind; the colour-distance penalty prevents
   near-grayscale or hue-different references from winning on structure
   alone.
6. Try both BGR and RGB channel orderings (scanned PNGs from the
   capture pipeline may be stored with non-standard byte order).
7. Return the best match.

Usage inside the pipeline::

    from wuwa_inventory_kamera.scraping.matching.sonata_icon import (
        SonataIconMatcher,
    )

    matcher = SonataIconMatcher()          # loads refs once
    key, score = matcher.match(icon_bgr)   # ~23×24 BGR crop
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
_REFS_DIR = _PROJECT_ROOT / "assets" / "IconS"

# How strongly mean-colour distance penalises the NCC score.
# score = NCC − _COLOR_PENALTY × colour_dist_norm
# where colour_dist_norm = ‖mean_scan − mean_ref‖₂ / (√3 × 255) ∈ [0, 1].
_COLOR_PENALTY: float = 1.5

# Minimum combined score to accept a match.  Below this, the match is
# considered unreliable and ``match()`` returns ``None``.
_MIN_SCORE: float = 0.20

_APOSTROPHE_RE = re.compile(r"['']")


# ---------------------------------------------------------------------------
# Circular masking
# ---------------------------------------------------------------------------

def make_circle_mask(
    h: int,
    w: int,
    cx: float | None = None,
    cy: float | None = None,
    r: float | None = None,
) -> np.ndarray:
    """Smooth (anti-aliased) circle mask with ~1 px soft edge.

    Returns a ``uint8`` image in [0, 255].  If *cx*/*cy*/*r* are
    ``None``, the circle is centred with ``radius = min(h, w) / 2 − 0.5``.
    """
    if cx is None:
        cx = w / 2.0
    if cy is None:
        cy = h / 2.0
    if r is None:
        r = min(h, w) / 2.0 - 0.5

    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    dist = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2)
    mask = np.clip(r - dist + 0.5, 0.0, 1.0)
    return (mask * 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# Reference loading
# ---------------------------------------------------------------------------

def load_references(
    refs_dir: Path = _REFS_DIR,
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Load reference RGBA icons.

    Returns ``{stem: (bgr, alpha)}`` where *bgr* is 3-channel and *alpha*
    is single-channel ``uint8``.  Icons without alpha get a solid mask.
    Only PNGs directly in *refs_dir* are loaded (subdirectories like
    ``templates/`` are skipped).
    """
    refs: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for p in sorted(refs_dir.glob("*.png")):
        img = cv2.imread(str(p), cv2.IMREAD_UNCHANGED)
        if img is None or img.ndim != 3:
            continue
        if img.shape[2] == 4:
            bgr = img[:, :, :3]
            alpha = img[:, :, 3]
        else:
            bgr = img
            alpha = np.full(img.shape[:2], 255, dtype=np.uint8)
        refs[p.stem] = (bgr, alpha)

    if not refs:
        logger.error("No reference PNGs found in %s", refs_dir)
    else:
        logger.info("Loaded %d reference icons from %s", len(refs), refs_dir)
    return refs


# ---------------------------------------------------------------------------
# Matching helpers
# ---------------------------------------------------------------------------

def _ncc_masked(
    img1: np.ndarray,
    img2: np.ndarray,
    mask: np.ndarray,
) -> float:
    """Normalised cross-correlation over masked pixels.

    Both images must have the same shape.  *mask* is single-channel uint8;
    pixels with ``mask > 127`` are included.
    """
    m = mask.ravel() > 127
    if not m.any():
        return 0.0
    c = img1.shape[2] if img1.ndim == 3 else 1
    a = img1.reshape(-1, c)[m].ravel().astype(np.float64)
    b = img2.reshape(-1, c)[m].ravel().astype(np.float64)
    a -= a.mean()
    b -= b.mean()
    denom = np.sqrt(np.dot(a, a) * np.dot(b, b))
    if denom < 1e-10:
        return 0.0
    return float(np.dot(a, b) / denom)


def _match_icon(
    scan_bgr: np.ndarray,
    scan_mask: np.ndarray,
    references: dict[str, tuple[np.ndarray, np.ndarray]],
) -> list[tuple[str, float]]:
    """Compare *scan_bgr* against every reference icon.

    Returns a list of ``(name, score)`` sorted best-first.
    """
    h, w = scan_bgr.shape[:2]
    scan_mask_bin = np.where(scan_mask > 127, np.uint8(255), np.uint8(0))

    results: list[tuple[str, float]] = []
    for name, (ref_bgr, ref_alpha) in references.items():
        ref_scaled = cv2.resize(ref_bgr, (w, h), interpolation=cv2.INTER_AREA)
        alpha_scaled = cv2.resize(ref_alpha, (w, h), interpolation=cv2.INTER_AREA)

        ref_mask_bin = np.where(alpha_scaled > 127, np.uint8(255), np.uint8(0))
        combined = cv2.bitwise_and(scan_mask_bin, ref_mask_bin)

        ncc = _ncc_masked(scan_bgr, ref_scaled, combined)

        m = combined.ravel() > 127
        if m.any():
            scan_mean = scan_bgr.reshape(-1, 3)[m].mean(axis=0).astype(np.float64)
            ref_mean = ref_scaled.reshape(-1, 3)[m].mean(axis=0).astype(np.float64)
            colour_dist_norm = float(
                np.linalg.norm(scan_mean - ref_mean) / (np.sqrt(3) * 255)
            )
        else:
            colour_dist_norm = 0.0

        score = ncc - _COLOR_PENALTY * colour_dist_norm
        results.append((name, score))

    results.sort(key=lambda x: x[1], reverse=True)
    return results


# ---------------------------------------------------------------------------
# Slug mapping
# ---------------------------------------------------------------------------

def _normalise_slug(name: str) -> str:
    """Strip apostrophes so filenames can be matched to sonataName keys."""
    return _APOSTROPHE_RE.sub("", name).lower()


def _build_slug_map(sonata_names: list[str]) -> dict[str, str]:
    """Build a mapping from normalised slug → original sonataName key.

    ``sonataName`` keys may contain apostrophes (e.g. ``flamewing'sshadow``)
    while reference filenames do not (``flamewingsshadow``).
    """
    return {_normalise_slug(k): k for k in sonata_names}


# ---------------------------------------------------------------------------
# SonataIconMatcher
# ---------------------------------------------------------------------------

class SonataIconMatcher:
    """Stateless matcher: load references once, call :meth:`match` per echo.

    Parameters
    ----------
    refs_dir:
        Directory containing RGBA reference PNGs.  Defaults to
        ``assets/IconS/``.
    """

    def __init__(self, refs_dir: Path | None = None) -> None:
        self._refs = load_references(refs_dir or _REFS_DIR)

    def match(
        self,
        icon_bgr: np.ndarray,
        cx: float | None = None,
        cy: float | None = None,
        r: float | None = None,
    ) -> tuple[str, float] | None:
        """Match *icon_bgr* against loaded references.

        Parameters
        ----------
        icon_bgr:
            The cropped sonata icon (BGR, typically ~23×24 px).
        cx, cy, r:
            Optional calibrated circle parameters (in icon-crop space).
            When ``None``, defaults to centred circle with ``r = min(h,w)/2 − 0.5``.

        Returns
        -------
        tuple[str, float] | None
            ``(reference_stem, combined_score)`` for the best match, or
            ``None`` if no reference scored above ``_MIN_SCORE``.
            The *reference_stem* is the filename without extension
            (e.g. ``"freezingfrost"``).
        """
        if not self._refs:
            logger.warning("No references loaded — cannot match sonata icon.")
            return None

        h, w = icon_bgr.shape[:2]
        mask = make_circle_mask(h, w, cx, cy, r)

        # Try both channel orderings — scanned PNGs may use non-standard
        # byte order.
        results_native = _match_icon(icon_bgr, mask, self._refs)
        scan_swapped = icon_bgr[:, :, ::-1].copy()
        results_swapped = _match_icon(scan_swapped, mask, self._refs)

        if results_swapped and results_swapped[0][1] > results_native[0][1]:
            results = results_swapped
            logger.debug("Channel swap improved matching — using swapped channels.")
        else:
            results = results_native

        if not results:
            return None

        best_name, best_score = results[0]
        if best_score < _MIN_SCORE:
            logger.warning(
                "Best sonata icon match %r scored %.4f < threshold %.2f — rejecting.",
                best_name, best_score, _MIN_SCORE,
            )
            return None

        logger.debug(
            "Sonata icon match: %s (score=%.4f)  runner-up=%s (%.4f)",
            best_name, best_score,
            results[1][0] if len(results) > 1 else "-",
            results[1][1] if len(results) > 1 else 0.0,
        )
        return best_name, best_score

    def match_to_sonata_key(
        self,
        icon_bgr: np.ndarray,
        sonata_names: list[str],
        cx: float | None = None,
        cy: float | None = None,
        r: float | None = None,
    ) -> str | None:
        """Match an icon and map the result to a ``sonataName`` key.

        Handles the apostrophe difference between reference filenames
        (``flamewingsshadow``) and sonataName keys
        (``flamewing'sshadow``).

        Returns the sonataName key, or ``None`` if no match.
        """
        result = self.match(icon_bgr, cx, cy, r)
        if result is None:
            return None

        ref_stem, score = result
        slug_map = _build_slug_map(sonata_names)
        normalised = _normalise_slug(ref_stem)

        if normalised in slug_map:
            return slug_map[normalised]

        # Direct match (reference stem == sonataName key)
        if ref_stem in sonata_names:
            return ref_stem

        logger.warning(
            "Sonata icon matched reference %r (score=%.4f) but no "
            "corresponding sonataName key found.",
            ref_stem, score,
        )
        return None
