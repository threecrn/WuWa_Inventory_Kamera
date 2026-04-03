# Builds sonata detection templates by applying the in-game sonata filter for
# each known sonata type, then capturing the sonata icon from every visible
# echo.  Because the filter guarantees every echo on screen belongs to the
# same sonata, no per-crop OCR matching is needed at all.
#
# Requires wiki icons to already be present in assets/IconS/.  If any are
# missing, run first:
#   python tools/update_sonata_templates/main.py update
#
# Usage:
#   wuwa-nav nav-scripts/build-sonata-templates-from-filter.py

# ── CONFIG — edit before running ─────────────────────────────────────────────

SORT_ORDER      = 'level'   # sort order to apply before scanning; None = keep current
# Slugs to process.  None → all slugs in sonataName.json (sorted by ID).
SLUGS: list[str] | None = None

# ── Script body — no changes needed below this line ──────────────────────────

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

_arg_parser = argparse.ArgumentParser(prog=Path(__file__).name, add_help=False)
_arg_parser.add_argument(
    '--lang', default='en', metavar='LANG',
    help='Language directory for sonataName.json (default: en).',
)
_script_args, _ = _arg_parser.parse_known_args()

# Bootstrap: load template-building helpers from the tool directory.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_LANG = _script_args.lang
sys.path.insert(0, str(_REPO_ROOT / 'tools' / 'update_sonata_templates'))
import main as _ust  # noqa: E402 (late import after path manipulation)

# ── Load sonata names ─────────────────────────────────────────────────────────

_sonata_json = _REPO_ROOT / 'data' / _LANG / 'sonataName.json'
_sonata_dict: dict[str, int] = json.loads(_sonata_json.read_text(encoding='utf-8'))
# Process sonatas in ascending ID order so progress is predictable.
_all_slugs: list[str] = [
    slug for slug, _ in sorted(_sonata_dict.items(), key=lambda kv: kv[1])
]
_target_slugs: list[str] = SLUGS if SLUGS is not None else _all_slugs

# ── Pre-flight: ensure wiki icons are available ───────────────────────────────

_wiki_icons = _ust.load_wiki_icons()
if not _wiki_icons:
    print('ERROR: No wiki icons found in assets/IconS/.')
    print('  Run: python tools/update_sonata_templates/main.py update')
    raise SystemExit(1)

print('build-sonata-templates-from-filter')
print(f'  wiki icons loaded : {len(_wiki_icons)}')
print(f'  language          : {_LANG}')
print(f'  sort              : {SORT_ORDER or "(unchanged)"}')
print(f'  sonatas to scan   : {len(_target_slugs)}')

# ── Navigation — initial setup ────────────────────────────────────────────────

focus_window(); wait(0.2) # wait after focus to ensure the game registers it before we start sending commands
open_inventory()
switch_tab('echoes')

#if SORT_ORDER:
#    set_sort(SORT_ORDER)

# ── Capture loop ──────────────────────────────────────────────────────────────

_crops_by_sonata: dict[str, list] = defaultdict(list)
_skipped_sonatas: list[str] = []

for _slug_idx, _slug in enumerate(_target_slugs):
    print(f'\n[{_slug_idx + 1}/{len(_target_slugs)}] {_slug}', flush=True)

    # Apply the filter — all visible echoes now belong to this sonata.
    _count = set_sonata_filter(_slug)
    wait(0.5)  # let the grid refresh

    if _count is None:
        # Filter was already active; read_count won't help (shows global total).
        # Re-apply to get the count: clear then set again.
        set_sonata_filter(None)
        wait(0.3)
        _count = set_sonata_filter(_slug)
        wait(0.5)

    _total = _count or 0

    if _total == 0:
        print(f'  no echoes found for {_slug!r} — skipping')
        _skipped_sonatas.append(_slug)
        continue

    print(f'  echoes: {_total}', flush=True)

    # Reset scroll and pre-load sonata panel state (mirrors build-sonata-templates.py).
    goto_index(0, scroll_wait=1.0)

    _collected = 0
    for _idx in range(_total):
        goto_index(_idx, click_wait=0.1)

        _crop = screenshot(roi='echoes.sonataIcon', as_image=True)

        if _crop is None:
            print(f'  echo_{_idx:04d}: could not read screenshot — skipping')
            continue

        _crops_by_sonata[_slug].append(_crop.copy())
        _collected += 1

        _pct = (_idx + 1) / _total * 100
        print(f'\r  {_idx + 1}/{_total} ({_pct:.0f}%)', end='', flush=True)

    print(f'  → {_collected} crop(s) collected')

# ── Teardown ──────────────────────────────────────────────────────────────────

# Clear the filter before closing so the inventory is left in a clean state.
set_sonata_filter(None)

close_inventory()

# ── Build and write templates ─────────────────────────────────────────────────

if not _crops_by_sonata:
    print('\nNo crops collected — nothing to write.')
    raise SystemExit(1)

print(f'\nCollected crops: { {k: len(v) for k, v in sorted(_crops_by_sonata.items())} }')
if _skipped_sonatas:
    print(f'Skipped sonatas : {_skipped_sonatas}')

_built, _total_samples = _ust.write_templates(_crops_by_sonata)
print(f'\nBuilt {len(_built)} template(s) from {_total_samples} samples.')
print(f'Templates written to: {_ust.TEMPLATES_DIR}')
