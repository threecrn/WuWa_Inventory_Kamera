# Legacy & Compatibility Cleanup Plan

Scan date: 2026-05-19.  
Completed items are kept below as historical notes; remaining work is grouped
by priority: **High** (live-path format or ownership debt) > **Medium**
(reachable legacy compatibility) > **Low** (design debt or optional cleanup).

---

## Completed: Phantom `_preprocess_scaled_plane` call

**Status:** Resolved in code on 2026-05-14; the crash path tied to
`sig_from_preprocessed` has been removed.

`_preprocess_for_signature` no longer calls a deleted helper, and the old
`sig_from_preprocessed` TOML field has been removed.

---

## Completed: Remove the `EchoOcrCache` stack

**Status:** Resolved in code on 2026-05-14.

The dead `_ocr_images_with_cache` path in `ocr_service.py` was removed along
with the dedicated echo-stat cache wiring.

### What was removed
- `_ocr_images_with_cache` from `ocr_service.py`
- the `--echo-stat-cache` CLI/config/UI plumbing
- `scraping/service/echo_ocr_cache.py`
- `tests/test_echo_ocr_cache.py`

---

## Completed: Remove root-level shim packages

**Status:** Resolved in code on 2026-05-14.

The root-level `scraping/` and `ui/` compatibility trees were deleted and the
remaining callers were migrated to `wuwa_inventory_kamera.*` imports.

### Note
The root-level `updater/` directory still exists because it contains
Qt-dependent compatibility subclasses rather than pure re-export shims.

---

## Completed: Remove the stale `ocr_cache.py` backward-compat comment

**Status:** Resolved in code.

The comment claiming a backward-compat re-export is gone. `ImageOcrResult`
still exists, but only as the internal result type alias used by `OcrCache`
itself.

---

## H-1: Align raw echo session persistence around one canonical format

The active live echo workflow, the shared raw-scan model, and the offline
load/reprocess path now all describe the same raw-session shape.

### Current state
- `EchoWorkflow._save_raw()` writes `full.png` plus `meta.json`.
- `loadRawScans()` reconstructs that same format.
- `RawEchoScan` carries only the full-frame persistence state needed for
  replay.
- `scraping/processing/echoes_processor.py` now derives sonata from icon
  matching on the full screenshot.

### Follow-up
If very old raw sessions still matter, handle them with a one-off converter or
external recovery script instead of reintroducing main-path compatibility.

---

## M-1: Legacy sonata scan branch in `echoes_processor.py`

**File:** `src/.../scraping/processing/echoes_processor.py`

The processor now resolves sonata exclusively from the header icon in the full
capture. The old compatibility branch and its raw-session fields have been
removed.

---

## Completed: Remove legacy-only echo fallback branches

**Status:** Resolved in code on 2026-05-27.

The active live scan path and the raw-session reprocess path now both submit
`EchoCapture` objects with the dedicated `echo_name` and `level` crops.

### What changed
- `ocr_service.py` no longer falls back to batch OCR on the full card when an
  echo-name ROI is missing.
- `echo_assembler.py` no longer parses levels from legacy card-text lines.
- `OcrService` now treats the dedicated level ROI as the only recovery surface:
  it makes one last dedicated-ROI recovery attempt before assembly and rejects
  the echo if that ROI still produces no digits.

---

## M-4: `app_config.py` mutable global state (`INVENTORY`, `FAILED`)

```python
INVENTORY: dict = {'items': {}, 'date': ''}
FAILED: list = []
```

These module-level globals are still imported by `ui/home.py` and by
`scraping/utils/common.py`. `savingScraped()` also captures `INVENTORY['items']`
in its default argument, which is a Python footgun.

### Plan
Pass scan results explicitly via return values or callbacks instead of mutating
shared module state. Rewrite `savingScraped()` to avoid the default argument
capturing global mutable data.

---

## M-5: Entry-point helper cross-imports between live scan and reprocess

Resolved by introducing
`src/wuwa_inventory_kamera/scraping/service/shared_scan_helpers.py`.

Current ownership:

- pixel-rarity helpers now live in the shared helper module and are imported by
  both live-scan and reprocess entry points
- shared region-debug and echo-debug artifact writers now live in the same
  helper module, so live scan no longer reaches into `echo_reprocess.py`

---

## M-6: CLI project-root bootstrap still exists in a couple of modules

Current occurrences:

- `src/wuwa_inventory_kamera/cli/reprocess.py`
- `src/wuwa_inventory_kamera/cli/detect_sonata_icon.py`

Both still prepend the project root to `sys.path` to support direct-script
execution.

### Plan
Decide whether direct execution of these modules outside installed/package mode
is still a supported workflow. If not, remove the bootstrap. If it is, isolate
it to one small compatibility wrapper instead of keeping it inline in each CLI
module.

---

## M-7: `echo_assembler.py` still depends on the legacy validator module

`EchoAssembler` imports `infer_cost`, `expected_sub_count`, and
`validate_echo_stats` from `scraping/processing/echoesValidator.py`.

### Plan
Either move those validators under the service/assembler surface or explicitly
quarantine `echoesValidator.py` as an intentional legacy dependency.

---

## L-1: `_rapidocr.py` `fallback_text_score` second OCR pass

`RapidOcrBackend` still constructs a second `RapidOCR()` instance
(`self._fallback_ocr`) with a lower `text_score` threshold and merges its
results in `thorough_recognize()`.

### Plan
1. Determine empirically whether the fallback pass improves real scan results.
2. If the improvement is negligible, set `fallback_text_score=None` for the DML
   path and remove the fallback-OCR branch.

---

## L-2: `region_specs.py` still labels parts of the render/signature flow as a "legacy path"

Current comments include:

```python
# 3. Render for OCR (currently legacy path, will expand)
# 5. Signature image (legacy path)
```

This is not dead code, but it signals unresolved design intent in the OCR spec
pipeline.

### Plan
Either update the comments to describe the current implementation plainly, or
continue the color-aware rendering work and remove the "legacy path" wording.

---

## L-3: `region_specs.py` still accepts the `sig_downscale` TOML alias

`_build_region_spec_from_dict()` still reads `sig_downscale` as a compatibility
alias for `signature.post_downscale`.

### Current audit result
No current TOML entries under `src/wuwa_inventory_kamera/config/` use
`sig_downscale`.

### Plan
Remove the alias loader and its compatibility diagnostics once there is no need
to preserve older TOML files.

---

## Suggested Work Order

```text
M-5/M-6/M-7  ->  M-4  ->  L-*
```

The remaining M-* items are local ownership and dependency cleanups now that
the raw-session boundary and the echo-specific compatibility branches have been
collapsed. L-* items are still deferrable.
