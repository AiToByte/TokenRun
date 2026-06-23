"""Tests for gateway.provider — LLM client with retry logic."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

from gateway.provider import LLMProvider, LLMProviderError, LLMResponse


@pytest.fixture
def provider():
    return LLMProvider(api_key="test-key", base_url="http://test", model_name="test-model")


class TestLLMProvider:
    @pytest.mark.asyncio
    async def test_request_success(self, provider):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "Hello!"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "model": "test-model",
        }
        mock_resp.raise_for_status = MagicMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        result = await provider.request([{"role": "user", "content": "Hi"}])
        assert result.content == "Hello!"
        assert result.prompt_tokens == 10
        assert result.completion_tokens == 5

    @pytest.mark.asyncio
    async def test_request_401_raises_immediately(self, provider):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"
        mock_resp.raise_for_status = MagicMock()
        exc = httpx.HTTPStatusError("401", request=MagicMock(), response=mock_resp)
        mock_resp.raise_for_status.side_effect = exc
        provider._client.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(LLMProviderError, match="401"):
            await provider.request([{"role": "user", "content": "Hi"}])

    @pytest.mark.asyncio
    async def test_request_429_retries(self, provider):
        # First call: 429, second call: success
        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_429.text = "Rate limited"
        resp_429.headers = {}
        exc_429 = httpx.HTTPStatusError("429", request=MagicMock(), response=resp_429)
        resp_429.raise_for_status.side_effect = exc_429

        resp_ok = MagicMock()
        resp_ok.status_code = 200
        resp_ok.json.return_value = {
            "choices": [{"message": {"content": "OK"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            "model": "test-model",
        }
        resp_ok.raise_for_status = MagicMock()

        provider._client.post = AsyncMock(side_effect=[resp_429, resp_ok])

        result = await provider.request([{"role": "user", "content": "Hi"}])
        assert result.content == "OK"
        assert provider._client.post.await_count == 2

    @pytest.mark.asyncio
    async def test_request_timeout_retries(self, provider):
        provider._client.post = AsyncMock(side_effect=[
            httpx.TimeoutException("timeout"),
            MagicMock(
                status_code=200,
                json=lambda: {"choices": [{"message": {"content": "OK"}}], "usage": {}, "model": "m"},
                raise_for_status=MagicMock(),
            ),
        ])

        result = await provider.request([{"role": "user", "content": "Hi"}])
        assert result.content == "OK"

    @pytest.mark.asyncio
    async def test_request_all_retries_fail(self, provider):
        provider._client.post = AsyncMock(
            side_effect=httpx.TimeoutException("timeout")
        )
        provider.max_retries = 2

        with pytest.raises(LLMProviderError, match="重试"):
            await provider.request([{"role": "user", "content": "Hi"}])

    @pytest.mark.asyncio
    async def test_request_with_response_format(self, provider):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": '{"key": "value"}'}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            "model": "test-model",
        }
        mock_resp.raise_for_status = MagicMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        result = await provider.request(
            [{"role": "user", "content": "Hi"}],
            response_format={"type": "json_object"},
        )
        assert '{"key": "value"}' in result.content

    @pytest.mark.asyncio
    async def test_close(self, provider):
        provider._client.aclose = AsyncMock()
        await provider.close()
        provider._client.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_context_manager(self):
        p = LLMProvider(api_key="test")
        p._client.aclose = AsyncMock()
        async with p as prov:
            assert prov is p
        p._client.aclose.assert_awaited_once()
