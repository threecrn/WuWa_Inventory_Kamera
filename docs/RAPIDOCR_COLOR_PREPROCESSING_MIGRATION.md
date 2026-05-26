# RapidOCR Color Preprocessing Migration Proposals

## Why This Exists

RapidOCR is now the OCR backend we actively optimize for, and its sweet spot is
high-resolution 3-channel color input.

The current preprocessing path still reflects an older grayscale / binary OCR
mental model:

- `src/wuwa_inventory_kamera/scraping/ocr/region_specs.py` builds a single-channel
  plane for OCR.
- Region knowledge such as rarity colors, background hues, threshold floors, and
  single-line morphology is mostly used to collapse the crop into a mask.
- `_format_for_ocr()` then converts that single-channel image to RGB only at the
  very end.

That means RapidOCR receives a 3-channel array, but not a genuinely color-aware
one. We are already doing the expensive part, namely encoding UI-specific
knowledge per region. The missing step is to use that knowledge to guide a
3-channel render for OCR instead of terminating in grayscale.

## Goals

- Keep OCR inputs 3-channel end-to-end.
- Preserve the current value of `OcrRegionSpec`: region-local UI knowledge,
  rarity overrides, scaling, and cache-signature rules.
- Let preprocessing highlight the text while preserving useful color and
  anti-aliased edge information.
- Avoid coupling OCR rendering and cache-key generation too tightly.
- Make migration incremental so we can compare legacy grayscale renders against
  color-targeted renders on the same crops.

## Non-Goals

- Do not rewrite OCR orchestration and cache tiers in one pass.
- Do not remove the current signature-normalization path until color rendering
  has been validated on real scan / reprocess corpora.
- Do not assume every region should keep its raw background. Some regions will
  still benefit from aggressive background suppression; the change is that the
  final OCR image stays 3-channel.

## Current Constraints In The Repo

The current design already gives us good building blocks:

- `OcrRegionSpec` knows per-region color spaces, rarity overrides,
  background reject ranges, threshold modes, morphology, and scaling bounds.
- `OcrService` already supports multi-strategy recognition for difficult crops,
  especially `echoes.echoName`.
- `pre_upscale` and `post_upscale` already exist, which matches RapidOCR's
  preference for larger inputs.
- `signature_preprocess` already separates cache-key concerns from OCR concerns.

The main mismatch is architectural: preprocessing is expressed as if the final
artifact must be a binary or grayscale plane, even when the information source
is explicitly color-driven.

## Proposal 1 - Split Preprocessing Into Guidance And Rendering

Recommended direction: treat preprocessing as two related but distinct steps.

1. Build a guidance mask or guidance signal from game-UI knowledge.
2. Render a 3-channel OCR image from the original crop using that guidance.

Today, `text_color_ranges`, `background_color_ranges`, thresholding, and
morphology all feed directly into a single output plane. Instead, they should
feed into a `text_mask` or `text_guidance` image that is then used to create the
final RGB image.

### What changes conceptually

- Color-range rules stop meaning "produce a binary final image".
- They start meaning "identify likely text pixels or likely background pixels".
- Thresholding becomes a way to refine guidance, not a command to collapse the
  final OCR input to gray.
- Morphology becomes a mask repair tool, not an image-format decision.

### Recommended internal model

```python
@dataclass(frozen=True, slots=True)
class OcrPreprocessResult:
    ocr_rgb: np.ndarray
    signature_image: np.ndarray
    text_mask: np.ndarray | None = None
    debug_steps: dict[str, np.ndarray] = field(default_factory=dict)
```

And inside `OcrRegionSpec`:

```python
def preprocess_for_ocr(self, bgr: np.ndarray, rarity: int | None = None) -> OcrPreprocessResult:
    scaled_bgr = apply_pre_scaling(bgr)
    text_mask = build_text_mask(scaled_bgr, rarity=rarity)
    rendered_rgb = render_for_ocr(scaled_bgr, text_mask)
    rendered_rgb = apply_post_scaling(rendered_rgb)
    signature_image = build_signature_image(bgr, rarity=rarity)
    return OcrPreprocessResult(rendered_rgb, signature_image, text_mask)
```

### Why this is the right seam

- It preserves region-specific UI knowledge.
- It allows more than one render strategy without multiplying unrelated code.
- It keeps signature generation independent.
- It fits the current `cli/calibrate_ocr.py preview` tooling, which can be
  extended to show raw crop, mask, rendered color image, and signature image.

## Proposal 2 - Add Explicit Color Render Modes To `OcrRegionSpec`

The spec system should stop assuming a single render style. A small set of
explicit render modes is enough.

### Suggested render modes

| Render mode | Intended use | Behavior |
|-------------|--------------|----------|
| `raw_passthrough` | Already clean crops | Keep crop color, only scale and format for OCR |
| `masked_color` | Rarity-colored names, colored ranks | Keep original text pixels, darken or zero non-text pixels, preserve text chroma |
| `neutral_bg_color` | White/off-white text on busy panels | Use background reject mask plus luma boost, but render onto a neutral dark RGB background |
| `luma_boost_color` | White text on stable dark panels | Apply luminance contrast enhancement while preserving 3-channel output |
| `legacy_binary_rgb` | Temporary fallback only | Reproduce current single-channel result, but keep it behind an explicit compatibility mode |

