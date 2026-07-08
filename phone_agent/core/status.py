"""
StatusReporter — lightweight structured event dispatcher for observability.

Provides CLI-visible feedback about memory system availability, preference
usage, clarification decisions, and SpecGuard actions. Supports an optional
callback for webui integration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class StatusEvent:
    """A single status event emitted by a subsystem."""

    source: str     # "memory", "clarify", "spec_guard", "graph", "agent"
    level: str      # "info", "warn", "action"
    message: str
    details: dict[str, Any] = field(default_factory=dict)


# Level → prefix mapping for CLI output
_LEVEL_PREFIX: dict[str, str] = {
    "info": "i",
    "warn": "!",
    "action": "*",
}


class StatusReporter:
    """Collects and dispatches status events.

    Args:
        callback: Optional external handler (e.g. webui status panel).
        verbose: Whether to print events to stdout.
    """

    def __init__(
        self,
        callback: Callable[[StatusEvent], None] | None = None,
        verbose: bool = True,
    ) -> None:
        self._callback = callback
        self._verbose = verbose
        self._events: list[StatusEvent] = []

    def emit(
        self,
        source: str,
        level: str,
        message: str,
        **details: Any,
    ) -> None:
        """Emit a status event.

        Args:
            source: Subsystem name (memory, clarify, spec_guard, graph, agent).
            level: Severity (info, warn, action).
            message: Human-readable message.
            **details: Arbitrary key-value metadata.
        """
        event = StatusEvent(
            source=source,
            level=level,
            message=message,
            details=details,
        )
        self._events.append(event)
        if self._verbose:
            self._print(event)
        if self._callback:
            try:
                self._callback(event)
            except Exception:
                pass  # Never let a callback crash the agent

    @property
    def events(self) -> list[StatusEvent]:
        """All events emitted in this session."""
        return list(self._events)

    def clear(self) -> None:
        """Clear recorded events (e.g. between tasks)."""
        self._events.clear()

    @staticmethod
    def _print(event: StatusEvent) -> None:
        prefix = _LEVEL_PREFIX.get(event.level, "?")
        print(f"[{prefix}] [{event.source}] {event.message}")
