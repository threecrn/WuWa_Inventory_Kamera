# Navigates the entire echo list and captures only the sonata icon region of
# each echo — no full screenshots, no OCR.  The output directory is ready for
# the update_sonata_templates tool to consume directly.
#
# Output layout:
#   <OUTPUT_DIR>/<session-id>/
#     echo_0000/sonata.png
#     echo_0001/sonata.png
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

from datetime import datetime
from pathlib import Path

_CELLS_PER_PAGE = 24

session_id = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
out_dir    = Path(OUTPUT_DIR) / session_id

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

# ── Sonata-icon pass ──────────────────────────────────────────────────────────
# Mirror the scroll-reset pattern from scan-echoes.py: position to the first
# item and toggle the sonata scroll twice in each direction so the UI is in a
# known state before the loop begins.

goto_index(0, scroll_wait=1.0)
sonata_down()
sonata_down()
sonata_up()
sonata_up()

for idx in range(TOTAL):
    goto_index(idx, click_wait=0.1)

    item_dir = out_dir / f'echo_{idx:04d}'
    item_dir.mkdir(parents=True, exist_ok=True)

    # Scroll the detail panel so the sonata section is inside the ROI, then
    # capture.  The goto_index at the end resets the panel scroll for the next
    # item (avoids cumulative drift across a long list).
    move(1600, 500)
    scroll(-500.0, wait=1.0)
    scroll(5.0, wait=0.5)
    screenshot(roi='sonata', out=item_dir / 'sonata.png')
    goto_index(idx, click_wait=0.0)

    pct = (idx + 1) / TOTAL * 100
    print(f'\r  {idx + 1}/{TOTAL} ({pct:.0f}%)', end='', flush=True)

print()  # newline after progress

close_inventory()

print(f'\nDone.')
print(f'  Captures  : {out_dir}')
print(f'  Next step : python tools/update_sonata_templates/main.py update --crop-dir {out_dir}')
