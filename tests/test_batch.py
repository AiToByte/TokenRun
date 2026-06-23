"""Tests for gateway.batch_provider — OpenAI Batch API integration."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from gateway.batch_provider import (
    BatchProvider,
    BatchJob,
    BatchRequest,
    BatchResult,
)


def _make_request(custom_id="req-1"):
    return BatchRequest(
        custom_id=custom_id,
        messages=[{"role": "user", "content": "Hello"}],
        model="gpt-4o-mini",
    )


class TestBatchRequest:
    def test_creation(self):
        req = _make_request("r1")
        assert req.custom_id == "r1"
        assert req.model == "gpt-4o-mini"
        assert req.temperature == 0.1


class TestBatchResult:
    def test_success_result(self):
        r = BatchResult(custom_id="r1", content="Hello!", prompt_tokens=10, completion_tokens=5)
        assert r.error is None
        assert r.content == "Hello!"

    def test_error_result(self):
        r = BatchResult(custom_id="r1", error="Rate limit exceeded")
        assert r.content == ""
        assert r.error == "Rate limit exceeded"


class TestBatchJob:
    def test_job_fields(self):
        j = BatchJob(
            batch_id="batch-1",
            status="completed",
            total_requests=100,
            completed_requests=98,
            failed_requests=2,
        )
        assert j.batch_id == "batch-1"
        assert j.status == "completed"


class TestBatchProvider:
    @pytest.mark.asyncio
    async def test_context_manager(self):
        """BatchProvider should support async context manager."""
        bp = BatchProvider(api_key="test-key")
        async with bp as provider:
            assert provider is bp
        # Client should be closed after exit

    @pytest.mark.asyncio
    async def test_retrieve_results_empty(self):
        """retrieve_results with no output_file_id returns empty list."""
        bp = BatchProvider(api_key="test-key")
        job = BatchJob(batch_id="b1", status="completed", output_file_id="")
        results = await bp.retrieve_results(job)
        assert results == []
        await bp.close()

    @pytest.mark.asyncio
    async def test_retrieve_results_parses_jsonl(self):
        """retrieve_results should parse JSONL output correctly."""
        bp = BatchProvider(api_key="test-key")

        # Mock the HTTP response
        jsonl_lines = [
            json.dumps({
                "custom_id": "req-1",
                "response": {
                    "body": {
                        "choices": [{"message": {"content": "Answer 1"}}],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                    }
                },
            }),
            json.dumps({
                "custom_id": "req-2",
                "response": {
                    "body": {
                        "choices": [{"message": {"content": "Answer 2"}}],
                        "usage": {"prompt_tokens": 8, "completion_tokens": 3},
                    }
                },
            }),
        ]
        mock_resp = MagicMock()
        mock_resp.text = "\n".join(jsonl_lines)
        mock_resp.raise_for_status = MagicMock()

        bp._client = MagicMock()
        bp._client.get = AsyncMock(return_value=mock_resp)
        bp._client.aclose = AsyncMock()

        job = BatchJob(batch_id="b1", status="completed", output_file_id="file-123")
        results = await bp.retrieve_results(job)

        assert len(results) == 2
        assert results[0].custom_id == "req-1"
        assert results[0].content == "Answer 1"
        assert results[0].prompt_tokens == 10
        assert results[1].custom_id == "req-2"
        assert results[1].content == "Answer 2"

        await bp.close()

    @pytest.mark.asyncio
    async def test_retrieve_results_with_error(self):
        """retrieve_results should handle error entries."""
        bp = BatchProvider(api_key="test-key")

        jsonl_line = json.dumps({
            "custom_id": "req-err",
            "error": {"message": "Rate limit"},
        })
        mock_resp = MagicMock()
        mock_resp.text = jsonl_line
        mock_resp.raise_for_status = MagicMock()

        bp._client = MagicMock()
        bp._client.get = AsyncMock(return_value=mock_resp)
        bp._client.aclose = AsyncMock()

        job = BatchJob(batch_id="b1", status="completed", output_file_id="file-123")
        results = await bp.retrieve_results(job)

        assert len(results) == 1
        assert results[0].custom_id == "req-err"
        assert results[0].error is not None

        await bp.close()
