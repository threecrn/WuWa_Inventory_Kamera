from __future__ import annotations

import json
from pathlib import Path

from wuwa_inventory_kamera.updater.database import BaseDataUpdater


def _write_json(path: Path, data) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def _prepare_workspace(tmp_path: Path, monkeypatch) -> None:
	monkeypatch.chdir(tmp_path)
	_write_json(tmp_path / 'data' / 'languages.json', {'English': 'en'})


def test_load_json_normalizes_list_based_multitext(tmp_path: Path, monkeypatch) -> None:
	_prepare_workspace(tmp_path, monkeypatch)
	_write_json(
		tmp_path / 'data' / 'en' / 'MultiText.json',
		[
			{'Id': 'RoleInfo_1_Name', 'Content': 'Rover', 'RedirectDbIndex': 0},
			{'Id': 'MonsterInfo_340000160_Name', 'Content': 'Tambourinist', 'RedirectDbIndex': 0},
		],
	)

	updater = BaseDataUpdater(lang='English', source='arikatsu')

	assert updater.loadJson('MultiText.json') == {
		'RoleInfo_1_Name': 'Rover',
		'MonsterInfo_340000160_Name': 'Tambourinist',
	}


def test_update_files_uses_sha_state_for_normalized_multitext(tmp_path: Path, monkeypatch) -> None:
	_prepare_workspace(tmp_path, monkeypatch)

	requested_urls: list[str] = []
	downloads: list[str] = []
	payloads = {
		'https://example.test/multi': [
			{'Id': 'RoleInfo_1_Name', 'Content': 'Rover', 'RedirectDbIndex': 0},
		],
		'https://example.test/items': [
			{
				'Id': 1,
				'Name': 'ItemInfo_1_Name',
				'Icon': '/Game/Aki/UI/UIResources/Common/Image/IconA/T_IconA_AccountExp_UI.T_IconA_AccountExp_UI',
			},
		],
		'https://example.test/weapons': [
			{
				'WeaponName': 'WeaponConf_1_WeaponName',
				'ModelId': 101,
				'QualityId': 4,
				'Icon': '/Game/Aki/UI/UIResources/Common/Image/IconWeapon/T_Weapon_UI.T_Weapon_UI',
			},
		],
	}

	def fake_fetch(self, url: str):
		requested_urls.append(url)
		if 'Textmaps/en/multi_text/MultiText.json' in url:
			return {'sha': 'sha-multi', 'size': 400, 'download_url': 'https://example.test/multi'}
		if 'BinData/item/iteminfo.json' in url:
			return {'sha': 'sha-items', 'size': 200, 'download_url': 'https://example.test/items'}
		if 'BinData/weapon/weaponconf.json' in url:
			return {'sha': 'sha-weapons', 'size': 200, 'download_url': 'https://example.test/weapons'}
		raise AssertionError(f'unexpected url: {url}')

	def fake_urlretrieve(url: str, filename, reporthook=None):
		downloads.append(url)
		Path(filename).write_text(json.dumps(payloads[url], ensure_ascii=False, indent=2), encoding='utf-8')
		if reporthook:
			reporthook(1, 1, 1)
		return filename, None

	monkeypatch.setattr(BaseDataUpdater, 'fetchFileData', fake_fetch)
	monkeypatch.setattr('urllib.request.urlretrieve', fake_urlretrieve)

	updater = BaseDataUpdater(lang='English', source='arikatsu')
	updater.updateFiles()

	assert downloads == [
		'https://example.test/multi',
		'https://example.test/items',
		'https://example.test/weapons',
	]
	assert any('Textmaps/en/multi_text/MultiText.json' in url for url in requested_urls)
	assert updater.loadJson('MultiText.json') == {'RoleInfo_1_Name': 'Rover'}

	updater = BaseDataUpdater(lang='English', source='arikatsu')
	updater.updateFiles()

	assert downloads == [
		'https://example.test/multi',
		'https://example.test/items',
		'https://example.test/weapons',
	]


def test_update_items_overwrites_existing_outputs(tmp_path: Path, monkeypatch) -> None:
	_prepare_workspace(tmp_path, monkeypatch)
	_write_json(
		tmp_path / 'data' / 'en' / 'MultiText.json',
		{
			'ItemInfo_1_Name': 'Shell Credit',
			'WeaponConf_1_WeaponName': 'Training Broadblade',
		},
	)
	_write_json(
		tmp_path / 'data' / 'en' / 'ItemInfo.json',
		[
			{
				'Id': 1,
				'Name': 'ItemInfo_1_Name',
				'Icon': '/Game/Aki/UI/UIResources/Common/Image/IconA/T_IconA_AccountExp_UI.T_IconA_AccountExp_UI',
			},
		],
	)
	_write_json(
		tmp_path / 'data' / 'en' / 'WeaponConf.json',
		[
			{
				'WeaponName': 'WeaponConf_1_WeaponName',
				'ModelId': 101,
				'QualityId': 4,
				'Icon': '/Game/Aki/UI/UIResources/Common/Image/IconWeapon/T_Weapon_UI.T_Weapon_UI',
			},
		],
	)
	_write_json(tmp_path / 'data' / 'en' / 'items.json', {'stale': {'id': 0}})
	_write_json(tmp_path / 'data' / 'en' / 'weapons.json', {'stale': {'id': 0}})

	updater = BaseDataUpdater(lang='English')
	updater.updateItems()

	items = json.loads((tmp_path / 'data' / 'en' / 'items.json').read_text(encoding='utf-8'))
	weapons = json.loads((tmp_path / 'data' / 'en' / 'weapons.json').read_text(encoding='utf-8'))

	assert 'shellcredit' in items
	assert 'stale' not in items
	assert 'trainingbroadblade' in weapons
	assert 'stale' not in weapons