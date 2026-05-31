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
from wuwa_inventory_kamera.ui.inventory import (
    CharacterTileCard,
    EchoTileCard,
    InventoryInterface,
    ResultCard,
    TileCard,
    WeaponTileCard,
)

from wuwa_inventory_kamera.ui.inventory_models import (
    CharacterDisplayData,
    EchoDisplayData,
    InventoryDocument,
    InventoryRow,
    InventorySection,
    WeaponDisplayData,
)


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


def _build_tile_document(*, count: int = 7) -> InventoryDocument:
    rows = tuple(
        InventoryRow(
            title=f'Extremely Long Inventory Item Name {index}',
            body_lines=(str(index),),
            display_kind='tile',
        )
        for index in range(1, count + 1)
    )
    return InventoryDocument(
        kind='test',
        title='Inventory',
        sections=(InventorySection(title='Resources', rows=rows),),
    )


def _build_weapon_tile_document() -> InventoryDocument:
    rows = tuple(
        InventoryRow(
            title=f'Extremely Long Weapon Name {index}',
            subtitle=f'Weapon ID: 2101007{index}',
            body_lines=('Lv. 90 | Max 90 | Rank 1 | Rarity 5', f'Equipped: Shorekeeper {index}'),
            display_kind='weapon_tile',
            weapon_display=WeaponDisplayData(
                level=90,
                max_level=90,
                rank=1,
                rarity=5,
                equipped=f'Shorekeeper {index}',
            ),
        )
        for index in range(1, 8)
    )
    return InventoryDocument(
        kind='test',
        title='Weapons',
        sections=(InventorySection(title='Weapons', rows=rows),),
    )


def _build_character_tile_document() -> InventoryDocument:
    rows = tuple(
        InventoryRow(
            title=f'Extremely Long Character Name {index}',
            subtitle=f'Character ID: 110{index}',
            display_kind='character_tile',
            character_display=CharacterDisplayData(
                level=90,
                max_level=90,
                chain=2,
                rarity=4,
            ),
        )
        for index in range(1, 8)
    )
    return InventoryDocument(
        kind='test',
        title='Characters',
        sections=(InventorySection(title='Characters', rows=rows),),
    )


