# Pipeline Maintainability Notes

## Scope

This document is intentionally broader than echoes, but the current pass starts
with the echo pipeline because it is the heaviest shared scan/reprocess path and
already exposes most of the architectural problems we will also care about in
other inventory surfaces.

The inspection for this pass focused on:

- [src/wuwa_inventory_kamera/scraping/scanning/echo_workflow.py](src/wuwa_inventory_kamera/scraping/scanning/echo_workflow.py)
- [src/wuwa_inventory_kamera/scraping/service/echo_reprocess.py](src/wuwa_inventory_kamera/scraping/service/echo_reprocess.py)
- [src/wuwa_inventory_kamera/cli/reprocess.py](src/wuwa_inventory_kamera/cli/reprocess.py)
- [src/wuwa_inventory_kamera/scraping/service/ocr_service.py](src/wuwa_inventory_kamera/scraping/service/ocr_service.py)
- [src/wuwa_inventory_kamera/scraping/service/assemblers/echo_assembler.py](src/wuwa_inventory_kamera/scraping/service/assemblers/echo_assembler.py)
- [src/wuwa_inventory_kamera/scraping/models/raw_scan.py](src/wuwa_inventory_kamera/scraping/models/raw_scan.py)
- [src/wuwa_inventory_kamera/scraping/utils/common.py](src/wuwa_inventory_kamera/scraping/utils/common.py)
- [src/wuwa_inventory_kamera/scraping/processing/echoes_processor.py](src/wuwa_inventory_kamera/scraping/processing/echoes_processor.py)
- [src/wuwa_inventory_kamera/scraping/ocr/region_specs.py](src/wuwa_inventory_kamera/scraping/ocr/region_specs.py)
- [src/wuwa_inventory_kamera/game/game_roi.py](src/wuwa_inventory_kamera/game/game_roi.py)
- [src/wuwa_inventory_kamera/game/screen_info.py](src/wuwa_inventory_kamera/game/screen_info.py)

This is not a full rewrite plan. It is a grounded list of maintainability
problems visible in the current code and a concrete record of the cleanup
decisions already made.

## Decisions Already Made

These are no longer open questions for the echo pipeline:

1. `wuwa-reprocess` is now an `OcrService`-only entry point.
   The legacy extractor mode, `--extractor`, `--use-bw`, and the Tesseract
   variants are no longer part of the supported reprocess surface.
2. Tesseract-specific code is being removed rather than preserved as a fallback.
   The codebase should assume RapidOCR plus the service pipeline going forward.
3. Old UI branches are being deleted.
   The game is a live service title, so compatibility code for superseded UI
   layouts is dead weight.
4. Only 1920x1080 and 1920x1200 are supported for the active inventory layout.
   Nearest-resolution scaling and the older resolution tables are intentionally
   gone for now.

These decisions are important because they let future cleanup work target the
real product surface instead of spending time preserving branches we have
already agreed not to keep.

## Current Shape

Today there are two active echo-processing flows:

1. Live app scan:
   [src/wuwa_inventory_kamera/scraping/scanning/echo_workflow.py](src/wuwa_inventory_kamera/scraping/scanning/echo_workflow.py)
   captures a full frame, crops a single echo into an `EchoCapture`, and submits
   it to [src/wuwa_inventory_kamera/scraping/service/ocr_service.py](src/wuwa_inventory_kamera/scraping/service/ocr_service.py).
2. Offline reprocess:
   [src/wuwa_inventory_kamera/cli/reprocess.py](src/wuwa_inventory_kamera/cli/reprocess.py)
   loads `RawEchoScan` data and
   [src/wuwa_inventory_kamera/scraping/service/echo_reprocess.py](src/wuwa_inventory_kamera/scraping/service/echo_reprocess.py)
   reconstructs an `EchoCapture` before sending it to the same `OcrService`.

There is still older RapidOCR-only processing code in
[src/wuwa_inventory_kamera/scraping/processing/echoes_processor.py](src/wuwa_inventory_kamera/scraping/processing/echoes_processor.py),
but it is no longer the supported reprocess entry path. That means the useful
shared seam is now clearly:

`EchoCapture -> OcrService -> EchoAssembler`

The remaining maintainability work is mostly about making both active paths meet
that seam in exactly the same way.

