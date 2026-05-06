from __future__ import annotations

import numpy as np

from wuwa_inventory_kamera.scraping.service.ocr_service import (
    _echo_name_candidate_from_results,
    _is_plausible_echo_name_results,
)


def _bbox() -> np.ndarray:
    return np.asarray([[0, 0], [8, 0], [8, 8], [0, 8]], dtype=np.float32)


def test_echo_name_candidate_normalizes_spacing_and_prefix() -> None:
    results = [
        (
            "Phantom: Reminiscence: Threnodian - Voidborne Construct",
            0.99,
            _bbox(),
        )
    ]

    candidate = _echo_name_candidate_from_results(results)

    assert candidate == "reminiscence:threnodian-voidborneconstruct"


def test_echo_name_guard_accepts_known_name() -> None:
    results = [
        (
            "Reminiscence: Threnodian - Voidborne Construct",
            0.99,
            _bbox(),
        )
    ]

    assert _is_plausible_echo_name_results(results)


def test_echo_name_guard_rejects_garbage_candidate() -> None:
    results = [
        (
            "Relinis(ete:llelnoxlit1 Voixllelitltlt",
            0.65,
            _bbox(),
        )
    ]

    assert not _is_plausible_echo_name_results(results)
