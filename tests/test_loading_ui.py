from __future__ import annotations

import os
from typing import cast

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('PySide6')
pytest.importorskip('qfluentwidgets')

from PySide6.QtWidgets import QApplication

import wuwa_inventory_kamera.ui.loading as loading_module


@pytest.fixture(scope='module')
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return cast(QApplication, app)


def test_loading_screen_skips_updates_when_disabled(qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    finished: list[bool] = []
    data_thread_started: list[bool] = []
    original_get = loading_module.cfg.get

    def fake_get(item):
        if item is loading_module.cfg.checkUpdateAtStartUp:
            return False
        return original_get(item)

    monkeypatch.setattr(loading_module.cfg, 'get', fake_get)
    monkeypatch.setattr(loading_module.LoadingScreen, 'on_updateFinished', lambda self: finished.append(True))
    monkeypatch.setattr(
        loading_module.DataUpdaterThread,
        'start',
        lambda self: data_thread_started.append(True),
    )

    screen = loading_module.LoadingScreen()
    qapp.processEvents()

    assert finished == [True]
    assert data_thread_started == []
    assert screen.file_label.text() == 'Startup updates disabled'

    screen.hide()
    screen.deleteLater()
    qapp.processEvents()