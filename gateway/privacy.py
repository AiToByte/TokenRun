"""
Privacy Redactor — reversible PII masking with local-only mapping.

Sensitive values are replaced with ``[[TR_{LABEL}_{N}]]`` placeholders
before text leaves the device.  The mapping table lives only in memory
and is destroyed when the task completes, so the cloud never sees real
values and has no key to reverse them.

For production use with crash recovery, use :class:`PersistentRedactor`
which persists the vault mapping to SQLite alongside task traces.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Dict, List, Optional

__all__ = ["PrivacyRedactor", "PersistentRedactor"]


# Pre-compiled patterns for common PII categories.
_DEFAULT_PATTERNS: Dict[str, re.Pattern] = {
    "EMAIL": re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"),
    "PHONE": re.compile(r"(?:\+?86)?1[3-9]\d{9}"),
    "ID_CARD": re.compile(r"\b\d{17}[\dXx]\b"),
    "IP_ADDR": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    "API_KEY": re.compile(r"sk-[a-zA-Z0-9]{20,}"),
}


class PrivacyRedactor:
    """Reversible PII masking using placeholder substitution.

    Usage::

        redactor = PrivacyRedactor()
        safe = redactor.mask("Contact: alice@example.com")
        # safe == "Contact: [[TR_EMAIL_1]]"

        original = redactor.unmask(safe)
        # original == "Contact: alice@example.com"

        redactor.clear_vault()  # wipe mapping from memory
    """

    def __init__(
        self,
        patterns: Optional[Dict[str, re.Pattern]] = None,
        rules: Optional[List[str]] = None,
    ) -> None:
        self._patterns = patterns or _DEFAULT_PATTERNS
        # Subset of pattern labels to apply (None = all).
        # Normalise rules to uppercase to match pattern keys.
        if rules:
            normalised = {r.upper() for r in rules}
            # Map common aliases from SecurityConfig.masking_rules
            aliases = {"EMAILS": "EMAIL", "API_KEYS": "API_KEY"}
            self._active_labels = {aliases.get(r, r) for r in normalised}
        else:
            self._active_labels = set(self._patterns.keys())
        self._vault: Dict[str, str] = {}
        self._reverse_vault: Dict[str, str] = {}  # value → placeholder
        self._counter: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def mask(self, text: str) -> str:
        """Replace sensitive values with reversible placeholders.

        All patterns are matched first, then replacements are applied
        in a single pass (longest match first) to avoid interference
        between overlapping patterns.
        """
        # Phase 1: collect all matches across all patterns
        all_matches: List[tuple[int, int, str, str]] = []  # (start, end, label, value)
        for label, pattern in self._patterns.items():
            if label not in self._active_labels:
                continue
            for match in pattern.finditer(text):
                all_matches.append((match.start(), match.end(), label, match.group()))

        if not all_matches:
            return text

        # Sort by start position, then by length descending (longest match wins)
        all_matches.sort(key=lambda m: (m[0], -(m[1] - m[0])))

        # Phase 2: resolve overlaps (keep non-overlapping matches)
        filtered: List[tuple[int, int, str, str]] = []
        last_end = 0
        for start, end, label, value in all_matches:
            if start >= last_end:
                filtered.append((start, end, label, value))
                last_end = end

        # Phase 3: apply replacements in reverse order (to preserve positions)
        result = text
        for start, end, label, value in reversed(filtered):
            placeholder = self._get_or_create_placeholder(label, value)
            result = result[:start] + placeholder + result[end:]

        return result

    def unmask(self, text: str) -> str:
        """Restore placeholders back to original values."""
        restored = text
        # Sort by placeholder length descending to avoid partial replacements.
        for placeholder, original in sorted(
            self._vault.items(), key=lambda kv: len(kv[0]), reverse=True
        ):
            restored = restored.replace(placeholder, original)
        return restored

    def clear_vault(self) -> None:
        """Erase the in-memory mapping table."""
        self._vault.clear()
        self._reverse_vault.clear()
        self._counter = 0

    @property
    def vault_size(self) -> int:
        """Number of unique sensitive values currently tracked."""
        return len(self._vault)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get_or_create_placeholder(self, label: str, value: str) -> str:
        """Return existing placeholder for *value*, or mint a new one."""
        existing = self._reverse_vault.get(value)
        if existing is not None:
            return existing
        self._counter += 1
        placeholder = f"[[TR_{label}_{self._counter}]]"
        self._vault[placeholder] = value
        self._reverse_vault[value] = placeholder
        return placeholder


# ---------------------------------------------------------------------------
# Persistent Redactor (crash-safe vault storage)
# ---------------------------------------------------------------------------


class PersistentRedactor(PrivacyRedactor):
    """Privacy redactor with SQLite-backed vault for crash recovery.

    The vault mapping is persisted to a ``privacy_vault`` table alongside
    task traces.  On restart after a crash, mappings can be restored so
    that previously masked text can still be unmasked.

    Parameters
    ----------
    db_path:
        Path to the SQLite database (default ``tokenrun_traces.db``).
    task_id:
        Identifier for the current task.  Used to namespace vault entries.
    patterns:
        Custom PII patterns (overrides defaults).
    rules:
        Subset of pattern labels to apply.
    """

    def __init__(
        self,
        db_path: str = "tokenrun_traces.db",
        task_id: str = "",
        patterns: Optional[Dict[str, re.Pattern]] = None,
        rules: Optional[List[str]] = None,
    ) -> None:
        super().__init__(patterns=patterns, rules=rules)
        self._db_path = db_path
        self._task_id = task_id
        self._pending_entries: List[tuple[str, str, str]] = []  # (placeholder, value, label)
        self._init_db()

    def _init_db(self) -> None:
        """Create the privacy_vault table if it doesn't exist."""
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS privacy_vault (
                    task_id    TEXT NOT NULL,
                    placeholder TEXT NOT NULL,
                    original_value TEXT NOT NULL,
                    label      TEXT NOT NULL,
                    PRIMARY KEY (task_id, placeholder)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_vault_task ON privacy_vault(task_id)"
            )
            conn.commit()
        finally:
            conn.close()

    def set_task_id(self, task_id: str) -> None:
        """Set the task ID for subsequent mask/unmask operations."""
        self._task_id = task_id

    def mask(self, text: str) -> str:
        """Mask PII and persist new mappings to SQLite."""
        result = super().mask(text)

        # Persist only newly-added vault entries
        if self._pending_entries and self._task_id:
            self._flush_pending()

        return result

    def _get_or_create_placeholder(self, label: str, value: str) -> str:
        """Override to track new entries for incremental persistence."""
        existing = self._reverse_vault.get(value)
        if existing is not None:
            return existing
        self._counter += 1
        placeholder = f"[[TR_{label}_{self._counter}]]"
        self._vault[placeholder] = value
        self._reverse_vault[value] = placeholder
        # Track for batch persistence
        self._pending_entries.append((placeholder, value, label))
        return placeholder

    def _flush_pending(self) -> None:
        """Write only newly-added entries to the database."""
        if not self._pending_entries:
            return
        conn = sqlite3.connect(self._db_path)
        try:
            for placeholder, original, label in self._pending_entries:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO privacy_vault
                        (task_id, placeholder, original_value, label)
                    VALUES (?, ?, ?, ?)
                    """,
                    (self._task_id, placeholder, original, label),
                )
            conn.commit()
            self._pending_entries.clear()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _persist_vault(self) -> None:
        """Write current vault entries to the database (full sync)."""
        conn = sqlite3.connect(self._db_path)
        try:
            for placeholder, original in self._vault.items():
                label = self._extract_label(placeholder)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO privacy_vault
                        (task_id, placeholder, original_value, label)
                    VALUES (?, ?, ?, ?)
                    """,
                    (self._task_id, placeholder, original, label),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _extract_label(placeholder: str) -> str:
        """Extract label from placeholder: [[TR_EMAIL_1]] → EMAIL."""
        if placeholder.startswith("[[TR_") and placeholder.endswith("]]"):
            # Remove [[TR_ prefix and ]] suffix → "EMAIL_1"
            inner = placeholder[5:-2]
            # rsplit to remove the numeric suffix → ["EMAIL", "1"]
            parts = inner.rsplit("_", 1)
            if len(parts) == 2:
                return parts[0]  # "EMAIL"
            return inner
        return "UNKNOWN"

    def restore_from_db(self, task_id: Optional[str] = None) -> int:
        """Restore vault mappings from the database.

        Parameters
        ----------
        task_id:
            Task ID to restore.  If None, uses the current ``self._task_id``.

        Returns
        -------
        int
            Number of entries restored.
        """
        tid = task_id or self._task_id
        if not tid:
            return 0

        conn = sqlite3.connect(self._db_path)
        try:
            cursor = conn.execute(
                "SELECT placeholder, original_value FROM privacy_vault WHERE task_id = ?",
                (tid,),
            )
            count = 0
            max_counter = self._counter
            for placeholder, original in cursor.fetchall():
                if placeholder not in self._vault:
                    self._vault[placeholder] = original
                    self._reverse_vault[original] = placeholder
                    count += 1
                    # Parse the counter from placeholder: [[TR_EMAIL_42]] → 42
                    num = self._parse_counter(placeholder)
                    if num > max_counter:
                        max_counter = num
            # Update counter to avoid collisions with restored entries
            if count > 0:
                self._counter = max_counter
            return count
        finally:
            conn.close()

    @staticmethod
    def _parse_counter(placeholder: str) -> int:
        """Extract the numeric counter from a placeholder string."""
        if placeholder.startswith("[[TR_") and placeholder.endswith("]]"):
            inner = placeholder[5:-2]
            parts = inner.rsplit("_", 1)
            if len(parts) == 2:
                try:
                    return int(parts[1])
                except ValueError:
                    pass
        return 0

    def clear_task_vault(self, task_id: Optional[str] = None) -> None:
        """Remove vault entries for a specific task from the database.

        Parameters
        ----------
        task_id:
            Task ID to clear.  If None, uses the current ``self._task_id``.
        """
        tid = task_id or self._task_id
        if not tid:
            return

        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute("DELETE FROM privacy_vault WHERE task_id = ?", (tid,))
            conn.commit()
        finally:
            conn.close()

    def clear_vault(self) -> None:
        """Clear both in-memory and persisted vault."""
        if self._task_id:
            self.clear_task_vault(self._task_id)
        super().clear_vault()

    def get_vault_stats(self) -> Dict[str, int]:
        """Return vault statistics.

        Returns
        -------
        dict
            Contains ``memory_size`` (in-memory entries) and
            ``db_size`` (persisted entries for current task).
        """
        memory_size = len(self._vault)
        db_size = 0
        if self._task_id:
            conn = sqlite3.connect(self._db_path)
            try:
                cursor = conn.execute(
                    "SELECT COUNT(*) FROM privacy_vault WHERE task_id = ?",
                    (self._task_id,),
                )
                db_size = cursor.fetchone()[0]
            finally:
                conn.close()
        return {"memory_size": memory_size, "db_size": db_size}
