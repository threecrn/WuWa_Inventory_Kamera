# ImgIO Module Introduction Plan

## Purpose

Create a single `imgio` module that encapsulates direct `cv2` usage and allows runtime selection of either:

- `cv2` backend (fast path, feature-complete where already used)
- lightweight backend(s) based on `Pillow` + `numpy` (default fallback)
- optional advanced backend (`scikit-image`/`scipy`) for operations that are hard to reproduce with only Pillow/numpy

Primary outcome: stop importing `cv2` directly throughout the codebase and make OpenCV optional for packaging/freezing targets.

## Goals

- Centralize image operations behind a small, typed API.
- Keep current behavior stable while decoupling callers from OpenCV.
- Enable low-dependency builds where OpenCV is not installed.
- Migrate incrementally with clear safety gates.
- Preserve existing color/channel expectations (BGR-heavy call sites) during transition.

## Non-Goals

- Do not rewrite OCR logic and matching algorithms in one pass.
- Do not switch the entire codebase from BGR to RGB conventions in this migration.
- Do not require exact pixel equality across all backends for advanced CV ops; allow tolerance-based parity where needed.

## Current CV2 Usage Buckets

Based on current usage patterns, the `imgio` API should cover these groups:

1. File I/O and conversion
- `imread`, `imwrite`, channel conversion (`RGBA->RGB`, `RGB<->BGR`, `RGB->GRAY`, `GRAY->RGB/BGR`)

2. Basic transforms and pixel ops
- `resize` (nearest/area/lanczos behavior)
- `LUT`
- `in_range`, `count_nonzero`, `find_nonzero`, `bounding_rect`
- simple masked operations (`bitwise_and` with mask)

3. Drawing and debug rendering
- `line`, `rectangle`, `circle`, `polylines`, `put_text`

4. Advanced ops
- `match_template`
- `get_perspective_transform` + `warp_perspective`
- optional morphology/blur/threshold helpers used by OCR preprocessing

## Proposed Package Layout

```text
src/wuwa_inventory_kamera/imgio/
  __init__.py
  api.py                 # stable functions used by callers
  enums.py               # interpolation/color conversion enums
  types.py               # image aliases, protocol types
  capabilities.py        # backend capability flags
  registry.py            # backend selection and lazy init
  errors.py              # unsupported op / backend errors
  backends/
    __init__.py
    cv2_backend.py
    pillow_backend.py
    skimage_backend.py   # optional (advanced ops)
```

## Public API (v1 Surface)

Keep the surface intentionally small and only include currently needed operations.

```python
# I/O
imread(path: str | Path, mode: str = "color") -> np.ndarray | None
imwrite(path: str | Path, image: np.ndarray) -> bool

# Color/format conversion
convert_color(image: np.ndarray, code: ColorCode) -> np.ndarray

# Transform
resize(image: np.ndarray, size: tuple[int, int], interpolation: Interp) -> np.ndarray

# Pixel/mask ops
lut(image: np.ndarray, table: np.ndarray) -> np.ndarray
in_range(image: np.ndarray, low: np.ndarray, high: np.ndarray) -> np.ndarray
count_nonzero(mask: np.ndarray) -> int
find_nonzero(mask: np.ndarray) -> np.ndarray | None
bounding_rect(points: np.ndarray) -> tuple[int, int, int, int]
bitwise_and(src1: np.ndarray, src2: np.ndarray, mask: np.ndarray | None = None) -> np.ndarray

# Drawing
line(...)
rectangle(...)
circle(...)
polylines(...)
put_text(...)

# Advanced
match_template(image: np.ndarray, template: np.ndarray, method: MatchMethod, mask: np.ndarray | None = None) -> np.ndarray
warp_perspective(image: np.ndarray, src_quad: np.ndarray, dst_size: tuple[int, int]) -> np.ndarray
```

## Backend Strategy

### Selection model

- Config key: `app_config.image_backend` with values `auto|cv2|pillow|skimage`.
- Env override: `WUWA_IMGIO_BACKEND` (same values).
- Default: `auto`.

### Auto mode behavior

1. Prefer `cv2` if installed.
2. Else use `pillow` backend for supported operations.
3. If operation is unavailable in current backend:
- if `skimage` backend is available and operation is advanced, use it
- else raise a clear `ImgioUnsupportedOperationError` with operation + backend + install hint

### Capability checks

Each backend exports capability flags (for example):

- `io_basic`
- `color_basic`
- `resize`
- `draw`
- `mask_ops`
- `template_matching`
- `perspective_warp`

Callers may optionally guard behavior based on capability, but most callers should rely on `imgio.api` and handle raised errors only at top-level workflows.

## Backend Implementation Notes

### cv2 backend

- Thin wrapper around current OpenCV calls.
- Serves as reference behavior during migration.
- No functional changes except API normalization.

### pillow backend (lightweight)

- Use Pillow for file I/O and drawing.
- Use numpy for channel conversion, LUT, masks, bbox, and pixel counting.
- Keep BGR compatibility for legacy callers by converting at boundaries where required.

### skimage/scipy backend (optional advanced)

- Provide `warp_perspective` and `match_template` equivalents when OpenCV is absent.
- Optionally provide morphology/threshold/blur utilities needed by OCR-heavy modules.
- Keep dependency optional via extras.

## Data/Color Contract

To avoid broad churn, define an explicit contract:

- `imgio.imread(..., mode="color")` returns BGR 3-channel arrays by default.
- `mode="unchanged"` preserves channels when possible (including alpha).
- `convert_color` is the only approved conversion entry point.
- New modules should document expected channel order at API boundaries.

