# OCR Preprocessing & Caching Co-location Plan

## Problem Statement

Today, preprocessing logic (colour filtering, thresholding, signature generation) is scattered across `ocr_service.py`, `echo_ocr_cache.py`, `common.py`, and `stats_extractor.py`. Only echo stat crops benefit from persistent caching. Most OCR crops hit the engine raw or with a one-size-fits-all `darken_background` treatment, despite us already knowing their exact text/background colour characteristics.

**Goal**: co-locate preprocessing and cache-signature parameters alongside the ROI definitions so that every OCR crop is systematically preprocessed and cached at the right scope.

Two additional constraints shape the design:

* **Rarity-colored names** — in the new UI the name text of an echo, weapon, or item is tinted by rarity (gold R5, purple R4, blue R3, green R2). Rarity is already detected before name OCR in the normal echo / weapon / item flow, so preprocessing should resolve a single per-rarity colour spec at runtime. OR-masking all rarity bands remains a fallback for bring-up or for flows where rarity metadata is missing.
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
    # The colour space in which to evaluate text_color_ranges and
    # background_color_ranges. Thresholding can still happen later on a
    # grayscale / luminance projection of the original crop.
    color_space: Literal["hsv", "rgb", "bgr", "gray"] = "gray"

    # A list of (lo, hi) inclusive bounds in the chosen colour space.
    # Exact mode is encoded as lo == hi, e.g.
    #   ((166, 235, 247), (166, 235, 247))
    # Multiple entries are OR-masked together.
    # None means skip explicit text-colour masking and go straight to the
    # threshold pipeline.
    text_color_ranges: list[tuple[tuple[int,int,int], tuple[int,int,int]]] | None = None

    # Optional per-rarity replacement for text_color_ranges.
    # When rarity is known upstream, preprocess() uses only the selected
    # rarity entry instead of OR-ing all rarity bands together.
    text_color_ranges_by_rarity: dict[int, list[tuple[tuple[int,int,int], tuple[int,int,int]]]] | None = None

    # Optional reject-mask for known background hues.
    # This is useful for white/off-white text on coloured panels: use HSV to
    # aggressively zero the blue background, then threshold the remaining crop
    # in grayscale.
    background_color_ranges: list[tuple[tuple[int,int,int], tuple[int,int,int]]] | None = None

    # invert=True means the text is darker than the background and should be inverted after masking to produce a white-on-black image for OCR. This is common for white or light-colored text on a dark background, which describes most of our crops except the rarity-tinted names. The echo name regions in particular are bright enough that a direct mask without inversion works well, and avoids amplifying background noise from the blurred portrait layer.
    invert: bool = False             # invert after masking (white text → black on white)

    # Thresholding options for non-color-based crops (e.g. page counts, stat values) or as a post-color-mask cleanup step.
    # "otsu" applies Otsu's method to automatically find a threshold; "floor" applies a fixed floor threshold; "none" skips thresholding.
    threshold_mode: Literal["otsu", "floor", "none"] = "none"
    floor_value: int = 100           # only used when threshold_mode == "floor"

    # morphology options for post-threshold cleanup (e.g. closing small gaps in stat digits). "close" applies a closing operation with a 3x3 kernel; "none" applies no morphology.
    morphology: Literal["close", "none"] = "none"
    allowed_chars: str | None = None # forwarded to OCR engine (e.g. "0123456789.%")

    # Optional scaling bounds applied around OCR preprocessing.
    # Each stage preserves aspect ratio. Upscale fields enforce minimum
    # size, downscale fields cap maximum size.
    pre_upscale: tuple[int, int] | None = None
    pre_downscale: tuple[int, int] | None = None
    post_upscale: tuple[int, int] | None = None
    post_downscale: tuple[int, int] | None = None

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
    # When True, the signature is computed on the *preprocessed* (color-masked) image
    # rather than the raw crop. This makes the key background-independent when the
    # preprocessing reliably strips the background (e.g. an HSV color mask).
    # When False (default for floor-threshold regions), sign the raw crop so that
    # luminance noise from a variable background does not corrupt the key.
    sig_from_preprocessed: bool = False

    def preprocess(self, bgr: np.ndarray, rarity: int | None = None) -> np.ndarray:
        """Apply the declared pipeline, returning a cleaned image for OCR.

        If *rarity* is supplied and *text_color_ranges_by_rarity* has a match,
        that override replaces the base text_color_ranges.

        Processing order:
        1. Resolve per-rarity overrides.
        2. Build an optional reject-mask from background_color_ranges.
        3. Either build a binary text-colour mask or run the threshold pipeline.

        Exact mode uses a range whose low/high tuples are identical.
        """
        ...

    def make_signature(self, bgr: np.ndarray) -> bytes:
        """Compute a stable binary fingerprint for cache keying."""
        ...
