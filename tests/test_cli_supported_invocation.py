from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / 'src'


def _module_env() -> dict[str, str]:
    env = os.environ.copy()
    pythonpath_parts = [str(SRC_ROOT)]
    existing = env.get('PYTHONPATH')
    if existing:
        pythonpath_parts.append(existing)
    env['PYTHONPATH'] = os.pathsep.join(pythonpath_parts)
    return env


def _run_module(module: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, '-m', module, *args],
        cwd=PROJECT_ROOT,
        env=_module_env(),
        capture_output=True,
        text=True,
        check=False,
    )


def test_reprocess_module_invocation_is_supported() -> None:
    result = _run_module('wuwa_inventory_kamera.cli.reprocess', '--help')

    assert result.returncode == 0, result.stderr or result.stdout
    assert 'Re-run OCR processing' in result.stdout


def test_detect_sonata_icon_module_invocation_is_supported() -> None:
    result = _run_module('wuwa_inventory_kamera.cli.detect_sonata_icon', '--help')

    assert result.returncode == 0, result.stderr or result.stdout
    assert 'build' in result.stdout
    assert 'detect' in result.stdout


def test_update_assets_module_invocation_is_supported() -> None:
    result = _run_module('wuwa_inventory_kamera.cli.update_assets', '--help')

    assert result.returncode == 0, result.stderr or result.stdout
    assert 'status' in result.stdout
    assert 'update' in result.stdout
    assert 'audit' in result.stdout