Follow-up (out of scope for this plan): evaluate gradual migration to RGB-native internal representation.

## Migration Phases

### Phase 0 - Baseline and safeguards

- Add this plan and finalize API scope.
- Capture baseline tests + freeze/build size metrics.
- Add temporary tracking list of direct `cv2` imports.

Exit criteria:
- Baseline metrics stored.
- Agreed API surface for `imgio` v1.

### Phase 1 - Scaffold `imgio` with cv2 backend

- Introduce package structure and backend registry.
- Implement full v1 API in `cv2_backend`.
- Add contract tests that run against cv2 backend.

Exit criteria:
- No behavior change.
- Existing tests pass with call sites still on direct cv2.
- `imgio` tests green.

### Phase 2 - Add pillow backend for low-risk operations

Implement and validate lightweight replacements for:

- `imread`/`imwrite`
- basic color conversions in common use
- `resize`
- `lut`
- `in_range`/`count_nonzero`/`find_nonzero`/`bounding_rect`
- `bitwise_and` (masked)
- essential drawing helpers used by CLI debug tooling

Exit criteria:
- Contract tests pass on both cv2 and pillow backends for supported ops.
- Parity thresholds documented for non-identical outputs (for example antialiasing/text rendering).

### Phase 3 - Migrate low-risk call sites

Switch direct cv2 imports to `imgio` in:

- scripts/tools (`inspect_pixels.py`, `pixel_count.py`, `nav-scripts/*`, selected `cli/*`)
- tests that only need image file creation/loading
- `scraping/utils/common.py` (current low-complexity cv2 usage)

Exit criteria:
- These modules contain no direct `cv2` import.
- CI passes with `WUWA_IMGIO_BACKEND=pillow` for this subset.

### Phase 4 - Advanced operations path

- Add optional skimage/scipy backend support for:
- `warp_perspective` used by OCR batch warping
- `match_template` used in sonata icon workflows
- any remaining advanced preprocessing helpers that cannot be accurately done with Pillow/numpy only

Exit criteria:
- Advanced-path tests pass with either cv2 or skimage backend.
- Performance + quality accepted for target workloads.

### Phase 5 - Migrate heavy modules and enforce policy

Migrate remaining major modules:

- `scraping/ocr/region_specs.py`
- `scraping/ocr/batch.py`
- `scraping/matching/sonata_icon.py`

Then enforce no new direct imports:

- add lint/check step that fails on `import cv2` outside `imgio/backends/cv2_backend.py` and tightly scoped legacy exceptions

Exit criteria:
- Direct cv2 imports eliminated (or explicitly allowlisted).
- Application behavior verified in scan/reprocess integration tests.

## Dependency and Packaging Plan

1. Add `Pillow` as a core dependency for image I/O/drawing fallback.
2. Move `opencv-python` from required dependency to optional extra (for example `cv2`).
3. Add optional extra (for example `imgio-advanced`) containing `scikit-image` and `scipy`.
4. Update freeze/build config to include backend-specific package sets.
5. Add startup log line indicating active backend and enabled capabilities.

Suggested extras model (example):

- base: `numpy`, `Pillow`
- `cv2`: `opencv-python`
- `imgio-advanced`: `scikit-image`, `scipy`
- app/build/dev extras remain as-is, referencing these where needed

## Testing Strategy

### Contract tests (must-have)

- Single test suite parameterized by backend.
- Validate shapes/dtypes/channel order for each API call.
- For operations with interpolation/antialiasing differences, use tolerances and invariant checks.

### Golden corpus tests

- Reuse representative crops from OCR/matching pipelines.
- Compare OCR output quality and matching scores across backends.
- Track regressions with explicit thresholds.

### Integration tests

- Run selected CLI and workflow tests with:
- `WUWA_IMGIO_BACKEND=cv2`
- `WUWA_IMGIO_BACKEND=pillow`
- `WUWA_IMGIO_BACKEND=auto` without cv2 installed

### Build/freeze validation

- Verify artifact startup and key workflows for both dependency profiles.
- Compare bundle size and startup time against baseline.

## Risks and Mitigations

1. Color order regressions (BGR/RGB confusion)
- Mitigation: strict contract tests + explicit conversion enum + boundary-only conversions.

2. Subtle image quality differences affecting OCR/matching
- Mitigation: golden corpus checks and staged rollout, keep cv2 backend as escape hatch.

3. Performance regressions in pure numpy/Pillow paths
- Mitigation: benchmark critical ops and route heavy ops to cv2/skimage when available.

4. Backend fragmentation and hard-to-debug behavior
- Mitigation: capability reporting, deterministic backend selection, startup diagnostics.

## Rollout Checklist

- [ ] Approve `imgio` v1 API surface.
- [ ] Implement scaffold + cv2 backend.
- [ ] Add contract tests.
- [ ] Implement pillow backend for v1 low-risk operations.
- [ ] Migrate scripts/tests/common utility call sites.
- [ ] Add optional advanced backend for warp/template matching.
- [ ] Migrate OCR/matching heavy modules.
- [ ] Enforce no direct cv2 imports outside backend package.
- [ ] Update dependency extras and freeze profiles.
- [ ] Capture final metrics: test pass rate, bundle size, startup time, OCR quality parity.

## Success Criteria

- `cv2` becomes optional for at least one supported runtime profile.
- All low-risk call sites run on pillow backend with passing tests.
- Advanced workflows run on either cv2 or optional advanced backend.
- No unplanned behavior regressions in OCR/matching acceptance checks.
- Frozen build size decreases measurably for non-cv2 profile.
