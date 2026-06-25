"""Tests for core.eval_judge — multi-dimensional evaluation layer."""

from __future__ import annotations

import pytest

from core.eval_judge import (
    EvalDimension,
    EvalJudge,
    EvalResult,
    code_quality_evaluator,
    coherence_evaluator,
    completeness_evaluator,
    correctness_evaluator,
    safety_evaluator,
)


# ---------------------------------------------------------------------------
# Built-in evaluators
# ---------------------------------------------------------------------------


class TestSafetyEvaluator:
    """Tests for the rule-based safety evaluator."""

    @pytest.mark.asyncio
    async def test_clean_output_scores_high(self):
        score = await safety_evaluator("input", "This is a safe output.")
        assert score >= 0.8

    @pytest.mark.asyncio
    async def test_script_injection_penalized(self):
        score = await safety_evaluator("input", "<script>alert('xss')</script>")
        assert score < 0.8

    @pytest.mark.asyncio
    async def test_eval_injection_penalized(self):
        score = await safety_evaluator("input", "Use eval() to run code")
        assert score < 0.8

    @pytest.mark.asyncio
    async def test_subprocess_penalized(self):
        score = await safety_evaluator("input", "subprocess.call(['rm', '-rf', '/'])")
        assert score < 0.8

    @pytest.mark.asyncio
    async def test_pii_leak_penalized(self):
        score = await safety_evaluator("input", "SSN: 123-45-6789 and password: secret123")
        assert score < 0.8

    @pytest.mark.asyncio
    async def test_password_leak_penalized(self):
        score = await safety_evaluator("input", "Use <script>eval()</script> with password: secret123")
        assert score < 0.8

    @pytest.mark.asyncio
    async def test_score_clamped_to_zero(self):
        # Multiple violations should not go below 0
        output = "<script>eval(subprocess.os.system('hack auth security'))>"
        score = await safety_evaluator("input", output)
        assert score >= 0.0

    @pytest.mark.asyncio
    async def test_score_clamped_to_one(self):
        score = await safety_evaluator("input", "Perfectly safe content")
        assert score <= 1.0


class TestCodeQualityEvaluator:
    """Tests for the rule-based code quality evaluator."""

    @pytest.mark.asyncio
    async def test_valid_python_scores_high(self):
        code = "def hello():\n    return 'world'"
        score = await code_quality_evaluator("input", code)
        assert score >= 0.7

    @pytest.mark.asyncio
    async def test_syntax_error_penalized(self):
        code = "def hello(\n    return 'world'"
        score = await code_quality_evaluator("input", code)
        assert score < 0.7

    @pytest.mark.asyncio
    async def test_bare_except_penalized(self):
        code = "try:\n    pass\nexcept:\n    pass"
        score = await code_quality_evaluator("input", code)
        assert score < 1.0

    @pytest.mark.asyncio
    async def test_wildcard_import_penalized(self):
        code = "from os import *\nprint('hello')"
        score = await code_quality_evaluator("input", code)
        assert score < 1.0

    @pytest.mark.asyncio
    async def test_global_statement_penalized(self):
        code = "global x\nx = 10"
        score = await code_quality_evaluator("input", code)
        assert score < 1.0


class TestCompletenessEvaluator:
    """Tests for the rule-based completeness evaluator."""

    @pytest.mark.asyncio
    async def test_empty_output_scores_zero(self):
        score = await completeness_evaluator("input data", "")
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_keyword_coverage_improves_score(self):
        input_data = "Write a function to calculate fibonacci numbers"
        output = "Here is a function to calculate fibonacci numbers: def fib(n): ..."
        score = await completeness_evaluator(input_data, output)
        assert score > 0.3

    @pytest.mark.asyncio
    async def test_short_output_penalized(self):
        input_data = "Write a comprehensive analysis of the market trends"
        output = "ok"
        score = await completeness_evaluator(input_data, output)
        assert score < 0.5


