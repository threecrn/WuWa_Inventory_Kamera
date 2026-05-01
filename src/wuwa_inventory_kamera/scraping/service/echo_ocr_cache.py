"""
wuwa_inventory_kamera.scraping.service.echo_ocr_cache
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Persistent cache for OCR token lists derived from echo stat crops.

The live echo workflow often sees identical stat-name and stat-value
panels across sessions.  Re-running OCR on those crops wastes GPU time,
so this module stores the raw OCR token lists keyed by a stable hash of
the text-only image signal plus a cache-version tag.

The cache is intentionally narrow:

* only echo stat-name and stat-value crops use it;
* it stores OCR output, not assembled echo dicts;
* callers remain free to change assembler logic without invalidating the
  cache unless the OCR token format changes.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path

import numpy as np

ImageOcrResult = list[tuple[str, float, np.ndarray]]


class EchoOcrCache:
    """SQLite-backed persistent cache for echo stat OCR token lists."""

    _CACHE_VERSION = 'echo-stat-v2'
    _TEXT_VALUE_FLOOR = 200
    _TEXT_VALUE_MARGIN = 24
    _TEXT_MAX_CHANNEL_SPREAD = 32
    _FALLBACK_VALUE_FLOOR = 175
    _FALLBACK_VALUE_MARGIN = 48

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        with self._lock:
            self._conn.execute('PRAGMA journal_mode=WAL')
            self._conn.execute('PRAGMA synchronous=NORMAL')
            self._conn.execute(
                '''
                CREATE TABLE IF NOT EXISTS echo_ocr_cache (
                    cache_key   TEXT PRIMARY KEY,
                    crop_kind   TEXT NOT NULL,
                    payload     TEXT NOT NULL
                )
                '''
            )
            self._conn.commit()

    @property
    def path(self) -> Path:
        return self._db_path

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def lookup_many(
        self,
        crop_kind: str,
        images: list[np.ndarray],
    ) -> tuple[list[str], list[ImageOcrResult | None], list[int]]:
        """
        Resolve cached OCR results for *images*.

        Returns ``(keys, results, miss_indices)`` where ``results`` aligns
        with *images* and contains ``None`` for cache misses.
        """
        keys = [self._make_key(crop_kind, image) for image in images]
        cached: list[ImageOcrResult | None] = [None] * len(images)
        misses: list[int] = []

        with self._lock:
            cursor = self._conn.cursor()
            for idx, key in enumerate(keys):
                row = cursor.execute(
                    'SELECT payload FROM echo_ocr_cache WHERE cache_key = ?',
                    (key,),
                ).fetchone()
                if row is None:
                    misses.append(idx)
                    continue
                cached[idx] = self._deserialize(row[0])

        return keys, cached, misses

    def store_many(
        self,
        crop_kind: str,
        images: list[np.ndarray],
        results: list[ImageOcrResult],
        *,
        keys: list[str] | None = None,
    ) -> None:
        """Persist OCR token lists for *images* and *results*."""
        if not images:
            return

        if keys is None:
            keys = [self._make_key(crop_kind, image) for image in images]

        rows = [
            (key, crop_kind, self._serialize(image_results))
            for key, image_results in zip(keys, results)
        ]
        with self._lock:
            self._conn.executemany(
                '''
                INSERT OR REPLACE INTO echo_ocr_cache (cache_key, crop_kind, payload)
                VALUES (?, ?, ?)
                ''',
                rows,
            )
            self._conn.commit()

    @classmethod
    def _make_key(cls, crop_kind: str, image: np.ndarray) -> str:
        contiguous = np.ascontiguousarray(image)
        normalized = cls._normalize_for_hash(contiguous)
        digest = hashlib.blake2b(digest_size=20)
        digest.update(cls._CACHE_VERSION.encode('ascii'))
        digest.update(b'|')
        digest.update(crop_kind.encode('ascii'))
        digest.update(b'|')
        digest.update(str(contiguous.shape).encode('ascii'))
        digest.update(b'|')
        digest.update(contiguous.dtype.str.encode('ascii'))
        digest.update(b'|')
        digest.update(str(normalized.shape).encode('ascii'))
        digest.update(b'|')
        digest.update(normalized.dtype.str.encode('ascii'))
        digest.update(b'|')
        digest.update(normalized.tobytes())
        return digest.hexdigest()

    @classmethod
    def _normalize_for_hash(cls, image: np.ndarray) -> np.ndarray:
        if image.ndim == 2:
            return cls._normalize_plane(
                image,
                floor=cls._FALLBACK_VALUE_FLOOR,
                margin=cls._FALLBACK_VALUE_MARGIN,
            )

        if image.ndim == 3 and image.shape[2] >= 3:
            rgb = image[..., :3].astype(np.int16, copy=False)
            darkest_channel = rgb.min(axis=2)
            channel_spread = rgb.max(axis=2) - darkest_channel
            threshold = cls._threshold_value(
                darkest_channel,
                floor=cls._TEXT_VALUE_FLOOR,
                margin=cls._TEXT_VALUE_MARGIN,
            )
            mask = (darkest_channel >= threshold) & (
                channel_spread <= cls._TEXT_MAX_CHANNEL_SPREAD
            )
            if np.any(mask):
                return np.ascontiguousarray(np.where(mask, np.uint8(255), np.uint8(0)))

            gray = ((77 * rgb[..., 0]) + (150 * rgb[..., 1]) + (29 * rgb[..., 2])) >> 8
            return cls._normalize_plane(
                gray,
                floor=cls._FALLBACK_VALUE_FLOOR,
                margin=cls._FALLBACK_VALUE_MARGIN,
            )

        return image

    @classmethod
    def _normalize_plane(
        cls,
        plane: np.ndarray,
        *,
        floor: int,
        margin: int,
    ) -> np.ndarray:
        threshold = cls._threshold_value(plane, floor=floor, margin=margin)
        binary = np.ascontiguousarray(np.where(plane >= threshold, np.uint8(255), np.uint8(0)))
        if np.any(binary):
            return binary
        return np.ascontiguousarray(plane.astype(np.uint8, copy=False))

    @staticmethod
    def _threshold_value(plane: np.ndarray, *, floor: int, margin: int) -> int:
        return max(floor, int(np.max(plane)) - margin)

    @staticmethod
    def _serialize(image_results: ImageOcrResult) -> str:
        payload = [
            {
                'text': text,
                'confidence': float(confidence),
                'box': box.tolist(),
            }
            for text, confidence, box in image_results
        ]
        return json.dumps(payload, separators=(',', ':'))

    @staticmethod
    def _deserialize(payload: str) -> ImageOcrResult:
        data = json.loads(payload)
        return [
            (
                item['text'],
                float(item['confidence']),
                np.asarray(item['box'], dtype=np.float32),
            )
            for item in data
        ]