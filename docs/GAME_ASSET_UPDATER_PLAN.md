# Game Asset Updater Plan

This document proposes how to restore game asset support in the current app without returning to the old pre-fork implementation.

The goal is to make the current app populate the local `assets/` cache with the item and weapon PNGs already referenced by the generated catalogs, while preserving the current architecture and keeping binary assets out of git.

## Scope

Primary scope:

- restore item and weapon thumbnail support for the current app
- keep the existing sonata icon updater working
- reuse the current startup order: data update first, asset update second
- keep downloaded assets as a disposable local cache under `assets/`

Out of scope for the first pass:

- syncing the entire `Wuthering-Waves-GameAssets` repository
- committing downloaded game PNGs into this repo
- replacing the current `IconS` Fandom workflow unless a better canonical source is identified later
- building a generalized art browser for all game UI assets

## What The Current App Already Does

The current app still has most of the plumbing needed for game asset support.

1. The loading flow already runs assets after data.
   - `src/wuwa_inventory_kamera/ui/loading.py` runs `BaseDataUpdater` first and `BaseAssetsUpdater` second.
   - That ordering is correct for a catalog-driven asset updater because the image manifest can be derived from the freshly updated catalogs.

2. The data updater still emits relative image paths.
   - `src/wuwa_inventory_kamera/updater/database.py` still derives `image` values from upstream `Icon` fields.
   - Example contract:
     - catalog value: `IconA/T_IconA_AccountExp_UI.png`
     - local cache target: `assets/IconA/T_IconA_AccountExp_UI.png`

3. The runtime still consumes those image paths.
   - `src/wuwa_inventory_kamera/scraping/data.py` preserves `image` in the in-memory compatibility maps.
   - `src/wuwa_inventory_kamera/ui/inventory_models.py` resolves item and weapon metadata including `image_path`.
   - `src/wuwa_inventory_kamera/ui/inventory.py` loads thumbnails from `basePATH / 'assets' / image_path`.

4. The repo still treats assets as a cache.
   - `.gitignore` ignores `/assets/*` and whitelists only `/assets/icon.ico`.
   - That is still the correct repo policy for downloaded game assets.

5. The current asset updater is too narrow.
   - `src/wuwa_inventory_kamera/updater/assets.py` only downloads `assets/IconS/*.png` from the Fandom wiki.
   - That covers sonata matching but not item or weapon thumbnails.

## What The Sparse Asset Repo Tells Us

The sparse checkout in `scratchpad/asset-repo/Wuthering-Waves-GameAssets` is useful as a source map, not as a runtime dependency.

Observed facts:

- the asset repo contains the old image families under `UI/UIResources/Common/Image/...`
- `ls-files-t` shows relevant paths for:
  - `IconA`
  - `IconA160`
  - `IconElement`
  - `IconElementAttri`
  - and, by implication from the old metadata contract, the other legacy item families such as `IconC`, `IconCook`, `IconMout`, `IconMst`, `IconRup`, `IconTask`, and `IconWup`
- many of those paths are marked `S` in `ls-files-t`, which means skip-worktree from sparse checkout
- there is no `UI/UIResources/Common/Image/IconS/` family in this repo, so the current sonata icon source should remain separate for now

Important consequence:

- the app should not depend on the local sparse checkout at runtime
- the sparse checkout should only be used during development to validate path mapping and refine sparse patterns

## Recommended Target Contract

Keep the current local asset contract unchanged:

- app icon stays at `assets/icon.ico`
- sonata reference icons stay at `assets/IconS/*.png`
- item and weapon thumbnails stay at `assets/<catalog image path>`

That means the new game asset updater should translate:

- catalog image path: `IconRup/T_IconRup_Part_1102_UI.png`
- source repo path: `UI/UIResources/Common/Image/IconRup/T_IconRup_Part_1102_UI.png`
- local cache path: `assets/IconRup/T_IconRup_Part_1102_UI.png`

This is the simplest design because it matches the existing database updater output and the existing inventory viewer input.

## Recommended Design

### 1. Split assets into explicit families

The current `BaseAssetsUpdater` should stop being a sonata-specific downloader and become an orchestrator for asset families.

