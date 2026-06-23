"""Tests for Semantic Drift Detector — embedding-based drift detection."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from core.drift_detector import (
    DriftAlert,
    DriftDetector,
    SemanticDriftDetector,
    _cosine_similarity,
)
from gateway.provider import LLMProvider, LLMResponse


# ---------------------------------------------------------------------------
# Cosine Similarity
# ---------------------------------------------------------------------------

class TestCosineSimilarity:
    def test_identical_vectors(self):
        a = [1.0, 0.0, 0.0]
        assert _cosine_similarity(a, a) == 1.0

    def test_orthogonal_vectors(self):
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        assert abs(_cosine_similarity(a, b)) < 1e-10

    def test_opposite_vectors(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert abs(_cosine_similarity(a, b) - (-1.0)) < 1e-10

    def test_similar_vectors(self):
        a = [1.0, 0.5, 0.3]
        b = [1.0, 0.6, 0.2]
        sim = _cosine_similarity(a, b)
        assert 0.9 < sim < 1.0

    def test_zero_vector(self):
        a = [0.0, 0.0, 0.0]
        b = [1.0, 0.0, 0.0]
        assert _cosine_similarity(a, b) == 0.0

    def test_dimension_mismatch_raises(self):
        with pytest.raises(ValueError, match="dimensions mismatch"):
            _cosine_similarity([1.0, 0.0], [1.0])


# ---------------------------------------------------------------------------
# Semantic Drift Detector
# ---------------------------------------------------------------------------

def _make_embed_provider(return_embedding=None):
    """Create a mock embed provider."""
    default_emb = [0.1] * 10
    provider = MagicMock(spec=LLMProvider)
    provider.embed = AsyncMock(return_value=return_embedding or default_emb)
    return provider


def _make_actor(return_content="output text"):
    actor = MagicMock()
    actor.provider = MagicMock()
    actor.provider.request = AsyncMock(return_value=LLMResponse(
        content=return_content, prompt_tokens=10, completion_tokens=5, model_name="m"
    ))
    return actor


class TestSemanticDriftDetector:
    @pytest.mark.asyncio
    async def test_no_golden_samples(self):
        actor = _make_actor()
        embed_provider = _make_embed_provider()
        dd = SemanticDriftDetector(
            actor=actor, embed_provider=embed_provider,
            golden_samples=[], check_interval=1,
        )
        dd.tick()
        report = await dd.run_check("template")
        assert report["drift_detected"] is False
        assert report["avg_similarity"] == 1.0

    def test_disabled_by_default(self):
        actor = _make_actor()
        embed_provider = _make_embed_provider()
        dd = SemanticDriftDetector(
            actor=actor, embed_provider=embed_provider,
            golden_samples=[{"input": "test", "expected_output": "expected"}],
            check_interval=0,
        )
        assert dd.enabled is False
        assert dd.tick() is False

    def test_tick_counts(self):
        actor = _make_actor()
        embed_provider = _make_embed_provider()
        dd = SemanticDriftDetector(
            actor=actor, embed_provider=embed_provider,
            golden_samples=[{"input": "test", "expected_output": "expected"}],
            check_interval=5,
        )
        assert dd.enabled is True
        for _ in range(4):
            assert dd.tick() is False
        assert dd.tick() is True

    @pytest.mark.asyncio
    async def test_high_similarity_passes(self):
        """Similar outputs should pass (no drift)."""
        same_emb = [0.1, 0.2, 0.3, 0.4, 0.5]
        actor = _make_actor(return_content="similar output")
        embed_provider = _make_embed_provider(return_embedding=same_emb)

        dd = SemanticDriftDetector(
            actor=actor, embed_provider=embed_provider,
            golden_samples=[{"input": "test", "expected_output": "expected"}],
            check_interval=1, similarity_threshold=0.85,
        )
        dd.tick()
        report = await dd.run_check("template")
        assert report["drift_detected"] is False
        assert report["avg_similarity"] == 1.0  # identical vectors

    @pytest.mark.asyncio
    async def test_low_similarity_triggers_alert(self):
        """Different outputs should trigger drift alert."""
        golden_emb = [1.0, 0.0, 0.0, 0.0, 0.0]
        current_emb = [0.0, 0.0, 0.0, 0.0, 1.0]  # orthogonal

        actor = _make_actor(return_content="different output")
        embed_provider = MagicMock(spec=LLMProvider)
        # First call: golden embedding, second call: current embedding
        embed_provider.embed = AsyncMock(side_effect=[golden_emb, current_emb])

        dd = SemanticDriftDetector(
            actor=actor, embed_provider=embed_provider,
            golden_samples=[{"input": "test", "expected_output": "expected"}],
            check_interval=1, similarity_threshold=0.85,
        )
        dd.tick()

        with pytest.raises(DriftAlert, match="语义漂移"):
            await dd.run_check("template")

    @pytest.mark.asyncio
    async def test_multiple_golden_samples(self):
        """Multiple samples should compute average similarity."""
        emb1 = [0.1, 0.2, 0.3]
        emb2 = [0.4, 0.5, 0.6]

        actor = _make_actor(return_content="output")
        embed_provider = MagicMock(spec=LLMProvider)
        # 2 golden embeddings + 2 current embeddings
        embed_provider.embed = AsyncMock(side_effect=[emb1, emb2, emb1, emb2])

        dd = SemanticDriftDetector(
            actor=actor, embed_provider=embed_provider,
            golden_samples=[
                {"input": "test1", "expected_output": "expected1"},
                {"input": "test2", "expected_output": "expected2"},
            ],
            check_interval=1, similarity_threshold=0.85,
        )
        dd.tick()
        report = await dd.run_check("template")
        assert report["drift_detected"] is False
        assert len(report["details"]) == 2

    @pytest.mark.asyncio
    async def test_golden_embedding_cached(self):
        """Golden embeddings should be cached across checks."""
        golden_emb = [0.1, 0.2, 0.3]
        current_emb = [0.1, 0.2, 0.3]

        actor = _make_actor(return_content="output")
        embed_provider = MagicMock(spec=LLMProvider)
        # First check: golden + current; second check: only current (golden cached)
        embed_provider.embed = AsyncMock(side_effect=[
            golden_emb, current_emb,  # first check
            current_emb,              # second check (golden cached)
        ])

        dd = SemanticDriftDetector(
            actor=actor, embed_provider=embed_provider,
            golden_samples=[{"input": "test", "expected_output": "expected"}],
            check_interval=1, similarity_threshold=0.85,
        )

        # First check
        dd.tick()
        await dd.run_check("template")

        # Second check — should only call embed once (for current)
        dd.tick()
        report = await dd.run_check("template")
        assert report["drift_detected"] is False

    @pytest.mark.asyncio
    async def test_actor_exception_handled(self):
        """Actor exceptions should be recorded, not crash the check."""
        actor = MagicMock()
        actor.provider = MagicMock()
        actor.provider.request = AsyncMock(side_effect=Exception("API error"))

        embed_provider = _make_embed_provider()

        dd = SemanticDriftDetector(
            actor=actor, embed_provider=embed_provider,
            golden_samples=[{"input": "test", "expected_output": "expected"}],
            check_interval=1, similarity_threshold=0.85,
        )
        dd.tick()
        report = await dd.run_check("template")
        assert report["details"][0]["passed"] is False
        assert "error" in report["details"][0]

    @pytest.mark.asyncio
    async def test_no_expected_output_skips_comparison(self):
        """Samples without expected_output should be skipped."""
        actor = _make_actor(return_content="output")
        embed_provider = _make_embed_provider()

        dd = SemanticDriftDetector(
            actor=actor, embed_provider=embed_provider,
            golden_samples=[{"input": "test", "expected_output": ""}],
            check_interval=1, similarity_threshold=0.85,
        )
        dd.tick()
        report = await dd.run_check("template")
        assert report["details"][0]["passed"] is True
        assert report["details"][0]["note"] == "No golden embedding to compare"


class TestHashDriftDetector:
    @pytest.mark.asyncio
    async def test_empty_golden_samples(self):
        actor = _make_actor()
        dd = DriftDetector(actor=actor, golden_samples=[], check_interval=1)
        dd.tick()
        report = await dd.run_check("template")
        assert report["drift_detected"] is False

    @pytest.mark.asyncio
    async def test_empty_hash_always_matches(self):
        actor = _make_actor()
        dd = DriftDetector(
            actor=actor,
            golden_samples=[{"input": "test", "expected_output_hash": ""}],
            check_interval=1, threshold=1.0,
        )
        dd.tick()
        report = await dd.run_check("template")
        assert report["drift_detected"] is False
