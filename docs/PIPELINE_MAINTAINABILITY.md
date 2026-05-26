# Pipeline Maintainability Notes

## Scope

This document is intentionally broader than echoes, but the current pass still
starts with the echo pipeline because it is the heaviest shared scan/reprocess
path and already exposes most of the architectural problems we also care about
in other inventory surfaces.

The inspection for this pass focused on:

- [src/wuwa_inventory_kamera/scraping/scanning/echo_workflow.py](src/wuwa_inventory_kamera/scraping/scanning/echo_workflow.py)
- [src/wuwa_inventory_kamera/scraping/service/echo_reprocess.py](src/wuwa_inventory_kamera/scraping/service/echo_reprocess.py)
- [src/wuwa_inventory_kamera/cli/reprocess.py](src/wuwa_inventory_kamera/cli/reprocess.py)
- [src/wuwa_inventory_kamera/scraping/service/ocr_service.py](src/wuwa_inventory_kamera/scraping/service/ocr_service.py)
- [src/wuwa_inventory_kamera/scraping/service/assemblers/echo_assembler.py](src/wuwa_inventory_kamera/scraping/service/assemblers/echo_assembler.py)
- [src/wuwa_inventory_kamera/scraping/models/raw_scan.py](src/wuwa_inventory_kamera/scraping/models/raw_scan.py)
- [src/wuwa_inventory_kamera/scraping/utils/common.py](src/wuwa_inventory_kamera/scraping/utils/common.py)
- [src/wuwa_inventory_kamera/scraping/processing/echoes_processor.py](src/wuwa_inventory_kamera/scraping/processing/echoes_processor.py)
- [src/wuwa_inventory_kamera/scraping/processing/echoesValidator.py](src/wuwa_inventory_kamera/scraping/processing/echoesValidator.py)
- [src/wuwa_inventory_kamera/scraping/ocr/region_specs.py](src/wuwa_inventory_kamera/scraping/ocr/region_specs.py)
- [src/wuwa_inventory_kamera/config/app_config.py](src/wuwa_inventory_kamera/config/app_config.py)
- [src/wuwa_inventory_kamera/ui/home.py](src/wuwa_inventory_kamera/ui/home.py)
- [src/wuwa_inventory_kamera/game/game_roi.py](src/wuwa_inventory_kamera/game/game_roi.py)
- [src/wuwa_inventory_kamera/game/screen_info.py](src/wuwa_inventory_kamera/game/screen_info.py)
- [tests/test_echo_workflow.py](tests/test_echo_workflow.py)
- [tests/test_echo_reprocess.py](tests/test_echo_reprocess.py)

This is not a full rewrite plan. It is a grounded list of maintainability
problems visible in the current code and a concrete record of the cleanup
decisions already made.

## Decisions Already Made

These are no longer open questions for the active pipeline:

1. `wuwa-reprocess` is an `OcrService`-only entry point.
   The old extractor mode, `--extractor`, `--use-bw`, and the Tesseract
   variants are no longer part of the supported reprocess surface.
2. Tesseract-specific code is being removed rather than preserved as a fallback.
   The codebase should assume RapidOCR plus the service pipeline going forward.
3. Root-level `scraping/` and `ui/` shim trees are gone.
   Active callers now import `wuwa_inventory_kamera.*` directly.
4. Only 1920x1080 and 1920x1200 are supported for the active inventory layout.
   Nearest-resolution scaling and the older resolution tables are intentionally
   gone for now.
5. `SessionOrchestrator` returns structured results instead of writing to the
   old v1 result path, even though some UI compatibility state still exists for
   manual item correction.
6. New live echo raw sessions are built around `full.png` plus `meta.json`.
   Separate `sonata.png` capture is now a compatibility concern, not part of
   the primary live path.

These decisions matter because they let future cleanup work target the real
product surface instead of preserving branches we have already decided not to
keep.

## Current Shape

Today there are two active echo-processing flows:

1. Live app scan:
   [src/wuwa_inventory_kamera/scraping/scanning/echo_workflow.py](src/wuwa_inventory_kamera/scraping/scanning/echo_workflow.py)
   captures a full frame, crops a single echo into an `EchoCapture`, and submits
   it to [src/wuwa_inventory_kamera/scraping/service/ocr_service.py](src/wuwa_inventory_kamera/scraping/service/ocr_service.py).
   If raw saving is enabled, the live path persists `full.png` and `meta.json`
   through its local `_save_raw()` helper.
2. Offline reprocess:
   [src/wuwa_inventory_kamera/cli/reprocess.py](src/wuwa_inventory_kamera/cli/reprocess.py)
   loads `RawEchoScan` data through `loadRawScans()` and
   [src/wuwa_inventory_kamera/scraping/service/echo_reprocess.py](src/wuwa_inventory_kamera/scraping/service/echo_reprocess.py)
   reconstructs an `EchoCapture` before sending it to the same `OcrService`.

There is still older RapidOCR-only processing code in
[src/wuwa_inventory_kamera/scraping/processing/echoes_processor.py](src/wuwa_inventory_kamera/scraping/processing/echoes_processor.py),
but it is no longer the supported reprocess entry path. That means the useful
shared seam is still clearly:

