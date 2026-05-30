# Grandfather Master Asset Usage

This note describes how the original pre-fork app in `scratchpad/grandfather_master` used image assets, why the worktree no longer contains most of them, and how its asset updater worked.

## What lived in `assets/`

The original repo expected a runtime `assets/` directory next to the executable or working directory. In practice it used that directory for two different purposes:

1. `assets/icon.ico`
   - This was the tracked application icon.
   - It was used by both the loading screen and the main window.
   - `setup.py` also referenced it for packaging.

2. Downloaded PNG icon folders such as `IconA`, `IconC`, `IconCook`, `IconMout`, `IconMst`, `IconRup`, `IconTask`, and `IconWup`
   - These folders were not committed to git.
   - The original `.gitignore` explicitly ignored `assets/*` while whitelisting only `assets/icon.ico`.
   - That is why the worktree still has the folder structure but not the downloaded files.

The important consequence is that the original app was designed around a partially ephemeral asset cache: the executable icon shipped with the repo, but most game item icons were expected to be fetched after startup.

## How the app used those assets

### Window icon

The simplest use was the application window icon:

- `ui/loadingUI.py` loaded `basePATH / 'assets' / 'icon.ico'` for the loading window.
- `ui/mainUI.py` loaded the same file for the main application window.
- `setup.py` also pointed packaging metadata at `assets/icon.ico`.

So `icon.ico` was the only asset that the repo treated as mandatory and versioned.

### Item and weapon image metadata

The larger asset story starts in `updater/databaseUpdater.py`.

On startup, the app downloaded upstream game data from `Dimbreath/WutheringData`, especially:

- `TextMap/<lang>/MultiText.json`
- `ConfigDB/ItemInfo.json`
- `ConfigDB/WeaponConf.json`

From those files it generated local lookup tables such as `data/items.json` and `data/weapons.json`.

For each item or weapon, it derived a relative PNG path from the upstream Unreal-style icon reference. The key line was effectively:

```python
item['Icon'].split('/Image/')[1].rsplit('.', 1)[0] + '.png'
```

Example:

```text
/Game/Aki/UI/UIResources/Common/Image/IconA/T_IconA_AccountExp_UI.T_IconA_AccountExp_UI
-> IconA/T_IconA_AccountExp_UI.png
```

That derived relative path was stored in generated files like `data/items.json`. In the preserved worktree, the first entries still show that exact convention:

```json
{
    "unionexp": {
        "id": 1,
        "name": "Union EXP",
        "image": "IconA/T_IconA_AccountExp_UI.png"
    }
}
```

### Runtime consumption in the UI

The visible runtime consumer of those downloaded PNGs was the inventory viewer UI.

- `ui/inventoryUI.py` loaded an item's generated `image` field.
- It resolved the actual file as `basePATH / 'assets' / image`.
- It then created a `QPixmap` for the card shown in the inventory editor.

So the path flow was:

1. Upstream game metadata provided an Unreal asset path.
2. `databaseUpdater.py` normalized that into a relative PNG path under `assets/`.
3. `inventoryUI.py` joined that relative path onto `basePATH / 'assets'` and rendered it.

Weapon metadata also carried an `image` field, but in the preserved original UI code there is no equally direct visual consumer for weapon icons. The item inventory view is the clear confirmed runtime use.

## Startup order

The original app always entered through a loading screen:

1. `app.py` created `LoadingScreen`.
2. `LoadingScreen` started `DataUpdaterThread`.
3. When data updating finished, it started `AssetsUpdaterThread`.
4. Only after both completed did it construct `WuWaInventoryKamera`.

That means asset fetching was intended to happen before the main window became usable.

## How the asset updater worked

The asset updater lived in `updater/assetsUpdater.py` and used the GitHub contents API instead of git cloning the upstream asset repo.

### Upstream source it expected

The code hard-coded:

- owner: `Stormy-Waves`
- repo: `WW_Icon`
- base path inside that repo: `UIResources/Common/Image`

It then mirrored only these subfolders:

- `IconA`
- `IconC`
- `IconCook`
- `IconMout`
- `IconMst`
- `IconRup`
- `IconTask`
- `IconWup`

This matches the full-size `Icon` fields in the generated item metadata. The upstream data also included `IconMiddle` and `IconSmall` references such as `IconA160` or `IconA80`, but the original updater did not download those directories.

### Download algorithm

For each configured folder, the updater did the following:

1. Build a GitHub contents API URL for `UIResources/Common/Image/<folder>`.
2. Create the local directory `basePATH / 'assets' / <folder>`.
3. Fetch the directory listing from GitHub.
4. Compare the number of local files with the number of entries returned by the API.
5. If the counts differed, iterate the remote entries.
6. For each file that did not already exist locally, download it from the entry's `download_url`.
7. Emit Qt progress signals during each download.

The loading screen subscribed to those progress signals and changed its label to `Downloading <folder>/<file>...` while the updater ran.

### What it did not do

The updater was simple and had several important limitations:

- It did not verify checksums or timestamps.
- It did not redownload changed files if the filename already existed.
- It did not remove stale local files.
- It used local file count as its only coarse change detector.
- If the file count matched, it skipped the folder entirely.
- If the upstream repo disappeared, it simply returned no data and the app continued without rebuilding the cache.

So the updater behaved more like a bootstrapper for missing PNGs than a true synchronizer.

## Why this still matters even though `WW_Icon` is gone

Even with the original upstream asset repo unavailable, the preserved code still tells us how the asset system was designed:

- The authoritative naming scheme survived in `ItemInfo.json`, `WeaponConf.json`, and generated files like `data/items.json`.
- The expected local filesystem layout survived in `assetsUpdater.py`.
- The actual runtime join point survived in `inventoryUI.py`.
- The repository policy that treated the asset cache as disposable survived in `.gitignore`.

That is enough to reconstruct the original contract:

- local images were expected under `assets/<folder>/<file>.png`
- filenames came from the upstream Unreal icon path after trimming everything before `/Image/`
- only selected full-size icon folders were auto-fetched
- the app icon was the only asset committed to the repo

## Practical reconstruction notes

If someone wanted to recreate the old asset cache today, the required filenames can still be derived from the preserved data files even without the vanished GitHub repo.

The minimum viable reconstruction would be:

1. Read `data/items.json` and `data/weapons.json` for the expected relative PNG paths.
2. Recreate those files under `assets/` using the same subfolder layout.
3. Keep `assets/icon.ico` in place for the application window icon.

If those PNGs are absent, the app can still start, but inventory image cards will render without the intended thumbnails.