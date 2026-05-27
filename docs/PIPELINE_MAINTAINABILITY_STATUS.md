# Pipeline Maintainability Status

## Scope

This document records the current maintainability status of the live v2
pipeline. It is a status note, not a migration proposal.

The live product surface is concentrated under `src/wuwa_inventory_kamera/`,
especially:

- [src/wuwa_inventory_kamera/scraping/scanning/session_orchestrator.py](src/wuwa_inventory_kamera/scraping/scanning/session_orchestrator.py)
- [src/wuwa_inventory_kamera/scraping/scanning/echo_workflow.py](src/wuwa_inventory_kamera/scraping/scanning/echo_workflow.py)
- [src/wuwa_inventory_kamera/scraping/scanning/weapon_workflow.py](src/wuwa_inventory_kamera/scraping/scanning/weapon_workflow.py)
- [src/wuwa_inventory_kamera/scraping/scanning/character_workflow.py](src/wuwa_inventory_kamera/scraping/scanning/character_workflow.py)
- [src/wuwa_inventory_kamera/scraping/scanning/achievement_workflow.py](src/wuwa_inventory_kamera/scraping/scanning/achievement_workflow.py)
- [src/wuwa_inventory_kamera/scraping/scanning/shell_workflow.py](src/wuwa_inventory_kamera/scraping/scanning/shell_workflow.py)
- [src/wuwa_inventory_kamera/scraping/service/captures.py](src/wuwa_inventory_kamera/scraping/service/captures.py)
- [src/wuwa_inventory_kamera/scraping/service/echo_capture_utils.py](src/wuwa_inventory_kamera/scraping/service/echo_capture_utils.py)
- [src/wuwa_inventory_kamera/scraping/service/echo_reprocess.py](src/wuwa_inventory_kamera/scraping/service/echo_reprocess.py)
- [src/wuwa_inventory_kamera/scraping/service/ocr_service.py](src/wuwa_inventory_kamera/scraping/service/ocr_service.py)
- [src/wuwa_inventory_kamera/scraping/service/assemblers/echo_assembler.py](src/wuwa_inventory_kamera/scraping/service/assemblers/echo_assembler.py)
- [src/wuwa_inventory_kamera/scraping/models/raw_scan.py](src/wuwa_inventory_kamera/scraping/models/raw_scan.py)
- [src/wuwa_inventory_kamera/scraping/utils/common.py](src/wuwa_inventory_kamera/scraping/utils/common.py)
- [src/wuwa_inventory_kamera/scraping/ocr/region_specs.py](src/wuwa_inventory_kamera/scraping/ocr/region_specs.py)
- [src/wuwa_inventory_kamera/config/ocr_region_specs.toml](src/wuwa_inventory_kamera/config/ocr_region_specs.toml)
- [src/wuwa_inventory_kamera/game/screen_info.py](src/wuwa_inventory_kamera/game/screen_info.py)
- [src/wuwa_inventory_kamera/config/app_config.py](src/wuwa_inventory_kamera/config/app_config.py)
- [src/wuwa_inventory_kamera/ui/home.py](src/wuwa_inventory_kamera/ui/home.py)
- [tests/test_echo_workflow.py](tests/test_echo_workflow.py)
- [tests/test_echo_reprocess.py](tests/test_echo_reprocess.py)

This note remains echo-heavy because echoes are still the only major inventory
surface that has both:

- a live scan path driven by game navigation
- an offline reprocess path driven by persisted raw screenshots

That is still the first place where capture drift, raw-session compatibility,
color-space contracts, and replay fidelity problems show up.

## Status Snapshot

The important current facts are:

1. The v2 pipeline is implemented and in use.
   `SessionOrchestrator` drives echoes, weapons, dev items/resources,
   characters, achievements, and shell through one shared `OcrService`.
2. `wuwa-reprocess` is a service-pipeline entry point.
   The supported offline path is `loadRawScans()` plus
   `reprocess_echo_scans_with_service()`, not the old extractor-selection
   surface.