class TestCoherenceEvaluator:
    """Tests for the rule-based coherence evaluator."""

    @pytest.mark.asyncio
    async def test_empty_output_scores_zero(self):
        score = await coherence_evaluator("input", "")
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_very_short_output_low_score(self):
        score = await coherence_evaluator("input", "hi")
        assert score <= 0.3

    @pytest.mark.asyncio
    async def test_coherent_text_scores_high(self):
        text = (
            "This is a well-structured paragraph with clear sentences. "
            "It flows logically from one idea to the next. "
            "The coherence is maintained throughout the text."
        )
        score = await coherence_evaluator("input", text)
        assert score >= 0.7

    @pytest.mark.asyncio
    async def test_repetitive_text_penalized(self):
        lines = ["Same line repeated."] * 10
        score = await coherence_evaluator("input", "\n".join(lines))
        assert score < 0.8


# ---------------------------------------------------------------------------
# EvalDimension
# ---------------------------------------------------------------------------


class TestEvalDimension:
    """Tests for EvalDimension dataclass."""

    def test_default_values(self):
        dim = EvalDimension(name="test")
        assert dim.name == "test"
        assert dim.weight == 1.0
        assert dim.evaluator is None
        assert dim.description == ""

    def test_custom_values(self):
        dim = EvalDimension(
            name="custom",
            weight=0.5,
            evaluator=safety_evaluator,
            description="Custom dimension",
        )
        assert dim.name == "custom"
        assert dim.weight == 0.5
        assert dim.evaluator is safety_evaluator
        assert dim.description == "Custom dimension"


# ---------------------------------------------------------------------------
# EvalJudge
# ---------------------------------------------------------------------------


class TestEvalJudge:
    """Tests for the EvalJudge class."""

    def test_init_defaults(self):
        judge = EvalJudge()
        assert judge.dimensions == []
        assert judge.threshold == 0.7

    def test_init_with_dimensions(self):
        dims = [
            EvalDimension("safety", 0.5, safety_evaluator),
            EvalDimension("coherence", 0.5, coherence_evaluator),
        ]
        judge = EvalJudge(dimensions=dims, threshold=0.6)
        assert len(judge.dimensions) == 2
        assert judge.threshold == 0.6

    def test_register_dimension(self):
        judge = EvalJudge()
        dim = EvalDimension("safety", 1.0, safety_evaluator)
        judge.register_dimension(dim)
        assert len(judge.dimensions) == 1
        assert judge.dimensions[0].name == "safety"

    def test_register_duplicate_raises(self):
        judge = EvalJudge(dimensions=[EvalDimension("safety", 1.0, safety_evaluator)])
        with pytest.raises(ValueError, match="已存在"):
            judge.register_dimension(EvalDimension("safety", 0.5))

    def test_remove_dimension(self):
        judge = EvalJudge(dimensions=[EvalDimension("safety", 1.0, safety_evaluator)])
        assert judge.remove_dimension("safety") is True
        assert len(judge.dimensions) == 0

    def test_remove_nonexistent_returns_false(self):
        judge = EvalJudge()
        assert judge.remove_dimension("nonexistent") is False

    @pytest.mark.asyncio
    async def test_evaluate_empty_dimensions_fails(self):
        judge = EvalJudge()
        result = await judge.evaluate("input", "output")
        assert result.passed is False
        assert result.weighted_score == 0.0

    @pytest.mark.asyncio
    async def test_evaluate_with_safety_dimension(self):
        judge = EvalJudge(
            dimensions=[EvalDimension("safety", 1.0, safety_evaluator)],
            threshold=0.5,
        )
        result = await judge.evaluate("input", "Safe output text")
        assert isinstance(result, EvalResult)
        assert "safety" in result.scores
        assert result.scores["safety"] >= 0.8

    @pytest.mark.asyncio
    async def test_evaluate_with_multiple_dimensions(self):
        judge = EvalJudge(
            dimensions=[
                EvalDimension("safety", 1.0, safety_evaluator),
                EvalDimension("coherence", 1.0, coherence_evaluator),
                EvalDimension("completeness", 1.0, completeness_evaluator),
            ],
            threshold=0.5,
        )
        result = await judge.evaluate(
            "Write a safe and coherent analysis",
            "This is a safe, coherent, and complete analysis of the topic.",
        )
        assert len(result.scores) == 3
        assert "safety" in result.scores
        assert "coherence" in result.scores
        assert "completeness" in result.scores
        assert result.weighted_score > 0.0

    @pytest.mark.asyncio
    async def test_evaluate_passes_when_above_threshold(self):
        judge = EvalJudge(
            dimensions=[EvalDimension("safety", 1.0, safety_evaluator)],
            threshold=0.5,
        )
        result = await judge.evaluate("input", "Safe content here")
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_evaluate_fails_when_below_threshold(self):
        judge = EvalJudge(
            dimensions=[EvalDimension("safety", 1.0, safety_evaluator)],
            threshold=0.99,
        )
        result = await judge.evaluate(
            "input", "Use <script>eval()</script> and password: secret123"
        )
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_evaluate_result_has_summary(self):
        judge = EvalJudge(
            dimensions=[EvalDimension("safety", 1.0, safety_evaluator)],
        )
        result = await judge.evaluate("input", "output")
        assert result.summary
        assert "safety" in result.summary

    @pytest.mark.asyncio
    async def test_evaluate_with_no_evaluator(self):
        judge = EvalJudge(
            dimensions=[EvalDimension("empty", 1.0, None)],
        )
        result = await judge.evaluate("input", "output")
        assert "empty" in result.scores
        assert result.scores["empty"] == 0.5

    def test_get_dimension_report(self):
        judge = EvalJudge(
            dimensions=[
                EvalDimension("safety", 0.5, safety_evaluator),
                EvalDimension("coherence", 0.5, coherence_evaluator),
            ],
        )
        report = judge.get_dimension_report({"safety": 0.9, "coherence": 0.7})
        assert "safety" in report
        assert "coherence" in report
        assert "█" in report  # bar chart

    def test_get_dimension_report_empty(self):
        judge = EvalJudge()
        report = judge.get_dimension_report({})
        assert "无评估数据" in report


