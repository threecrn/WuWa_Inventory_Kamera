import numpy as np
from src.wuwa_inventory_kamera import imgio

files = [
    r"captures\test-echo-workflow\2026-05-01_02-07-55\echo_0000\level.png",
    r"captures\test-echo-workflow\2026-05-01_02-07-55\echo_0001\level.png"
]

ranges = {
    "signature": ([165, 181, 182], [172, 186, 189]),
    "OCR": ([150, 160, 170], [175, 190, 195])
}

for f in files:
    img = imgio.imread(f)
    if img is None:
        print(f"Error loading {f}")
        continue
    print(f"\nImage: {f}")
    for name, (low, high) in ranges.items():
        mask = imgio.in_range(img, np.array(low, dtype=np.uint8), np.array(high, dtype=np.uint8))
        count = imgio.count_nonzero(mask)
        coords = imgio.find_nonzero(mask)
        bbox = "N/A"
        if coords is not None:
            x, y, w, h = imgio.bounding_rect(coords)
            bbox = f"x={x},y={y},w={w},h={h}"
        print(f"  {name}: count={count}, bbox={bbox}")
