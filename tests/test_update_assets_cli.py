from __future__ import annotations

from wuwa_inventory_kamera.cli import update_assets as update_assets_module
from wuwa_inventory_kamera.updater.assets import AssetFamilyStatus


def test_update_assets_cli_status_prints_family_summary(monkeypatch, capsys) -> None:
    class _FakeUpdater:
        def __init__(self, *, force: bool = False) -> None:
            assert force is False

        def collect_status(self):
            return (
                AssetFamilyStatus(family='game-icons', total=3, existing=1, missing=2),
                AssetFamilyStatus(family='sonata-icons', total=2, existing=2, missing=0),
            )

    monkeypatch.setattr(update_assets_module, 'BaseAssetsUpdater', _FakeUpdater)

    assert update_assets_module.main(['status']) == 0

    stdout = capsys.readouterr().out
    assert 'game-icons: 1/3 present, 2 missing' in stdout
    assert 'sonata-icons: 2/2 present, 0 missing' in stdout


def test_update_assets_cli_update_passes_force(monkeypatch) -> None:
    force_values: list[bool] = []

    class _FakeConsoleUpdater:
        def __init__(self, *, force: bool = False) -> None:
            force_values.append(force)

        def run(self) -> None:
            return None

    monkeypatch.setattr(update_assets_module, '_ConsoleAssetsUpdater', _FakeConsoleUpdater)

    assert update_assets_module.main(['update', '--force']) == 0
    assert force_values == [True]