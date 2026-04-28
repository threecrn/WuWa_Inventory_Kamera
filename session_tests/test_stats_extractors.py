"""
session_tests.test_stats_extractors
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Integration tests that validate each OCR stats-extractor implementation
against a captured WuWa echo scan session.

Supply the session directory at invocation time::

    uv run pytest session_tests/ --session-dir K:/wuwa/export/2026-03-07_17-42-36
    uv run pytest session_tests/ --session-dir K:/wuwa/export/2026-03-07_17-42-36 -v

Each echo whose ``raw/echo_NNNN/debug/`` directory contains
``stats_name.png``, ``stats_value.png``, and ``result.json`` is tested.
The ``result.json`` was produced by the original RapidOCR pipeline and
acts as ground truth.

The test skips automatically when ``--session-dir`` is not provided.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from scraping.processing.echoesValidator import expected_sub_count, infer_cost, validate_echo_stats
from scraping.processing.statsExtractor import (
    RapidOcrCoordStatsExtractor,
    RapidOcrStatsExtractor,
    TesserOcrCoordStatsExtractor,
    TesserOcrStatsExtractor,
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
    """Mirror _processRawScan's retry logic."""
    sub_count = len(stats.get("sub", {}))
    missing_substats = sub_count < expected_sub_count(level)
    vresult = validate_echo_stats(cost, level, rarity, stats) if cost else None
    if missing_substats or (vresult is not None and not vresult.valid):
        tune_lv, stats, _ = extractor.retry_execute(name_crop, value_crop, idx)
    return tune_lv, stats


def _run(extractor, stats_case: dict) -> None:
    idx: int = stats_case["index"]
    debug_dir: Path = stats_case["debug_dir"]
    name_crop  = _load_image(debug_dir / "stats_name.png")
    value_crop = _load_image(debug_dir / "stats_value.png")
    cost, rarity, level, expected_tune, expected_stats = _load_expected(debug_dir)

    tune_lv, stats, _ = extractor.execute(name_crop, value_crop, {}, scan_index=idx)
    tune_lv, stats = _retry_if_needed(
        extractor, name_crop, value_crop, idx, cost, rarity, level, tune_lv, stats
    )

    assert tune_lv == expected_tune, (
        f"echo_{idx:04d}: tune_lv {tune_lv!r} != expected {expected_tune!r}"
    )
    assert stats == expected_stats, (
        f"echo_{idx:04d}: stats mismatch\n"
        f"  got:      {stats}\n"
        f"  expected: {expected_stats}"
    )


# ---------------------------------------------------------------------------
# Extractor fixtures
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
# Tests  (stats_case parametrized by conftest.pytest_generate_tests)
# ---------------------------------------------------------------------------

def test_rapid_ocr(rapid_extractor, stats_case) -> None:
    """RapidOCR line-order extractor — expected to match ground truth exactly."""
    if stats_case is None:
        pytest.skip("No stats data — supply --session-dir.")
    _run(rapid_extractor, stats_case)


def test_rapid_ocr_coord(rapid_coord_extractor, stats_case) -> None:
    """RapidOCR coordinate-aware extractor."""
    if stats_case is None:
        pytest.skip("No stats data — supply --session-dir.")
    _run(rapid_coord_extractor, stats_case)


def test_tesser_ocr(tesser_extractor, stats_case) -> None:
    """Tesseract line-order extractor."""
    if stats_case is None:
        pytest.skip("No stats data — supply --session-dir.")
    _run(tesser_extractor, stats_case)


def test_tesser_ocr_coord(tesser_coord_extractor, stats_case) -> None:
    """Tesseract coordinate-aware extractor."""
    if stats_case is None:
        pytest.skip("No stats data — supply --session-dir.")
    _run(tesser_coord_extractor, stats_case)