# ---------------------------------------------------------------------------
# EvalResult
# ---------------------------------------------------------------------------


class TestEvalResult:
    """Tests for EvalResult dataclass."""

    def test_default_values(self):
        result = EvalResult()
        assert result.passed is False
        assert result.weighted_score == 0.0
        assert result.scores == {}
        assert result.critiques == {}
        assert result.summary == ""
        assert result.threshold == 0.7

    def test_custom_values(self):
        result = EvalResult(
            passed=True,
            weighted_score=0.85,
            scores={"safety": 0.9, "coherence": 0.8},
            critiques={"safety": "优秀"},
            summary="Good output",
            threshold=0.7,
        )
        assert result.passed is True
        assert result.weighted_score == 0.85
        assert len(result.scores) == 2


# ---------------------------------------------------------------------------
# Correctness evaluator (LLM-based with fallback)
# ---------------------------------------------------------------------------


class TestCorrectnessEvaluator:
    """Tests for the correctness evaluator."""

    @pytest.mark.asyncio
    async def test_fallback_short_output(self):
        score = await correctness_evaluator("input", "hi")
        assert score <= 0.2

    @pytest.mark.asyncio
    async def test_fallback_medium_output(self):
        score = await correctness_evaluator("input", "x" * 40)
        assert score <= 0.5

    @pytest.mark.asyncio
    async def test_fallback_long_output(self):
        score = await correctness_evaluator("input", "x" * 100)
        assert score >= 0.5

    @pytest.mark.asyncio
    async def test_with_mock_llm_provider(self):
        from unittest.mock import AsyncMock, MagicMock

        mock_provider = MagicMock()
        mock_response = MagicMock()
        mock_response.content = '{"score": 0.85, "critique": "Good"}'
        mock_provider.request = AsyncMock(return_value=mock_response)

        score = await correctness_evaluator(
            "input data", "output data", llm_provider=mock_provider
        )
        assert score == 0.85

    @pytest.mark.asyncio
    async def test_llm_failure_returns_neutral(self):
        from unittest.mock import AsyncMock, MagicMock

        mock_provider = MagicMock()
        mock_provider.request = AsyncMock(side_effect=Exception("API error"))

        score = await correctness_evaluator(
            "input", "output", llm_provider=mock_provider
        )
        assert score == 0.5
