from __future__ import annotations

import base64
import os
import threading
import time
from typing import Any, cast

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('PySide6')
pytest.importorskip('qfluentwidgets')

from PySide6.QtWidgets import QApplication

from wuwa_inventory_kamera.ui import inventory as inventory_module
from wuwa_inventory_kamera.ui.inventory import InventoryInterface, ResultCard
from wuwa_inventory_kamera.ui.inventory_models import InventoryDocument, InventoryRow, InventorySection


_PNG_BYTES = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAACXBIWXMAAA9hAAAPYQGoP6dpAAAAC0lEQVQImWP4DwQACfsD/eNV8pwAAAAASUVORK5CYII='
)


@pytest.fixture(scope='module')
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return cast(QApplication, app)


def _build_document() -> InventoryDocument:
    return InventoryDocument(
        kind='test',
        title='Inventory',
        sections=(
            InventorySection(
                title='Echoes',
                rows=(
                    InventoryRow(title='Bell', details_lines=('Alpha details',)),
                    InventoryRow(title='Feilian', details_lines=('Beta details',)),
                ),
            ),
        ),
    )


def _details_texts(interface: InventoryInterface) -> list[str]:
    assert interface._detailsLayout is not None
    texts: list[str] = []
    for index in range(1, interface._detailsLayout.count()):
        item = interface._detailsLayout.itemAt(index)
        widget = item.widget() if item is not None else None
        if widget is not None:
            text = getattr(widget, 'text', None)
            if callable(text):
                texts.append(cast(str, text()))
    return texts


def _wait_until(qapp: QApplication, predicate, *, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    qapp.processEvents()
    assert predicate()


def test_row_selection_reuses_existing_result_cards(qapp: QApplication) -> None:
    interface = InventoryInterface()
    set_document = cast(Any, getattr(interface, '_InventoryInterface__setDocument'))
    set_document(_build_document())
    interface.resize(1200, 800)
    interface.show()
    qapp.processEvents()

    original_cards = list(interface._resultCards)
    original_geometries = [card.geometry() for card in original_cards]
    assert len(original_cards) == 2
    assert interface._currentRowIndex == 0
    assert original_cards[0]._selected is True
    assert original_cards[1]._selected is False
    assert _details_texts(interface) == ['Alpha details']

    on_row_selected = cast(Any, getattr(interface, '_InventoryInterface__onRowSelected'))
    on_row_selected(1)
    qapp.processEvents()

    assert [id(card) for card in interface._resultCards] == [id(card) for card in original_cards]
    assert interface._currentRowIndex == 1
    assert [card.geometry() for card in interface._resultCards] == original_geometries
    assert interface._resultCards[0]._selected is False
    assert interface._resultCards[1]._selected is True
    assert _details_texts(interface) == ['Beta details']

    interface.hide()
    interface.deleteLater()
    qapp.processEvents()


def test_result_card_lazy_downloads_missing_thumbnail(qapp: QApplication, tmp_path, monkeypatch) -> None:
    image_path = 'IconA/T_IconA_ShellCredit_UI.png'
    target_path = tmp_path / 'assets' / 'IconA' / 'T_IconA_ShellCredit_UI.png'
    requested_paths: list[str] = []

    def fake_ensure_game_asset_cached(requested_image_path: str):
        requested_paths.append(requested_image_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(_PNG_BYTES)
        return target_path

    monkeypatch.setattr(inventory_module, 'basePATH', tmp_path)
    monkeypatch.setattr(inventory_module._assets, 'ensure_game_asset_cached', fake_ensure_game_asset_cached)
    monkeypatch.setattr(inventory_module, '_lazy_game_icon_downloader', None)

    card = ResultCard(InventoryRow(title='Shell Credit', image_path=image_path))
    card.show()
    qapp.processEvents()

    _wait_until(qapp, lambda: not card.imageLabel.isHidden())

    assert requested_paths == [image_path]
    assert target_path.is_file()
    assert card.imageLabel.isHidden() is False
    pixmap = card.imageLabel.pixmap()
    assert pixmap is not None
    assert pixmap.isNull() is False

    card.hide()
    card.deleteLater()
    qapp.processEvents()


def test_lazy_downloader_deduplicates_in_flight_requests(
    qapp: QApplication,
    tmp_path,
    monkeypatch,
) -> None:
    image_path = 'IconA/T_IconA_ShellCredit_UI.png'
    target_path = tmp_path / 'assets' / 'IconA' / 'T_IconA_ShellCredit_UI.png'
    release = threading.Event()
    started = threading.Event()
    requested_paths: list[str] = []

    def fake_ensure_game_asset_cached(requested_image_path: str):
        requested_paths.append(requested_image_path)
        started.set()
        release.wait(timeout=1.0)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(_PNG_BYTES)
        return target_path

    monkeypatch.setattr(inventory_module._assets, 'ensure_game_asset_cached', fake_ensure_game_asset_cached)

    downloader = inventory_module._LazyGameIconDownloader()
    downloader.request(image_path)
    assert started.wait(timeout=1.0)

    downloader.request(image_path)
    release.set()

    _wait_until(qapp, lambda: image_path not in downloader._in_flight)

    assert requested_paths == [image_path]


def test_lazy_downloader_applies_short_failure_backoff(
    qapp: QApplication,
    monkeypatch,
) -> None:
    image_path = 'IconA/T_IconA_ShellCredit_UI.png'
    attempts: list[str] = []
    clock = {'value': 100.0}

    def fake_monotonic() -> float:
        return clock['value']

    def fake_ensure_game_asset_cached(requested_image_path: str):
        attempts.append(requested_image_path)
        raise RuntimeError('boom')

    monkeypatch.setattr(inventory_module.time, 'monotonic', fake_monotonic)
    monkeypatch.setattr(inventory_module._assets, 'ensure_game_asset_cached', fake_ensure_game_asset_cached)

    downloader = inventory_module._LazyGameIconDownloader()
    downloader.request(image_path)
    _wait_until(qapp, lambda: image_path not in downloader._in_flight)

    downloader.request(image_path)
    time.sleep(0.05)
    qapp.processEvents()

    assert attempts == [image_path]

    clock['value'] += inventory_module._LAZY_DOWNLOAD_FAILURE_BACKOFF_SECONDS + 0.1
    downloader.request(image_path)
    _wait_until(qapp, lambda: len(attempts) == 2 and image_path not in downloader._in_flight)

    assert attempts == [image_path, image_path]