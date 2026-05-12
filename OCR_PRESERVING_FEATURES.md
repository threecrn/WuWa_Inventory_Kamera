## Feature-Preserving Contrast Enhancement pipeline 

By staying in the 3-channel 8-bit space and avoiding a binary mask, you are treating the image as a continuous signal rather than a discrete set of "on/off" pixels. This allows you to maximize the "signal-to-noise" ratio (text vs. background) while preserving the structural gradients (anti-aliasing) that the OCR engine needs.

Here is a blueprint for how you can implement this "Pre-filtering" step effectively:

### 1. The "Anchor" Identification
First, you need to define your "anchors." Since you already have `text_color_ranges` and `background_color_ranges`, you don't need to guess.
*   **Text Anchor ($C_{text}$):** The midpoint of your `text_color_ranges`.
*   **Background Anchor ($C_{bg}$):** The midpoint of your `t_background_color_ranges` (or the average of the area outside your text ranges).

### 2. The "Distance-Based Weighting" (The Filter)
Instead of a hard cut, create a **Spatial Weight Map** based on color distance.

1.  **Distance Map ($D$):** For every pixel, calculate the Euclidean distance (in LAB or RGB) to the $C_{text}$.
    $$D(x, y) = \| \text{Pixel}(x, y) - C_{text} \|$$
2.  **Sigmoid Normalization:** Pass this distance through a Sigmoid function (or a similar smooth S-curve). This creates a "soft" transition.
    $$W(x, y) = \frac{1}{1 + e^{k(D(x, y) - \text{threshold})}}$$
    *   *Note: The $k$ parameter controls how "sharp" the edge is. A high $k$ approaches a binary mask; a low $k$ keeps it very blurry/smooth.*

### 3. The "Contrast Re-projection"
Now, use this weight $W$ to "re-project" the image. You want to push pixels toward the $C_{text}$ and $C_{bg}$ based on their proximity.

$$ \text{Pixel}_{new} = (W \cdot C_{text}) + ((1 - W) \cdot C_{bg}) $$

**Why this is powerful:**
*   **Text Strengthening:** Pixels that were already "text-like" get pushed closer to a pure, saturated $C_{text}$.
*   **Background Cleaning:** Pixially that were "background-like" get pushed toward a clean $C_{bg}$ (like white or grey).
*   **Edge Preservation:** The pixels in the transition zone (the anti-aliasing) are shifted, but they **remain in the middle**. They don't jump to one side or the other; they just "settle" into a cleaner version of the gradient.

### 4. Implementation Tips for Python/OpenCV

If you are implementing this in your preprocessing logic, here is how you can structure it:

```python
import cv2
import numpy as np

def enhance_contrast_3channel(image, text_anchor, bg_anchor, sharpness=10.0):
    """
    image: The 3-channel BGR image.
    text_anchor: BGR tuple representing the target text color.
    bg_anchor: BGR tuple representing the target background color.
    sharpness: Controls the steepness of the transition (the 'k' in sigmoid).
    """
    # 1. Calculate distance from each pixel to the text anchor
    # We use absolute difference and then a norm
    diff = image.astype(np.float32) - np.array(text_anchor, dtype=np.float32)
    dist = np.linalg.norm(diff, axis=2)

    # 2. Create the weight map (Sigmoid)
    # We need a threshold. A good one is the average distance in the text range.
    # For simplicity, let's assume a fixed threshold or calculate from ranges.
    threshold = 50.0 # This should ideally be derived from your color_ranges
    
    # Sigmoid: 1 / (1 + exp(k * (dist - threshold)))
    # We negate the distance inside to make high distance -> low weight
    weight = 1.0 / (1.0 + np.exp(sharpness * (dist - threshold)))
    
    # Add a channel dimension to weight for broadcasting
    weight = weight[..., np.newaxis]

    # 3. Interpolate
    # New Pixel = Weight * Text_Color + (1 - Weight) * BG_Color
    target_text = np.array(text_anchor, dtype=np.float32)
    target_bg = np.array(bg_anchor, dtype=np.float32)
    
    enhanced = (weight * target_text) + ((1.0 - weight) * target_bg)
    
    return enhanced.astype(np.uint8)
```

### Summary of Benefits for RapidOCR
1.  **No "Ghosting":** Because you are using the anchors, you aren't leaving behind bits of the original "dirty" background.
2.  **Feature Stability:** The 3-channel structure remains intact, so the OCR can still use color information if it needs to (e.g., distinguishing between blue and red text).
3.  **Gradient Preservation:** The `sharpness` parameter allows you to tune the filter specifically to the level of anti-aliasing present in your source images, ensuring the engine always sees "smooth" characters.