# Navigates the entire echo list and captures only the header sonata icon of
# each echo. The output directory is ready for the update_sonata_templates
# tool to consume directly.
#
# Output layout:
#   <OUTPUT_DIR>/<session-id>/
#     echo_0000/sonata_icon.png
#     echo_0001/sonata_icon.png
#     ...
#
# Usage:
#   wuwa-nav nav-scripts/scan-sonata-icons.py
#
# Build templates afterward:
#   python tools/update_sonata_templates/main.py update \
#       --crop-dir <OUTPUT_DIR>/<session-id>

# ── CONFIG — edit before running ─────────────────────────────────────────────

SORT_ORDER  = 'level'     # sort order to apply before scanning; None = keep current
OUTPUT_DIR  = 'captures'  # root directory; session subfolder is created automatically
TOTAL       = 24          # TODO: replace with read_count() once available

# ── Script body — no changes needed below this line ──────────────────────────

import sys
from datetime import datetime
from pathlib import Path

_CELLS_PER_PAGE = 24

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from wuwa_inventory_kamera import imgio
from wuwa_inventory_kamera.game.screen_info import ScreenInfo
from wuwa_inventory_kamera.scraping.ocr import get_backend, imageToString
from wuwa_inventory_kamera.scraping.service.echo_capture_utils import (
    decide_echo_level,
    select_level_dependent_sonata_slot,
)


def _capture_sonata_icon(full_bgr, echoes_info, backend):
    level_roi = echoes_info.level
    level_crop = full_bgr[
        int(level_roi.y) : int(level_roi.y + level_roi.h),
        int(level_roi.x) : int(level_roi.x + level_roi.w),
    ]
    level_text = imageToString(
        imgio.convert_color(level_crop, imgio.ColorCode.BGR2RGB),
        allowedChars='0123456789',
        backend=backend,
    ).strip()
    level_decision = decide_echo_level(level_text=level_text)
    sonata_slot = select_level_dependent_sonata_slot(
        echoes_info.sonataIcon,
        two_digits=level_decision.two_digits,
    )
    icon_roi = sonata_slot.icon
    return full_bgr[
        int(icon_roi.y) : int(icon_roi.y + icon_roi.h),
        int(icon_roi.x) : int(icon_roi.x + icon_roi.w),
    ]

session_id = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
out_dir    = Path(OUTPUT_DIR) / session_id
backend = get_backend('rapidocr')

print(f'scan-sonata-icons  session={session_id}')
print(f'  sort   : {SORT_ORDER or "(unchanged)"}')
print(f'  output : {out_dir}')
print(f'  total  : {TOTAL}')

focus_window()
open_inventory()
switch_tab('echoes')

if SORT_ORDER:
    set_sort(SORT_ORDER)

out_dir.mkdir(parents=True, exist_ok=True)

snap = snapshot()
layout = ScreenInfo(snap.window.width, snap.window.height, snap.window.monitor)
echoes_info = layout.echoes
print(f'  screen : {snap.window.width}x{snap.window.height}')

# ── Sonata-icon pass ──────────────────────────────────────────────────────────

for idx in range(TOTAL):
    goto_index(idx, click_wait=0.1)

    item_dir = out_dir / f'echo_{idx:04d}'
    item_dir.mkdir(parents=True, exist_ok=True)

    full = screenshot(roi='full', as_image=True)
    if full is None:
        print(f'\r  {idx + 1}/{TOTAL} (dry-run)', end='', flush=True)
        continue

    sonata_icon = _capture_sonata_icon(full, echoes_info, backend)
    imgio.imwrite(str(item_dir / 'sonata_icon.png'), sonata_icon)

    pct = (idx + 1) / TOTAL * 100
    print(f'\r  {idx + 1}/{TOTAL} ({pct:.0f}%)', end='', flush=True)

print()  # newline after progress

close_inventory()

print(f'\nDone.')
print(f'  Captures  : {out_dir}')
print(f'  Next step : python tools/update_sonata_templates/main.py update --crop-dir {out_dir}')
