# Game Asset Updater Plan

This document proposes how to restore game asset support in the current app without returning to the old pre-fork implementation.

The goal is to let the inventory viewer lazily populate the local `assets/` cache with the item and weapon PNGs it actually needs, while preserving the current architecture, keeping binary assets out of git, and retaining a manual full-cache prewarm path for repair or offline preparation.

## Scope

Primary scope:

- restore item and weapon thumbnail support for the current app
- prefer viewer-driven lazy downloads for `game-icons` instead of bulk startup syncing
- keep a manual full-manifest prewarm path for repair and offline preparation
- keep the existing sonata icon updater working
- keep the current data update path ahead of metadata resolution
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
  - That ordering remains useful for data refresh and optional prewarm work, but a lazy viewer flow only requires the catalogs to exist before the viewer asks for a thumbnail.

2. The data updater still emits relative image paths.
   - `src/wuwa_inventory_kamera/updater/database.py` still derives `image` values from upstream `Icon` fields.
   - Example contract:
     - catalog value: `IconA/T_IconA_AccountExp_UI.png`
     - local cache target: `assets/IconA/T_IconA_AccountExp_UI.png`

3. The runtime still consumes those image paths.
   - `src/wuwa_inventory_kamera/scraping/data.py` preserves `image` in the in-memory compatibility maps.
   - `src/wuwa_inventory_kamera/ui/inventory_models.py` resolves item and weapon metadata including `image_path`.
   - `src/wuwa_inventory_kamera/ui/inventory.py` loads thumbnails from `basePATH / 'assets' / image_path`.
  - When a local file is missing today, the viewer simply hides the thumbnail; that is the gap a lazy cache-repair path should close.

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
  - purpose: inventory thumbnails fetched on demand, plus optional manual prewarm for the same cache

- `sonata-icons`
  - source: current Fandom wiki workflow
  - local target: `assets/IconS/*.png`
  - purpose: `SonataIconMatcher`

- `static-app-icon`
  - source: tracked file already in repo
  - local target: `assets/icon.ico`
  - purpose: main window / loading screen icon

The orchestrator should continue to manage `sonata-icons` and optional `game-icons` prewarm work, but viewer-demanded single-file downloads should reuse the same shared path and source helpers.

### 2. Make game icon delivery viewer-driven by default

The default runtime behavior should be lazy, not bulk.

Recommended viewer algorithm:

1. Resolve a row's existing `image_path` from the generated item or weapon metadata.
2. If `assets/<image_path>` already exists, render it immediately.
3. If the file is missing, queue a background download for that single normalized path.
4. Show a placeholder or empty image state while the request is in flight.
5. Refresh the affected row or widget when the file lands in the cache.
6. De-duplicate concurrent requests by normalized `image_path` so repeated rows do not trigger repeated downloads.

Benefits:

- only downloads what a user actually opens in the viewer
- avoids blocking startup on 1000+ item-driven cache fills
- preserves the existing `assets/<image_path>` cache contract
- still lets the cache warm naturally over time across sessions

### 3. Keep the catalog manifest for manual prewarm and audit

Do not hard-code folder lists as the old app did. Build the full prewarm manifest from the current generated catalogs, but reserve it for manual repair, offline preparation, and audit rather than the default startup path.

Recommended manifest algorithm:

1. Load `data/catalog/items.json`.
2. Load `data/catalog/weapons.json`.
3. Collect every non-empty `image` value.
4. Normalize each path to a safe relative POSIX path.
5. Reject anything that tries to escape the assets root.
6. De-duplicate into a sorted manifest.

Manual prewarm boundary:

- include every catalog-backed item and weapon image the app can currently render
- implicitly cover `inventory`, `devItems`, `resources`, and `shell` rows because those views reuse item image paths
- exclude characters, echoes, achievements, sonatas, stats, and skills because the current generated data and runtime UI do not consume image paths for those families
- do not use this full manifest as the default viewer-time startup policy

Benefits:

- keeps repair and offline-prewarm behavior tied to the current generated catalog contract instead of reviving old hard-coded folders
- automatically adapts when new items or weapons appear in the data catalogs
- keeps audit and status commands meaningful
- avoids forcing every startup to download the whole current catalog image set

### 4. Map catalog paths to the new source repo layout

