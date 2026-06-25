"""Tests for core.quality_gate — quality circuit breaker module."""

from __future__ import annotations

import pytest

from core.quality_gate import QualityGate


class TestQualityGate:
    """Tests for QualityGate class."""

    def test_init_defaults(self):
        gate = QualityGate()
        assert gate.threshold == 0.6
        assert gate.window_size == 5

    def test_init_custom(self):
        gate = QualityGate(threshold=0.8, window_size=3)
        assert gate.threshold == 0.8
        assert gate.window_size == 3

    def test_not_halted_initially(self):
        gate = QualityGate()
        assert gate.is_halted() is False

    def test_single_low_score_not_halted(self):
        gate = QualityGate(threshold=0.6, window_size=3)
        gate.record_score(0.3)
        assert gate.is_halted() is False

    def test_consecutive_low_scores_trigger_halt(self):
        gate = QualityGate(threshold=0.6, window_size=3)
        gate.record_score(0.3)
        gate.record_score(0.4)
        gate.record_score(0.5)
        assert gate.is_halted() is True

    def test_mixed_scores_no_halt(self):
        gate = QualityGate(threshold=0.6, window_size=3)
        gate.record_score(0.3)
        gate.record_score(0.8)  # above threshold
        gate.record_score(0.4)
        assert gate.is_halted() is False

    def test_window_slides(self):
        gate = QualityGate(threshold=0.6, window_size=3)
        gate.record_score(0.3)
        gate.record_score(0.4)
        gate.record_score(0.5)
        # Window is full and all below threshold → halted
        assert gate.is_halted() is True

    def test_reset_clears_halt(self):
        gate = QualityGate(threshold=0.6, window_size=3)
        gate.record_score(0.3)
        gate.record_score(0.4)
        gate.record_score(0.5)
        assert gate.is_halted() is True
        gate.reset()
        assert gate.is_halted() is False

    def test_reset_clears_scores(self):
        gate = QualityGate(threshold=0.6, window_size=3)
        gate.record_score(0.3)
        gate.reset()
        assert gate.get_recent_scores() == []

    def test_get_window_average(self):
        gate = QualityGate(threshold=0.6, window_size=5)
        gate.record_score(0.4)
        gate.record_score(0.6)
        gate.record_score(0.8)
        avg = gate.get_window_average()
        assert abs(avg - 0.6) < 0.01

    def test_get_window_average_empty(self):
        gate = QualityGate()
        assert gate.get_window_average() == 0.0

    def test_get_recent_scores(self):
        gate = QualityGate(window_size=5)
        for score in [0.3, 0.4, 0.5, 0.6, 0.7]:
            gate.record_score(score)
        scores = gate.get_recent_scores()
        assert len(scores) == 5
        assert scores == [0.3, 0.4, 0.5, 0.6, 0.7]

    def test_window_overflow_keeps_recent(self):
        gate = QualityGate(window_size=3)
        for score in [0.1, 0.2, 0.3, 0.4, 0.5]:
            gate.record_score(score)
        scores = gate.get_recent_scores()
        assert len(scores) == 3
        assert scores == [0.3, 0.4, 0.5]

    def test_get_report(self):
        gate = QualityGate(threshold=0.6, window_size=3)
        gate.record_score(0.8)
        gate.record_score(0.7)
        report = gate.get_report()
        assert "threshold" in report
        assert "window_size" in report
        assert "recent_scores" in report
        assert "average" in report
        assert "is_halted" in report
        assert report["threshold"] == 0.6
        assert report["is_halted"] is False

    def test_get_report_halted(self):
        gate = QualityGate(threshold=0.6, window_size=2)
        gate.record_score(0.3)
        gate.record_score(0.4)
        report = gate.get_report()
        assert report["is_halted"] is True

    def test_exact_threshold_not_halted(self):
        """Scores exactly at threshold should NOT trigger halt (strictly below)."""
        gate = QualityGate(threshold=0.6, window_size=2)
        gate.record_score(0.6)
        gate.record_score(0.6)
        assert gate.is_halted() is False

    def test_just_below_threshold_triggers_halt(self):
        gate = QualityGate(threshold=0.6, window_size=2)
        gate.record_score(0.59)
        gate.record_score(0.59)
        assert gate.is_halted() is True