3. Tesseract is not part of the supported OCR architecture.
   The active OCR stack is RapidOCR plus the spec-driven service layer.
4. Supported inventory layouts are intentionally narrow.
   `ScreenInfo` accepts only exact `1920x1080` and `1920x1200` layouts and
   raises for anything else.
5. OCR preprocessing and cache signatures are centralized.
   The main spec surface is
   [src/wuwa_inventory_kamera/config/ocr_region_specs.toml](src/wuwa_inventory_kamera/config/ocr_region_specs.toml)
   plus
   [src/wuwa_inventory_kamera/scraping/ocr/region_specs.py](src/wuwa_inventory_kamera/scraping/ocr/region_specs.py).
6. Root-level `scraping/` and `ui/` shim packages are gone.
   The remaining legacy burden now lives inside `src/wuwa_inventory_kamera/`,
   mostly under `scraping/processing/`, `scraping/utils/common.py`,
   `scraping/models/raw_scan.py`, and a few UI/CLI compatibility hooks.
7. New live echo raw sessions use the simpler v2 format.
  `EchoWorkflow._save_raw()` and `loadRawScans()` both use `full.png` plus
  `meta.json`, with sonata resolved from icon matching on the full frame.

## Current Echo Pipeline Shape

Today the active echo pipeline has three main layers.

### 1. Capture entry points

There are still two active ways to create an `EchoCapture`:

1. Live scan:
   [src/wuwa_inventory_kamera/scraping/scanning/echo_workflow.py](src/wuwa_inventory_kamera/scraping/scanning/echo_workflow.py)
   navigates the inventory grid, optionally pre-reads the current level for
   min-level early stop, captures a full frame, derives the echo-specific
   crops, and submits an `EchoCapture`. If raw saving is enabled, it persists
   `full.png` and `meta.json` for each echo.
2. Offline reprocess:
   [src/wuwa_inventory_kamera/cli/reprocess.py](src/wuwa_inventory_kamera/cli/reprocess.py)
   loads `RawEchoScan` sessions through `loadRawScans()`, and
   [src/wuwa_inventory_kamera/scraping/service/echo_reprocess.py](src/wuwa_inventory_kamera/scraping/service/echo_reprocess.py)
   rebuilds the crops from `full.png` before submitting the same
  `EchoCapture` type.

### 2. OCR service

[src/wuwa_inventory_kamera/scraping/service/ocr_service.py](src/wuwa_inventory_kamera/scraping/service/ocr_service.py)
is the shared OCR boundary.

For echoes it currently does all of the following:

- batches OCR work in a single service thread
- uses the generalized OCR cache
- applies region-spec preprocessing and signature generation
- runs the echo-name fallback policy inline
- converts stat crops back to BGR before spec-driven OCR
- forwards OCR tokens plus capture metadata into `EchoAssembler`

### 3. Assembly

[src/wuwa_inventory_kamera/scraping/service/assemblers/echo_assembler.py](src/wuwa_inventory_kamera/scraping/service/assemblers/echo_assembler.py)
still owns the final conversion from OCR tokens plus capture metadata into the
final `EchoResult`.

The effective seam is now clearly:

`frame -> EchoCapture -> OcrService -> EchoAssembler -> EchoResult`

What is still missing is one canonical shared builder for the
`frame -> EchoCapture` part and one canonical raw-session contract around it.

## Cleanup Already Landed

The older maintainability note is stale in a few important ways because some of
the previously proposed cleanup has already happened.

### Shared helper extraction has started

[src/wuwa_inventory_kamera/scraping/service/echo_capture_utils.py](src/wuwa_inventory_kamera/scraping/service/echo_capture_utils.py)
now holds shared helper logic for:

- `ensure_bgr_image`
- `decide_echo_level`
- `select_level_dependent_sonata_slot`

That means the live path and reprocess path no longer each carry their own
version of level parsing, level-width handling, and source-space normalization.

