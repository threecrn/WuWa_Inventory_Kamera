# To-Do List

## Current State Snapshot

- [x] The 3.3 new-UI OCR baseline is live via `spec_version = "3.3-new-ui-v2"`.
- [x] The v2 live scan path exists for echoes, weapons, characters, achievements, and shell through `SessionOrchestrator`.
- [x] Echo validation and export metadata improvements have landed.
  - [x] Sonata names are validated against localized data.
  - [x] Echo exports include `_scanIndex`, `_monsterId`, and `_cost`.
  - [x] Incomplete substat-line reads warn instead of silently passing.
- [x] Navigation and workflow regression coverage now exists for screenshot capture and scan workflow slices.
- [x] `ScreenInfo` currently supports scaled same-aspect-ratio layouts for 16:9 and 8:5, in addition to the base 1920x1080 and 1920x1200 layouts.

## Active Priorities

### High

- [x] Align raw echo session persistence around one canonical format.

### Medium

- [ ] Collapse duplicated `EchoCapture` construction between live scan and reprocess into one shared builder/helper.
- [x] Remove legacy-only echo fallback branches after the raw-session format is settled.
  - [x] `ocr_service.py`: drop the no-dedicated-`echo_name` fallback if all active captures always provide that ROI.
  - [x] `echo_assembler.py`: drop the legacy level-text fallback if all active captures always provide `detected_level`.
- [ ] Move shared helpers out of entry-point modules.
  - [ ] Relocate rarity helpers and shared debug-artifact helpers into a neutral module.
- [ ] Clarify ownership of validator logic still imported by `EchoAssembler` from legacy processing modules.
- [ ] Remove mutable global scan-result state from `app_config.py` / `scraping.utils.common`.
- [ ] Decide whether direct-script CLI bootstrap via `sys.path` patching is still a supported workflow.

### Low

- [ ] Re-evaluate the extra RapidOCR fallback pass (`fallback_text_score`) and remove it if it has no measurable value.
- [ ] Finish OCR region-spec cleanup.
  - [ ] Remove the `sig_downscale` compatibility alias if older TOML files no longer need it.
  - [ ] Replace remaining `legacy path` comments in `region_specs.py` with plain descriptions of the current behavior.

## Backlog

- [ ] Localization: multi-language support for sonata names, achievement names, character names, etc.
  - [ ] separate canonical names for echoes, characters, etc from English localized strings
  - [ ] directory layout and strategy for managing multi-language data
  - [ ] validation of OCR results against localized data (e.g. sonata names)
    - [ ] geometry validation: are RoI sizes and positions compatible with the expected text lengths of localized strings?
    - [ ] signature validation: do the OCR cache signature setups work for localized strings with different character sets, draw widths, etc?
- [ ] minor issues on edge cases:
  - [ ] at 2560x1440, the echo level signature configuration doesn't work properly because the key text color is slightly off
  - [ ] on slow systems, wait delays might be too short for UI animations to complete, causing OCR failures
- [ ] RapidOCR CPU backend: multi-threaded batch processing for faster CPU inference?
- [ ] end-user installation: where to put config and cache files?
- [ ] fork repo: how to deal with references to the original (e.g. feedback link), README.md, etc?

## Docs And Consistency

- [ ] Reconcile resolution-support documentation with code and tests.
  - [ ] Docs still say only exact 1920x1080 / 1920x1200 are supported.
  - [ ] Code and tests currently support scaled same-aspect-ratio 16:9 and 8:5 layouts.
- [ ] Refresh docs that still describe compatibility-only or pre-v2 behavior as if it were the active path.
