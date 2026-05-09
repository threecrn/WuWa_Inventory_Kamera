from __future__ import annotations

from types import SimpleNamespace

from wuwa_inventory_kamera.game.screen import GameWindow


def test_game_window_size_prefers_client_area(monkeypatch) -> None:
    monkeypatch.setattr(GameWindow, 'client_size', property(lambda self: (1920, 1080)))
    monkeypatch.setattr(GameWindow, 'dpi_scale', property(lambda self: 1.0))

    gw = object.__new__(GameWindow)
    gw._window = SimpleNamespace(width=1936, height=1119)
    gw.windowed = False

    assert gw.size == (1920, 1080)


def test_game_window_size_falls_back_to_outer_rect_when_client_missing(monkeypatch) -> None:
    monkeypatch.setattr(GameWindow, 'client_size', property(lambda self: (0, 0)))
    monkeypatch.setattr(GameWindow, 'dpi_scale', property(lambda self: 1.0))

    gw = object.__new__(GameWindow)
    gw._window = SimpleNamespace(width=1936, height=1119)
    gw.windowed = False

    assert gw.size == (1936, 1119)