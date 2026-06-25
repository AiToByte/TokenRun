"""
Quality Gate — circuit breaker for production quality monitoring.

Tracks a sliding window of task scores and halts the pipeline when
consecutive scores fall below a configurable threshold.  Extracted
from orchestrator.py for testability and reuse.

Usage::

    from core.quality_gate import QualityGate

    gate = QualityGate(threshold=0.6, window_size=5)
    gate.record_score(result["score"])
    if gate.is_halted():
        print("Quality circuit breaker triggered!")
"""

from __future__ import annotations

from collections import deque
from typing import Any, Dict, List

__all__ = ["QualityGate"]


class QualityGate:
    """Sliding-window quality circuit breaker.

    Parameters
    ----------
    threshold:
        Minimum acceptable score.  Scores strictly below this are "low".
    window_size:
        Number of recent scores to track.  When all scores in the window
        are below the threshold, the gate halts.
    """

    def __init__(
        self,
        threshold: float = 0.6,
        window_size: int = 5,
        recovery_window: int = 0,
    ) -> None:
        self.threshold = threshold
        self.window_size = window_size
        self.recovery_window = recovery_window
        self._recent_scores: deque = deque(maxlen=window_size)
        self._halted = False
        self._good_since_halt = 0

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def is_halted(self) -> bool:
        """Check if the quality gate has been triggered.

        Returns
        -------
        bool
            True if all scores in the window are below the threshold.
        """
        return self._halted

    def reset(self) -> None:
        """Reset the gate to its initial state."""
        self._recent_scores.clear()
        self._halted = False
        self._good_since_halt = 0

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_score(self, score: float) -> None:
        """Record a new score and check the quality window.

        Parameters
        ----------
        score:
            The task quality score (typically 0.0–1.0).
        """
        self._recent_scores.append(score)

        if self._halted:
            # Recovery logic: auto-unhalt after N consecutive good scores
            if score >= self.threshold:
                self._good_since_halt += 1
                if (
                    self.recovery_window > 0
                    and self._good_since_halt >= self.recovery_window
                ):
                    self._halted = False
                    self._good_since_halt = 0
            else:
                self._good_since_halt = 0
        else:
            # Check if window is full and all scores are below threshold
            if len(self._recent_scores) >= self.window_size:
                if all(s < self.threshold for s in self._recent_scores):
                    self._halted = True
                    self._good_since_halt = 0

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_recent_scores(self) -> List[float]:
        """Return the current sliding window of scores."""
        return list(self._recent_scores)

    def get_window_average(self) -> float:
        """Return the average of scores in the current window."""
        if not self._recent_scores:
            return 0.0
        return sum(self._recent_scores) / len(self._recent_scores)

    def get_report(self) -> Dict[str, Any]:
        """Return a diagnostic report.

        Returns
        -------
        dict
            Contains threshold, window_size, recent_scores, average,
            is_halted.
        """
        return {
            "threshold": self.threshold,
            "window_size": self.window_size,
            "recovery_window": self.recovery_window,
            "recent_scores": list(self._recent_scores),
            "average": self.get_window_average(),
            "is_halted": self._halted,
        }
