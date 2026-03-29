"""
tools/update_sonata_templates/main.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

One-stop tool for updating sonata icon assets and detection templates
after a new game version adds new sonata sets.

Workflow
--------
1. Load all known sonata keys from ``data/en/sonataName.json``.
2. Audit which wiki icons (``assets/IconS/*.png``) and which detection
   templates (``assets/IconS/templates/*.png``) already exist.
3. Download any missing wiki icons from the Fandom wiki API.
4. Optionally rebuild median detection templates from labeled raw
   screenshots (``--raw-dir``).
5. Print a summary: new downloads, new templates, and anything still
   missing.

Usage examples
--------------
Show current status only (no downloads, no rebuild)::

    python tools/update_sonata_templates/main.py status

Fetch missing wiki icons and show status::

    python tools/update_sonata_templates/main.py update

Fetch icons *and* rebuild templates from labeled echo screenshots::

    python tools/update_sonata_templates/main.py update \\
        --raw-dir K:/wuwa/export/current/raw

Force re-download of all wiki icons (even if they exist)::

    python tools/update_sonata_templates/main.py update --force
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
ICONS_DIR = REPO_ROOT / "assets" / "IconS"
TEMPLATES_DIR = ICONS_DIR / "templates"
SONATA_JSON = DATA_DIR / "en" / "sonataName.json"

# Reference icon crop coordinates at 1920 × 1080.
_REF_WIDTH = 1920
_REF_HEIGHT = 1080
_ICON_X1 = 1442
_ICON_Y1 = 316
_ICON_X2 = 1465
_ICON_Y2 = 340

API_URL = "https://wutheringwaves.fandom.com/api.php"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REQUEST_HEADERS = {
    "User-Agent": "WuWaInventoryKamera/1.0 (sonata-icon updater; fair use; "
    "https://github.com/Wuper/WuWa_Inventory_Kamera)",
}


def normalize(name: str) -> str:
    """Lowercase and remove underscores, spaces, and apostrophes."""
    return re.sub(r"[_\s']", "", name).lower()


def load_sonata_keys() -> dict[str, int]:
    """Return ``{normalized_key: id}`` from ``sonataName.json``."""
    with SONATA_JSON.open(encoding="utf-8") as f:
        raw: dict[str, int] = json.load(f)
    return {normalize(k): v for k, v in raw.items()}


def existing_stems(directory: Path) -> set[str]:
    """Return the set of PNG stems in *directory*."""
    if not directory.is_dir():
        return set()
    return {p.stem for p in directory.glob("*.png")}


# ---------------------------------------------------------------------------
# MediaWiki API — icon scraping
# ---------------------------------------------------------------------------


def _api_get(params: dict) -> dict:
    params["format"] = "json"
    url = API_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=_REQUEST_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _iter_icon_files():
    """Yield ``{name, url}`` for every ``Icon_*.png`` on the wiki."""
    params: dict = {
        "action": "query",
        "list": "allimages",
        "aiprefix": "Icon_",
        "ailimit": "500",
        "aiprop": "url",
    }
    while True:
        data = _api_get(params)
        for entry in data.get("query", {}).get("allimages", []):
            yield entry
        cont = data.get("continue")
        if not cont:
            break
        params.update(cont)
        time.sleep(0.4)


def _build_url_mapping(sonata_keys: set[str]) -> dict[str, str]:
    """Return ``{normalized_key: cdn_url}`` for wiki icons matching known sonatas."""
    mapping: dict[str, str] = {}
    log.info("Querying Fandom API for Icon_*.png files …")
    for entry in _iter_icon_files():
        filename: str = entry.get("name", "")
        if not filename.lower().endswith(".png"):
            continue
        stem = filename
        if stem.lower().startswith("icon_"):
            stem = stem[5:]
        stem = Path(stem).stem
        key = normalize(stem)
        if key in sonata_keys:
            mapping[key] = entry["url"]
    return mapping


def _download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers=_REQUEST_HEADERS)
    with urllib.request.urlopen(req, timeout=60) as resp:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.read())


def download_missing_icons(
    sonata_keys: set[str],
    *,
    force: bool = False,
) -> tuple[list[str], list[str]]:
    """
    Download wiki icons for every key in *sonata_keys* that is not already
    present in ``assets/IconS/``.

    Returns ``(downloaded, failed)`` lists of sonata keys.
    """
    have = existing_stems(ICONS_DIR)
    need = sonata_keys if force else sonata_keys - have
    if not need:
        log.info("All wiki icons are already present.")
        return [], []

    log.info("%d wiki icon(s) to fetch.", len(need))
    url_map = _build_url_mapping(need)

    downloaded: list[str] = []
    failed: list[str] = []

    for key in sorted(need):
        url = url_map.get(key)
        if url is None:
            log.warning("No wiki icon found for '%s'", key)
            failed.append(key)
            continue
        dest = ICONS_DIR / f"{key}.png"
        log.info("Downloading %s → %s", url.split("/")[-1], dest.name)
        try:
            _download(url, dest)
            downloaded.append(key)
        except Exception as exc:  # noqa: BLE001
            log.error("Download failed for %s: %s", key, exc)
            failed.append(key)
        time.sleep(0.3)

    return downloaded, failed


# ---------------------------------------------------------------------------
# Template building (from labeled raw screenshots)
# ---------------------------------------------------------------------------


def _icon_roi(width: int, height: int) -> tuple[int, int, int, int]:
    """Return (y1, y2, x1, x2) scaled to the given resolution."""
    sx = width / _REF_WIDTH
    sy = height / _REF_HEIGHT
    return (
        round(_ICON_Y1 * sy),
        round(_ICON_Y2 * sy),
        round(_ICON_X1 * sx),
        round(_ICON_X2 * sx),
    )


def rebuild_templates(raw_dir: Path) -> tuple[list[str], int]:
    """
    Build median templates from labeled echo screenshots.

    Each ``echo_NNNN/`` folder should contain ``full.png`` and
    ``debug/ocr.json`` with a ``sonata.matched`` field.

    Returns ``(built_keys, total_samples)``.
    """
    if not raw_dir.is_dir():
        log.error("--raw-dir does not exist: %s", raw_dir)
        sys.exit(1)

    crops_by_sonata: dict[str, list[np.ndarray]] = defaultdict(list)

    for echo_dir in sorted(raw_dir.iterdir()):
        if not echo_dir.is_dir() or not echo_dir.name.startswith("echo_"):
            continue
        full_path = echo_dir / "full.png"
        ocr_path = echo_dir / "debug" / "ocr.json"
        if not full_path.exists() or not ocr_path.exists():
            continue

        with open(ocr_path, encoding="utf-8") as f:
            ocr = json.load(f)
        sonata_data = ocr.get("sonata", {})
        matched = sonata_data.get("matched") if isinstance(sonata_data, dict) else None
        if not matched:
            continue

        full = cv2.imread(str(full_path), cv2.IMREAD_COLOR)
        if full is None:
            continue
        h, w = full.shape[:2]
        y1, y2, x1, x2 = _icon_roi(w, h)
        crop = full[y1:y2, x1:x2]
        key = normalize(matched)
        crops_by_sonata[key].append(crop)

    if not crops_by_sonata:
        log.error("No labeled echo screenshots found in %s", raw_dir)
        return [], 0

    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    total_samples = 0
    built: list[str] = []

    for name, crops in sorted(crops_by_sonata.items()):
        stack = np.stack(crops, axis=0).astype(np.float32)
        median = np.median(stack, axis=0).astype(np.uint8)
        dest = TEMPLATES_DIR / f"{name}.png"
        cv2.imwrite(str(dest), median)
        built.append(name)
        total_samples += len(crops)
        log.info("  %-30s %4d samples → %s", name, len(crops), dest.name)

    log.info(
        "Built %d templates from %d samples → %s",
        len(built), total_samples, TEMPLATES_DIR,
    )
    return built, total_samples


# ---------------------------------------------------------------------------
# Status / audit
# ---------------------------------------------------------------------------


def print_status(sonata_keys: set[str]) -> None:
    """Print a table of every sonata key and whether its assets exist."""
    icons = existing_stems(ICONS_DIR)
    templates = existing_stems(TEMPLATES_DIR)

    header = f"{'Sonata key':<32s}  {'Wiki icon':<12s}  {'Template':<12s}"
    print()
    print(header)
    print("─" * len(header))

    missing_icon: list[str] = []
    missing_tmpl: list[str] = []

    for key in sorted(sonata_keys):
        has_icon = "✓" if key in icons else "✗"
        has_tmpl = "✓" if key in templates else "✗"
        print(f"  {key:<30s}  {has_icon:^12s}  {has_tmpl:^12s}")
        if key not in icons:
            missing_icon.append(key)
        if key not in templates:
            missing_tmpl.append(key)

    print()
    print(f"Sonata keys:     {len(sonata_keys)}")
    print(f"Wiki icons:      {len(icons & sonata_keys)} / {len(sonata_keys)}")
    print(f"Templates:       {len(templates & sonata_keys)} / {len(sonata_keys)}")

    if missing_icon:
        print(f"\nMissing wiki icons:  {', '.join(sorted(missing_icon))}")
    if missing_tmpl:
        print(f"Missing templates:   {', '.join(sorted(missing_tmpl))}")

    # Check for stale assets (files with no corresponding sonata key)
    stale_icons = icons - sonata_keys
    stale_tmpls = templates - sonata_keys
    if stale_icons:
        print(f"\nStale wiki icons (no sonata key): {', '.join(sorted(stale_icons))}")
    if stale_tmpls:
        print(f"Stale templates (no sonata key):  {', '.join(sorted(stale_tmpls))}")

    if not missing_icon and not missing_tmpl and not stale_icons and not stale_tmpls:
        print("\nAll assets are up to date.")


# ---------------------------------------------------------------------------
# Sub-commands
# ---------------------------------------------------------------------------


def cmd_status(_args: argparse.Namespace) -> None:
    sonata_keys = set(load_sonata_keys())
    print_status(sonata_keys)


def cmd_update(args: argparse.Namespace) -> None:
    sonata_keys = set(load_sonata_keys())

    # 1. Download missing wiki icons
    downloaded, failed = download_missing_icons(sonata_keys, force=args.force)
    if downloaded:
        print(f"\nDownloaded {len(downloaded)} wiki icon(s): {', '.join(downloaded)}")
    if failed:
        print(f"Failed to fetch {len(failed)} icon(s): {', '.join(failed)}")

    # 2. Rebuild templates if --raw-dir was given
    if args.raw_dir:
        raw_dir = Path(args.raw_dir)
        print()
        built, total = rebuild_templates(raw_dir)
        if built:
            print(f"Rebuilt {len(built)} template(s) from {total} samples.")
    else:
        templates = existing_stems(TEMPLATES_DIR)
        missing_tmpl = sonata_keys - templates
        if missing_tmpl:
            log.info(
                "Templates missing for %d sonata(s). "
                "Pass --raw-dir to rebuild from labeled screenshots.",
                len(missing_tmpl),
            )

    # 3. Final status
    print_status(sonata_keys)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="update_sonata_templates",
        description="Update sonata wiki icons and detection templates for new game versions.",
    )
    sub = parser.add_subparsers(dest="command")

    # ── status ─────────────────────────────────────────────────────────────
    sub.add_parser(
        "status",
        help="Show which icons and templates exist for every known sonata.",
    )

    # ── update ─────────────────────────────────────────────────────────────
    update_p = sub.add_parser(
        "update",
        help="Download missing wiki icons and optionally rebuild templates.",
    )
    update_p.add_argument(
        "--raw-dir",
        metavar="PATH",
        help="Directory with echo_NNNN/ folders (full.png + debug/ocr.json) "
        "to rebuild templates from.",
    )
    update_p.add_argument(
        "--force",
        action="store_true",
        help="Re-download all wiki icons even if they already exist.",
    )

    args = parser.parse_args()

    if args.command == "status":
        cmd_status(args)
    elif args.command == "update":
        cmd_update(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
