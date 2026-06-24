"""Tests for core.app — TokenRunApp integration."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from core.app import TokenRunApp
from core.models import Runfile, TaskNode, LoopConfig, ValidationRule
from gateway.provider import LLMProvider, LLMResponse


@pytest.fixture
def mock_providers():
    actor = MagicMock(spec=LLMProvider)
    actor.model_name = "test-actor"
    actor.close = AsyncMock()

    critic = MagicMock(spec=LLMProvider)
    critic.model_name = "test-critic"
    critic.close = AsyncMock()

    return actor, critic


@pytest.fixture
def runfile():
    return Runfile(
        name="Test Mission",
        workflow=[TaskNode(
            id="n1", name="Summarize",
            actor_prompt_template="Summarize: {{ data }}",
            loop_config=LoopConfig(max_attempts=1, exit_criteria=[
                ValidationRule(type="llm_eval", criteria="good"),
            ]),
        )],
    )


class TestTokenRunApp:
    def test_init(self, runfile, mock_providers):
        actor_p, critic_p = mock_providers
        app = TokenRunApp(runfile, actor_p, critic_p)
        assert app.runfile.name == "Test Mission"
        assert app.ledger is not None
        assert app.persistence is not None
        assert app.redactor is not None
        assert app.telemetry is not None

    @pytest.mark.asyncio
    async def test_sense_resources_demo_data(self, runfile, mock_providers):
        actor_p, critic_p = mock_providers
        app = TokenRunApp(runfile, actor_p, critic_p)
        data = await app.sense_resources()
        assert len(data) == 3  # demo data has 3 items
        assert all(isinstance(d, str) for d in data)

    @pytest.mark.asyncio
    async def test_sense_resources_local_file(self, tmp_path, mock_providers):
        # Create a test file
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "test.txt").write_text("File content here", encoding="utf-8")

        from core.models import Resource, ResourceType
        runfile = Runfile(
            name="Test",
            context=[Resource(id="r1", uri=f"local://{data_dir}", type=ResourceType.LOCAL_FILE)],
            workflow=[TaskNode(
                id="n1", name="T",
                actor_prompt_template="Do {{ data }}",
                loop_config=LoopConfig(max_attempts=1, exit_criteria=[
                    ValidationRule(type="llm_eval", criteria="good"),
                ]),
            )],
        )
        actor_p, critic_p = mock_providers
        app = TokenRunApp(runfile, actor_p, critic_p)
        data = await app.sense_resources()
        assert len(data) == 1
        assert data[0] == "File content here"

    @pytest.mark.asyncio
    async def test_sense_resources_empty_dir_falls_back(self, tmp_path, mock_providers):
        from core.models import Resource, ResourceType
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        runfile = Runfile(
            name="Test",
            context=[Resource(id="r1", uri=f"local://{empty_dir}", type=ResourceType.LOCAL_FILE)],
            workflow=[TaskNode(
                id="n1", name="T",
                actor_prompt_template="Do {{ data }}",
                loop_config=LoopConfig(max_attempts=1, exit_criteria=[
                    ValidationRule(type="llm_eval", criteria="good"),
                ]),
            )],
        )
        actor_p, critic_p = mock_providers
        app = TokenRunApp(runfile, actor_p, critic_p)
        data = await app.sense_resources()
        # Should fall back to demo data
        assert len(data) == 3

    def test_demo_data_content(self):
        data = TokenRunApp._demo_data()
        assert len(data) == 3
        assert "人工智能" in data[0]
        assert "量子计算" in data[1]
        assert "可持续发展" in data[2]
