"""
Privacy Redactor — reversible PII masking with local-only mapping.

Sensitive values are replaced with ``[[TR_{LABEL}_{N}]]`` placeholders
before text leaves the device.  The mapping table lives only in memory
and is destroyed when the task completes, so the cloud never sees real
values and has no key to reverse them.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

__all__ = ["PrivacyRedactor"]


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