### Live scan avoids one redundant level OCR call

The live workflow now reuses the min-level pre-read when that OCR already ran
for early-stop filtering, instead of forcing a second `echoes.level` OCR lookup
for the same echo during capture preparation.

### Reprocess now normalizes the critical BGR surfaces explicitly

The reprocess path converts card, level, echo-name, and sonata-icon crops from
the raw session's RGB reloads back to BGR before feeding OCR signatures or icon
matching. That closes one of the biggest live-vs-reprocess drift sources the
older document called out.

### Root-level import shims have been removed

The project no longer carries root-level `scraping/` and `ui/` compatibility
packages. Active callers now import `wuwa_inventory_kamera.*` directly.

### Supported resolution handling is intentionally strict now

`ScreenInfo` and the ROI table only keep the exact supported layouts.
Nearest-resolution fallback is gone.

### Raw echo sessions now use one canonical format

The live echo workflow writes `full.png` and `meta.json` only, and
`loadRawScans()` reconstructs that same format. Sonata is derived from the
header icon on the saved full frame rather than from a separately persisted
crop.

### Focused regression tests now exist for the most drift-prone helper seams

The test suite now covers several echo-specific invariants that were missing
when this document was first written, including:

- level parsing and sonata-slot selection behavior
- BGR normalization for reprocess level OCR
- live capture reuse of prefetched level values
- debug artifact generation for the shared crop set
- rarity helpers for BGR- and RGB-ordered pixels

Those tests are not full parity tests yet, but they do cover several of the
smaller seams that used to drift silently.

## Remaining Issues

### 1. Shared `frame -> EchoCapture` builder is now in place

Live scan and reprocess now both build their canonical `EchoCapture` through
the shared helper in
[src/wuwa_inventory_kamera/scraping/service/echo_capture_utils.py](src/wuwa_inventory_kamera/scraping/service/echo_capture_utils.py).

The shared builder now owns:

- ROI slicing for the canonical single-echo crop set
- level-dependent sonata icon layout selection
- sonata icon circle metadata threading
- per-field color-space normalization
- `EchoCapture` instantiation

What still remains outside that builder is narrower and matches later cleanup
items:

- entry-point-specific rarity detection helpers
- debug-artifact writing ownership
- the mixed per-field color contract carried by `EchoCapture`

### 2. The `EchoCapture` color contract is still mixed

`EchoCapture` still mixes channel orders inside one DTO:

- `card`, `echo_name`, and `sonata_icon` are treated as BGR
- `stats_name` and `stats_value` are treated as RGB
- `OcrService` converts stat crops back to BGR before spec-driven OCR

That contract is more explicit than it used to be, but it is still not one
canonical internal image space.

Cleanup direction:

- either normalize all `EchoCapture` image fields to one space
- or make source-space metadata explicit enough that conversions are no longer
  implicit per field

### 3. OCR specs still do not own the full recognition policy

`OcrRegionSpec` centralizes preprocessing and cache-signature rules, but the
most echo-specific recognition behavior still lives inline in
`OcrService._process_echoes`.

That currently includes:

- runtime construction of the echo-name allowlist from localized data
- plausibility guards for echo-name OCR output
- the multi-strategy fallback chain for the echo-name crop
- echo-name-specific cache lookup and acceptance policy

Cleanup direction:

- keep region specs focused on preprocessing and signatures
- extract echo-name recognition policy into a dedicated helper or component

### 4. Entry-point reverse dependencies are gone for the shared scan helpers

The last helper-level reverse dependencies between live scan and reprocess were
removed by introducing
[src/wuwa_inventory_kamera/scraping/service/shared_scan_helpers.py](src/wuwa_inventory_kamera/scraping/service/shared_scan_helpers.py).

That shared module now owns:

- pixel-rarity helpers for BGR, RGB, and capture-order tolerant sampling
- shared debug-artifact writers for region-level and echo-level OCR crops

