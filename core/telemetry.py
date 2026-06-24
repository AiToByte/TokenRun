"""
Telemetry Manager — event broadcasting for real-time monitoring.

Collects execution events and broadcasts them to connected consumers.
In CLI mode, logs to console via Rich.  In web mode, broadcasts via
WebSocket (future).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

__all__ = ["TelemetryEvent", "TelemetryManager"]


@dataclass
class TelemetryEvent:
    """A single telemetry event."""

    event_type: str  # "STATUS_UPDATE" | "TRACE_EVENT" | "APPROVAL_REQUIRED" | "ERROR"
    task_id: str
    timestamp: float = field(default_factory=time.time)
    data: Dict[str, Any] = field(default_factory=dict)


class TelemetryManager:
    """Collect and broadcast execution events.

    Supports registering callback handlers for real-time event
    consumption (CLI logging, WebSocket push, etc.).

    Telemetry levels:
        L1 — Progress + exceptions only (default, low bandwidth)
        L2 — Node-level detail (when user inspects a specific node)
        L3 — Full trace (every iteration, high bandwidth)

    Usage::

        telemetry = TelemetryManager()
        telemetry.on_event(lambda e: print(e.event_type, e.data))
        telemetry.emit("TRACE_EVENT", "task-1", {"output": "..."})
    """

    def __init__(self, level: int = 1) -> None:
        self._handlers: List[Callable[[TelemetryEvent], None]] = []
        self._event_log: List[TelemetryEvent] = []
        self.level = level  # 1=L1, 2=L2, 3=L3

    def on_event(self, handler: Callable[[TelemetryEvent], None]) -> None:
        """Register an event handler callback."""
        self._handlers.append(handler)

    def set_level(self, level: int) -> None:
        """Change the telemetry level at runtime."""
        self.level = max(1, min(3, level))

    def emit(
        self,
        event_type: str,
        task_id: str,
        data: Optional[Dict[str, Any]] = None,
        level: int = 1,
    ) -> Optional[TelemetryEvent]:
        """Emit a telemetry event to all registered handlers.

        Parameters
        ----------
        event_type:
            Event type string.
        task_id:
            Mission or task identifier.
        data:
            Event payload.
        level:
            Telemetry level (1=L1, 2=L2, 3=L3).
            Only emits if ``level <= self.level``.
        """
        if level > self.level:
            return None

        event = TelemetryEvent(
            event_type=event_type,
            task_id=task_id,
            data=data or {},
        )
        self._event_log.append(event)
        for handler in self._handlers:
            try:
                handler(event)
            except Exception as exc:
                import warnings

                warnings.warn(f"Telemetry handler error: {exc}")
        return event

    def emit_step(
        self,
        task_id: str,
        node_id: str,
        iteration: int,
        passed: bool,
        score: float,
        output_preview: str = "",
    ) -> Optional[TelemetryEvent]:
        """Convenience: emit a TRACE_EVENT for a single loop iteration (L3)."""
        return self.emit(
            "TRACE_EVENT",
            task_id,
            {
                "node_id": node_id,
                "iteration": iteration,
                "passed": passed,
                "score": score,
                "output_preview": output_preview[:200],
            },
            level=3,
        )

    def emit_status(
        self,
        task_id: str,
        phase: str,
        progress: float = 0.0,
        cost_usd: float = 0.0,
    ) -> Optional[TelemetryEvent]:
        """Convenience: emit a STATUS_UPDATE event (L1)."""
        return self.emit(
            "STATUS_UPDATE",
            task_id,
            {
                "phase": phase,
                "progress": progress,
                "cost_usd": cost_usd,
            },
        )

    def emit_sample_report(
        self,
        task_id: str,
        report: Dict[str, Any],
    ) -> TelemetryEvent:
        """Convenience: emit an APPROVAL_REQUIRED event with sampling report."""
        return self.emit("APPROVAL_REQUIRED", task_id, {"report": report})

    def get_log(self) -> List[TelemetryEvent]:
        """Return all recorded events."""
        return list(self._event_log)

    def clear_log(self) -> None:
        """Clear the event log."""
        self._event_log.clear()
