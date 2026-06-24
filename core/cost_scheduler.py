"""
Cost Scheduler — token arbitrage and cost-aware execution routing.

Routes tasks to the most cost-effective execution path:
- HIGH priority → immediate sync execution
- NORMAL priority → standard queue
- LOW priority → Batch API (50% cost savings)

Monitors pricing and selects the cheapest provider when multiple
are available.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.task_queue import Priority, TaskQueue
from gateway.batch_provider import BatchProvider

__all__ = ["CostScheduler", "ExecutionPlan"]


class ExecutionPlan:
    """Decision on how to execute a task based on cost analysis."""

    def __init__(
        self,
        strategy: str,  # "sync" | "queue" | "batch"
        provider_model: str,
        estimated_cost_usd: float,
        reason: str,
    ) -> None:
        self.strategy = strategy
        self.provider_model = provider_model
        self.estimated_cost_usd = estimated_cost_usd
        self.reason = reason

    def __repr__(self) -> str:
        return (
            f"ExecutionPlan(strategy={self.strategy}, "
            f"model={self.provider_model}, "
            f"cost=${self.estimated_cost_usd:.4f})"
        )


class CostScheduler:
    """Route tasks to the most cost-effective execution path.

    Parameters
    ----------
    pricing_map:
        Per-model pricing (per 1000 tokens).
    batch_provider:
        Optional Batch API provider for LOW priority tasks.
    budget_remaining:
        Remaining budget in USD.
    """

    def __init__(
        self,
        pricing_map: Optional[Dict[str, Dict[str, float]]] = None,
        batch_provider: Optional[BatchProvider] = None,
        budget_remaining: float = 100.0,
    ) -> None:
        self._pricing = pricing_map or {}
        self._batch_provider = batch_provider
        self._budget_remaining = budget_remaining
        self._task_queue = TaskQueue(max_concurrent=5)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def plan_execution(
        self,
        priority: Priority,
        estimated_tokens: int = 1000,
        available_models: Optional[List[str]] = None,
    ) -> ExecutionPlan:
        """Decide the best execution strategy for a task.

        Parameters
        ----------
        priority:
            Task priority level.
        estimated_tokens:
            Estimated total tokens (prompt + completion).
        available_models:
            List of model names to consider.

        Returns
        -------
        ExecutionPlan
            The recommended execution strategy.
        """
        models = available_models or list(self._pricing.keys())

        if priority == Priority.HIGH:
            # HIGH: immediate sync execution with best available model
            model = self._cheapest_model(models)
            cost = self._estimate_cost(model, estimated_tokens)
            return ExecutionPlan(
                strategy="sync",
                provider_model=model,
                estimated_cost_usd=cost,
                reason="HIGH priority — immediate execution",
            )

        if priority == Priority.LOW and self._batch_provider:
            # LOW: route to Batch API for 50% savings
            model = self._cheapest_model(models)
            cost = self._estimate_cost(model, estimated_tokens) * 0.5
            return ExecutionPlan(
                strategy="batch",
                provider_model=model,
                estimated_cost_usd=cost,
                reason="LOW priority — Batch API (50% savings)",
            )

        # NORMAL or no Batch API: standard queue
        model = self._cheapest_model(models)
        cost = self._estimate_cost(model, estimated_tokens)
        return ExecutionPlan(
            strategy="queue",
            provider_model=model,
            estimated_cost_usd=cost,
            reason="NORMAL priority — standard queue",
        )

    def select_cheapest_provider(
        self,
        providers: Dict[str, Any],
        estimated_tokens: int = 1000,
    ) -> tuple[str, Any]:
        """Select the provider with the lowest estimated cost.

        Returns (model_name, provider).
        """
        if not providers:
            raise ValueError("No providers available")

        best_model = None
        best_provider = None
        best_cost = float("inf")

        for model, provider in providers.items():
            cost = self._estimate_cost(model, estimated_tokens)
            if cost < best_cost:
                best_cost = cost
                best_model = model
                best_provider = provider

        return best_model, best_provider

    def update_budget(self, remaining: float) -> None:
        """Update the remaining budget."""
        self._budget_remaining = remaining

    def get_stats(self) -> Dict[str, Any]:
        """Return scheduler statistics."""
        return {
            "budget_remaining": self._budget_remaining,
            "queue_stats": self._task_queue.get_stats(),
            "batch_available": self._batch_provider is not None,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _cheapest_model(self, models: List[str]) -> str:
        """Find the model with the lowest completion cost."""
        if not models:
            return "gpt-4o-mini"  # safe default

        best = models[0]
        best_cost = float("inf")
        for m in models:
            pricing = self._pricing.get(m, {"prompt": 0.01, "completion": 0.03})
            cost = pricing.get("completion", 0.03)
            if cost < best_cost:
                best_cost = cost
                best = m
        return best

    def _estimate_cost(self, model: str, tokens: int) -> float:
        """Estimate cost for a given model and token count."""
        pricing = self._pricing.get(model, {"prompt": 0.01, "completion": 0.03})
        # Assume 70% prompt, 30% completion
        prompt_tokens = int(tokens * 0.7)
        completion_tokens = int(tokens * 0.3)
        return (prompt_tokens / 1000 * pricing.get("prompt", 0.01)) + (
            completion_tokens / 1000 * pricing.get("completion", 0.03)
        )
