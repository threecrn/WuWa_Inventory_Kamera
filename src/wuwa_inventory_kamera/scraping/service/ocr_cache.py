"""
wuwa_inventory_kamera.scraping.service.ocr_cache
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Generalized two-tier OCR cache supporting both transient (in-memory,
session-scoped) and persistent (SQLite-backed, cross-session) caching.

The cache is driven by :class:`~..ocr.region_specs.OcrRegionSpec`
descriptors: each spec declares its ``cache_mode`` and signature
parameters, and this module handles the lookup/store/eviction logic
for both tiers.

Lookup order
------------
1. Transient cache (O(1) dict hit — tried for both ``transient`` and
   ``persistent`` tiers).
2. Persistent SQLite (only for ``persistent`` specs).
3. On miss, the caller runs OCR and populates the appropriate tier(s).

A ``persistent``-tier region also populates the transient cache on hit,
avoiding repeated SQLite round-trips within the same session.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

import numpy as np

from ..ocr.region_specs import OcrRegionSpec

logger = logging.getLogger(__name__)

ImageOcrResult = list[tuple[str, float, np.ndarray]]


class OcrCache:
    """Two-tier OCR cache (transient + persistent).

    Parameters
    ----------
    db_path:
        Path to the SQLite database for persistent caching.
        Pass ``None`` to disable the persistent tier entirely.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        # ---- Transient tier ----
        self._transient: dict[str, dict[str, ImageOcrResult]] = {}
        self._transient_lock = threading.Lock()

        # ---- Persistent tier ----
        self._db_path = Path(db_path) if db_path is not None else None
        self._conn: sqlite3.Connection | None = None
        self._db_lock = threading.Lock()

        # ---- Counters for session report ----
        self._hits_transient: dict[str, int] = {}
        self._hits_persistent: dict[str, int] = {}
        self._misses: dict[str, int] = {}
        # Per-roi_key OCR latency samples (seconds per call) — populated by
        # record_ocr_latency() to support time-saved estimates in session_report.
        self._ocr_latency_samples: dict[str, list[float]] = {}

        if self._db_path is not None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            with self._db_lock:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA synchronous=NORMAL")
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS ocr_cache (
                        roi_key     TEXT NOT NULL,
                        cache_key   TEXT NOT NULL,
                        spec_version TEXT NOT NULL DEFAULT '',
                        payload     TEXT NOT NULL,
                        hit_ts      INTEGER NOT NULL DEFAULT 0,
                        hit_count   INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY (roi_key, cache_key)
                    )
                    """
                )
                self._conn.commit()

    @property
    def db_path(self) -> Path | None:
        return self._db_path

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def lookup(
        self,
        spec: OcrRegionSpec,
        image: np.ndarray,
        rarity: int | None = None,
    ) -> ImageOcrResult | None:
        """Look up a cached OCR result for *image* under *spec*.

        Returns ``None`` on cache miss.
        """
        if spec.cache_mode == "none":
            return None

        key = spec.make_signature(image, rarity)

        # 1. Transient tier
        with self._transient_lock:
            bucket = self._transient.get(spec.roi_key)
            if bucket is not None and key in bucket:
                self._count("transient", spec.roi_key)
                return bucket[key]

        # 2. Persistent tier
        if spec.cache_mode == "persistent" and self._conn is not None:
            row = self._sqlite_lookup(spec.roi_key, key)
            if row is not None:
                result = self._deserialize(row)
                # Promote to transient for same-session re-hits
                self._transient_store(spec.roi_key, key, result)
                self._count("persistent", spec.roi_key)
                return result

        self._count("miss", spec.roi_key)
        return None

    def lookup_many(
        self,
        spec: OcrRegionSpec,
        images: list[np.ndarray],
        rarity: int | None = None,
    ) -> tuple[list[str], list[ImageOcrResult | None], list[int]]:
        """Batch lookup — returns (keys, results, miss_indices)."""
        keys = [spec.make_signature(img, rarity) for img in images]
        results: list[ImageOcrResult | None] = [None] * len(images)
        misses: list[int] = []

        for idx, key in enumerate(keys):
            cached = self._try_lookup(spec, key)
            if cached is not None:
                results[idx] = cached
            else:
                misses.append(idx)
                self._count("miss", spec.roi_key)

        return keys, results, misses

    # ------------------------------------------------------------------
    # Store
    # ------------------------------------------------------------------

    def store(
        self,
        spec: OcrRegionSpec,
        image: np.ndarray,
        result: ImageOcrResult,
        rarity: int | None = None,
        *,
        key: str | None = None,
    ) -> None:
        """Store an OCR result under the appropriate tier(s)."""
        if spec.cache_mode == "none":
            return

        if key is None:
            key = spec.make_signature(image, rarity)

        self._transient_store(spec.roi_key, key, result)

        if spec.cache_mode == "persistent" and self._conn is not None:
            self._sqlite_store(spec.roi_key, key, spec.spec_version, result)

    def store_many(
        self,
        spec: OcrRegionSpec,
        images: list[np.ndarray],
        results: list[ImageOcrResult],
        rarity: int | None = None,
        *,
        keys: list[str] | None = None,
    ) -> None:
        """Batch store — stores each result under the appropriate tier(s)."""
        if spec.cache_mode == "none" or not images:
            return

        if keys is None:
            keys = [spec.make_signature(img, rarity) for img in images]

        for key, result in zip(keys, results):
            self._transient_store(spec.roi_key, key, result)

        if spec.cache_mode == "persistent" and self._conn is not None:
            self._sqlite_store_many(
                spec.roi_key,
                keys,
                spec.spec_version,
                results,
            )

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def clear_transient(self) -> None:
        """Discard all transient cache entries (call between sessions)."""
        with self._transient_lock:
            self._transient.clear()

    def close(self) -> None:
        """Close the persistent database connection."""
        if self._conn is not None:
            with self._db_lock:
                self._conn.close()
            self._conn = None

    def record_ocr_latency(self, roi_key: str, elapsed_sec: float) -> None:
        """Record a single OCR-call latency sample for *roi_key*.

        Called by :class:`OcrService` after each batch of OCR misses so that
        :meth:`session_report` can estimate time saved by cache hits.
        """
        samples = self._ocr_latency_samples.setdefault(roi_key, [])
        samples.append(elapsed_sec)

    def session_report(self) -> list[str]:
        """Return per-roi_key cache report lines.

        Each line shows transient hits, persistent hits, miss count,
        hit-rate percentage, estimated time saved, and the cache tier.
        """
        from ..ocr.region_specs import get_spec

        all_keys = sorted(
            set(self._hits_transient) | set(self._hits_persistent) | set(self._misses)
        )
        lines: list[str] = []
        for key in all_keys:
            t_hits = self._hits_transient.get(key, 0)
            p_hits = self._hits_persistent.get(key, 0)
            m = self._misses.get(key, 0)
            total = t_hits + p_hits + m
            hit_rate = (t_hits + p_hits) / total * 100 if total else 0.0

            # Estimate time saved from recorded latency samples
            samples = self._ocr_latency_samples.get(key, [])
            if samples and (t_hits + p_hits) > 0:
                avg_sec = sum(samples) / len(samples)
                saved_sec = avg_sec * (t_hits + p_hits)
                time_str = f" — saved ~{saved_sec:.1f}s"
            else:
                time_str = ""

            # Cache tier label from the spec registry
            spec = get_spec(key)
            tier_label = f"  [{spec.cache_mode}]" if spec is not None else ""

            lines.append(
                f"[CacheReport] {key}: "
                f"{t_hits} transient-hits / {p_hits} persistent-hits / "
                f"{m} misses  ({hit_rate:.0f}% hit){time_str}{tier_label}"
            )
        return lines

    def reset_counters(self) -> None:
        """Reset session hit/miss counters and latency samples."""
        self._hits_transient.clear()
        self._hits_persistent.clear()
        self._misses.clear()
        self._ocr_latency_samples.clear()

    def cleanup_older_than(self, days: int) -> int:
        """Delete persistent entries older than *days* days."""
        if self._conn is None:
            return 0
        cutoff = int(time.time()) - days * 86400
        with self._db_lock:
            cursor = self._conn.execute(
                "DELETE FROM ocr_cache WHERE hit_ts < ?", (cutoff,)
            )
            deleted = cursor.rowcount
            self._conn.commit()
        return deleted

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _try_lookup(
        self,
        spec: OcrRegionSpec,
        key: str,
    ) -> ImageOcrResult | None:
        # Transient
        with self._transient_lock:
            bucket = self._transient.get(spec.roi_key)
            if bucket is not None and key in bucket:
                self._count("transient", spec.roi_key)
                return bucket[key]

        # Persistent
        if spec.cache_mode == "persistent" and self._conn is not None:
            row = self._sqlite_lookup(spec.roi_key, key)
            if row is not None:
                result = self._deserialize(row)
                self._transient_store(spec.roi_key, key, result)
                self._count("persistent", spec.roi_key)
                return result

        return None

    def _transient_store(
        self, roi_key: str, key: str, result: ImageOcrResult
    ) -> None:
        with self._transient_lock:
            bucket = self._transient.setdefault(roi_key, {})
            bucket[key] = result

    def _sqlite_lookup(self, roi_key: str, key: str) -> str | None:
        with self._db_lock:
            row = self._conn.execute(  # type: ignore[union-attr]
                "SELECT payload FROM ocr_cache WHERE roi_key = ? AND cache_key = ?",
                (roi_key, key),
            ).fetchone()
            if row is not None:
                now = int(time.time())
                self._conn.execute(  # type: ignore[union-attr]
                    """
                    UPDATE ocr_cache
                    SET hit_ts = ?, hit_count = hit_count + 1
                    WHERE roi_key = ? AND cache_key = ?
                    """,
                    (now, roi_key, key),
                )
                self._conn.commit()  # type: ignore[union-attr]
                return row[0]
        return None

    def _sqlite_store(
        self,
        roi_key: str,
        key: str,
        spec_version: str,
        result: ImageOcrResult,
    ) -> None:
        now = int(time.time())
        payload = self._serialize(result)
        with self._db_lock:
            self._conn.execute(  # type: ignore[union-attr]
                """
                INSERT OR REPLACE INTO ocr_cache
                    (roi_key, cache_key, spec_version, payload, hit_ts, hit_count)
                VALUES (?, ?, ?, ?, ?, 0)
                """,
                (roi_key, key, spec_version, payload, now),
            )
            self._conn.commit()  # type: ignore[union-attr]

    def _sqlite_store_many(
        self,
        roi_key: str,
        keys: list[str],
        spec_version: str,
        results: list[ImageOcrResult],
    ) -> None:
        now = int(time.time())
        rows = [
            (roi_key, key, spec_version, self._serialize(res), now)
            for key, res in zip(keys, results)
        ]
        with self._db_lock:
            self._conn.executemany(  # type: ignore[union-attr]
                """
                INSERT OR REPLACE INTO ocr_cache
                    (roi_key, cache_key, spec_version, payload, hit_ts, hit_count)
                VALUES (?, ?, ?, ?, ?, 0)
                """,
                rows,
            )
            self._conn.commit()  # type: ignore[union-attr]

    def _count(self, tier: str, roi_key: str) -> None:
        if tier == "transient":
            self._hits_transient[roi_key] = self._hits_transient.get(roi_key, 0) + 1
        elif tier == "persistent":
            self._hits_persistent[roi_key] = self._hits_persistent.get(roi_key, 0) + 1
        elif tier == "miss":
            self._misses[roi_key] = self._misses.get(roi_key, 0) + 1

    @staticmethod
    def _serialize(result: ImageOcrResult) -> str:
        payload = [
            {
                "text": text,
                "confidence": float(confidence),
                "box": box.tolist(),
            }
            for text, confidence, box in result
        ]
        return json.dumps(payload, separators=(",", ":"))

    @staticmethod
    def _deserialize(payload: str) -> ImageOcrResult:
        data = json.loads(payload)
        return [
            (
                item["text"],
                float(item["confidence"]),
                np.asarray(item["box"], dtype=np.float32),
            )
            for item in data
        ]