```

### Rarity-aware vs. Union-mask preprocessing

For name regions where the text color depends on item rarity:

**Primary path — Rarity-context injection (recommended)**: the caller passes the already-resolved `rarity: int`. `preprocess()` looks up `text_color_ranges_by_rarity[rarity]` and uses only that list. For exact-match fills, each entry is encoded as `((b, g, r), (b, g, r))`. This keeps the mask tight and makes the preprocessed result more cacheable.

**Fallback path — Union mask**: if rarity is unavailable, `preprocess()` may fall back to the base `text_color_ranges` and OR all entries together. This is a compatibility / bring-up mode, not the preferred steady-state path.

### White / Off-white text over coloured backgrounds

For white text, HSV is still useful, but not as the primary text selector. White and off-white hues are too unstable in HSV at low saturation. Instead, use HSV to reject the known background hue range via `background_color_ranges`, then threshold the remaining crop in gray / RGB. In short: use HSV to suppress the blue panel, not to find the white text.

### Where It Lives

New module: `src/wuwa_inventory_kamera/scraping/ocr/region_specs.py`

This keeps OCR concerns separate from the pure geometry in `game_roi.py`, while providing a single registry that the `OcrService` can look up by crop kind.

### Integration Points

| Component | Change |
|-----------|--------|
| `OcrService._process_*` methods | Replace inline preprocessing with `spec.preprocess(crop)` |
| `EchoOcrCache._make_key` | Delegate to `spec.make_signature(crop)` |
| `imageToString` | Accept optional `OcrRegionSpec`; apply preprocessing before engine call |
| Capture dataclasses | Carry optional rarity metadata so the service can resolve per-rarity specs before preprocessing |

---

## Proposal 2 — Per-Region Spec Definitions

Below is the full inventory of OCR call sites mapped to their proposed spec parameters.

### Echo Workflow

The echo card header (`echoCard`) renders on top of a blurred, matted freeze-frame of the current game scene. This background changes between sessions (different map location) but is stable throughout a single scan batch. The stat panel below uses a fixed dark-gradient UI layer unaffected by the scene.

| Crop | Text Colour | Background | Preprocessing | Cache tier |
|------|-------------|------------|---------------|------------|
| `echoName` | **Rarity-tinted** (gold/purple/blue/green) | Blurred in-game scene (dynamic) and echo portrait overlay | Rarity-resolved single-colour mask (exact `lo == hi`, or narrow range if calibration needs slack) → binary | **transient** |
| `fullStatsName` | Near-white (darkest ch ≥ 200, spread ≤ 32) | Stable dark UI gradient | Floor threshold 100 | **persistent** |
| `fullStatsValue` | Near-white | Stable dark UI gradient | Floor threshold 100 | **persistent** |
| `level` | White digits | Semi-transparent dark (card header, dynamic bg) | Floor threshold 150 | **transient** |

Preferred runtime mode for names is a per-rarity exact BGR match loaded from config. The broad HSV bands below remain useful as a calibration fallback and as a bring-up default before the exact fill colours have been measured.

Approximate HSV ranges for each rarity text colour (cv2 H scale 0-180):

| Rarity | Colour | cv2 H range | cv2 S min | cv2 V min |
|--------|--------|-------------|-----------|----------|
| 5 | Gold | 22 – 32 | 60 | 150 |
| 4 | Purple | 135 – 150 | 60 | 150 |
| 3 | Blue | 112 – 125 | 60 | 150 |
| 2 | Green | 55 – 70 | 60 | 150 |

```python
ECHO_NAME = OcrRegionSpec(
    roi_key="echoes.echoName",
    color_space="bgr",
    text_color_ranges_by_rarity=CALIBRATED_ECHO_NAME_BGR_BY_RARITY,
    sig_from_preprocessed=True,   # sign the masked result, not the portrait background
    cache_mode="persistent",      # background varies across sessions but we use an exact color mask to strip it, making the preprocessed signal cacheable across sessions
)

