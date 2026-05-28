from __future__ import annotations

import json
from types import SimpleNamespace

from wuwa_inventory_kamera.cli import reprocess


def test_detect_raw_session_kind_recognizes_weapon_sessions(tmp_path) -> None:
    raw_dir = tmp_path / 'raw'
    (raw_dir / 'weapon_0001').mkdir(parents=True)

    assert reprocess._detect_raw_session_kind(raw_dir) == 'weapons'


def test_detect_raw_session_kind_recognizes_character_sessions(tmp_path) -> None:
    raw_dir = tmp_path / 'raw'
    (raw_dir / 'char_0001').mkdir(parents=True)

    assert reprocess._detect_raw_session_kind(raw_dir) == 'characters'


def test_detect_raw_session_kinds_recognizes_mixed_sessions(tmp_path) -> None:
    raw_dir = tmp_path / 'raw'
    (raw_dir / 'echo_0001').mkdir(parents=True)
    (raw_dir / 'char_0001').mkdir(parents=True)

    assert reprocess._detect_raw_session_kind(raw_dir) is None
    assert reprocess._detect_raw_session_kinds(raw_dir) == ['echoes', 'characters']


def test_filter_scans_supports_generic_scan_ids() -> None:
    scans = [
        SimpleNamespace(index=3),
        SimpleNamespace(index=5),
        SimpleNamespace(index=7),
    ]

    filtered = reprocess._filter_scans(
        scans,
        scan_ids='0005,0007',
        scan_id_range=None,
        scan_label='weapon',
    )

    assert [scan.index for scan in filtered] == [5, 7]


def test_filter_scans_supports_generic_scan_ranges() -> None:
    scans = [
        SimpleNamespace(index=3),
        SimpleNamespace(index=5),
        SimpleNamespace(index=7),
    ]

    filtered = reprocess._filter_scans(
        scans,
        scan_ids=None,
        scan_id_range='4,7',
        scan_label='weapon',
    )

    assert [scan.index for scan in filtered] == [5]


def test_main_reprocesses_mixed_sessions_and_writes_one_file_per_kind(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    raw_dir = tmp_path / 'session' / 'raw'
    (raw_dir / 'echo_0001').mkdir(parents=True)
    (raw_dir / 'char_0001').mkdir(parents=True)
    output_dir = tmp_path / 'out'

    def fake_load_session_scans(_raw_dir, session_kind: str):
        if session_kind == 'echoes':
            return [SimpleNamespace(index=1)]
        if session_kind == 'characters':
            return [SimpleNamespace(index=2)]
        raise AssertionError(f'unexpected session kind: {session_kind}')

    monkeypatch.setattr(reprocess, '_load_session_scans', fake_load_session_scans)
    monkeypatch.setattr(
        reprocess,
        '_run_echo_service',
        lambda scans, raw_dir, **kwargs: [{'index': scans[0].index, 'kind': 'echo'}],
    )
    monkeypatch.setattr(
        reprocess,
        '_run_character_service',
        lambda scans, raw_dir, **kwargs: {
            'Rover': {'index': scans[0].index, 'kind': 'character'},
        },
    )
    monkeypatch.setattr(reprocess.sys, 'argv', [
        'wuwa-reprocess',
        '--raw-dir',
        str(raw_dir),
        '--output-dir',
        str(output_dir),
        '--provider',
        'cpu',
    ])

    reprocess.main()

    assert json.loads((output_dir / 'echoes_wuwainventorykamera.json').read_text(encoding='utf-8')) == [
        {'index': 1, 'kind': 'echo'},
    ]
    assert json.loads((output_dir / 'characters_wuwainventorykamera.json').read_text(encoding='utf-8')) == {
        'Rover': {'index': 2, 'kind': 'character'},
    }

    stdout = capsys.readouterr().out
    assert 'echoes_wuwainventorykamera.json: 1 echo(s)' in stdout
    assert 'characters_wuwainventorykamera.json: 1 character(s)' in stdout


def test_main_skips_empty_filtered_kind_in_mixed_sessions(
    tmp_path,
    monkeypatch,
) -> None:
    raw_dir = tmp_path / 'session' / 'raw'
    (raw_dir / 'echo_0001').mkdir(parents=True)
    (raw_dir / 'char_0001').mkdir(parents=True)
    output_dir = tmp_path / 'out'

    def fake_load_session_scans(_raw_dir, session_kind: str):
        if session_kind == 'echoes':
            return [SimpleNamespace(index=1)]
        if session_kind == 'characters':
            return [SimpleNamespace(index=2)]
        raise AssertionError(f'unexpected session kind: {session_kind}')

    monkeypatch.setattr(reprocess, '_load_session_scans', fake_load_session_scans)
    monkeypatch.setattr(
        reprocess,
        '_run_echo_service',
        lambda scans, raw_dir, **kwargs: [{'index': scans[0].index, 'kind': 'echo'}],
    )

    character_calls: list[str] = []

    def fake_run_character_service(scans, raw_dir, **kwargs):
        character_calls.append('called')
        return {'Rover': {'index': scans[0].index, 'kind': 'character'}}

    monkeypatch.setattr(reprocess, '_run_character_service', fake_run_character_service)
    monkeypatch.setattr(reprocess.sys, 'argv', [
        'wuwa-reprocess',
        '--raw-dir',
        str(raw_dir),
        '--output-dir',
        str(output_dir),
        '--provider',
        'cpu',
        '--scan-ids',
        '0001',
    ])

    reprocess.main()

    assert json.loads((output_dir / 'echoes_wuwainventorykamera.json').read_text(encoding='utf-8')) == [
        {'index': 1, 'kind': 'echo'},
    ]
    assert not (output_dir / 'characters_wuwainventorykamera.json').exists()
    assert character_calls == []