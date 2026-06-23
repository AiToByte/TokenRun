"""
Drift Detector — periodic consistency checks during mass production.

Runs golden samples (from solidified skills or sampling phase) through
the Actor at configurable intervals.  If outputs deviate beyond a
threshold, raises an alert indicating model drift.
"""

from __future__ import annotations

import hashlib
from typing import Any, Callable, Dict, List, Optional

from core.actor import TaskActor

__all__ = ["DriftDetector", "DriftAlert"]


class DriftAlert(Exception):
    """Raised when model drift is detected."""


class DriftDetector:
    """Monitor output consistency during long-running production.

    Parameters
    ----------
    actor:
        The Actor to run golden samples through.
    golden_samples:
        List of ``{"input": ..., "expected_output_hash": ...}`` dicts.
    check_interval:
        Run drift check every N processed items.  0 = disabled.
    threshold:
        Fraction of samples that must match (0.0–1.0).  Default 0.8.
    """

    def __init__(
        self,
        actor: TaskActor,
        golden_samples: Optional[List[Dict[str, str]]] = None,
        check_interval: int = 0,
        threshold: float = 0.8,
    ) -> None:
        self.actor = actor
        self.golden_samples = golden_samples or []
        self.check_interval = check_interval
        self.threshold = threshold
        self._items_since_check = 0
        self._check_count = 0

    @property
    def enabled(self) -> bool:
        """True if drift detection is active."""
        return self.check_interval > 0 and len(self.golden_samples) > 0

    def tick(self) -> bool:
        """Increment the item counter.  Returns True if a check is due."""
        if not self.enabled:
            return False
        self._items_since_check += 1
        if self._items_since_check >= self.check_interval:
            self._items_since_check = 0
            return True
        return False

    async def run_check(self, prompt_template: str) -> Dict[str, Any]:
        """Run golden samples through the Actor and compare outputs.

        Returns a report dict with ``drift_detected``, ``match_rate``,
        and ``details``.
        """
        if not self.golden_samples:
            return {"drift_detected": False, "match_rate": 1.0, "details": []}

        self._check_count += 1
        matches = 0
        details = []

        for sample in self.golden_samples:
            input_text = sample.get("input", "")
            expected_hash = sample.get("expected_output_hash", "")

            try:
                from jinja2 import Template
                rendered = Template(prompt_template).render(data=input_text)
                resp = await self.actor.provider.request(
                    messages=[{"role": "user", "content": rendered}]
                )
                actual_hash = hashlib.sha256(
                    resp.content.encode()
                ).hexdigest()[:16]
                matched = actual_hash == expected_hash if expected_hash else True
                if matched:
                    matches += 1
                details.append({
                    "input_preview": input_text[:50],
                    "matched": matched,
                })
            except Exception as exc:
                details.append({
                    "input_preview": input_text[:50],
                    "matched": False,
                    "error": str(exc),
                })

        total = len(self.golden_samples)
        match_rate = matches / total if total > 0 else 1.0
        drift_detected = match_rate < self.threshold

        report = {
            "drift_detected": drift_detected,
            "match_rate": round(match_rate, 4),
            "check_number": self._check_count,
            "details": details,
        }

        if drift_detected:
            raise DriftAlert(
                f"🚨 [漂移检测] 第 {self._check_count} 次检查发现模型漂移！"
                f" 匹配率: {match_rate:.1%} (阈值: {self.threshold:.1%})"
                f" 建议暂停执行并重新采样。"
            )

        return report
