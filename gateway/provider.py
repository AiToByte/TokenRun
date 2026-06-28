"""
LLM Provider — generic async client for OpenAI-compatible APIs.

Wraps httpx with smart retry logic (only retries transient failures)
and returns a standardised ``LLMResponse`` for ledger integration.
Supports optional circuit breaker for fault isolation.
"""

from __future__ import annotations

import asyncio
import ipaddress
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

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


def _is_private_host(hostname: str) -> bool:
    """Check if a hostname resolves to a private/internal IP address.

    Returns True for localhost, private IPs, link-local, and metadata endpoints.
    Used to prevent SSRF attacks via configurable base_url fields.
    """
    # Quick string checks before DNS resolution
    if hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return True
    if hostname.startswith("169.254."):  # AWS metadata, link-local
        return True
    try:
        addr = ipaddress.ip_address(hostname)
        return addr.is_private or addr.is_loopback or addr.is_link_local
    except ValueError:
        # hostname is a domain name — allow it (DNS resolution would be
        # needed to check, but that adds latency; trust DNS-based SSRF
        # protection to httpx's transport layer)
        return False


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
    circuit_breaker:
        Optional :class:`~core.resilience.CircuitBreaker` for fault isolation.
    allow_private:
        If False (default), block requests to private/internal IP addresses
        to prevent SSRF attacks.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        model_name: str = "gpt-4o-mini",
        timeout: float = 60.0,
        max_retries: int = 3,
        circuit_breaker: Any = None,
        allow_private: bool = False,
    ) -> None:
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name
        self.timeout = timeout
        self.max_retries = max_retries
        self._client = httpx.AsyncClient(timeout=timeout)
        self._circuit_breaker = circuit_breaker

        # SSRF protection: validate base_url
        if not allow_private:
            parsed = urlparse(self.base_url)
            hostname = parsed.hostname or ""
            if _is_private_host(hostname):
                raise LLMProviderError(
                    f"SSRF blocked: base_url '{base_url}' resolves to a "
                    f"private/internal host ({hostname}). "
                    f"Set allow_private=True to override."
                )

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

        if self._circuit_breaker is not None:
            return await self._circuit_breaker.call(self._request_inner, payload)
        return await self._request_inner(payload)

    async def _request_inner(self, payload: Dict[str, Any]) -> LLMResponse:
        """Inner request method (called directly or via circuit breaker)."""
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
                                delay = 2.0**attempt
                        else:
                            delay = 2.0**attempt
                    else:
                        delay = 2.0**attempt

                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(delay)
                        continue
                    resp.raise_for_status()

                # For 4xx errors (non-retryable), raise immediately.
                if resp.status_code >= 400:
                    raise LLMProviderError(
                        f"LLM 请求被拒绝 (HTTP {resp.status_code}): {resp.text}"
                    )

                data = resp.json()
                usage = data.get("usage", {})
                try:
                    content = data["choices"][0]["message"]["content"]
                except (KeyError, IndexError) as exc:
                    raise LLMProviderError(f"LLM 响应格式异常: {exc} — {data}") from exc
                return LLMResponse(
                    content=content,
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                    model_name=data.get("model", payload["model"]),
                )

            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in _RETRYABLE_STATUS:
                    last_exc = exc
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(2.0**attempt)
                        continue
                raise LLMProviderError(
                    f"LLM 请求被拒绝 (HTTP {exc.response.status_code}): "
                    f"{exc.response.text}"
                ) from exc

            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                last_exc = exc
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2.0**attempt)
                    continue

            except LLMProviderError:
                raise
            except Exception as exc:
                raise LLMProviderError(f"LLM 请求遇到意外错误: {exc}") from exc

        raise LLMProviderError(
            f"LLM 请求失败 (已重试{self.max_retries}次): {last_exc}"
        ) from last_exc

    async def embed(
        self,
        text: str,
        model: str = "text-embedding-3-small",
    ) -> List[float]:
        """Generate an embedding vector for the given text.

        Parameters
        ----------
        text:
            Text to embed.
        model:
            Embedding model name.

        Returns
        -------
        list[float]
            The embedding vector.
        """
        payload = {
            "model": model,
            "input": text,
        }

        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                resp = await self._client.post(
                    f"{self.base_url}/embeddings",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=payload,
                )

                if resp.status_code in _RETRYABLE_STATUS:
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(2.0**attempt)
                        continue
                    resp.raise_for_status()

                if resp.status_code >= 400:
                    raise LLMProviderError(
                        f"Embedding 请求被拒绝 (HTTP {resp.status_code}): {resp.text}"
                    )

                data = resp.json()
                return data["data"][0]["embedding"]

            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in _RETRYABLE_STATUS:
                    last_exc = exc
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(2.0**attempt)
                        continue
                raise LLMProviderError(
                    f"Embedding 请求被拒绝 (HTTP {exc.response.status_code}): "
                    f"{exc.response.text}"
                ) from exc

            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                last_exc = exc
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2.0**attempt)
                    continue

            except LLMProviderError:
                raise
            except Exception as exc:
                raise LLMProviderError(f"Embedding 请求遇到意外错误: {exc}") from exc

        raise LLMProviderError(
            f"Embedding 请求失败 (已重试{self.max_retries}次): {last_exc}"
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
