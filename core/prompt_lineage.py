"""
Prompt Lineage — version control for prompt templates.

Manages the evolution of prompts through user interventions (Edit & Resample).
Each modification creates a new ``PromptVersion`` with parent tracking,
enabling full lineage reconstruction and performance comparison.
"""

from __future__ import annotations

import hashlib
import time
from typing import Dict, List, Optional

from core.models import PromptVersion, TaskNode

__all__ = ["PromptLineageManager"]


class PromptLineageManager:
    """Manage prompt version creation and tracking.

    Usage::

        mgr = PromptLineageManager()
        v1 = mgr.register_initial(node, "Hello {{ data }}")
        v2 = mgr.create_version(node, "Hello {{ data }}, be concise", "Too verbose")
        history = mgr.get_history(node)
    """

    def register_initial(
        self,
        node: TaskNode,
        template: str,
    ) -> PromptVersion:
        """Register the initial prompt version (v1.0)."""
        version = PromptVersion(
            version_id="v1.0",
            hash=self._hash(template),
            template=template,
            parent_id=None,
            change_log="Initial version",
            timestamp=time.time(),
        )
        node.prompt_registry = [version]
        node.current_version_id = version.version_id
        return version

    def create_version(
        self,
        node: TaskNode,
        new_template: str,
        change_log: str = "",
        stats: Optional[Dict[str, Any]] = None,
    ) -> PromptVersion:
        """Create a new prompt version linked to the current one.

        Parameters
        ----------
        node:
            The task node whose prompt is being modified.
        new_template:
            The new prompt template text.
        change_log:
            Human-readable description of what changed and why.
        stats:
            Performance stats for this version (pass_rate, avg_score, etc.).
        """
        parent_id = node.current_version_id
        # Generate next version ID
        existing = [v.version_id for v in node.prompt_registry]
        next_num = len(existing) + 1
        version_id = f"v1.{next_num - 1}"  # v1.0, v1.1, v1.2, ...

        version = PromptVersion(
            version_id=version_id,
            hash=self._hash(new_template),
            template=new_template,
            parent_id=parent_id,
            change_log=change_log,
            stats=stats or {},
            timestamp=time.time(),
        )
        node.prompt_registry.append(version)
        node.current_version_id = version_id

        # Update the actual template on the node
        node.actor_prompt_template = new_template

        return version

    def get_current(self, node: TaskNode) -> Optional[PromptVersion]:
        """Return the currently active prompt version."""
        for v in node.prompt_registry:
            if v.version_id == node.current_version_id:
                return v
        return None

    def get_history(self, node: TaskNode) -> List[PromptVersion]:
        """Return all versions in chronological order."""
        return sorted(node.prompt_registry, key=lambda v: v.timestamp)

    def get_lineage_chain(self, node: TaskNode) -> List[PromptVersion]:
        """Return the lineage chain from root to current version."""
        by_id = {v.version_id: v for v in node.prompt_registry}
        chain: List[PromptVersion] = []
        current = self.get_current(node)
        while current:
            chain.append(current)
            current = by_id.get(current.parent_id) if current.parent_id else None
        chain.reverse()
        return chain

    def record_stats(
        self,
        node: TaskNode,
        version_id: str,
        stats: Dict[str, Any],
    ) -> None:
        """Update performance stats for a specific version."""
        for v in node.prompt_registry:
            if v.version_id == version_id:
                v.stats.update(stats)
                return

    @staticmethod
    def _hash(template: str) -> str:
        return hashlib.sha256(template.encode()).hexdigest()[:16]