`EchoCapture -> OcrService -> EchoAssembler`

The remaining maintainability work is mostly about making both active paths
meet that seam in exactly the same way and about collapsing the legacy raw-scan
compatibility layer around them.

## Cleanup Already Landed

The codebase has already been simplified in several important ways:

- `wuwa-reprocess` no longer exposes legacy extractor selection.
- Tesseract-specific extractor classes, debug-tool options, and tests have been
  removed.
- `ScreenInfo` now rejects unsupported resolutions instead of scaling to the
  nearest known layout.
- The ROI table keeps only 1920x1080 and 1920x1200 entries.
- Live scan now reuses the min-level pre-read when that OCR already succeeded,
  instead of issuing a second `echoes.level` OCR/cache lookup for the same echo.
- Live scan and reprocess now share one neutral helper module for level parsing,
  level-dependent sonata icon selection, and source-space normalization.
- Root-level import shims are gone.
- The active live echo raw path no longer depends on separate `sonata.png`
  persistence.

That cleanup does not solve the deeper architecture problem yet, but it removes
several dead surfaces that were obscuring it.

## Issues Found

### 1. `EchoCapture` construction is still duplicated across live scan and reprocess

The live path and the reprocess path still rebuild the same single-echo data
bundle by hand.

Both paths currently do all of the following themselves:

- slice `echoCard`, `fullStatsName`, `fullStatsValue`, `echoName`, and sonata
  icon ROIs
- resolve the level-dependent sonata icon ROI variant
- resolve level-dependent icon circle metadata
- detect rarity from a single sampled pixel
- optionally write the same debug crop artifacts
- instantiate `EchoCapture`

The duplication is still most obvious between
[src/wuwa_inventory_kamera/scraping/scanning/echo_workflow.py](src/wuwa_inventory_kamera/scraping/scanning/echo_workflow.py)
and
[src/wuwa_inventory_kamera/scraping/service/echo_reprocess.py](src/wuwa_inventory_kamera/scraping/service/echo_reprocess.py).

Cleanup direction:

- extract one pure capture-builder function or class that takes a full frame,
  screen layout, and source metadata and returns the canonical `EchoCapture`
- let live scan own frame acquisition only
- let reprocess own frame loading only
- keep everything from crop selection onward shared

### 2. The raw-session contract is split between the active v2 format and the legacy helper/model format

The active live raw path and the older helper/model layer no longer describe the
same thing.

Current state:

- `EchoWorkflow._save_raw()` writes `full.png` plus `meta.json`
- `saveRawScan()` still writes `full.png` plus `sonata.png` plus `meta.json`
- `RawEchoScan` still exposes `sonata_screenshot` and `sonata_path`
- `loadRawScans()` supports optional `sonata.png`
- `echoes_processor.py` still carries the old separate-sonata branch

That means the repository is currently supporting both a new raw-session shape
and an older one.

Cleanup direction:

- choose one canonical raw-session format for the active pipeline
- keep old-format support behind a converter or explicit legacy loader if it is
  still needed

### 3. The image color-space contract is still implicit and inconsistent

The code still relies on channel-order knowledge that is spread out between
comments, helper names, and one-off conversions.

Examples:

- `EchoCapture` comments say `card`, `echo_name`, and `sonata_icon` are BGR,
  while `stats_name` and `stats_value` are RGB
- live scan rarity detection treats the captured frame as BGR
- `RawEchoScan.load_images()` converts disk images to RGB
- reprocess converts several crops back to BGR before submission
- `OcrService` converts stat crops from RGB back to BGR before spec-driven OCR

Cleanup direction:

- choose one canonical internal image space for `EchoCapture`
- make source color space explicit at acquisition/load boundaries
- add tests that compare live-built and reprocess-built captures for the same
  frame input

### 4. OCR region specs are still only half of the story

[src/wuwa_inventory_kamera/scraping/ocr/region_specs.py](src/wuwa_inventory_kamera/scraping/ocr/region_specs.py)
does centralize preprocessing and cache-signature rules, which is good.

But several decisions still sit outside the spec system:

- choosing `level_X` vs `level_XX` after a separate level OCR pass
- echo-name strategy switching inside `OcrService` between filtered single-line,
  thorough OCR, batch OCR, and raw fallback
- runtime construction of the echo-name allowed-character set from localized
  data

Cleanup direction:

- keep `OcrRegionSpec` focused on OCR preprocessing and cache signatures
- introduce a separate capture/layout abstraction for dynamic ROI selection
- move echo-name recognition policy into a dedicated helper instead of embedding
  it inline in the generic echo batch processor

### 5. Shared low-level helpers still live in entry-point modules

Some pure helpers are still stored in the wrong place architecturally:

- reprocess still imports rarity helpers from
  [src/wuwa_inventory_kamera/scraping/scanning/echo_workflow.py](src/wuwa_inventory_kamera/scraping/scanning/echo_workflow.py)
