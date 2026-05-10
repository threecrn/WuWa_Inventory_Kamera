"""
wuwa_inventory_kamera.scraping.ocr._rapidocr
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Adapter that wraps ``rapidocr_onnxruntime.RapidOCR`` to satisfy the
:class:`~wuwa_inventory_kamera.scraping.ocr.OcrBackend` protocol.
"""
from __future__ import annotations

import contextlib
import logging

import numpy as np

from ._types import OcrResult

logger = logging.getLogger(__name__)

_DEDUP_Y_THRESHOLD = 15
_RUNTIME_DEBUG_PATCHED = True


def _y_center(bbox) -> float:
    return sum(pt[1] for pt in bbox) / len(bbox)


def _merge_unique(base: list[OcrResult], candidates: list[OcrResult]) -> list[OcrResult]:
    existing_ys = [_y_center(bbox) for bbox, _, _ in base]
    added = list(base)
    for bbox, text, conf in candidates:
        yc = _y_center(bbox)
        if all(abs(yc - ey) > _DEDUP_Y_THRESHOLD for ey in existing_ys):
            added.append((bbox, text, conf))
            existing_ys.append(yc)
    return added


class RapidOcrBackend:
    """
    Wraps ``rapidocr_onnxruntime.RapidOCR`` as an
    :class:`~wuwa_inventory_kamera.scraping.ocr.OcrBackend`.

    Parameters
    ----------
    pad_px:
        Padding (pixels) used in :meth:`thorough_recognize`.
    fallback_text_score:
        ``text_score`` used by the low-confidence fallback pass inside
        :meth:`thorough_recognize`.  ``None`` disables the fallback pass.
    onnx_providers:
        List of ONNX Runtime execution provider names, e.g.
        ``['DmlExecutionProvider', 'CPUExecutionProvider']``.
        When ``None``, the library's default provider selection is used.
    **kwargs:
        Forwarded verbatim to ``RapidOCR(**kwargs)``.
    """

    @staticmethod
    def _model_kind(model_name: str) -> str:
        lower_name = model_name.lower()
        if '_det' in lower_name:
            return 'det'
        if '_rec' in lower_name:
            return 'rec'
        if '_cls' in lower_name:
            return 'cls'
        return 'model'

    @staticmethod
    @contextlib.contextmanager
    def _provider_patch(providers: list):
        """
        Temporarily monkey-patch ``OrtInferSession.__init__`` so that every
        ONNX session created inside the context uses *providers*.
        """
        from rapidocr_onnxruntime import utils as _rutils
        import onnxruntime as ort
        import random

        _orig_init = _rutils.OrtInferSession.__init__

        def _patched_init(self, config):
            model_path = config['model_path']
            model_name = model_path.split("\\")[-1]
            sess_opt = ort.SessionOptions()
            sess_opt.log_severity_level = 4
            sess_opt.enable_cpu_mem_arena = False # Avoid fragmentation issues with large models
            sess_opt.enable_mem_pattern = False   # Avoid fragmentation issues with large models
            sess_opt.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL # Avoid concurrency issues with some providers
            #sess_opt.add_session_config_entry('arena_extend_strategy', '1') # Allow arena to grow beyond initial size for large models
            #sess_opt.add_session_config_entry('initial_chunk_size_bytes', str(256*1024*1024)) # Start with a larger initial chunk to reduce fragmentation for large models
            #sess_opt.add_session_config_entry("session.use_device_allocator_for_initializers", "1") # Use device memory for initializers to reduce fragmentation and improve performance on some providers
            sess_opt.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            #sess_opt.enable_profiling = True
            r: str = random.randbytes(8).hex()
            provider_type: str = 'dml' if 'DmlExecutionProvider' in providers else 'cpu'
            #print(f"model_path: {config['model_path']}")
            sess_opt.profile_file_prefix = f"rapidocr_onnxr_{r}_{provider_type}_{model_name}_"
            _rutils.OrtInferSession._verify_model(model_path)
            self.session = ort.InferenceSession(
                model_path,
                sess_options=sess_opt,
                providers=providers,
            )
            self._wuwa_model_name = model_name
            self._wuwa_model_kind = RapidOcrBackend._model_kind(model_name)
            self._wuwa_requested_providers = tuple(providers)
            logger.debug(
                'RapidOCR session providers (patched): %s',
                self.session.get_providers(),
            )

        _rutils.OrtInferSession.__init__ = _patched_init
        try:
            yield
        finally:
            _rutils.OrtInferSession.__init__ = _orig_init

    @staticmethod
    def _ensure_runtime_debug_patches() -> None:
        global _RUNTIME_DEBUG_PATCHED

        from rapidocr_onnxruntime import utils as _rutils

        if _RUNTIME_DEBUG_PATCHED:
            return

        _orig_call = _rutils.OrtInferSession.__call__

        def _patched_call(self, input_content):
            if logger.isEnabledFor(logging.DEBUG):
                shape = tuple(getattr(input_content, 'shape', ()))
                model_name = getattr(self, '_wuwa_model_name', '<unknown>')
                model_kind = getattr(self, '_wuwa_model_kind', 'model')
                providers = tuple(self.session.get_providers())

                if model_kind == 'rec' and len(shape) == 4:
                    logger.debug(
                        'RapidOCR rec batch: model=%s providers=%s batch_size=%d batch_width=%d input_shape=%s',
                        model_name,
                        providers,
                        shape[0],
                        shape[3],
                        shape,
                    )
                else:
                    logger.debug(
                        'RapidOCR %s input: model=%s providers=%s input_shape=%s',
                        model_kind,
                        model_name,
                        providers,
                        shape,
                    )

            return _orig_call(self, input_content)

        _rutils.OrtInferSession.__call__ = _patched_call
        _RUNTIME_DEBUG_PATCHED = True

    def __init__(
        self,
        pad_px: int = 10,
        fallback_text_score: float | None = 0.3,
        onnx_providers: list | None = None,
        **kwargs,
    ):
        from rapidocr_onnxruntime import RapidOCR
        self._ensure_runtime_debug_patches()
        ctx = self._provider_patch(onnx_providers) if onnx_providers else contextlib.nullcontext()
        with ctx:
            self._ocr = RapidOCR(**kwargs)
        self._pad_px = pad_px
        self._kwargs = dict(kwargs)

        if fallback_text_score is not None:
            fallback_kwargs = dict(kwargs)
            fallback_kwargs['text_score'] = fallback_text_score
            with (self._provider_patch(onnx_providers) if onnx_providers else contextlib.nullcontext()):
                self._fallback_ocr = RapidOCR(**fallback_kwargs)
            self._fallback_text_score: float | None = fallback_text_score
        else:
            self._fallback_ocr = None
            self._fallback_text_score = None

    def _run_once(self, image: np.ndarray, ocr=None, **call_kwargs) -> list[OcrResult]:
        if ocr is None:
            ocr = self._ocr
        raw, _elapsed = ocr(image, **call_kwargs)
        if not raw:
            return []
        results = []
        for item in raw:
            if len(item) == 2:
                # use_det=False: RapidOCR returns (text, conf) with no bbox
                text, conf = item
                results.append((None, text, float(conf)))
            else:
                bbox, text, conf = item
                results.append((bbox, text, float(conf)))
        return results

    def _padded_results(self, image: np.ndarray) -> list[OcrResult]:
        p = self._pad_px
        pad_width = ((p, p), (p, p)) if image.ndim == 2 else ((p, p), (p, p), (0, 0))
        padded = np.pad(image, pad_width, mode='edge')
        raw = self._run_once(padded)
        return [
            ([[pt[0] - p, pt[1] - p] for pt in bbox], text, conf)
            for bbox, text, conf in raw
        ]

    def recognize(self, image: np.ndarray) -> list[OcrResult]:
        """Fast single-pass OCR, sorted by Y-centre."""
        results = self._run_once(image)
        results.sort(key=lambda r: _y_center(r[0]))
        return results

    def recognize_single_line(self, image: np.ndarray) -> list[OcrResult]:
        """OCR without text detection; treats the whole image as one text region."""
        return self._run_once(image, use_det=False)

    def thorough_recognize(self, image: np.ndarray) -> list[OcrResult]:
        """
        Multi-pass OCR for maximum recall.

        Three passes (primary, padded, fallback-score) merged by Y-centre
        deduplication.
        """
        primary = self._run_once(image)
        merged = list(primary)

        padded = self._padded_results(image)
        merged = _merge_unique(merged, padded)

        if self._fallback_ocr is not None:
            fallback = self._run_once(image, ocr=self._fallback_ocr)
            merged = _merge_unique(merged, fallback)

        merged.sort(key=lambda r: _y_center(r[0]))
        return merged
