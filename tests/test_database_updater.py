from __future__ import annotations

import json
from pathlib import Path

from wuwa_inventory_kamera.updater.database import BaseDataUpdater


def _write_json(path: Path, data) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def _raw_path(base: Path, lang: str, filename: str) -> Path:
	return base / 'data' / 'raw' / lang / filename


def _prepare_workspace(tmp_path: Path, monkeypatch) -> None:
	monkeypatch.chdir(tmp_path)
	_write_json(tmp_path / 'data' / 'languages.json', {'English': 'en'})


def test_load_json_normalizes_list_based_multitext(tmp_path: Path, monkeypatch) -> None:
	_prepare_workspace(tmp_path, monkeypatch)
	_write_json(
		_raw_path(tmp_path, 'en', 'MultiText.json'),
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
		'https://example.test/roles': [
			{
				'Id': 1,
				'Name': 'RoleInfo_1_Name',
				'RoleHeadIcon': '/Game/Aki/UI/UIResources/Common/Image/IconRoleHead80/T_IconRoleHead80_14_UI.T_IconRoleHead80_14_UI',
			},
		],
		'https://example.test/monsters': [
			{
				'Id': 310000010,
				'Name': 'MonsterInfo_310000010_Name',
				'Icon': '/Game/Aki/UI/UIResources/Common/Image/IconMonsterHead/T_IconMonsterHead_015_UI.T_IconMonsterHead_015_UI',
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
		if 'BinData/role/roleinfo.json' in url:
			return {'sha': 'sha-roles', 'size': 200, 'download_url': 'https://example.test/roles'}
		if 'BinData/monster_Info/monsterinfo.json' in url:
			return {'sha': 'sha-monsters', 'size': 200, 'download_url': 'https://example.test/monsters'}
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
		'https://example.test/roles',
		'https://example.test/monsters',
	]
	assert any('Textmaps/en/multi_text/MultiText.json' in url for url in requested_urls)
	assert any('BinData/role/roleinfo.json' in url for url in requested_urls)
	assert any('BinData/monster_Info/monsterinfo.json' in url for url in requested_urls)
	assert _raw_path(tmp_path, 'en', 'MultiText.json').is_file()
	assert _raw_path(tmp_path, 'en', 'ItemInfo.json').is_file()
	assert _raw_path(tmp_path, 'en', 'WeaponConf.json').is_file()
	assert _raw_path(tmp_path, 'en', 'RoleInfo.json').is_file()
	assert _raw_path(tmp_path, 'en', 'MonsterInfo.json').is_file()
	assert _raw_path(tmp_path, 'en', '.updater_state.json').is_file()
	assert updater.loadJson('MultiText.json') == {'RoleInfo_1_Name': 'Rover'}

	updater = BaseDataUpdater(lang='English', source='arikatsu')
	updater.updateFiles()

	assert downloads == [
		'https://example.test/multi',
		'https://example.test/items',
		'https://example.test/weapons',
		'https://example.test/roles',
		'https://example.test/monsters',
	]


def test_init_migrates_legacy_raw_files_into_data_raw(tmp_path: Path, monkeypatch) -> None:
	_prepare_workspace(tmp_path, monkeypatch)
	_write_json(
		tmp_path / 'data' / 'en' / 'MultiText.json',
		{'RoleInfo_1_Name': 'Rover'},
	)
	_write_json(
		tmp_path / 'data' / 'en' / 'ItemInfo.json',
		[{'Id': 1, 'Name': 'ItemInfo_1_Name', 'Icon': 'Icon'}],
	)
	_write_json(
		tmp_path / 'data' / 'en' / 'WeaponConf.json',
		[{'WeaponName': 'WeaponConf_1_WeaponName', 'ModelId': 101, 'QualityId': 4, 'Icon': 'Icon'}],
	)
	_write_json(
		tmp_path / 'data' / 'en' / 'RoleInfo.json',
		[{'Id': 1, 'Name': 'RoleInfo_1_Name', 'RoleHeadIcon': 'Icon'}],
	)
	_write_json(
		tmp_path / 'data' / 'en' / 'MonsterInfo.json',
		[{'Id': 310000010, 'Name': 'MonsterInfo_310000010_Name', 'Icon': 'Icon'}],
	)
	_write_json(tmp_path / 'data' / 'en' / '.updater_state.json', {'source': 'dimbreath', 'files': {}})

	updater = BaseDataUpdater(lang='English')

	assert updater.loadJson('MultiText.json') == {'RoleInfo_1_Name': 'Rover'}
	assert _raw_path(tmp_path, 'en', 'MultiText.json').is_file()
	assert _raw_path(tmp_path, 'en', 'ItemInfo.json').is_file()
	assert _raw_path(tmp_path, 'en', 'WeaponConf.json').is_file()
	assert _raw_path(tmp_path, 'en', 'RoleInfo.json').is_file()
	assert _raw_path(tmp_path, 'en', 'MonsterInfo.json').is_file()
	assert _raw_path(tmp_path, 'en', '.updater_state.json').is_file()
	assert not (tmp_path / 'data' / 'en' / 'MultiText.json').exists()
	assert not (tmp_path / 'data' / 'en' / 'ItemInfo.json').exists()
	assert not (tmp_path / 'data' / 'en' / 'WeaponConf.json').exists()
	assert not (tmp_path / 'data' / 'en' / 'RoleInfo.json').exists()
	assert not (tmp_path / 'data' / 'en' / 'MonsterInfo.json').exists()
	assert not (tmp_path / 'data' / 'en' / '.updater_state.json').exists()


def test_base_data_updater_defaults_to_arikatsu_source(tmp_path: Path, monkeypatch) -> None:
	_prepare_workspace(tmp_path, monkeypatch)

	updater = BaseDataUpdater(lang='English')

	assert updater.source == 'arikatsu'


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

	assert not (tmp_path / 'data' / 'en' / 'items.json').exists()
	assert not (tmp_path / 'data' / 'en' / 'weapons.json').exists()

	item_catalog = json.loads((tmp_path / 'data' / 'catalog' / 'items.json').read_text(encoding='utf-8'))
	weapon_catalog = json.loads((tmp_path / 'data' / 'catalog' / 'weapons.json').read_text(encoding='utf-8'))

	assert 'shellcredit' in item_catalog
	assert 'stale' not in item_catalog
	assert 'trainingbroadblade' in weapon_catalog
	assert 'stale' not in weapon_catalog


def test_update_items_writes_catalog_and_locale_outputs(tmp_path: Path, monkeypatch) -> None:
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

	updater = BaseDataUpdater(lang='English')
	updater.updateItems()

	item_catalog = json.loads((tmp_path / 'data' / 'catalog' / 'items.json').read_text(encoding='utf-8'))
	weapon_catalog = json.loads((tmp_path / 'data' / 'catalog' / 'weapons.json').read_text(encoding='utf-8'))
	item_locale = json.loads((tmp_path / 'data' / 'locale' / 'en' / 'items.json').read_text(encoding='utf-8'))
	weapon_locale = json.loads((tmp_path / 'data' / 'locale' / 'en' / 'weapons.json').read_text(encoding='utf-8'))
	item_lookup = json.loads((tmp_path / 'data' / 'locale' / 'en' / 'lookup' / 'items.json').read_text(encoding='utf-8'))
	weapon_lookup = json.loads((tmp_path / 'data' / 'locale' / 'en' / 'lookup' / 'weapons.json').read_text(encoding='utf-8'))

	assert item_catalog == {
		'shellcredit': {
			'id': 1,
			'text_key': 'ItemInfo_1_Name',
			'image': 'IconA/T_IconA_AccountExp_UI.png',
		},
	}
	assert weapon_catalog == {
		'trainingbroadblade': {
			'id': 101,
			'text_key': 'WeaponConf_1_WeaponName',
			'rarity': 4,
			'image': 'IconWeapon/T_Weapon_UI.png',
		},
	}
	assert item_locale == {
		'shellcredit': {
			'display_name': 'Shell Credit',
			'normalized': 'shellcredit',
			'aliases': ['shellcredit'],
		},
	}
	assert weapon_locale == {
		'trainingbroadblade': {
			'display_name': 'Training Broadblade',
			'normalized': 'trainingbroadblade',
			'aliases': ['trainingbroadblade'],
		},
	}
	assert item_lookup == {'shellcredit': 'shellcredit'}
	assert weapon_lookup == {'trainingbroadblade': 'trainingbroadblade'}


def test_non_english_echo_update_uses_catalog_keys_for_locale_outputs(tmp_path: Path, monkeypatch) -> None:
	_prepare_workspace(tmp_path, monkeypatch)
	_write_json(tmp_path / 'data' / 'languages.json', {'English': 'en', 'Japanese': 'ja'})
	_write_json(
		tmp_path / 'data' / 'en' / 'MultiText.json',
		{'MonsterInfo_310000010_Name': 'Vanguard Junrock'},
	)
	_write_json(
		tmp_path / 'data' / 'ja' / 'MultiText.json',
		{'MonsterInfo_310000010_Name': '先鋒岩塊'},
	)

	english_updater = BaseDataUpdater(lang='English')
	english_updater.updateEcho()

	localized_updater = BaseDataUpdater(lang='Japanese')
	localized_updater.updateEcho()

	echo_catalog = json.loads((tmp_path / 'data' / 'catalog' / 'echoes.json').read_text(encoding='utf-8'))
	ja_locale = json.loads((tmp_path / 'data' / 'locale' / 'ja' / 'echoes.json').read_text(encoding='utf-8'))
	ja_lookup = json.loads((tmp_path / 'data' / 'locale' / 'ja' / 'lookup' / 'echoes.json').read_text(encoding='utf-8'))

	assert echo_catalog == {
		'vanguardjunrock': {
			'id': 310000010,
			'text_key': 'MonsterInfo_310000010_Name',
		},
	}
	assert ja_locale == {
		'vanguardjunrock': {
			'display_name': '先鋒岩塊',
			'normalized': '先鋒岩塊',
			'aliases': ['先鋒岩塊'],
		},
	}
	assert ja_lookup == {'先鋒岩塊': 'vanguardjunrock'}


def test_update_echo_writes_catalog_image_from_monsterinfo(tmp_path: Path, monkeypatch) -> None:
	_prepare_workspace(tmp_path, monkeypatch)
	_write_json(
		tmp_path / 'data' / 'en' / 'MultiText.json',
		{'MonsterInfo_310000010_Name': 'Vanguard Junrock'},
	)
	_write_json(
		tmp_path / 'data' / 'en' / 'MonsterInfo.json',
		[
			{
				'Id': 310000010,
				'Name': 'MonsterInfo_310000010_Name',
				'Icon': '/Game/Aki/UI/UIResources/Common/Image/IconMonsterHead/T_IconMonsterHead_015_UI.T_IconMonsterHead_015_UI',
			},
		],
	)

	updater = BaseDataUpdater(lang='English')
	updater.updateEcho()

	echo_catalog = json.loads((tmp_path / 'data' / 'catalog' / 'echoes.json').read_text(encoding='utf-8'))
	echo_locale = json.loads((tmp_path / 'data' / 'locale' / 'en' / 'echoes.json').read_text(encoding='utf-8'))
	echo_lookup = json.loads((tmp_path / 'data' / 'locale' / 'en' / 'lookup' / 'echoes.json').read_text(encoding='utf-8'))

	assert echo_catalog == {
		'vanguardjunrock': {
			'id': 310000010,
			'text_key': 'MonsterInfo_310000010_Name',
			'image': 'IconMonsterHead/T_IconMonsterHead_015_UI.png',
		},
	}
	assert echo_locale == {
		'vanguardjunrock': {
			'display_name': 'Vanguard Junrock',
			'normalized': 'vanguardjunrock',
			'aliases': ['vanguardjunrock'],
		},
	}
	assert echo_lookup == {'vanguardjunrock': 'vanguardjunrock'}


def test_update_characters_writes_catalog_image_and_rarity_from_roleinfo(tmp_path: Path, monkeypatch) -> None:
	_prepare_workspace(tmp_path, monkeypatch)
	_write_json(
		tmp_path / 'data' / 'en' / 'MultiText.json',
		{
			'RoleInfo_1102_Name': 'Sanhua',
		},
	)
	_write_json(
		tmp_path / 'data' / 'en' / 'RoleInfo.json',
		[
			{
				'Id': 1102,
				'Name': 'RoleInfo_1102_Name',
				'QualityId': 5,
				'ItemQualityId': 4,
				'RoleHeadIcon': '/Game/Aki/UI/UIResources/Common/Image/IconRoleHead80/T_IconRoleHead80_14_UI.T_IconRoleHead80_14_UI',
			},
			{
				'Id': 5101,
				'Name': 'RoleInfo_5101_Name',
				'RoleHeadIcon': '/Game/Aki/UI/UIResources/Common/Image/IconMonsterHead/T_IconMonsterHead_1_UI.T_IconMonsterHead_1_UI',
			},
		],
	)

	updater = BaseDataUpdater(lang='English')
	updater.updateCharacters()

	character_catalog = json.loads((tmp_path / 'data' / 'catalog' / 'characters.json').read_text(encoding='utf-8'))
	character_locale = json.loads((tmp_path / 'data' / 'locale' / 'en' / 'characters.json').read_text(encoding='utf-8'))
	character_lookup = json.loads((tmp_path / 'data' / 'locale' / 'en' / 'lookup' / 'characters.json').read_text(encoding='utf-8'))

	assert character_catalog == {
		'sanhua': {
			'id': 1102,
			'text_key': 'RoleInfo_1102_Name',
			'image': 'IconRoleHead80/T_IconRoleHead80_14_UI.png',
			'rarity': 5,
		},
	}
	assert character_locale == {
		'sanhua': {
			'display_name': 'Sanhua',
			'normalized': 'sanhua',
			'aliases': ['sanhua'],
		},
	}
	assert character_lookup == {'sanhua': 'sanhua'}


def test_update_stats_and_defined_text_write_locale_outputs(tmp_path: Path, monkeypatch) -> None:
	_prepare_workspace(tmp_path, monkeypatch)
	_write_json(tmp_path / 'data' / 'languages.json', {'English': 'en', 'Japanese': 'ja'})
	_write_json(
		tmp_path / 'data' / 'en' / 'MultiText.json',
		{
			'PropertyIndex_10003_Name': 'HP',
			'PrefabTextItem_1547656443_Text': 'Terminal',
			'PrefabTextItem_128820487_Text': 'Claim',
			'PrefabTextItem_3963945691_Text': 'Activated',
		},
	)
	_write_json(
		tmp_path / 'data' / 'ja' / 'MultiText.json',
		{
			'PropertyIndex_10003_Name': '体力',
			'PrefabTextItem_1547656443_Text': '端末',
			'PrefabTextItem_128820487_Text': '受取',
			'PrefabTextItem_3963945691_Text': '発動済み',
		},
	)

	english_updater = BaseDataUpdater(lang='English')
	english_updater.updateEchoStats()
	english_updater.updateDefinedText()

	localized_updater = BaseDataUpdater(lang='Japanese')
	localized_updater.updateEchoStats()
	localized_updater.updateDefinedText()

	stats_catalog = json.loads((tmp_path / 'data' / 'catalog' / 'stats.json').read_text(encoding='utf-8'))
	ja_stats = json.loads((tmp_path / 'data' / 'locale' / 'ja' / 'stats.json').read_text(encoding='utf-8'))
	ja_stats_lookup = json.loads((tmp_path / 'data' / 'locale' / 'ja' / 'lookup' / 'stats.json').read_text(encoding='utf-8'))
	ja_defined = json.loads((tmp_path / 'data' / 'locale' / 'ja' / 'definedText.json').read_text(encoding='utf-8'))
	ja_defined_lookup = json.loads((tmp_path / 'data' / 'locale' / 'ja' / 'lookup' / 'definedText.json').read_text(encoding='utf-8'))

	assert stats_catalog == {
		'hp': {
			'text_key': 'PropertyIndex_10003_Name',
		},
	}
	assert ja_stats == {
		'hp': {
			'display_name': '体力',
			'normalized': '体力',
			'aliases': ['体力'],
		},
	}
	assert ja_stats_lookup == {'体力': 'hp'}
	assert ja_defined == {
		'PrefabTextItem_1547656443_Text': {
			'display_text': '端末',
			'normalized': '端末',
			'aliases': ['端末'],
		},
		'PrefabTextItem_128820487_Text': {
			'display_text': '受取',
			'normalized': '受取',
			'aliases': ['受取'],
		},
		'PrefabTextItem_3963945691_Text': {
			'display_text': '発動済み',
			'normalized': '発動済み',
			'aliases': ['発動済み'],
		},
	}
	assert ja_defined_lookup == {
		'端末': 'PrefabTextItem_1547656443_Text',
		'受取': 'PrefabTextItem_128820487_Text',
		'発動済み': 'PrefabTextItem_3963945691_Text',
	}


def test_update_sonata_writes_generated_outputs_without_legacy_compat_file(tmp_path: Path, monkeypatch) -> None:
	_prepare_workspace(tmp_path, monkeypatch)
	_write_json(tmp_path / 'data' / 'languages.json', {'English': 'en', 'Japanese': 'ja'})
	_write_json(
		tmp_path / 'data' / 'en' / 'MultiText.json',
		{'PhantomFetter_1_Name': 'Moonlit Clouds'},
	)
	_write_json(
		tmp_path / 'data' / 'ja' / 'MultiText.json',
		{'PhantomFetter_1_Name': '月を窺う軽雲'},
	)

	english_updater = BaseDataUpdater(lang='English')
	english_updater.updateSonata()

	localized_updater = BaseDataUpdater(lang='Japanese')
	localized_updater.updateSonata()

	sonata_catalog = json.loads((tmp_path / 'data' / 'catalog' / 'sonatas.json').read_text(encoding='utf-8'))
	ja_locale = json.loads((tmp_path / 'data' / 'locale' / 'ja' / 'sonatas.json').read_text(encoding='utf-8'))
	ja_lookup = json.loads((tmp_path / 'data' / 'locale' / 'ja' / 'lookup' / 'sonatas.json').read_text(encoding='utf-8'))

	assert sonata_catalog == {
		'moonlitclouds': {
			'id': 1,
			'text_key': 'PhantomFetter_1_Name',
		},
	}
	assert ja_locale == {
		'moonlitclouds': {
			'display_name': '月を窺う軽雲',
			'normalized': '月を窺う軽雲',
			'aliases': ['月を窺う軽雲'],
		},
	}
	assert ja_lookup == {'月を窺う軽雲': 'moonlitclouds'}
	assert not (tmp_path / 'data' / 'en' / 'sonataName.json').exists()
	assert not (tmp_path / 'data' / 'ja' / 'sonataName.json').exists()


def test_run_bootstraps_missing_generated_outputs_from_existing_source_data(
	tmp_path: Path,
	monkeypatch,
) -> None:
	_prepare_workspace(tmp_path, monkeypatch)
	_write_json(
		tmp_path / 'data' / 'en' / 'MultiText.json',
		{
			'ItemInfo_1_Name': 'Shell Credit',
			'WeaponConf_1_WeaponName': 'Training Broadblade',
			'PropertyIndex_10003_Name': 'HP',
			'PhantomFetter_1_Name': 'Moonlit Clouds',
			'Achievement_1_Name': 'First Step',
			'RoleInfo_1102_Name': 'Sanhua',
			'MonsterInfo_310000010_Name': 'Vanguard Junrock',
			'PrefabTextItem_1547656443_Text': 'Terminal',
			'PrefabTextItem_128820487_Text': 'Claim',
			'PrefabTextItem_3963945691_Text': 'Activated',
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

	monkeypatch.setattr(BaseDataUpdater, 'updateFiles', lambda self: None)

	updater = BaseDataUpdater(lang='English')
	updater.run()

	assert (tmp_path / 'data' / 'catalog' / 'items.json').is_file()
	assert (tmp_path / 'data' / 'catalog' / 'echoes.json').is_file()
	assert (tmp_path / 'data' / 'catalog' / 'sonatas.json').is_file()
	assert (tmp_path / 'data' / 'locale' / 'en' / 'lookup' / 'definedText.json').is_file()


def test_non_english_run_bootstraps_english_catalog_before_locale_generation(
	tmp_path: Path,
	monkeypatch,
) -> None:
	_prepare_workspace(tmp_path, monkeypatch)
	_write_json(tmp_path / 'data' / 'languages.json', {'English': 'en', 'Japanese': 'ja'})
	_write_json(
		tmp_path / 'data' / 'en' / 'MultiText.json',
		{
			'ItemInfo_1_Name': 'Shell Credit',
			'WeaponConf_1_WeaponName': 'Training Broadblade',
			'PropertyIndex_10003_Name': 'HP',
			'PhantomFetter_1_Name': 'Moonlit Clouds',
			'Achievement_1_Name': 'First Step',
			'RoleInfo_1102_Name': 'Sanhua',
			'MonsterInfo_310000010_Name': 'Vanguard Junrock',
			'PrefabTextItem_1547656443_Text': 'Terminal',
			'PrefabTextItem_128820487_Text': 'Claim',
			'PrefabTextItem_3963945691_Text': 'Activated',
		},
	)
	_write_json(
		tmp_path / 'data' / 'ja' / 'MultiText.json',
		{
			'ItemInfo_1_Name': 'シェルコイン',
			'WeaponConf_1_WeaponName': '訓練用大剣',
			'PropertyIndex_10003_Name': '体力',
			'PhantomFetter_1_Name': '月を窺う軽雲',
			'Achievement_1_Name': '最初の一歩',
			'RoleInfo_1102_Name': '散華',
			'MonsterInfo_310000010_Name': '先鋒岩塊',
			'PrefabTextItem_1547656443_Text': '端末',
			'PrefabTextItem_128820487_Text': '受取',
			'PrefabTextItem_3963945691_Text': '発動済み',
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
	_write_json(
		tmp_path / 'data' / 'ja' / 'ItemInfo.json',
		[
			{
				'Id': 1,
				'Name': 'ItemInfo_1_Name',
				'Icon': '/Game/Aki/UI/UIResources/Common/Image/IconA/T_IconA_AccountExp_UI.T_IconA_AccountExp_UI',
			},
		],
	)
	_write_json(
		tmp_path / 'data' / 'ja' / 'WeaponConf.json',
		[
			{
				'WeaponName': 'WeaponConf_1_WeaponName',
				'ModelId': 101,
				'QualityId': 4,
				'Icon': '/Game/Aki/UI/UIResources/Common/Image/IconWeapon/T_Weapon_UI.T_Weapon_UI',
			},
		],
	)

	monkeypatch.setattr(BaseDataUpdater, 'updateFiles', lambda self: None)

	updater = BaseDataUpdater(lang='Japanese')
	updater.run()

	echo_catalog = json.loads((tmp_path / 'data' / 'catalog' / 'echoes.json').read_text(encoding='utf-8'))
	ja_locale = json.loads((tmp_path / 'data' / 'locale' / 'ja' / 'echoes.json').read_text(encoding='utf-8'))

	assert echo_catalog == {
		'vanguardjunrock': {
			'id': 310000010,
			'text_key': 'MonsterInfo_310000010_Name',
		},
	}
	assert ja_locale == {
		'vanguardjunrock': {
			'display_name': '先鋒岩塊',
			'normalized': '先鋒岩塊',
			'aliases': ['先鋒岩塊'],
		},
	}