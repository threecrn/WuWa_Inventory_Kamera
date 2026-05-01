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
from typing import TYPE_CHECKING, cast

import numpy as np

from ..ocr._rapidocr import RapidOcrBackend
from ..ocr.batch import BatchOcr
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

# ---------------------------------------------------------------------------
# Echo name colour-filter preprocessing
# ---------------------------------------------------------------------------

# The echo name in the new UI is rendered in a distinctive turquoise colour
# (HSV H≈94, S≈84, V≈247) on a busy, non-monotone portrait background.
# Masking by hue before OCR isolates the text and discards the background.
_ECHO_NAME_HSV_LO = np.array([85,  60, 170], dtype=np.uint8)
_ECHO_NAME_HSV_HI = np.array([105, 255, 255], dtype=np.uint8)


def _filter_echo_name(bgr: np.ndarray) -> np.ndarray:
    """Return a white-on-black RGB image containing only the turquoise name text."""
    import cv2
    hsv  = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, _ECHO_NAME_HSV_LO, _ECHO_NAME_HSV_HI)
    mono = np.where(mask > 0, np.uint8(255), np.uint8(0))
    return cv2.cvtColor(mono, cv2.COLOR_GRAY2RGB)


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
        while True:
            batch = self._drain_batch()
            if batch is None:
                break
            if batch:
                self._process_batch(batch)

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

        # Prefer the dedicated echoName ROI (new UI): try colour-filtered
        # first, then raw echoName if detection returns no boxes.
        # Legacy resolutions without echoName keep using card OCR.
        name_source_filtered = [
            _filter_echo_name(c.echo_name) if c.echo_name is not None else c.card
            for c in captures
        ]
        name_source_raw = [
            c.echo_name if c.echo_name is not None else c.card
            for c in captures
        ]

        filtered_results = self._batch_ocr.ocr_images(name_source_filtered)
        raw_results = self._batch_ocr.ocr_images(name_source_raw)

        def _has_usable_text(results: list[tuple[str, float, np.ndarray]]) -> bool:
            return any(text and any(ch.isalnum() for ch in text) for text, _conf, _box in results)

        def _backend_to_batch(tokens) -> list[tuple[str, float, np.ndarray]]:
            return [
                (text, conf, np.asarray(bbox, dtype=np.float32))
                for bbox, text, conf in tokens
                if bbox is not None
            ]

        card_results: list[list[tuple[str, float, np.ndarray]]] = []
        for i, c in enumerate(captures):
            if _has_usable_text(filtered_results[i]):
                card_results.append(filtered_results[i])
                continue

            # Fallback chain for new-UI echo names:
            #   1) single-image recognize on filtered crop (matches nav script)
            #   2) thorough recognize on filtered crop
            #   3) batched OCR on raw echoName crop
            if c.echo_name is not None:
                single_results = _backend_to_batch(
                    self._backend.recognize(name_source_filtered[i])
                )
                if _has_usable_text(single_results):
                    logger.debug(
                        'Echo %d — echoName recovered via single-image OCR fallback.',
                        c.echo_index,
                    )
                    card_results.append(single_results)
                    continue

                thorough_results = _backend_to_batch(
                    self._backend.thorough_recognize(name_source_filtered[i])
                )
                if _has_usable_text(thorough_results):
                    logger.debug(
                        'Echo %d — echoName recovered via thorough OCR fallback.',
                        c.echo_index,
                    )
                    card_results.append(thorough_results)
                    continue

                if _has_usable_text(raw_results[i]):
                    logger.debug(
                        'Echo %d — echoName filtered OCR empty; '
                        'falling back to raw echoName crop.',
                        c.echo_index,
                    )
                    card_results.append(raw_results[i])
                    continue

            card_results.append(raw_results[i])

        name_results   = self._batch_ocr.ocr_images([c.stats_name   for c in captures])
        value_results  = self._batch_ocr.ocr_images([c.stats_value  for c in captures])

        # Convert [[( text, conf, box ), ...], ...] → [[OcrResult, ...], ...]
        # OcrResult = (bbox_list, text, conf); box is (4,2) ndarray so convert.
        def to_tokens(ocr_result_list):
            return [
                [(box.tolist(), text, conf) for text, conf, box in image_results]
                for image_results in ocr_result_list
            ]

        card_tok   = to_tokens(card_results)
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

    def _process_weapons(self, group: list[_QueueItem]) -> None:
        """Run batched OCR and assembly for a group of :class:`WeaponCapture` objects."""
        captures = [it.capture for it in group]

        name_results  = self._batch_ocr.ocr_images([c.name  for c in captures])
        value_results = self._batch_ocr.ocr_images([c.value for c in captures])

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
        info_results = self._batch_ocr.ocr_images([c.info for c in captures])

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
                key_results[key] = self._batch_ocr.ocr_images(images)

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
        status_results = self._batch_ocr.ocr_images([c.status for c in captures])

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
        amount_results = self._batch_ocr.ocr_images([c.amount for c in captures])

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
