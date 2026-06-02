# CV2 usage & replacement analysis

Summary
- Files importing/using `cv2`: ~28 modules (see list below).
- Main usage categories: image I/O (imread/imwrite), color conversions (cvtColor), resizing, drawing/annotation (line/rect/circle/putText), display (imshow/waitKey), basic masks and morphology, filtering/CLAHE, LUT, template matching (matchTemplate), perspective warp (getPerspectiveTransform / warpPerspective), and simple pixel utilities (inRange/countNonZero/findNonZero/boundingRect).

High-level recommendation
- Replace the *trivial* parts (image I/O, simple color swaps, resizing, saving, and drawing) with Pillow (`PIL`) + `numpy` first — low effort and removes a lot of binary weight from OpenCV.
- For intermediate tasks (resize with controlled resampling, channel swaps, binary mask logic, `inRange`, `countNonZero`, simple bitwise operations) use `numpy` + `Pillow` or small helpers.
- For advanced CV ops (CLAHE, morphology, warpPerspective, template matching, LAB/HSV conversions), prefer `scikit-image`/`scipy` equivalents where available. These are lighter than full OpenCV in many distributions but still bring native deps — evaluate trade-offs for your freezer.
- Make `cv2` optional via a small image-backend shim (see suggested `image_backend` below). Keep `cv2` as a fast optional path; default to Pillow/numpy so frozen builds don't include OpenCV unless explicitly opted in.

Common mappings (examples)
- Read / write

```py
# cv2.imread -> Pillow (returning BGR to preserve existing callers)
from PIL import Image
import numpy as np

def imread_pil_bgr(path, *, unchanged=False):
    im = Image.open(path)
    if unchanged:
        im = im.convert('RGBA')
    else:
        im = im.convert('RGB')
    arr = np.asarray(im)
    # OpenCV returns BGR; if code expects BGR keep compatibility
    if arr.ndim == 3 and arr.shape[2] == 3:
        return arr[..., ::-1].copy()
    return arr

# cv2.imwrite -> Pillow
from PIL import Image

def imwrite_pil_bgr(path, arr):
    # arr expected BGR; convert to RGB for PIL
    if arr.ndim == 3 and arr.shape[2] == 3:
        arr = arr[..., ::-1]
    Image.fromarray(arr).save(path)
```

- Color swaps & simple conversions

```py
# BGR <-> RGB
rgb = bgr[..., ::-1]
# BGR -> GRAY (fast integer approx matching OpenCV behaviour)
gray = ( (77*bgr[...,2] + 150*bgr[...,1] + 29*bgr[...,0]) >> 8 ).astype(np.uint8)
```

- Resize

```py
from PIL import Image

pil = Image.fromarray(img[..., ::-1])  # BGR -> RGB
resized = np.asarray(pil.resize((w, h), resample=Image.Resampling.LANCZOS))[..., ::-1]
```

- Drawing (circle/rect/text)

```py
from PIL import Image, ImageDraw, ImageFont
im = Image.fromarray(bgr[..., ::-1])
d = ImageDraw.Draw(im)
d.ellipse((x-r,y-r,x+r,y+r), fill=(255,0,0))
# For text use ImageFont.truetype if you need TTF font control
```

- Bitwise, masks, pixel counters

```py
mask = np.all((img >= lo) & (img <= hi), axis=2)
count = int(np.count_nonzero(mask))
coords = np.column_stack(np.nonzero(mask))  # similar to cv2.findNonZero
if coords.size:
    ys, xs = coords[:,0], coords[:,1]
    x, y, w, h = xs.min(), ys.min(), xs.max()-xs.min()+1, ys.max()-ys.min()+1
```

- CLAHE / Otsu / morphology / filters

Use `skimage` / `scipy` equivalents when available (example CLAHE):

```py
from skimage import exposure, filters, morphology
import numpy as np

clahe = exposure.equalize_adapthist(gray/255.0, clip_limit=0.01)
clahe_u8 = (np.clip(clahe*255.0, 0, 255)).astype(np.uint8)
th = filters.threshold_otsu(clahe_u8)
bin = (clahe_u8 > th).astype(np.uint8) * 255
closed = morphology.closing(bin, morphology.square(2))
```

