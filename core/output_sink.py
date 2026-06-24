"""
Output Sink — configurable output destinations for processed data.

Supports writing results to files, DuckDB, or webhook endpoints.
Configured via the Runfile's ``output_sink`` field.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

__all__ = ["OutputSink", "FileSink", "DuckDBSink", "VectorSink", "create_sink"]


class OutputSink:
    """Base class for output destinations."""

    def write(self, items: List[Dict[str, Any]]) -> None:
        """Write a batch of result items to the sink."""
        raise NotImplementedError

    def close(self) -> None:
        """Close the sink and release resources."""
        pass


class FileSink(OutputSink):
    """Write results to local files.

    Parameters
    ----------
    output_dir:
        Directory to write output files.
    suffix:
        File suffix (default ``.jsonl``).
    """

    def __init__(self, output_dir: str = "output", suffix: str = ".jsonl") -> None:
        self.output_dir = Path(output_dir)
        self.suffix = suffix
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write(self, items: List[Dict[str, Any]]) -> None:
        """Write items as JSONL to the output directory."""
        output_file = self.output_dir / f"results{self.suffix}"
        with open(output_file, "a", encoding="utf-8") as f:
            for item in items:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")


class DuckDBSink(OutputSink):
    """Write results to a DuckDB database.

    Parameters
    ----------
    db_path:
        Path to the DuckDB database file.
    table_name:
        Table name to write to.
    """

    def __init__(self, db_path: str = ":memory:", table_name: str = "results") -> None:
        try:
            import duckdb
        except ImportError:
            raise ImportError(
                "DuckDBSink requires duckdb. Install with: pip install duckdb"
            )
        self._conn = duckdb.connect(db_path)
        self._table_name = table_name
        self._created = False

    def write(self, items: List[Dict[str, Any]]) -> None:
        """Write items to DuckDB table."""
        if not items:
            return

        import json

        # Create table from first item's keys
        if not self._created:
            keys = list(items[0].keys())
            cols = ", ".join(f'"{k}" TEXT' for k in keys)
            self._conn.execute(
                f'CREATE TABLE IF NOT EXISTS "{self._table_name}" ({cols})'
            )
            self._created = True

        # Insert items
        for item in items:
            values = [
                json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
                for v in item.values()
            ]
            placeholders = ", ".join("?" for _ in values)
            cols = ", ".join(f'"{k}"' for k in item.keys())
            self._conn.execute(
                f'INSERT INTO "{self._table_name}" ({cols}) VALUES ({placeholders})',
                values,
            )

    def close(self) -> None:
        """Close the DuckDB connection."""
        self._conn.close()


class VectorSink(OutputSink):
    """Write results to a vector database for RAG applications.

    Parameters
    ----------
    collection_name:
        Name of the vector collection/table.
    backend:
        ``"chroma"`` (default) or ``"qdrant"``.
    host:
        Database host (for remote connections).
    """

    def __init__(
        self,
        collection_name: str = "tokenrun_results",
        backend: str = "chroma",
        host: Optional[str] = None,
    ) -> None:
        self.collection_name = collection_name
        self.backend = backend
        self.host = host
        self._client = None
        self._collection = None

    def _ensure_client(self) -> None:
        """Lazy-initialize the vector DB client."""
        if self._client is not None:
            return

        if self.backend == "chroma":
            try:
                import chromadb
            except ImportError:
                raise ImportError(
                    "VectorSink requires chromadb. Install with: pip install chromadb"
                )
            if self.host:
                self._client = chromadb.HttpClient(host=self.host)
            else:
                self._client = chromadb.Client()
            self._collection = self._client.get_or_create_collection(
                self.collection_name
            )
        else:
            raise ValueError(f"Unsupported vector backend: {self.backend}")

    def write(self, items: List[Dict[str, Any]]) -> None:
        """Write items to the vector database.

        Each item's ``output`` field is stored as the document.
        Metadata is preserved for filtering.
        """
        if not items:
            return

        self._ensure_client()

        ids = []
        documents = []
        metadatas = []

        for i, item in enumerate(items):
            doc_id = f"doc-{int(time.time())}-{i}"
            content = item.get("output") or item.get("content") or json.dumps(item)
            metadata = {
                k: str(v)
                for k, v in item.items()
                if k not in ("output", "content") and not isinstance(v, (dict, list))
            }
            ids.append(doc_id)
            documents.append(content)
            metadatas.append(metadata)

        if self._collection:
            self._collection.add(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
            )

    def close(self) -> None:
        """Close the vector DB connection."""
        if self._client and hasattr(self._client, "close"):
            self._client.close()


def create_sink(config: Optional[Dict[str, Any]]) -> Optional[OutputSink]:
    """Create an OutputSink from a Runfile configuration dict.

    Parameters
    ----------
    config:
        Dict with ``type`` key and sink-specific parameters.
        Supported types: ``file``, ``duckdb``, ``vector``.
    """
    if not config:
        return None

    sink_type = config.get("type", "file")
    if sink_type == "file":
        return FileSink(
            output_dir=config.get("output_dir", "output"),
            suffix=config.get("suffix", ".jsonl"),
        )
    elif sink_type == "duckdb":
        return DuckDBSink(
            db_path=config.get("db_path", ":memory:"),
            table_name=config.get("table_name", "results"),
        )
    elif sink_type == "vector":
        return VectorSink(
            collection_name=config.get("collection_name", "tokenrun_results"),
            backend=config.get("backend", "chroma"),
            host=config.get("host"),
        )
    else:
        raise ValueError(f"Unsupported output sink type: {sink_type}")
