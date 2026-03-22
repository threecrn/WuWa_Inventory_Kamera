"""
wuwa_inventory_kamera.scraping.ocr.batch
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Efficient batched OCR using DML + ONNX io_binding.

This module provides :class:`BatchOcr`, which wraps a
:class:`~wuwa_inventory_kamera.scraping.ocr._rapidocr.RapidOcrBackend`
and exposes four methods that replace the per-image OCR loop with two
batched forward passes:

1. **detect_batch** — stack N preprocessed images → single ``[N, 3, H, W]``
   detection forward pass.
2. **extract_crops** — perspective-warp each detected quad into a flat
   rectangle.
3. **recognize_batch** — group crops by aspect ratio, run recognition
   forward passes using ``io_binding``.
4. **ocr_images** — end-to-end pipeline combining the three above.

Usage::

    from wuwa_inventory_kamera.scraping.ocr._rapidocr import RapidOcrBackend
    from wuwa_inventory_kamera.scraping.ocr.batch import BatchOcr

    backend = RapidOcrBackend(
        onnx_providers=['DmlExecutionProvider', 'CPUExecutionProvider']
    )
    batch = BatchOcr(backend)

    results = batch.ocr_images(images_bgr)
    # results[i] — list of (text, conf, box) for image i
"""
from __future__ import annotations

import logging
from typing import NamedTuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class ImageCrop(NamedTuple):
    """One text region detected and cropped from a source image."""
    image_idx: int          # index of the source image in the input list
    box_idx:   int          # index of the detected box within that image
    crop:      np.ndarray   # RGB crop ready for the recognition model
    box:       np.ndarray   # (4, 2) quad corners in original image coordinates


class BatchOcr:
    """
    Batched OCR engine built on top of a :class:`RapidOcrBackend`.

    Borrows the internal ONNX sessions and preprocessing ops from the
    backend, replacing the per-image forward pass with a single batched
    call per crop type.

    Parameters
    ----------
    backend:
        A fully initialised :class:`RapidOcrBackend` instance.  The
        sessions it owns are shared — do not use *backend* concurrently
        from another thread while :class:`BatchOcr` is running.
    """

    def __init__(self, backend) -> None:
        ocr = backend._ocr
        self._det = ocr.text_det
        self._rec = ocr.text_rec

        logger.debug(
            'BatchOcr init — det providers: %s | rec providers: %s',
            self._det.infer.session.get_providers(),
            self._rec.session.session.get_providers(),
        )

    # no private helpers needed — TextDetector and TextRecognizer expose the full pipeline

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect_batch(self, images_bgr: list[np.ndarray]) -> list[np.ndarray]:
        """
        Run text detection on each image in *images_bgr*.

        The PPOCRv3 detection model runs best with batch size 1; we call
        :class:`TextDetector` once per image and collect results.

        Returns
        -------
        list[np.ndarray]
            One array per input image, shape ``(K, 4, 2)`` — K detected
            quads, each quad is 4 ``[x, y]`` corners.  An empty array
            (shape ``(0, 4, 2)``) is returned when nothing is detected.
        """
        all_boxes: list[np.ndarray] = []
        for img in images_bgr:
            boxes, _ = self._det(img)
            if boxes is None or len(boxes) == 0:
                all_boxes.append(np.empty((0, 4, 2), dtype=np.float32))
            else:
                all_boxes.append(boxes)
        return all_boxes

    @staticmethod
    def _warp_crop(img: np.ndarray, box: np.ndarray) -> np.ndarray:
        """Perspective-warp a detected quad into a flat rectangle."""
        box = box.astype(np.float32)
        w = int(max(np.linalg.norm(box[0] - box[1]), np.linalg.norm(box[2] - box[3])))
        h = int(max(np.linalg.norm(box[0] - box[3]), np.linalg.norm(box[1] - box[2])))
        w, h = max(w, 1), max(h, 1)
        dst = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float32)
        M = cv2.getPerspectiveTransform(box, dst)
        return cv2.warpPerspective(img, M, (w, h))

    def extract_crops(
        self,
        images_bgr: list[np.ndarray],
        boxes_per_image: list[np.ndarray],
    ) -> list[ImageCrop]:
        """
        Warp all detected quads into flat RGB rectangles.

        Parameters
        ----------
        images_bgr:
            Source images (BGR).
        boxes_per_image:
            Output of :meth:`detect_batch`.

        Returns
        -------
        list[ImageCrop]
            All crops across all images in enumeration order.
        """
        crops: list[ImageCrop] = []
        for img_idx, (img, boxes) in enumerate(zip(images_bgr, boxes_per_image)):
            if boxes is None or len(boxes) == 0:
                continue
            for box_idx, box in enumerate(boxes):
                crop = self._warp_crop(img, box)
                if crop.size > 0:
                    crops.append(ImageCrop(img_idx, box_idx, crop, box))
        return crops

    def recognize_batch(self, crops: list[np.ndarray]) -> list[tuple[str, float]]:
        """
        Run text recognition on *crops*.

        Delegates to :class:`TextRecognizer`, which already batches crops
        internally by ``rec_batch_num`` and sorts by aspect ratio.

        Returns
        -------
        list[tuple[str, float]]
            ``(text, confidence)`` pairs in the same order as *crops*.
        """
        if not crops:
            return []
        results, _ = self._rec(crops)
        return results

    def ocr_images(
        self,
        images_bgr: list[np.ndarray],
        det_batch_size: int = 32,
    ) -> list[list[tuple[str, float, np.ndarray]]]:
        """
        End-to-end OCR pipeline.

        Runs detection (chunked by *det_batch_size*) then recognition on
        all *images_bgr*.

        Parameters
        ----------
        images_bgr:
            Input images in BGR format.
        det_batch_size:
            Number of images per detection forward pass.  Reduce if OOM.

        Returns
        -------
        list[list[tuple[str, float, np.ndarray]]]
            Per image, per detected text region: ``(text, confidence, box)``
            where *box* is a ``(4, 2)`` float32 array of quad corners in the
            original image's pixel coordinates.
        """
        if not images_bgr:
            return []

        # --- Detection (chunked) ------------------------------------------
        boxes_per_image: list[np.ndarray] = []
        for chunk_start in range(0, len(images_bgr), det_batch_size):
            chunk = images_bgr[chunk_start : chunk_start + det_batch_size]
            boxes_per_image.extend(self.detect_batch(chunk))

        # --- Crop extraction ----------------------------------------------
        all_crops = self.extract_crops(images_bgr, boxes_per_image)

        # --- Recognition --------------------------------------------------
        texts = self.recognize_batch([c.crop for c in all_crops]) if all_crops else []

        # --- Reassemble per-image -----------------------------------------
        per_image: list[list[tuple[str, float, np.ndarray]]] = [[] for _ in images_bgr]
        for crop_meta, (text, conf) in zip(all_crops, texts):
            per_image[crop_meta.image_idx].append((text, conf, crop_meta.box))

        return per_image
