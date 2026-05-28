from __future__ import annotations

import os
from typing import Any, cast

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('PySide6')
pytest.importorskip('qfluentwidgets')

from PySide6.QtWidgets import QApplication

from wuwa_inventory_kamera.ui.inventory import InventoryInterface
from wuwa_inventory_kamera.ui.inventory_models import InventoryDocument, InventoryRow, InventorySection


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