"""
StepRecord — unified step data model.

Merges KnowledgeBase.StepRecord + SessionMemory.StepSummary.
StepSummary is deleted — its 'summary' field was never read by VLM context.
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class StepRecord:
    """A single step's data, stored in unified state."""

    step: int
    action_type: str
    action_target: str = ""
    thinking_short: str = ""   # first 1-2 sentences (injected into context)
    thinking_full: str = ""    # complete reasoning (archived for retrieval)
    page_type: str = ""
    app: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def summary(self) -> str:
        """One-line summary for display/logging (not for VLM context)."""
        if self.action_target:
            return f"{self.action_type}({self.action_target})"
        return self.action_type
