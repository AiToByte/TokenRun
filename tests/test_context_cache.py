"""Tests for core.context_cache — prompt cache optimization module."""

from __future__ import annotations

import time

import pytest

from core.context_cache import ContextCache


class TestContextCache:
    """Tests for ContextCache class."""

    def test_init_defaults(self):
        cache = ContextCache()
        assert cache.max_size == 1000
        assert cache.ttl_seconds == 300

    def test_init_custom(self):
        cache = ContextCache(max_size=500, ttl_seconds=60)
        assert cache.max_size == 500
        assert cache.ttl_seconds == 60

    def test_compute_prefix_hash(self):
        cache = ContextCache()
        h = cache.compute_prefix_hash("Hello, World!")
        assert isinstance(h, str)
        assert len(h) == 16  # SHA-256 truncated to 16 hex chars

    def test_compute_prefix_hash_same_prefix(self):
        cache = ContextCache()
        # Both strings share the same first 50 chars (long common prefix)
        prefix = "A" * 50
        h1 = cache.compute_prefix_hash(prefix + " item 1 data")
        h2 = cache.compute_prefix_hash(prefix + " item 2 data")
        assert h1 == h2  # same first 50 chars → same hash

    def test_compute_prefix_hash_different_prefix(self):
        cache = ContextCache()
        h1 = cache.compute_prefix_hash("Alpha: first item data")
        h2 = cache.compute_prefix_hash("Beta: second item data")
        assert h1 != h2

    def test_compute_prefix_hash_custom_length(self):
        cache = ContextCache()
        h1 = cache.compute_prefix_hash("ABCDEFGHIJKLMNOP", length=5)
        h2 = cache.compute_prefix_hash("ABCDEFGHIJKLMNOZ", length=5)
        assert h1 == h2  # same first 5 chars

    def test_sort_for_cache(self):
        cache = ContextCache()
        items = ["banana data", "apple data", "cherry data", "avocado data"]
        sorted_items = cache.sort_for_cache(items)
        assert sorted_items[0].startswith("apple")
        assert sorted_items[1].startswith("avocado")

    def test_sort_for_cache_empty(self):
        cache = ContextCache()
        assert cache.sort_for_cache([]) == []

    def test_sort_for_cache_single(self):
        cache = ContextCache()
        result = cache.sort_for_cache(["only one"])
        assert result == ["only one"]

    def test_should_use_cache_short_prompt(self):
        cache = ContextCache()
        assert cache.should_use_cache("hi") is False

    def test_should_use_cache_long_prompt(self):
        cache = ContextCache()
        assert cache.should_use_cache("x" * 200) is True

    def test_should_use_cache_threshold(self):
        cache = ContextCache(cache_threshold=100)
        assert cache.should_use_cache("x" * 99) is False
        assert cache.should_use_cache("x" * 100) is True


class TestContextCacheEntry:
    """Tests for cache entry tracking."""

    def test_put_and_get(self):
        cache = ContextCache()
        cache.put("key1", "value1")
        assert cache.get("key1") == "value1"

    def test_get_missing_returns_none(self):
        cache = ContextCache()
        assert cache.get("nonexistent") is None

    def test_put_evicts_oldest(self):
        cache = ContextCache(max_size=2)
        cache.put("k1", "v1")
        cache.put("k2", "v2")
        cache.put("k3", "v3")  # should evict k1
        assert cache.get("k1") is None
        assert cache.get("k2") == "v2"
        assert cache.get("k3") == "v3"

    def test_put_updates_existing(self):
        cache = ContextCache(max_size=2)
        cache.put("k1", "v1")
        cache.put("k1", "v1_updated")
        assert cache.get("k1") == "v1_updated"

    def test_ttl_expiration(self):
        cache = ContextCache(ttl_seconds=1)  # 1 second TTL
        cache.put("k1", "v1")
        assert cache.get("k1") == "v1"  # not expired yet
        import time
        time.sleep(1.1)  # wait for expiration
        assert cache.get("k1") is None  # expired

    def test_get_stats(self):
        cache = ContextCache(max_size=100)
        cache.put("k1", "v1")
        cache.get("k1")  # hit
        cache.get("nonexistent")  # miss
        stats = cache.get_stats()
        assert stats["size"] == 1
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["hit_rate"] == 0.5
        assert stats["max_size"] == 100

    def test_clear(self):
        cache = ContextCache()
        cache.put("k1", "v1")
        cache.put("k2", "v2")
        cache.clear()
        assert cache.get("k1") is None
        assert cache.get("k2") is None
        stats = cache.get_stats()
        assert stats["size"] == 0

    def test_contains(self):
        cache = ContextCache()
        cache.put("k1", "v1")
        assert cache.contains("k1") is True
        assert cache.contains("k2") is False

    def test_len(self):
        cache = ContextCache()
        assert len(cache) == 0
        cache.put("k1", "v1")
        assert len(cache) == 1
