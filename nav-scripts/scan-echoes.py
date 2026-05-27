# Emulates the wuwa-scan workflow as a wuwa-nav script.
# Navigates through each requested inventory section and saves raw
# screenshots to disk — no OCR, no OcrService required.
#
# Echo captures are laid out so wuwa-reprocess can consume them directly:
#
#   <output-dir>/<session-id>/raw/
#     echo_0000/full.png      ← full screenshot (stats panel, before scroll)
#     echo_0000/meta.json     ← grid position metadata
#     echo_0001/ ...
#
#   captures/<session-id>/weapons/   0000_full.png ...
#   captures/<session-id>/devItems/  0000_full.png ...
#   captures/<session-id>/resources/ 0000_full.png ...
#   captures/<session-id>/manifest.json
#
# Usage:
#   wuwa-nav nav-scripts/scan-echoes.py
#
# Reprocess echoes afterward:
#   wuwa-reprocess --raw-dir captures/<session-id>/raw

# ── CONFIG — edit before running ─────────────────────────────────────────────

SORT_ORDER = 'level'      # set sort before scanning; None = keep current order
OUTPUT_DIR = 'captures'   # root directory; session subfolder is created automatically

# ── Script body — no changes needed below this line ──────────────────────────

import json
from datetime import datetime
from pathlib import Path

_CELLS_PER_PAGE = 24
_GRID_COLS      = 6


def _grid_pos(idx: int) -> tuple[int, int, int]:
    """Return (page, row, col) for a zero-based scan index."""
    page      = idx // _CELLS_PER_PAGE
    local_idx = idx % _CELLS_PER_PAGE
    return page, local_idx // _GRID_COLS, local_idx % _GRID_COLS


session_id = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
base_dir   = Path(OUTPUT_DIR) / session_id

manifest = {
    'session':  session_id,
}

print(f'scripted-scan  session={session_id}')
print(f'  sort     : {SORT_ORDER or "(unchanged)"}')
print(f'  output   : {base_dir}')

focus_window()

# Capture screen geometry so wuwa-reprocess can reconstruct ScreenInfo.
_snap = snapshot()
screen_width  = _snap.window.width
screen_height = _snap.window.height
monitor       = _snap.window.monitor
manifest['screen_width']  = screen_width
manifest['screen_height'] = screen_height
manifest['monitor']       = monitor
print(f'  screen   : {screen_width}x{screen_height} (monitor {monitor})')

open_inventory()

switch_tab("echoes")

set_sort('rarity')       # hotfix: toggle once so the UI re-sorts reliably
if SORT_ORDER:
    set_sort(SORT_ORDER)

# TODO: until it works, hardcode total
total = 24
#info  = read_count()
#total = info['items']
#print(f'\n  {total} item(s) found')

is_echo = True

# Echoes live under raw/ so wuwa-reprocess can pick them up directly.
# Other scrapers get their own named subdirectory.
out_dir = (base_dir / 'raw')
out_dir.mkdir(parents=True, exist_ok=True)

saved: list[dict] = []

# 24 is one "page" of echoes on 1920x1080
batch=0
batch_size=24

while batch*batch_size < total:
    goto_index(batch*batch_size)  # ensure we're at the start of the list before beginning

    batch_end = min(total, (batch + 1) * batch_size)
    for idx in range(batch*batch_size, batch_end):
        goto_index(idx, scroll_wait=0.5, click_wait=0.1)  # add waits to ensure UI has time to update before screenshots

        page, row, col = _grid_pos(idx)

        item_dir = out_dir / f'echo_{idx:04d}'
        item_dir.mkdir(parents=True, exist_ok=True)

        # Full screenshot with the stats panel visible (before any scroll).
        screenshot(roi='full', out=item_dir / 'full.png')

        # Metadata consumed by wuwa-reprocess
        meta = {
            'session_id':   session_id,
            'index':        idx,
            'page':         page,
            'row':          row,
            'col':          col,
            'screen_width':  screen_width,
            'screen_height': screen_height,
            'monitor':       monitor,
        }
        (item_dir / 'meta.json').write_text(
            json.dumps(meta, indent=2), encoding='utf-8'
        )

        files = [
            str(item_dir / 'full.png'),
        ]

        saved.append({
            'index': idx,
            'page':  page,
            'row':   row,
            'col':   col,
            'files': files,
        })

        pct = (idx + 1) / total * 100
        print(f'\r  {idx + 1}/{total} ({pct:.0f}%)', end='', flush=True)

    print()  # newline after progress line

    batch += 1

manifest['scrapers'] = {
    'echoes': {
        'total':      total,
        'sort_order': SORT_ORDER,
        'items':      saved,
    },
}

# ── Finish ────────────────────────────────────────────────────────────────────

close_inventory()

manifest_path = base_dir / 'manifest.json'
manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')

print(f'\nDone.')
print(f'  Raw captures : {base_dir}')
print(f'  Manifest     : {manifest_path}')
print(f'  Reprocess    : wuwa-reprocess --raw-dir {base_dir / "raw"}')
