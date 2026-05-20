from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "cli" / "tune_region_spec.py"


def _write_rgb_png(path: Path, image_rgb: np.ndarray) -> None:
    ok = cv2.imwrite(str(path), cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR))
    assert ok


def _read_rgb_png(path: Path) -> np.ndarray:
    image_bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
    assert image_bgr is not None
    return cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return result


def test_tune_region_spec_writes_ocr_preview(tmp_path: Path) -> None:
    input_path = tmp_path / "region.png"
    output_path = tmp_path / "region.ocr.png"
    image_rgb = np.zeros((6, 6, 3), dtype=np.uint8)
    image_rgb[2, 2] = np.asarray([255, 255, 255], dtype=np.uint8)
    _write_rgb_png(input_path, image_rgb)

    _run_cli(
        str(input_path),
        "--output",
        str(output_path),
        "--type",
        "ocr",
        "--render-mode",
        "masked_color",
        "--color-space",
        "bgr",
        "--text-color-ranges",
        "255,255,255",
    )

    output_rgb = _read_rgb_png(output_path)

    assert output_rgb.shape == (6, 6, 3)
    assert output_rgb[2, 2].tolist() == [255, 255, 255]
    assert output_rgb[0, 0].tolist() == [0, 0, 0]


def test_tune_region_spec_writes_signature_preview(tmp_path: Path) -> None:
    input_path = tmp_path / "region.png"
    output_path = tmp_path / "region.signature.png"
    image_rgb = np.zeros((8, 8, 3), dtype=np.uint8)
    image_rgb[2:6, 3:5] = np.asarray([255, 255, 255], dtype=np.uint8)
    _write_rgb_png(input_path, image_rgb)

    _run_cli(
        str(input_path),
        "--output",
        str(output_path),
        "--type",
        "signature",
        "--signature-post-downscale",
        "8,8",
    )

    output_rgb = _read_rgb_png(output_path)

    assert output_rgb.shape == (8, 8, 3)
    assert np.array_equal(output_rgb[..., 0], output_rgb[..., 1])
    assert np.array_equal(output_rgb[..., 1], output_rgb[..., 2])
    assert output_rgb[3, 4, 0] == 255
    assert output_rgb[0, 0, 0] == 0