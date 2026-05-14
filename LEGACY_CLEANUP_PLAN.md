# Legacy & Fallback Cleanup Plan

Scan date: 2026-05-14.  
Grouped by priority: **Bug** > **High** (dead code) > **Medium** (active legacy) > **Low** (design debt).

---

## Bug: Phantom `_preprocess_scaled_plane` call

**File:** `src/.../scraping/ocr/region_specs.py`, lines 306 & 247  
**Impact:** `AttributeError` crash at runtime for any spec with `sig_from_preprocessed = true`.

### Affected specs (from `ocr_region_specs.toml`)
- `weapons.name`
- `characters.weaponName`
- `characters.weaponRank`

### What happened
`_preprocess_for_signature` calls `self._preprocess_scaled_plane(…)` (line 306), but that method no longer exists — it was deleted (or merged elsewhere), leaving a stale comment at line 247 and a dangling call site.  The existing `_preprocess_plane` (line 249) covers the same logic but lacks the surrounding scaling wraps.

### Fix
Replace the `self._preprocess_scaled_plane(…)` call in `_preprocess_for_signature` with explicit scaling + `_preprocess_plane`:

```python
def _preprocess_for_signature(self, bgr, rarity):
    sig = self.signature_preprocess
    # 1. Pre-scale
    scaled = _apply_scaling_stage(
        bgr,
        upscale_min=(sig.pre_upscale if sig is not None else None),
        downscale_max=(sig.pre_downscale if sig is not None else None),
    )
    # 2. Preprocess plane (color → mask → binary)
    plane = self._preprocess_plane(scaled, rarity, <merged sig+self kwargs>)
    # 3. Post-scale
    plane = _apply_scaling_stage(plane, upscale_min=..., downscale_max=...)
    return plane
```

After the fix, the comment at line 247 and the now-unused `_preprocess_plane` method can be evaluated for removal.

---

## H-1: Remove the `EchoOcrCache` stack (dead code)

`_ocr_images_with_cache` in `ocr_service.py` has **no callers** — the OCR preprocessing plan notes that all calls were removed from `_process_echoes` in favour of `_ocr_with_spec`.  The entire `echo-stat-ocr.sqlite3` cache path is wired but never exercised.

### Files to change

| File | Change |
|---|---|
| `scraping/service/ocr_service.py` | Remove `_ocr_images_with_cache` method; remove `echo_stat_cache_path` param + `self._echo_stat_cache` from `__init__`; remove `EchoOcrCache` import |
| `scraping/service/echo_reprocess.py` | Remove `echo_stat_cache_path` param |
| `cli/scan.py` | Remove `--echo-stat-cache` arg and wiring |
| `cli/reprocess.py` | Remove `--echo-stat-cache` arg and wiring |
| `ui/home.py` | Remove `echo_stat_cache_path` param and `_echo_stat_cache_path` field |
| `ui/settings.py` | Remove the "Legacy echo-stat cache" cleanup block and the settings card |
| `ui/config.py` | Remove `echoStatCachePath` ConfigItem |
| `config/app_config.py` | Remove `default_echo_stat_cache_path()` and `echoStatCachePath` attribute |
| `scraping/service/echo_ocr_cache.py` | **Delete file** |

Separately, the `export/echo-stat-ocr.sqlite3` database file in the repo can be removed once the UI setting is gone (it becomes an unreferenced artefact).

---

## H-2: Remove root-level shim packages

The root-level `scraping/`, `ui/`, and `updater/` trees are pure re-export shims to `wuwa_inventory_kamera.*`.  They exist only because a few files still import via old paths.

### Active callers holding up removal

| Shim | Caller | Migration |
|---|---|---|
| `scraping/data.py` | `updater/databaseUpdater.py` | Change to `from wuwa_inventory_kamera.scraping.data import …` |
| `scraping/utils/…` | `cli/debug_ocr.py` | Change to `from wuwa_inventory_kamera.scraping.utils import …` |
| `scraping/processing/echoesProcessor.py` | `tests/test_ocrSubstatNames.py` | Update test import |
| `scraping/processing/echoesValidator.py` | `conftest.py` mentions it; `session_tests/` | Update imports |
| `scraping/models/rawScan.py` | (scan may be implicit via conftest path) | Update imports |
| `game/screenInfo.py` (via conftest sys.path) | `tests/test_ocrSubstatNames.py` | Change to `from wuwa_inventory_kamera.game.screen_info import ScreenInfo` |

### Steps
1. Migrate all callers listed above to use `wuwa_inventory_kamera.*` imports directly.
2. Update `conftest.py` — remove the `sys.path.insert(0, …)` hack once all tests use package imports.
3. Delete `scraping/`, `ui/`, `updater/` shim trees from the project root.

Note: `ui/` shims (`homeUI.py`, `inventoryUI.py`, `loadingUI.py`, `mainUI.py`, `settingsUI.py`) have no Python callers found; they may already be unused.  `updater/databaseUpdater.py` and `updater/assetsUpdater.py` still contain the Qt-dependent subclasses (`DataUpdater`, `AssetsUpdater`), so only the `from scraping.data import …` lines within them need updating — the files themselves must stay.

---

## M-1: Remove `_preprocess_plane` dead method (post Bug fix)

After the Bug fix above, `_preprocess_scaled_plane` no longer exists.  `_preprocess_plane` will be called directly by `_preprocess_for_signature`.  Once that is stable, evaluate whether `_preprocess_plane` can be inlined or whether it is useful enough to keep.