## Cleanup Applied In This Pass

The codebase has already been simplified in a few important ways:

- `wuwa-reprocess` no longer exposes legacy extractor selection.
- Tesseract-specific extractor classes, debug-tool options, and tests have been
  removed.
- `ScreenInfo` now rejects unsupported resolutions instead of scaling to the
  nearest known layout.
- The active echo scan and reprocess paths now assume the supported sonata icon
  layout shape directly, instead of branching for flat legacy coordinates.
- The ROI table keeps only 1920x1080 and 1920x1200 entries.

That cleanup does not solve the deeper architecture problem yet, but it removes
several dead surfaces that were obscuring it.

- live scan now reuses the min-level pre-read when that level OCR already
  succeeded, instead of issuing a second `echoes.level` OCR/cache lookup for
  the same selected echo during capture
- live scan and reprocess now share one neutral helper for level parsing and
  `level_X` vs `level_XX` sonata icon selection

## Issues Found

### 1. `EchoCapture` construction is still duplicated across live scan and reprocess

The live path and the reprocess path still rebuild the same single-echo data
bundle by hand.

Both paths currently do all of the following themselves:

- slice `echoCard`, `fullStatsName`, `fullStatsValue`, `echoName`, and sonata icon ROIs
- resolve the level-dependent sonata icon ROI variant, with live scan able to
  reuse the min-level pre-read and reprocess OCRing only once from the loaded
  frame
- resolve level-dependent icon circle metadata
- detect rarity from a single sampled pixel
- optionally write the same debug crop artifacts
- instantiate `EchoCapture`

The duplication is still most obvious between
[src/wuwa_inventory_kamera/scraping/scanning/echo_workflow.py](src/wuwa_inventory_kamera/scraping/scanning/echo_workflow.py)
and
[src/wuwa_inventory_kamera/scraping/service/echo_reprocess.py](src/wuwa_inventory_kamera/scraping/service/echo_reprocess.py).

This remains the main structural reason app scan and reprocess drift apart
whenever a crop rule, color assumption, or debug behavior changes.

Cleanup direction:

- extract one pure capture-builder function or class that takes a full frame,
  screen layout, and source metadata and returns the canonical `EchoCapture`
- let live scan own frame acquisition only
- let reprocess own frame loading only
- keep everything from crop selection onward shared

### 2. The image color-space contract is still implicit and inconsistent

The code still relies on channel-order knowledge that is spread out between
comments, helper names, and one-off conversions.

Examples:

- `EchoCapture` comments say `card`, `echo_name`, and `sonata_icon` are BGR,
  while `stats_name` and `stats_value` are RGB
- live scan rarity detection treats the captured frame as BGR
- `RawEchoScan.load_images()` converts disk images to RGB
- reprocess converts only `echo_name` back to BGR before submission
- `OcrService` converts stat crops from RGB back to BGR before spec-driven OCR

Even when behavior is technically correct, this contract is still hard to reason
about because it is not represented at one explicit boundary.

Cleanup direction:

- choose one canonical internal image space for `EchoCapture`
- make source color space explicit at acquisition/load boundaries
- add tests that compare live-built and reprocess-built captures for the same
  frame input

### 3. OCR region specs are still only half of the story

[src/wuwa_inventory_kamera/scraping/ocr/region_specs.py](src/wuwa_inventory_kamera/scraping/ocr/region_specs.py)
does centralize preprocessing and cache-signature rules, which is good.

But several decisions still sit outside the spec system:

- choosing `level_X` vs `level_XX` after a separate level OCR pass
- echo-name strategy switching inside `OcrService` between filtered single-line,
  thorough OCR, batch OCR, and raw fallback
- runtime construction of the echo-name allowed-character set from localized data

The old UI compatibility branches are gone, but the broader point remains:
"OCR spec" only means preprocessing and caching today, while important capture
and recognition policy still lives elsewhere.

Cleanup direction:

- keep `OcrRegionSpec` focused on OCR preprocessing and cache signatures
- introduce a separate capture/layout abstraction for dynamic ROI selection
- move echo-name recognition policy into a dedicated helper instead of embedding
  it inline in the generic echo batch processor

### 4. Shared low-level helpers still live in entry-point modules

Some pure helpers are still stored in the wrong place architecturally:

- reprocess still imports rarity helpers from
  [src/wuwa_inventory_kamera/scraping/scanning/echo_workflow.py](src/wuwa_inventory_kamera/scraping/scanning/echo_workflow.py)
- live workflow still imports debug-artifact writing from
  [src/wuwa_inventory_kamera/scraping/service/echo_reprocess.py](src/wuwa_inventory_kamera/scraping/service/echo_reprocess.py)

That creates reverse dependencies between modules that should only be top-level
orchestration entry points.

Cleanup direction:

- continue moving rarity helpers and shared crop-debug writing
  into a neutral module such as `scraping/service/echo_capture_utils.py`

### 5. Raw-session persistence still does not preserve the canonical prepared capture

The raw session format is still optimized around `full.png` plus metadata, not
around the actual `EchoCapture` that the service consumes.

That means reprocess must reconstruct the same derived data again later:

- crop the same regions again
- detect rarity again
- rerun level OCR again to resolve icon layout
- rebuild icon circle metadata again

This makes offline reprocess sensitive to later ROI logic changes instead of
faithfully replaying the capture state that the app had when the scan ran.

Cleanup direction:

- minimum option: persist resolved capture metadata alongside `full.png`
- stronger option: persist a versioned canonical capture bundle for each echo

### 6. The boundary between capture preparation, OCR orchestration, and assembly is still soft

`EchoCapture` currently carries a mix of:

- raw image crops
- derived metadata such as rarity and parsed level
- geometry metadata for sonata icon matching
- debug-only data such as full screenshots

Meanwhile:

- `OcrService` contains echo-specific recognition policy for the name crop
- `EchoAssembler` still reaches back into older modules for validators and
  compatibility helpers

The result is that responsibilities are not yet sharply separated.

Cleanup direction:

- split the pipeline into three clearer layers:
  1. capture preparation: full frame -> canonical prepared echo capture
  2. OCR execution: prepared capture -> OCR tokens by region
  3. assembly and validation: OCR tokens + derived metadata -> `EchoResult`

### 7. Legacy processing code still exists in-tree even though the product surface is smaller

Removing the CLI flags and Tesseract classes reduced the supported surface, but
some legacy code remains in the tree:

- [src/wuwa_inventory_kamera/scraping/processing/echoes_processor.py](src/wuwa_inventory_kamera/scraping/processing/echoes_processor.py)
- [src/wuwa_inventory_kamera/scraping/processing/stats_extractor.py](src/wuwa_inventory_kamera/scraping/processing/stats_extractor.py)
- compatibility shims under the project-root `scraping/` package

That is now a repository-organization problem rather than a supported-user-path
problem, which is better, but it is still noise for future work.

Cleanup direction:

- either move the remaining old processing code under an explicit `legacy/`
  namespace or delete it once remaining callers are gone

### 8. Test coverage still does not enforce live/reprocess parity strongly enough

[tests/test_echo_reprocess.py](tests/test_echo_reprocess.py) covers useful
reprocess-specific behavior, especially echo-name crop reconstruction and debug
artifact writing.

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
3. `OcrService` layer
   consumes prepared captures, applies spec-driven OCR/caching, and does not own
   entry-point-specific reconstruction logic.
4. `EchoAssembler` layer
   converts OCR tokens plus capture metadata into `EchoResult`.

With that split, the live scan path and the reprocess path become thin wrappers
over the same single-echo preparation function.

## Suggested Refactor Sequence

1. Extract a shared single-echo capture builder and make live scan + reprocess
   call it without changing behavior.
2. Normalize the image color-space contract at the `EchoCapture` boundary and add
   parity tests.
3. Move shared helpers out of entry-point modules.
4. Decide whether raw sessions should replay exact prepared captures or continue
   to rebuild them from `full.png`.
5. Collapse remaining cache duplication onto one OCR cache abstraction.
6. Quarantine or delete the remaining legacy processing modules.

## Remaining Open Questions

- Should offline reprocess reproduce the exact original capture state, or should
  it intentionally pick up future ROI and preprocessing improvements?
- Do we want `OcrService` to remain echo-aware for name-recognition strategy, or
  should that policy move into a dedicated echo-name recognizer component?
- Is the remaining RapidOCR-only legacy processing code worth keeping at all,
  or should it be deleted once shared-capture parity exists?