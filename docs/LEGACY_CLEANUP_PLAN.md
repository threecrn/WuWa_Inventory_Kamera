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

The active live echo workflow and the older helper/model layer now describe two
different raw-session shapes.

### Current state
- `EchoWorkflow._save_raw()` writes `full.png` plus `meta.json`.
- `saveRawScan()` in `scraping/utils/common.py` still writes `full.png` plus
  `sonata.png` plus `meta.json`.
- `RawEchoScan` still carries `sonata_screenshot` and `sonata_path`.
- `loadRawScans()` accepts optional `sonata.png`.
- `scraping/processing/echoes_processor.py` still contains the old separate
  `sonata.png` branch.
- `saveRawScan()` currently has no active call sites in the live v2 path.

### Plan
1. Decide whether old raw sessions containing `sonata.png` still need first-class support.
2. If not, remove `saveRawScan()`, `sonata_screenshot`, `sonata_path`, and the
   old separate-sonata branch.
3. If yes, keep that support behind an explicit legacy loader or one-time
   converter so the active pipeline contract remains `full.png` plus `meta.json`.

---

## M-1: Legacy sonata scan branch in `echoes_processor.py`

**File:** `src/.../scraping/processing/echoes_processor.py`

```python
if scan.sonata_screenshot is not None:
    sonata, sonata_raw = _extractSonata(scan.sonata_screenshot, ...)
else:
    sonata = _extractSonataFromIcon(image, ...)
```

The `else` branch is the v2 icon-matching path. The `if` branch only exists for
old raw-scan directories that contain `sonata.png`.

### Plan
Resolve H-1 first. If old `sonata.png` sessions are no longer supported in the
main path, remove this branch, `_extractSonata`, and the related compatibility
fields from `RawEchoScan`.

---

## M-2: `ocr_service.py` legacy echo-name OCR path for captures without a dedicated ROI

**File:** `src/.../scraping/service/ocr_service.py`

```python
elif _has_usable_text(filtered_result):
    # Legacy path (no echoName ROI): use batch OCR on card crop.
    ocr_result = filtered_result
```

This branch fires only when `EchoCapture` has no dedicated `echo_name` crop.
Current live scan and v2 reprocess captures do populate that ROI.

### Plan
Confirm that all active `EchoCapture` producers always provide `echo_name`.
Then remove this branch and the corresponding guard logic above it.

---

## M-3: `echo_assembler.py` level OCR fallback for legacy captures

**File:** `src/.../scraping/service/assemblers/echo_assembler.py`

```python
if capture.detected_level is not None:
    level = capture.detected_level
else:
    level_text = card_lines[1] if len(card_lines) > 1 else ''
    ...
```

The `else` branch exists for `EchoCapture` objects that did not receive
`detected_level` from the scan layer, which is a pre-level-ROI pattern.

### Plan
Confirm that `detected_level` is always set in the live scan path and in the
service reprocess path. If so, remove the fallback branch.

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

Current narrow reverse dependencies:

- `echo_reprocess.py` imports `_rarity_from_rgb_pixel` from `echo_workflow.py`
- `echo_workflow.py` imports `_write_echo_debug_artifacts` from
  `echo_reprocess.py`

### Plan
Move rarity helpers and shared debug-artifact helpers into a neutral helper
module so both entry points depend on that module, not on each other.

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
H-1  ->  M-1/M-2/M-3  ->  M-5/M-6/M-7  ->  M-4  ->  L-*
```

H-1 is the most important because the split raw-session contract is the biggest
remaining source of confusion between the live path, reprocess path, and legacy
processor. The M-* items are all local follow-ons once that format boundary is
settled. L-* items are still deferrable.
