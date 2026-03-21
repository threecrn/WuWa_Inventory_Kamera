"""
batch_ocr.py — Efficient batch OCR of many same-size images using RapidOCR,
               DirectML (DmlExecutionProvider) and ONNX io_binding.

The standard RapidOCR pipeline processes images one at a time:
  for each image → preprocess → det forward → decode boxes →
                   crop regions → rec forward → decode text

This script replaces the per-image loop with two batched forward passes:
  1.  Stack all N preprocessed images → single [N, 3, H, W] det forward
  2.  Collect all crops across images → batched rec forward (grouped by width)

io_binding removes the per-call Python-↔-driver overhead and lets the DML
driver coalesce work without waiting for the Python GIL between calls.

Usage:
    python batch_ocr.py                          # uses built-in test images
    python batch_ocr.py K:/wuwa/export/.../raw   # scans echo_*/debug/stats_name.png
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import NamedTuple

import cv2
import numpy as np
import onnxruntime as ort

# ---------------------------------------------------------------------------
# 1.  Build DML-enabled RapidOCR sessions (reuse the project's patch)
# ---------------------------------------------------------------------------

from scraping.ocr._rapidocr import RapidOcrBackend

_PROVIDERS = ['DmlExecutionProvider', 'CPUExecutionProvider']

# Build one backend just to borrow the session objects and preprocessing ops.
_backend = RapidOcrBackend(onnx_providers=_PROVIDERS)

_ocr          = _backend._ocr
_det          = _ocr.text_detector
_rec          = _ocr.text_recognizer

DET_SESSION: ort.InferenceSession = _det.infer.session
REC_SESSION: ort.InferenceSession = _rec.session.session  # OrtInferSession → .session

print("Det providers:", DET_SESSION.get_providers())
print("Rec providers:", REC_SESSION.get_providers())

DET_INPUT_NAME  = DET_SESSION.get_inputs()[0].name   # 'x'
DET_OUTPUT_NAME = DET_SESSION.get_outputs()[0].name  # 'sigmoid_0.tmp_0'
REC_INPUT_NAME  = REC_SESSION.get_inputs()[0].name   # 'x'
REC_OUTPUT_NAME = REC_SESSION.get_outputs()[0].name  # 'softmax_5.tmp_0'

# ---------------------------------------------------------------------------
# 2.  Detection preprocessing (reuse rapidocr's own ops)
# ---------------------------------------------------------------------------
# DetResizeForTest scales the shorter side to at least limit_side_len (736),
# then rounds dimensions to multiples of 32.  For same-size input images the
# output shape is identical across the whole batch, so no padding is needed.

from rapidocr_onnxruntime.ch_ppocr_v3_det.utils import create_operators, transform
from rapidocr_onnxruntime.ch_ppocr_v3_det.utils import DBPostProcess

_DET_OPS = create_operators({
    'DetResizeForTest': {'limit_side_len': 736, 'limit_type': 'min'},
    'NormalizeImage': {
        'std': [0.229, 0.224, 0.225],
        'mean': [0.485, 0.456, 0.406],
        'scale': '1./255.',
        'order': 'hwc',
    },
    'ToCHWImage': None,
    'KeepKeys': {'keep_keys': ['image', 'shape']},
})

_DET_POST = DBPostProcess(
    thresh=0.3, box_thresh=0.5, max_candidates=1000,
    unclip_ratio=1.6, use_dilation=True, score_mode='fast',
)


def _det_preprocess(img_bgr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Returns (chw_float32, shape_list) for one image."""
    data = transform({'image': img_bgr}, _DET_OPS)
    chw, shape = data
    return chw.astype(np.float32), np.array(shape, dtype=np.float32)


# ---------------------------------------------------------------------------
# 3.  Batched detection with io_binding
# ---------------------------------------------------------------------------