This is enough to cover the current region types without introducing an overly
generic image DSL.

### Suggested spec additions

```python
ocr_render_mode: Literal[
    "raw_passthrough",
    "masked_color",
    "neutral_bg_color",
    "luma_boost_color",
    "legacy_binary_rgb",
] = "legacy_binary_rgb"

background_render: Literal["keep", "darken", "neutralize", "zero"] = "darken"
text_gain: float = 1.0
background_gain: float = 0.25
mask_blur_px: int = 0
```

These fields stay small on purpose. Most of the complexity should remain inside
reusable helper functions, not the TOML.

## Proposal 3 - Keep Signature Generation Separate From OCR Rendering

OCR images and cache signatures have different optimization goals.

- OCR images should maximize recognizability.
- Signature images should maximize stability.

That means we should not immediately switch cache hashing to the new rendered
color image.

Recommended rule:

- Keep `signature_preprocess`, `sig_from_preprocessed`, and the current binary /
  normalized signature path as the default cache mechanism.
- Allow color-aware OCR rendering to evolve independently.
- Only hash rendered RGB images for a region after we prove they are at least as
  stable as the existing signature image.

This matters most for dynamic-background regions such as `echoes.echoName`,
where OCR wants a text-emphasized color image but cache signatures still need to
ignore scene-dependent portrait noise.

## Proposal 4 - Region Classes And Recommended Recipes

The migration does not need one recipe for every ROI. It needs a few stable
region classes.

### 1. Rarity-colored name lines

Examples:

- `echoes.echoName`
- `weapons.name`
- `characters.weaponName`
- `characters.weaponRank`

Recommended recipe:

- Resolve per-rarity exact BGR or narrow HSV range.
- Build a text mask from those ranges.
- Optionally repair the mask for thin anti-aliased gaps.
- Render with `masked_color`:
  - keep original text pixels in color
  - set non-text pixels to dark neutral RGB, not grayscale
- Prefer pre-upscale before mask generation for tiny or compressed crops.

Why:

- These regions already derive most of their signal from color.
- Converting them to binary throws away precisely the feature that makes them
  easy to isolate.

### 2. White or off-white text on dark stable panels

Examples:

- `echoes.fullStatsName`
- `echoes.fullStatsValue`
- `weapons.value`
- `weapons.level`
- `characters.resonatorLevel`
- `characters.weaponLevel`
- `characters.skillLevel`
- `shell.amount`

Recommended recipe:

- Use reject masks only when a colored panel or accent needs to be suppressed.
- Avoid hard binarization as the default steady-state render.
- Build a guidance mask from luma thresholding or near-white logic.
- Render with `luma_boost_color` or `neutral_bg_color`:
  - preserve RGB output
  - boost luminance mainly where guidance indicates text
  - optionally compress background contrast instead of erasing it outright

Why:

- RapidOCR can benefit from preserved anti-aliasing and subpixel edge cues.
- These regions are already visually simple, so heavy binary conversion is often
  not necessary once scaling is handled properly.

### 3. Tiny numeric badges and one-line compact regions

Examples:

- `echoes.level`
- `nav.pageCount`

Recommended recipe:

- Prefer pre-upscale before thresholding or mask generation.
- Keep `single_line = true` behavior, but apply it to the text mask rather than
  the final image.
- Render onto a neutral dark RGB background so the text stays clean and the
  output remains 3-channel.
- Keep `legacy_binary_rgb` available for A/B comparison because these regions
  are the most likely to expose regressions.

Why:

- These are the crops where a small mask mistake changes an entire token.
- The migration needs a fallback path while calibration is being tuned.

### 4. Mixed-color information blocks

Examples:

- `items.info`
- `achievements.status`
- `characters.resonatorName`

Recommended recipe:

- Treat these as composite regions rather than assuming a single threshold mode.
- Support either:
  - a union text mask from multiple color families, or
  - multiple render candidates from one spec.
- Prefer `neutral_bg_color` so the OCR engine sees a stable background but keeps
  whatever color differences remain informative.

Why:

- A single grayscale threshold is the most lossy choice for mixed UI text.
- These regions are good candidates for spec-driven candidate renders.

## Proposal 5 - Let Specs Produce OCR Candidates, Not Just One Image

The current `echoes.echoName` flow in `OcrService` already proves that one crop
may need more than one OCR variant.

Instead of keeping this as ad hoc orchestration, let the spec define ordered
candidate renders.

Example:

```python
candidate_modes = [
    "masked_color",
    "raw_passthrough",
]
```

Or, more explicitly:

```python
@dataclass(frozen=True, slots=True)
class OcrRenderCandidate:
    name: str
    image_rgb: np.ndarray
    use_single_line: bool
```

Benefits:

- The echo-name special case becomes less special.
- Region-local UI knowledge stays in the spec layer.
- `OcrService` can run a generic policy: try candidates in order, stop when the
  result passes a region-specific acceptance rule.

