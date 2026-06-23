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

__all__ = ["DriftDetector", "SemanticDriftDetector", "DriftAlert"]


class DriftAlert(Exception):
    """Raised when model drift is detected."""


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if len(a) != len(b):
        raise ValueError(f"Vector dimensions mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


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


class SemanticDriftDetector:
    """Drift detection using Embedding vector similarity.

    Instead of hash comparison, uses cosine similarity between the current
    output's embedding and the golden sample's embedding.  This catches
    semantic drift (tone change, hallucination) even when the exact text
    differs.

    Parameters
    ----------
    actor:
        The Actor to run golden samples through.
    embed_provider:
        An :class:`LLMProvider` with ``embed()`` capability.
    golden_samples:
        List of ``{"input": ..., "expected_output": ...}`` dicts.
    check_interval:
        Run drift check every N processed items.  0 = disabled.
    similarity_threshold:
        Minimum cosine similarity to pass (0.0–1.0).  Default 0.85.
    """

    def __init__(
        self,
        actor: TaskActor,
        embed_provider: Any,
        golden_samples: Optional[List[Dict[str, str]]] = None,
        check_interval: int = 0,
        similarity_threshold: float = 0.85,
    ) -> None:
        self.actor = actor
        self.embed_provider = embed_provider
        self.golden_samples = golden_samples or []
        self.check_interval = check_interval
        self.similarity_threshold = similarity_threshold
        self._items_since_check = 0
        self._check_count = 0
        # Cache golden embeddings to avoid re-computing every check
        self._golden_embeddings: Optional[List[List[float]]] = None

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
        """Run golden samples and compare via embedding similarity.

        Returns a report dict with ``drift_detected``, ``avg_similarity``,
        and ``details``.
        """
        if not self.golden_samples:
            return {"drift_detected": False, "avg_similarity": 1.0, "details": []}

        self._check_count += 1

        # Cache golden embeddings on first check
        if self._golden_embeddings is None:
            self._golden_embeddings = []
            for sample in self.golden_samples:
                expected = sample.get("expected_output", "")
                if expected:
                    emb = await self.embed_provider.embed(expected)
                    self._golden_embeddings.append(emb)
                else:
                    self._golden_embeddings.append([])

        details = []
        similarities = []

        for i, sample in enumerate(self.golden_samples):
            input_text = sample.get("input", "")
            golden_emb = self._golden_embeddings[i]

            try:
                from jinja2 import Template
                rendered = Template(prompt_template).render(data=input_text)
                resp = await self.actor.provider.request(
                    messages=[{"role": "user", "content": rendered}]
                )

                if golden_emb:
                    current_emb = await self.embed_provider.embed(resp.content)
                    similarity = _cosine_similarity(golden_emb, current_emb)
                    similarities.append(similarity)
                    details.append({
                        "input_preview": input_text[:50],
                        "similarity": round(similarity, 4),
                        "passed": similarity >= self.similarity_threshold,
                    })
                else:
                    details.append({
                        "input_preview": input_text[:50],
                        "similarity": None,
                        "passed": True,
                        "note": "No golden embedding to compare",
                    })

            except Exception as exc:
                details.append({
                    "input_preview": input_text[:50],
                    "similarity": 0.0,
                    "passed": False,
                    "error": str(exc),
                })

        avg_similarity = (
            sum(similarities) / len(similarities) if similarities else 1.0
        )
        drift_detected = avg_similarity < self.similarity_threshold

        report = {
            "drift_detected": drift_detected,
            "avg_similarity": round(avg_similarity, 4),
            "similarity_threshold": self.similarity_threshold,
            "check_number": self._check_count,
            "details": details,
        }

        if drift_detected:
            raise DriftAlert(
                f"🚨 [语义漂移检测] 第 {self._check_count} 次检查发现语义漂移！"
                f" 平均相似度: {avg_similarity:.4f} (阈值: {self.similarity_threshold})"
                f" 建议暂停执行并重新采样。"
            )

        return report