For `game-icons`, the path translator should be deterministic for both lazy single-file fetches and manual full-manifest prewarm:

- local asset relative path: `<image_path>`
- source repo relative path: `UI/UIResources/Common/Image/<image_path>`

That path mapping should live in one small tested helper, not be duplicated across updater code and tools.

Example:

```text
IconA/T_IconA_AccountExp_UI.png
-> UI/UIResources/Common/Image/IconA/T_IconA_AccountExp_UI.png
```

### 5. Use a remote source adapter, not the scratchpad checkout

The shipping updater should not read from `scratchpad/asset-repo/...` because packaged users will not have that directory.

Recommended runtime source strategy:

- download individual files from the GitHub-hosted asset repo using a raw-content URL or equivalent HTTP endpoint
- keep a small source adapter responsible for turning a repo-relative path into a download URL

Recommended development-only option:

- optionally allow a local source root override for testing against the sparse checkout
- use that only in a CLI or debug path, not as the default runtime behavior

### 6. Preserve the current `IconS` updater as a separate source

Because `Wuthering-Waves-GameAssets` does not expose `IconS`, the current sonata updater should remain in place during the first implementation.

Practical recommendation:

- keep the current Fandom-backed `IconS` download logic
- move it behind a `sonata-icons` family updater
- let the new orchestrator run both families in sequence

This keeps existing sonata matching stable while restoring game thumbnails.

## Proposed Code Changes

### Existing files to update

- `src/wuwa_inventory_kamera/updater/assets.py`
  - expose shared single-file game-icon resolution and download helpers, plus manual prewarm and audit logic

- `src/wuwa_inventory_kamera/ui/inventory.py`
  - detect missing local thumbnails, queue lazy downloads, and refresh the viewer when the cache is repaired

- `src/wuwa_inventory_kamera/ui/loading.py`
  - keep current data update flow
  - avoid blocking startup on full `game-icons` prewarm by default
  - keep startup asset work limited to lightweight tasks such as sonata support if still needed
  - honor `checkUpdateAtStartUp` instead of always running updates

- `updater/assetsUpdater.py`
  - keep as the Qt compatibility wrapper around the refactored base updater and shared lazy-download helpers

### Recommended new files

- `src/wuwa_inventory_kamera/updater/asset_manifest.py`
  - build the manual prewarm manifest of required `game-icons` from generated catalogs

- `src/wuwa_inventory_kamera/updater/asset_sources.py`
  - source-specific path resolution and download helpers
  - one adapter for the GitHub game-assets repo
  - one adapter for the existing Fandom `IconS` source

- `src/wuwa_inventory_kamera/ui/asset_cache.py`
  - small Qt-aware queue or cache-repair helper for viewer-triggered lazy downloads

- `src/wuwa_inventory_kamera/cli/update_assets.py`
  - manual `status` / `update` / `--force` entry point for prewarm, repair, and testing outside the GUI

The exact file split can change, but the important part is to separate:

- lazy cache lookup and in-flight de-duplication
- manifest building for manual prewarm
- source resolution
- single-file and bulk download orchestration
- Qt progress signaling

## Update Policy

### Default runtime policy

For the first pass, keep the default runtime behavior simple and lazy.

- if the viewer needs `assets/<image_path>` and the file exists locally, render it immediately
- if the viewer needs `assets/<image_path>` and the file is missing, fetch just that file in the background
- write successful downloads back into the normal `assets/<image_path>` cache
- do not bulk-sync the full `game-icons` manifest during startup by default

Why this is acceptable for MVP:

- the user only pays network cost for assets they actually open
- the viewer only needs the files referenced by the current rows being rendered
- this avoids turning startup into a full repository synchronization step
- the cache still becomes more complete over time without special migration work

### Manual prewarm policy

Keep an explicit full-cache repair path for users who want it.

- `wuwa-assets update` may still download the full current item and weapon manifest
- if `--force` is requested, redownload those files
- if a manifest entry disappears from the catalogs, optionally prune it only if it belongs to a managed prewarm family

### Hardening after MVP

If lazy viewer fetches or manual prewarm become more complex, harden them incrementally.

Options, in order of preference:

1. de-duplicate in-flight viewer downloads and add a short retry backoff for recent failures
2. optionally prefetch the current document's item and weapon image set after a result file is opened
3. track the upstream repo commit SHA and managed-family state for explicit manual prewarm and audit flows

