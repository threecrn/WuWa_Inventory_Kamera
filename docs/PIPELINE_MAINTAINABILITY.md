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
  The repository no longer preserves a separate sonata-only artifact.

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
  level-dependent sonata icon selection, source-space normalization, and
  `frame -> EchoCapture` construction.
- Root-level import shims are gone.
- The active live echo raw path no longer depends on any separately persisted
  sonata-only artifact.

That cleanup does not solve the deeper architecture problem yet, but it removes
several dead surfaces that were obscuring it.

## Issues Found

### 1. `EchoCapture` construction now shares one builder

Live scan and offline reprocess now route their single-echo crop assembly
through the same builder helper in
[src/wuwa_inventory_kamera/scraping/service/echo_capture_utils.py](src/wuwa_inventory_kamera/scraping/service/echo_capture_utils.py).

That helper now owns:

- slicing the canonical `echoCard`, `fullStatsName`, `fullStatsValue`,
  `echoName`, `equipped`, and sonata-icon crops
- resolving the level-dependent sonata icon ROI variant and circle metadata
- normalizing the per-field image spaces to the `EchoCapture` contract
- instantiating the final `EchoCapture`

The remaining entry-point-specific work is now narrower:

- live scan still owns frame acquisition and its capture-order-aware rarity helper
- reprocess still owns raw-session loading and its RGB rarity helper
- both entry points still opt into debug-artifact writing locally

### 2. The raw-session contract is split between the active v2 format and the legacy helper/model format

The active live raw path and the shared helper/model layer are now aligned.

Current state:

- `EchoWorkflow._save_raw()` writes `full.png` plus `meta.json`
- `RawEchoScan` and `loadRawScans()` use that same format
- `echoes_processor.py` derives sonata from icon matching on the full screenshot

That leaves one canonical raw-session shape in the codebase.

Cleanup direction:

- keep `_save_raw()`, `RawEchoScan`, and `loadRawScans()` aligned around the
  same format
- if very old sessions still matter, recover them outside the main path with a
  converter or one-off script

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

### 5. Shared low-level helpers now live in a neutral helper module

The remaining helper cross-imports between live scan and reprocess were removed
by introducing
[src/wuwa_inventory_kamera/scraping/service/shared_scan_helpers.py](src/wuwa_inventory_kamera/scraping/service/shared_scan_helpers.py).

That module now owns:

- pixel-rarity helpers used by echo/weapon live scan and raw-session reprocess
- shared crop-debug writers for region-level and echo-level OCR artifacts

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
[tests/test_echo_workflow.py](tests/test_echo_workflow.py) now cover useful
helper behavior, reprocess-specific behavior, and the shared
`frame -> EchoCapture` builder parity across BGR and RGB sources.

What is still missing is a test that asserts:

- the same prepared single-echo input produces the same `EchoResult` regardless
  of whether it came from a live scan or a raw session reload
- unsupported resolutions fail loudly and intentionally

Without those checks, live-vs-reprocess parity can still drift above the shared
builder boundary.

## Suggested Direction

The most useful cleanup target still looks like this:

1. `FrameSource` layer
   live app capture and raw-session replay both produce a full frame plus an
   explicit source color space and layout context.
2. `EchoCaptureBuilder` layer
  one pure module now resolves ROIs, level-dependent icon layout, and the
  canonical `EchoCapture`; remaining rarity/debug ownership can move here if
  that surface stays stable.
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

1. Add an end-to-end parity test that feeds the same prepared capture through
  both live-scan and raw-session assembly paths.
2. Normalize the image color-space contract at the `EchoCapture` boundary and
  extend parity coverage beyond crop construction.
3. Clarify ownership of validator logic still imported by `EchoAssembler` from
  legacy processing modules.
4. Replace mutable globals and legacy persistence helpers with explicit session
   results.
5. Quarantine or delete the remaining legacy processing modules and CLI
   bootstrap code.

## Remaining Open Questions

- Should offline reprocess reproduce the exact original capture state, or should
  it intentionally pick up future ROI and preprocessing improvements?
- If very old raw sessions still matter, should they be handled by an external
  converter instead of the main path?
- Do we want `OcrService` to remain echo-aware for name-recognition strategy, or
  should that policy move into a dedicated echo-name recognizer component?
- Do the CLI modules still need direct-script execution support, or can the
  remaining `sys.path` bootstrap be removed?