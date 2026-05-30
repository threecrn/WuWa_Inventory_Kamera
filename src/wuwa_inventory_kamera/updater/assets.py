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
from functools import lru_cache
import hashlib
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
_GAME_ASSET_UI_ROOT = 'UI/UIResources'
_GAME_ASSET_REPO_IMAGE_ROOT = 'UI/UIResources/Common/Image'
_GAME_ASSET_COMMITS_API = 'https://api.github.com/repos/{owner}/{repo}/commits/{ref}'
_GAME_ASSET_RAW_ROOT = (
    f'https://raw.githubusercontent.com/{_GAME_ASSET_REPO_OWNER}/'
    f'{_GAME_ASSET_REPO_NAME}/{_GAME_ASSET_REPO_REF}/'
    f'{_GAME_ASSET_UI_ROOT}'
)
_GAME_ICON_RUNTIME_CATALOGS = ('items.json', 'weapons.json', 'characters.json')
_ASSET_STATE_FILENAME = '.asset_state.json'

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
    """Return the deduplicated runtime image paths the app can render."""
    manifest: set[str] = set()
    for filename in _GAME_ICON_RUNTIME_CATALOGS:
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
    return f'Common/Image/{normalized}'


def _build_game_asset_download_url_from_repo_path(repo_path: str) -> str:
    normalized = _normalize_asset_path(repo_path)
    if normalized is None:
        raise ValueError(f'Invalid game asset repo path: {repo_path!r}')
    return f'{_GAME_ASSET_RAW_ROOT}/{urllib.parse.quote(normalized, safe="/")}'


def _build_game_asset_download_url(image_path: str) -> str:
    return _build_game_asset_download_url_from_repo_path(_build_game_asset_repo_path(image_path))


def _build_git_commit_api_url(owner: str, repo: str, ref: str) -> str:
    return _GAME_ASSET_COMMITS_API.format(
        owner=urllib.parse.quote(owner, safe=''),
        repo=urllib.parse.quote(repo, safe=''),
        ref=urllib.parse.quote(ref, safe=''),
    )


def _fetch_github_commit_sha(owner: str, repo: str, ref: str) -> str:
    url = _build_git_commit_api_url(owner, repo, ref)
    req = urllib.request.Request(url, headers=_REQUEST_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode('utf-8'))
    sha = payload.get('sha') if isinstance(payload, dict) else None
    if not isinstance(sha, str) or not sha:
        raise ValueError(f'Unexpected commit payload from {url}')
    return sha


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


def _find_raw_asset_source_file(data_dir: Path, filename: str) -> Path | None:
    raw_root = data_dir / 'raw'
    preferred = raw_root / 'en' / filename
    if preferred.is_file():
        return preferred
    for candidate in sorted(raw_root.glob(f'*/{filename}')):
        if candidate.is_file():
            return candidate
    return None


def _extract_game_asset_source_paths(unreal_icon_path: object) -> tuple[str, str] | None:
    if not isinstance(unreal_icon_path, str):
        return None
    if '/UI/UIResources/' not in unreal_icon_path:
        return None
    repo_tail = unreal_icon_path.split('/UI/UIResources/', 1)[1].rsplit('.', 1)[0] + '.png'
    if '/Image/' not in repo_tail:
        return None
    local_tail = repo_tail.split('/Image/', 1)[1]
    normalized_local = _normalize_asset_path(local_tail)
    normalized_repo = _normalize_asset_path(repo_tail)
    if normalized_local is None or normalized_repo is None:
        return None
    return normalized_local, normalized_repo


