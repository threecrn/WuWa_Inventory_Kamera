Your implementation is a highly elegant, geometrically sound way to handle this problem. By using 3D Euclidean distance in the BGR color space rather than a flat grayscale conversion, you are preserving the color distinctness of your text from the background, which completely bypasses the risk of different colors blending into the same grayscale value.
However, your _anchor_contrast_weight implementation contains a couple of subtle mathematical and NumPy quirks that will break execution, warp your contrast curves, or dramatically slow down your preprocessing pipeline.
## The Bugs and Fixes## 1. The Multi-Channel np.exp Trap (Performance & Logic)
In _anchor_contrast_weight, you write:

return 1.0 / (1.0 + np.exp(sharpness * (dist_norm - 0.5)))

Because dist_norm is a 2D array (height, width) resulting from axis=2 norm, this expression works structurally. However, passing a raw, un-optimized NumPy array directly into np.exp over massive arrays can hit performance bottlenecks. More importantly, check your exponent sign:

* When pixels are close to the text anchor, dist_norm approaches 0.0.
* dist_norm - 0.5 becomes negative (-0.5).
* If sharpness is positive, sharpness * -0.5 is negative.
* 1.0 / (1.0 + exp(-negative)) yields a value close to 1.0 (Correct: High weight for text).
* When pixels are close to the background anchor, dist_norm approaches 1.0. dist_norm - 0.5 is positive. 1.0 / (1.0 + exp(positive)) yields a value close to 0.0 (Correct).

The catch: Your normalization function _normalize_anchor_weight assumes absolute symmetry:

bg_weight = 1.0 / (1.0 + math.exp(sharpness * 0.5))text_weight = 1.0 - bg_weight

Let's evaluate mathematically what your actual weights are at the endpoints based on your formula:

* At text anchor (dist_norm = 0): $W_{text} = \frac{1}{1 + e^{-0.5 \cdot sharpness}}$
* At bg anchor (dist_norm = 1): $W_{bg} = \frac{1}{1 + e^{0.5 \cdot sharpness}}$

Because $e^{-x} = 1 - \frac{1}{1+e^x}$ is true for logistic curves, your text_weight = 1.0 - bg_weight logic holds perfectly! However, the math module will crash or throw a NameError inside your function unless import math is explicitly declared. It is safer and cleaner to stick entirely to np.exp to avoid mixing types.
## 2. Vector Norm Memory Overhead
np.linalg.norm(..., axis=2) is notorious for being slower on images because it doesn't utilize underlying vectorized CPU optimizations (like OpenCV instructions) efficiently. For a rapid OCR pipeline, pre-calculating the squared differences manually is significantly faster.
## The Corrected, Optimized Code
Here is the clean, mathematically sound version with fixed imports, optimized performance, and proper handling of the full-range output:

```python
def _anchor_contrast_weight(
    bgr: np.ndarray,
    *,
    text_anchor_bgr: tuple[int, int, int],
    bg_anchor_bgr: tuple[int, int, int],
    sharpness: float,
) -> np.ndarray | None:
    text_f32 = np.array(text_anchor_bgr, dtype=np.float32)
    bg_f32 = np.array(bg_anchor_bgr, dtype=np.float32)

    anchor_dist = float(np.linalg.norm(text_f32 - bg_f32))
    if anchor_dist < 1.0:
        return None

    # Optimized manual norm along channels (Faster than np.linalg.norm on axis=2)
    diff = bgr.astype(np.float32) - text_f32
    dist_raw = np.sqrt(diff[:, :, 0]**2 + diff[:, :, 1]**2 + diff[:, :, 2]**2)
    dist_norm = dist_raw / anchor_dist

    # Sigmoid centered at 0.5: text (dist 0) gets high weight (~1.0)
    return 1.0 / (1.0 + np.exp(sharpness * (dist_norm - 0.5)))

def _normalize_anchor_weight(weight: np.ndarray, sharpness: float) -> np.ndarray:
    """Stretch the anchor sigmoid so declared endpoints land at 0 and 1."""
    # Using np.exp instead of math.exp to keep dependencies unified
    bg_weight = 1.0 / (1.0 + np.exp(sharpness * 0.5))
    text_weight = 1.0 - bg_weight
    
    span = text_weight - bg_weight
    if abs(span) < 1e-6:
        return np.clip(weight, 0.0, 1.0)
    
    return np.clip((weight - bg_weight) / span, 0.0, 1.0)

def _render_normalized_anchor_contrast(
    bgr: np.ndarray,
    text_anchor_bgr: tuple[int, int, int],
    bg_anchor_bgr: tuple[int, int, int],
    sharpness: float,
) -> np.ndarray:
    weight = _anchor_contrast_weight(
        bgr,
        text_anchor_bgr=text_anchor_bgr,
        bg_anchor_bgr=bg_anchor_bgr,
        sharpness=sharpness,
    )
    if weight is None:
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

    normalized_weight = _normalize_anchor_weight(weight, sharpness)
    
    # Broadcast weights to 3 channels: shape (H, W, 1)
    mask = normalized_weight[:, :, np.newaxis]
    
    # Multiply against original image data to erase the background 
    # but keep the exact text colors intact
    color_preserved = np.clip(bgr.astype(np.float32) * mask, 0.0, 255.0).astype(np.uint8)
    
    return cv2.cvtColor(color_preserved, cv2.COLOR_BGR2RGB)
```

## Why this is perfect for your specific targets:
Using your target metrics from earlier:

* Text: RGB[220, 220, 190] $\rightarrow$ BGR[190, 220, 220]
* Background: RGB[80, 80, 120] $\rightarrow$ BGR[120, 80, 80]

Any "off-color noise" in the background that jitters slightly to RGB[90, 75, 130] will register a massive Euclidean distance from your text anchor. Because your sharpness parameter dictates the steepness of the sigmoid drop-off, those noisy background pixels are aggressively flattened to 0.0 weight, completely wiping them out before they reach RapidOCR's text detector.
If you'd like, let me know:

* What sharpness value you are currently testing (e.g., 10, 20)
* If you need an automated fallback for cases where multiple distinct background colors exist in the same frame

I can show you how to adapt this into a multi-anchor variant!