The comment `# _preprocess_scaled_plane is now obsolete and can be removed in future cleanup` at line 247 should be removed regardless.

---

## M-2: Legacy sonata scan branch in `echoes_processor.py`

**File:** `src/.../scraping/processing/echoes_processor.py`, line 547

```python
if scan.sonata_screenshot is not None:
    # Legacy path: sonata.png was captured separately (old scanner with scroll-down).
    sonata, sonata_raw = _extractSonata(scan.sonata_screenshot, …)
else:
    sonata = _extractSonataFromIcon(image, …)
```

The `else` branch (icon matching) is the v2 path.  The `if` branch only triggers when reprocessing **old** raw-scan directories that contain a `sonata.png` file.

### Plan
1. Audit whether any user-facing raw-scan directories still ship `sonata.png`.
2. If not (or after a suitable deprecation window), remove the `if` branch, `_extractSonata`, and `sonata_path` handling from `RawEchoScan`.

---

## M-3: `ocr_service.py` — legacy echoName OCR path (no ROI)

**File:** `src/.../scraping/service/ocr_service.py`, line 744

```python
elif _has_usable_text(filtered_result):
    # Legacy path (no echoName ROI): use batch OCR on card crop.
    ocr_result = filtered_result
```

This branch fires when `EchoCapture` has no dedicated `echoName` region crop.  New captures always provide the ROI.

### Plan
Verify that `EchoCapture.echo_name_crop` (or equivalent) is always set in the live scan workflow and in v2 reprocess.  If so, remove this branch and the corresponding `_echo_name_spec is None` guard above it.

---

## M-4: `app_config.py` — mutable global state (`INVENTORY`, `FAILED`)

```python
INVENTORY: dict = {'items': {}, 'date': ''}
FAILED: list = []
```

These are module-level mutable globals imported by `ui/home.py` and `scraping/utils/common.py` (`savingScraped` uses `INVENTORY`). They represent implicit shared state between the UI and scanning layers.

### Plan
Pass scan results explicitly via return values / callback instead of mutating globals.  The `savingScraped` helper in `scraping/utils/common.py` is the main consumer; its default parameter `{'inventory_wuwainventorykamera.json': (INVENTORY['items'], dict)}` bakes in a reference to the global at definition time, which is a Python trap.

---

## M-5: `echo_assembler.py` — level OCR fallback for legacy captures

```python
if capture.detected_level is not None:
    level = capture.detected_level
else:
    # card layout: [name, level, cost] — level is at index 1
    level_text = card_lines[1] if len(card_lines) > 1 else ''
    …
    # Fallback: trailing digits in name (phantom OCR merges name + level)
```

The else-branch is for `EchoCapture` objects that did not receive `detected_level` from the scan layer — a pattern from before the level ROI was added.

### Plan
Confirm that `detected_level` is always set in the live scan path and v2 reprocess path.  If so, the else-branch (including the inline `import re`) can be removed.

---

## L-1: `_rapidocr.py` — `fallback_text_score` second OCR pass

`RapidOcrBackend` constructs a second `RapidOCR()` instance (`self._fallback_ocr`) with a lower `text_score` threshold (default `0.3`) and merges its results in `thorough_recognize`.  This effectively doubles the model-session count for DML backends.

The memory notes document a hypothesis that 6 DML sessions (3 main + 3 fallback) contribute to VRAM pressure, though A/B tests with allocator knobs were inconclusive.

### Plan
1. Determine empirically whether the fallback pass improves any real scan result (compare OCR quality with and without).
2. If the improvement is negligible, pass `fallback_text_score=None` to the DML `RapidOcrBackend` and remove the fallback-OCR path from `_rapidocr.py` (`_fallback_ocr`, `_fallback_text_score`, `thorough_recognize` merge logic).

---

## L-2: `region_specs.py` — `render_for_ocr` "legacy path" comment

`render_for_ocr` contains:
```
Currently: legacy path (grayscale to RGB). Later: color-aware rendering.
```

The full-color rendering path is partially implemented (`text_mask`-based clearing), but the comment implies a planned improvement.  This is a design note, not dead code — track separately in the OCR preprocessing plan.

---

## L-3: `region_specs.py` — `sig_downscale` TOML alias

`_build_region_spec_from_dict` reads `sig_downscale` from TOML as a compatibility alias for `signature.post_downscale`:

```python
legacy_sig_downscale = data.get("sig_downscale")
```

### Plan
Grep all TOML files for `sig_downscale`.  If none exist, remove the alias loader.  Otherwise, migrate the TOML entries to `[….signature] post_downscale = …` and then remove the alias.

---

## L-4: `ocr_cache.py` — stale re-export comment

```python
# Re-export for backward compat
ImageOcrResult = list[tuple[str, float, np.ndarray]]
```

This re-export exists so callers of the old `echo_ocr_cache` can use `ImageOcrResult` from `ocr_cache`.  Once H-1 (EchoOcrCache removal) is done, check whether any remaining callers still import `ImageOcrResult` from `ocr_cache` and remove the comment + alias if not.

---

## Suggested Work Order

```
Bug fix  →  H-1  →  H-2  →  M-1  →  M-2  →  M-3  →  M-4  →  M-5  →  L-*
```

The Bug fix is independent of everything else and should be first because it masks a live crash.  H-1 and H-2 are pure removals and reduce confusion for all subsequent work.  M-* items require a short audit step before the code change.  L-* items are deferrable.
