"""
LLM Provider — generic async client for OpenAI-compatible APIs.

Wraps httpx with smart retry logic (only retries transient failures)
and returns a standardised ``LLMResponse`` for ledger integration.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

__all__ = ["LLMResponse", "LLMProvider", "LLMProviderError"]


class LLMProviderError(RuntimeError):
    """Raised when the LLM API returns a non-retryable error."""


@dataclass
class LLMResponse:
    """Normalised response from any OpenAI-compatible chat endpoint."""
    content: str
    prompt_tokens: int
    completion_tokens: int
    model_name: str


# HTTP status codes that are safe to retry (transient server-side issues).
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class LLMProvider:
    """Async HTTP client for OpenAI-compatible ``/chat/completions``.

    Parameters
    ----------
    api_key:
        Bearer token for the target API.
    base_url:
        Root URL **without** the ``/chat/completions`` suffix.
        Examples: ``https://api.openai.com/v1``,
        ``https://api.deepseek.com/v1``.
    model_name:
        Default model to use when none is passed to :meth:`request`.
    timeout:
        Per-request timeout in seconds.
    max_retries:
        Number of retry attempts on transient failures.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        model_name: str = "gpt-4o-mini",
        timeout: float = 60.0,
        max_retries: int = 3,
    ) -> None:
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name
        self.timeout = timeout
        self.max_retries = max_retries
        self._client = httpx.AsyncClient(timeout=timeout)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def request(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.1,
        response_format: Optional[Dict[str, str]] = None,
        model: Optional[str] = None,
    ) -> LLMResponse:
        """Send a chat completion request and return a normalised response.

        Parameters
        ----------
        messages:
            Standard ``[{role, content}, ...]`` message list.
        temperature:
            Sampling temperature (default 0.1 for determinism).
        response_format:
            E.g. ``{"type": "json_object"}`` to force structured output.
        model:
            Override the provider-level default model.
        """
        payload: Dict[str, Any] = {
            "model": model or self.model_name,
            "messages": messages,
            "temperature": temperature,
        }
        if response_format is not None:
            payload["response_format"] = response_format

        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                resp = await self._client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=payload,
                )

                # Do NOT retry client errors (401, 403, 404, 422).
                if resp.status_code in _RETRYABLE_STATUS:
                    # Respect Retry-After header for 429
                    if resp.status_code == 429:
                        retry_after = resp.headers.get("Retry-After")
                        if retry_after:
                            try:
                                delay = float(retry_after)
                            except ValueError:
                                delay = 2.0 ** attempt
                        else:
                            delay = 2.0 ** attempt
                    else:
                        delay = 2.0 ** attempt

                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(delay)
                        continue
                    resp.raise_for_status()

                # For 4xx errors (non-retryable), raise immediately.
                if resp.status_code >= 400:
                    raise LLMProviderError(
                        f"LLM 请求被拒绝 (HTTP {resp.status_code}): "
                        f"{resp.text}"
                    )

                data = resp.json()
                usage = data.get("usage", {})
                return LLMResponse(
                    content=data["choices"][0]["message"]["content"],
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                    model_name=data.get("model", payload["model"]),
                )

            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in _RETRYABLE_STATUS:
                    last_exc = exc
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(2.0 ** attempt)
                        continue
                raise LLMProviderError(
                    f"LLM 请求被拒绝 (HTTP {exc.response.status_code}): "
                    f"{exc.response.text}"
                ) from exc

            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                last_exc = exc
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2.0 ** attempt)
                    continue

            except LLMProviderError:
                raise
            except Exception as exc:
                raise LLMProviderError(
                    f"LLM 请求遇到意外错误: {exc}"
                ) from exc

        raise LLMProviderError(
            f"LLM 请求失败 (已重试{self.max_retries}次): {last_exc}"
        ) from last_exc

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    async def __aenter__(self) -> LLMProvider:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()