- Perspective warp

Use `skimage.transform.ProjectiveTransform` + `warp`:

```py
from skimage.transform import ProjectiveTransform, warp
import numpy as np

src = src_quad.astype(np.float32)
dst = dst_quad.astype(np.float32)
t = ProjectiveTransform()
t.estimate(dst, src)  # note order — estimate(dst, src) gives t mapping dst->src
warped = warp(img[..., ::-1], t.inverse, output_shape=(h, w))
warped_u8 = (np.clip(warped*255.0,0,255)).astype(np.uint8)[..., ::-1]
```

- Template matching

`skimage.feature.match_template` is a drop-in for many use-cases (returns correlation map). If you rely on masked templates (cv2 supports masked templates in later versions), you may need a small masked NCC implementation (the repo already contains `_ncc_masked` in `sonata_icon.py`).

Per-file summary (where `cv2` is used and replacement notes)
- [cli/calibrate_ocr.py](cli/calibrate_ocr.py)
  - Uses: `imread`, `imwrite`, `cvtColor`, `resize`, `circle`, `putText`, `imshow`, `waitKey`, `destroyWindow`, `LUT`.
  - Replacement: I/O, color swaps, resize, drawing → Pillow/numpy (easy). Interactive `imshow` → optional `matplotlib`/PIL (debug-only). LUT → `np.take` (easy). Overall: medium.

- [inspect_pixels.py](inspect_pixels.py)
  - Uses: `imread` only. Replacement: Pillow (easy).

- [pixel_count.py](pixel_count.py)
  - Uses: `imread`, `inRange`, `countNonZero`, `findNonZero`, `boundingRect`.
  - Replacement: Pillow + numpy for masks and bbox (easy).

- [cli/compare_ocr.py](cli/compare_ocr.py), [cli/debug_ocr.py](cli/debug_ocr.py), [cli/tune_region_spec.py](cli/tune_region_spec.py)
  - Uses: I/O, color conversions, drawing, resize, imwrite. Replacement: Pillow + numpy (easy→medium).

- [tools/update_sonata_templates/main.py](tools/update_sonata_templates/main.py), [src/wuwa_inventory_kamera/cli/detect_sonata_icon.py](src/wuwa_inventory_kamera/cli/detect_sonata_icon.py), [nav-scripts/scan-sonata-icons.py](nav-scripts/scan-sonata-icons.py)
  - Uses: `imread`/`imwrite`, `resize`, `bitwise_and`, `matchTemplate`.
  - Replacement: I/O/resize/masks → Pillow + numpy (easy). `matchTemplate` → `skimage.feature.match_template` or custom NCC (medium).

- [src/wuwa_inventory_kamera/scraping/matching/sonata_icon.py](src/wuwa_inventory_kamera/scraping/matching/sonata_icon.py)
  - Uses: `imread` (IMREAD_UNCHANGED), `resize`, `bitwise_and`. Replacement: Pillow open as RGBA and `numpy` -> easy. The module already contains a masked NCC (`_ncc_masked`) — this is portable.

- [src/wuwa_inventory_kamera/scraping/models/raw_scan.py](src/wuwa_inventory_kamera/scraping/models/raw_scan.py)
  - Uses: `imread`, `cvtColor(BGR->RGB)` on load. Replacement: Pillow + `np.asarray` + channel swap (easy).

- [src/wuwa_inventory_kamera/scraping/ocr/region_specs.py](src/wuwa_inventory_kamera/scraping/ocr/region_specs.py)
  - Heavy use: color conversion (RGB/BGR/HSV/LAB), CLAHE, Gaussian blur, thresholding (Otsu), LUT, morphology, filter2D, resizing with binary-preserve logic.
  - Replacement: Most operations map to `skimage` / `scipy.ndimage` functions. Implementing a fully equivalent pipeline without `scikit-image`/`scipy` is possible but more work. Complexity: medium→high (this is the most CV-heavy module).

- [src/wuwa_inventory_kamera/scraping/utils/common.py](src/wuwa_inventory_kamera/scraping/utils/common.py)
  - Uses: screenshot RGBA→RGB conversion, `createCLAHE`, Gaussian blur, threshold, morphology, filter2D, LUT.
  - Replacement: `skimage.exposure.equalize_adapthist`, `skimage.filters`, `scipy.ndimage` equivalents; medium effort.

