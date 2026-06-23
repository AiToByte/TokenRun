"""
SQL Gateway — read data from relational databases.

Requires ``sqlalchemy`` to be installed.  Supports any SQLAlchemy-compatible
database (PostgreSQL, MySQL, SQLite, etc.).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

__all__ = ["SQLGateway"]


class SQLGateway:
    """Execute queries and stream results from a SQL database.

    Parameters
    ----------
    connection_string:
        SQLAlchemy connection string.
        Examples: ``sqlite:///data.db``,
        ``postgresql://user:pass@localhost/mydb``.
    """

    def __init__(self, connection_string: str) -> None:
        try:
            from sqlalchemy import create_engine
        except ImportError:
            raise ImportError(
                "SQLGateway requires sqlalchemy. "
                "Install with: pip install sqlalchemy"
            )
        self._engine = create_engine(connection_string)

    def query(
        self,
        sql: str,
        params: Optional[Dict[str, Any]] = None,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """Execute a SELECT query and return rows as dicts.

        Parameters
        ----------
        sql:
            SQL query string (use ``:param`` for parameterized queries).
        params:
            Query parameters.
        limit:
            Maximum rows to return.
        """
        from sqlalchemy import text

        with self._engine.connect() as conn:
            result = conn.execute(text(sql), params or {})
            columns = list(result.keys())
            rows = []
            for i, row in enumerate(result):
                if i >= limit:
                    break
                rows.append(dict(zip(columns, row)))
            return rows

    def stream_rows(
        self,
        sql: str,
        params: Optional[Dict[str, Any]] = None,
        batch_size: int = 100,
    ) -> List[str]:
        """Execute a query and return results as a flat list of JSON strings.

        Each row is serialized to a JSON string, suitable for feeding
        into the TokenRun pipeline.
        """
        import json
        from sqlalchemy import text

        rows = self.query(sql, params, limit=10000)
        return [json.dumps(row, ensure_ascii=False, default=str) for row in rows]

    def get_table_info(self, table_name: str) -> Dict[str, Any]:
        """Return column metadata for a table."""
        from sqlalchemy import inspect

        inspector = inspect(self._engine)
        columns = inspector.get_columns(table_name)
        return {
            "table": table_name,
            "columns": [
                {"name": c["name"], "type": str(c["type"])}
                for c in columns
            ],
        }
