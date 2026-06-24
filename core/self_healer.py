"""
Self-Healer — automatic Prompt optimization from Critique patterns.

Monitors Critic feedback during execution.  When the same critique
pattern repeats across N consecutive items, the healer invokes a
"meta-model" to generate an improved Prompt and creates a new
PromptVersion for user approval.
"""

from __future__ import annotations

from collections import Counter
from typing import List, Optional

from core.models import PromptVersion, TaskNode
from core.prompt_lineage import PromptLineageManager
from gateway.provider import LLMProvider

__all__ = ["SelfHealer", "HealingSuggestion"]


class HealingSuggestion:
    """A suggested Prompt improvement from the self-healer."""

    def __init__(
        self,
        original_prompt: str,
        suggested_prompt: str,
        critique_pattern: str,
        frequency: int,
        confidence: float,
    ) -> None:
        self.original_prompt = original_prompt
        self.suggested_prompt = suggested_prompt
        self.critique_pattern = critique_pattern
        self.frequency = frequency
        self.confidence = confidence

    def __repr__(self) -> str:
        return (
            f"HealingSuggestion(pattern='{self.critique_pattern[:50]}', "
            f"freq={self.frequency}, confidence={self.confidence:.2f})"
        )


class SelfHealer:
    """Monitor Critique patterns and suggest Prompt improvements.

    Parameters
    ----------
    meta_provider:
        An LLM provider for the "meta-model" that generates improved
        prompts (typically GPT-4o or similar high-capability model).
    min_pattern_frequency:
        Minimum number of similar critiques before triggering healing.
    """

    def __init__(
        self,
        meta_provider: Optional[LLMProvider] = None,
        min_pattern_frequency: int = 3,
    ) -> None:
        self.meta_provider = meta_provider
        self.min_pattern_frequency = min_pattern_frequency
        self._critiques: List[str] = []
        self._lineage = PromptLineageManager()

    def record_critique(self, critique: str) -> None:
        """Record a Critic's critique for pattern analysis."""
        if critique:
            self._critiques.append(critique.strip())

    def check_healing_needed(self) -> Optional[HealingSuggestion]:
        """Check if a healing suggestion should be generated.

        Returns a ``HealingSuggestion`` if a pattern is detected,
        otherwise ``None``.
        """
        if len(self._critiques) < self.min_pattern_frequency:
            return None

        # Find the most common critique pattern
        # Normalize critiques by taking first 5 chars as pattern key
        patterns = Counter()
        for c in self._critiques:
            key = c[:5].strip()
            if key:
                patterns[key] += 1

        if not patterns:
            return None

        most_common, count = patterns.most_common(1)[0]
        if count < self.min_pattern_frequency:
            return None

        return HealingSuggestion(
            original_prompt="",
            suggested_prompt="",
            critique_pattern=most_common,
            frequency=count,
            confidence=min(1.0, count / 10.0),
        )

    async def generate_healing(
        self,
        node: TaskNode,
    ) -> Optional[HealingSuggestion]:
        """Generate a healing suggestion using the meta-model.

        Analyzes the accumulated critiques and asks the meta-model
        to produce an improved prompt template.
        """
        if not self.meta_provider:
            return None

        suggestion = self.check_healing_needed()
        if not suggestion:
            return None

        # Build the meta-prompt
        critiques_summary = "\n".join(
            f"- {c}"
            for c in self._critiques[-20:]  # last 20 critiques
        )

        meta_prompt = f"""你是一个 Prompt 工程专家。

以下是当前 Prompt 模板：
```
{node.actor_prompt_template}
```

以下是 Critic 最近给出的反复出现的批评（共 {suggestion.frequency} 次）：
{critiques_summary}

请分析这些批评的共同模式，然后生成一个改进版的 Prompt 模板。

要求：
1. 保持 {{ data }} 变量不变
2. 针对批评的核心问题进行修正
3. 只输出改进后的 Prompt 模板，不要解释
"""

        response = await self.meta_provider.request(
            messages=[{"role": "user", "content": meta_prompt}],
            temperature=0.3,
        )

        improved_prompt = response.content.strip()
        if improved_prompt and improved_prompt != node.actor_prompt_template:
            suggestion.original_prompt = node.actor_prompt_template
            suggestion.suggested_prompt = improved_prompt
            return suggestion

        return None

    def apply_healing(
        self,
        node: TaskNode,
        suggestion: HealingSuggestion,
        change_log: str = "",
    ) -> PromptVersion:
        """Apply a healing suggestion by creating a new PromptVersion."""
        version = self._lineage.create_version(
            node,
            suggestion.suggested_prompt,
            change_log=change_log or f"Auto-heal: {suggestion.critique_pattern}",
            stats={
                "healing_frequency": suggestion.frequency,
                "healing_confidence": suggestion.confidence,
            },
        )
        # Reset critique history after healing
        self._critiques.clear()
        return version

    def reset(self) -> None:
        """Clear critique history."""
        self._critiques.clear()
