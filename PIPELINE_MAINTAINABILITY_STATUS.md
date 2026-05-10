# Pipeline Maintainability Notes

## Scope

This document now records the current maintainability status of the active V2
pipeline, not a proposed migration plan.

The V2 stack under `src/wuwa_inventory_kamera/` is the live product surface:

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
- [src/wuwa_inventory_kamera/scraping/ocr/region_specs.py](src/wuwa_inventory_kamera/scraping/ocr/region_specs.py)
- [src/wuwa_inventory_kamera/config/ocr_region_specs.toml](src/wuwa_inventory_kamera/config/ocr_region_specs.toml)
- [src/wuwa_inventory_kamera/game/screen_info.py](src/wuwa_inventory_kamera/game/screen_info.py)
- [tests/test_echo_workflow.py](tests/test_echo_workflow.py)
- [tests/test_echo_reprocess.py](tests/test_echo_reprocess.py)

This document is still echo-heavy because echoes remain the most complicated
surface from a maintainability perspective. They are the only major inventory
surface that currently has both:

- a live scan path driven by game navigation
- an offline reprocess path driven by persisted raw screenshots

That makes the echo pipeline the main place where capture drift, color-space
contracts, and replay fidelity problems show up first.

## Status Snapshot

The important status-quo facts are now:

1. The V2 pipeline is implemented and in use.
  `SessionOrchestrator` drives echoes, weapons, items, characters,
  achievements, and shell through one shared `OcrService`.
2. `wuwa-reprocess` is an `OcrService` path.
  The supported reprocess entry point is the service pipeline plus
  assemblers, not the old extractor-selection surface.
3. Tesseract is no longer part of the supported OCR architecture.
  The active OCR path is RapidOCR plus the service layer.
4. Supported inventory layouts are intentionally narrow.
  `ScreenInfo` now accepts only exact `1920x1080` and `1920x1200` layouts and
  raises for anything else.
5. OCR preprocessing and cache signatures are centralized.
  The main spec surface is
  [src/wuwa_inventory_kamera/config/ocr_region_specs.toml](src/wuwa_inventory_kamera/config/ocr_region_specs.toml)
  plus
  [src/wuwa_inventory_kamera/scraping/ocr/region_specs.py](src/wuwa_inventory_kamera/scraping/ocr/region_specs.py).
6. Legacy processing code still exists in-tree, but it is not the main product
  surface anymore.
  The active implementation lives under `src/wuwa_inventory_kamera/`, while
  older `scraping/` modules and `scraping.restored/` are now mainly a
  compatibility and reference burden.

## Current Echo Pipeline Shape

Today the active echo pipeline has three main layers.

### 1. Capture entry points

There are still two active ways to create an `EchoCapture`:

1. Live scan:
  [src/wuwa_inventory_kamera/scraping/scanning/echo_workflow.py](src/wuwa_inventory_kamera/scraping/scanning/echo_workflow.py)
  navigates the inventory grid, optionally pre-reads the current level for
  min-level early stop, captures a full frame, derives the echo-specific crops,
  and submits an `EchoCapture`.
2. Offline reprocess:
  [src/wuwa_inventory_kamera/cli/reprocess.py](src/wuwa_inventory_kamera/cli/reprocess.py)
  loads `RawEchoScan` sessions, and
  [src/wuwa_inventory_kamera/scraping/service/echo_reprocess.py](src/wuwa_inventory_kamera/scraping/service/echo_reprocess.py)
  rebuilds the crops from the stored `full.png` frame before submitting the
  same `EchoCapture` type.

### 2. OCR service

[src/wuwa_inventory_kamera/scraping/service/ocr_service.py](src/wuwa_inventory_kamera/scraping/service/ocr_service.py)
is the shared OCR boundary.

For echoes it currently does all of the following:

- batches OCR work in a single service thread
- uses the generalized OCR cache and the legacy echo-stat cache shim
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
`frame -> EchoCapture` part.

## Cleanup Already Landed

The original maintainability note is stale in a few important ways because some
of the previously proposed cleanup has already happened.

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

### Reprocess now normalizes critical BGR surfaces explicitly

The reprocess path converts card, level, echo-name, and sonata-icon crops from
the raw session's RGB reloads back to BGR before feeding OCR signatures or icon
matching. That closes one of the biggest live-vs-reprocess drift sources that
the older document called out.

### Tiny level-badge OCR is now more intentionally configured

`echoes.level` is now treated as a single-line ROI with explicit resize tuning
and transient caching in the OCR spec, which better matches the real shape of
the badge crop than the earlier generic OCR assumptions did.

### Focused regression tests now exist for helper parity slices

The test suite now covers several echo-specific invariants that were missing
when this document was first written, including:

- level parsing and sonata-slot selection behavior
- BGR normalization for reprocess level OCR
- live capture reuse of prefetched level values
- debug artifact generation for the shared crop set
- rarity helpers for BGR and RGB ordered pixels

Those tests are not full parity tests yet, but they do cover some of the
smaller drift-prone seams that used to be unguarded.

## Remaining Issues

### 1. `EchoCapture` construction is still duplicated, just less than before

The level helpers are shared now, but live scan and reprocess still each build
the `EchoCapture` by hand.

Both paths still locally decide all of the following:

- which ROIs to slice from the full frame
- how to package debug artifacts
- when and how to instantiate `EchoCapture`
- where to source rarity detection
- how to thread capture-specific metadata into the service

The most obvious duplication remains between:

