"""
wuwa_inventory_kamera.scraping.service.ocr_service
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Background service thread that batches OCR work from multiple scanner
threads into efficient GPU forward passes.

Architecture
------------
Scanners submit :class:`~.captures.CaptureType` objects via
:meth:`OcrService.submit`, which returns a
:class:`~concurrent.futures.Future`.  The service thread drains the queue
into batches, runs :class:`~..ocr.batch.BatchOcr` once per crop kind, then
calls the appropriate assembler and resolves the futures.

The key design constraints are:

* **Single DML thread** — only the service thread ever calls into
  ``onnxruntime``.  This avoids driver-level concurrency issues with
  ``DmlExecutionProvider``.
* **Lookahead decoupling** — scanner threads that move faster than OCR
  (echoes) submit non-blocking and collect futures after the grid sweep.
  Scanners that block on OCR results (weapons, items, characters) call
  ``future.result()`` immediately; the 50 ms drain timeout means they
  wait at most that long.
* **Batch timeout** — the service waits at most ``batch_timeout`` seconds
  for additional captures after draining the first item from the queue.
  This keeps latency bounded.
* **Graceful shutdown** — call :meth:`shutdown` (or use as a context
  manager).

Usage::

    from . import OcrService

    with OcrService() as svc:
        future = svc.submit(EchoCapture(...))
        # ... navigate game, submit more captures ...
        result = future.result()   # EchoResult
"""
from __future__ import annotations

import concurrent.futures
import itertools
import logging
import queue
import threading
import time
from collections import defaultdict
from difflib import get_close_matches
from typing import TYPE_CHECKING, cast

import numpy as np

from ..ocr import tokens_to_lines
from ..ocr._rapidocr import RapidOcrBackend
from ..ocr.batch import BatchOcr
from ..ocr.region_specs import OcrRegionSpec, get_spec
from .ocr_cache import OcrCache
from .echo_ocr_cache import EchoOcrCache
from .captures import (
    _Stop,
    CaptureType,
    AchievementCapture,
    AchievementResult,
    CharCapture,
    CharResult,
    EchoCapture,
    EchoResult,
    ItemCapture,
    ItemResult,
    ShellCapture,
    ShellResult,
    WeaponCapture,
    WeaponResult,
)
from .assemblers.echo_assembler import EchoAssembler
from .assemblers.weapon_assembler import WeaponAssembler
from .assemblers.item_assembler import ItemAssembler
from .assemblers.character_assembler import CharAssembler
from .assemblers.achievement_assembler import AchievementAssembler
from .assemblers.shell_assembler import ShellAssembler

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_ECHO_NAME_FUZZY_CUTOFF = 0.75

# ---------------------------------------------------------------------------
# Spec-driven preprocessing helper
# ---------------------------------------------------------------------------

def _preprocess_with_spec(
    bgr: np.ndarray,
    roi_key: str,
    rarity: int | None = None,
) -> np.ndarray:
    """Apply the OcrRegionSpec preprocessing for *roi_key*.

    Falls back to the raw image (as RGB) if no spec is registered.
    """
    import cv2
    spec = get_spec(roi_key)
    if spec is not None:
        return spec.preprocess(bgr, rarity=rarity)
    # No spec — return image as-is in RGB
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def _echo_name_candidate_from_results(
    results: list[tuple[str, float, np.ndarray]],
) -> str:
    """Normalize OCR tokens into the canonical echo-name lookup form."""
    if not results:
        return ""

    # Convert (text, conf, box) -> (box, text, conf) for tokens_to_lines.
    boxed_tokens = [
        (box.tolist(), text, conf)
        for text, conf, box in results
        if box is not None and text
    ]

    if boxed_tokens:
        lines = tokens_to_lines(boxed_tokens, divisor='', bannedChars=' +')
        name = lines[0] if lines else ''
    else:
        # Fallback for OCR modes that do not provide boxes.
        name = ''.join(text for text, _conf, _box in results if text)

    name = name.lower().strip()
    if name.startswith('phantom:'):
        name = name[len('phantom:'):]
    return name