# Example exact entry inside CALIBRATED_ECHO_NAME_BGR_BY_RARITY:
#   5: [((166, 235, 247), (166, 235, 247))]

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
| `weapons.name` | **Rarity-tinted** (same four rarity colors) | Weapon splash art (fixed per weapon) | Rarity-resolved single-colour mask (exact `lo == hi`, or narrow range if calibration needs slack) | **persistent** (art is fixed per weapon identity) |
| `weapons.value` | White digits | Solid dark panel | Floor threshold 150 | **persistent** |
| `weapons.level` | White, "Lv.XX/XX" | Solid dark panel | Floor threshold 150 | **persistent** |

> **Note**: weapon splash art is deterministic for each weapon, unlike the echo portrait which reflects the current game scene. Persistent caching is viable once the rarity-color mask reliably strips the art background.

```python
WEAPON_NAME = OcrRegionSpec(
    roi_key="weapons.name",
    color_space="bgr",
    text_color_ranges_by_rarity=CALIBRATED_NAME_BGR_BY_RARITY,
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
| `weaponName` | **Rarity-tinted** (four rarity bands) | Stable dark | Rarity-resolved single-colour mask (exact `lo == hi`, or narrow range if calibration needs slack) | **persistent** |
| `weaponLevel` | White digits | Stable dark | Floor threshold 150, digits | **persistent** |
| `weaponRank` | Gold/yellow (R5 band only) | Stable dark | Exact or narrow calibrated colour mask | **persistent** |
| `skillLevel` | White digits | Stable dark | Floor threshold 150, digits | **persistent** |

For white / off-white text on coloured panels, background suppression happens before thresholding:

```python
WHITE_TEXT_ON_BLUE_PANEL = OcrRegionSpec(
    roi_key="characters.resonatorName",
    color_space="hsv",
    background_color_ranges=[((98, 40, 30), (125, 255, 255))],
    threshold_mode="floor",
    floor_value=120,
    cache_mode="persistent",
)
```

Here `color_space="hsv"` is used only to suppress the blue background. The text itself is still recovered by thresholding a gray / luminance projection after those background pixels have been zeroed.

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

## Proposal 3 — Data Flow / Pipeline

### Effective spec resolution

1. The scanner produces a raw crop as `bgr: np.ndarray`, plus metadata such as `roi_key`, `crop_kind`, and optionally `rarity`.
2. `OcrService` resolves the base `OcrRegionSpec` for that ROI.
3. If `rarity` is present and `text_color_ranges_by_rarity` contains an entry, that entry becomes the effective `text_color_ranges` for this crop.
4. Otherwise, the base `text_color_ranges` are used as-is.

### OCR-input preprocessing path

```python
def preprocess_for_ocr(crop_bgr: np.ndarray, spec: OcrRegionSpec, rarity: int | None) -> np.ndarray:
    crop_bgr = apply_stage_scaling(
        crop_bgr,
        min_size=spec.pre_upscale,
        max_size=spec.pre_downscale,
    )
    rarity_ranges = spec.text_color_ranges_by_rarity or {}
    effective_ranges = rarity_ranges.get(rarity, spec.text_color_ranges)
    color_view = convert_color_space(crop_bgr, spec.color_space)

    reject_mask = mask_from_ranges(color_view, spec.background_color_ranges)

    if effective_ranges is not None:
        include_mask = mask_from_ranges(color_view, effective_ranges)
        mask = include_mask & ~reject_mask
        plane = np.where(mask, 255, 0).astype(np.uint8)
    else:
        crop_bgr = zero_out_masked_pixels(crop_bgr, reject_mask)
        plane = project_to_luminance(crop_bgr)
        plane = apply_threshold(plane, spec.threshold_mode, spec.floor_value)

    plane = apply_morphology(plane, spec.morphology)
    if spec.invert:
        plane = 255 - plane
    plane = apply_stage_scaling(
        plane,
        min_size=spec.post_upscale,
        max_size=spec.post_downscale,
    )
    return format_for_ocr_backend(plane)
