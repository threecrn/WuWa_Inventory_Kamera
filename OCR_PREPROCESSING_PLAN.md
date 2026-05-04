# OCR Preprocessing & Caching Co-location Plan

## Problem Statement

Today, preprocessing logic (colour filtering, thresholding, signature generation) is scattered across `ocr_service.py`, `echo_ocr_cache.py`, `common.py`, and `stats_extractor.py`. Only echo stat crops benefit from persistent caching. Most OCR crops hit the engine raw or with a one-size-fits-all `darken_background` treatment, despite us already knowing their exact text/background colour characteristics.

**Goal**: co-locate preprocessing and cache-signature parameters alongside the ROI definitions so that every OCR crop is systematically preprocessed and cached at the right scope.

Two additional constraints shape the design:

* **Rarity-colored names** — in the new UI the name text of an echo, weapon, or item is tinted by rarity (gold R5, purple R4, blue R3, green R2). A single HSV range cannot cover all four; preprocessing for name regions must handle multiple color ranges simultaneously, or be rarity-context-aware.
* **Blurred portrait backgrounds** — some UI panels (most notably the echo card header) use a blurred, matted freeze-frame of the current in-game scene as their background layer. This background is stable within a play session but changes between sessions, making a cross-session persistent cache mostly ineffective. An in-session in-memory cache is still useful because the same echo name may appear on many cards within a single scan batch.

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
    # A list of (lo, hi) inclusive bounds in the chosen color space.
    # Multiple ranges are OR-masked together, which handles multi-rarity name colors.
    # None means skip color masking and go straight to threshold_mode.
    text_color_ranges: list[tuple[tuple[int,int,int], tuple[int,int,int]]] | None = None
    invert: bool = False             # invert after masking (white text → black on white)
    threshold_mode: Literal["otsu", "floor", "none"] = "none"
    floor_value: int = 100           # only used when threshold_mode == "floor"
    morphology: Literal["close", "none"] = "none"
    allowed_chars: str | None = None # forwarded to OCR engine (e.g. "0123456789.%")

    # --- Cache Tier ---
    # "none"        — no caching; always hit the OCR engine (e.g. page counts that change every frame)
    # "transient"   — in-memory dict keyed by signature, scoped to the current scan session.
    #                 Use when the background layer is a blurred in-game screenshot that
    #                 stays constant within a session but changes across sessions.
    # "persistent"  — SQLite cache that survives across sessions.
    #                 Only appropriate when the background is a stable UI element so the
    #                 preprocessed signal is reproducible regardless of session context.
    cache_mode: Literal["none", "transient", "persistent"] = "persistent"

    # --- Signature Parameters (transient and persistent modes) ---
    sig_text_floor: int = 200        # pixel intensity floor for text isolation
    sig_max_spread: int = 32         # max channel spread for "near-white" detection
    sig_downscale: tuple[int, int] = (64, 64)  # max signature thumb size
    # When True, the signature is computed on the *preprocessed* (color-masked) image
    # rather than the raw crop. This makes the key background-independent when the
    # preprocessing reliably strips the background (e.g. an HSV color mask).
    # When False (default for floor-threshold regions), sign the raw crop so that
    # luminance noise from a variable background does not corrupt the key.
    sig_from_preprocessed: bool = False

    def preprocess(self, bgr: np.ndarray, rarity: int | None = None) -> np.ndarray:
        """Apply the declared pipeline, returning a cleaned image for OCR.

        If *rarity* is supplied and *text_color_ranges* contains per-rarity
        entries, only that rarity's range is applied instead of OR-ing all ranges.
        """
        ...

    def make_signature(self, bgr: np.ndarray) -> bytes:
        """Compute a stable binary fingerprint for cache keying."""
        ...