def _build_echo_tile_document(*, image_path: str, sonata_icon_path: str) -> InventoryDocument:
    rows = tuple(
        InventoryRow(
            title=f'Extremely Long Echo Name {index}',
            subtitle=f'Echo ID: 3100000{index}',
            image_path=image_path,
            display_kind='echo_tile',
            echo_display=EchoDisplayData(
                level=25,
                cost=4,
                rarity=5,
                main_stat='Healing Bonus 26.4%',
                equipped=f'Shorekeeper {index}' if index % 2 else '',
                sonata_name='Moonlit Clouds',
                sonata_icon_path=sonata_icon_path,
            ),
        )
        for index in range(1, 8)
    )
    return InventoryDocument(
        kind='test',
        title='Echoes',
        sections=(InventorySection(title='Echoes', rows=rows),),
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


def _layout_texts(layout) -> list[str]:
    texts: list[str] = []
    for index in range(layout.count()):
        item = layout.itemAt(index)
        widget = item.widget() if item is not None else None
        if widget is None:
            continue
        text = getattr(widget, 'text', None)
        if callable(text):
            texts.append(cast(str, text()))
    return texts


def _details_card_label_texts(interface: InventoryInterface) -> list[str]:
    assert interface._detailsCard is not None
    texts: list[str] = []
    widgets = list(interface._detailsCard.findChildren(inventory_module.BodyLabel))
    widgets.extend(interface._detailsCard.findChildren(inventory_module.StrongBodyLabel))
    for widget in widgets:
        text = widget.text().strip()
        if text:
            texts.append(text)
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


class _FakeSignal:
    def connect(self, _slot) -> None:
        return None


class _FakeDownloader:
    def __init__(self) -> None:
        self.downloadFinished = _FakeSignal()
        self.requested_paths: list[str] = []

    def request(self, image_path: str) -> None:
        self.requested_paths.append(image_path)


def test_row_selection_reuses_existing_result_cards(qapp: QApplication) -> None:
    interface = InventoryInterface()
    set_document = cast(Any, getattr(interface, '_InventoryInterface__setDocument'))
    set_document(_build_document())
    interface.resize(1400, 800)
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


def test_details_pane_renders_to_the_right_of_results(qapp: QApplication) -> None:
    interface = InventoryInterface()
    set_document = cast(Any, getattr(interface, '_InventoryInterface__setDocument'))
    set_document(_build_document())
    interface.resize(1400, 800)
    interface.show()
    qapp.processEvents()

    assert interface._detailsCard is not None

    first_card = interface._resultCards[0]
    first_card_right = first_card.mapTo(interface, first_card.rect().topRight()).x()
    details_left = interface._detailsCard.mapTo(interface, interface._detailsCard.rect().topLeft()).x()

    assert details_left > first_card_right

    interface.hide()
    interface.deleteLater()
    qapp.processEvents()


def test_inventory_view_omits_document_title_header(qapp: QApplication) -> None:
    interface = InventoryInterface()
    set_document = cast(Any, getattr(interface, '_InventoryInterface__setDocument'))
    set_document(
        InventoryDocument(
            kind='test',
            title='inventory_wuwainventorykamera.json',
            message_lines=('Loaded inventory export.',),
        )
    )
    qapp.processEvents()

    assert _layout_texts(interface.contentLayout) == ['Loaded inventory export.']

    interface.hide()
    interface.deleteLater()
    qapp.processEvents()


def test_details_pane_uses_compact_content_height(qapp: QApplication) -> None:
    interface = InventoryInterface()
    set_document = cast(Any, getattr(interface, '_InventoryInterface__setDocument'))
    set_document(_build_document())
    interface.resize(1400, 800)
    interface.show()
    qapp.processEvents()

    assert interface._detailsCard is not None
    assert interface._detailsCard.height() < interface.height() // 2

    interface.hide()
    interface.deleteLater()
    qapp.processEvents()


def test_details_pane_expands_for_wrapped_details_after_selection(qapp: QApplication) -> None:
    interface = InventoryInterface()
    set_document = cast(Any, getattr(interface, '_InventoryInterface__setDocument'))
    set_document(
        InventoryDocument(
            kind='test',
            title='Inventory',
            sections=(
                InventorySection(
                    title='Echoes',
                    rows=(
                        InventoryRow(title='Bell', details_lines=('short line',)),
                        InventoryRow(
                            title='Feilian',
                            details_lines=(
                                'Very long detail line that should wrap across the fixed details pane width and remain readable without collapsing into a tiny clipped block.',
                                'Second very long detail line that should also wrap and still keep the details panel tall enough to show the text.',
                                'Third line for extra height.',
                            ),
                        ),
                    ),
                ),
            ),
        )
    )
    interface.resize(1400, 800)
    interface.show()
    qapp.processEvents()

    assert interface._detailsCard is not None
    initial_height = interface._detailsCard.height()
    on_row_selected = cast(Any, getattr(interface, '_InventoryInterface__onRowSelected'))
    on_row_selected(1)
    qapp.processEvents()

    assert interface._detailsCard.height() >= initial_height + 80
    assert interface._detailsLayout is not None
    for index in range(1, interface._detailsLayout.count()):
        item = interface._detailsLayout.itemAt(index)
        widget = item.widget() if item is not None else None
        assert widget is not None
        assert widget.height() > 0

    interface.hide()
    interface.deleteLater()
    qapp.processEvents()


def test_echo_details_pane_renders_main_and_substat_tables(qapp: QApplication) -> None:
    interface = InventoryInterface()
    set_document = cast(Any, getattr(interface, '_InventoryInterface__setDocument'))
    set_document(
        InventoryDocument(
            kind='test',
            title='Echoes',
            sections=(
                InventorySection(
                    title='Echoes',
                    rows=(
                        InventoryRow(
                            title='Bell Borne Geochelone',
                            subtitle='Echo ID: 310000010',
                            display_kind='echo_tile',
                            details_lines=(
                                'Echo ID: 310000010',
                                'Main Stat: Crit. Rate 22.0',
                                'Main Stat: ATK 150',
                                'Substat: ATK% 7.9',
                                'Substat: Crit. DMG 12.6',
                            ),
                            echo_display=EchoDisplayData(
                                level=25,
                                cost=4,
                                rarity=5,
                                equipped='Cartethyia',
                                sonata_name='Moonlit Clouds',
                            ),
                        ),
                    ),
                ),
            ),
        )
    )
    interface.resize(1400, 800)
    interface.show()
    qapp.processEvents()

    texts = _details_card_label_texts(interface)
    assert 'Main Stat' in texts
    assert 'Substats' in texts
    assert 'Crit. Rate' in texts
    assert 'ATK' in texts
    assert 'ATK%' in texts
    assert 'Crit. DMG' in texts
    assert '22.0' in texts
    assert '150' in texts
    assert '7.9%' in texts
    assert '12.6' in texts

    interface.hide()
    interface.deleteLater()
    qapp.processEvents()


def test_six_column_tile_cards_share_the_same_width() -> None:
    assert TileCard.TILE_WIDTH == WeaponTileCard.TILE_WIDTH == CharacterTileCard.TILE_WIDTH == 144


def test_tile_section_uses_tile_cards_with_six_column_wrap(qapp: QApplication) -> None:
    interface = InventoryInterface()
    set_document = cast(Any, getattr(interface, '_InventoryInterface__setDocument'))
    set_document(_build_tile_document())
    interface.resize(1400, 800)
    interface.show()
    qapp.processEvents()

    assert len(interface._resultCards) == 7
    assert all(isinstance(card, TileCard) for card in interface._resultCards)

    first_card = cast(TileCard, interface._resultCards[0])
    sixth_card = cast(TileCard, interface._resultCards[5])
    seventh_card = cast(TileCard, interface._resultCards[6])

    assert first_card.width() == TileCard.TILE_WIDTH
    assert first_card.height() == TileCard.TILE_HEIGHT
    assert first_card.countLabel.text() == '1'
    assert first_card.nameLabel.text() != first_card.row.title
    assert first_card.nameLabel.text().endswith('…')
    assert sixth_card.y() == first_card.y()
    assert seventh_card.y() > first_card.y()

    interface.hide()
    interface.deleteLater()
    qapp.processEvents()


def test_large_sections_scroll_inside_the_results_grid(qapp: QApplication) -> None:
    interface = InventoryInterface()
    set_document = cast(Any, getattr(interface, '_InventoryInterface__setDocument'))
    set_document(_build_tile_document(count=36))
    interface.resize(1400, 700)
    interface.show()
    qapp.processEvents()

    assert interface._sectionScrollArea is not None
    assert interface.verticalScrollBar().maximum() == 0
    assert interface._sectionScrollArea.verticalScrollBar().maximum() > 0

    interface.hide()
    interface.deleteLater()
    qapp.processEvents()


def test_weapon_section_uses_weapon_tile_cards_with_six_column_wrap(qapp: QApplication) -> None:
    interface = InventoryInterface()
    set_document = cast(Any, getattr(interface, '_InventoryInterface__setDocument'))
    set_document(_build_weapon_tile_document())
    interface.resize(1400, 800)
    interface.show()
    qapp.processEvents()

    assert len(interface._resultCards) == 7
    assert all(isinstance(card, WeaponTileCard) for card in interface._resultCards)

    first_card = cast(WeaponTileCard, interface._resultCards[0])
    sixth_card = cast(WeaponTileCard, interface._resultCards[5])
    seventh_card = cast(WeaponTileCard, interface._resultCards[6])

    assert first_card.width() == WeaponTileCard.TILE_WIDTH
    assert first_card.height() == WeaponTileCard.TILE_HEIGHT
    assert first_card.nameLabel.text() != first_card.row.title
    assert first_card.nameLabel.text().endswith('…')
    assert first_card.summaryLabel.text() == '90/90 (1)'
    assert first_card.equippedLabel.text() == 'By: Shorekeeper 1'
    assert '#fffab0' in first_card.rarityLine.styleSheet()
    assert sixth_card.y() == first_card.y()
    assert seventh_card.y() > first_card.y()

    interface.hide()
    interface.deleteLater()
    qapp.processEvents()


def test_character_section_uses_character_tile_cards_with_six_column_wrap(qapp: QApplication) -> None:
    interface = InventoryInterface()
    set_document = cast(Any, getattr(interface, '_InventoryInterface__setDocument'))
    set_document(_build_character_tile_document())
    interface.resize(1400, 800)
    interface.show()
    qapp.processEvents()

    assert len(interface._resultCards) == 7
    assert all(isinstance(card, CharacterTileCard) for card in interface._resultCards)

    first_card = cast(CharacterTileCard, interface._resultCards[0])
    sixth_card = cast(CharacterTileCard, interface._resultCards[5])
    seventh_card = cast(CharacterTileCard, interface._resultCards[6])

    assert first_card.width() == CharacterTileCard.TILE_WIDTH
    assert first_card.height() == CharacterTileCard.TILE_HEIGHT
    assert first_card.nameLabel.text() != first_card.row.title
    assert first_card.nameLabel.text().endswith('…')
    assert first_card.summaryLabel.text() == '90/90 (2)'
    assert '#e8a1ff' in first_card.rarityLine.styleSheet()
    assert sixth_card.y() == first_card.y()
    assert seventh_card.y() > first_card.y()

    interface.hide()
    interface.deleteLater()
    qapp.processEvents()


def test_echo_section_uses_echo_tile_cards_with_six_column_wrap(
    qapp: QApplication,
    tmp_path,
    monkeypatch,
) -> None:
    image_path = 'IconMonsterHead/T_IconMonsterHead_015_UI.png'
    sonata_icon_path = 'IconS/moonlitclouds.png'
    echo_icon_file = tmp_path / 'assets' / 'IconMonsterHead' / 'T_IconMonsterHead_015_UI.png'
    sonata_icon_file = tmp_path / 'assets' / 'IconS' / 'moonlitclouds.png'
    echo_icon_file.parent.mkdir(parents=True, exist_ok=True)
    sonata_icon_file.parent.mkdir(parents=True, exist_ok=True)
    echo_icon_file.write_bytes(_PNG_BYTES)
    sonata_icon_file.write_bytes(_PNG_BYTES)

    monkeypatch.setattr(inventory_module, 'basePATH', tmp_path)

    interface = InventoryInterface()
    set_document = cast(Any, getattr(interface, '_InventoryInterface__setDocument'))
    set_document(_build_echo_tile_document(image_path=image_path, sonata_icon_path=sonata_icon_path))
    interface.resize(1400, 800)
    interface.show()
    qapp.processEvents()

    assert len(interface._resultCards) == 7
    assert all(isinstance(card, EchoTileCard) for card in interface._resultCards)

    first_card = cast(EchoTileCard, interface._resultCards[0])
    second_card = cast(EchoTileCard, interface._resultCards[1])
    sixth_card = cast(EchoTileCard, interface._resultCards[5])
    seventh_card = cast(EchoTileCard, interface._resultCards[6])

    assert first_card.width() == EchoTileCard.TILE_WIDTH
    assert first_card.height() == EchoTileCard.TILE_HEIGHT
    assert first_card.nameLabel.text() != first_card.row.title
    assert first_card.nameLabel.text().endswith('…')
    assert first_card.costLabel.text() == '(4)'
    assert first_card.levelLabel.text() == '+25'
    assert first_card.mainStatLabel.text() == 'Healing Bonus 26.4%'
    assert first_card.equippedLabel.text() == 'By: Shorekeeper 1'
    assert second_card.equippedLabel.text() == ' '
    assert '#fffab0' in first_card.rarityLine.styleSheet()
    assert first_card.sonataIconLabel.isHidden() is False
    pixmap = first_card.sonataIconLabel.pixmap()
    assert pixmap is not None
    assert pixmap.isNull() is False
    assert sixth_card.y() == first_card.y()
    assert seventh_card.y() > first_card.y()

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


def test_setting_document_prefetches_missing_images_from_other_sections(
    qapp: QApplication,
    tmp_path,
    monkeypatch,
) -> None:
    visible_path = 'IconA/T_IconA_ShellCredit_UI.png'
    hidden_section_path = 'IconWeapon/T_IconWeapon_Test_UI.png'
    cached_path = tmp_path / 'assets' / 'IconA' / 'T_IconA_ShellCredit_UI.png'
    cached_path.parent.mkdir(parents=True, exist_ok=True)
    cached_path.write_bytes(_PNG_BYTES)

    fake_downloader = _FakeDownloader()
    monkeypatch.setattr(inventory_module, 'basePATH', tmp_path)
    monkeypatch.setattr(inventory_module, '_lazy_game_icon_downloader', fake_downloader)

    interface = InventoryInterface()
    set_document = cast(Any, getattr(interface, '_InventoryInterface__setDocument'))
    set_document(
        InventoryDocument(
            kind='test',
            title='Inventory',
            sections=(
                InventorySection(
                    title='Visible',
                    rows=(
                        InventoryRow(title='Shell Credit', image_path=visible_path),
                    ),
                ),
                InventorySection(
                    title='Hidden',
                    rows=(
                        InventoryRow(title='Training Sword', image_path=hidden_section_path),
                    ),
                ),
            ),
        )
    )
    qapp.processEvents()

    assert fake_downloader.requested_paths == [hidden_section_path]

    interface.hide()
    interface.deleteLater()
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