```

Key points:

1. **Exact mode** is not a separate switch. It is just a `text_color_ranges` entry whose `lo` and `hi` tuples are identical.
2. **HSV for white text** is used through `background_color_ranges`, not by trying to directly mask "white" in HSV.
3. The OCR engine always receives the post-processed binary / grayscale image produced by this path.

### Signature-generation path

```python
def image_for_signature(crop_bgr: np.ndarray, spec: OcrRegionSpec, rarity: int | None) -> np.ndarray:
    if spec.sig_from_preprocessed:
        return preprocess_for_ocr(crop_bgr, spec, rarity)

    # Raw-signature path: start from the raw crop, but it may still honour
    # stable background-suppression ranges before the signature-specific
    # normalization steps.
    color_view = convert_color_space(crop_bgr, spec.color_space)
    reject_mask = mask_from_ranges(color_view, spec.background_color_ranges)
    normalized = suppress_background_for_signature(crop_bgr, reject_mask)
    normalized = normalize_for_signature(
        normalized,
        floor=spec.sig_text_floor,
        max_spread=spec.sig_max_spread,
    )
    normalized = apply_stage_scaling(
        normalized,
        min_size=spec.signature.post_upscale,
        max_size=spec.signature.post_downscale,
    )
    return normalized
```

Then:

1. `spec.make_signature()` hashes the normalized bytes plus `roi_key`, `crop_kind`, and `spec_version`.
2. The OCR call and the signature path therefore share the same resolved per-rarity colour config, but they may intentionally diverge on whether they use the fully preprocessed image or the raw-signature normalization path.

### Worked examples

**Exact rarity-coloured echo name**

1. Raw `echoName` crop arrives as BGR.
2. Rarity was already detected as 5.
3. The effective `text_color_ranges` become `[((166, 235, 247), (166, 235, 247))]`.
4. `preprocess()` builds a binary mask directly from that exact fill colour.
5. OCR reads the binary mask.
6. Because `sig_from_preprocessed = True`, the same binary mask becomes the signature source, making the crop highly cacheable.

**White text on a blueish panel**

1. Raw crop arrives as BGR.
2. `background_color_ranges` suppress the blue panel in HSV.
3. The remaining crop is projected to gray and thresholded.
4. OCR reads the thresholded result.
5. The signature either hashes that same preprocessed image (`sig_from_preprocessed = True`) or hashes a raw-normalized variant (`False`) depending on which is empirically more stable.

---

## Proposal 4 — Three-Tier Cache Architecture

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

## Proposal 5 — Maintainability Strategy

### Problem

Game UI updates can shift colours (e.g. the echo name changed from turquoise to orange in a past patch). Hardcoded HSV ranges in Python source are fragile.

### Solution: External Spec Table + Visual Calibration Tool

1. **JSON/TOML spec file** (`src/wuwa_inventory_kamera/config/ocr_region_specs.toml`):
   - Each region's preprocessing params live in a human-readable config file.
   - The `OcrRegionSpec` registry is loaded at startup from this file.
   - Updating colour ranges after a game patch = editing one config value, no code changes.
   - Exact mode uses the same `text_color_ranges` structure as fuzzy mode: a single exact colour is represented by a range whose `lo` and `hi` arrays are identical.
   - Per-rarity colour selection lives in `rarity_overrides` so the loader can merge a base region spec with the already-resolved rarity.

   ```toml
   spec_version = "2.1-patch60"

   [echoes.echoName]
   color_space = "bgr"
   cache_mode = "persistent"
   sig_from_preprocessed = true
   rarity_source = "capture.rarity"

   [echoes.echoName.rarity_overrides."5"]
   text_color_ranges = [
       [[166, 235, 247], [166, 235, 247]],
   ]

   [echoes.echoName.rarity_overrides."4"]
   # Repeat for 4 / 3 / 2 with the calibrated exact BGR fill colour.
   text_color_ranges = [
       [[145, 205, 235], [145, 205, 235]],
   ]

   [echoes.echoName.fallback]
   # Optional bring-up fallback if rarity metadata is missing.
   text_color_ranges = [
       [[22, 60, 150], [32, 255, 255]],
       [[135, 60, 150], [150, 255, 255]],
       [[112, 60, 150], [125, 255, 255]],
       [[55, 60, 150], [70, 255, 255]],
   ]
   color_space = "hsv"

   [echoes.level]
   color_space = "gray"
   threshold_mode = "floor"
   floor_value = 150
   cache_mode = "transient"

   [echoes.fullStatsName]
   color_space = "rgb"
   threshold_mode = "floor"
   floor_value = 100
   cache_mode = "persistent"
   sig_text_floor = 200
   sig_max_spread = 32

   [characters.resonatorName]
   color_space = "hsv"
   background_color_ranges = [
       [[98, 40, 30], [125, 255, 255]],
   ]
   threshold_mode = "floor"
   floor_value = 120
   cache_mode = "persistent"
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