- [src/wuwa_inventory_kamera/scraping/scanning/echo_workflow.py](src/wuwa_inventory_kamera/scraping/scanning/echo_workflow.py)
- [src/wuwa_inventory_kamera/scraping/service/echo_reprocess.py](src/wuwa_inventory_kamera/scraping/service/echo_reprocess.py)

This is still the main place where future crop or metadata changes can diverge.

Cleanup direction:

- extract one shared `EchoCaptureBuilder` or pure builder function
- let live scan own frame acquisition only
- let reprocess own frame loading only
- keep crop resolution onward identical

### 2. The `EchoCapture` color contract is more explicit, but still mixed

The current code is clearer than before, but `EchoCapture` still mixes channel
orders inside one DTO:

- `card`, `echo_name`, and `sonata_icon` are treated as BGR
- `stats_name` and `stats_value` are treated as RGB
- `OcrService` converts stat crops back to BGR before spec-driven OCR

That is a workable contract, and it is now documented in code and helper usage,
but it is still not one canonical internal image space.

Cleanup direction:

- either normalize all `EchoCapture` image fields to one space
- or make source-space metadata explicit enough that conversions are no longer
  implicit per field

### 3. OCR specs still do not own the full recognition policy

`OcrRegionSpec` does a good job centralizing preprocessing and cache-signature
rules, but the most echo-specific recognition behavior still lives inline in
`OcrService._process_echoes`.

That currently includes:

- runtime construction of the echo-name allowlist from localized data
- plausibility guards for echo-name OCR output
- the multi-strategy fallback chain for the echo-name crop
- echo-name-specific cache lookup and acceptance policy

So the codebase has centralized image preprocessing, but not a cleanly isolated
echo-name recognizer.

Cleanup direction:

- keep region specs focused on preprocessing and signatures
- extract echo-name recognition policy into a dedicated helper or component

### 4. Entry-point reverse dependencies remain, but only in two narrow places

The old document overstated this issue. Most of the shared low-level logic is
no longer trapped in the entry-point modules.

The remaining reverse dependencies are now specifically:

- reprocess still imports `_rarity_from_rgb_pixel` from
  [src/wuwa_inventory_kamera/scraping/scanning/echo_workflow.py](src/wuwa_inventory_kamera/scraping/scanning/echo_workflow.py)
- live workflow still imports `_write_echo_debug_artifacts` from
  [src/wuwa_inventory_kamera/scraping/service/echo_reprocess.py](src/wuwa_inventory_kamera/scraping/service/echo_reprocess.py)

This is still worth cleaning up, but it is now a narrow helper-placement issue,
not a broad architectural tangle.

Cleanup direction:

- move shared rarity and debug-artifact helpers into a neutral module alongside
  the existing capture helpers

### 5. Raw-session persistence still replays from `full.png`, not from a canonical prepared capture

`RawEchoScan` still persists the raw session around the full-frame screenshot
plus metadata, then asks reprocess to rebuild the actual prepared capture state
later.

That means reprocess still has to:

- crop the same regions again
- sample rarity again
- rerun level OCR again
- rebuild sonata icon metadata again

This is useful if the goal is to benefit from later ROI or OCR improvements,
but it is not a faithful replay of the exact prepared capture state the live
scan originally used.

Cleanup direction:

- minimum option: persist resolved capture metadata next to `full.png`
- stronger option: persist a versioned prepared-capture bundle per echo

### 6. The assembly layer still reaches back into older processing modules

`EchoAssembler` still imports validators from the older processing package, and
the legacy processing modules still remain nearby in-tree.

That means the V2 assembler boundary is not fully self-contained yet.

Cleanup direction:

- either move the remaining validator logic under the service/assembler surface
- or explicitly quarantine it as a stable legacy dependency instead of leaving
  ownership ambiguous

### 7. Legacy processing and shim code still add search and ownership noise

The following still exist in parallel with the active V2 surface:

- [src/wuwa_inventory_kamera/scraping/processing/echoes_processor.py](src/wuwa_inventory_kamera/scraping/processing/echoes_processor.py)
- [src/wuwa_inventory_kamera/scraping/processing/stats_extractor.py](src/wuwa_inventory_kamera/scraping/processing/stats_extractor.py)
- project-root `scraping/` compatibility modules
- `scraping.restored/`

This is not a user-path problem anymore, but it is still a maintenance problem
because it makes ownership and code search noisier than they need to be.

Cleanup direction:

- move the remaining legacy code under an explicit `legacy/` namespace
- or delete it once compatibility callers are gone

### 8. Test coverage is better, but it still does not enforce full live/reprocess parity

[tests/test_echo_workflow.py](tests/test_echo_workflow.py) and
[tests/test_echo_reprocess.py](tests/test_echo_reprocess.py) now cover several
important shared helper behaviors.

What is still missing is a test that proves, end-to-end, that:

- the same frame produces the same `EchoCapture` regardless of live vs raw-session source
- the same prepared capture produces the same `EchoResult` regardless of source path
- unsupported resolutions fail under an explicit regression test, not only by implementation contract

Without those tests, the remaining duplicated capture-builder logic can still
drift quietly.

## Suggested Direction

The most useful remaining cleanup path still looks incremental rather than a
large rewrite:

1. Extract one shared `frame -> EchoCapture` builder and put both live scan and
  reprocess behind it.
2. Move the remaining shared rarity/debug helpers out of the entry-point
  modules and into the same neutral capture-helper surface.
3. Decide whether `EchoCapture` should keep a mixed per-field color contract or
  normalize to a single internal image space.
4. If echo-name OCR policy keeps growing, split it out of
  `OcrService._process_echoes` into a dedicated recognizer helper.
5. Decide whether raw sessions should replay the exact prepared capture state or
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