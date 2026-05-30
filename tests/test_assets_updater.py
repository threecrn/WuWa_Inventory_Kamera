from __future__ import annotations

import json
from pathlib import Path

from wuwa_inventory_kamera.updater import assets as assets_module


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


class _RecorderUpdater(assets_module.BaseAssetsUpdater):
    def __init__(self) -> None:
        self.progress: list[tuple[str, float]] = []
        self.finished = False

    def _onProgress(self, file_name: str, percent: float) -> None:
        self.progress.append((file_name, percent))

    def _onFinished(self) -> None:
        self.finished = True


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


def test_load_game_asset_manifest_filters_invalid_paths(tmp_path) -> None:
    data_dir = tmp_path / 'data'
    _write_json(
        data_dir / 'catalog' / 'items.json',
        {
            'shell_credit': {'id': 1, 'image': 'IconA/T_IconA_ShellCredit_UI.png'},
            'escaped': {'id': 2, 'image': '../outside.png'},
            'wrong_type': {'id': 3, 'image': 123},
        },
    )
    _write_json(
        data_dir / 'catalog' / 'weapons.json',
        {
            'standard_sword': {'id': 11, 'image': 'IconWup\\T_IconWup_StandardSword_UI.png'},
            'duplicate': {'id': 12, 'image': 'IconA/T_IconA_ShellCredit_UI.png'},
            'absolute': {'id': 13, 'image': '/IconA/T_Absolute.png'},
            'non_png': {'id': 14, 'image': 'IconA/T_IconA_Thumb.jpg'},
        },
    )

    assert assets_module._load_game_asset_manifest(data_dir) == (
        'IconA/T_IconA_ShellCredit_UI.png',
        'IconWup/T_IconWup_StandardSword_UI.png',
    )


def test_base_assets_updater_uses_explicit_game_and_sonata_families() -> None:
    assert [family.name for family in assets_module.BaseAssetsUpdater()._iter_asset_families()] == [
        'game-icons',
        'sonata-icons',
    ]


def test_base_assets_updater_downloads_game_and_sonata_assets(tmp_path, monkeypatch) -> None:
    _write_json(
        tmp_path / 'data' / 'catalog' / 'items.json',
        {
            'shell_credit': {'id': 1, 'image': 'IconA/T_IconA_ShellCredit_UI.png'},
        },
    )
    _write_json(tmp_path / 'data' / 'catalog' / 'weapons.json', {})
    _write_json(
        tmp_path / 'data' / 'catalog' / 'sonatas.json',
        {'moonlitclouds': {'id': 12, 'text_key': 'PhantomFetter_12_Name'}},
    )

    monkeypatch.setattr(assets_module, 'basePATH', tmp_path)
    monkeypatch.setattr(assets_module.time, 'sleep', lambda _: None)
    monkeypatch.setattr(
        assets_module,
        '_build_icon_mapping',
        lambda sonata_keys: {'moonlitclouds': 'https://example.test/Icon_moonlitclouds.png'},
    )

    payloads = {
        assets_module._build_game_asset_download_url('IconA/T_IconA_ShellCredit_UI.png'): b'game-asset',
        'https://example.test/Icon_moonlitclouds.png': b'sonata-asset',
    }

    def fake_urlopen(request, timeout=60):
        url = request.full_url if hasattr(request, 'full_url') else str(request)
        return _FakeResponse(payloads[url])

    monkeypatch.setattr(assets_module.urllib.request, 'urlopen', fake_urlopen)

    updater = _RecorderUpdater()
    updater.run()

    assert (tmp_path / 'assets' / 'IconA' / 'T_IconA_ShellCredit_UI.png').read_bytes() == b'game-asset'
    assert (tmp_path / 'assets' / 'IconS' / 'moonlitclouds.png').read_bytes() == b'sonata-asset'
    assert updater.finished is True
    assert [label for label, _percent in updater.progress] == [
        'IconA/T_IconA_ShellCredit_UI.png',
        'IconS/moonlitclouds.png',
    ]
