"""
tools/scrape_sonata_icons/main.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Scrapes sonata-set icons from the Wuthering Waves fandom wiki (fair use).

Usage
-----
    python tools/scrape_sonata_icons/main.py [--output OUTPUT_DIR] [--data DATA_DIR]

Algorithm
---------
1. Load every sonata key from ``data/catalog/sonatas.json`` (fallback:
    legacy compatibility file ``data/en/sonataName.json``).
2. Call the MediaWiki ``allimages`` API to list every ``Icon_*.png`` file
   hosted on the wiki, paging through all results.
3. Match each wiki filename to a sonata key by normalising both to lowercase
   with underscores/spaces/apostrophes stripped.
4. Download the direct CDN image URL returned by the API.
5. Save as ``{key}.png`` in the output directory (default: ``assets/IconS/``).

Notes
-----
* Only standard-library modules are used (``urllib``, ``json``, …).
* A short sleep between requests keeps traffic polite.
* Images are skipped if they already exist on disk.
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
from pathlib import Path
from typing import Iterator

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from wuwa_inventory_kamera import localization_data as _localization_data

API_URL = "https://wutheringwaves.fandom.com/api.php"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def normalize(name: str) -> str:
    """Lowercase and remove underscores, spaces, hyphens, and apostrophes."""
    return re.sub(r"[_\s\-']", "", name).lower()


# Wiki filenames that don't normalize to the same key as the game data.
# Maps normalize(wiki_stem) → sonata key.
_WIKI_NAME_OVERRIDES: dict[str, str] = {
    # Wiki: "Sun-sinking Eclipse"  ↔  Game: "Havoc Eclipse"
    "sunsinkingeclipse": "havoceclipse",
}


def load_sonata_keys(data_dir: Path) -> set[str]:
    raw = _localization_data.load_sonata_id_map(data_root=data_dir, strict=True)
    return {normalize(key) for key in raw if isinstance(key, str)}


# ---------------------------------------------------------------------------
# MediaWiki API
# ---------------------------------------------------------------------------

_REQUEST_HEADERS = {
    "User-Agent": "WuWaInventoryKamera/1.0 (sonata-icon scraper; fair use; "
    "https://github.com/Wuper/WuWa_Inventory_Kamera)",
}


def _api_get(params: dict) -> dict:
    params["format"] = "json"
    url = API_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=_REQUEST_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def iter_icon_files() -> Iterator[dict]:
    """
    Yield every ``{name, url}`` dict for files whose name starts with
    ``Icon_`` on the wiki, paging automatically.
    """
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


def build_mapping(sonata_keys: set[str]) -> dict[str, str]:
    """
    Return ``{normalized_sonata_key: cdn_image_url}`` for every icon that
    matches a known sonata.

    Wiki filenames look like ``Icon_Trailblazing_Star.png``; sonata keys look
    like ``trailblazingstar``.  Both are normalised before comparison.
    """
    mapping: dict[str, str] = {}
    log.info("Fetching Icon_*.png file list from Fandom API …")

    for entry in iter_icon_files():
        filename: str = entry.get("name", "")
        if not filename.lower().endswith(".png"):
            continue

        # Strip leading "Icon_" and trailing ".png"
        stem = filename
        if stem.lower().startswith("icon_"):
            stem = stem[5:]
        stem = Path(stem).stem  # remove extension

        key = normalize(stem)
        if key in _WIKI_NAME_OVERRIDES:
            key = _WIKI_NAME_OVERRIDES[key]
        if key in sonata_keys:
            mapping[key] = entry["url"]

    return mapping


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


def download_image(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers=_REQUEST_HEADERS)
    with urllib.request.urlopen(req, timeout=60) as resp:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.read())


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download sonata icons from the Wuthering Waves fandom wiki."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "assets" / "IconS",
        metavar="DIR",
        help="Directory to save icons (default: assets/IconS/)",
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=REPO_ROOT / "data",
        metavar="DIR",
        help="Data directory containing catalog/sonatas.json (default: data/)",
    )
    args = parser.parse_args()

    sonata_keys = load_sonata_keys(args.data)
    log.info("Loaded %d sonata keys from generated data", len(sonata_keys))

    mapping = build_mapping(sonata_keys)
    log.info("Matched %d / %d sonata icons", len(mapping), len(sonata_keys))

    unmatched = sonata_keys - set(mapping)
    if unmatched:
        log.warning("No wiki icon found for: %s", ", ".join(sorted(unmatched)))

    args.output.mkdir(parents=True, exist_ok=True)

    for key, url in sorted(mapping.items()):
        dest = args.output / f"{key}.png"
        if dest.exists():
            log.info("Skip (already exists): %s", dest.name)
            continue
        log.info("Downloading %-40s → %s", url.split("/")[-1], dest.name)
        try:
            download_image(url, dest)
        except Exception as exc:  # noqa: BLE001
            log.error("Failed to download %s: %s", url, exc)
        time.sleep(0.3)  # polite crawl rate

    log.info("Done — icons saved to %s", args.output)


if __name__ == "__main__":
    main()
