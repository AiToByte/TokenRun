"""Tests for core.persistence — real SQLite trace storage."""

import pytest
from core.persistence import TaskPersistence


@pytest.fixture
def persistence(tmp_path):
    return TaskPersistence(db_path=str(tmp_path / "test.db"))


class TestTaskPersistence:
    def test_save_and_get_status(self, persistence):
        persistence.save_trace("unit-1", "hash-1", "completed", {"data": "test"}, "output")
        assert persistence.get_status("unit-1") == "completed"

    def test_get_status_unknown(self, persistence):
        assert persistence.get_status("nonexistent") is None

    def test_idempotent_save(self, persistence):
        persistence.save_trace("unit-1", "hash-1", "running", {"step": 1})
        persistence.save_trace("unit-1", "hash-1", "completed", {"step": 2}, "done")
        assert persistence.get_status("unit-1") == "completed"

    def test_get_pending_ids(self, persistence):
        persistence.save_trace("a", "h1", "completed", {}, "out")
        persistence.save_trace("b", "h2", "running", {})
        pending = persistence.get_pending_ids(["a", "b", "c"])
        assert "a" not in pending
        assert "b" in pending
        assert "c" in pending

    def test_get_pending_ids_empty(self, persistence):
        assert persistence.get_pending_ids([]) == []

    def test_get_pending_ids_all_done(self, persistence):
        persistence.save_trace("a", "h1", "completed", {}, "out")
        assert persistence.get_pending_ids(["a"]) == []

    def test_get_all_traces(self, persistence):
        persistence.save_trace("a", "h1", "completed", {"x": 1}, "out1")
        persistence.save_trace("b", "h2", "failed", {"x": 2})
        traces = persistence.get_all_traces()
        assert len(traces) == 2
        assert traces[0]["id"] == "a"

    def test_get_all_traces_empty(self, persistence):
        assert persistence.get_all_traces() == []

    def test_creates_directory(self, tmp_path):
        db_path = str(tmp_path / "subdir" / "test.db")
        p = TaskPersistence(db_path=db_path)
        p.save_trace("x", "h", "ok", {})
        assert p.get_status("x") == "ok"

    def test_multiple_units(self, persistence):
        for i in range(100):
            persistence.save_trace(f"unit-{i}", f"hash-{i}", "completed", {}, f"out-{i}")
        assert len(persistence.get_all_traces()) == 100
        assert persistence.get_pending_ids([f"unit-{i}" for i in range(100)]) == []
