"""
DuckDB Gateway — structured data analysis with DuckDB.

Requires ``duckdb`` to be installed.  Ideal for analytical queries
on large datasets without loading everything into memory.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

__all__ = ["DuckDBGateway"]

_SAFE_IDENTIFIER = re.compile(r"^[a-zA-Z_]\w*$")


class DuckDBGateway:
    """Execute analytical queries on structured data using DuckDB.

    Parameters
    ----------
    db_path:
        Path to the DuckDB database file.  "":memory:"" for in-memory.
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        try:
            import duckdb
        except ImportError:
            raise ImportError(
                "DuckDBGateway requires duckdb. Install with: pip install duckdb"
            )
        self._conn = duckdb.connect(db_path)

    def query(
        self, sql: str, params: Optional[List[Any]] = None
    ) -> List[Dict[str, Any]]:
        """Execute a SQL query and return results as a list of dicts."""
        result = self._conn.execute(sql, params or [])
        columns = [desc[0] for desc in result.description]
        return [dict(zip(columns, row)) for row in result.fetchall()]

    def register_csv(self, name: str, path: str) -> None:
        """Register a CSV file as a virtual table."""
        if not _SAFE_IDENTIFIER.match(name):
            raise ValueError(f"Invalid table name: {name}")
        safe_path = path.replace("'", "''")
        self._conn.execute(
            f'CREATE TABLE "{name}" AS SELECT * FROM read_csv_auto(?)',
            [safe_path],
        )

    def register_parquet(self, name: str, path: str) -> None:
        """Register a Parquet file as a virtual table."""
        if not _SAFE_IDENTIFIER.match(name):
            raise ValueError(f"Invalid table name: {name}")
        safe_path = path.replace("'", "''")
        self._conn.execute(
            f'CREATE TABLE "{name}" AS SELECT * FROM read_parquet(?)',
            [safe_path],
        )

    def close(self) -> None:
        """Close the connection."""
        self._conn.close()
