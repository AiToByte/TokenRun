"""
Batch Provider — OpenAI Batch API integration for cost-efficient bulk processing.

Submits requests via the Batch API at 50% cost.  Results are returned
within 24 hours.  Ideal for non-urgent bulk processing workloads.

Usage::

    async with BatchProvider(api_key="sk-...") as bp:
        job_id = await bp.submit_batch(requests)
        status = await bp.check_status(job_id)
        results = await bp.retrieve_results(job_id)
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

__all__ = ["BatchProvider", "BatchJob", "BatchRequest", "BatchResult"]


@dataclass
class BatchRequest:
    """A single request in a batch."""

    custom_id: str
    messages: List[Dict[str, str]]
    model: str = "gpt-4o-mini"
    temperature: float = 0.1
    max_tokens: int = 4096


@dataclass
class BatchResult:
    """Result for a single request in a completed batch."""

    custom_id: str
    content: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    error: Optional[str] = None


@dataclass
class BatchJob:
    """Status of a batch job."""

    batch_id: str
    status: str  # "validating" | "in_progress" | "completed" | "failed" | "expired"
    total_requests: int = 0
    completed_requests: int = 0
    failed_requests: int = 0
    created_at: float = 0.0
    completed_at: float = 0.0
    output_file_id: str = ""
    error_file_id: str = ""


class BatchProvider:
    """Submit and manage OpenAI Batch API jobs.

    Parameters
    ----------
    api_key:
        OpenAI API key.
    base_url:
        API base URL (default: ``https://api.openai.com/v1``).
    poll_interval:
        Seconds between status polls when waiting for completion.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        poll_interval: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.poll_interval = poll_interval
        self._client = httpx.AsyncClient(timeout=120.0)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def submit_batch(
        self,
        requests: List[BatchRequest],
        completion_window: str = "24h",
    ) -> str:
        """Submit a batch of requests to the OpenAI Batch API.

        Parameters
        ----------
        requests:
            List of :class:`BatchRequest` objects.
        completion_window:
            How long OpenAI has to process the batch (``"24h"`` default).

        Returns
        -------
        str
            The batch ID for tracking.
        """
        # Build JSONL file content
        lines = []
        for req in requests:
            line = {
                "custom_id": req.custom_id,
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": {
                    "model": req.model,
                    "messages": req.messages,
                    "temperature": req.temperature,
                    "max_tokens": req.max_tokens,
                },
            }
            lines.append(json.dumps(line, ensure_ascii=False))

        jsonl_content = "\n".join(lines)

        # Upload the JSONL file
        file_resp = await self._client.post(
            f"{self.base_url}/files",
            headers={"Authorization": f"Bearer {self._api_key}"},
            data={"purpose": "batch"},
            files={
                "file": ("batch.jsonl", jsonl_content.encode(), "application/jsonl")
            },
        )
        file_resp.raise_for_status()
        file_id = file_resp.json()["id"]

        # Create the batch job
        batch_resp = await self._client.post(
            f"{self.base_url}/batches",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={
                "input_file_id": file_id,
                "endpoint": "/v1/chat/completions",
                "completion_window": completion_window,
            },
        )
        batch_resp.raise_for_status()
        return batch_resp.json()["id"]

    async def check_status(self, batch_id: str) -> BatchJob:
        """Check the status of a batch job.

        Returns
        -------
        BatchJob
            Current status of the batch.
        """
        resp = await self._client.get(
            f"{self.base_url}/batches/{batch_id}",
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        resp.raise_for_status()
        data = resp.json()

        return BatchJob(
            batch_id=data["id"],
            status=data["status"],
            total_requests=data.get("request_counts", {}).get("total", 0),
            completed_requests=data.get("request_counts", {}).get("completed", 0),
            failed_requests=data.get("request_counts", {}).get("failed", 0),
            created_at=data.get("created_at", 0),
            completed_at=data.get("completed_at", 0),
            output_file_id=data.get("output_file_id", ""),
            error_file_id=data.get("error_file_id", ""),
        )

    async def wait_for_completion(
        self,
        batch_id: str,
        timeout: float = 86400.0,
        on_progress: Optional[Any] = None,
    ) -> BatchJob:
        """Poll until the batch completes or times out.

        Parameters
        ----------
        batch_id:
            The batch ID to poll.
        timeout:
            Maximum seconds to wait (default 24h).
        on_progress:
            Optional callback ``(job: BatchJob) -> None`` called on each poll.

        Returns
        -------
        BatchJob
            Final status.

        Raises
        ------
        TimeoutError
            If the batch does not complete within *timeout* seconds.
        RuntimeError
            If the batch fails or expires.
        """
        start = time.time()
        while time.time() - start < timeout:
            job = await self.check_status(batch_id)
            if on_progress:
                on_progress(job)

            if job.status == "completed":
                return job
            if job.status in ("failed", "expired"):
                raise RuntimeError(f"批次 {batch_id} 状态异常: {job.status}")

            await asyncio.sleep(self.poll_interval)

        raise TimeoutError(f"批次 {batch_id} 在 {timeout}s 内未完成。")

    async def retrieve_results(self, batch_job: BatchJob) -> List[BatchResult]:
        """Download and parse results from a completed batch.

        Returns
        -------
        list[BatchResult]
            One result per request, matched by ``custom_id``.
        """
        if not batch_job.output_file_id:
            return []

        resp = await self._client.get(
            f"{self.base_url}/files/{batch_job.output_file_id}/content",
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        resp.raise_for_status()

        results: List[BatchResult] = []
        for line in resp.text.strip().split("\n"):
            if not line.strip():
                continue
            data = json.loads(line)
            custom_id = data.get("custom_id", "")
            response_body = data.get("response", {}).get("body", {})
            choices = response_body.get("choices", [])
            usage = response_body.get("usage", {})
            error = data.get("error")

            if error:
                results.append(
                    BatchResult(
                        custom_id=custom_id,
                        error=str(error),
                    )
                )
            elif choices:
                results.append(
                    BatchResult(
                        custom_id=custom_id,
                        content=choices[0].get("message", {}).get("content", ""),
                        prompt_tokens=usage.get("prompt_tokens", 0),
                        completion_tokens=usage.get("completion_tokens", 0),
                    )
                )

        return results

    async def cancel(self, batch_id: str) -> BatchJob:
        """Cancel a running batch."""
        resp = await self._client.post(
            f"{self.base_url}/batches/{batch_id}/cancel",
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        resp.raise_for_status()
        data = resp.json()
        return BatchJob(
            batch_id=data["id"],
            status=data["status"],
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    async def __aenter__(self) -> BatchProvider:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()
