"""
Context Cache — prompt cache optimization for TokenRun.

Provides cache-aware data sorting, prefix hashing, and LRU caching
to maximize prompt cache hits when processing large data streams.

Usage::

    from core.context_cache import ContextCache

    cache = ContextCache(max_size=1000, ttl_seconds=300)
    sorted_data = cache.sort_for_cache(data_stream)
    cache.put("prompt_hash", "cached_response")
    result = cache.get("prompt_hash")
"""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional

__all__ = ["ContextCache"]


class ContextCache:
    """Cache-aware data optimizer for prompt caching.

    Parameters
    ----------
    max_size:
        Maximum number of cache entries (LRU eviction).
    ttl_seconds:
        Time-to-live for cache entries in seconds.
    cache_threshold:
        Minimum prompt length (chars) to consider caching.
    """

    def __init__(
        self,
        max_size: int = 1000,
        ttl_seconds: int = 300,
        cache_threshold: int = 100,
    ) -> None:
        if max_size <= 0:
            raise ValueError("max_size 必须大于 0。")
        if ttl_seconds < 0:
            raise ValueError("ttl_seconds 不能为负数。")
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self.cache_threshold = cache_threshold
        # OrderedDict for O(1) LRU eviction
        self._cache: OrderedDict[str, tuple[str, float]] = OrderedDict()
        self._hits = 0
        self._misses = 0

    # ------------------------------------------------------------------
    # Prefix hashing
    # ------------------------------------------------------------------

    @staticmethod
    def compute_prefix_hash(text: str, length: int = 50) -> str:
        """Compute SHA-256 hash of the first *length* characters.

        Parameters
        ----------
        text:
            Input text to hash.
        length:
            Number of prefix characters to hash (default 50).

        Returns
        -------
        str
            Truncated 16-character hex hash.
        """
        prefix = text[:length]
        return hashlib.sha256(prefix.encode()).hexdigest()[:16]

    # ------------------------------------------------------------------
    # Cache-friendly sorting
    # ------------------------------------------------------------------

    @staticmethod
    def sort_for_cache(data: List[str]) -> List[str]:
        """Sort data items by first 50 characters for cache-friendly ordering.

        Items with similar prefixes are grouped together, allowing the LLM
        provider to reuse cached context between consecutive requests.

        Parameters
        ----------
        data:
            Input data items to sort.

        Returns
        -------
        list[str]
            Sorted copy of the input data.
        """
        return sorted(data, key=lambda x: x[:50])

    # ------------------------------------------------------------------
    # Cache eligibility
    # ------------------------------------------------------------------

    def should_use_cache(self, prompt: str) -> bool:
        """Check if a prompt is long enough to benefit from caching.

        Parameters
        ----------
        prompt:
            The prompt text to check.

        Returns
        -------
        bool
            True if the prompt exceeds the cache threshold.
        """
        return len(prompt) >= self.cache_threshold

    # ------------------------------------------------------------------
    # LRU Cache operations
    # ------------------------------------------------------------------

    def put(self, key: str, value: str) -> None:
        """Store a value in the cache.

        Parameters
        ----------
        key:
            Cache key.
        value:
            Value to store.
        """
        # Remove existing entry if present (will be re-inserted at end)
        if key in self._cache:
            del self._cache[key]

        # Evict oldest if at capacity
        while len(self._cache) >= self.max_size:
            self._cache.popitem(last=False)

        self._cache[key] = (value, time.time())

    def get(self, key: str) -> Optional[str]:
        """Retrieve a value from the cache.

        Parameters
        ----------
        key:
            Cache key to look up.

        Returns
        -------
        str or None
            Cached value if found and not expired, else None.
        """
        if key not in self._cache:
            self._misses += 1
            return None

        value, ts = self._cache[key]

        # Check TTL (ttl_seconds <= 0 means no TTL)
        if self.ttl_seconds > 0 and (time.time() - ts) > self.ttl_seconds:
            del self._cache[key]
            self._misses += 1
            return None

        # Move to end (most recently used)
        self._cache.move_to_end(key)
        self._hits += 1
        return value

    def contains(self, key: str) -> bool:
        """Check if a key exists and is not expired (no side effects on stats)."""
        if key not in self._cache:
            return False
        _, ts = self._cache[key]
        if self.ttl_seconds > 0 and (time.time() - ts) > self.ttl_seconds:
            return False
        return True

    def clear(self) -> None:
        """Clear all cache entries and reset stats."""
        self._cache.clear()
        self._hits = 0
        self._misses = 0

    def __len__(self) -> int:
        """Return the number of entries in the cache."""
        return len(self._cache)

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        """Return cache statistics.

        Returns
        -------
        dict
            Contains size, max_size, hits, misses, hit_rate, evictions.
        """
        total = self._hits + self._misses
        return {
            "size": len(self._cache),
            "max_size": self.max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self._hits / total if total > 0 else 0.0,
            "ttl_seconds": self.ttl_seconds,
        }
