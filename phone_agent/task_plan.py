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
    step_id: int = 0  # stable id used by milestone revisions

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
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

    MAX_STEPS = 12

    def apply_revision(
        self,
        completed_step_ids: list[int],
        revised_subtasks: list[dict[str, Any]],
    ) -> str:
        """Atomically apply a milestone revision from the supervisor.

        done/skipped steps keep their original objects; the pending tail is
        rebuilt from ``revised_subtasks`` (empty list = keep pending steps
        unchanged). Validation failures reject the whole revision.

        Returns a human-readable change summary for the revision log.
        """
        completed = set(int(i) for i in (completed_step_ids or []))

        # Build the new list in local state first — atomic swap at the end.
        kept: list[PlanStep] = []
        marked_done = 0
        for step in self.steps:
            if step.status in ("done", "skipped"):
                kept.append(step)
            elif step.step_id in completed:
                step.status = "done"
                marked_done += 1
                kept.append(step)

        new_tail: list[PlanStep] = []
        if revised_subtasks:
            next_id = max((s.step_id for s in self.steps), default=0) + 1
            for item in revised_subtasks:
                if not isinstance(item, dict):
                    continue
                desc = str(item.get("description") or "").strip()
                if not desc:
                    continue
                new_tail.append(PlanStep(
                    description=desc,
                    target_page=str(item.get("target_page") or ""),
                    step_id=next_id,
                ))
                next_id += 1

        if new_tail:
            new_steps = (kept + new_tail)[: self.MAX_STEPS]
            note = f"revised:{len(new_tail)}条"
        else:
            # No (valid) revision — keep the original pending tail
            pending_tail = [
                s for s in self.steps
                if s not in kept and s.status in ("pending", "current")
            ]
            new_steps = kept + pending_tail
            note = "revised:rejected(empty)" if revised_subtasks else "revised:0"

        # Re-point the cursor at the first non-finished step
        new_index = len(new_steps)
        for i, step in enumerate(new_steps):
            if step.status not in ("done", "skipped"):
                new_index = i
                break
        for step in new_steps[new_index:]:
            if step.status == "current":
                step.status = "pending"
        if new_index < len(new_steps):
            new_steps[new_index].status = "current"

        self.steps = new_steps
        self.current_index = new_index
        return f"done:+{marked_done} {note}"

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
        for idx, item in enumerate(raw_steps, start=1):
            if isinstance(item, dict):
                steps.append(PlanStep(
                    description=item.get("description", str(item)),
                    target_page=item.get("target_page", ""),
                    step_id=idx,
                ))
            elif isinstance(item, str):
                steps.append(PlanStep(description=item, step_id=idx))
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
