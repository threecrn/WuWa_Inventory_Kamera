"""
wuwa_inventory_kamera.updater.assets
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Core asset downloader — no GUI/Qt dependencies.

Downloads ``assets/IconS/`` (sonata-set icons) from the Wuthering Waves
fandom wiki (fair use) using the MediaWiki ``allimages`` API.

The Qt-dependent ``AssetsUpdater`` (with Signal support) remains in
``updater/assetsUpdater.py`` and subclasses this.
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
import logging
from pathlib import Path

from ..config.app_config import basePATH

logger = logging.getLogger('AssetsUpdater')

# ---------------------------------------------------------------------------
# Fandom wiki API
# ---------------------------------------------------------------------------

_FANDOM_API = 'https://wutheringwaves.fandom.com/api.php'

_REQUEST_HEADERS = {
    'User-Agent': (
        'WuWaInventoryKamera/1.0 (sonata-icon updater; fair use; '
        'https://github.com/Psycho-Marcus/WuWa_Inventory_Kamera)'
    ),
}

# Wiki filenames whose normalised stem doesn't match the game's sonata key.
# Maps normalize(wiki_stem) → sonata key.
_WIKI_NAME_OVERRIDES: dict[str, str] = {
    # Wiki: "Sun-sinking Eclipse"  ↔  Game: "Havoc Eclipse"
    'sunsinkingeclipse': 'havoceclipse',
}


def _normalize(name: str) -> str:
    """Lowercase and strip underscores, spaces, hyphens, and apostrophes."""
    return re.sub(r"[_\s\-']", '', name).lower()


def _api_get(params: dict) -> dict:
    params['format'] = 'json'
    url = _FANDOM_API + '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=_REQUEST_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _iter_icon_files():
    """Yield ``{name, url}`` dicts for every ``Icon_*.png`` on the wiki."""
    params: dict = {
        'action': 'query',
        'list': 'allimages',
        'aiprefix': 'Icon_',
        'ailimit': '500',
        'aiprop': 'url',
    }
    while True:
        data = _api_get(params)
        for entry in data.get('query', {}).get('allimages', []):
            yield entry
        cont = data.get('continue')
        if not cont:
            break
        params.update(cont)
        time.sleep(0.4)


def _load_sonata_keys(data_dir: Path) -> set[str]:
    path = data_dir / 'en' / 'sonataName.json'
    with path.open(encoding='utf-8') as fh:
        raw: dict[str, int] = json.load(fh)
    return {_normalize(k) for k in raw}


def _build_icon_mapping(sonata_keys: set[str]) -> dict[str, str]:
    """Return ``{normalized_sonata_key: cdn_url}`` for matched wiki icons."""
    mapping: dict[str, str] = {}
    logger.info('Fetching Icon_*.png list from Fandom API …')
    for entry in _iter_icon_files():
        filename: str = entry.get('name', '')
        if not filename.lower().endswith('.png'):
            continue
        stem = filename
        if stem.lower().startswith('icon_'):
            stem = stem[5:]
        stem = Path(stem).stem
        key = _normalize(stem)
        key = _WIKI_NAME_OVERRIDES.get(key, key)
        if key in sonata_keys:
            mapping[key] = entry['url']
    return mapping


# ---------------------------------------------------------------------------
# BaseAssetsUpdater
# ---------------------------------------------------------------------------

class BaseAssetsUpdater:
    """Qt-free asset downloader.

    Downloads ``assets/IconS/`` from the Wuthering Waves fandom wiki.
    Subclass and override :meth:`_onProgress` / :meth:`_onFinished` for
    lifecycle notifications (e.g. Qt signals).
    """

    def run(self) -> None:
        output_dir: Path = basePATH / 'assets' / 'IconS'
        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            sonata_keys = _load_sonata_keys(basePATH / 'data')
        except Exception as exc:
            logger.error('Failed to load sonata keys: %s', exc)
            self._onFinished()
            return

        logger.info('Loaded %d sonata keys', len(sonata_keys))

        try:
            mapping = _build_icon_mapping(sonata_keys)
        except Exception as exc:
            logger.error('Failed to fetch icon list from wiki: %s', exc)
            self._onFinished()
            return

        logger.info('Matched %d / %d sonata icons', len(mapping), len(sonata_keys))
        unmatched = sonata_keys - set(mapping)
        if unmatched:
            logger.warning('No wiki icon found for: %s', ', '.join(sorted(unmatched)))

        total = len(mapping)
        for idx, (key, url) in enumerate(sorted(mapping.items()), start=1):
            dest = output_dir / f'{key}.png'
            if dest.exists():
                logger.debug('Skip (already exists): %s', dest.name)
                continue
            label = f'IconS/{dest.name}'
            logger.info('Downloading %s', label)
            try:
                req = urllib.request.Request(url, headers=_REQUEST_HEADERS)
                with urllib.request.urlopen(req, timeout=60) as resp:
                    dest.write_bytes(resp.read())
                self._onProgress(label, idx / total * 100)
            except Exception as exc:
                logger.error('Failed to download %s: %s', url, exc)
            time.sleep(0.3)

        self._onFinished()

    def _onProgress(self, file_name: str, percent: float) -> None:
        """Override to receive download progress."""

    def _onFinished(self) -> None:
        """Override or connect to be notified on completion."""