def _is_plausible_echo_name_results(
    results: list[tuple[str, float, np.ndarray]],
) -> bool:
    """Return whether OCR tokens look like a known echo name."""
    candidate = _echo_name_candidate_from_results(results)
    if not candidate or not any(ch.isalnum() for ch in candidate):
        return False

    # Keep this lazy so OcrService startup stays lightweight.
    from ..data import echoesID

    if not echoesID:
        # If data failed to load, avoid blocking OCR strategies.
        return True

    if candidate in echoesID:
        return True

    return bool(get_close_matches(
        candidate,
        list(echoesID.keys()),
        n=1,
        cutoff=_ECHO_NAME_FUZZY_CUTOFF,
    ))

# ---------------------------------------------------------------------------
# Internal queue item wrapper
# ---------------------------------------------------------------------------

class _QueueItem:
    """Bundles a capture with its future so the service thread can resolve it."""
    __slots__ = ('capture', 'uid', 'future')

    def __init__(
        self,
        capture: CaptureType,
        uid: int,
        future: concurrent.futures.Future,
    ) -> None:
        self.capture = capture
        self.uid     = uid
        self.future  = future


# ---------------------------------------------------------------------------
# OcrService
# ---------------------------------------------------------------------------

class OcrService:
    """
    Background OCR service that batches work from scanner threads.

    Parameters
    ----------
    providers:
        ONNX Runtime execution providers.  Defaults to DML with CPU
        fallback.  Pass ``['CPUExecutionProvider']`` to stay on CPU.
    batch_timeout:
        After draining the first queue item the service collects more for
        at most *batch_timeout* seconds before running a forward pass.
    max_batch_size:
        Hard cap on captures processed per batch iteration.
    min_rarity / min_level:
        Forwarded to :class:`EchoAssembler` and :class:`WeaponAssembler`
        for threshold filtering.
    backend_kwargs:
        Additional keyword arguments forwarded to :class:`RapidOcrBackend`.
    """

    def __init__(
        self,
        providers: list[str] | None = None,
        batch_timeout: float = 0.05,
        max_batch_size: int = 32,
        min_rarity: int = 1,
        min_level: int = 0,
        echo_stat_cache_path: str | None = None,
        ocr_cache_path: str | None = None,
        resolution: str | None = None,
        **backend_kwargs,
    ) -> None:
        if providers is None:
            providers = ['DmlExecutionProvider', 'CPUExecutionProvider']

        self._backend = RapidOcrBackend(onnx_providers=providers, **backend_kwargs)
        self._batch_ocr = BatchOcr(self._backend)

        self._queue:       queue.Queue[_QueueItem | _Stop] = queue.Queue()
        self._counter      = itertools.count()
        self._batch_timeout  = batch_timeout
        self._max_batch_size = max_batch_size

        # Legacy echo stat cache (kept for backward compat)
        self._echo_stat_cache = (
            EchoOcrCache(echo_stat_cache_path)
            if echo_stat_cache_path is not None else None
        )
        # Generalized two-tier OCR cache
        self._ocr_cache = OcrCache(db_path=ocr_cache_path)
        self._resolution = resolution

        # Assemblers
        self._echo_asm        = EchoAssembler(min_rarity=min_rarity, min_level=min_level)
        self._weapon_asm      = WeaponAssembler(min_rarity=min_rarity, min_level=min_level)
        self._item_asm        = ItemAssembler()
        self._char_asm        = CharAssembler()
        self._achievement_asm = AchievementAssembler()
        self._shell_asm        = ShellAssembler()

        self._thread = threading.Thread(
            target=self._run, daemon=True, name='OcrService',
        )
        self._thread.start()
        logger.info('OcrService started (providers=%s)', providers)
        if self._echo_stat_cache is not None:
            logger.info('Echo stat OCR cache enabled: %s', self._echo_stat_cache.path)
        if self._ocr_cache.db_path is not None:
            logger.info('Generalized OCR cache enabled: %s', self._ocr_cache.db_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def submit(self, capture: CaptureType) -> concurrent.futures.Future:
        """
        Enqueue *capture* for OCR processing.

        Returns a :class:`~concurrent.futures.Future` that resolves to the
        matching ``*Result`` object when OCR + assembly is complete.

        Thread-safe.  May be called from any thread.
        """
        fut = concurrent.futures.Future()
        uid = next(self._counter)
        self._queue.put(_QueueItem(capture, uid, fut))
        return fut

    def shutdown(self, wait: bool = True) -> None:
        """
        Request a clean shutdown of the service thread.

        After calling this, do not submit new captures.  If *wait* is
        ``True`` (the default) this call blocks until the service thread
        finishes processing any remaining queue items.
        """
        self._queue.put(_Stop())
        if wait:
            self._thread.join()
        logger.info('OcrService shut down.')

    def __enter__(self) -> 'OcrService':
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        # Don't block joining the OCR thread when an exception (e.g. KeyboardInterrupt)
        # is propagating — just fire the stop signal and let the daemon thread die.
        self.shutdown(wait=exc_type is None)

    # ------------------------------------------------------------------
    # Service thread
    # ------------------------------------------------------------------

    def _run(self) -> None:
        """Main loop of the background service thread."""
        try:
            while True:
                batch = self._drain_batch()
                if batch is None:
                    break
                if batch:
                    self._process_batch(batch)
        finally:
            # Emit cache report
            for line in self._ocr_cache.session_report():
                logger.info(line)
            self._ocr_cache.close()
            if self._echo_stat_cache is not None:
                self._echo_stat_cache.close()

    def _drain_batch(self) -> list[_QueueItem] | None:
        """
        Block until at least one item arrives, then greedily collect more
        within ``batch_timeout`` or until ``max_batch_size`` items are held.

        Returns ``None`` on the :class:`_Stop` sentinel.
        """
        # Block indefinitely for the first item
        first = self._queue.get(block=True)
        if isinstance(first, _Stop):
            return None

        batch = [first]
        deadline = time.monotonic() + self._batch_timeout

        while len(batch) < self._max_batch_size:
            remaining = max(0.0, deadline - time.monotonic())
            try:
                item = self._queue.get(timeout=remaining)
            except queue.Empty:
                break
            if isinstance(item, _Stop):
                self._queue.put(item)   # re-queue so _run sees it next loop
                break
            batch.append(item)

        logger.debug('OcrService — draining %d item(s)', len(batch))
        return batch

    def _process_batch(self, batch: list[_QueueItem]) -> None:
        """Dispatch items in *batch* to the correct per-type processor."""
        by_type: defaultdict[type, list[_QueueItem]] = defaultdict(list)
        for item in batch:
            by_type[type(item.capture)].append(item)

        for cls, group in by_type.items():
            try:
                if cls is EchoCapture:
                    self._process_echoes(group)
                elif cls is WeaponCapture:
                    self._process_weapons(group)
                elif cls is ItemCapture:
                    self._process_items(group)
                elif cls is CharCapture:
                    self._process_chars(group)
                elif cls is AchievementCapture:
                    self._process_achievements(group)
                elif cls is ShellCapture:
                    self._process_shell(group)
                else:
                    logger.warning('OcrService — unknown capture type %s', cls)
                    for item in group:
                        item.future.set_exception(TypeError(f'Unknown capture type {cls}'))
            except Exception as exc:
                logger.exception('OcrService — error processing %s batch', cls.__name__)
                for item in group:
                    if not item.future.done():
                        item.future.set_exception(exc)

    # ------------------------------------------------------------------
    # Per-type processors
    # ------------------------------------------------------------------

    def _process_echoes(self, group: list[_QueueItem]) -> None:
        """
        Run batched OCR for a group of :class:`EchoCapture` objects.

        Three separate ``ocr_images`` calls are made — one per crop type
        (card, stats_name, stats_value) — so that all images passed to a
        single detection forward pass share the same spatial dimensions
        (no wasted padding).

        Sonata detection is handled by the :class:`EchoAssembler` via
        icon template matching on ``capture.sonata_icon``, bypassing OCR.
        """
        captures: list[EchoCapture] = [cast(EchoCapture, it.capture) for it in group]

        # Prefer the dedicated echoName ROI (new UI): preprocess via spec,
        # then try multiple OCR strategies in priority order.
        name_source_filtered = [
            _preprocess_with_spec(c.echo_name, 'echoes.echoName', rarity=c.detected_rarity)
            if c.echo_name is not None else c.card
            for c in captures
        ]
        name_source_raw = [
            c.echo_name if c.echo_name is not None else c.card
            for c in captures
        ]

        _echo_name_spec = get_spec('echoes.echoName')
        _echo_name_single_line = bool(
            _echo_name_spec is not None and _echo_name_spec.single_line
        )

        def _has_usable_text(results: list[tuple[str, float, np.ndarray]]) -> bool:
            return any(text and any(ch.isalnum() for ch in text) for text, _conf, _box in results)

        def _accept_filtered_echo_name(
            capture: EchoCapture,
            results: list[tuple[str, float, np.ndarray]],
            source: str,
        ) -> bool:
            if not _has_usable_text(results):
                return False
            if _is_plausible_echo_name_results(results):
                return True
            logger.debug(
                'Echo %d — %s OCR rejected by echo-name guard: %r',
                capture.echo_index,
                source,
                _echo_name_candidate_from_results(results),
            )
            return False

        def _backend_to_batch(
            tokens,
            *,
            image: np.ndarray | None = None,
        ) -> list[tuple[str, float, np.ndarray]]:
            h = int(image.shape[0]) if image is not None else 1
            w = int(image.shape[1]) if image is not None else 1
            fallback_box = np.asarray(
                [[0, 0], [max(w - 1, 0), 0], [max(w - 1, 0), max(h - 1, 0)], [0, max(h - 1, 0)]],
                dtype=np.float32,
            )

            return [
                (
                    text,
                    float(conf),
                    np.asarray(bbox, dtype=np.float32) if bbox is not None else fallback_box,
                )
                for bbox, text, conf in tokens
            ]

        card_results: list[list[tuple[str, float, np.ndarray]] | None] = [None] * len(captures)
        miss_indices: list[int] = []
        for i, c in enumerate(captures):
            # Source image used for cache keying (raw BGR, same as what the
            # spec's make_signature() expects).
            _cache_src = c.echo_name if c.echo_name is not None else c.card

            # ---- Cache lookup (transient + persistent) ----
            if _echo_name_spec is not None:
                _cached = self._ocr_cache.lookup(
                    _echo_name_spec, _cache_src, rarity=c.detected_rarity
                )
                if _cached is not None:
                    card_results[i] = _cached
                    continue

            miss_indices.append(i)

        # Run batch OCR only for captures that missed cache.
        filtered_results_by_idx: dict[int, list[tuple[str, float, np.ndarray]]] = {}
        raw_results_by_idx: dict[int, list[tuple[str, float, np.ndarray]]] = {}
        if miss_indices:
            miss_filtered = [name_source_filtered[idx] for idx in miss_indices]
            miss_raw = [name_source_raw[idx] for idx in miss_indices]

            miss_filtered_results = self._batch_ocr.ocr_images(miss_filtered)
            miss_raw_results = self._batch_ocr.ocr_images(miss_raw)

            for list_pos, capture_idx in enumerate(miss_indices):
                filtered_results_by_idx[capture_idx] = miss_filtered_results[list_pos]
                raw_results_by_idx[capture_idx] = miss_raw_results[list_pos]

        for i in miss_indices:
            c = captures[i]
            _cache_src = c.echo_name if c.echo_name is not None else c.card
            filtered_result = filtered_results_by_idx[i]
            raw_result = raw_results_by_idx[i]

            # ---- Multi-strategy OCR ----
            # New-UI path: captures with a dedicated echoName ROI use
            # single-image backend.recognize as the PRIMARY method.  This
            # matches the nav-script (imageToString on the filtered crop) and
            # gives excellent quality.  The batch-OCR pipeline (text_det +
            # text_rec) on the binary white-on-black filtered image frequently
            # produces spurious single-character results that pass
            # _has_usable_text and silently block the reliable fallback.
            ocr_result: list[tuple[str, float, np.ndarray]] | None = None

            if c.echo_name is not None:
                if _echo_name_single_line:
                    single_results = _backend_to_batch(
                        self._backend.recognize_single_line(name_source_filtered[i]),
                        image=name_source_filtered[i],
                    )
                else:
                    single_results = _backend_to_batch(
                        self._backend.recognize(name_source_filtered[i]),
                        image=name_source_filtered[i],
                    )

                if _accept_filtered_echo_name(c, single_results, 'single filtered echoName'):
                    ocr_result = single_results
                else:
                    if _echo_name_single_line:
                        thorough_results = _backend_to_batch(
                            self._backend.recognize(name_source_filtered[i]),
                            image=name_source_filtered[i],
                        )
                    else:
                        thorough_results = _backend_to_batch(
                            self._backend.thorough_recognize(name_source_filtered[i]),
                            image=name_source_filtered[i],
                        )

                    if _accept_filtered_echo_name(c, thorough_results, 'thorough filtered echoName'):
                        logger.debug(
                            'Echo %d — echoName recovered via thorough OCR.',
                            c.echo_index,
                        )
                        ocr_result = thorough_results
                    elif _accept_filtered_echo_name(c, filtered_result, 'batch filtered echoName'):
                        logger.debug(
                            'Echo %d — echoName recovered via batch-OCR on filtered crop.',
                            c.echo_index,
                        )
                        ocr_result = filtered_result
                    elif _has_usable_text(raw_result):
                        logger.debug(
                            'Echo %d — echoName recovered via raw echoName crop.',
                            c.echo_index,
                        )
                        ocr_result = raw_result
            elif _has_usable_text(filtered_result):
                # Legacy path (no echoName ROI): use batch OCR on card crop.
                ocr_result = filtered_result

            if ocr_result is None:
                ocr_result = raw_result

            # ---- Cache store ----
            if _echo_name_spec is not None and ocr_result:
                self._ocr_cache.store(
                    _echo_name_spec, _cache_src, ocr_result,
                    rarity=c.detected_rarity,
                )

            card_results[i] = ocr_result

        final_card_results = [result or [] for result in card_results]

        name_results = self._ocr_with_spec(
            'echoes.fullStatsName',
            [c.stats_name for c in captures],
        )
        value_results = self._ocr_with_spec(
            'echoes.fullStatsValue',
            [c.stats_value for c in captures],
        )

        # Convert [[( text, conf, box ), ...], ...] → [[OcrResult, ...], ...]
        # OcrResult = (bbox_list, text, conf); box is (4,2) ndarray so convert.
        def to_tokens(ocr_result_list):
            return [
                [(box.tolist(), text, conf) for text, conf, box in image_results]
                for image_results in ocr_result_list
            ]

        card_tok   = to_tokens(final_card_results)
        name_tok   = to_tokens(name_results)
        value_tok  = to_tokens(value_results)

        for i, item in enumerate(group):
            capture = captures[i]
            try:
                result = self._echo_asm.assemble(
                    capture,
                    card_tok[i],
                    name_tok[i],
                    value_tok[i],
                )
                item.future.set_result(result)
            except Exception as exc:
                logger.exception('OcrService — echo %d assembly error', capture.echo_index)
                item.future.set_exception(exc)

    def _ocr_images_with_cache(
        self,
        crop_kind: str,
        images: list[np.ndarray],
    ) -> list[list[tuple[str, float, np.ndarray]]]:
        """Run OCR for *images*, serving cached echo stat results when present.

        This is the legacy path that uses the old ``EchoOcrCache``.
        New workflows should use :meth:`_ocr_with_spec` instead.
        """
        cache = self._echo_stat_cache
        if cache is None or not images or self._resolution is None:
            return self._batch_ocr.ocr_images(images)

        keys, cached_results, miss_indices = cache.lookup_many(resolution=self._resolution, crop_kind=crop_kind, images=images)
        if miss_indices:
            miss_images = [images[idx] for idx in miss_indices]
            miss_results = self._batch_ocr.ocr_images(miss_images)
            cache.store_many(
                resolution=self._resolution,
                crop_kind=crop_kind,
                images=miss_images,
                results=miss_results,
                keys=[keys[idx] for idx in miss_indices],
            )
            for idx, image_results in zip(miss_indices, miss_results):
                cached_results[idx] = image_results

        hits = len(images) - len(miss_indices)
        logger.debug(
            'Echo OCR cache %s — hits=%d misses=%d total=%d',
            crop_kind,
            hits,
            len(miss_indices),
            len(images),
        )
        return [image_results or [] for image_results in cached_results]

    @staticmethod
    def _filter_allowed_chars(
        results: list[list[tuple[str, float, np.ndarray]]],
        allowed_chars: str | None,
    ) -> list[list[tuple[str, float, np.ndarray]]]:
        """Strip characters outside *allowed_chars* from every token.

        Tokens that become empty after stripping are dropped.  When
        *allowed_chars* is ``None`` the results are returned unchanged.
        """
        if not allowed_chars:
            return results
        charset = frozenset(allowed_chars)
        filtered = []
        for image_tokens in results:
            cleaned_tokens = []
            for text, conf, box in image_tokens:
                cleaned = ''.join(c for c in text if c in charset)
                if cleaned:
                    cleaned_tokens.append((cleaned, conf, box))
            filtered.append(cleaned_tokens)
        return filtered

    def _ocr_with_spec(
        self,
        roi_key: str,
        images_bgr: list[np.ndarray],
        rarity: int | None = None,
    ) -> list[list[tuple[str, float, np.ndarray]]]:
        """Spec-driven OCR with preprocessing, caching, and allowed_chars.

        1. Looks up the :class:`OcrRegionSpec` for *roi_key*.
        2. Checks the two-tier cache for each image.
        3. Preprocesses cache misses via ``spec.preprocess()``.
        4. Runs OCR on the preprocessed images.
        5. Stores results in the appropriate cache tier(s).
        6. Applies ``allowed_chars`` post-filtering on the final results.

        Falls back to raw batch OCR if no spec is registered.
        """
        spec = get_spec(roi_key)
        if spec is None or not images_bgr:
            return self._batch_ocr.ocr_images(images_bgr)

        # Check cache for all images
        keys, cached_results, miss_indices = self._ocr_cache.lookup_many(
            spec, images_bgr, rarity=rarity,
        )

        if miss_indices:
            # Preprocess and OCR the misses
            miss_preprocessed = [
                spec.preprocess(images_bgr[idx], rarity=rarity)
                for idx in miss_indices
            ]
            t0 = time.monotonic()
            miss_results = self._batch_ocr.ocr_images(miss_preprocessed)
            elapsed_sec = time.monotonic() - t0

            # Record latency for session report
            if miss_indices:
                per_call = elapsed_sec / len(miss_indices)
                for _ in miss_indices:
                    self._ocr_cache.record_ocr_latency(roi_key, per_call)

            # Store raw (unfiltered) results in cache
            self._ocr_cache.store_many(
                spec,
                [images_bgr[idx] for idx in miss_indices],
                miss_results,
                rarity=rarity,
                keys=[keys[idx] for idx in miss_indices],
            )
            for idx, result in zip(miss_indices, miss_results):
                cached_results[idx] = result

        raw_results = [r or [] for r in cached_results]
        return self._filter_allowed_chars(raw_results, spec.allowed_chars)

    def _process_weapons(self, group: list[_QueueItem]) -> None:
        """Run batched OCR and assembly for a group of :class:`WeaponCapture` objects."""
        captures = [it.capture for it in group]

        name_results  = self._ocr_with_spec('weapons.name', [c.name for c in captures])
        value_results = self._ocr_with_spec('weapons.value', [c.value for c in captures])

        # Rank is optional — only batch images that are present
        rank_present = [i for i, c in enumerate(captures) if c.rank is not None]
        rank_results_map: dict[int, list] = {}
        if rank_present:
            rank_images = [captures[i].rank for i in rank_present]
            rank_all    = self._batch_ocr.ocr_images(rank_images)
            for list_pos, capture_idx in enumerate(rank_present):
                rank_results_map[capture_idx] = rank_all[list_pos]

        def to_tokens(image_results):
            return [(box.tolist(), text, conf) for text, conf, box in image_results]

        for i, item in enumerate(group):
            rank_raw = rank_results_map.get(i)
            rank_tok = [to_tokens(rank_raw)] if rank_raw is not None else None
            try:
                result = self._weapon_asm.assemble(
                    item.capture,
                    to_tokens(name_results[i]),
                    to_tokens(value_results[i]),
                    rank_tok[0] if rank_tok else None,
                )
                item.future.set_result(result)
            except Exception as exc:
                logger.exception('OcrService — weapon %d assembly error', item.capture.index)
                item.future.set_exception(exc)

    def _process_items(self, group: list[_QueueItem]) -> None:
        """Run batched OCR and assembly for a group of :class:`ItemCapture` objects."""
        captures = [it.capture for it in group]
        info_results = self._ocr_with_spec('items.info', [c.info for c in captures])

        def to_tokens(image_results):
            return [(box.tolist(), text, conf) for text, conf, box in image_results]

        for i, item in enumerate(group):
            try:
                result = self._item_asm.assemble(
                    item.capture,
                    to_tokens(info_results[i]),
                )
                item.future.set_result(result)
            except Exception as exc:
                logger.exception('OcrService — item %d assembly error', item.capture.index)
                item.future.set_exception(exc)

    def _process_chars(self, group: list[_QueueItem]) -> None:
        """
        Run batched OCR and assembly for a group of :class:`CharCapture` objects.

        Each :class:`CharCapture` carries a ``crops`` dict with variable keys.
        We flatten all crop arrays across the group, run one OCR pass per
        crop key name, then distribute results back.
        """
        captures = [it.capture for it in group]

        # Collect the unique crop key names (in insertion order)
        all_keys: list[str] = []
        for c in captures:
            for k in c.crops:
                if k not in all_keys:
                    all_keys.append(k)

        # For each key, gather images from all captures that have it
        # (others get a placeholder empty result)
        key_results: dict[str, list] = {}
        for key in all_keys:
            images = [c.crops[key] for c in captures if key in c.crops]
            if images:
                roi_key = f'characters.{key}'
                key_results[key] = self._ocr_with_spec(roi_key, images)

        def to_tokens(image_results):
            return [(box.tolist(), text, conf) for text, conf, box in image_results]

        for item in group:
            cap = item.capture
            # Build the per-crop token lists for this specific capture
            section_tokens: list[list] = []
            for key in cap.crops:
                if key in key_results:
                    # Find this capture's index in key_results[key]
                    same_key_caps = [c for c in captures if key in c.crops]
                    pos = same_key_caps.index(cap)
                    section_tokens.append(to_tokens(key_results[key][pos]))
                else:
                    section_tokens.append([])

            try:
                result = self._char_asm.assemble(cap, *section_tokens)
                item.future.set_result(result)
            except Exception as exc:
                logger.exception(
                    'OcrService — char %d section %d assembly error',
                    cap.char_index, cap.section,
                )
                item.future.set_exception(exc)

    def _process_achievements(self, group: list[_QueueItem]) -> None:
        """Run batched OCR and assembly for a group of :class:`AchievementCapture` objects."""
        captures = [it.capture for it in group]
        status_results = self._ocr_with_spec(
            'achievements.status', [c.status for c in captures],
        )

        def to_tokens(image_results):
            return [(box.tolist(), text, conf) for text, conf, box in image_results]

        for i, item in enumerate(group):
            try:
                result = self._achievement_asm.assemble(
                    item.capture,
                    to_tokens(status_results[i]),
                )
                item.future.set_result(result)
            except Exception as exc:
                logger.exception(
                    'OcrService — achievement %r assembly error',
                    item.capture.achievement_name,
                )
                item.future.set_exception(exc)

    def _process_shell(self, group: list[_QueueItem]) -> None:
        """Run batched OCR and assembly for a group of :class:`ShellCapture` objects."""
        captures = [it.capture for it in group]
        amount_results = self._ocr_with_spec(
            'shell.amount', [c.amount for c in captures],
        )

        def to_tokens(image_results):
            return [(box.tolist(), text, conf) for text, conf, box in image_results]

        for i, item in enumerate(group):
            try:
                result = self._shell_asm.assemble(
                    item.capture,
                    to_tokens(amount_results[i]),
                )
                item.future.set_result(result)
            except Exception as exc:
                logger.exception('OcrService — shell assembly error')
                item.future.set_exception(exc)
