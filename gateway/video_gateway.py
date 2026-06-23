"""
Video Gateway — extract frames and metadata from video files.

Requires ``opencv-python`` (``cv2``) to be installed.
Extracts key frames at configurable intervals for LLM processing.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

__all__ = ["VideoGateway"]


class VideoGateway:
    """Extract frames and metadata from video files.

    Parameters
    ----------
    fps_sample:
        Extract one frame every N seconds.  Default 1 (one per second).
    max_frames:
        Maximum frames to extract per video.  0 = unlimited.
    output_format:
        Format for frame output: ``"base64"`` (JPEG base64) or ``"path"`` (save to disk).
    """

    def __init__(
        self,
        fps_sample: float = 1.0,
        max_frames: int = 0,
        output_format: str = "base64",
    ) -> None:
        self.fps_sample = fps_sample
        self.max_frames = max_frames
        self.output_format = output_format

    def extract_frames(
        self,
        video_path: str,
        output_dir: Optional[str] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        """Extract frames from a video file.

        Yields dicts with ``frame_number``, ``timestamp``, and either
        ``base64`` or ``path`` depending on output_format.
        """
        try:
            import cv2
        except ImportError:
            raise ImportError(
                "VideoGateway requires opencv-python. "
                "Install with: pip install opencv-python"
            )

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise FileNotFoundError(f"Cannot open video: {video_path}")

        video_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        frame_interval = int(video_fps * self.fps_sample)
        frame_count = 0
        extracted = 0

        out_path = Path(output_dir) if output_dir else None
        if out_path:
            out_path.mkdir(parents=True, exist_ok=True)

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            if frame_count % frame_interval == 0:
                timestamp = frame_count / video_fps

                if self.output_format == "base64":
                    _, buffer = cv2.imencode(".jpg", frame)
                    b64 = base64.b64encode(buffer).decode("utf-8")
                    yield {
                        "frame_number": frame_count,
                        "timestamp": round(timestamp, 2),
                        "base64": b64,
                        "format": "jpeg",
                    }
                elif self.output_format == "path" and out_path:
                    frame_file = out_path / f"frame_{frame_count:06d}.jpg"
                    cv2.imwrite(str(frame_file), frame)
                    yield {
                        "frame_number": frame_count,
                        "timestamp": round(timestamp, 2),
                        "path": str(frame_file),
                    }

                extracted += 1
                if self.max_frames > 0 and extracted >= self.max_frames:
                    break

            frame_count += 1

        cap.release()

    def get_video_info(self, video_path: str) -> Dict[str, Any]:
        """Return metadata about a video file."""
        try:
            import cv2
        except ImportError:
            raise ImportError(
                "VideoGateway requires opencv-python. "
                "Install with: pip install opencv-python"
            )

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise FileNotFoundError(f"Cannot open video: {video_path}")

        info = {
            "path": video_path,
            "fps": cap.get(cv2.CAP_PROP_FPS),
            "frame_count": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
            "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        }
        info["duration_seconds"] = round(info["frame_count"] / info["fps"], 2) if info["fps"] > 0 else 0
        cap.release()
        return info