```

### Rarity-aware vs. Union-mask preprocessing

For name regions where the text color depends on item rarity:

**Option A — Union mask (recommended default)**: `preprocess()` OR-combines all ranges in `text_color_ranges`. This requires no extra context from the caller and is robust when rarity detection might be delayed or unreliable.

**Option B — Rarity-context injection**: the caller passes the already-resolved `rarity: int` to `preprocess()`. The spec stores `text_color_ranges` as a 4-element list indexed by rarity, and only the matching range is applied. More precise, but couples preprocessing to the rarity-detection step.

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

The echo card header (`echoCard`) renders on top of a blurred, matted freeze-frame of the current game scene. This background changes between sessions (different map location) but is stable throughout a single scan batch. The stat panel below uses a fixed dark-gradient UI layer unaffected by the scene.

| Crop | Text Colour | Background | Preprocessing | Cache tier |
|------|-------------|------------|---------------|------------|
| `echoName` | **Rarity-tinted** (gold/purple/blue/green) | Blurred in-game scene (dynamic) | Multi-range HSV union mask → binary | **transient** |
| `fullStatsName` | Near-white (darkest ch ≥ 200, spread ≤ 32) | Stable dark UI gradient | Floor threshold 100 | **persistent** |
| `fullStatsValue` | Near-white | Stable dark UI gradient | Floor threshold 100 | **persistent** |
| `level` | White digits | Semi-transparent dark (card header, dynamic bg) | Floor threshold 150 | **transient** |

Approximate HSV ranges for each rarity text colour (cv2 H scale 0-180):

| Rarity | Colour | cv2 H range | cv2 S min | cv2 V min |
|--------|--------|-------------|-----------|----------|
| 5 | Gold | 22 – 32 | 60 | 150 |
| 4 | Purple | 135 – 150 | 60 | 150 |
| 3 | Blue | 112 – 125 | 60 | 150 |
| 2 | Green | 55 – 70 | 60 | 150 |

```python
# All four rarity hue bands defined in one list — OR-masked at preprocessing time.
_ECHO_NAME_RARITY_RANGES = [
    # (lo_HSV, hi_HSV)
    ((22,  60, 150), (32,  255, 255)),   # R5 gold
    ((135, 60, 150), (150, 255, 255)),   # R4 purple
    ((112, 60, 150), (125, 255, 255)),   # R3 blue
    ((55,  60, 150), (70,  255, 255)),   # R2 green
]

ECHO_NAME = OcrRegionSpec(
    roi_key="echoes.echoName",
    color_space="hsv",
    text_color_ranges=_ECHO_NAME_RARITY_RANGES,
    sig_from_preprocessed=True,   # sign the masked result, not the portrait background
    cache_mode="transient",        # background varies across sessions
)

ECHO_STATS_NAME = OcrRegionSpec(
    roi_key="echoes.fullStatsName",
    color_space="rgb",
    threshold_mode="floor",
    floor_value=100,
    allowed_chars=None,  # alpha + punctuation
    cache_mode="persistent",
    sig_text_floor=200,
    sig_max_spread=32,
)

ECHO_STATS_VALUE = OcrRegionSpec(
    roi_key="echoes.fullStatsValue",
    color_space="rgb",
    threshold_mode="floor",
    floor_value=100,
    allowed_chars="0123456789.%+",
    cache_mode="persistent",
    sig_text_floor=200,
    sig_max_spread=32,
)

ECHO_LEVEL = OcrRegionSpec(
    roi_key="echoes.level",
    color_space="gray",
    threshold_mode="floor",
    floor_value=150,
    allowed_chars="0123456789",
    sig_from_preprocessed=True,
    cache_mode="transient",        # sits in the card header over dynamic background
)
```

### Weapon Workflow

The weapon name renders over the weapon's splash art (fixed per weapon identity) and uses a rarity-tinted color for the name, same scheme as echoes. The stat values beneath use a stable dark panel.

| Crop | Text Colour | Background | Preprocessing | Cache tier |
|------|-------------|------------|---------------|------------|
| `weapons.name` | **Rarity-tinted** (same four rarity colors) | Weapon splash art (fixed per weapon) | Multi-range HSV union mask | **persistent** (art is fixed per weapon identity) |
| `weapons.value` | White digits | Solid dark panel | Floor threshold 150 | **persistent** |
| `weapons.level` | White, "Lv.XX/XX" | Solid dark panel | Floor threshold 150 | **persistent** |

> **Note**: weapon splash art is deterministic for each weapon, unlike the echo portrait which reflects the current game scene. Persistent caching is viable once the rarity-color mask reliably strips the art background.

```python
WEAPON_NAME = OcrRegionSpec(
    roi_key="weapons.name",
    color_space="hsv",
    text_color_ranges=_ECHO_NAME_RARITY_RANGES,   # reuse the same four rarity bands
    sig_from_preprocessed=True,
    cache_mode="persistent",
)

WEAPON_VALUE = OcrRegionSpec(
    roi_key="weapons.value",
    color_space="gray",
    threshold_mode="floor",
    floor_value=150,
    allowed_chars="0123456789",
    cache_mode="persistent",
)

