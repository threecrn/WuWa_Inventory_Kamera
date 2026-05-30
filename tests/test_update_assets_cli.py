from __future__ import annotations

from pathlib import Path

from wuwa_inventory_kamera.cli import update_assets as update_assets_module
from wuwa_inventory_kamera.updater.assets import AssetAuditResult, AssetFamilyStatus


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


def test_update_assets_cli_audit_reports_missing_paths(monkeypatch, capsys) -> None:
    class _FakeUpdater:
        def audit_game_asset_source_manifest(self, manifest_path: Path):
            assert manifest_path == Path('custom-ls-files-t')
            return AssetAuditResult(
                manifest_path=manifest_path,
                checked=2,
                present=1,
                missing=('UI/UIResources/Common/Image/IconA/T_IconA_Missing_UI.png',),
            )

    monkeypatch.setattr(update_assets_module, 'BaseAssetsUpdater', lambda: _FakeUpdater())

    assert update_assets_module.main(['audit', '--source-manifest', 'custom-ls-files-t', '--list-missing']) == 1

    stdout = capsys.readouterr().out
    assert 'Checked 2 catalog path(s) against custom-ls-files-t' in stdout
    assert 'Present: 1; missing: 1' in stdout
    assert 'UI/UIResources/Common/Image/IconA/T_IconA_Missing_UI.png' in stdout