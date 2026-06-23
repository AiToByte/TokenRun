"""Tests for core.ledger — token budgeting and circuit breaker."""

import pytest

from core.ledger import BudgetExceededError, TokenLedger, TokenRunError, UsageReport


class TestTokenLedger:
    def test_initial_state(self):
        ledger = TokenLedger(budget_usd=10.0)
        assert ledger.budget_usd == 10.0
        assert ledger.is_fused is False
        assert ledger.report.total_cost_usd == 0.0
        assert ledger.report.call_count == 0

    def test_record_actor_usage(self):
        ledger = TokenLedger(budget_usd=10.0)
        ledger.record_usage("gpt-4o", prompt_tokens=1000, completion_tokens=500, role="actor")
        assert ledger.report.actor_prompt_tokens == 1000
        assert ledger.report.actor_completion_tokens == 500
        assert ledger.report.call_count == 1
        assert ledger.report.total_cost_usd > 0

    def test_record_critic_usage(self):
        ledger = TokenLedger(budget_usd=10.0)
        ledger.record_usage("gpt-4o-mini", prompt_tokens=500, completion_tokens=200, role="critic")
        assert ledger.report.critic_prompt_tokens == 500
        assert ledger.report.critic_completion_tokens == 200

    def test_cost_calculation(self):
        """Verify cost = (prompt/1000 * price_prompt) + (completion/1000 * price_completion)."""
        pricing = {"test-model": {"prompt": 0.01, "completion": 0.03}}
        ledger = TokenLedger(budget_usd=100.0, pricing_map=pricing)
        ledger.record_usage("test-model", prompt_tokens=1000, completion_tokens=1000)
        # cost = 1000/1000 * 0.01 + 1000/1000 * 0.03 = 0.04
        assert abs(ledger.report.total_cost_usd - 0.04) < 1e-6

    def test_budget_exceeded_trips_fuse(self):
        pricing = {"m": {"prompt": 1.0, "completion": 1.0}}
        ledger = TokenLedger(budget_usd=0.002, pricing_map=pricing)
        with pytest.raises(BudgetExceededError):
            ledger.record_usage("m", prompt_tokens=1, completion_tokens=1)
        assert ledger.is_fused is True

    def test_no_recording_after_fuse(self):
        pricing = {"m": {"prompt": 1.0, "completion": 1.0}}
        ledger = TokenLedger(budget_usd=0.001, pricing_map=pricing)
        with pytest.raises(BudgetExceededError):
            ledger.record_usage("m", prompt_tokens=1, completion_tokens=1)
        # Subsequent calls are recorded but do NOT raise again
        ledger.record_usage("m", prompt_tokens=10000, completion_tokens=10000)
        assert ledger.report.call_count == 2  # both calls counted
        assert ledger.is_fused is True

    def test_unknown_model_uses_fallback_pricing(self):
        ledger = TokenLedger(budget_usd=10.0)
        ledger.record_usage("nonexistent-model", prompt_tokens=1000, completion_tokens=1000)
        # Unknown models use conservative fallback pricing (not zero)
        assert ledger.report.total_cost_usd > 0.0

    def test_get_summary(self):
        ledger = TokenLedger(budget_usd=10.0)
        ledger.record_usage("gpt-4o", prompt_tokens=1000, completion_tokens=500, role="actor")
        summary = ledger.get_summary()
        assert "total_cost" in summary
        assert "tokens" in summary
        assert summary["tokens"]["actor"] == 1500
        assert summary["tokens"]["critic"] == 0
        assert summary["fused"] is False

    def test_multiple_calls_accumulate(self):
        pricing = {"m": {"prompt": 0.001, "completion": 0.001}}
        ledger = TokenLedger(budget_usd=10.0, pricing_map=pricing)
        for _ in range(5):
            ledger.record_usage("m", prompt_tokens=100, completion_tokens=100)
        assert ledger.report.call_count == 5
        assert ledger.report.total_cost_usd == pytest.approx(5 * 0.0002)

    def test_budget_error_is_token_run_error(self):
        """BudgetExceededError should inherit from TokenRunError (not MemoryError)."""
        assert issubclass(BudgetExceededError, TokenRunError)
        assert issubclass(BudgetExceededError, RuntimeError)
        assert not issubclass(BudgetExceededError, MemoryError)
