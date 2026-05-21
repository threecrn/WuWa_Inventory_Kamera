from __future__ import annotations

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