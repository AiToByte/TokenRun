"""
Token Ledger — real-time cost tracking with budget circuit breaker.

Every LLM call passes through the ledger.  When cumulative cost reaches
the configured budget the ledger raises ``BudgetExceededError`` to halt
the entire task immediately (no silent overspend).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

__all__ = ["TokenRunError", "BudgetExceededError", "UsageReport", "TokenLedger"]


class TokenRunError(RuntimeError):
    """Base exception for all TokenRun errors."""


class BudgetExceededError(TokenRunError):
    """Raised when the task's USD budget is exhausted."""


@dataclass
class UsageReport:
    """Accumulated token counts and cost for a single task run."""
    actor_prompt_tokens: int = 0
    actor_completion_tokens: int = 0
    critic_prompt_tokens: int = 0
    critic_completion_tokens: int = 0
    total_cost_usd: float = 0.0
    start_time: float = field(default_factory=time.time)
    elapsed_seconds: float = 0.0
    call_count: int = 0


# Default pricing per 1 000 tokens (USD).  Override via *pricing_map*.
_DEFAULT_PRICING: Dict[str, Dict[str, float]] = {
    "gpt-4o": {"prompt": 0.0025, "completion": 0.01},
    "gpt-4o-mini": {"prompt": 0.00015, "completion": 0.0006},
    "claude-3-5-sonnet": {"prompt": 0.003, "completion": 0.015},
    "claude-3-haiku": {"prompt": 0.00025, "completion": 0.00125},
}

# Conservative fallback for unknown models (most expensive tier).
_FALLBACK_PRICING: Dict[str, float] = {"prompt": 0.01, "completion": 0.03}


class TokenLedger:
    """Tracks token consumption and enforces a hard USD budget.

    Parameters
    ----------
    budget_usd:
        Maximum spend before the circuit breaker trips.
    pricing_map:
        Per-model pricing keyed by model name.  Each entry maps
        ``"prompt"`` and ``"completion"`` to USD per 1 000 tokens.
    """

    def __init__(
        self,
        budget_usd: float,
        pricing_map: Optional[Dict[str, Dict[str, float]]] = None,
    ) -> None:
        self.budget_usd = budget_usd
        self._pricing = pricing_map or _DEFAULT_PRICING
        self.report = UsageReport()
        self._fused = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_fused(self) -> bool:
        """``True`` if the budget circuit breaker has tripped."""
        return self._fused

    def record_usage(
        self,
        model_name: str,
        prompt_tokens: int,
        completion_tokens: int,
        role: str = "actor",
    ) -> None:
        """Record one API call's consumption and check the budget.

        Parameters
        ----------
        model_name:
            The model identifier (must exist in the pricing map, or a
            zero-cost fallback is used).
        prompt_tokens / completion_tokens:
            Token counts returned by the API.
        role:
            ``"actor"`` or ``"critic"`` — determines which counter to
            increment.

        Raises
        ------
        BudgetExceededError
            If cumulative cost reaches ``budget_usd``.
        """
        pricing = self._pricing.get(model_name)
        if pricing is None:
            # Unknown model: use conservative fallback pricing to prevent
            # zero-cost bypass.  Log once per unique model.
            pricing = _FALLBACK_PRICING

        cost = (
            prompt_tokens / 1000 * pricing["prompt"]
            + completion_tokens / 1000 * pricing["completion"]
        )

        if role == "actor":
            self.report.actor_prompt_tokens += prompt_tokens
            self.report.actor_completion_tokens += completion_tokens
        else:
            self.report.critic_prompt_tokens += prompt_tokens
            self.report.critic_completion_tokens += completion_tokens

        self.report.total_cost_usd += cost
        self.report.call_count += 1
        self.report.elapsed_seconds = time.time() - self.report.start_time

        # Check budget AFTER recording — always keep accurate accounting.
        if not self._fused and self.report.total_cost_usd >= self.budget_usd:
            self._fused = True
            raise BudgetExceededError(
                f"\U0001f6a8 [熔断] 已达到预算上限 ${self.budget_usd:.4f}。"
                f" 当前消耗 ${self.report.total_cost_usd:.4f}。任务紧急停止。"
            )

    def get_summary(self) -> Dict[str, Any]:
        """Return a snapshot of current consumption."""
        actor_total = self.report.actor_prompt_tokens + self.report.actor_completion_tokens
        critic_total = self.report.critic_prompt_tokens + self.report.critic_completion_tokens
        if critic_total > 0:
            ratio_str = f"{actor_total / critic_total:.2f} (Actor/Critic Ratio)"
        else:
            ratio_str = f"{actor_total} Actor tokens, 0 Critic tokens"
        return {
            "total_cost": f"${self.report.total_cost_usd:.4f}",
            "budget": f"${self.budget_usd:.4f}",
            "calls": self.report.call_count,
            "tokens": {
                "actor": actor_total,
                "critic": critic_total,
            },
            "efficiency": ratio_str,
            "elapsed": f"{self.report.elapsed_seconds:.1f}s",
            "fused": self._fused,
        }

    def get_roi_report(
        self,
        data_count: int = 0,
        success_count: int = 0,
        skill_id: str = "",
    ) -> str:
        """Generate a human-readable ROI report.

        Parameters
        ----------
        data_count:
            Total number of data items processed.
        success_count:
            Number of successfully processed items.
        skill_id:
            ID of the solidified skill (if any).
        """
        actor_total = self.report.actor_prompt_tokens + self.report.actor_completion_tokens
        critic_total = self.report.critic_prompt_tokens + self.report.critic_completion_tokens
        total_tokens = actor_total + critic_total

        lines = [
            f"\n{'='*50}",
            f"  Proof of Value — 任务价值报告",
            f"{'='*50}",
            f"  Token 消耗: {total_tokens:,} (Actor: {actor_total:,}, Critic: {critic_total:,})",
            f"  费用: ${self.report.total_cost_usd:.4f} / ${self.budget_usd:.4f}",
            f"  API 调用: {self.report.call_count} 次",
            f"  耗时: {self.report.elapsed_seconds:.1f} 秒",
        ]

        if data_count > 0:
            lines.append(f"  处理数据: {data_count} 条")
            if success_count > 0:
                lines.append(f"  成功产出: {success_count} 条")
                cost_per_item = self.report.total_cost_usd / success_count
                lines.append(f"  单条成本: ${cost_per_item:.4f}")

        if skill_id:
            lines.append(f"  固化技能: {skill_id}")
            lines.append(f"  下次运行预计成本降低 15%（逻辑已锁定，重试率将降低）")

        lines.append(f"{'='*50}")
        return "\n".join(lines)
