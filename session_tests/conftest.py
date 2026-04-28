"""
session_tests/conftest.py
~~~~~~~~~~~~~~~~~~~~~~~~~

pytest configuration for session-data integration tests.

Provides a ``--session-dir`` CLI option that points to a captured WuWa
echo scan session folder (the parent of ``raw/``).  Tests that require
external session data skip automatically when the option is not supplied
or the path does not exist.

Usage
-----
::

    uv run pytest session_tests/ --session-dir K:/wuwa/export/2026-03-29_15-04-03
    uv run pytest session_tests/ --session-dir K:/wuwa/export/2026-03-29_15-04-03 -v
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--session-dir",
        metavar="PATH",
        default=None,
        help=(
            "Path to a WuWa scan session folder containing raw/, manifest.json, "
            "and echoes_wuwainventorykamera.json.  "
            "Required for session_tests; tests are skipped if not supplied."
        ),
    )


def _require_session_dir(config: pytest.Config) -> Path | None:
    """Return the session dir Path, or None if not supplied / not found."""
    raw = config.getoption("--session-dir")
    if raw is None:
        return None
    p = Path(raw)
    return p if p.exists() else None


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def session_dir(request: pytest.FixtureRequest) -> Path:
    """Resolved session directory path.  Skips the test if not provided."""
    p = _require_session_dir(request.config)
    if p is None:
        raw = request.config.getoption("--session-dir")
        if raw is None:
            pytest.skip("--session-dir not supplied; skipping session test.")
        else:
            pytest.skip(f"--session-dir path does not exist: {raw}")
    return p


@pytest.fixture(scope="session")
def session_manifest(session_dir: Path) -> dict:
    """Parsed manifest.json from the session directory."""
    return json.loads((session_dir / "manifest.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def session_ground_truth(session_dir: Path) -> list[dict]:
    """Parsed echoes_wuwainventorykamera.json — list of echo dicts."""
    return json.loads(
        (session_dir / "echoes_wuwainventorykamera.json").read_text(encoding="utf-8")
    )


@pytest.fixture(scope="session")
def index_to_sonata(session_ground_truth: list[dict]) -> dict[int, str]:
    """Map scan_index → sonata key, built from ground-truth output."""
    return {
        echo_data["_scanIndex"]: echo_data["sonata"]
        for entry in session_ground_truth
        for echo_data in entry.values()
        if "_scanIndex" in echo_data
    }


# ---------------------------------------------------------------------------
# Dynamic parametrization for echo-level tests
# ---------------------------------------------------------------------------

def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parametrize ``echo_case`` and/or ``stats_case`` with session data."""
    if "echo_case" in metafunc.fixturenames:
        _parametrize_echo_cases(metafunc)
    if "stats_case" in metafunc.fixturenames:
        _parametrize_stats_cases(metafunc)


def _parametrize_echo_cases(metafunc: pytest.Metafunc) -> None:
    session_dir = _require_session_dir(metafunc.config)
    if session_dir is None:
        metafunc.parametrize("echo_case", [pytest.param(None, id="no-data")])
        return

    # Load ground truth to know which scan indices are valid
    gt_path = session_dir / "echoes_wuwainventorykamera.json"
    if not gt_path.exists():
        metafunc.parametrize("echo_case", [pytest.param(None, id="no-data")])
        return

    gt_data: list[dict] = json.loads(gt_path.read_text(encoding="utf-8"))
    index_to_sonata = {
        echo_data["_scanIndex"]: echo_data["sonata"]
        for entry in gt_data
        for echo_data in entry.values()
        if "_scanIndex" in echo_data
    }

    raw_dir = session_dir / "raw"
    cases: list[pytest.param] = []
    for echo_dir in sorted(raw_dir.glob("echo_*")):
        full_png = echo_dir / "full.png"
        meta_path = echo_dir / "meta.json"
        if not full_png.exists() or not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        idx = meta["index"]
        if idx not in index_to_sonata:
            continue
        cases.append(
            pytest.param(
                {"index": idx, "full_png": full_png, "sonata": index_to_sonata[idx]},
                id=f"echo_{idx:04d}",
            )
        )

    if not cases:
        metafunc.parametrize("echo_case", [pytest.param(None, id="no-data")])
    else:
        metafunc.parametrize("echo_case", cases)


def _parametrize_stats_cases(metafunc: pytest.Metafunc) -> None:
    session_dir = _require_session_dir(metafunc.config)
    if session_dir is None:
        metafunc.parametrize("stats_case", [pytest.param(None, id="no-data")])
        return

    raw_dir = session_dir / "raw"
    cases: list[pytest.param] = []
    for echo_dir in sorted(raw_dir.glob("echo_*")):
        debug = echo_dir / "debug"
        if not all((
            (debug / "stats_name.png").exists(),
            (debug / "stats_value.png").exists(),
            (debug / "result.json").exists(),
        )):
            continue
        idx = int(echo_dir.name.split("_")[1])
        cases.append(pytest.param({"index": idx, "debug_dir": debug}, id=echo_dir.name))

    if not cases:
        metafunc.parametrize("stats_case", [pytest.param(None, id="no-data")])
    else:
        metafunc.parametrize("stats_case", cases)
