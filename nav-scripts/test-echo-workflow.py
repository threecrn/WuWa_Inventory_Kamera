# Test nav script for the new echo scanning workflow.
#
# Validates the full capture pipeline per-echo:
#   1. OCR the level from the dedicated level ROI.
#   2. Pick the correct sonataIcon slot (level_X vs level_XX).
#   3. Capture the sonata icon crop and run icon matching.
#   4. Capture the echoName ROI, apply colour filter, and OCR it.
#   5. Print results for each echo to verify correctness.
#
# Usage:
#   wuwa-nav nav-scripts/test-echo-workflow.py
#
# Requires the game to be open with the inventory visible.

# ── CONFIG ───────────────────────────────────────────────────────────────────

SORT_ORDER = 'level'   # Sort echoes by level descending before scanning
TOTAL      = 30        # Number of echoes to test (first N in sorted order)
OUTPUT_DIR = 'captures/test-echo-workflow'  # Save debug images here

# ── Script body ──────────────────────────────────────────────────────────────

import sys
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from wuwa_inventory_kamera.scraping.ocr import imageToString, get_backend
from wuwa_inventory_kamera.scraping.matching.sonata_icon import SonataIconMatcher
from wuwa_inventory_kamera.scraping.service.ocr_service import _filter_echo_name
from wuwa_inventory_kamera.scraping.data import sonataName
from wuwa_inventory_kamera.game.screen_info import ScreenInfo

# ── Helpers ──────────────────────────────────────────────────────────────────

session_id = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
out_dir    = Path(OUTPUT_DIR) / session_id
out_dir.mkdir(parents=True, exist_ok=True)

matcher = SonataIconMatcher()
backend = get_backend('rapidocr')

print(f'test-echo-workflow  session={session_id}')
print(f'  sort   : {SORT_ORDER or "(unchanged)"}')
print(f'  total  : {TOTAL}')
print(f'  output : {out_dir}')
print()

# ── Navigate ─────────────────────────────────────────────────────────────────

focus_window()
#open_inventory()
switch_tab('echoes')

if SORT_ORDER:
    set_sort(SORT_ORDER)

# Get screen dimensions from a snapshot to build layout
snap = snapshot()
layout = ScreenInfo(snap.window.width, snap.window.height, snap.window.monitor)
ei = layout.echoes
print(f'  screen : {snap.window.width}x{snap.window.height}')
print()

# ── Per-echo test loop ───────────────────────────────────────────────────────

results = []

for idx in range(TOTAL):
    goto_index(idx, click_wait=0.2)

    # Grab a full screenshot as BGR ndarray
    full = screenshot(roi='full', as_image=True)
    if full is None:
        print(f'  [{idx:03d}] SKIP (dry-run)')
        continue

    # ── 1. Level OCR ─────────────────────────────────────────────────────
    level_crop = full[
        int(ei.level.y) : int(ei.level.y + ei.level.h),
        int(ei.level.x) : int(ei.level.x + ei.level.w),
    ]
    # imageToString expects RGB
    level_rgb = cv2.cvtColor(level_crop, cv2.COLOR_BGR2RGB)
    level_text = imageToString(level_rgb, allowedChars='0123456789', backend=backend).strip()
    level = int(level_text) if level_text.isdigit() else -1
    two_digits = len(level_text) == 2

    # ── 2. Sonata icon crop (level-dependent) ────────────────────────────
    si_raw = ei.sonataIcon
    cx, cy, r = None, None, None

    if hasattr(si_raw, 'level_X'):
        si_slot = si_raw.level_XX if two_digits else si_raw.level_X
        si = si_slot.icon
        cx = si_slot.circle.x
        cy = si_slot.circle.y
        r  = si_raw.radius
    else:
        si = si_raw

    sonata_icon = full[
        int(si.y) : int(si.y + si.h),
        int(si.x) : int(si.x + si.w),
    ]

    # ── 3. Sonata icon matching ──────────────────────────────────────────
    sonata_key = matcher.match_to_sonata_key(sonata_icon, sonataName, cx=cx, cy=cy, r=r)

    # ── 4. Echo name OCR (colour-filtered) ───────────────────────────────
    echo_name_bgr = full[
        int(ei.echoName.y) : int(ei.echoName.y + ei.echoName.h),
        int(ei.echoName.x) : int(ei.echoName.x + ei.echoName.w),
    ]
    filtered = _filter_echo_name(echo_name_bgr)  # returns RGB white-on-black
    name_text = imageToString(filtered, backend=backend).strip().lower()

    # ── 5. Report ────────────────────────────────────────────────────────
    status = 'OK' if (sonata_key and name_text) else 'WARN'
    print(f'  [{idx:03d}] level={level:2d}  sonata={sonata_key or "???":24s}  name={name_text or "???"}  [{status}]')

    results.append({
        'index': idx,
        'level': level,
        'level_text': level_text,
        'two_digits': two_digits,
        'sonata': sonata_key,
        'name': name_text,
        'status': status,
    })

    # ── Save debug crops ─────────────────────────────────────────────────
    item_dir = out_dir / f'echo_{idx:04d}'
    item_dir.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(item_dir / 'level.png'), level_crop)
    cv2.imwrite(str(item_dir / 'sonata_icon.png'), sonata_icon)
    cv2.imwrite(str(item_dir / 'echo_name_raw.png'), echo_name_bgr)
    cv2.imwrite(str(item_dir / 'echo_name_filtered.png'), filtered)

# ── Summary ──────────────────────────────────────────────────────────────────

close_inventory()

ok_count = sum(1 for r in results if r['status'] == 'OK')
print(f'\n  Done: {ok_count}/{len(results)} OK')
print(f'  Debug crops: {out_dir}')
