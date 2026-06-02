v2.0.0b1 — Release Draft (changes since v1.7.1)

**Overview**

This draft summarizes the notable changes merged since tag `v1.7.1`. It covers
new user-facing features, export/compatibility work, OCR/reprocess improvements,
asset and packaging changes, and key bug fixes and refactors. For detailed
schema and compatibility guidance see the output schema docs in `docs/`.

**Highlights**

- **New Exporter & CLI**: Added a WutheringTools export path and a `wuwa-exporter`
  CLI for producing the new exporter format. The exporter includes character
  talents and a conditional weapons refinement payload.
- **Serializer-first v1 compatibility**: Implemented a serializer layer to write
  v1-compatible standalone exports while preserving richer in-memory v2 data.
  See [docs/OUTPUT_SCHEMA_V1_1.md](docs/OUTPUT_SCHEMA_V1_1.md) and
  [docs/OUTPUT_SCHEMA_V1.md](docs/OUTPUT_SCHEMA_V1.md) for the schema details.
- **Inventory Viewer & UI**: Major inventory viewer work — grid/tile layouts for
  echoes, weapons, resources, and dev items; structured echo/character detail
  panes; result search bar; Export tab in UI; default Dark theme and several
  UX polish items (persist scan confirmation opt-out, wait cursor on slow ops).
- **OCR & Reprocess**: Substantial OCR preproc and region-spec improvements,
  caching for OCR captures, auto-invalidating OCR cache when specs change,
  multi-pass and upscaling options for higher accuracy, and improved
  reprocess CLI/workflow and round-trip tests.
- **Asset Updater & Game Assets**: Phase 1 game-asset syncing, sonata-family
  isolation, startup gating and asset repair CLI, and lazy viewer-driven
  downloads for game icons and other assets.
- **Packaging / Windows freeze**: Improved frozen Windows app packaging to
  include Python runtime DLLs and hardened cx_Freeze attempts; fixed freezing
  behavior around downloadables and config.

**Compatibility & Breaking Notes**

- **v1 export compatibility maintained**: The serializer-first approach attempts
  to preserve v1 export shapes for downstream consumers. The codebase documents
  the v1 and v1.1 shapes and the compatibility plan in docs.
- **Removed legacy compatibility outputs**: Several legacy compatibility
  artifacts were removed or made lazy-loaded (per-language compatibility
  outputs and sonata compatibility outputs). If downstream tooling relied on
  legacy compatibility bundles, update to read the canonical `scan_result.json`
  or the restored v1 standalone files emitted by the serializer.
- **Updater cache relocation**: The updater raw cache was moved into `data/raw`.
  Any scripts reading the old updater cache path should be updated.

**Notable Bugfixes & Quality Improvements**

- Preserve echo stat ordering and display casing for sonata names.
- Fixes for overlapping final inventory pages and grid layout clamping.
- Numerous scanning reliability fixes: character/weapon scan offsets,
  scaled-resolution ROI support, RGB capture mode fixes, and echo-level stop
  conditions to avoid unnecessary OCR.
- Hardening of the asset updater state, pruning, and audit flow.

**Developer / Testing**

- Many refactors to simplify imports, move CLI tools into `cli/`, and
  relocate/modernize region specs into package config.
- Added and reorganized tests (session-based tests, reprocess round-trip,
  focused inventory/viewer tests) and several architecture/migration docs
  (see `docs/ARCHITECTURE_V2.md`).

**Actions for integrators & release checklist**

- Verify downstream importers against `scan_result.json` and the v1-compatible
  standalone files. See [docs/OUTPUT_SCHEMA_V1_1.md](docs/OUTPUT_SCHEMA_V1_1.md).
- Test the new `wuwa-exporter` CLI and WutheringTools payload for any
  integration-specific expectations (weapon id shapes, talent payloads).
- Test frozen Windows build on supported Windows versions — packaging changed
  to include runtime DLLs.
- Run the new session-based tests and the reprocess round-trip tests locally.

**Where to read more**

- Export schema and compatibility plan: [docs/OUTPUT_SCHEMA_V1_1.md](docs/OUTPUT_SCHEMA_V1_1.md)
- Architecture and migration: [docs/ARCHITECTURE_V2.md](docs/ARCHITECTURE_V2.md)
- OCR preproc & region spec plans: [docs/OCR_PREPROCESSING_PLAN.md](docs/OCR_PREPROCESSING_PLAN.md)

--

This is a draft. I can expand any section with commit-level details, a
categorized changelog, or a proposed GitHub-style release description.
