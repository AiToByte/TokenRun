"""
Task Critic — cheap-model quality auditor with structured output.

The Critic is the "eyes" of the loop: it evaluates the Actor's output
against the exit criteria defined in the Runfile and returns a
structured ``EvaluationResult`` with multi-dimensional scores.
"""

from __future__ import annotations

import json
from typing import Dict, List

from core.models import EvaluationResult, ValidationRule
from gateway.provider import LLMProvider

__all__ = ["TaskCritic"]


class TaskCritic:
    """Evaluate Actor output using a cheap LLM with structured JSON output.

    Parameters
    ----------
    provider:
        An :class:`LLMProvider` configured for the cheap model
        (e.g. GPT-4o-mini, Claude 3 Haiku).
    """

    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    async def evaluate(
        self,
        task_name: str,
        input_data: str,
        output_content: str,
        rules: List[ValidationRule],
    ) -> EvaluationResult:
        """Audit *output_content* against *rules*.

        Parameters
        ----------
        task_name:
            Human-readable task name for context.
        input_data:
            The original input that was given to the Actor.
        output_content:
            The Actor's generated output to evaluate.
        rules:
            Exit criteria from the Runfile.

        Returns
        -------
        EvaluationResult
            Contains ``passed``, ``score``, ``scores`` (per-dimension),
            ``critique``, and ``suggestions``.
        """
        rules_desc = "\n".join(
            f"- [{r.type}] 权重 {r.weight}: {r.criteria}" for r in rules
        )

        system_prompt = (
            "你是一个专业的工业级质量审计员。\n"
            "你的任务是根据用户提供的规则，对 AI 生成的内容进行审查。\n"
            "你必须以 JSON 格式输出，包含以下字段：\n"
            "- passed (boolean): 是否通过所有核心规则\n"
            "- score (float): 综合评分 (0.0 - 1.0)\n"
            "- scores (object): 各维度评分，键为维度名，值为 0.0-1.0\n"
            '  例如: {"accuracy": 0.9, "completeness": 0.8, "format": 1.0}\n'
            "- critique (string): 如果不合格，请详细说明原因；如果合格，请留空\n"
            "- suggestions (list of strings): 具体的改进建议"
        )

        user_content = (
            f"请评估以下任务产出：\n"
            f"【任务名称】：{task_name}\n"
            f"【原始输入】：{input_data}\n"
            f"【待评估产出】：{output_content}\n\n"
            f"【必须遵循的审计规则】：\n{rules_desc}\n\n"
            f"请基于上述标准给出你的审计报告。"
        )

        response = await self.provider.request(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
        )

        # Always record the audit cost, even on parse failure.
        total_audit_tokens = response.prompt_tokens + response.completion_tokens

        try:
            report = json.loads(response.content)
            score = max(0.0, min(1.0, float(report.get("score", 0.0))))

            # Parse multi-dimensional scores
            raw_scores = report.get("scores", {})
            scores: Dict[str, float] = {}
            if isinstance(raw_scores, dict):
                for k, v in raw_scores.items():
                    scores[str(k)] = max(0.0, min(1.0, float(v)))

            return EvaluationResult(
                passed=bool(report.get("passed", False)),
                score=score,
                scores=scores,
                critique=report.get("critique") or None,
                suggestions=report.get("suggestions", []),
                audit_cost=total_audit_tokens,
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return EvaluationResult(
                passed=False,
                score=0.0,
                scores={},
                critique="审计报告格式异常，强制重新循环",
                suggestions=[],
                audit_cost=total_audit_tokens,
            )
