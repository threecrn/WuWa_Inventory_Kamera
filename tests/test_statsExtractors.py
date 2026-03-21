"""
tests.test_statsExtractors
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Quality regression test for :mod:`scraping.processing.statsExtractor`.

For each extractor implementation, every echo in the reference session
(echo_0000 … echo_0099) that has all required files is run through the
extractor and compared against the reference output stored in
``debug/result.json`` (produced by the original RapidOCR pipeline).

Test data
---------
``K:/wuwa/export/2026-03-07_17-42-36/raw/echo_{NNNN}/debug/``

    stats_name.png   — cropped stat-names column (colour, as seen by OCR)
    stats_value.png  — cropped stat-values column (colour)
    result.json      — reference output: contains ``tuneLv`` and ``stats``

Run with::

    pytest tests/test_statsExtractors.py -v

Add ``--tb=short`` to see failure details without full tracebacks.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from scraping.processing.echoesValidator import expected_sub_count, infer_cost, validate_echo_stats
from scraping.processing.statsExtractor import (
    RapidOcrStatsExtractor,
    RapidOcrCoordStatsExtractor,
    TesserOcrCoordStatsExtractor,
    TesserOcrStatsExtractor,
)

# ---------------------------------------------------------------------------
# Test-data discovery
# ---------------------------------------------------------------------------

_SESSION_RAW = Path("K:/wuwa/export/2026-03-07_17-42-36/raw")
_ECHO_INDICES = range(100, 200)  # echo_0100 … echo_0199


def _collect_cases() -> list[pytest.param]:
    """
    Walk the reference session and collect all echo directories that have
    the three required files.  Returns a list of ``pytest.param`` objects
    so each case gets an informative ID in the test report.
    """
    cases: list[pytest.param] = []
    for idx in _ECHO_INDICES:
        debug = _SESSION_RAW / f"echo_{idx:04d}" / "debug"
        name_img  = debug / "stats_name.png"
        value_img = debug / "stats_value.png"
        result    = debug / "result.json"
        if name_img.exists() and value_img.exists() and result.exists():
            cases.append(pytest.param(idx, debug, id=f"echo_{idx:04d}"))
    return cases


_CASES = _collect_cases()

if not _CASES:
    pytest.skip(
        f"No test data found under {_SESSION_RAW}. "
        "Mount the drive or adjust _SESSION_RAW.",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_image(path: Path) -> np.ndarray:
    return np.array(Image.open(path).convert("RGB"))


def _load_expected(debug_dir: Path) -> tuple[int, int, int, int, dict]:
    """Return ``(cost, rarity, level, tune_lv, stats)`` from ``result.json``."""
    data = json.loads((debug_dir / "result.json").read_text(encoding="utf-8"))
    echo_data = next(iter(data.values()))
    cost = echo_data.get("_cost") or infer_cost(echo_data["stats"]) or 0
    return cost, echo_data["rarity"], echo_data["level"], echo_data["tuneLv"], echo_data["stats"]


def _retry_if_needed(
    extractor,
    name_crop: np.ndarray,
    value_crop: np.ndarray,
    idx: int,
    cost: int,
    rarity: int,
    level: int,
    tune_lv: int,
    stats: dict,
) -> tuple[int, dict]:
    """Mirror _processRawScan's retry logic: retry when validation fails or
    a level-25 echo has fewer than 5 substats."""
    sub_count = len(stats.get("sub", {}))
    missing_substats = sub_count < expected_sub_count(level)
    vresult = validate_echo_stats(cost, level, rarity, stats) if cost else None
    if missing_substats or (vresult is not None and not vresult.valid):
        tune_lv, stats, _ = extractor.retry_execute(name_crop, value_crop, idx)
    return tune_lv, stats


# ---------------------------------------------------------------------------
# Extractor fixtures — skip when the backend library is not installed
# ---------------------------------------------------------------------------

@pytest.fixture(
    scope="module",
    params=[pytest.param(False, id="colour"), pytest.param(True, id="bw")],
)
def rapid_extractor(request):
    return RapidOcrStatsExtractor(use_bw=request.param)


@pytest.fixture(
    scope="module",
    params=[pytest.param(False, id="colour"), pytest.param(True, id="bw")],
)
def rapid_coord_extractor(request):
    return RapidOcrCoordStatsExtractor(use_bw=request.param)


@pytest.fixture(
    scope="module",
    params=[pytest.param(False, id="colour"), pytest.param(True, id="bw")],
)
def tesser_extractor(request):
    pytest.importorskip("tesserocr", reason="tesserocr not installed — skipping Tesseract tests")
    return TesserOcrStatsExtractor(use_bw=request.param)


@pytest.fixture(
    scope="module",
    params=[pytest.param(False, id="colour"), pytest.param(True, id="bw")],
)
def tesser_coord_extractor(request):
    pytest.importorskip("tesserocr", reason="tesserocr not installed — skipping Tesseract tests")
    return TesserOcrCoordStatsExtractor(use_bw=request.param)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRapidOcrStatsExtractor:
    """
    RapidOCR extractor against the reference session.

    The reference results were produced by the RapidOCR pipeline, so this
    extractor is expected to match exactly on every case.
    """

    @pytest.mark.parametrize("idx, debug_dir", _CASES)
    def test_stats_match(self, rapid_extractor, idx, debug_dir):
        name_crop  = _load_image(debug_dir / "stats_name.png")
        value_crop = _load_image(debug_dir / "stats_value.png")
        cost, rarity, level, expected_tune, expected_stats = _load_expected(debug_dir)

        tune_lv, stats, _ = rapid_extractor.execute(name_crop, value_crop, {}, scan_index=idx)
        tune_lv, stats = _retry_if_needed(
            rapid_extractor, name_crop, value_crop, idx, cost, rarity, level, tune_lv, stats
        )

        assert tune_lv == expected_tune, (
            f"echo_{idx:04d}: tune_lv {tune_lv!r} != expected {expected_tune!r}"
        )
        assert stats == expected_stats, (
            f"echo_{idx:04d}: stats mismatch\n"
            f"  got:      {stats}\n"
            f"  expected: {expected_stats}"
        )


class TestRapidOcrCoordStatsExtractor:
    """
    RapidOCR coordinate-aware extractor against the reference session.
    """

    @pytest.mark.parametrize("idx, debug_dir", _CASES)
    def test_stats_match(self, rapid_coord_extractor, idx, debug_dir):
        name_crop  = _load_image(debug_dir / "stats_name.png")
        value_crop = _load_image(debug_dir / "stats_value.png")
        cost, rarity, level, expected_tune, expected_stats = _load_expected(debug_dir)

        tune_lv, stats, _ = rapid_coord_extractor.execute(name_crop, value_crop, {}, scan_index=idx)
        tune_lv, stats = _retry_if_needed(
            rapid_coord_extractor, name_crop, value_crop, idx, cost, rarity, level, tune_lv, stats
        )

        assert tune_lv == expected_tune, (
            f"echo_{idx:04d}: tune_lv {tune_lv!r} != expected {expected_tune!r}"
        )
        assert stats == expected_stats, (
            f"echo_{idx:04d}: stats mismatch\n"
            f"  got:      {stats}\n"
            f"  expected: {expected_stats}"
        )

class TestTesserOcrStatsExtractor:
    """
    Tesseract line-order extractor against the reference session.

    Differences from the reference (RapidOCR) output are expected on some
    echoes due to OCR engine differences; each failure represents a case
    worth inspecting manually.
    """

    @pytest.mark.parametrize("idx, debug_dir", _CASES)
    def test_stats_match(self, tesser_extractor, idx, debug_dir):
        name_crop  = _load_image(debug_dir / "stats_name.png")
        value_crop = _load_image(debug_dir / "stats_value.png")
        cost, rarity, level, expected_tune, expected_stats = _load_expected(debug_dir)

        tune_lv, stats, _ = tesser_extractor.execute(name_crop, value_crop, {}, scan_index=idx)
        tune_lv, stats = _retry_if_needed(
            tesser_extractor, name_crop, value_crop, idx, cost, rarity, level, tune_lv, stats
        )

        assert tune_lv == expected_tune, (
            f"echo_{idx:04d}: tune_lv {tune_lv!r} != expected {expected_tune!r}"
        )
        assert stats == expected_stats, (
            f"echo_{idx:04d}: stats mismatch\n"
            f"  got:      {stats}\n"
            f"  expected: {expected_stats}"
        )


class TestTesserOcrCoordStatsExtractor:
    """
    Tesseract coordinate-aware extractor against the reference session.

    This extractor should be more robust than the line-order Tesseract
    variant.  Same comparison approach — failures indicate cases to review.
    """

    @pytest.mark.parametrize("idx, debug_dir", _CASES)
    def test_stats_match(self, tesser_coord_extractor, idx, debug_dir):
        name_crop  = _load_image(debug_dir / "stats_name.png")
        value_crop = _load_image(debug_dir / "stats_value.png")
        cost, rarity, level, expected_tune, expected_stats = _load_expected(debug_dir)

        tune_lv, stats, _ = tesser_coord_extractor.execute(
            name_crop, value_crop, {}, scan_index=idx
        )
        tune_lv, stats = _retry_if_needed(
            tesser_coord_extractor, name_crop, value_crop, idx, cost, rarity, level, tune_lv, stats
        )

        assert tune_lv == expected_tune, (
            f"echo_{idx:04d}: tune_lv {tune_lv!r} != expected {expected_tune!r}"
        )
        assert stats == expected_stats, (
            f"echo_{idx:04d}: stats mismatch\n"
            f"  got:      {stats}\n"
            f"  expected: {expected_stats}"
        )
