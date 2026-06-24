"""
Sampling Manager — controls the 1% sampling gate and human approval.

After the sampling phase completes, the manager generates a preview
report with ROI estimation and blocks execution until the user
explicitly approves.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

__all__ = ["SamplingManager"]


class SamplingManager:
    """Manage the sampling-to-approval transition."""

    def __init__(self) -> None:
        self.is_approved: bool = False

    async def generate_report(
        self,
        sampled_results: List[Dict[str, Any]],
        total_data_count: int = 0,
        sampling_ratio: float = 0.01,
        current_cost_usd: float = 0.0,
    ) -> Dict[str, Any]:
        """Build a sampling decision report with ROI estimation.

        Parameters
        ----------
        sampled_results:
            Results from the sampling phase.
        total_data_count:
            Total number of items in the full dataset.
        sampling_ratio:
            The ratio of sampled items to total.
        current_cost_usd:
            USD already spent during sampling.
        """
        successful = [r for r in sampled_results if r.get("status") == "success"]
        total = len(sampled_results)
        avg_score = 0.0
        avg_retries = 0.0
        if successful:
            scores = []
            retries = []
            for r in successful:
                history = r.get("history", [])
                if history:
                    scores.append(history[-1].get("score", 0.0))
                    retries.append(len(history))
            avg_score = sum(scores) / len(scores) if scores else 0.0
            avg_retries = sum(retries) / len(retries) if retries else 0.0

        # ROI estimation
        success_rate = len(successful) / total if total > 0 else 0.0
        cost_per_sample = current_cost_usd / total if total > 0 else 0.0
        estimated_total = (
            cost_per_sample * total_data_count if total_data_count > 0 else 0.0
        )
        estimated_success = int(total_data_count * success_rate)
        cost_per_success = (
            estimated_total / estimated_success if estimated_success > 0 else 0.0
        )

        return {
            "type": "SAMPLING_REPORT",
            "summary": {
                "sample_count": total,
                "success_count": len(successful),
                "success_rate": round(success_rate, 4),
                "average_quality_score": round(avg_score, 2),
                "average_retries": round(avg_retries, 2),
                "status": "AWAITING_APPROVAL",
            },
            "economics": {
                "sampling_cost_usd": round(current_cost_usd, 4),
                "cost_per_sample": round(cost_per_sample, 6),
                "cost_per_success": round(cost_per_success, 6),
                "estimated_total_cost_usd": round(estimated_total, 4),
                "estimated_success_count": estimated_success,
                "total_data_count": total_data_count,
            },
            "preview": successful[:3],
            "actions": ["APPROVE_FULL_RUN", "REVISE_PROMPT", "ABORT"],
        }

    async def wait_for_approval(self, poll_interval: float = 1.0) -> None:
        """Block until the user approves the sampling results."""
        print("⏸️ [采样闸门] 任务已暂停。等待用户确认采样结果...")
        while not self.is_approved:
            await asyncio.sleep(poll_interval)
        print("▶️ [采样闸门] 用户已批准。开启全量生产模式。")

    def approve(self) -> None:
        """Signal that the user has approved the sampling results."""
        self.is_approved = True