def detect_batch(images_bgr: list[np.ndarray]) -> list[np.ndarray]:
    """
    Run text detection on *images_bgr* in a single batched forward pass.

    Returns a list of box arrays (one per input image).  Each array has shape
    (K, 4, 2) — K detected quads, each quad is 4 (x, y) corners.
    """
    # --- preprocess ---
    preprocessed = [_det_preprocess(img) for img in images_bgr]
    chw_list   = [p[0] for p in preprocessed]
    shape_list = [p[1] for p in preprocessed]

    # For same-size inputs all chw tensors have identical shape.
    # For mixed-size inputs you would need to pad to a common shape here.
    batch = np.stack(chw_list, axis=0)                    # [N, 3, H, W]
    shapes = np.stack(shape_list, axis=0)                 # [N, 4]

    # --- io_binding: feed CPU array, get output back on CPU ---
    # DmlExecutionProvider handles the H→D copy internally when you call
    # bind_cpu_input.  bind_output without a device spec returns a CPU array.
    io = DET_SESSION.io_binding()
    io.bind_cpu_input(DET_INPUT_NAME, batch)
    io.bind_output(DET_OUTPUT_NAME)
    DET_SESSION.run_with_iobinding(io)

    heatmap_batch: np.ndarray = io.get_outputs()[0].numpy()  # [N, 1, H, W]

    # --- per-image postprocessing (CPU, cannot be batched) ---
    all_boxes: list[np.ndarray] = []
    for i, img in enumerate(images_bgr):
        hm = heatmap_batch[i : i + 1]          # keep batch dim: [1, 1, H, W]
        sh = shapes[i : i + 1]                 # [1, 4]
        post = _DET_POST(hm, sh)

        raw_boxes = post[0]['points']           # (K, 4, 2) or empty
        boxes = _det.filter_tag_det_res(raw_boxes, img.shape[:2])
        all_boxes.append(boxes)

    return all_boxes


# ---------------------------------------------------------------------------
# 4.  Crop text regions from detected boxes
# ---------------------------------------------------------------------------

class ImageCrop(NamedTuple):
    image_idx: int         # which source image this crop came from
    box_idx:   int         # which box within that image
    crop:      np.ndarray
    box:       np.ndarray  # (4, 2) quad corners in original image coordinates


def _warp_crop(img: np.ndarray, box: np.ndarray, pad: int = 2) -> np.ndarray:
    """Perspective-warp a detected quad into a flat rectangle."""
    box = box.astype(np.float32)
    w = int(max(np.linalg.norm(box[0] - box[1]), np.linalg.norm(box[2] - box[3])))
    h = int(max(np.linalg.norm(box[0] - box[3]), np.linalg.norm(box[1] - box[2])))
    w, h = max(w, 1), max(h, 1)

    dst = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float32)
    M = cv2.getPerspectiveTransform(box, dst)
    return cv2.warpPerspective(img, M, (w, h))


def extract_crops(images_bgr: list[np.ndarray],
                  boxes_per_image: list[np.ndarray]) -> list[ImageCrop]:
    """Return all text crops across all images, labelled by source index."""
    crops: list[ImageCrop] = []
    for img_idx, (img, boxes) in enumerate(zip(images_bgr, boxes_per_image)):
        if boxes is None or len(boxes) == 0:
            continue
        # rapidocr expects RGB crops for recognition
        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        for box_idx, box in enumerate(boxes):
            crop = _warp_crop(img_rgb, box)
            if crop.size > 0:
                crops.append(ImageCrop(img_idx, box_idx, crop, box))
    return crops


# ---------------------------------------------------------------------------
# 5.  Batched recognition with io_binding
# ---------------------------------------------------------------------------

_REC_H      = _rec.rec_image_shape[1]   # 48
_REC_MAX_W  = _rec.rec_image_shape[2]   # 320  (can grow for wide crops)
_REC_BATCH  = _rec.rec_batch_num        # 6


def _rec_preprocess_batch(crops: list[np.ndarray]) -> np.ndarray:
    """
    Resize-and-normalise a list of crops that will be padded to the same width.

    All crops in one batch call must be padded to *max_wh_ratio* of that batch
    (same logic as TextRecognizer.resize_norm_img + padding).
    """
    max_ratio = max(c.shape[1] / float(c.shape[0]) for c in crops)
    norm_imgs = [_rec.resize_norm_img(c, max_ratio) for c in crops]
    return np.stack(norm_imgs, axis=0).astype(np.float32)  # [B, 3, 48, W]


def recognize_batch(crops: list[np.ndarray]) -> list[tuple[str, float]]:
    """
    Run text recognition on *crops* using batched io_binding calls.

    Returns a list of (text, confidence) pairs in the same order as *crops*.
    """
    # Sort by aspect ratio so crops in the same batch need minimal padding.
    ratios  = [c.shape[1] / float(c.shape[0]) for c in crops]
    order   = np.argsort(ratios)
    results = [('', 0.0)] * len(crops)

    for start in range(0, len(crops), _REC_BATCH):
        idx_slice = order[start : start + _REC_BATCH]
        batch_crops = [crops[i] for i in idx_slice]

        # B: pre-process batch (resize + normalise) → [B, 3, 48, W]
        norm_batch = _rec_preprocess_batch(batch_crops)  # [B, 3, 48, W]

        # io_binding: input on CPU (DML copies to device), output back to CPU.
        # If you were chaining with another model you could bind the output to
        # the DML device with:  io.bind_output(name, 'directml', 0)
        # and only call .numpy() when you actually need the data on the CPU.
        io = REC_SESSION.io_binding()
        io.bind_cpu_input(REC_INPUT_NAME, norm_batch)
        io.bind_output(REC_OUTPUT_NAME)
        REC_SESSION.run_with_iobinding(io)

        # B: post-process batch (decode text) → list of (text, conf)
        logits: np.ndarray = io.get_outputs()[0].numpy()  # [B, T, vocab]
        decoded = _rec.postprocess_op(logits)             # list of (text, conf)

        for local_i, global_i in enumerate(idx_slice):
            results[global_i] = decoded[local_i]

    return results