This is not strictly required for the first color migration, but it is the
cleanest way to keep preprocessing and recognition policy aligned.

## Proposal 6 - Preferred Processing Order

The most important practical change is the order of operations.

Recommended order for color-targeted preprocessing:

1. Start from the raw BGR crop.
2. Apply `pre_upscale` if the crop is small or compact.
3. Convert to the working color space only for mask construction.
4. Build text and background masks.
5. Repair masks via `single_line` or morphology if needed.
6. Render a 3-channel OCR image from the original color crop using those masks.
7. Apply post-render scaling if a larger OCR input is still needed.
8. Convert once to the backend's expected channel order and ensure contiguous
   memory.

What to avoid:

- Converting to grayscale before building a color-informed render.
- Applying morphology directly to the final RGB image.
- Reusing signature-normalization code as the OCR render path.

## Concrete Implementation Notes

### `region_specs.py`

Refactor the current helpers into three groups:

- mask builders
  - `_mask_from_ranges()`
  - `_build_text_mask_from_threshold()`
  - `_repair_single_line_mask()`
- renderers
  - `_render_masked_color()`
  - `_render_neutral_bg_color()`
  - `_render_luma_boost_color()`
- signature helpers
  - existing `_normalize_preprocessed_for_signature()`
  - existing `_normalize_for_signature()`

The current `_preprocess_plane()` should stop being the universal final stage.
It can be decomposed into `build_guidance_mask()` plus a render function.

### `cli/calibrate_ocr.py`

Extend `preview` output so each sample can show:

- raw crop
- text mask
- rendered OCR RGB image
- signature image
- OCR text on raw and rendered variants

That gives a tight calibration loop for migration without having to wire the
entire service path every time.

### `OcrService`

Near-term:

- keep `_ocr_with_spec()` shape largely intact
- let `spec.preprocess()` return the rendered RGB image only
- keep signatures internal to the cache path

Medium-term:

- let specs expose ordered render candidates for tricky regions
- move echo-name image variant logic out of inline `OcrService` code

## Incremental Rollout Plan

### Phase 0 - Instrumentation

- Add color-render previews to `cli/calibrate_ocr.py`.
- Save raw, mask, rendered RGB, and signature artifacts side-by-side.
- Add a debug switch that prints OCR text and confidence for raw vs rendered.

### Phase 1 - Internal API Prep

- Refactor `region_specs.py` so mask construction and final rendering are
  separate helpers.
- Keep the public behavior identical by default with `legacy_binary_rgb`.
- Make `_format_for_ocr()` a thin channel-order / contiguity helper only.

### Phase 2 - Migrate Low-Risk Regions First

- Start with stable white-text panel regions:
  - `echoes.fullStatsName`
  - `echoes.fullStatsValue`
  - `weapons.value`
  - `weapons.level`
  - `shell.amount`
- Measure OCR deltas on saved crops and offline reprocess samples.

### Phase 3 - Migrate Color-Driven Name Regions

- Move rarity-colored name lines to `masked_color`.
- Validate cache behavior separately from OCR quality.
- Keep raw-color fallback candidate for the regions that currently use hand-made
  service-level fallback logic.

### Phase 4 - Migrate Mixed And Compact Regions

- Handle `items.info`, `characters.resonatorName`, `nav.pageCount`, and
  `echoes.level`.
- Introduce candidate renders where one fixed recipe is not enough.

### Phase 5 - Remove Accidental Grayscale Assumptions

- Remove comments and helper names that imply OCR preprocessing must produce a
  single-channel plane.
- Make new specs default to a color render mode.
- Keep `legacy_binary_rgb` only as an escape hatch for proven exceptions.

## Validation Criteria

We should treat this as a measured migration, not a style cleanup.

Success metrics:

- same or better exact-match OCR accuracy on the existing reprocess corpus
- same or better confidence on known-good crops
- no material increase in false positives for short numeric regions
- cache hit rate does not regress for regions whose signature path is unchanged
- calibration workflow remains fast enough to tune after game patches

Recommended checks:

- golden crop preview comparisons from `cli/calibrate_ocr.py preview --ocr`
- focused offline reprocess runs on saved sessions
- per-region before / after OCR result diffing
- confidence histograms for name regions and compact numeric regions

## Open Questions

1. Confirm the backend-facing channel order we want to standardize on. The
   current helper promotes grayscale to RGB, so RGB is the least disruptive
   default unless RapidOCR benchmarks show a different preference.
2. Decide whether `items.info` should be one mixed-color OCR region or split
   into smaller regions with simpler recipes.
3. Decide whether render-candidate ordering belongs in the spec TOML or in a
   thin Python helper attached to selected specs.

## Recommended Path

If we want the smallest high-value change, the right sequence is:

1. Refactor `region_specs.py` from "build a plane" to "build a mask, then render
   RGB".
2. Keep signature generation separate.
3. Migrate stable white-text panel regions first.
4. Migrate rarity-colored name lines next with `masked_color` rendering.
5. Only after that, generalize multi-candidate rendering for the few regions
   that still need fallback logic.

That path aligns the preprocessing layer with RapidOCR's actual interface
without throwing away the game-specific UI knowledge we already encoded.