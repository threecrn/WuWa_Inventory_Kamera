"""
session_tests.test_sonata_icon_matching
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Integration test that validates sonata icon template-matching accuracy
(:class:`~wuwa_inventory_kamera.scraping.matching.sonata_icon.SonataIconMatcher`)
against the ground-truth session
``K:\\wuwa\\export\\2026-03-29_15-04-03``.

For each echo directory the test:
  1. Loads ``full.png``.
  2. Crops the ``sonataIcon`` region using the screen resolution from
     ``manifest.json``.
  3. Calls :meth:`SonataIconMatcher.match_to_sonata_key` with calibrated
     circle parameters from the layout.
  4. Compares the result with the ground-truth ``sonata`` field in
     ``echoes_wuwainventorykamera.json``.

The test asserts overall accuracy ≥ 95 % and prints a per-sonata
breakdown as well as the full list of mismatches.

Run with::

    uv run pytest session_tests/ -v

Skipped automatically when the session directory is not present.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_SESSION_DIR = Path("K:/wuwa/export/2026-03-29_15-04-03")
_RAW_DIR     = _SESSION_DIR / "raw"
_MANIFEST    = _SESSION_DIR / "manifest.json"
_GROUND_TRUTH = _SESSION_DIR / "echoes_wuwainventorykamera.json"

if not _SESSION_DIR.exists():
    pytest.skip(
        f"Session directory not found: {_SESSION_DIR}. "
        "Mount the drive or adjust _SESSION_DIR.",
        allow_module_level=True,
    )

# ---------------------------------------------------------------------------
# Load session metadata
# ---------------------------------------------------------------------------

_manifest: dict = json.loads(_MANIFEST.read_text(encoding="utf-8"))
_SCREEN_W: int = _manifest["screen_width"]
_SCREEN_H: int = _manifest["screen_height"]

# ---------------------------------------------------------------------------
# Load ground truth: scan_index → sonata key
# ---------------------------------------------------------------------------

_gt_data: list[dict] = json.loads(_GROUND_TRUTH.read_text(encoding="utf-8"))
_INDEX_TO_SONATA: dict[int, str] = {
    echo_data["_scanIndex"]: echo_data["sonata"]
    for entry in _gt_data
    for echo_data in entry.values()
    if "_scanIndex" in echo_data
}

# ---------------------------------------------------------------------------
# Sonata name list (used by match_to_sonata_key for slug resolution)
# ---------------------------------------------------------------------------

_SONATA_NAMES_FILE = Path(__file__).parent.parent / "data" / "en" / "sonataName.json"
_SONATA_NAMES: list[str] = list(
    json.loads(_SONATA_NAMES_FILE.read_text(encoding="utf-8")).keys()
)

# ---------------------------------------------------------------------------
# Layout coordinates (crop bounds + circle params for the icon)
# ---------------------------------------------------------------------------

from wuwa_inventory_kamera.game.screen_info import ScreenInfo

_LAYOUT = ScreenInfo(_SCREEN_W, _SCREEN_H)
_ICON_ROI = _LAYOUT.echoes.sonataIcon          # Coordinates(x, y, w, h)
_CIRCLE   = _LAYOUT.echoes.sonataIconCircle    # ScreenInfoObject with .circle and .radius

_CIRCLE_CX: float | None = _CIRCLE.circle.x if _CIRCLE else None
_CIRCLE_CY: float | None = _CIRCLE.circle.y if _CIRCLE else None
_CIRCLE_R:  float | None = _CIRCLE.radius    if _CIRCLE else None

# ---------------------------------------------------------------------------
# Matcher (instantiated once for the whole module)
# ---------------------------------------------------------------------------

from wuwa_inventory_kamera.scraping.matching.sonata_icon import SonataIconMatcher

_MATCHER = SonataIconMatcher()

# ---------------------------------------------------------------------------
# Test-case collection
# ---------------------------------------------------------------------------

def _collect_cases() -> list[pytest.param]:
    """Return one pytest.param per echo directory that has both full.png
    and a ground-truth sonata label."""
    cases: list[pytest.param] = []
    for echo_dir in sorted(_RAW_DIR.glob("echo_*")):
        full_png = echo_dir / "full.png"
        if not full_png.exists():
            continue
        meta = json.loads((echo_dir / "meta.json").read_text(encoding="utf-8"))
        idx = meta["index"]
        if idx not in _INDEX_TO_SONATA:
            continue  # no ground truth for this echo (was rejected by the pipeline)
        cases.append(
            pytest.param(idx, full_png, _INDEX_TO_SONATA[idx], id=f"echo_{idx:04d}")
        )
    return cases


_CASES = _collect_cases()

if not _CASES:
    pytest.skip(
        f"No testable echo directories found under {_RAW_DIR}.",
        allow_module_level=True,
    )

# ---------------------------------------------------------------------------
# Accuracy tracking (module-level so the summary fixture can read it)
# ---------------------------------------------------------------------------

_results: dict[str, list[int]] = defaultdict(list)   # sonata → [correct, total]


# ---------------------------------------------------------------------------
# Per-echo parametrised test
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("scan_index,full_png,expected_sonata", _CASES)
def test_sonata_icon_match(
    scan_index: int,
    full_png: Path,
    expected_sonata: str,
) -> None:
    """Match the sonata icon crop of one echo against reference PNGs."""
    full = cv2.imread(str(full_png), cv2.IMREAD_COLOR)
    assert full is not None, f"cv2.imread failed for {full_png}"

    # Crop the sonata icon region
    x  = int(_ICON_ROI.x)
    y  = int(_ICON_ROI.y)
    w  = int(_ICON_ROI.w)
    h  = int(_ICON_ROI.h)
    icon = full[y : y + h, x : x + w]
    assert icon.size > 0, f"Empty icon crop at ({x},{y},{w},{h}) for {full_png}"

    predicted = _MATCHER.match_to_sonata_key(
        icon,
        _SONATA_NAMES,
        cx=_CIRCLE_CX,
        cy=_CIRCLE_CY,
        r=_CIRCLE_R,
    )

    # Track for per-sonata accuracy summary
    is_correct = predicted == expected_sonata
    _results[expected_sonata].append(int(is_correct))

    if predicted != expected_sonata:
        pytest.fail(
            f"echo_{scan_index:04d}: expected {expected_sonata!r}, "
            f"got {predicted!r}"
        )


# ---------------------------------------------------------------------------
# Summary fixture — runs once after all parametrised cases
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def _print_accuracy_summary(request):
    """Print per-sonata and overall accuracy after the session ends."""
    yield
    if not _results:
        return

    total_correct = 0
    total_count   = 0
    lines = ["\n── Sonata icon matching accuracy ──────────────────────"]
    for sonata in sorted(_results):
        hits   = sum(_results[sonata])
        count  = len(_results[sonata])
        pct    = 100 * hits / count if count else 0.0
        total_correct += hits
        total_count   += count
        marker = "" if hits == count else "  ← misses"
        lines.append(f"  {sonata:<30s}  {hits:3d}/{count:3d}  ({pct:6.1f}%){marker}")

    overall = 100 * total_correct / total_count if total_count else 0.0
    lines.append("─" * 56)
    lines.append(
        f"  {'TOTAL':<30s}  {total_correct:3d}/{total_count:3d}  ({overall:6.1f}%)"
    )
    print("\n".join(lines))


def test_overall_accuracy() -> None:
    """Assert that overall sonata icon matching accuracy is ≥ 99 %."""
    total_correct = sum(sum(v) for v in _results.values())
    total_count   = sum(len(v) for v in _results.values())

    if total_count == 0:
        pytest.skip("No results collected — parametrised tests may not have run yet.")

    accuracy = total_correct / total_count
    assert accuracy >= 0.99, (
        f"Sonata icon matching accuracy {accuracy:.1%} is below the 99 % threshold "
        f"({total_correct}/{total_count} correct)."
    )