WEAPON_LEVEL = OcrRegionSpec(
    roi_key="weapons.level",
    color_space="gray",
    threshold_mode="floor",
    floor_value=150,
    allowed_chars="0123456789Lv./",
    cache_mode="persistent",
)
```

### Item Workflow

Item info uses a stable dark panel background. The item name line itself is rarity-tinted, but the quantity and other numeric fields beneath it are white.

| Crop | Text Colour | Background | Preprocessing | Cache tier |
|------|-------------|------------|---------------|------------|
| `items.info` | Rarity-tinted title + white body | Stable dark panel | Floor threshold 100 (catches all) | **persistent** |

For the info block as a whole, a luminance floor is sufficient since the rarity-tinted title lines are bright enough to survive a threshold at 100. A dedicated title-only crop with an HSV union mask could be added later if accuracy is insufficient.

```python
ITEM_INFO = OcrRegionSpec(
    roi_key="items.info",
    color_space="gray",
    threshold_mode="floor",
    floor_value=100,
    cache_mode="persistent",
)
```

### Character Workflow

Character panels use a stable dark-gradient UI background.

| Crop | Text Colour | Background | Preprocessing | Cache tier |
|------|-------------|------------|---------------|------------|
| `resonatorName` | White | Stable dark gradient | Floor threshold 120 | **persistent** |
| `resonatorLevel` | White digits | Stable dark | Floor threshold 150, digits | **persistent** |
| `weaponName` | **Rarity-tinted** (four rarity bands) | Stable dark | Multi-range HSV mask | **persistent** |
| `weaponLevel` | White digits | Stable dark | Floor threshold 150, digits | **persistent** |
| `weaponRank` | Gold/yellow (R5 band only) | Stable dark | HSV mask (H 22-32, S 60+, V 150+) | **persistent** |
| `skillLevel` | White digits | Stable dark | Floor threshold 150, digits | **persistent** |

### Navigation / Page Counts

| Crop | Text Colour | Background | Preprocessing | Cache tier |
|------|-------------|------------|---------------|------------|
| `weapons.page` / `echoes.page` | White digits | Semi-transparent | Floor 150, digits only | **none** (changes every navigation step) |

```python
NAV_PAGE_COUNT = OcrRegionSpec(
    roi_key="weapons.page",  # shared across tabs
    color_space="gray",
    threshold_mode="floor",
    floor_value=150,
    allowed_chars="0123456789/",
    cache_mode="none",
)
```

### Achievement Workflow

| Crop | Text Colour | Background | Preprocessing | Cache tier |
|------|-------------|------------|---------------|------------|
| `achievements.status` | White / green (completed) | Stable dark panel | Floor threshold 100 | **none** (short result set, overhead not worth it) |

### Shell Workflow

| Crop | Text Colour | Background | Preprocessing | Cache tier |
|------|-------------|------------|---------------|------------|
| `shell` (amount) | White digits | Stable dark header bar | Floor threshold 150, digits | **persistent** |

---

## Proposal 3 — Three-Tier Cache Architecture

### Tier Summary

| Tier | Storage | Lifespan | Key includes background? | Appropriate when |
|------|---------|----------|--------------------------|-----------------|
| `none` | — | — | — | Content changes every frame / tiny result set |
| `transient` | `dict[str, Result]` in memory | Current scan session | No (sign preprocessed image only) | Background is a dynamic in-game scene (e.g. echo card portrait) |
| `persistent` | SQLite WAL (existing `EchoOcrCache` generalised) | Cross-session | No (sign preprocessed image only) | Background is a fixed UI element or deterministic art |

### Transient Cache Implementation

`OcrService` gains a `_transient_cache: dict[str, dict[str, OcrResult]]` — a `crop_kind → key → result` mapping created fresh each session and discarded on `OcrService.shutdown()`. The key is computed identically to the persistent cache (via `spec.make_signature`) so the two tiers share the same hashing logic.

Lookup order for a cacheable region:
1. Check transient cache (O(1) dict lookup, always tried first for both tiers).
2. If `cache_mode == "persistent"` and still a miss, query SQLite.
3. On miss at all levels, run OCR then populate both transient and (if persistent) SQLite.

This means a persistent-tier region also benefits from transient hits within the same session, avoiding repeated SQLite round-trips for the same crop.

### Signature for Regions with Dynamic Backgrounds

For regions where `sig_from_preprocessed = True`, `make_signature()` calls `preprocess()` first and hashes the result instead of the raw crop. This strips the background before hashing, making the key reflect only the text content. This is what enables transient caching to work across different echoes of the same type even if their portrait panels are slightly different frames.

---

## Proposal 4 — Maintainability Strategy

### Problem

Game UI updates can shift colours (e.g. the echo name changed from turquoise to orange in a past patch). Hardcoded HSV ranges in Python source are fragile.

### Solution: External Spec Table + Visual Calibration Tool

1. **JSON/TOML spec file** (`config/ocr_region_specs.toml`):
   - Each region's preprocessing params live in a human-readable config file.
   - The `OcrRegionSpec` registry is loaded at startup from this file.
   - Updating colour ranges after a game patch = editing one config value, no code changes.
   - The `text_color_ranges` list makes it easy to add/remove rarity bands if the game changes a rarity's color scheme.

   ```toml
   [echoes.echoName]
   color_space = "hsv"
   cache_mode = "transient"
   sig_from_preprocessed = true
   # One [[entry]] per rarity band; OR-masked at preprocessing time.
   [[echoes.echoName.text_color_ranges]]
   lo = [22, 60, 150]   # R5 gold
   hi = [32, 255, 255]
   [[echoes.echoName.text_color_ranges]]
   lo = [135, 60, 150]  # R4 purple
   hi = [150, 255, 255]
   [[echoes.echoName.text_color_ranges]]
   lo = [112, 60, 150]  # R3 blue
   hi = [125, 255, 255]
   [[echoes.echoName.text_color_ranges]]
   lo = [55, 60, 150]   # R2 green
   hi = [70, 255, 255]

   [echoes.fullStatsName]
   color_space = "rgb"
   threshold_mode = "floor"
   floor_value = 100
   cache_mode = "persistent"
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