Recommended families:

- `game-icons`
  - source: `Wuthering-Waves-GameAssets`
  - local target: `assets/IconA/...`, `assets/IconRup/...`, etc.
  - purpose: inventory thumbnails and any future item/weapon UI artwork

- `sonata-icons`
  - source: current Fandom wiki workflow
  - local target: `assets/IconS/*.png`
  - purpose: `SonataIconMatcher`

- `static-app-icon`
  - source: tracked file already in repo
  - local target: `assets/icon.ico`
  - purpose: main window / loading screen icon

The orchestrator should update `game-icons` and `sonata-icons`, but leave `icon.ico` untouched.

### 2. Make the game icon updater manifest-driven

Do not hard-code folder lists as the old app did. Build the manifest from the current generated catalogs.

Recommended manifest algorithm:

1. Load `data/catalog/items.json`.
2. Load `data/catalog/weapons.json`.
3. Collect every non-empty `image` value.
4. Normalize each path to a safe relative POSIX path.
5. Reject anything that tries to escape the assets root.
6. De-duplicate into a sorted manifest.

Benefits:

- only downloads what the current app can actually use
- follows the current generated catalog contract instead of reviving old hard-coded folders
- automatically adapts when new items or weapons appear in the data catalogs
- avoids downloading unused folders like `IconA160` unless the catalogs begin referencing them

### 3. Map catalog paths to the new source repo layout

For `game-icons`, the path translator should be deterministic:

- local asset relative path: `<image_path>`
- source repo relative path: `UI/UIResources/Common/Image/<image_path>`

That path mapping should live in one small tested helper, not be duplicated across updater code and tools.

Example:

```text
IconA/T_IconA_AccountExp_UI.png
-> UI/UIResources/Common/Image/IconA/T_IconA_AccountExp_UI.png
```

### 4. Use a remote source adapter, not the scratchpad checkout

The shipping updater should not read from `scratchpad/asset-repo/...` because packaged users will not have that directory.

Recommended runtime source strategy:

- download individual files from the GitHub-hosted asset repo using a raw-content URL or equivalent HTTP endpoint
- keep a small source adapter responsible for turning a repo-relative path into a download URL

Recommended development-only option:

- optionally allow a local source root override for testing against the sparse checkout
- use that only in a CLI or debug path, not as the default runtime behavior

### 5. Preserve the current `IconS` updater as a separate source

Because `Wuthering-Waves-GameAssets` does not expose `IconS`, the current sonata updater should remain in place during the first implementation.

Practical recommendation:

- keep the current Fandom-backed `IconS` download logic
- move it behind a `sonata-icons` family updater
- let the new orchestrator run both families in sequence

This keeps existing sonata matching stable while restoring game thumbnails.

## Proposed Code Changes

### Existing files to update

- `src/wuwa_inventory_kamera/updater/assets.py`
  - convert from single-purpose `IconS` downloader into a small orchestrator plus family-specific helpers

- `src/wuwa_inventory_kamera/ui/loading.py`
  - keep current data-then-assets flow
  - make sure progress labels can identify which family is downloading
  - honor `checkUpdateAtStartUp` instead of always running updates

- `updater/assetsUpdater.py`
  - keep as the Qt compatibility wrapper around the refactored base updater

### Recommended new files

- `src/wuwa_inventory_kamera/updater/asset_manifest.py`
  - build the manifest of required `game-icons` from generated catalogs

- `src/wuwa_inventory_kamera/updater/asset_sources.py`
  - source-specific path resolution and download helpers
  - one adapter for the GitHub game-assets repo
  - one adapter for the existing Fandom `IconS` source

- `src/wuwa_inventory_kamera/cli/update_assets.py`
  - manual `status` / `update` / `--force` entry point for repair and testing outside the GUI

The exact file split can change, but the important part is to separate:

- manifest building
- source resolution
- sync orchestration
- Qt progress signaling

## Update Policy

### MVP policy

For the first pass, keep update behavior simple and pragmatic.

- if a manifest file is missing locally, download it
- if `--force` is requested, redownload it
- if a manifest entry disappears from the catalogs, optionally prune it only if it belongs to a managed family

