# Navigates the entire echo list, captures each sonata icon into a temporary
# directory, auto-matches it against known wiki icons, and writes the resulting
# median detection templates directly — no permanent icon files are kept.
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
import tempfile
from collections import defaultdict
from pathlib import Path

import cv2

# Bootstrap: load template-building helpers from the tool directory.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / 'tools' / 'update_sonata_templates'))
import main as _ust  # noqa: E402 (late import after path manipulation)

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

# ── Capture and match loop ────────────────────────────────────────────────────

_crops_by_sonata: dict[str, list] = defaultdict(list)
_skipped = 0

# Use a single temp directory for intermediate screenshots (cleaned up at end).
_tmp_dir = Path(tempfile.mkdtemp(prefix='wuwa_sonata_'))
_tmp_png  = _tmp_dir / 'sonata_tmp.png'

# Reset UI scroll state before the loop (matches scan-echoes.py pattern).
goto_index(0, scroll_wait=1.0)
sonata_down()
sonata_down()
sonata_up()
sonata_up()

for idx in range(TOTAL):
    goto_index(idx, click_wait=0.1)

    # Scroll detail panel to sonata section, capture, then reset.
    move(1600, 500)
    scroll(-500.0, wait=1.0)
    scroll(5.0, wait=0.5)
    screenshot(roi='sonata', out=_tmp_png)
    goto_index(idx, click_wait=0.0)

    crop = cv2.imread(str(_tmp_png), cv2.IMREAD_COLOR)
    if crop is None:
        print(f'  echo_{idx:04d}: could not read screenshot — skipping')
        _skipped += 1
        continue

    key, score = _ust.match_sonata_crop(crop, _wiki_icons)
    if score < MIN_MATCH_SCORE:
        print(f'  echo_{idx:04d}: low score {score:.2f} for {key!r} — skipping')
        _skipped += 1
        continue

    _crops_by_sonata[key].append(crop.copy())

    pct = (idx + 1) / TOTAL * 100
    print(f'\r  {idx + 1}/{TOTAL} ({pct:.0f}%)  last: {key} ({score:.2f})', end='', flush=True)

print()  # newline after progress

# Clean up temp files.
_tmp_png.unlink(missing_ok=True)
_tmp_dir.rmdir()

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
