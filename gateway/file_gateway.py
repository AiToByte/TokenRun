"""
File Gateway — safe local file streaming with glob-based traversal.

Implements the ``local://`` protocol referenced in Runfile resources.
Uses generators to avoid loading entire directory trees into memory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Generator, Optional

__all__ = ["FileGateway"]


class FileGateway:
    """Stream files from a local directory tree.

    Parameters
    ----------
    base_path:
        Root directory to traverse.  Must exist.

    Raises
    ------
    FileNotFoundError
        If *base_path* does not exist.
    """

    def __init__(self, base_path: str) -> None:
        self.base_path = Path(base_path)
        if not self.base_path.exists():
            raise FileNotFoundError(f"Gateway 根路径不存在: {base_path}")

    def stream_files(
        self, pattern: str = "**/*.*"
    ) -> Generator[Dict[str, Any], None, None]:
        """Yield file metadata and content matching *pattern*.

        Each yield is a dict with keys: ``file_name``, ``relative_path``,
        ``content``, ``size``.
        """
        # Path traversal protection: ensure resolved paths stay within base
        base_resolved = self.base_path.resolve()
        for file_path in sorted(self.base_path.glob(pattern)):
            resolved = file_path.resolve()
            if not resolved.is_relative_to(base_resolved):
                continue  # skip files outside base directory
            if file_path.is_file():
                try:
                    content = file_path.read_text(encoding="utf-8")
                    yield {
                        "file_name": file_path.name,
                        "relative_path": str(file_path.relative_to(self.base_path)),
                        "content": content,
                        "size": file_path.stat().st_size,
                    }
                except (UnicodeDecodeError, PermissionError) as exc:
                    # Skip unreadable files rather than crashing the stream.
                    yield {
                        "file_name": file_path.name,
                        "relative_path": str(file_path.relative_to(self.base_path)),
                        "content": None,
                        "size": file_path.stat().st_size,
                        "error": str(exc),
                    }

    def save_result(
        self,
        relative_path: str,
        content: str,
        suffix: str = ".refined",
        output_dir: Optional[str] = None,
    ) -> Path:
        """Write processed content back to disk.

        Parameters
        ----------
        relative_path:
            Path relative to ``base_path`` (or *output_dir* if given).
        content:
            Text to write.
        suffix:
            Appended to the original filename.
        output_dir:
            Override output root (defaults to ``base_path``).

        Returns
        -------
        Path
            The written file path.
        """
        root = Path(output_dir) if output_dir else self.base_path
        out = (root / f"{relative_path}{suffix}").resolve()
        root_resolved = root.resolve()
        if not str(out).startswith(str(root_resolved)):
            raise ValueError(f"Path traversal detected: {relative_path}")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content, encoding="utf-8")
        return out
