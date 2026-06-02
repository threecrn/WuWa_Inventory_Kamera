import numpy as np
from src.wuwa_inventory_kamera import imgio

files = [
    r"captures\test-echo-workflow\2026-05-01_02-07-55\echo_0000\level.png",
    r"captures\test-echo-workflow\2026-05-01_02-07-55\echo_0001\level.png"
]

for f in files:
    img = imgio.imread(f)
    print(f"\n{f} shape: {img.shape if img is not None else 'Error'}")
    if img is not None:
        # Show unique colors
        unique_colors = np.unique(img.reshape(-1, 3), axis=0)
        print(f"Unique BGR colors (first 10): {unique_colors[:10].tolist()}")
        print(f"Max BGR: {img.max(axis=(0,1))}, Min BGR: {img.min(axis=(0,1))}")