- [src/wuwa_inventory_kamera/scraping/ocr/batch.py](src/wuwa_inventory_kamera/scraping/ocr/batch.py)
  - Uses: `getPerspectiveTransform` + `warpPerspective` for cropping detected quads.
  - Replacement: `skimage.transform.ProjectiveTransform` + `warp` (medium effort).

- [nav-scripts/*] and small tools
  - Mostly I/O, color swaps and write-outs (`imread`/`imwrite`/`cvtColor`) — easy to swap to Pillow.

- Tests (many under `tests/` and `session_tests/`)
  - Use `cv2` to write tiny placeholder images and asserts. Replace with `PIL` for test-only image I/O (easy).

Suggested migration strategy
1. Add a small compatibility shim e.g. `src/wuwa_inventory_kamera/image_backend.py` implementing the minimal functions used across the repo with a `cv2` fast path and a Pillow/numpy fallback. Keep the shim surface small (imread, imwrite, resize, convert_bgr_rgb, draw helpers, basic masks).

2. Replace trivial calls (scripts, tools, tests) to use the shim. This immediately drops OpenCV from many frozen entry points.

3. For heavier modules (`region_specs`, `scraping.utils.common`) evaluate two options:
   - Add `scikit-image`/`scipy` to the project extras (e.g. `extras_require["cv_alts"]`) and implement advanced fallbacks using them. Keep `cv2` optional.
   - Or implement only the required subset of functionality with `numpy` + small custom functions (more work, but reduces native deps).

4. Keep unit tests updated and add tests for shim fallbacks.

Example shim sketch

```py
# src/wuwa_inventory_kamera/image_backend.py
try:
    import cv2
    HAVE_CV2 = True
except Exception:
    HAVE_CV2 = False

from PIL import Image
import numpy as np

def imread(path, *, unchanged=False):
    if HAVE_CV2:
        flag = cv2.IMREAD_UNCHANGED if unchanged else cv2.IMREAD_COLOR
        return cv2.imread(str(path), flag)
    im = Image.open(path)
    if unchanged:
        im = im.convert('RGBA')
    else:
        im = im.convert('RGB')
    arr = np.asarray(im)
    # Default callers expect BGR arrays — preserve compatibility
    if arr.ndim == 3 and arr.shape[2] == 3:
        return arr[..., ::-1].copy()
    return arr

# similar imwrite(), resize(), to_bgr()/to_rgb() helpers …
```

Notes & caveats
- Channel order: OpenCV uses BGR. If you migrate to Pillow you can either (a) change codebase conventions to use RGB (more correct in Python land but larger refactor), or (b) preserve BGR in shim for minimal code churn.
- `scikit-image` and `scipy` are lighter than OpenCV for some installers, but they still introduce native wheels. Test your freezer (cx_Freeze / PyInstaller) to measure final binary size.
- Some functions (e.g., masked `matchTemplate` behaviour, exact LAB conversions or the CLAHE parameters) may produce subtly different results — run visual/regression tests.
- `cv2.imshow` and windowing functions are debugging conveniences — consider gating behind a `--show` flag and preferring `matplotlib` or `PIL.Image.show` when `cv2` is unavailable.

Short action plan
- [ ] Add `image_backend` shim and use it for all `imread`/`imwrite` calls in tools/scripts/tests (low effort).
- [ ] Replace drawing/annotation code paths in `src/wuwa_inventory_kamera/cli/nav.py` and `cli/*` with Pillow draw calls (low→medium).
- [ ] Evaluate `region_specs` and `scraping.utils.common` to decide between `scikit-image` vs custom numpy implementations for CLAHE/morphology/filters (medium→high).
- [ ] For matching/warping, port `warpPerspective` & `matchTemplate` uses to `skimage` equivalents (medium).

If you want, I can:
- Create the `image_backend` shim and replace a small set of safe call sites (tools + tests) as a first PR to reduce the frozen size quickly.
- Prototype a `skimage`-based replacement for `_warp_crop()` in `src/wuwa_inventory_kamera/scraping/ocr/batch.py` and a PIL-based `imread/imwrite` shim.