## Proposal 6 — Testing OCR Quality & Cache Efficiency

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
    """Verify that the resolved per-rarity mask extracts the name for every rarity tier."""
    crop = cv2.imread(str(sample_dir / f"echoName_r{rarity}.png"))
    processed = ECHO_NAME.preprocess(crop, rarity=rarity)
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
| 1 | Create `OcrRegionSpec` dataclass with `text_color_ranges`, `text_color_ranges_by_rarity`, `background_color_ranges`, `cache_mode`, `sig_from_preprocessed` | S |
| 2 | Implement `preprocess()` with exact/range colour masks, background suppression, and floor/otsu threshold modes | S |
| 3 | Implement `make_signature()` with raw vs. preprocessed branching | S |
| 4 | Add transient cache dict to `OcrService`; implement two-tier lookup (transient → persistent → OCR) | M |
| 5 | Define specs for echo regions; replace `_filter_echo_name` and `_ocr_images_with_cache` with rarity-resolved spec-driven path | M |
| 6 | Define specs for weapons, items, characters, shell | S |
| 7 | Wire remaining `_process_*` methods through the spec-driven pipeline | M |
| 8 | Extract spec params to TOML config + startup loader | S |
| 9 | Build calibration CLI | M |
| 10 | Build regression test corpus (one sample per region × per rarity) + CI job | M |
| 11 | Add per-tier cache hit-rate logging + session report | S |

S = small (< 2h), M = medium (2-4h)

---

## Implementation Status (as of 2026-05-06)

### Summary

Phases 1–8 and 11 are complete. Phases 9–10 are not yet started.

### Phase-by-Phase Status

