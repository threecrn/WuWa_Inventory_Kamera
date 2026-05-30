"""
wuwa_inventory_kamera.updater.assets
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Core asset downloader — no GUI/Qt dependencies.

Downloads catalog-driven game UI icons into ``assets/<image path>`` from the
``Wuthering-Waves-GameAssets`` repository and keeps the existing ``assets/IconS``
sonata icons synced from the Wuthering Waves fandom wiki.

The Qt-dependent ``AssetsUpdater`` (with Signal support) remains in
``updater/assetsUpdater.py`` and subclasses this.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
import time
import urllib.parse
import urllib.request
import logging
from pathlib import Path, PurePosixPath

from .. import localization_data as _localization_data
from ..config.app_config import basePATH

logger = logging.getLogger('AssetsUpdater')

_GAME_ASSET_REPO_OWNER = '555me'
_GAME_ASSET_REPO_NAME = 'Wuthering-Waves-GameAssets'
_GAME_ASSET_REPO_REF = 'main'
_GAME_ASSET_REPO_IMAGE_ROOT = 'UI/UIResources/Common/Image'
_GAME_ASSET_RAW_ROOT = (
    f'https://raw.githubusercontent.com/{_GAME_ASSET_REPO_OWNER}/'
    f'{_GAME_ASSET_REPO_NAME}/{_GAME_ASSET_REPO_REF}/'
    f'{_GAME_ASSET_REPO_IMAGE_ROOT}'
)

# ---------------------------------------------------------------------------
# Remote sources
# ---------------------------------------------------------------------------

_FANDOM_API = 'https://wutheringwaves.fandom.com/api.php'

_REQUEST_HEADERS = {
    'User-Agent': (
        'WuWaInventoryKamera/1.0 (asset updater; fair use; '
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


def _normalize_asset_path(raw_path: object) -> str | None:
    """Return a safe relative asset path or ``None`` when invalid."""
    if not isinstance(raw_path, str):
        return None

    candidate = raw_path.strip().replace('\\', '/')
    if not candidate:
        return None
    if candidate.startswith('/'):
        return None

    pure_path = PurePosixPath(candidate)
    parts = pure_path.parts
    if not parts:
        return None
    if any(part in ('', '.', '..') for part in parts):
        return None
    if ':' in parts[0]:
        return None
    if pure_path.suffix.lower() != '.png':
        return None

    return pure_path.as_posix()


def _load_game_asset_manifest(data_dir: Path) -> tuple[str, ...]:
    """Return the deduplicated set of catalog-driven game icon paths."""
    manifest: set[str] = set()
    for filename in ('items.json', 'weapons.json'):
        payload = _localization_data.load_json_file(data_dir / 'catalog' / filename)
        if not isinstance(payload, dict):
            continue
        for info in payload.values():
            if not isinstance(info, dict):
                continue
            normalized = _normalize_asset_path(info.get('image'))
            if normalized:
                manifest.add(normalized)

    return tuple(sorted(manifest))


def _build_game_asset_repo_path(image_path: str) -> str:
    normalized = _normalize_asset_path(image_path)
    if normalized is None:
        raise ValueError(f'Invalid game asset path: {image_path!r}')
    return f'{_GAME_ASSET_REPO_IMAGE_ROOT}/{normalized}'


def _build_game_asset_download_url(image_path: str) -> str:
    normalized = _normalize_asset_path(image_path)
    if normalized is None:
        raise ValueError(f'Invalid game asset path: {image_path!r}')
    return f'{_GAME_ASSET_RAW_ROOT}/{urllib.parse.quote(normalized, safe="/")}'


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
    raw = _localization_data.load_sonata_id_map(data_root=data_dir, strict=True)
    return {_normalize(key) for key in raw}


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


def _download_binary(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers=_REQUEST_HEADERS)
    with urllib.request.urlopen(req, timeout=60) as resp:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.read())


@dataclass(frozen=True)
class AssetFamilyStatus:
    family: str
    total: int
    existing: int
    missing: int


@dataclass(frozen=True)
class _AssetDownload:
    family: str
    label: str
    url: str
    dest: Path
    delay_seconds: float = 0.0


class _AssetFamily:
    name = 'assets'

    def prepare_downloads(self, *, data_dir: Path, assets_dir: Path) -> tuple[_AssetDownload, ...]:
        raise NotImplementedError


class _GameIconsAssetFamily(_AssetFamily):
    name = 'game-icons'

    def prepare_downloads(self, *, data_dir: Path, assets_dir: Path) -> tuple[_AssetDownload, ...]:
        manifest = _load_game_asset_manifest(data_dir)
        logger.info('Loaded %d game asset paths from catalogs', len(manifest))
        return tuple(
            _AssetDownload(
                family=self.name,
                label=image_path,
                url=_build_game_asset_download_url(image_path),
                dest=assets_dir / Path(image_path),
                delay_seconds=0.1,
            )
            for image_path in manifest
        )


class _SonataIconsAssetFamily(_AssetFamily):
    name = 'sonata-icons'

    def prepare_downloads(self, *, data_dir: Path, assets_dir: Path) -> tuple[_AssetDownload, ...]:
        sonata_keys = _load_sonata_keys(data_dir)
        logger.info('Loaded %d sonata keys', len(sonata_keys))

        mapping = _build_icon_mapping(sonata_keys)
        logger.info('Matched %d / %d sonata icons', len(mapping), len(sonata_keys))
        unmatched = sonata_keys - set(mapping)
        if unmatched:
            logger.warning('No wiki icon found for: %s', ', '.join(sorted(unmatched)))

        output_dir = assets_dir / 'IconS'
        return tuple(
            _AssetDownload(
                family=self.name,
                label=f'IconS/{key}.png',
                url=url,
                dest=output_dir / f'{key}.png',
                delay_seconds=0.3,
            )
            for key, url in sorted(mapping.items())
        )


class _ProgressTracker:
    def __init__(self, updater: BaseAssetsUpdater, total: int) -> None:
        self._updater = updater
        self._total = total
        self._completed = 0

    def advance(self, label: str) -> None:
        if self._total <= 0:
            return
        self._completed += 1
        self._updater._onProgress(label, self._completed / self._total * 100)


# ---------------------------------------------------------------------------
# BaseAssetsUpdater
# ---------------------------------------------------------------------------

class BaseAssetsUpdater:
    """Qt-free asset downloader.

    Downloads catalog-driven game icons into ``assets/`` and keeps
    ``assets/IconS/`` synced from the Wuthering Waves fandom wiki. Sync work is
    split into explicit asset families so sonata support remains isolated while
    preserving the caller-visible ``IconS`` contract. Subclass and override
    :meth:`_onProgress` / :meth:`_onFinished` for lifecycle notifications
    (e.g. Qt signals).
    """

    force: bool = False

    def __init__(self, *, force: bool = False) -> None:
        self.force = force

    def plan_downloads(self) -> tuple[_AssetDownload, ...]:
        data_dir = basePATH / 'data'
        assets_dir = basePATH / 'assets'
        downloads: list[_AssetDownload] = []
        for family in self._iter_asset_families():
            try:
                downloads.extend(family.prepare_downloads(data_dir=data_dir, assets_dir=assets_dir))
            except Exception as exc:
                logger.error('Failed to prepare %s asset downloads: %s', family.name, exc)
        return tuple(downloads)

    def collect_status(self) -> tuple[AssetFamilyStatus, ...]:
        buckets: dict[str, dict[str, int]] = {}
        for download in self.plan_downloads():
            bucket = buckets.setdefault(download.family, {'total': 0, 'existing': 0})
            bucket['total'] += 1
            if download.dest.exists():
                bucket['existing'] += 1

        return tuple(
            AssetFamilyStatus(
                family=family,
                total=counts['total'],
                existing=counts['existing'],
                missing=counts['total'] - counts['existing'],
            )
            for family, counts in buckets.items()
        )

    def run(self) -> None:
        assets_dir = basePATH / 'assets'
        assets_dir.mkdir(parents=True, exist_ok=True)

        downloads = self.plan_downloads()
        progress = _ProgressTracker(self, len(downloads))
        self._sync_downloads(downloads, progress)

        self._onFinished()

    def _iter_asset_families(self) -> tuple[_AssetFamily, ...]:
        return (
            _GameIconsAssetFamily(),
            _SonataIconsAssetFamily(),
        )

    def _sync_downloads(
        self,
        downloads: list[_AssetDownload] | tuple[_AssetDownload, ...],
        progress: _ProgressTracker,
    ) -> None:
        for download in downloads:
            progress_label = self._progress_label(download)
            if download.dest.exists() and not self.force:
                logger.debug('Skip (already exists): %s', progress_label)
                progress.advance(progress_label)
                continue

            action = 'Refreshing' if self.force and download.dest.exists() else 'Downloading'
            logger.info('%s %s', action, progress_label)
            try:
                _download_binary(download.url, download.dest)
            except Exception as exc:
                logger.error('Failed to download %s: %s', download.url, exc)
            progress.advance(progress_label)
            if download.delay_seconds > 0:
                time.sleep(download.delay_seconds)

    @staticmethod
    def _progress_label(download: _AssetDownload) -> str:
        return f'{download.family}: {download.label}'

    def _onProgress(self, file_name: str, percent: float) -> None:
        """Override to receive download progress."""

    def _onFinished(self) -> None:
        """Override or connect to be notified on completion."""
