from __future__ import annotations

import os
import time
from typing import cast

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('PySide6')
pytest.importorskip('qfluentwidgets')

from PySide6.QtWidgets import QApplication

import wuwa_inventory_kamera.scraping.scanning.session_orchestrator as session_orchestrator_module
from wuwa_inventory_kamera.ui.home import ScanThread


@pytest.fixture(scope='module')
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return cast(QApplication, app)


def _ordered_scan_result() -> dict[str, object]:
    return {
        'date': '2026-05-29_18-31-15',
        'echoes': [
            {
                '340000151': {
                    'echo_key': 'reminiscence:fenrico',
                    'level': 25,
                    'tuneLv': 5,
                    'sonata': 'lawofharmony',
                    'sonata_key': 'lawofharmony',
                    'rarity': 5,
                    'stats': {
                        'main': {
                            'cd%': 44.0,
                            'atk': 150,
                        },
                        'sub': {
                            'cd%': 13.8,
                            'atk': 40,
                            'cr%': 7.5,
                            'basicAttack%': 6.4,
                            'hp%': 7.1,
                        },
                    },
                    '_equipped': 'qiuyuan',
                    '_scanIndex': 17,
                    '_monsterId': 340000151,
                    '_cost': 4,
                },
            },
        ],
    }


def test_scan_thread_finished_signal_preserves_echo_key_order(qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    expected = _ordered_scan_result()

    class _FakeSessionOrchestrator:
        def __init__(self, **_kwargs) -> None:
            pass

        def run(self) -> dict[str, object]:
            return expected

    monkeypatch.setattr(session_orchestrator_module, 'SessionOrchestrator', _FakeSessionOrchestrator)

    thread = ScanThread(
        scrapers=['echoes'],
        ocr_providers=None,
        min_rarity=1,
        min_level=0,
        weapon_min_rarity=None,
        weapon_min_level=None,
        inventory_key='b',
        export_folder='.',
    )

    results: list[dict[str, object]] = []
    errors: list[str] = []
    thread.finished.connect(lambda result: results.append(cast(dict[str, object], result)))
    thread.error.connect(errors.append)

    thread.start()
    assert thread.wait(5000)

    deadline = time.monotonic() + 2.0
    while not results and not errors and time.monotonic() < deadline:
        qapp.processEvents()

    assert not errors
    assert len(results) == 1

    result = results[0]
    assert result == expected

    echo = cast(dict[str, object], cast(list[object], result['echoes'])[0])['340000151']
    echo_data = cast(dict[str, object], echo)
    assert list(echo_data.keys()) == [
        'echo_key',
        'level',
        'tuneLv',
        'sonata',
        'sonata_key',
        'rarity',
        'stats',
        '_equipped',
        '_scanIndex',
        '_monsterId',
        '_cost',
    ]

    stats = cast(dict[str, object], echo_data['stats'])
    assert list(cast(dict[str, object], stats['main']).keys()) == ['cd%', 'atk']
    assert list(cast(dict[str, object], stats['sub']).keys()) == ['cd%', 'atk', 'cr%', 'basicAttack%', 'hp%']