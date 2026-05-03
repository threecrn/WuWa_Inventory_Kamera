# OCR Preprocessing & Caching Co-location Plan

## Problem Statement

Today, preprocessing logic (colour filtering, thresholding, signature generation) is scattered across `ocr_service.py`, `echo_ocr_cache.py`, `common.py`, and `stats_extractor.py`. Only echo stat crops benefit from persistent caching. Most OCR crops hit the engine raw or with a one-size-fits-all `darken_background` treatment, despite us already knowing their exact text/background colour characteristics.

**Goal**: co-locate preprocessing and cache-signature parameters alongside the ROI definitions so that every OCR crop is systematically preprocessed and cacheable.

---

## Proposal 1 — Design Adjustment: `OcrRegionSpec` Descriptors

### Concept

Introduce a declarative descriptor that pairs a ROI key with its preprocessing recipe and cache-signature strategy.

```python
@dataclass(frozen=True, slots=True)
class OcrRegionSpec:
    """Declares how to preprocess and fingerprint a specific OCR region."""

    roi_key: str                     # matches game_roi.py region key path e.g. "echoes.echoName"

    # --- Preprocessing ---
    color_space: Literal["hsv", "rgb", "gray"] = "gray"
    text_color_lo: tuple[int, int, int] | None = None   # lower bound (inclusive)
    text_color_hi: tuple[int, int, int] | None = None   # upper bound (inclusive)
    invert: bool = False             # invert after masking (white text → black on white)
    threshold_mode: Literal["otsu", "floor", "none"] = "none"
    floor_value: int = 100           # only used when threshold_mode == "floor"
    morphology: Literal["close", "none"] = "none"
    allowed_chars: str | None = None # forwarded to OCR engine (e.g. "0123456789.%")

    # --- Cache / Signature ---
    cacheable: bool = True
    sig_text_floor: int = 200        # pixel intensity floor for text isolation
    sig_max_spread: int = 32         # max channel spread for "near-white" detection
    sig_downscale: tuple[int, int] = (64, 64)  # max signature thumb size

    def preprocess(self, bgr: np.ndarray) -> np.ndarray:
        """Apply the declared pipeline, returning a cleaned image for OCR."""
        ...

    def make_signature(self, bgr: np.ndarray) -> bytes:
        """Compute a stable binary fingerprint for cache keying."""
        ...
```

### Where It Lives

New module: `src/wuwa_inventory_kamera/scraping/ocr/region_specs.py`

This keeps OCR concerns separate from the pure geometry in `game_roi.py`, while providing a single registry that the `OcrService` can look up by crop kind.

### Integration Points

| Component | Change |
|-----------|--------|
| `OcrService._process_*` methods | Replace inline preprocessing with `spec.preprocess(crop)` |
| `EchoOcrCache._make_key` | Delegate to `spec.make_signature(crop)` |
| `imageToString` | Accept optional `OcrRegionSpec`; apply preprocessing before engine call |
| Capture dataclasses | Add optional `region_spec` field (or resolve by crop-kind enum at service level) |

---

## Proposal 2 — Per-Region Spec Definitions

Below is the full inventory of OCR call sites mapped to their proposed spec parameters.

### Echo Workflow

| Crop | Text Colour | Background | Preprocessing | Cache |
|------|-------------|------------|---------------|-------|
| `echoName` | Dull orange HSV (20-30, 60-255, 80-255) | Busy portrait | HSV mask → binary | Yes (new) |
| `fullStatsName` | Near-white (darkest ch ≥ 200, spread ≤ 32) | Dark gradient | Floor threshold 100 | Yes (existing) |
| `fullStatsValue` | Near-white | Dark gradient | Floor threshold 100 | Yes (existing) |
| `level` | White digits | Semi-transparent dark | Floor threshold 150 | Yes (small region, cheap) |

```python
ECHO_NAME = OcrRegionSpec(
    roi_key="echoes.echoName",
    color_space="hsv",
    text_color_lo=(20, 60, 80),
    text_color_hi=(30, 255, 255),
    cacheable=True,
)

ECHO_STATS_NAME = OcrRegionSpec(
    roi_key="echoes.fullStatsName",
    color_space="rgb",
    threshold_mode="floor",
    floor_value=100,
    allowed_chars=None,  # alpha + punctuation
    cacheable=True,
    sig_text_floor=200,
    sig_max_spread=32,
)

ECHO_STATS_VALUE = OcrRegionSpec(
    roi_key="echoes.fullStatsValue",
    color_space="rgb",
    threshold_mode="floor",
    floor_value=100,
    allowed_chars="0123456789.%+",
    cacheable=True,
    sig_text_floor=200,
    sig_max_spread=32,
)

ECHO_LEVEL = OcrRegionSpec(
    roi_key="echoes.level",
    color_space="gray",
    threshold_mode="floor",
    floor_value=150,
    allowed_chars="0123456789",
    cacheable=True,
)
```

