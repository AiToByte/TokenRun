"""
Task Persistence — SQLite-backed trace storage for checkpoint/resume.

Every iteration of every data item is persisted atomically.  On restart
the system can query which items have already been completed and skip
them, preventing duplicate API calls (and duplicate cost).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

__all__ = ["TaskPersistence"]


class TaskPersistence:
    """Store and retrieve execution traces in a local SQLite database.

    Parameters
    ----------
    db_path:
        Path to the SQLite file.  Created on first use.
    """

    def __init__(self, db_path: str = "logs/tokenrun_traces.db") -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save_trace(
        self,
        unit_id: str,
        input_hash: str,
        status: str,
        trace: Dict[str, Any],
        output: str = "",
    ) -> None:
        """Persist or update a single data item's execution trace.

        Uses ``INSERT OR REPLACE`` for idempotency — re-saving the same
        ``unit_id`` overwrites the previous row rather than duplicating.
        Thread-safe via an internal lock.
        """
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO task_traces
                        (id, input_hash, status, trace_data, final_output, updated_at)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (unit_id, input_hash, status, json.dumps(trace), output),
                )

    def get_status(self, unit_id: str) -> Optional[str]:
        """Return the current status of *unit_id*, or ``None`` if unseen."""
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.execute(
                    "SELECT status FROM task_traces WHERE id = ?", (unit_id,)
                )
                row = cur.fetchone()
                return row[0] if row else None

    def get_pending_ids(self, all_ids: List[str]) -> List[str]:
        """Filter *all_ids* to only those not yet completed."""
        if not all_ids:
            return []
        placeholders = ",".join("?" for _ in all_ids)
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.execute(
                    f"SELECT id FROM task_traces WHERE id IN ({placeholders}) "
                    "AND status = 'completed'",
                    all_ids,
                )
                done = {row[0] for row in cur.fetchall()}
        return [uid for uid in all_ids if uid not in done]

    def get_all_traces(self) -> List[Dict[str, Any]]:
        """Return all stored traces (for solidification / analysis)."""
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.execute("SELECT * FROM task_traces ORDER BY updated_at")
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS task_traces (
                    id TEXT PRIMARY KEY,
                    input_hash TEXT,
                    status TEXT,
                    trace_data TEXT,
                    final_output TEXT,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
