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
        self._det = ocr.text_detector
        self._rec = ocr.text_recognizer

        det_session = self._det.infer.session
        rec_session = self._rec.session.session

        self._det_in  = det_session.get_inputs()[0].name
        self._det_out = det_session.get_outputs()[0].name
        self._rec_in  = rec_session.get_inputs()[0].name
        self._rec_out = rec_session.get_outputs()[0].name

        self._det_session = det_session
        self._rec_session = rec_session

        from rapidocr_onnxruntime.ch_ppocr_v3_det.utils import (
            create_operators,
            DBPostProcess,
        )

        self._det_ops = create_operators({
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

        self._det_post = DBPostProcess(
            thresh=0.3, box_thresh=0.5, max_candidates=1000,
            unclip_ratio=1.6, use_dilation=True, score_mode='fast',
        )

        self._rec_h     = self._rec.rec_image_shape[1]   # 48
        self._rec_batch = self._rec.rec_batch_num         # 6

        logger.debug(
            'BatchOcr init — det providers: %s | rec providers: %s',
            det_session.get_providers(),
            rec_session.get_providers(),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _det_preprocess(self, img_bgr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Returns ``(chw_float32, shape_list)`` for one image."""
        from rapidocr_onnxruntime.ch_ppocr_v3_det.utils import transform
        data = transform({'image': img_bgr}, self._det_ops)
        chw, shape = data
        return chw.astype(np.float32), np.array(shape, dtype=np.float32)

    def _rec_preprocess_batch(self, crops: list[np.ndarray]) -> np.ndarray:
        """
        Resize-and-normalise *crops* to the same width (max aspect ratio
        of the batch), returning a ``[B, 3, 48, W]`` float32 array.
        """
        max_ratio = max(c.shape[1] / float(c.shape[0]) for c in crops)
        norm_imgs = [self._rec.resize_norm_img(c, max_ratio) for c in crops]
        return np.stack(norm_imgs, axis=0).astype(np.float32)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect_batch(self, images_bgr: list[np.ndarray]) -> list[np.ndarray]:
        """
        Run text detection on *images_bgr* in a single batched forward pass.

        All images in one call must have the **same spatial dimensions**
        (height, width).  For same-size images (typical in a single scraper
        session) no padding is needed.

        Returns
        -------
        list[np.ndarray]
            One array per input image, shape ``(K, 4, 2)`` — K detected
            quads, each quad is 4 ``[x, y]`` corners.  An empty array
            (shape ``(0, 4, 2)``) is returned when nothing is detected.
        """
        preprocessed = [self._det_preprocess(img) for img in images_bgr]
        chw_list   = [p[0] for p in preprocessed]
        shape_list = [p[1] for p in preprocessed]

        batch  = np.stack(chw_list,   axis=0)   # [N, 3, H, W]
        shapes = np.stack(shape_list, axis=0)   # [N, 4]

        io = self._det_session.io_binding()
        io.bind_cpu_input(self._det_in, batch)
        io.bind_output(self._det_out)
        self._det_session.run_with_iobinding(io)

        heatmap_batch: np.ndarray = io.get_outputs()[0].numpy()  # [N, 1, H, W]

        all_boxes: list[np.ndarray] = []
        for i, img in enumerate(images_bgr):
            hm = heatmap_batch[i : i + 1]      # [1, 1, H, W]
            sh = shapes[i : i + 1]             # [1, 4]
            post = self._det_post(hm, sh)
            raw_boxes = post[0]['points']
            boxes = self._det.filter_tag_det_res(raw_boxes, img.shape[:2])
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
            img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            for box_idx, box in enumerate(boxes):
                crop = self._warp_crop(img_rgb, box)
                if crop.size > 0:
                    crops.append(ImageCrop(img_idx, box_idx, crop, box))
        return crops

    def recognize_batch(self, crops: list[np.ndarray]) -> list[tuple[str, float]]:
        """
        Run text recognition on *crops* using batched ``io_binding`` calls.

        Crops are sorted by aspect ratio so images in the same sub-batch
        need minimal width-padding.

        Returns
        -------
        list[tuple[str, float]]
            ``(text, confidence)`` pairs in the same order as *crops*.
        """
        if not crops:
            return []

        ratios  = [c.shape[1] / float(c.shape[0]) for c in crops]
        order   = np.argsort(ratios)
        results: list[tuple[str, float]] = [('', 0.0)] * len(crops)

        for start in range(0, len(crops), self._rec_batch):
            idx_slice = order[start : start + self._rec_batch]
            batch_crops = [crops[i] for i in idx_slice]

            norm_batch = self._rec_preprocess_batch(batch_crops)

            io = self._rec_session.io_binding()
            io.bind_cpu_input(self._rec_in, norm_batch)
            io.bind_output(self._rec_out)
            self._rec_session.run_with_iobinding(io)

            logits: np.ndarray = io.get_outputs()[0].numpy()
            decoded = self._rec.postprocess_op(logits)

            for local_i, global_i in enumerate(idx_slice):
                results[global_i] = decoded[local_i]

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