### Weapon Workflow

| Crop | Text Colour | Background | Preprocessing | Cache |
|------|-------------|------------|---------------|-------|
| `weapons.name` | White | Gradient dark/weapon art | Floor threshold 120 | Yes |
| `weapons.value` | White digits | Solid dark | Floor threshold 150 | Yes |
| `weapons.level` | White, "Lv.XX/XX" | Semi-transparent | Floor threshold 150 | Yes |

```python
WEAPON_NAME = OcrRegionSpec(
    roi_key="weapons.name",
    color_space="gray",
    threshold_mode="floor",
    floor_value=120,
    cacheable=True,
)

WEAPON_VALUE = OcrRegionSpec(
    roi_key="weapons.value",
    color_space="gray",
    threshold_mode="floor",
    floor_value=150,
    allowed_chars="0123456789",
    cacheable=True,
)

WEAPON_LEVEL = OcrRegionSpec(
    roi_key="weapons.level",
    color_space="gray",
    threshold_mode="floor",
    floor_value=150,
    allowed_chars="0123456789Lv./",
    cacheable=True,
)
```

### Item Workflow

| Crop | Text Colour | Background | Preprocessing | Cache |
|------|-------------|------------|---------------|-------|
| `items.info` | White / gold (rarity-colored title) | Dark panel | Floor threshold 100 | Yes |

```python
ITEM_INFO = OcrRegionSpec(
    roi_key="items.info",
    color_space="gray",
    threshold_mode="floor",
    floor_value=100,
    cacheable=True,
)
```

### Character Workflow

| Crop | Text Colour | Background | Preprocessing | Cache |
|------|-------------|------------|---------------|-------|
| `resonatorName` | White | Dark gradient | Floor threshold 120 | Yes |
| `resonatorLevel` | White digits | Dark | Floor threshold 150, digits | Yes |
| `weaponName` | White | Dark | Floor threshold 120 | Yes |
| `weaponLevel` | White digits | Dark | Floor threshold 150, digits | Yes |
| `weaponRank` | Gold/yellow | Dark | HSV mask (H 20-40, S 100+, V 150+) | Yes |
| `skillLevel` | White digits | Dark | Floor threshold 150, digits | Yes |

### Navigation / Page Counts

| Crop | Text Colour | Background | Preprocessing | Cache |
|------|-------------|------------|---------------|-------|
| `weapons.page` / `echoes.page` | White digits | Semi-transparent | Floor 150, digits only | No (changes every frame) |

```python
NAV_PAGE_COUNT = OcrRegionSpec(
    roi_key="weapons.page",  # shared across tabs
    color_space="gray",
    threshold_mode="floor",
    floor_value=150,
    allowed_chars="0123456789/",
    cacheable=False,  # content changes every navigation step
)
```

### Achievement Workflow

| Crop | Text Colour | Background | Preprocessing | Cache |
|------|-------------|------------|---------------|-------|
| `achievements.status` | White / green (completed) | Dark panel | Floor threshold 100 | No (small text, varies) |

### Shell Workflow

| Crop | Text Colour | Background | Preprocessing | Cache |
|------|-------------|------------|---------------|-------|
| `shell` (amount) | White digits | Dark header bar | Floor threshold 150, digits | Yes |

---

## Proposal 3 — Maintainability Strategy

### Problem

Game UI updates can shift colours (e.g. the echo name changed from turquoise to orange in a past patch). Hardcoded HSV ranges in Python source are fragile.

### Solution: External Spec Table + Visual Calibration Tool

1. **JSON/TOML spec file** (`config/ocr_region_specs.toml`):
   - Each region's preprocessing params live in a human-readable config file.
   - The `OcrRegionSpec` registry is loaded at startup from this file.
   - Updating colour ranges after a game patch = editing one config value, no code changes.

   ```toml
   [echoes.echoName]
   color_space = "hsv"
   text_color_lo = [20, 60, 80]
   text_color_hi = [30, 255, 255]
   cacheable = true

   [echoes.fullStatsName]
   color_space = "rgb"
   threshold_mode = "floor"
   floor_value = 100
   sig_text_floor = 200
   sig_max_spread = 32
   ```

2. **Calibration CLI** (`cli/calibrate_ocr.py`):
   - Takes a screenshot (or uses saved captures from `captures/`).
   - For each ROI, shows the crop + preprocessed result side-by-side (OpenCV `imshow` or saves to disk).
   - Lets the developer adjust thresholds interactively and writes updated TOML.

