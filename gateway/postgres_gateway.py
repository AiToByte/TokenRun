"""
PostgreSQL Gateway — async PostgreSQL access via asyncpg.

Requires ``asyncpg`` to be installed.
"""

from __future__ import annotations

from typing import Any, Dict, List

__all__ = ["PostgresGateway"]


class PostgresGateway:
    """Async PostgreSQL client for TokenRun data sources.

    Parameters
    ----------
    dsn:
        PostgreSQL connection string.
        Example: ``postgresql://user:pass@localhost/mydb``
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool = None

    async def connect(self) -> None:
        """Initialize the connection pool."""
        try:
            import asyncpg
        except ImportError:
            raise ImportError(
                "PostgresGateway requires asyncpg. Install with: pip install asyncpg"
            )
        self._pool = await asyncpg.create_pool(self._dsn)

    async def query(
        self, sql: str, *args: Any, limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """Execute a query and return rows as dicts."""
        if not self._pool:
            await self.connect()
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
            return [dict(row) for row in rows[:limit]]

    async def execute(self, sql: str, *args: Any) -> str:
        """Execute a statement (INSERT/UPDATE/DELETE)."""
        if not self._pool:
            await self.connect()
        async with self._pool.acquire() as conn:
            return await conn.execute(sql, *args)

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool:
            await self._pool.close()