# ---------------------------------------------------------------------------
# 6.  End-to-end pipeline
# ---------------------------------------------------------------------------

def ocr_images(
    images_bgr: list[np.ndarray],
    det_batch_size: int = 32,
) -> list[list[tuple[str, float]]]:
    """
    Run the full OCR pipeline on *images_bgr*.

    *det_batch_size* controls how many images are sent to the detection model
    in one forward pass.  Larger values are faster but use more GPU memory.
    A value of 16–64 is a good starting point; tune to your VRAM budget.

    Returns a list (one per image) of lists of ``(text, confidence, box)``
    tuples, one per detected text region, in detection order.
    ``box`` is a ``(4, 2)`` float32 array of quad corners in the original
    image's pixel coordinates (top-left, top-right, bottom-right, bottom-left).
    """
    t0 = time.perf_counter()

    # --- Detection (chunked over det_batch_size) ---------------------------
    boxes_per_image: list[np.ndarray] = []
    for chunk_start in range(0, len(images_bgr), det_batch_size):
        chunk = images_bgr[chunk_start : chunk_start + det_batch_size]
        boxes_per_image.extend(detect_batch(chunk))

    t1 = time.perf_counter()

    # --- Crop extraction ---------------------------------------------------
    all_crops = extract_crops(images_bgr, boxes_per_image)
    t2 = time.perf_counter()

    # --- Recognition (internally chunked by rec_batch_num) -----------------
    if all_crops:
        texts = recognize_batch([c.crop for c in all_crops])
    else:
        texts = []
    t3 = time.perf_counter()

    print(f"  det={t1-t0:.3f}s  crop={t2-t1:.3f}s  rec={t3-t2:.3f}s"
          f"  total={t3-t0:.3f}s  ({len(images_bgr)} images, {len(all_crops)} crops)")

    # Reassemble per-image results — each entry is (text, conf, box)
    # where box is a (4, 2) array of quad corners in original image coordinates.
    per_image: list[list[tuple[str, float, np.ndarray]]] = [[] for _ in images_bgr]
    for crop_meta, text_result in zip(all_crops, texts):
        text, conf = text_result
        per_image[crop_meta.image_idx].append((text, conf, crop_meta.box))

    return per_image


# ---------------------------------------------------------------------------
# 7.  Main demo
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import sys
    import argparse

    parser = argparse.ArgumentParser(description='Batch OCR with RapidOCR + DML')
    parser.add_argument('raw_dir', nargs='?', help='Root dir to glob echo_*/debug/stats_name.png')
    parser.add_argument('--det-batch', type=int, default=32, metavar='N',
                        help='Images per detection batch (default 32; lower if OOM)')
    parser.add_argument('--limit', type=int, default=None, metavar='N',
                        help='Process only the first N images (for quick tests)')
    args = parser.parse_args()

    if args.raw_dir:
        raw_root = Path(args.raw_dir)
        image_paths = sorted(raw_root.glob('echo_*/debug/stats_name.png'))
    else:
        print("No raw dir given — using synthetic images.")
        image_paths = []

    if image_paths:
        if args.limit:
            image_paths = image_paths[:args.limit]
        print(f"Loading {len(image_paths)} stats_name.png images …")
        images = [img for p in image_paths
                  if (img := cv2.imread(str(p))) is not None]
        print(f"Loaded {len(images)} valid images.")
    else:
        rng = np.random.default_rng(42)
        images = [rng.integers(0, 256, (380, 360, 3), dtype=np.uint8) for _ in range(10)]

    if not images:
        print("No images to process.")
        sys.exit(1)

    print(f"\nImage shape: {images[0].shape}  |  det_batch={args.det_batch}")
    print("Running batched OCR …\n")

    results = ocr_images(images, det_batch_size=args.det_batch)

    print("\n--- Results ---")
    for img_idx, regions in enumerate(results):
        src = image_paths[img_idx].parent.parent.name if image_paths else f"img_{img_idx:04d}"
        if regions:
            for text, conf, box in regions:
                tl = box[0].astype(int)
                br = box[2].astype(int)
                print(f"  {src}: {text!r:40s}  conf={conf:.2f}  box=({tl[0]},{tl[1]})-({br[0]},{br[1]})")
        else:
            print(f"  {src}: (no text detected)")
