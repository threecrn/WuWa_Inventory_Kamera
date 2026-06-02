# Navigates the entire echo list, captures each header sonata icon,
# auto-matches it against known wiki icons, and writes the resulting median
# detection templates directly — no temporary on-disk icon crops are kept.
#
# Requires wiki icons to already be present in assets/IconS/.  If any are
# missing, run first:
#   python tools/update_sonata_templates/main.py update
#
# Usage:
#   wuwa-nav nav-scripts/build-sonata-templates.py

# ── CONFIG — edit before running ─────────────────────────────────────────────

SORT_ORDER      = 'level'   # sort order to apply before scanning; None = keep current
TOTAL           = 24        # TODO: replace with read_count() once available
MIN_MATCH_SCORE = 0.50      # discard crops whose auto-match confidence is below this

# ── Script body — no changes needed below this line ──────────────────────────

import sys
from collections import defaultdict
from pathlib import Path

# Bootstrap: load template-building helpers from the tool directory.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / 'src'))
sys.path.insert(0, str(_REPO_ROOT / 'tools' / 'update_sonata_templates'))
import main as _ust  # noqa: E402 (late import after path manipulation)

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

# ── Pre-flight: ensure wiki icons are available ───────────────────────────────

_wiki_icons = _ust.load_wiki_icons()
if not _wiki_icons:
    print('ERROR: No wiki icons found in assets/IconS/.')
    print('  Run: python tools/update_sonata_templates/main.py update')
    raise SystemExit(1)

print(f'build-sonata-templates')
print(f'  wiki icons loaded : {len(_wiki_icons)}')
print(f'  min match score   : {MIN_MATCH_SCORE}')
print(f'  sort              : {SORT_ORDER or "(unchanged)"}')
print(f'  total             : {TOTAL}')

# ── Navigation ────────────────────────────────────────────────────────────────

focus_window()
open_inventory()
switch_tab('echoes')

if SORT_ORDER:
    set_sort(SORT_ORDER)

snap = snapshot()
layout = ScreenInfo(snap.window.width, snap.window.height, snap.window.monitor)
echoes_info = layout.echoes
backend = get_backend('rapidocr')
print(f'  screen            : {snap.window.width}x{snap.window.height}')

# ── Capture and match loop ────────────────────────────────────────────────────

_crops_by_sonata: dict[str, list] = defaultdict(list)
_skipped = 0

for idx in range(TOTAL):
    goto_index(idx, click_wait=0.1)

    full = screenshot(roi='full', as_image=True)
    if full is None:
        print(f'  echo_{idx:04d}: dry-run capture — skipping')
        _skipped += 1
        continue

    crop = _capture_sonata_icon(full, echoes_info, backend)

    key, score = _ust.match_sonata_crop(crop, _wiki_icons)
    if score < MIN_MATCH_SCORE:
        print(f'  echo_{idx:04d}: low score {score:.2f} for {key!r} — skipping')
        _skipped += 1
        continue

    _crops_by_sonata[key].append(crop.copy())

    pct = (idx + 1) / TOTAL * 100
    print(f'\r  {idx + 1}/{TOTAL} ({pct:.0f}%)  last: {key} ({score:.2f})', end='', flush=True)

print()  # newline after progress

close_inventory()

# ── Build and write templates ─────────────────────────────────────────────────

if not _crops_by_sonata:
    print('\nNo crops collected — nothing to write.')
    raise SystemExit(1)

print(f'\nCollected crops: { {k: len(v) for k, v in sorted(_crops_by_sonata.items())} }')
if _skipped:
    print(f'Skipped         : {_skipped} echo(es)')

built, total_samples = _ust.write_templates(_crops_by_sonata)
print(f'\nBuilt {len(built)} template(s) from {total_samples} samples.')
print(f'Templates written to: {_ust.TEMPLATES_DIR}')