## Proposal 5 — Testing OCR Quality & Cache Efficiency

### A. OCR Quality Testing

#### Unit Tests (offline, no game)

- **Test corpus**: pairs of `(raw_crop.png, expected_text.txt)` per region spec, with samples covering all four rarity colors for name regions.
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

@pytest.mark.parametrize("rarity", [2, 3, 4, 5])
def test_echo_name_all_rarities(rarity, sample_dir):
    """Verify that the union HSV mask extracts the name for every rarity tier."""
    crop = cv2.imread(str(sample_dir / f"echoName_r{rarity}.png"))
    processed = ECHO_NAME.preprocess(crop)
    assert processed.max() > 0, f"No text pixels extracted for rarity {rarity}"
```

#### A/B Comparison Script

- `cli/compare_ocr.py`: runs same crops through (a) raw → OCR and (b) spec.preprocess → OCR.
- Reports per-region accuracy improvement and runtime delta.

### B. Cache Efficiency Testing

#### Metrics to Track

| Metric | How | Where |
|--------|-----|-------|
| Hit rate per crop_kind | `hits / (hits + misses)` per `_ocr_images_with_cache` call | Logged per batch in `OcrService` |
| Transient vs. persistent hit split | Separate counters per tier | Logged at session end |
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

At the end of each scan session, emit a summary log distinguishing cache tiers:

```
[CacheReport] echo_name:       312 transient-hits /   0 persistent-hits / 15 misses — saved ~1.1s  [transient]
[CacheReport] echo_stats_name: 847 transient-hits / 847 persistent-hits / 12 misses — saved ~4.2s  [persistent]
[CacheReport] echo_stats_value:834 transient-hits / 834 persistent-hits / 25 misses — saved ~4.0s  [persistent]
[CacheReport] weapon_name:       0 transient-hits /   0 persistent-hits / 48 misses — new region   [persistent]
[CacheReport] nav_page_count:    — (caching disabled)
```

---

## Implementation Order

| Phase | Work | Effort |
|-------|------|--------|
| 1 | Create `OcrRegionSpec` dataclass with `text_color_ranges`, `cache_mode`, `sig_from_preprocessed` | S |
| 2 | Implement `preprocess()` with union HSV mask + floor threshold modes | S |
| 3 | Implement `make_signature()` with raw vs. preprocessed branching | S |
| 4 | Add transient cache dict to `OcrService`; implement two-tier lookup (transient → persistent → OCR) | M |
| 5 | Define specs for echo regions; replace `_filter_echo_name` and `_ocr_images_with_cache` with spec-driven path | M |
| 6 | Define specs for weapons, items, characters, shell | S |
| 7 | Wire remaining `_process_*` methods through the spec-driven pipeline | M |
| 8 | Extract spec params to TOML config + startup loader | S |
| 9 | Build calibration CLI | M |
| 10 | Build regression test corpus (one sample per region × per rarity) + CI job | M |
| 11 | Add per-tier cache hit-rate logging + session report | S |

S = small (< 2h), M = medium (2-4h)

---

## Open Questions

1. Should the generalized `OcrCache` remain SQLite or move to a simpler file-based store (one file per signature)?  
   → SQLite is fine; it's already proven. Keep it.

2. Should `OcrRegionSpec` support per-resolution overrides (e.g. different floor values at 1440p vs 1080p)?  
   → Start without; add `resolution_overrides: dict[tuple, dict]` only if quality diverges.

3. Should the cache incorporate game-version / patch number in the key to auto-invalidate after major UI changes?  
   → Yes, via `spec_version` from the TOML. Avoids stale hits after colour shifts.

4. Should the rarity-color ranges be validated against the `rarityColorPick` pixel that's already being sampled per echo/item?  
   → Yes, as a calibration check. The calibration CLI (Phase 9) could compare the sampled rarity pixel's hue against the declared ranges and warn if it falls outside any band.

5. Should `cache_mode = "transient"` regions eventually gain opt-in persistence once a reliable background-stripping preprocessing is confirmed?  
   → Yes. If testing shows that `sig_from_preprocessed = True` with the HSV union mask consistently strips the portrait background, the echo name tier can be promoted to `"persistent"` with no other code changes.
