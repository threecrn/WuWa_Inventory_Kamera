# To-Do List

## Current Baseline

- The 3.3 new-UI OCR baseline is live via `spec_version = "3.3-new-ui-v2"`.
- The v2 live scan path exists for echoes, weapons, characters, achievements, and shell through `SessionOrchestrator`.
- Echo validation and export metadata improvements have landed.
  - [x] Sonata names are validated against localized data.
  - [x] Echo exports include `_scanIndex`, `_monsterId`, and `_cost`.
  - [x] Incomplete substat-line reads warn instead of silently passing.
- Navigation and workflow regression coverage now exists for screenshot capture and scan workflow slices.
- `ScreenInfo` currently supports scaled same-aspect-ratio layouts for 16:9 and 8:5, in addition to the base 1920x1080 and 1920x1200 layouts.

## Near-Term Follow-Up

- [ ] Re-evaluate the extra RapidOCR fallback pass (`fallback_text_score`) and remove it if it has no measurable value.
- [ ] Finish OCR region-spec cleanup.
  - [x] Remove the `sig_downscale` compatibility alias if older TOML files no longer need it.
  - [x] Replace remaining `legacy path` comments in `region_specs.py` with plain descriptions of the current behavior.
- [ ] Reconcile resolution-support documentation with code and tests.
  - [ ] Docs still say only exact 1920x1080 / 1920x1200 are supported.
  - [ ] Code and tests currently support scaled same-aspect-ratio 16:9 and 8:5 layouts.
- [ ] Refresh docs that still describe compatibility-only or pre-v2 behavior as if it were the active path.

## Product And Workflow Backlog

- [ ] Recognize and handle the "Discard echo" popup notice when opening the inventory.
- [ ] Decide what to do with the inventory page of the app: implement it or remove it.
  - [x] Make a plan for how to implement a viewer for the different result type jsons.
    - See `docs/INVENTORY_VIEWER_PLAN.md`.
- [ ] Grid scroll toward the bottom for weapon, echo, and item tabs: predict the end of the page and handle it correctly.
- [ ] Windowed game support: keep command-line and config behavior consistent with autodetection.

## Data And Localization Backlog

- [ ] Game data and localization.
  - [ ] Separate canonical names for echoes, characters, and other entities from English localized strings.
  - [ ] Extend the game-data format. `echoes.json` is currently a dict of normalized localized echo name to echo id.
  - [ ] Localization: multi-language support for sonata names, achievement names, character names, and other user-facing labels.
    - [ ] Directory layout and strategy for managing multi-language data.
    - [ ] Validation of OCR results against localized data (for example sonata names).
      - [ ] Geometry validation: are RoI sizes and positions compatible with the expected text lengths of localized strings?
      - [ ] Signature validation: do the OCR cache signature setups work for localized strings with different character sets, draw widths, and related variations?

## Reliability And Performance Backlog

- [x] Mixed scan bug: When doing a echo+weapon live scan, the weapon scan fails because at the end of the echo scan, the control leaves the tab menu and ESC to the main game screen, so the weapon scan starts on the wrong screen and fails to find the expected UI elements.
  - [x] Fix: reset between chained scrapers through `GameNavigator` so inventory state stays in sync when `Esc` closes the previous panel.
- [ ] Minor issues on edge cases.
  - [ ] At 2560x1440, the echo level signature configuration does not work properly because the key text color is slightly off.
  - [ ] On slow systems, wait delays might be too short for UI animations to complete, causing OCR failures.
    - [ ] Add a `slow-system` option that adds extra delays and maybe disables some optimizations to improve reliability on lower-end hardware.
- [ ] RapidOCR CPU backend: multi-threaded batch processing for faster CPU inference?

## Packaging And Repo Backlog

- [ ] End-user installation: where should config and cache files live?
- [ ] Fork repo: how should references to the original project (for example the feedback link and `README.md`) be handled?

## Recently Completed

- [x] Investigate failing tests.
- [x] Align raw echo session persistence around one canonical format.
- [x] Collapse duplicated `EchoCapture` construction between live scan and reprocess into one shared builder/helper.
- [x] Remove legacy-only echo fallback branches after the raw-session format was settled.
  - [x] `ocr_service.py`: drop the no-dedicated-`echo_name` fallback if all active captures always provide that ROI.
  - [x] `echo_assembler.py`: drop the legacy level-text fallback if all active captures always provide `detected_level`.
- [x] Move shared helpers out of entry-point modules.
  - [x] Relocate rarity helpers and shared debug-artifact helpers into a neutral module.
- [x] Clarify ownership of validator logic still imported by `EchoAssembler` from legacy processing modules.
- [x] Remove mutable global scan-result state from `app_config.py` and `scraping.utils.common`.
- [x] Decide whether direct-script CLI bootstrap via `sys.path` patching is still a supported workflow.
  - [x] Direct file-path execution is no longer a supported CLI mode; use console scripts or `python -m wuwa_inventory_kamera.cli...`.
