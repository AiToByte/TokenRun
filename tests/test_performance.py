"""Performance benchmark tests — verify latency and throughput under load."""

import time
import pytest

from core.persistence import TaskPersistence
from core.solidifier import SkillSolidifier
from gateway.privacy import PrivacyRedactor


class TestPrivacyPerformance:
    def test_mask_throughput(self):
        """Should mask 10000 texts in under 5 seconds."""
        r = PrivacyRedactor()
        text = "Contact alice@test.com or call 13800138000, server 10.0.0.1"

        start = time.time()
        for _ in range(10000):
            r.mask(text)
        elapsed = time.time() - start

        assert elapsed < 5.0, f"10000 masks took {elapsed:.2f}s (expected < 5s)"

    def test_unmask_throughput(self):
        """Should unmask 10000 texts in under 5 seconds."""
        r = PrivacyRedactor()
        text = "Contact alice@test.com or call 13800138000"
        masked = r.mask(text)

        start = time.time()
        for _ in range(10000):
            r.unmask(masked)
        elapsed = time.time() - start

        assert elapsed < 5.0, f"10000 unmasks took {elapsed:.2f}s (expected < 5s)"

    def test_large_text_mask(self):
        """Should handle 1MB text without excessive delay."""
        r = PrivacyRedactor()
        # 1MB text with some emails sprinkled in
        base = "Lorem ipsum dolor sit amet. " * 40000  # ~1MB
        text = base + "alice@test.com " + base

        start = time.time()
        masked = r.mask(text)
        elapsed = time.time() - start

        assert elapsed < 10.0, f"1MB mask took {elapsed:.2f}s (expected < 10s)"
        assert "alice@test.com" not in masked


class TestPersistencePerformance:
    def test_bulk_write_performance(self, tmp_path):
        """Should write 1000 traces in under 10 seconds."""
        p = TaskPersistence(db_path=str(tmp_path / "perf.db"))

        start = time.time()
        for i in range(1000):
            p.save_trace(f"unit-{i}", f"hash-{i}", "completed", {"idx": i}, f"out-{i}")
        elapsed = time.time() - start

        assert elapsed < 10.0, f"1000 writes took {elapsed:.2f}s (expected < 10s)"

    def test_bulk_read_performance(self, tmp_path):
        """Should read 1000 traces in under 5 seconds."""
        p = TaskPersistence(db_path=str(tmp_path / "perf.db"))
        for i in range(1000):
            p.save_trace(f"unit-{i}", f"hash-{i}", "completed", {"idx": i}, f"out-{i}")

        start = time.time()
        traces = p.get_all_traces()
        elapsed = time.time() - start

        assert elapsed < 5.0, f"1000 reads took {elapsed:.2f}s (expected < 5s)"
        assert len(traces) == 1000

    def test_pending_ids_performance(self, tmp_path):
        """Should filter 1000 IDs in under 1 second."""
        p = TaskPersistence(db_path=str(tmp_path / "perf.db"))
        for i in range(500):
            p.save_trace(f"done-{i}", f"h-{i}", "completed", {}, f"out-{i}")

        all_ids = [f"done-{i}" for i in range(500)] + [f"pending-{i}" for i in range(500)]

        start = time.time()
        pending = p.get_pending_ids(all_ids)
        elapsed = time.time() - start

        assert elapsed < 1.0, f"Filtering 1000 IDs took {elapsed:.2f}s (expected < 1s)"
        assert len(pending) == 500


class TestSolidifierPerformance:
    def test_distill_large_trace_set(self, tmp_path):
        """Should distill 1000 traces in under 5 seconds."""
        s = SkillSolidifier(vault_path=str(tmp_path / "vault"))
        traces = [
            {
                "status": "success" if i % 3 != 0 else "exhausted",
                "history": [
                    {"iteration": 1, "output": f"Output {i}", "passed": True, "score": 0.8 + (i % 20) * 0.01, "critique": None}
                ],
            }
            for i in range(1000)
        ]

        start = time.time()
        path = s.distill("Perf Test", traces, "Do {{ data }}")
        elapsed = time.time() - start

        assert elapsed < 5.0, f"Distilling 1000 traces took {elapsed:.2f}s (expected < 5s)"
        assert path.endswith(".trs")