### 5. Raw-session contract is now unified

The active live echo workflow, `RawEchoScan`, `loadRawScans()`, and the legacy
processor now all describe the same raw-session contract.

Current state:

- `EchoWorkflow._save_raw()` writes `full.png` plus `meta.json`
- `RawEchoScan` persists and reloads only the full-frame capture plus metadata
- `loadRawScans()` reconstructs that format directly
- `scraping/processing/echoes_processor.py` derives sonata from the header icon
  inside the full screenshot

That removes the older competing raw-session shape from the main codebase.

Cleanup direction:

- choose one canonical raw-session format for the active pipeline
- either convert older sessions at the boundary or quarantine that support as a
  legacy-only path

### 6. Echo validation now sits under the service layer

`EchoAssembler` now imports validators from
`scraping/service/echo_validation.py`.

The older `scraping/processing/echoesValidator.py` module is now only a
compatibility wrapper for legacy imports, so the active assembler boundary no
longer depends on processing-owned validator logic.

Cleanup direction:

- keep deleting direct imports of the compatibility wrapper as legacy callers move
- remove the wrapper entirely once the remaining legacy processing imports are gone

### 7. Shared scan-state globals and CLI bootstrap are gone

The active package no longer relies on package-global scan-result state or
inline CLI path mutation.

Current state:

- `config/app_config.py` now exposes configuration only
- `savingScraped()` persists explicit caller-provided data
- `ui/home.py` keeps manual-recognition queue state on the widget instance
- supported CLI invocation is package mode only: console scripts or `python -m`

Remaining cleanup direction:

- keep compatibility loaders isolated from the core scan and OCR path
- keep evaluating whether the manual-recognition UI still belongs in the active
  v2 product surface

### 8. Test coverage still does not enforce full live/reprocess parity

The current tests cover helper parity slices and some reprocess specifics, but
they still do not assert:

- that the same full frame produces the same prepared `EchoCapture` in live
  scan and offline reprocess
- that the same prepared single-echo input produces the same `EchoResult`
  regardless of origin
- that unsupported resolutions fail loudly and intentionally

Cleanup direction:

- add explicit capture-parity tests
- add end-to-end service/assembler parity tests
- add a focused `ScreenInfo` failure test for unsupported layouts

### 8. Test coverage is better, but it still does not enforce full live/reprocess parity

[tests/test_echo_workflow.py](tests/test_echo_workflow.py) and
[tests/test_echo_reprocess.py](tests/test_echo_reprocess.py) now cover several
important shared helper behaviors, including parity for the shared
`frame -> EchoCapture` builder across BGR and RGB inputs.

What is still missing is a test that proves, end-to-end, that:

- the same prepared capture produces the same `EchoResult` regardless of source path
- unsupported resolutions fail under an explicit regression test, not only by implementation contract

Without those tests, parity can still drift above the shared builder boundary.

## Suggested Direction

The most useful remaining cleanup path still looks incremental rather than a
large rewrite:

1. Move the remaining shared rarity/debug helpers out of the entry-point
  modules and into the same neutral capture-helper surface.
2. Decide whether `EchoCapture` should keep a mixed per-field color contract or
  normalize to a single internal image space.
3. If echo-name OCR policy keeps growing, split it out of
  `OcrService._process_echoes` into a dedicated recognizer helper.
4. Decide whether raw sessions should replay the exact prepared capture state or
  intentionally rebuild from `full.png` using the latest logic.
6. Quarantine or delete the remaining legacy processing and shim modules once
  downstream callers are gone.

## Remaining Open Questions

- Should offline reprocess aim to replay the exact original prepared capture,
  or should it intentionally pick up future ROI/spec improvements?
- Is the current mixed-color `EchoCapture` contract acceptable long-term if it
  stays explicit, or is the conversion churn already too error-prone?
- Does echo-name recognition policy belong in `OcrService`, or should it become
  an echo-specific component next to `EchoAssembler`?