"""
EvalJudge — multi-dimensional evaluation layer for TokenRun.

Provides structured, multi-dimensional evaluation of LLM outputs with
pluggable evaluators (rule-based, LLM-based, hybrid).  Each dimension
produces a 0.0–1.0 score, and results are aggregated with configurable
weights.

Usage::

    from core.eval_judge import EvalJudge, EvalDimension

    judge = EvalJudge(dimensions=[
        EvalDimension("correctness", weight=0.4, evaluator=llm_correctness),
        EvalDimension("completeness", weight=0.3, evaluator=llm_completeness),
        EvalDimension("coherence", weight=0.2, evaluator=llm_coherence),
        EvalDimension("safety", weight=0.1, evaluator=rule_safety),
    ])
    result = await judge.evaluate(input_data="...", output="...")
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol

__all__ = [
    "EvalDimension",
    "EvalResult",
    "EvalJudge",
    "EvaluatorFunc",
    "correctness_evaluator",
    "completeness_evaluator",
    "coherence_evaluator",
    "safety_evaluator",
    "code_quality_evaluator",
]


# ---------------------------------------------------------------------------
# Protocols & Dataclasses
# ---------------------------------------------------------------------------


class EvaluatorFunc(Protocol):
    """Protocol for evaluator callables.

    An evaluator takes (input_data, output) and returns a float in [0.0, 1.0].
    """

    async def __call__(self, input_data: str, output: str) -> float: ...


@dataclass
class EvalDimension:
    """A single evaluation dimension.

    Parameters
    ----------
    name:
        Dimension identifier (e.g., "correctness", "safety").
    weight:
        Relative weight for aggregation (default 1.0).
    evaluator:
        Async callable that scores the output in [0.0, 1.0].
    description:
        Human-readable description of what this dimension measures.
    """

    name: str
    weight: float = 1.0
    evaluator: Optional[Callable] = None
    description: str = ""


@dataclass
class EvalResult:
    """Result of a multi-dimensional evaluation.

    Attributes
    ----------
    passed:
        True if weighted_score >= threshold.
    weighted_score:
        Weighted average across all dimensions.
    scores:
        Per-dimension scores (name -> float).
    critiques:
        Per-dimension textual feedback (name -> str).
    summary:
        Aggregated summary text.
    threshold:
        Pass/fail threshold used.
    """

    passed: bool = False
    weighted_score: float = 0.0
    scores: Dict[str, float] = field(default_factory=dict)
    critiques: Dict[str, str] = field(default_factory=dict)
    summary: str = ""
    threshold: float = 0.7


# ---------------------------------------------------------------------------
# Built-in evaluators (rule-based, no LLM)
# ---------------------------------------------------------------------------


async def safety_evaluator(input_data: str, output: str) -> float:
    """Rule-based safety check.

    Detects common safety issues: injection patterns, dangerous code,
    PII leakage, and harmful content markers.
    """
    score = 1.0

    # Check for code injection patterns
    injection_patterns = [
        r"<script[^>]*>",
        r"__import__\s*\(",
        r"eval\s*\(",
        r"exec\s*\(",
        r"subprocess\.",
        r"os\.system\s*\(",
    ]
    for pattern in injection_patterns:
        if re.search(pattern, output, re.IGNORECASE):
            score -= 0.3

    # Check for potential PII leakage
    pii_patterns = [
        r"\b\d{3}-\d{2}-\d{4}\b",  # SSN
        r"\b\d{16}\b",  # Credit card
        r"password\s*[:=]\s*\S+",  # Passwords
    ]
    for pattern in pii_patterns:
        if re.search(pattern, output, re.IGNORECASE):
            score -= 0.2

    # Check for harmful content markers
    harmful_patterns = [
        r"\b(hack|exploit|bypass)\s+(auth|security|login)\b",
        r"\b(drop\s+table|delete\s+from|truncate)\b",
    ]
    for pattern in harmful_patterns:
        if re.search(pattern, output, re.IGNORECASE):
            score -= 0.2

    return max(0.0, min(1.0, score))


async def code_quality_evaluator(input_data: str, output: str) -> float:
    """Rule-based code quality check for code outputs.

    Checks for: syntax errors, long functions, deep nesting,
    missing error handling, and common anti-patterns.
    """
    score = 1.0
    lines = output.strip().split("\n")

    # Check for extremely long output (likely unstructured)
    if len(lines) > 500:
        score -= 0.1

    # Check for syntax issues in Python code
    if any(keyword in output for keyword in ["def ", "class ", "import "]):
        try:
            compile(output, "<eval>", "exec")
        except SyntaxError:
            score -= 0.4

    # Check for deep nesting (more than 4 levels of indentation)
    deep_lines = sum(1 for line in lines if len(line) - len(line.lstrip()) > 16)
    if deep_lines > len(lines) * 0.1:
        score -= 0.1

    # Check for missing error handling
    has_try = "try:" in output
    has_except = "except" in output
    if has_try and not has_except:
        score -= 0.2

    # Check for common anti-patterns
    anti_patterns = [
        (r"except\s*:", -0.15),  # Bare except
        (r"import\s+\*", -0.1),  # Wildcard import
        (r"global\s+", -0.1),  # Global statement
    ]
    for pattern, penalty in anti_patterns:
        if re.search(pattern, output):
            score += penalty

    return max(0.0, min(1.0, score))


async def completeness_evaluator(input_data: str, output: str) -> float:
    """Rule-based completeness check.

    Evaluates whether the output addresses the input requirements
    based on keyword coverage and length ratio.
    """
    if not input_data.strip() or not output.strip():
        return 0.0

    # Extract key terms from input (words > 3 chars, not stopwords)
    stopwords = {
        "the",
        "and",
        "for",
        "that",
        "this",
        "with",
        "from",
        "are",
        "was",
        "were",
        "been",
        "have",
        "has",
        "had",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "can",
        "shall",
        "not",
        "but",
        "also",
        "just",
        "only",
        "very",
        "really",
        "quite",
        "rather",
        "some",
        "any",
        "all",
        "each",
        "every",
        "both",
        "few",
        "more",
        "most",
        "other",
        "another",
        "such",
        "than",
        "too",
        "very",
        "已经",
        "可以",
        "需要",
        "进行",
        "使用",
        "通过",
        "一个",
        "这个",
        "那个",
        "以及",
    }
    input_words = {
        w.lower()
        for w in re.findall(r"\b\w{4,}\b", input_data)
        if w.lower() not in stopwords
    }
    output_words = {w.lower() for w in re.findall(r"\b\w{4,}\b", output)}

    if not input_words:
        return 0.5

    # Coverage: what fraction of input keywords appear in output
    covered = input_words & output_words
    coverage = len(covered) / len(input_words)

    # Length ratio: penalize if output is too short or too long
    input_len = len(input_data)
    output_len = len(output)
    if input_len > 0:
        ratio = output_len / input_len
        if ratio < 0.3:
            length_score = 0.3
        elif ratio < 0.8:
            length_score = 0.7
        elif ratio > 10.0:
            length_score = 0.8
        else:
            length_score = 1.0
    else:
        length_score = 0.5

    return 0.6 * coverage + 0.4 * length_score


async def coherence_evaluator(input_data: str, output: str) -> float:
    """Rule-based coherence check.

    Evaluates logical flow: paragraph structure, sentence variety,
    and absence of contradictions.
    """
    score = 1.0
    lines = [line.strip() for line in output.split("\n") if line.strip()]

    if not lines:
        return 0.0

    # Check for very short output (likely incoherent)
    if len(output.strip()) < 20:
        return 0.2

    # Check for repeated lines (incoherence signal)
    unique_lines = set(lines)
    if len(lines) > 5:
        repetition_rate = 1.0 - (len(unique_lines) / len(lines))
        if repetition_rate > 0.3:
            score -= 0.3

    # Check for sentence length variety (good coherence has variety)
    sentence_lengths = [len(line.split()) for line in lines if len(line) > 5]
    if sentence_lengths:
        avg_len = sum(sentence_lengths) / len(sentence_lengths)
        if avg_len < 3:  # Too short
            score -= 0.2
        elif avg_len > 50:  # Too long
            score -= 0.1

    # Check for contradiction markers
    contradiction_patterns = [
        r"\bhowever\b.*\btherefore\b",
        r"\bbut\b.*\balso\b.*\bbut\b",
    ]
    for pattern in contradiction_patterns:
        if re.search(pattern, output, re.IGNORECASE | re.DOTALL):
            score -= 0.1

    return max(0.0, min(1.0, score))


# ---------------------------------------------------------------------------
# LLM-based evaluators (require provider)
# ---------------------------------------------------------------------------


async def correctness_evaluator(
    input_data: str, output: str, llm_provider: Any = None
) -> float:
    """LLM-based correctness evaluation.

    If no provider is available, falls back to a simple heuristic.
    """
    if llm_provider is None:
        # Fallback: check if output is non-empty and has substance
        if len(output.strip()) < 10:
            return 0.1
        if len(output.strip()) < 50:
            return 0.4
        return 0.6

    try:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are an evaluation judge. Rate the correctness of the "
                    "output on a scale of 0.0 to 1.0. Respond with ONLY a JSON "
                    'object: {"score": float, "critique": str}'
                ),
            },
            {
                "role": "user",
                "content": f"Input: {input_data[:500]}\n\nOutput: {output[:500]}",
            },
        ]
        response = await llm_provider.request(
            messages=messages, temperature=0.0, response_format={"type": "json_object"}
        )
        result = json.loads(response.content)
        return max(0.0, min(1.0, float(result.get("score", 0.5))))
    except Exception:
        return 0.5  # Neutral on failure


# ---------------------------------------------------------------------------
# EvalJudge
# ---------------------------------------------------------------------------


class EvalJudge:
    """Multi-dimensional evaluator for LLM outputs.

    Parameters
    ----------
    dimensions:
        List of evaluation dimensions with weights and evaluators.
    threshold:
        Weighted score threshold for passing (default 0.7).
    llm_provider:
        Optional LLM provider for LLM-based evaluators.

    Examples
    --------
    >>> judge = EvalJudge(dimensions=[
    ...     EvalDimension("safety", 0.3, safety_evaluator),
    ...     EvalDimension("completeness", 0.7, completeness_evaluator),
    ... ])
    >>> result = await judge.evaluate("input text", "output text")
    >>> result.passed, result.weighted_score
    (True, 0.85)
    """

    def __init__(
        self,
        dimensions: Optional[List[EvalDimension]] = None,
        threshold: float = 0.7,
        llm_provider: Any = None,
    ) -> None:
        self._dimensions: List[EvalDimension] = dimensions or []
        self.threshold = threshold
        self._llm_provider = llm_provider
        # Cache: which evaluators accept llm_provider parameter
        self._evaluator_accepts_provider: Dict[int, bool] = {}
        self._inspect_evaluators()

    @property
    def dimensions(self) -> List[EvalDimension]:
        """Registered evaluation dimensions."""
        return list(self._dimensions)

    def register_dimension(self, dimension: EvalDimension) -> None:
        """Add a new evaluation dimension.

        Parameters
        ----------
        dimension:
            The dimension to register.

        Raises
        ------
        ValueError
            If a dimension with the same name already exists.
        """
        for existing in self._dimensions:
            if existing.name == dimension.name:
                raise ValueError(f"维度 '{dimension.name}' 已存在，请使用不同的名称。")
        self._dimensions.append(dimension)
        # Cache evaluator signature
        if dimension.evaluator:
            self._inspect_single_evaluator(dimension.evaluator)

    def remove_dimension(self, name: str) -> bool:
        """Remove a dimension by name.

        Returns True if removed, False if not found.
        """
        for i, dim in enumerate(self._dimensions):
            if dim.name == name:
                self._dimensions.pop(i)
                return True
        return False

    def _inspect_evaluators(self) -> None:
        """Cache signature info for all registered evaluators."""
        import inspect

        for dim in self._dimensions:
            if dim.evaluator:
                try:
                    sig = inspect.signature(dim.evaluator)
                    self._evaluator_accepts_provider[id(dim.evaluator)] = (
                        "llm_provider" in sig.parameters
                    )
                except (ValueError, TypeError):
                    self._evaluator_accepts_provider[id(dim.evaluator)] = False

    def _inspect_single_evaluator(self, evaluator: Callable) -> None:
        """Cache signature info for a single evaluator."""
        import inspect

        try:
            sig = inspect.signature(evaluator)
            self._evaluator_accepts_provider[id(evaluator)] = (
                "llm_provider" in sig.parameters
            )
        except (ValueError, TypeError):
            self._evaluator_accepts_provider[id(evaluator)] = False

    async def evaluate(
        self,
        input_data: str,
        output: str,
        extra_context: Optional[Dict[str, Any]] = None,
    ) -> EvalResult:
        """Run all dimensions and aggregate scores.

        Parameters
        ----------
        input_data:
            The original input/prompt.
        output:
            The LLM output to evaluate.
        extra_context:
            Additional context passed to evaluators (unused by built-ins).

        Returns
        -------
        EvalResult
            Aggregated evaluation result with per-dimension scores.
        """
        if not self._dimensions:
            return EvalResult(
                passed=False,
                weighted_score=0.0,
                scores={},
                critiques={},
                summary="无评估维度，无法评估质量。",
                threshold=self.threshold,
            )

        # Run all evaluators concurrently
        tasks = []
        for dim in self._dimensions:
            if dim.evaluator:
                tasks.append(self._run_dimension(dim, input_data, output))
            else:
                tasks.append(self._null_dimension(dim))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect scores and critiques
        scores: Dict[str, float] = {}
        critiques: Dict[str, str] = {}
        for i, result in enumerate(results):
            dim = self._dimensions[i]
            if isinstance(result, Exception):
                scores[dim.name] = 0.0
                critiques[dim.name] = f"评估异常: {result}"
            elif isinstance(result, tuple):
                score, critique = result
                scores[dim.name] = score
                if critique:
                    critiques[dim.name] = critique
            else:
                scores[dim.name] = float(result) if result is not None else 0.0

        # Compute weighted score
        weighted = self._compute_weighted_score(scores)

        # Build summary
        summary = self._build_summary(scores, critiques, weighted)

        return EvalResult(
            passed=weighted >= self.threshold,
            weighted_score=round(weighted, 4),
            scores=scores,
            critiques=critiques,
            summary=summary,
            threshold=self.threshold,
        )

    async def _run_dimension(
        self, dim: EvalDimension, input_data: str, output: str
    ) -> tuple[float, str]:
        """Run a single dimension evaluator."""
        try:
            evaluator = dim.evaluator
            if evaluator is None:
                return 0.0, ""

            # Use cached signature info
            accepts_provider = self._evaluator_accepts_provider.get(
                id(evaluator), False
            )
            if accepts_provider:
                score = await evaluator(
                    input_data, output, llm_provider=self._llm_provider
                )
            else:
                score = await evaluator(input_data, output)

            score = max(0.0, min(1.0, float(score)))
            critique = self._score_to_critique(dim.name, score)
            return score, critique
        except Exception as exc:
            return 0.0, f"评估失败: {exc}"

    @staticmethod
    async def _null_dimension(dim: EvalDimension) -> tuple[float, str]:
        """Handle dimension with no evaluator."""
        return 0.5, f"维度 '{dim.name}' 未配置评估器"

    def _compute_weighted_score(self, scores: Dict[str, float]) -> float:
        """Compute weighted average of dimension scores.

        Uses each dimension's ``weight`` attribute.  Falls back to simple
        average if no weights are configured.
        """
        if not scores:
            return 0.0

        total_weight = 0.0
        weighted_sum = 0.0
        for dim in self._dimensions:
            score = scores.get(dim.name, 0.0)
            weighted_sum += score * dim.weight
            total_weight += dim.weight

        if total_weight > 0:
            return weighted_sum / total_weight

        # Fallback: simple average
        return sum(scores.values()) / len(scores)

    @staticmethod
    def _score_to_critique(name: str, score: float) -> str:
        """Generate a textual critique from a score."""
        if score >= 0.9:
            return f"{name}: 优秀"
        elif score >= 0.7:
            return f"{name}: 良好"
        elif score >= 0.5:
            return f"{name}: 一般"
        elif score >= 0.3:
            return f"{name}: 较差"
        else:
            return f"{name}: 不合格"

    @staticmethod
    def _build_summary(
        scores: Dict[str, float],
        critiques: Dict[str, str],
        weighted: float,
    ) -> str:
        """Build a human-readable summary."""
        lines = [f"综合评分: {weighted:.2f}"]
        for name, score in scores.items():
            critique = critiques.get(name, "")
            lines.append(f"  {name}: {score:.2f} — {critique}")
        return "\n".join(lines)

    def get_dimension_report(self, scores: Dict[str, float]) -> str:
        """Generate a detailed dimension report.

        Parameters
        ----------
        scores:
            Per-dimension scores from an EvalResult.

        Returns
        -------
        str
            Formatted report.
        """
        if not scores:
            return "无评估数据。"

        lines = ["评估维度报告", "=" * 40]
        for dim in self._dimensions:
            score = scores.get(dim.name, 0.0)
            bar = "█" * int(score * 20) + "░" * (20 - int(score * 20))
            lines.append(
                f"  {dim.name:15s} [{bar}] {score:.2f} (权重: {dim.weight:.1f})"
            )
        lines.append("=" * 40)
        weighted = self._compute_weighted_score(scores)
        lines.append(f"  综合: {weighted:.2f} (阈值: {self.threshold:.2f})")
        lines.append(
            f"  结果: {'通过 ✓' if weighted >= self.threshold else '未通过 ✗'}"
        )
        return "\n".join(lines)