Do not repeat the old file-count heuristic.

## Startup And Settings Behavior

The current app has a `checkUpdateAtStartUp` setting, but the loading path still always runs the updaters.

That should be fixed as part of this work.

Recommended behavior:

- if `checkUpdateAtStartUp` is true:
  - run data updater
  - then run only lightweight startup asset work if still needed, such as sonata support
  - do not block startup on full `game-icons` prewarm
- if it is false:
  - skip both network update phases and proceed to the main window

Recommended lazy-download behavior:

- do not tie viewer-triggered missing-thumbnail fetches to `checkUpdateAtStartUp`
- treat viewer lazy downloads as runtime cache repair rather than startup synchronization

Optional later improvement:

- split the setting into separate toggles for startup data update, sonata prewarm, viewer lazy downloads, and full `game-icons` prewarm if users need finer control

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

- lazy viewer request handling for a missing thumbnail
- de-duplication of repeated requests for the same `image_path`
- manifest extraction from generated `items.json` and `weapons.json` for manual prewarm
- path normalization and path traversal rejection
- translation from local `image` path to source repo path
- manual prewarm orchestration behavior when one family succeeds and another fails
- `checkUpdateAtStartUp` gating in the loading flow without bulk `game-icons` startup sync

### Integration tests

Use temporary directories and mocked download helpers to verify:

- opening the viewer with a missing image downloads just that file to the correct local path
- repeated opens do not trigger duplicate in-flight downloads for the same path
- existing files are skipped once the cache contains them
- `wuwa-assets update --force` still redownloads files in manual prewarm mode
- managed prune logic does not delete `assets/icon.ico` or unrelated files

### Manual validation

Manual smoke test after implementation:

1. remove cached item and weapon icons from `assets/`
2. run the app from repo root
3. let data update finish
4. open the inventory viewer on an export with item or weapon rows
5. confirm only the viewed thumbnails are downloaded under `assets/` and begin rendering without a restart
6. run `wuwa-assets update` and confirm manual full-cache prewarm still works when desired
7. run an echo flow and verify `IconS` sonata matching still works

## Suggested Implementation Phases

### Phase 1: Restore the core contract with lazy viewer fetches

- extract shared single-image game-icon fetch helpers from the existing path contract
- add deterministic path translation into `UI/UIResources/Common/Image/...`
- teach the inventory viewer to queue missing thumbnail downloads into `assets/`
- refresh viewer rows when the local cache is repaired

Success condition:

- opening an inventory export with an empty cache downloads only the needed item or weapon thumbnails and renders them from the existing `image_path` contract

### Phase 2: Preserve and isolate sonata support

- move the current Fandom logic behind a dedicated `sonata-icons` family updater
- keep `assets/IconS` behavior unchanged from the caller perspective

Success condition:

- `SonataIconMatcher` still loads `assets/IconS/*.png` without any behavior change

### Phase 3: Startup and CLI ergonomics

- honor `checkUpdateAtStartUp`
- add a manual CLI for status, prewarm, repair, and forced refresh
- remove bulk `game-icons` startup syncing from the default loading path
- keep any startup progress labels focused on the remaining lightweight asset work

Success condition:

- users can let the viewer repair missing thumbnails lazily or run an explicit prewarm command when they want the whole cache populated

### Phase 4: Hardening

- add in-flight de-duplication and retry backoff for lazy viewer fetches
- optionally add current-document prefetch and managed prune rules for explicit prewarm flows
- add a developer audit tool to compare catalog references against the asset repo path space

Success condition:

- the lazy cache repair path is predictable, testable, and the manual prewarm path does not silently drift from the catalogs

## Recommended MVP Decision

Implement the smallest version that closes the current functional gap:

- keep `IconS` as-is
- reuse the existing generated `image_path` contract and source mapping helpers
- let the inventory viewer lazy-download missing item and weapon thumbnails into the existing `assets/<image_path>` layout
- keep `wuwa-assets update` as an explicit full-manifest prewarm or repair command
- do not block startup on bulk `game-icons` sync
- honor `checkUpdateAtStartUp` for the remaining startup network phases

That restores the missing game asset support in the current app without reviving the old folder-count sync model, without forcing 1000+ startup downloads, and without coupling runtime behavior to the scratchpad checkout.