3. **Version tag in config**:
   - `spec_version = "2.1-patch60"` at the top of the TOML.
   - Cache keys incorporate the spec version → automatic invalidation on parameter changes.

4. **Regression capture set** (`captures/ocr-regression/`):
   - A curated folder of raw screenshots covering all resolutions + rare edge cases.
   - CI or a local script replays preprocessing on these and diffs OCR output against golden files.

---

## Proposal 4 — Testing OCR Quality & Cache Efficiency

### A. OCR Quality Testing

#### Unit Tests (offline, no game)

- **Test corpus**: pairs of `(raw_crop.png, expected_text.txt)` per region spec.
- **Assertion**: after `spec.preprocess(crop)` + OCR engine, Levenshtein distance to expected ≤ threshold.
- **Location**: `tests/test_ocr_preprocessing.py`

```python
@pytest.mark.parametrize("spec,sample_dir", REGION_SPEC_TEST_CASES)
def test_ocr_accuracy(spec, sample_dir):
    for crop_path in sample_dir.glob("*.png"):
        expected = crop_path.with_suffix(".txt").read_text().strip()
        crop = cv2.imread(str(crop_path))
        processed = spec.preprocess(crop)
        result = ocr_engine.recognize(processed, allowed_chars=spec.allowed_chars)
        assert levenshtein(result, expected) <= 2, f"Mismatch for {crop_path.name}"
```

#### A/B Comparison Script

- `cli/compare_ocr.py`: runs same crops through (a) raw → OCR and (b) spec.preprocess → OCR.
- Reports per-region accuracy improvement and runtime delta.

### B. Cache Efficiency Testing

#### Metrics to Track

| Metric | How | Where |
|--------|-----|-------|
| Hit rate per crop_kind | `hits / (hits + misses)` per `_ocr_images_with_cache` call | Logged per batch in `OcrService` |
| Signature collision rate | Count distinct images mapping to same key | Offline audit script on SQLite |
| Cache size growth | Row count + DB file size | Logged at session end |
| Time saved | `(cache_hits × avg_ocr_ms)` | Logged per session summary |

#### Unit Test for Signature Stability

```python
def test_signature_stable_across_minor_variations():
    """Same text with ±1 pixel jitter should produce identical signatures."""
    base = load_sample("echo_stats_value_sample.png")
    shifted = np.roll(base, 1, axis=1)  # 1px horizontal shift
    spec = ECHO_STATS_VALUE
    assert spec.make_signature(base) == spec.make_signature(shifted)
```

#### Integration Test for Cache Round-Trip

```python
def test_cache_roundtrip(tmp_path):
    cache = OcrCache(tmp_path / "test.sqlite3")
    spec = ECHO_STATS_VALUE
    crop = load_sample("echo_stats_value_sample.png")
    tokens = [("ATK", 0.95, np.array([10, 5, 100, 20]))]

    cache.store(spec, crop, tokens)
    result = cache.lookup(spec, crop)
    assert result == tokens
```

#### Session-level Cache Report

At the end of each scan session, emit a summary log:

```
[CacheReport] echo_stats_name: 847 hits / 12 misses (98.6%) — saved ~4.2s
[CacheReport] echo_stats_value: 834 hits / 25 misses (97.1%) — saved ~4.0s
[CacheReport] weapon_name: 0 hits / 48 misses (0.0%) — new region, populating
```

---

## Implementation Order

| Phase | Work | Effort |
|-------|------|--------|
| 1 | Create `OcrRegionSpec` dataclass + `preprocess()` + `make_signature()` | S |
| 2 | Define specs for echo stats (mirrors existing `EchoOcrCache` logic) | S |
| 3 | Refactor `OcrService._process_echoes` to use specs; generalize `EchoOcrCache` → `OcrCache` | M |
| 4 | Define specs for weapons, items, characters, shell | S |
| 5 | Wire remaining `_process_*` methods through the spec-driven pipeline | M |
| 6 | Extract spec params to TOML config + startup loader | S |
| 7 | Build calibration CLI | M |
| 8 | Build regression test corpus + CI job | M |
| 9 | Add cache hit-rate logging + session report | S |

S = small (< 2h), M = medium (2-4h)

---

## Open Questions

1. Should the generalized `OcrCache` remain SQLite or move to a simpler file-based store (one file per signature)?  
   → SQLite is fine; it's already proven. Keep it.

2. Should `OcrRegionSpec` support per-resolution overrides (e.g. different floor values at 1440p vs 1080p)?  
   → Start without; add `resolution_overrides: dict[tuple, dict]` only if quality diverges.

3. Should the cache incorporate game-version / patch number in the key to auto-invalidate after major UI changes?  
   → Yes, via `spec_version` from the TOML. Avoids stale hits after colour shifts.