| Phase | Status | Notes |
|-------|--------|-------|
| 1 — `OcrRegionSpec` dataclass | **Done** | `src/wuwa_inventory_kamera/scraping/ocr/region_specs.py` — full dataclass with all planned fields including `fallback_color_space` |
| 2 — `preprocess()` | **Done** | Color-space conversion, include/reject masks, threshold, morphology, invert, output formatting; rarity vs. fallback path uses correct color space via `fallback_color_space` |
| 3 — `make_signature()` | **Done** | `sig_from_preprocessed` branching implemented; signature includes `spec_version` + `roi_key` |
| 4 — Transient + persistent cache wired | **Done** | `OcrCache` in `scraping/service/ocr_cache.py`; `OcrCachePath` added to `AppConfig`, `config.json`, Qt config, `SessionOrchestrator`, and `OcrService`; all non-echo-stat persistent specs now hit SQLite |
| 5 — Echo region specs fully wired | **Done** | `echoes.fullStatsName` and `echoes.fullStatsValue` now use `_ocr_with_spec`; `_ocr_images_with_cache` legacy calls removed from `_process_echoes` |
| 6 — Weapon / item / character / shell specs | **Done** | All defined in TOML and wired via `_ocr_with_spec` in `OcrService` |
| 7 — Wire `_process_*` methods | **Done** | All `_process_*` methods use `_ocr_with_spec`; echo stats fully migrated |
| 8 — TOML config + startup loader | **Done** | `src/wuwa_inventory_kamera/config/ocr_region_specs.toml`; `load_specs_from_toml` / `get_spec` / `reload_specs` in `region_specs.py`; `fallback_color_space` correctly loaded from fallback section |
| 9 — Calibration CLI | **Not started** | `cli/calibrate_ocr.py` and `cli/compare_ocr.py` from the plan do not exist; `cli/debug_ocr.py` covers diagnostics but not interactive calibration or A/B accuracy comparison |
| 10 — Regression test corpus + CI | **Not started** | No `test_ocr_preprocessing.py`; no per-region-spec unit tests; no rarity-tier sample corpus; existing `session_tests/test_stats_extractors.py` covers extractor regression but not spec preprocessing |
| 11 — Cache hit-rate logging | **Done** | `OcrCache.session_report()` now emits hit-rate percentage, tier label, and estimated time saved (from `record_ocr_latency()` samples recorded by `_ocr_with_spec`) |

### Known Gaps and Open Tasks

#### `echoes.echoName` cache tier (monitor)

The plan recommends `cache_mode = "transient"` for `echoes.echoName` because its background is a session-varying blurred game screenshot. The current TOML sets it to `"persistent"`. This is safe if `sig_from_preprocessed = true` reliably strips the portrait background before hashing — which should be confirmed empirically before relying on persistent hits.

#### Phase 9 — Calibration CLI

Neither `cli/calibrate_ocr.py` nor `cli/compare_ocr.py` exist. The existing `cli/debug_ocr.py` provides raw diagnostic output but does not:
- Accept a screenshot and interactively adjust thresholds.
- Write updated color ranges back to `ocr_region_specs.toml`.
- Run an A/B comparison of raw-vs-preprocessed OCR accuracy.

#### Phase 10 — Preprocessing unit tests

There are no unit tests for:
- `OcrRegionSpec.preprocess()` with each supported pipeline branch (color mask, background suppression, floor threshold, Otsu, morphology, invert).
- `OcrRegionSpec.make_signature()` stability (same text ± jitter → same key).
- `load_specs_from_toml()` round-trip (TOML → spec → expected field values).
- Rarity-aware path: correct range selection when `rarity` is provided vs. fallback when it is not.
- `fallback_color_space`: verify that fallback HSV ranges are evaluated in HSV when rarity is absent.

---

## Open Questions

1. Should the generalized `OcrCache` remain SQLite or move to a simpler file-based store (one file per signature)?  
   → SQLite is fine; it's already proven. Keep it.

2. Should `OcrRegionSpec` support per-resolution overrides (e.g. different floor values at 1440p vs 1080p)?  
   → Start without; add `resolution_overrides: dict[tuple, dict]` only if quality diverges.

3. Should the cache incorporate game-version / patch number in the key to auto-invalidate after major UI changes?  
   → Yes, via `spec_version` from the TOML. Avoids stale hits after colour shifts.

4. Should the calibrated per-rarity exact colours be validated against the `rarityColorPick` pixel that's already being sampled per echo/item?  
    → Yes, as a calibration check. The calibration CLI (Phase 9) could compare the sampled pixel against the configured exact / narrow range for that rarity and warn if it falls outside tolerance.

5. Should `cache_mode = "transient"` regions eventually gain opt-in persistence once a reliable background-stripping preprocessing is confirmed?  
    → Yes. If testing shows that `sig_from_preprocessed = True` with the resolved per-rarity exact mask consistently strips the portrait background, the echo name tier can be promoted to `"persistent"` with no other code changes.
