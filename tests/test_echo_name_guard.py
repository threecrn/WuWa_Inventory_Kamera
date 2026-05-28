from __future__ import annotations

import numpy as np

import wuwa_inventory_kamera.scraping.service.ocr_service as ocr_service_module
from wuwa_inventory_kamera.scraping.service.ocr_service import (
    _allowed_chars_from_names,
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


def test_allowed_chars_from_names_includes_case_variants() -> None:
    allowed = _allowed_chars_from_names([
        'jué',
        'scar:aberrantnightmare',
    ])

    assert allowed is not None
    assert 'j' in allowed
    assert 'J' in allowed
    assert 'é' in allowed
    assert 'É' in allowed
    assert ':' in allowed


def test_runtime_echo_name_allowed_chars_uses_selected_language_locale_data(
    tmp_path,
    monkeypatch,
) -> None:
    data_dir = tmp_path / 'data'
    (data_dir / 'locale' / 'ja').mkdir(parents=True)
    (data_dir / 'languages.json').write_text(
        '{"English": "en", "日本語": "ja"}',
        encoding='utf-8',
    )
    (data_dir / 'locale' / 'ja' / 'echoes.json').write_text(
        '{"bellbornegeochelone": {"display_name": "先兵岩塊", "normalized": "先兵岩塊", "aliases": ["先兵岩塊"]}, "scaraberrantnightmare": {"display_name": "jué", "normalized": "jué", "aliases": ["jué"]}}',
        encoding='utf-8',
    )

    monkeypatch.setattr(ocr_service_module, 'basePATH', tmp_path)
    monkeypatch.setattr(ocr_service_module.app_config, 'gameLanguage', '日本語')
    monkeypatch.setattr(ocr_service_module, '_ECHO_NAME_RUNTIME_ALLOWED_CACHE_KEY', None)
    monkeypatch.setattr(ocr_service_module, '_ECHO_NAME_RUNTIME_ALLOWED_CACHE_VALUE', None)

    allowed = ocr_service_module._runtime_echo_name_allowed_chars()

    assert allowed is not None
    assert '先' in allowed
    assert '兵' in allowed
    assert 'é' in allowed
    assert 'É' in allowed


def test_runtime_echo_name_allowed_chars_prefers_generated_locale_lookup(
    tmp_path,
    monkeypatch,
) -> None:
    data_dir = tmp_path / 'data'
    (data_dir / 'locale' / 'ja' / 'lookup').mkdir(parents=True)
    (data_dir / 'ja').mkdir(parents=True)
    (data_dir / 'languages.json').write_text(
        '{"English": "en", "日本語": "ja"}',
        encoding='utf-8',
    )
    (data_dir / 'locale' / 'ja' / 'lookup' / 'echoes.json').write_text(
        '{"鐘鳴の亀守": "bellbornegeochelone", "jué": "scaraberrantnightmare"}',
        encoding='utf-8',
    )
    (data_dir / 'ja' / 'echoes.json').write_text(
        '{"legacy": 1}',
        encoding='utf-8',
    )

    monkeypatch.setattr(ocr_service_module, 'basePATH', tmp_path)
    monkeypatch.setattr(ocr_service_module.app_config, 'gameLanguage', '日本語')
    monkeypatch.setattr(ocr_service_module, '_ECHO_NAME_RUNTIME_ALLOWED_CACHE_KEY', None)
    monkeypatch.setattr(ocr_service_module, '_ECHO_NAME_RUNTIME_ALLOWED_CACHE_VALUE', None)

    allowed = ocr_service_module._runtime_echo_name_allowed_chars()

    assert allowed is not None
    assert '鐘' in allowed
    assert '亀' in allowed
    assert 'é' in allowed
    assert 'É' in allowed
    assert 'l' not in allowed
