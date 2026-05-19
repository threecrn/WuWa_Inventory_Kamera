import cv2
import numpy as np
import os

files = [
    r"captures\test-echo-workflow\2026-05-01_02-07-55\echo_0000\level.png",
    r"captures\test-echo-workflow\2026-05-01_02-07-55\echo_0001\level.png"
]

ranges = {
    "signature": ([165, 181, 182], [172, 186, 189]),
    "OCR": ([150, 160, 170], [175, 190, 195])
}

for f in files:
    img = cv2.imread(f)
    if img is None:
        print(f"Error loading {f}")
        continue
    print(f"\nImage: {f}")
    for name, (low, high) in ranges.items():
        mask = cv2.inRange(img, np.array(low), np.array(high))
        count = cv2.countNonZero(mask)
        coords = cv2.findNonZero(mask)
        bbox = "N/A"
        if coords is not None:
            x, y, w, h = cv2.boundingRect(coords)
            bbox = f"x={x},y={y},w={w},h={h}"
        print(f"  {name}: count={count}, bbox={bbox}")
