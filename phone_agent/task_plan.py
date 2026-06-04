"""Structured task plan for VLM-primary execution.

The VLM decomposes a user task into ordered sub-goals (PlanStep).
TaskPlan tracks progress across steps and renders status text for
injection into VLM context — so the VLM always knows where it is
in the overall task.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PlanStep:
    """A single sub-goal in the task plan."""

    description: str
    target_page: str = ""
    status: str = "pending"  # pending | current | done | skipped

    def to_dict(self) -> dict[str, str]:
        return {
            "description": self.description,
            "target_page": self.target_page,
            "status": self.status,
        }


@dataclass
class TaskPlan:
    """Ordered task decomposition with progress tracking.

    Created by ``_vlm_pre_plan()`` at task start.  Updated after each
    step based on the VLM's ``<summary>`` output or Fast Path completion.
    Rendered into VLM context via ``status_text()``.
    """

    original_task: str
    steps: list[PlanStep] = field(default_factory=list)
    current_index: int = 0
    goal_slots: dict[str, str] = field(default_factory=dict)

    def current_step(self) -> PlanStep | None:
        if 0 <= self.current_index < len(self.steps):
            return self.steps[self.current_index]
        return None

    def status_text(self) -> str:
        """Render plan with progress markers for VLM context injection."""
        lines = [f"任务: {self.original_task}"]
        for i, step in enumerate(self.steps):
            marker = {
                "done": "[done]",
                "current": "[current]",
                "pending": "[pending]",
                "skipped": "[skipped]",
            }.get(step.status, "[?]")
            lines.append(f"  {i + 1}. {marker} {step.description}")
        return "\n".join(lines)

    def try_advance(self, summary: str) -> None:
        """Mark current step done and advance to next.

        Called after a successful action — either from VLM ``<summary>``
        or from Fast Path completion description.
        """
        if self.current_index >= len(self.steps):
            return
        current = self.steps[self.current_index]
        current.status = "done"
        self.current_index += 1
        if self.current_index < len(self.steps):
            self.steps[self.current_index].status = "current"

    def is_complete(self) -> bool:
        return self.current_index >= len(self.steps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_task": self.original_task,
            "steps": [s.to_dict() for s in self.steps],
            "current_index": self.current_index,
            "goal_slots": dict(self.goal_slots),
        }

    @classmethod
    def from_vlm_output(
        cls,
        task: str,
        vlm_result: dict[str, Any],
    ) -> TaskPlan:
        """Build a TaskPlan from the VLM pre-plan JSON output.

        Expected ``vlm_result`` format::

            {
              "steps": [{"description": "...", "target_page": "..."}],
              "search_query": "...",
              "specs": {"color": "...", ...},
            }
        """
        raw_steps = vlm_result.get("steps", [])
        steps: list[PlanStep] = []
        for item in raw_steps:
            if isinstance(item, dict):
                steps.append(PlanStep(
                    description=item.get("description", str(item)),
                    target_page=item.get("target_page", ""),
                ))
            elif isinstance(item, str):
                steps.append(PlanStep(description=item))
        if steps:
            steps[0].status = "current"

        goal_slots: dict[str, str] = {}
        if vlm_result.get("search_query"):
            goal_slots["query"] = str(vlm_result["search_query"])
        if vlm_result.get("product"):
            goal_slots["product"] = str(vlm_result["product"])
        specs = vlm_result.get("specs")
        if isinstance(specs, dict):
            for k, v in specs.items():
                if v:
                    goal_slots[k] = str(v)

        return cls(
            original_task=task,
            steps=steps,
            goal_slots=goal_slots,
        )