Why this is acceptable for MVP:

- newly added game assets appear under new filenames, so missing-file sync restores the primary broken behavior
- the inventory viewer only needs the files referenced by the current catalogs
- this avoids turning startup into a full repository synchronization step

### Hardening after MVP

If same-name asset replacements become a real problem, add source revision tracking later.

Options, in order of preference:

1. track the upstream repo commit SHA and invalidate a managed-family state file when it changes
2. persist per-file metadata such as size or hash for managed assets
3. add a one-shot tool that audits local cache vs source repo state on demand

Do not repeat the old file-count heuristic.

## Startup And Settings Behavior

The current app has a `checkUpdateAtStartUp` setting, but the loading path still always runs the updaters.

That should be fixed as part of this work.

Recommended behavior:

- if `checkUpdateAtStartUp` is true:
  - run data updater
  - then run asset updater
- if it is false:
  - skip both network update phases and proceed to the main window

Optional later improvement:

- split the setting into separate data and asset toggles if users need finer control

## Development Use Of The Sparse Checkout

The sparse checkout is still valuable during implementation.

Recommended uses:

- validate that `UI/UIResources/Common/Image/<image_path>` exists for the catalog-derived manifest
- inspect additional families such as `IconA160` and `IconElement` for future UI work
- refine sparse-checkout rules when local testing needs real files materialized

Recommended non-use:

- do not make the app read `scratchpad/asset-repo/Wuthering-Waves-GameAssets` during normal startup
- do not parse `ls-files-t` at runtime

## Validation Plan

### Unit tests

Add focused tests for:

- manifest extraction from generated `items.json` and `weapons.json`
- path normalization and path traversal rejection
- translation from local `image` path to source repo path
- orchestrator behavior when one family succeeds and another fails
- `checkUpdateAtStartUp` gating in the loading flow

### Integration tests

Use temporary directories and mocked download helpers to verify:

- missing manifest entries are downloaded to the correct local paths
- existing files are skipped in normal mode
- `--force` redownloads files
- managed prune logic does not delete `assets/icon.ico` or unrelated files

### Manual validation

Manual smoke test after implementation:

1. remove cached item and weapon icons from `assets/`
2. run the app from repo root
3. let data update finish
4. confirm the asset phase downloads game icon files under `assets/IconA`, `assets/IconRup`, and other referenced families
5. open the inventory viewer and verify item and weapon thumbnails render again
6. run an echo flow and verify `IconS` sonata matching still works

## Suggested Implementation Phases

### Phase 1: Restore the core contract

- replace the sonata-only `BaseAssetsUpdater` with a family orchestrator
- add manifest extraction from generated item and weapon catalogs
- add deterministic path translation into `UI/UIResources/Common/Image/...`
- download missing game icon files into `assets/`

Success condition:

- the inventory viewer renders thumbnails again from the existing `image_path` contract

### Phase 2: Preserve and isolate sonata support

- move the current Fandom logic behind a dedicated `sonata-icons` family updater
- keep `assets/IconS` behavior unchanged from the caller perspective

Success condition:

- `SonataIconMatcher` still loads `assets/IconS/*.png` without any behavior change

### Phase 3: Startup and CLI ergonomics

- honor `checkUpdateAtStartUp`
- add a manual CLI for status, repair, and forced refresh
- improve progress labels so the loading screen shows which asset family is active

Success condition:

- users can repair assets without deleting the whole cache and restarting blindly

### Phase 4: Hardening

- add prune rules for managed families
- optionally add source revision tracking
- add a developer audit tool to compare catalog references against the asset repo path space

Success condition:

- the updater is predictable, testable, and does not silently drift from the catalogs

## Recommended MVP Decision

Implement the smallest version that closes the current functional gap:

- keep `IconS` as-is
- derive required game icon paths from the generated catalogs
- download those files from `Wuthering-Waves-GameAssets` into the existing `assets/<image_path>` layout
- wire the refactored updater into the existing startup path
- honor `checkUpdateAtStartUp`

That restores the missing game asset support in the current app without reviving the old folder-count sync model and without coupling runtime behavior to the scratchpad checkout.