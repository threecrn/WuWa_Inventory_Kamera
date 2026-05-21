from __future__ import annotations

import json

from wuwa_inventory_kamera.scraping.utils.common import loadWeaponRawScans


def test_load_weapon_raw_scans_discovers_weapon_directories(tmp_path) -> None:
    raw_dir = tmp_path / 'raw'
    scan_dir = raw_dir / 'weapon_0003'
    scan_dir.mkdir(parents=True)

    (scan_dir / 'full.png').write_bytes(b'not-an-image-needed-for-discovery')
    with open(scan_dir / 'meta.json', 'w', encoding='utf-8') as handle:
        json.dump(
            {
                'session_id': '2026-05-21_10-00-00',
                'index': 3,
                'page': 0,
                'row': 0,
                'col': 0,
                'screen_width': 1920,
                'screen_height': 1080,
                'monitor': 1,
            },
            handle,
        )

    scans = loadWeaponRawScans(raw_dir)

    assert len(scans) == 1
    assert scans[0].index == 3
    assert scans[0].full_path == scan_dir / 'full.png'
    assert scans[0].sonata_path is None