- live workflow still imports debug-artifact writing from
  [src/wuwa_inventory_kamera/scraping/service/echo_reprocess.py](src/wuwa_inventory_kamera/scraping/service/echo_reprocess.py)

Cleanup direction:

- continue moving rarity helpers and shared crop-debug writing into a neutral
  helper module

### 6. The boundary between capture preparation, OCR orchestration, and assembly is still soft

`EchoCapture` currently carries a mix of:

- raw image crops
- derived metadata such as rarity and parsed level
- geometry metadata for sonata icon matching
- debug-only data such as full screenshots

Meanwhile:

- `OcrService` contains echo-specific recognition policy for the name crop
- `EchoAssembler` still reaches back into older modules for validators

Cleanup direction:

- split the pipeline into three clearer layers:
  1. capture preparation: full frame -> canonical prepared echo capture
  2. OCR execution: prepared capture -> OCR tokens by region
  3. assembly and validation: OCR tokens + derived metadata -> `EchoResult`

### 7. Legacy state and compatibility hooks still remain active in the package

The remaining compatibility burden is no longer root-level import shims. It is
mostly package-local state and bootstrap code such as:

- `INVENTORY` and `FAILED` in `config/app_config.py`
- `savingScraped()` in `scraping/utils/common.py`
- the manual item-recognition flow in `ui/home.py`
- project-root `sys.path` bootstrapping in CLI modules that still support direct
  script execution

Cleanup direction:

- replace remaining mutable shared state with explicit result flow
- isolate compatibility loaders/helpers from the main scan pipeline
- remove CLI bootstrap code once direct-script execution requirements are clear

### 8. Legacy processing code still exists in-tree even though the supported surface is smaller

Removing the CLI flags and Tesseract classes reduced the supported surface, but
some legacy code remains in the tree:

- [src/wuwa_inventory_kamera/scraping/processing/echoes_processor.py](src/wuwa_inventory_kamera/scraping/processing/echoes_processor.py)
- [src/wuwa_inventory_kamera/scraping/processing/stats_extractor.py](src/wuwa_inventory_kamera/scraping/processing/stats_extractor.py)
- [src/wuwa_inventory_kamera/scraping/processing/echoesValidator.py](src/wuwa_inventory_kamera/scraping/processing/echoesValidator.py)
- older raw-scan helper code in [src/wuwa_inventory_kamera/scraping/utils/common.py](src/wuwa_inventory_kamera/scraping/utils/common.py)

Cleanup direction:

- either move the remaining old processing code under an explicit `legacy/`
  namespace or delete it once remaining callers are gone

### 9. Test coverage still does not enforce live/reprocess parity strongly enough

[tests/test_echo_reprocess.py](tests/test_echo_reprocess.py) and
[tests/test_echo_workflow.py](tests/test_echo_workflow.py) cover useful helper
and reprocess-specific behavior.

What is still missing is a test that asserts:

- the same underlying frame produces the same `EchoCapture` in live scan and in
  offline reprocess
- the same prepared single-echo input produces the same `EchoResult` regardless
  of whether it came from a live scan or a raw session reload
- unsupported resolutions fail loudly and intentionally

Without those checks, the duplicated builder logic can keep diverging quietly.

## Suggested Direction

The most useful cleanup target still looks like this:

1. `FrameSource` layer
   live app capture and raw-session replay both produce a full frame plus an
   explicit source color space and layout context.
2. `EchoCaptureBuilder` layer
   one pure module resolves ROIs, detects rarity, resolves level-dependent icon
   layout, and builds the canonical `EchoCapture`.
3. `RawSessionFormat` layer
   one explicit raw-session contract describes what the live path persists and
   what offline reprocess consumes.
4. `OcrService` layer
   consumes prepared captures, applies spec-driven OCR/caching, and does not own
   entry-point-specific reconstruction logic.
5. `EchoAssembler` layer
   converts OCR tokens plus capture metadata into `EchoResult`.

With that split, the live scan path and the reprocess path become thin wrappers
over the same single-echo preparation function and one explicit raw-session
format.

## Suggested Refactor Sequence

1. Extract a shared single-echo capture builder and make live scan + reprocess
   call it without changing behavior.
2. Choose one canonical raw-session format and align `_save_raw()`,
   `saveRawScan()`, `RawEchoScan`, and `loadRawScans()` around it.
3. Normalize the image color-space contract at the `EchoCapture` boundary and
   add parity tests.
4. Move shared helpers out of entry-point modules.
5. Replace mutable globals and legacy persistence helpers with explicit session
   results.
6. Quarantine or delete the remaining legacy processing modules and CLI
   bootstrap code.

## Remaining Open Questions

- Should offline reprocess reproduce the exact original capture state, or should
  it intentionally pick up future ROI and preprocessing improvements?
- Do we still need first-class support for old raw sessions that contain
  `sonata.png`, or should those be converted once and kept out of the main path?
- Do we want `OcrService` to remain echo-aware for name-recognition strategy, or
  should that policy move into a dedicated echo-name recognizer component?
- Do the CLI modules still need direct-script execution support, or can the
  remaining `sys.path` bootstrap be removed?