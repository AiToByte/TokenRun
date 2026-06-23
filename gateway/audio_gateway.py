"""
Audio Gateway — transcribe audio files to text.

Requires ``openai`` package for Whisper API, or ``whisper`` for local
transcription.  Falls back to OpenAI API by default.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

__all__ = ["AudioGateway"]


class AudioGateway:
    """Transcribe audio files to text.

    Parameters
    ----------
    backend:
        ``"openai"`` (default, uses Whisper API) or ``"local"``
        (uses local whisper model).
    model:
        Whisper model name (default ``"whisper-1"`` for OpenAI).
    language:
        Expected language code (e.g. ``"zh"``, ``"en"``).  Auto-detect if None.
    """

    def __init__(
        self,
        backend: str = "openai",
        model: str = "whisper-1",
        language: Optional[str] = None,
    ) -> None:
        self.backend = backend
        self.model = model
        self.language = language

    async def transcribe(
        self,
        audio_path: str,
        api_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Transcribe an audio file to text.

        Parameters
        ----------
        audio_path:
            Path to the audio file (mp3, wav, m4a, etc.).
        api_key:
            OpenAI API key (reads from OPENAI_API_KEY env if not provided).

        Returns
        -------
        dict
            ``{"text": str, "language": str, "duration": float}``
        """
        if self.backend == "openai":
            return await self._transcribe_openai(audio_path, api_key)
        elif self.backend == "local":
            return self._transcribe_local(audio_path)
        else:
            raise ValueError(f"Unsupported backend: {self.backend}")

    async def _transcribe_openai(
        self,
        audio_path: str,
        api_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Transcribe using OpenAI Whisper API."""
        key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if not key:
            raise ValueError("OpenAI API key required for transcription")

        try:
            import httpx
        except ImportError:
            raise ImportError("httpx required. Install with: pip install httpx")

        path = Path(audio_path)
        if not path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        async with httpx.AsyncClient(timeout=300.0) as client:
            with open(audio_path, "rb") as f:
                files = {"file": (path.name, f, "audio/mpeg")}
                data = {"model": self.model}
                if self.language:
                    data["language"] = self.language

                resp = await client.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {key}"},
                    files=files,
                    data=data,
                )
                resp.raise_for_status()
                result = resp.json()

        return {
            "text": result.get("text", ""),
            "language": result.get("language", self.language or "unknown"),
            "duration": 0.0,  # OpenAI doesn't return duration in this endpoint
        }

    def _transcribe_local(self, audio_path: str) -> Dict[str, Any]:
        """Transcribe using local Whisper model."""
        try:
            import whisper
        except ImportError:
            raise ImportError(
                "Local transcription requires openai-whisper. "
                "Install with: pip install openai-whisper"
            )

        model = whisper.load_model("base")
        result = model.transcribe(audio_path, language=self.language)

        return {
            "text": result.get("text", ""),
            "language": result.get("language", self.language or "unknown"),
            "duration": result.get("duration", 0.0),
        }

    async def transcribe_batch(
        self,
        audio_paths: List[str],
        api_key: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Transcribe multiple audio files sequentially."""
        results = []
        for path in audio_paths:
            result = await self.transcribe(path, api_key)
            result["source"] = path
            results.append(result)
        return results
