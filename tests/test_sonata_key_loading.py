from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from wuwa_inventory_kamera.updater import assets as assets_module

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def _load_module(name: str, relative_path: str):
    file_path = _REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, file_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_assets_sonata_loader_prefers_generated_catalog(tmp_path) -> None:
    data_dir = tmp_path / 'data'
    _write_json(
        data_dir / 'catalog' / 'sonatas.json',
        {'moonlitclouds': {'id': 12, 'text_key': 'PhantomFetter_12_Name'}},
    )
    _write_json(data_dir / 'en' / 'sonataName.json', {'stalelegacy': 1})

    assert assets_module._load_sonata_keys(data_dir) == {'moonlitclouds'}


def test_assets_sonata_loader_falls_back_to_legacy_file(tmp_path) -> None:
    data_dir = tmp_path / 'data'
    _write_json(data_dir / 'en' / 'sonataName.json', {'Moonlit Clouds': 12})

    assert assets_module._load_sonata_keys(data_dir) == {'moonlitclouds'}


def test_scrape_sonata_icons_loader_prefers_generated_catalog(tmp_path) -> None:
    module = _load_module('test_scrape_sonata_icons_main', 'tools/scrape_sonata_icons/main.py')
    data_dir = tmp_path / 'data'
    _write_json(
        data_dir / 'catalog' / 'sonatas.json',
        {'moonlitclouds': {'id': 12, 'text_key': 'PhantomFetter_12_Name'}},
    )
    _write_json(data_dir / 'en' / 'sonataName.json', {'stalelegacy': 1})

    assert module.load_sonata_keys(data_dir) == {'moonlitclouds'}


def test_update_sonata_templates_loader_prefers_generated_catalog(tmp_path) -> None:
    module = _load_module('test_update_sonata_templates_main', 'tools/update_sonata_templates/main.py')
    data_dir = tmp_path / 'data'
    _write_json(
        data_dir / 'catalog' / 'sonatas.json',
        {'moonlitclouds': {'id': 12, 'text_key': 'PhantomFetter_12_Name'}},
    )
    _write_json(data_dir / 'en' / 'sonataName.json', {'stalelegacy': 1})

    assert module.load_sonata_keys(data_dir) == {'moonlitclouds': 12}

def test_update_sonata_templates_loader_falls_back_to_legacy_file(tmp_path) -> None:
    module = _load_module('test_update_sonata_templates_main_legacy', 'tools/update_sonata_templates/main.py')
    data_dir = tmp_path / 'data'
    _write_json(data_dir / 'en' / 'sonataName.json', {'Moonlit Clouds': 12})

    assert module.load_sonata_keys(data_dir) == {'moonlitclouds': 12}
