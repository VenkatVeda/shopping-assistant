"""
Semantic Cache for the RAG search pipeline.

Wraps embedding generation + vector_client.search() so that semantically
near-identical queries return cached results without hitting the vector index.

How it works
------------
1. On each search, compute a query embedding (already needed for search).
2. Compute cosine similarity between this embedding and every cached entry
   for the same filter fingerprint (so "red bags" and "blue bags" never share
   a cache slot despite high textual similarity).
3. If similarity >= HIT_THRESHOLD (default 0.95), return the cached result set.
4. Otherwise, run the real search, store the result with a TTL, and return it.

Backing stores
--------------
Two backends are provided:

  InMemorySemanticCache   — process-local, lost on restart. Good for dev/testing
                            and as a warm-up layer before Delta is available.

  DeltaSemanticCache      — persists to a Databricks Delta table. Survives
                            restarts. Recommended for production.

Both implement the same interface so they can be swapped via an env var without
changing any call site.

Usage (in workflow.py)
----------------------
    from core.semantic_cache import build_cache

    # called once in ShoppingAssistantWorkflow.__init__
    self.semantic_cache = build_cache()

    # called inside product_search_node instead of the bare search block
    results = self.semantic_cache.search(
        query_text    = search_query,
        query_vector  = query_embedding,
        filters       = filters,
        top_k         = 50,
        search_fn     = lambda v, k, f: self.vector_client.search(vector=v, top_k=k, filters=f),
    )
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------

HIT_THRESHOLD    = float(os.getenv("SEMANTIC_CACHE_THRESHOLD",  "0.95"))
TTL_SECONDS      = int(os.getenv("SEMANTIC_CACHE_TTL_SECONDS",  "3600"))   # 1 hour default
MAX_ENTRIES      = int(os.getenv("SEMANTIC_CACHE_MAX_ENTRIES",  "500"))    # in-memory cap


# ---------------------------------------------------------------------------
# Cosine similarity helper (no external deps)
# ---------------------------------------------------------------------------

def _cosine_similarity(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def _filter_fingerprint(filters: Optional[Dict]) -> str:
    """Stable string key for a filter dict — used to namespace cache slots."""
    if not filters:
        return "__no_filter__"
    return hashlib.md5(
        json.dumps(filters, sort_keys=True, default=str).encode()
    ).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class SemanticCacheBase(ABC):

    def search(
        self,
        query_text:   str,
        query_vector: List[float],
        filters:      Optional[Dict],
        top_k:        int,
        search_fn:    Callable[[List[float], int, Optional[Dict]], List[Dict]],
    ) -> Tuple[List[Dict], bool]:
        """
        Execute a cached vector search.

        Args:
            query_text:   Human-readable query (used only for logging).
            query_vector: Pre-computed embedding for the query.
            filters:      Metadata filter dict passed to the vector index.
            top_k:        Number of results to retrieve.
            search_fn:    Callable(vector, top_k, filters) → list[dict].
                          Called only on a cache miss.

        Returns:
            (results, cache_hit) — results is the product list,
            cache_hit is True if the result came from cache.
        """
        fingerprint = _filter_fingerprint(filters)
        cached = self._lookup(query_vector, fingerprint)

        if cached is not None:
            logger.info(
                f"[SEMANTIC CACHE] HIT — '{query_text[:60]}' "
                f"(filter={fingerprint})"
            )
            return cached, True

        logger.info(
            f"[SEMANTIC CACHE] MISS — '{query_text[:60]}' "
            f"(filter={fingerprint}) — calling vector index"
        )
        results = search_fn(query_vector, top_k, filters)
        self._store(query_vector, fingerprint, results)
        return results, False

    @abstractmethod
    def _lookup(
        self,
        query_vector: List[float],
        fingerprint:  str,
    ) -> Optional[List[Dict]]:
        """Return cached results if a near-identical query exists, else None."""

    @abstractmethod
    def _store(
        self,
        query_vector: List[float],
        fingerprint:  str,
        results:      List[Dict],
    ) -> None:
        """Persist a new cache entry."""

    def invalidate(self, fingerprint: Optional[str] = None) -> int:
        """
        Invalidate cache entries.
        If fingerprint is None, flushes the entire cache.
        Returns the number of entries removed.
        """
        return 0

    def stats(self) -> Dict[str, Any]:
        """Return a snapshot of cache stats for the /api/metrics endpoint."""
        return {}


# ---------------------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------------------

class InMemorySemanticCache(SemanticCacheBase):
    """
    Process-local LRU-style semantic cache.
    Entries expire after TTL_SECONDS and the store is capped at MAX_ENTRIES.
    Lost on process restart — use DeltaSemanticCache for durability.
    """

    def __init__(
        self,
        hit_threshold: float = HIT_THRESHOLD,
        ttl_seconds:   int   = TTL_SECONDS,
        max_entries:   int   = MAX_ENTRIES,
    ):
        self._threshold   = hit_threshold
        self._ttl         = ttl_seconds
        self._max_entries = max_entries
        # fingerprint → list of (vector, results, expires_at)
        self._store_dict: Dict[str, List[Tuple[List[float], List[Dict], float]]] = {}
        self._hits   = 0
        self._misses = 0

    def _lookup(self, query_vector, fingerprint):
        now     = time.time()
        entries = self._store_dict.get(fingerprint, [])

        # Prune expired entries in-place
        live = [(v, r, exp) for v, r, exp in entries if exp > now]
        if len(live) != len(entries):
            self._store_dict[fingerprint] = live

        for cached_vec, cached_results, _ in live:
            sim = _cosine_similarity(query_vector, cached_vec)
            if sim >= self._threshold:
                self._hits += 1
                logger.debug(f"[SEMANTIC CACHE] cosine={sim:.4f} >= {self._threshold}")
                return cached_results

        self._misses += 1
        return None

    def _store(self, query_vector, fingerprint, results):
        expires = time.time() + self._ttl
        if fingerprint not in self._store_dict:
            self._store_dict[fingerprint] = []
        self._store_dict[fingerprint].append((query_vector, results, expires))

        # Enforce global cap: evict oldest entries across all fingerprints
        total = sum(len(v) for v in self._store_dict.values())
        if total > self._max_entries:
            # Remove the oldest entry (first in insertion order per fingerprint)
            for fp in list(self._store_dict):
                if self._store_dict[fp]:
                    self._store_dict[fp].pop(0)
                    if not self._store_dict[fp]:
                        del self._store_dict[fp]
                    break

    def invalidate(self, fingerprint=None):
        if fingerprint is None:
            count = sum(len(v) for v in self._store_dict.values())
            self._store_dict.clear()
            return count
        entries = self._store_dict.pop(fingerprint, [])
        return len(entries)

    def stats(self):
        total  = self._hits + self._misses
        return {
            "backend":     "in_memory",
            "entries":     sum(len(v) for v in self._store_dict.values()),
            "fingerprints": len(self._store_dict),
            "hits":        self._hits,
            "misses":      self._misses,
            "hit_rate":    round(self._hits / total, 3) if total else 0.0,
            "threshold":   self._threshold,
            "ttl_seconds": self._ttl,
        }


# ---------------------------------------------------------------------------
# Delta (Databricks) implementation
# ---------------------------------------------------------------------------

class DeltaSemanticCache(SemanticCacheBase):
    """
    Delta-table-backed semantic cache.  Survives process restarts.

    Table schema:
        fingerprint   STRING       -- filter hash
        vector_json   STRING       -- JSON-encoded query embedding
        results_json  STRING       -- JSON-encoded result list
        expires_at    BIGINT       -- unix epoch seconds
        created_at    TIMESTAMP

    On each lookup the table is scanned for live rows matching the fingerprint,
    then cosine similarity is computed in Python on the small result set
    (typically < 50 rows per fingerprint).  This is intentionally simple —
    for high-traffic production use, replace with a Redis HSET pattern.
    """

    def __init__(
        self,
        workspace_client,
        warehouse_id:  str,
        table_name:    str   = "sandbox.venkat.semantic_cache",
        hit_threshold: float = HIT_THRESHOLD,
        ttl_seconds:   int   = TTL_SECONDS,
    ):
        from databricks.sdk.service.sql import StatementParameterListItem
        self._wc          = workspace_client
        self._wh          = warehouse_id
        self._table       = table_name
        self._threshold   = hit_threshold
        self._ttl         = ttl_seconds
        self._ParamItem   = StatementParameterListItem
        self._hits        = 0
        self._misses      = 0

    # -- public helpers -------------------------------------------------------

    def create_table_if_not_exists(self):
        self._exec(f"""
            CREATE TABLE IF NOT EXISTS {self._table} (
                fingerprint  STRING    NOT NULL,
                vector_json  STRING    NOT NULL,
                results_json STRING    NOT NULL,
                expires_at   BIGINT    NOT NULL,
                created_at   TIMESTAMP NOT NULL
            ) USING DELTA
        """)
        logger.info(f"[SEMANTIC CACHE] Delta table {self._table} ready")

    def invalidate(self, fingerprint=None):
        now = int(time.time())
        if fingerprint is None:
            # Delete all expired + all active entries
            self._exec(
                f"DELETE FROM {self._table} WHERE expires_at <= :now OR expires_at > :now",
                [self._ParamItem(name="now", value=str(now), type="LONG")],
            )
            return -1   # count unknown without a SELECT
        self._exec(
            f"DELETE FROM {self._table} WHERE fingerprint = :fp",
            [self._ParamItem(name="fp", value=fingerprint, type="STRING")],
        )
        return -1

    def stats(self):
        total = self._hits + self._misses
        return {
            "backend":     "delta",
            "table":       self._table,
            "hits":        self._hits,
            "misses":      self._misses,
            "hit_rate":    round(self._hits / total, 3) if total else 0.0,
            "threshold":   self._threshold,
            "ttl_seconds": self._ttl,
        }

    # -- SemanticCacheBase implementation -------------------------------------

    def _lookup(self, query_vector, fingerprint):
        now  = int(time.time())
        rows = self._fetch_live_rows(fingerprint, now)

        for row in rows:
            try:
                cached_vec = json.loads(row[0])
                sim = _cosine_similarity(query_vector, cached_vec)
                if sim >= self._threshold:
                    self._hits += 1
                    logger.debug(f"[SEMANTIC CACHE] Delta hit cosine={sim:.4f}")
                    return json.loads(row[1])
            except Exception as e:
                logger.warning(f"[SEMANTIC CACHE] Could not parse cached row: {e}")

        self._misses += 1
        return None

    def _store(self, query_vector, fingerprint, results):
        expires = int(time.time()) + self._ttl
        try:
            self._exec(
                f"INSERT INTO {self._table} "
                "(fingerprint, vector_json, results_json, expires_at, created_at) "
                "VALUES (:fp, :vec, :res, :exp, current_timestamp())",
                [
                    self._ParamItem(name="fp",  value=fingerprint,              type="STRING"),
                    self._ParamItem(name="vec", value=json.dumps(query_vector), type="STRING"),
                    self._ParamItem(name="res", value=json.dumps(results),      type="STRING"),
                    self._ParamItem(name="exp", value=str(expires),             type="LONG"),
                ],
            )
            # Opportunistic cleanup of expired rows for this fingerprint
            self._exec(
                f"DELETE FROM {self._table} WHERE fingerprint = :fp AND expires_at <= :now",
                [
                    self._ParamItem(name="fp",  value=fingerprint,       type="STRING"),
                    self._ParamItem(name="now", value=str(int(time.time())), type="LONG"),
                ],
            )
        except Exception as e:
            logger.error(f"[SEMANTIC CACHE] Store error: {e}")

    # -- internal -------------------------------------------------------------

    def _fetch_live_rows(self, fingerprint: str, now: int) -> List[Tuple]:
        try:
            from databricks.sdk.service.sql import StatementState
            response = self._wc.statement_execution.execute_statement(
                warehouse_id=self._wh,
                statement=(
                    f"SELECT vector_json, results_json FROM {self._table} "
                    "WHERE fingerprint = :fp AND expires_at > :now"
                ),
                parameters=[
                    self._ParamItem(name="fp",  value=fingerprint,  type="STRING"),
                    self._ParamItem(name="now", value=str(now),     type="LONG"),
                ],
                wait_timeout="10s",
            )
            if (
                response.status.state == StatementState.SUCCEEDED
                and response.result
                and response.result.data_array
            ):
                return response.result.data_array
        except Exception as e:
            logger.error(f"[SEMANTIC CACHE] Fetch error: {e}")
        return []

    def _exec(self, stmt: str, params: Optional[List] = None):
        try:
            from databricks.sdk.service.sql import StatementState
            kwargs = {
                "warehouse_id": self._wh,
                "statement":    stmt,
                "wait_timeout": "15s",
            }
            if params:
                kwargs["parameters"] = params
            response = self._wc.statement_execution.execute_statement(**kwargs)
            if response.status.state not in (
                StatementState.SUCCEEDED, StatementState.RUNNING
            ):
                logger.error(f"[SEMANTIC CACHE] SQL error: {response.status.state}")
        except Exception as e:
            logger.error(f"[SEMANTIC CACHE] Exec error: {e}")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_cache(workspace_client=None, warehouse_id: Optional[str] = None) -> SemanticCacheBase:
    """
    Return the appropriate cache backend based on environment.

    - If SEMANTIC_CACHE_BACKEND=delta and workspace_client + warehouse_id are
      provided, returns DeltaSemanticCache.
    - Otherwise falls back to InMemorySemanticCache.

    Call once in ShoppingAssistantWorkflow.__init__ and store as self.semantic_cache.
    """
    backend = os.getenv("SEMANTIC_CACHE_BACKEND", "memory").lower()

    if backend == "delta" and workspace_client and warehouse_id:
        table = os.getenv("SEMANTIC_CACHE_TABLE", "sandbox.venkat.semantic_cache")
        cache = DeltaSemanticCache(
            workspace_client=workspace_client,
            warehouse_id=warehouse_id,
            table_name=table,
        )
        try:
            cache.create_table_if_not_exists()
        except Exception as e:
            logger.warning(f"[SEMANTIC CACHE] Delta init failed, falling back to in-memory: {e}")
            return InMemorySemanticCache()
        logger.info(f"[SEMANTIC CACHE] Using Delta backend ({table})")
        return cache

    logger.info("[SEMANTIC CACHE] Using in-memory backend")
    return InMemorySemanticCache()
