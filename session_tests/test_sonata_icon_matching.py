"""
session_tests.test_sonata_icon_matching
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Integration test that validates sonata icon template-matching accuracy
(:class:`~wuwa_inventory_kamera.scraping.matching.sonata_icon.SonataIconMatcher`)
against a captured WuWa echo scan session.

Supply the session directory at invocation time::

    uv run pytest session_tests/ --session-dir K:/wuwa/export/2026-03-29_15-04-03
    uv run pytest session_tests/ --session-dir K:/wuwa/export/2026-03-29_15-04-03 -v

The test is skipped automatically when ``--session-dir`` is not provided
or the path does not exist.

For each echo in the session the test:
  1. Loads ``raw/echo_NNNN/full.png``.
  2. Crops the ``sonataIcon`` region using the screen resolution from
     ``manifest.json`` and :class:`~wuwa_inventory_kamera.game.screen_info.ScreenInfo`.
  3. Calls :meth:`SonataIconMatcher.match_to_sonata_key` with calibrated
     circle parameters from the layout.
  4. Compares the result with the ground-truth ``sonata`` field in
     ``echoes_wuwainventorykamera.json``.
  5. ``test_overall_accuracy`` asserts ≥ 99 % accuracy across all echoes.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import cv2
import pytest

# ---------------------------------------------------------------------------
# Sonata name list (language-independent slug resolution)
# ---------------------------------------------------------------------------

_SONATA_NAMES_FILE = Path(__file__).parent.parent / "data" / "en" / "sonataName.json"
_SONATA_NAMES: list[str] = list(
    json.loads(_SONATA_NAMES_FILE.read_text(encoding="utf-8")).keys()
)

# ---------------------------------------------------------------------------
# Matcher — loaded once for the whole module
# ---------------------------------------------------------------------------

from wuwa_inventory_kamera.scraping.matching.sonata_icon import SonataIconMatcher

_MATCHER = SonataIconMatcher()

# ---------------------------------------------------------------------------
# Accuracy tracking (module-level; updated by each parametrised test case)
# ---------------------------------------------------------------------------

_results: dict[str, list[int]] = defaultdict(list)  # sonata → [1|0, ...]


# ---------------------------------------------------------------------------
# Per-echo parametrised test  (cases injected by conftest.pytest_generate_tests)
# ---------------------------------------------------------------------------

def test_sonata_icon_match(
    echo_case,
    session_manifest: dict,
) -> None:
    """Match the sonata icon crop of one echo against reference PNGs."""
    if echo_case is None:
        pytest.skip("No echo data — supply --session-dir.")

    idx: int       = echo_case["index"]
    full_png: Path = echo_case["full_png"]
    expected: str  = echo_case["sonata"]

    screen_w: int = session_manifest["screen_width"]
    screen_h: int = session_manifest["screen_height"]

    # Resolve icon ROI and circle params from the layout
    from wuwa_inventory_kamera.game.screen_info import ScreenInfo
    layout   = ScreenInfo(screen_w, screen_h)
    icon_roi = layout.echoes.sonataIcon
    circle   = layout.echoes.sonataIconCircle

    cx = circle.circle.x if circle else None
    cy = circle.circle.y if circle else None
    r  = circle.radius   if circle else None

    # Load full screenshot and crop the icon
    full = cv2.imread(str(full_png), cv2.IMREAD_COLOR)
    assert full is not None, f"cv2.imread failed for {full_png}"

    x, y, w, h = int(icon_roi.x), int(icon_roi.y), int(icon_roi.w), int(icon_roi.h)
    icon = full[y : y + h, x : x + w]
    assert icon.size > 0, f"Empty icon crop at ({x},{y},{w},{h}) for {full_png}"

    predicted = _MATCHER.match_to_sonata_key(icon, _SONATA_NAMES, cx=cx, cy=cy, r=r)

    # Record result for the accuracy summary
    _results[expected].append(int(predicted == expected))

    if predicted != expected:
        pytest.fail(f"echo_{idx:04d}: expected {expected!r}, got {predicted!r}")


# ---------------------------------------------------------------------------
# Overall accuracy assertion
# ---------------------------------------------------------------------------

def test_overall_accuracy() -> None:
    """Assert that overall sonata icon matching accuracy is ≥ 99 %."""
    total_correct = sum(sum(v) for v in _results.values())
    total_count   = sum(len(v) for v in _results.values())

    if total_count == 0:
        pytest.skip("No results collected — run with --session-dir to populate.")

    accuracy = total_correct / total_count
    assert accuracy >= 0.99, (
        f"Sonata icon matching accuracy {accuracy:.2%} is below the 99 % threshold "
        f"({total_correct}/{total_count} correct)."
    )


# ---------------------------------------------------------------------------
# Per-sonata accuracy summary (printed at session teardown)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def _print_accuracy_summary() -> None:  # type: ignore[return]
    yield
    if not _results:
        return

    total_correct = total_count = 0
    lines = ["\n── Sonata icon matching accuracy ──────────────────────"]
    for sonata in sorted(_results):
        hits  = sum(_results[sonata])
        count = len(_results[sonata])
        pct   = 100 * hits / count if count else 0.0
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

    accuracy = total_correct / total_count
    assert accuracy >= 0.99, (
        f"Sonata icon matching accuracy {accuracy:.1%} is below the 99 % threshold "
        f"({total_correct}/{total_count} correct)."
    )