def _load_game_asset_source_map(data_dir: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for filename in ('ItemInfo.json', 'WeaponConf.json'):
        raw_path = _find_raw_asset_source_file(data_dir, filename)
        if raw_path is None:
            continue
        payload = _localization_data.load_json_file(raw_path)
        if not isinstance(payload, list):
            continue
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            extracted = _extract_game_asset_source_paths(entry.get('Icon'))
            if extracted is None:
                continue
            local_path, repo_path = extracted
            mapping.setdefault(local_path, repo_path)
    return mapping


@lru_cache(maxsize=4)
def _load_game_asset_source_map_cached(data_dir_key: str) -> dict[str, str]:
    return _load_game_asset_source_map(Path(data_dir_key))


def _get_game_asset_source_map(data_dir: Path) -> dict[str, str]:
    return _load_game_asset_source_map_cached(str(data_dir))


def ensure_game_asset_cached(
    image_path: str,
    *,
    data_dir: Path | None = None,
    assets_dir: Path | None = None,
) -> Path:
    """Ensure one game icon exists in the local cache and return its path."""
    normalized = _normalize_asset_path(image_path)
    if normalized is None:
        raise ValueError(f'Invalid game asset path: {image_path!r}')

    resolved_assets_dir = assets_dir if assets_dir is not None else basePATH / 'assets'
    dest = resolved_assets_dir / Path(normalized)
    if dest.is_file():
        return dest

    resolved_data_dir = data_dir if data_dir is not None else basePATH / 'data'
    source_map = _get_game_asset_source_map(resolved_data_dir)
    url = _build_game_asset_download_url_from_repo_path(
        _resolve_game_asset_repo_path(normalized, source_map)
    )
    _download_binary(url, dest)
    return dest


def _resolve_game_asset_repo_path(image_path: str, source_map: dict[str, str]) -> str:
    normalized = _normalize_asset_path(image_path)
    if normalized is None:
        raise ValueError(f'Invalid game asset path: {image_path!r}')
    return source_map.get(normalized, _build_game_asset_repo_path(normalized))


def _fingerprint_payload(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _load_source_manifest_paths(manifest_path: Path) -> set[str]:
    entries: set[str] = set()
    for raw_line in manifest_path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line:
            continue
        candidate = line.split(' ', 1)[1] if ' ' in line else line
        candidate = candidate.strip().replace('\\', '/')
        if not candidate.startswith(f'{_GAME_ASSET_UI_ROOT}/'):
            continue
        if not candidate.lower().endswith('.png'):
            continue
        entries.add(candidate)
    return entries


@dataclass(frozen=True)
class AssetFamilyStatus:
    family: str
    total: int
    existing: int
    missing: int


@dataclass(frozen=True)
class AssetAuditResult:
    manifest_path: Path
    checked: int
    present: int
    missing: tuple[str, ...]


@dataclass(frozen=True)
class _AssetDownload:
    family: str
    label: str
    url: str
    dest: Path
    delay_seconds: float = 0.0


@dataclass(frozen=True)
class _PreparedAssetFamily:
    family: str
    revision: str
    downloads: tuple[_AssetDownload, ...]


class _AssetFamily:
    name = 'assets'

    def prepare_downloads(self, *, data_dir: Path, assets_dir: Path) -> _PreparedAssetFamily:
        raise NotImplementedError


class _GameIconsAssetFamily(_AssetFamily):
    name = 'game-icons'

    def prepare_downloads(self, *, data_dir: Path, assets_dir: Path) -> _PreparedAssetFamily:
        manifest = _load_game_asset_manifest(data_dir)
        source_map = _get_game_asset_source_map(data_dir)
        logger.info('Loaded %d game asset paths from catalogs', len(manifest))
        if source_map:
            logger.info('Loaded %d raw source-path overrides for game assets', len(source_map))
        revision = _fetch_github_commit_sha(
            _GAME_ASSET_REPO_OWNER,
            _GAME_ASSET_REPO_NAME,
            _GAME_ASSET_REPO_REF,
        )
        downloads = tuple(
            _AssetDownload(
                family=self.name,
                label=image_path,
                url=_build_game_asset_download_url_from_repo_path(
                    _resolve_game_asset_repo_path(image_path, source_map)
                ),
                dest=assets_dir / Path(image_path),
                delay_seconds=0.1,
            )
            for image_path in manifest
        )
        return _PreparedAssetFamily(family=self.name, revision=revision, downloads=downloads)


class _SonataIconsAssetFamily(_AssetFamily):
    name = 'sonata-icons'

    def prepare_downloads(self, *, data_dir: Path, assets_dir: Path) -> _PreparedAssetFamily:
        sonata_keys = _load_sonata_keys(data_dir)
        logger.info('Loaded %d sonata keys', len(sonata_keys))

        mapping = _build_icon_mapping(sonata_keys)
        logger.info('Matched %d / %d sonata icons', len(mapping), len(sonata_keys))
        unmatched = sonata_keys - set(mapping)
        if unmatched:
            logger.warning('No wiki icon found for: %s', ', '.join(sorted(unmatched)))

        output_dir = assets_dir / 'IconS'
        downloads = tuple(
            _AssetDownload(
                family=self.name,
                label=f'IconS/{key}.png',
                url=url,
                dest=output_dir / f'{key}.png',
                delay_seconds=0.3,
            )
            for key, url in sorted(mapping.items())
        )
        revision = _fingerprint_payload(
            {
                'sonata_keys': sorted(sonata_keys),
                'mapping': sorted(mapping.items()),
            }
        )
        return _PreparedAssetFamily(family=self.name, revision=revision, downloads=downloads)


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

    def __init__(
        self,
        *,
        force: bool = False,
        include_families: tuple[str, ...] | None = None,
    ) -> None:
        self.force = force
        self._included_family_names = (
            frozenset(include_families)
            if include_families is not None
            else None
        )

    def _assets_dir(self) -> Path:
        return basePATH / 'assets'

    def _state_path(self) -> Path:
        return self._assets_dir() / _ASSET_STATE_FILENAME

    @staticmethod
    def _base_state() -> dict[str, object]:
        return {
            'version': 1,
            'families': {},
        }

    def _load_asset_state(self) -> dict[str, object]:
        try:
            with open(self._state_path(), 'r', encoding='utf-8') as handle:
                payload = json.load(handle)
            if isinstance(payload, dict):
                return payload
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        return self._base_state()

    def _save_asset_state(self, state: dict[str, object]) -> None:
        try:
            with open(self._state_path(), 'w', encoding='utf-8') as handle:
                json.dump(state, handle, indent=2, ensure_ascii=False)
        except Exception as exc:
            logger.error('Failed to save asset state: %s', exc)

    def _plan_families(self) -> tuple[_PreparedAssetFamily, ...]:
        data_dir = basePATH / 'data'
        assets_dir = self._assets_dir()
        plans: list[_PreparedAssetFamily] = []
        for family in self._iter_asset_families():
            try:
                plans.append(family.prepare_downloads(data_dir=data_dir, assets_dir=assets_dir))
            except Exception as exc:
                logger.error('Failed to prepare %s asset downloads: %s', family.name, exc)
        return tuple(plans)

    def plan_downloads(self) -> tuple[_AssetDownload, ...]:
        return tuple(
            download
            for plan in self._plan_families()
            for download in plan.downloads
        )

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
        assets_dir = self._assets_dir()
        assets_dir.mkdir(parents=True, exist_ok=True)

        state = self._load_asset_state()
        plans = self._plan_families()
        refresh_families = self._families_requiring_refresh(plans, state)
        if refresh_families:
            logger.info('Refreshing managed families after source revision change: %s', ', '.join(sorted(refresh_families)))

        self._prune_managed_files(plans, state, assets_dir)

        downloads = tuple(download for plan in plans for download in plan.downloads)
        progress = _ProgressTracker(self, len(downloads))
        self._sync_downloads(downloads, progress, refresh_families=refresh_families)
        self._update_state_after_sync(state, plans)
        self._save_asset_state(state)

        self._onFinished()

    def audit_game_asset_source_manifest(self, manifest_path: Path) -> AssetAuditResult:
        source_map = _get_game_asset_source_map(basePATH / 'data')
        expected_paths = tuple(
            f'{_GAME_ASSET_UI_ROOT}/{_resolve_game_asset_repo_path(image_path, source_map)}'
            for image_path in _load_game_asset_manifest(basePATH / 'data')
        )
        available_paths = _load_source_manifest_paths(manifest_path)
        missing = tuple(sorted(path for path in expected_paths if path not in available_paths))
        return AssetAuditResult(
            manifest_path=manifest_path,
            checked=len(expected_paths),
            present=len(expected_paths) - len(missing),
            missing=missing,
        )

    def _iter_asset_families(self) -> tuple[_AssetFamily, ...]:
        families = (
            _GameIconsAssetFamily(),
            _SonataIconsAssetFamily(),
        )
        if self._included_family_names is None:
            return families
        return tuple(
            family
            for family in families
            if family.name in self._included_family_names
        )

    def _sync_downloads(
        self,
        downloads: list[_AssetDownload] | tuple[_AssetDownload, ...],
        progress: _ProgressTracker,
        *,
        refresh_families: set[str] | None = None,
    ) -> None:
        families_to_refresh = refresh_families or set()
        for download in downloads:
            progress_label = self._progress_label(download)
            refresh_existing = self.force or download.family in families_to_refresh
            if download.dest.exists() and not refresh_existing:
                logger.debug('Skip (already exists): %s', progress_label)
                progress.advance(progress_label)
                continue

            action = 'Refreshing' if refresh_existing and download.dest.exists() else 'Downloading'
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

    @staticmethod
    def _family_state(state: dict[str, object], family_name: str) -> dict[str, object]:
        families = state.get('families')
        if not isinstance(families, dict):
            return {}
        family_state = families.get(family_name)
        return family_state if isinstance(family_state, dict) else {}

    @staticmethod
    def _normalize_managed_path(path: object) -> str | None:
        return _normalize_asset_path(path)

    def _families_requiring_refresh(
        self,
        plans: tuple[_PreparedAssetFamily, ...],
        state: dict[str, object],
    ) -> set[str]:
        refresh_families: set[str] = set()
        for plan in plans:
            previous_revision = self._family_state(state, plan.family).get('revision')
            if isinstance(previous_revision, str) and previous_revision and plan.revision and previous_revision != plan.revision:
                refresh_families.add(plan.family)
        return refresh_families

    def _prune_managed_files(
        self,
        plans: tuple[_PreparedAssetFamily, ...],
        state: dict[str, object],
        assets_dir: Path,
    ) -> None:
        for plan in plans:
            previous = self._family_state(state, plan.family).get('managed_files')
            if not isinstance(previous, list):
                continue
            current = {download.label for download in plan.downloads}
            for raw_path in previous:
                normalized = self._normalize_managed_path(raw_path)
                if normalized is None or normalized in current:
                    continue
                self._delete_managed_file(assets_dir, normalized)

    def _delete_managed_file(self, assets_dir: Path, relative_path: str) -> None:
        path = assets_dir / Path(relative_path)
        if not path.is_file():
            return
        try:
            path.unlink()
            logger.info('Pruned stale managed asset %s', relative_path)
        except OSError as exc:
            logger.error('Failed to prune %s: %s', relative_path, exc)
            return

        parent = path.parent
        while parent != assets_dir and parent.exists():
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent

    def _update_state_after_sync(
        self,
        state: dict[str, object],
        plans: tuple[_PreparedAssetFamily, ...],
    ) -> None:
        families = state.setdefault('families', {})
        if not isinstance(families, dict):
            state['families'] = {}
            families = state['families']

        for plan in plans:
            families[plan.family] = {
                'revision': plan.revision,
                'managed_files': [download.label for download in plan.downloads],
            }

    def _onProgress(self, file_name: str, percent: float) -> None:
        """Override to receive download progress."""

    def _onFinished(self) -> None:
        """Override or connect to be notified on completion."""
