"""Trajectory loading and observation extraction for offline lifecycle replay.

A saved trajectory records, per step, the page the agent SAW when it chose the
action (``page_type``) and the action it took. The page that *resulted* from that
action is the ``page_type`` of the **next** step. So one observation is the
adjacent-step pair::

    observation[i] = (page_type[i], action[i], page_type[i+1])

The final step is always a terminal answer/unknown action with no successor, so
it never becomes a transition source.

Ground-truth note: ``observed_target_page_type`` comes from the trajectory (what
the agent actually observed on the device), which is *exogenous* to the spatial
graph. This is exactly what lets the replay measure lifecycle behavior without
the "test the graph with the graph" circularity.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Page-type labels that carry no usable information for a transition endpoint.
_EMPTY_PAGE_TYPES = frozenset({"", "unknown", "none", "null"})

# Terminal actions that end a task — never a real GUI transition source.
_TERMINAL_ACTIONS = frozenset({"unknown", "answer", "terminate", "finish", "done"})


def derive_action_type(action: dict[str, Any]) -> str:
    """Mirror ``SpatialGraphMemory.record_observation``'s action_type derivation."""
    return str(action.get("action_type") or action.get("action") or "unknown")


def derive_action_target(action: dict[str, Any]) -> str:
    """Mirror ``record_observation``'s lifecycle action_target derivation.

    NB: ``record_observation`` (unlike ``TransitionEdge.from_action``) does NOT
    fall back to ``text`` — so Type/Swipe/Launch actions key by an EMPTY target
    and merge across observations, while Tap keys by raw ``element`` coordinates
    and almost never merges. This asymmetry is central to the data-gap finding.
    """
    return str(
        action.get("semantic_target")
        or action.get("target")
        or action.get("element")
        or ""
    )


@dataclass(frozen=True)
class Observation:
    """A single (source -> action -> observed target) transition from a trajectory."""

    traj_id: str
    step_index: int
    app: str
    source_page_type: str
    observed_target_page_type: str
    action: dict[str, Any] = field(default_factory=dict)

    @property
    def action_type(self) -> str:
        return derive_action_type(self.action)

    @property
    def action_target(self) -> str:
        return derive_action_target(self.action)

    @property
    def is_self_loop(self) -> bool:
        return self.source_page_type == self.observed_target_page_type

    @property
    def is_coordinate_keyed(self) -> bool:
        """True when the lifecycle key uses raw coordinates (so it won't merge)."""
        has_semantic = bool(self.action.get("semantic_target") or self.action.get("target"))
        return ("element" in self.action) and not has_semantic


@dataclass(frozen=True)
class Trajectory:
    """A saved task run loaded from ``memory_db/.../trajectories/*.json``."""

    traj_id: str
    task: str
    success: bool
    apps: tuple[str, ...]
    n_steps: int
    steps: tuple[dict[str, Any], ...]

    @classmethod
    def from_dict(cls, traj_id: str, d: dict[str, Any]) -> "Trajectory":
        steps = tuple(d.get("step_details") or ())
        return cls(
            traj_id=traj_id,
            task=str(d.get("task") or ""),
            success=bool(d.get("success", False)),
            apps=tuple(str(a) for a in (d.get("apps") or ())),
            n_steps=int(d.get("steps") or len(steps)),
            steps=steps,
        )

    @classmethod
    def from_file(cls, path: str | Path) -> "Trajectory":
        p = Path(path)
        with p.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return cls.from_dict(p.stem, data)


def load_trajectories(directory: str | Path) -> list[Trajectory]:
    """Load every ``*.json`` trajectory in ``directory`` (sorted by filename)."""
    root = Path(directory)
    if not root.is_dir():
        raise FileNotFoundError(f"trajectory directory not found: {root}")
    return [Trajectory.from_file(p) for p in sorted(root.glob("*.json"))]


def _step_action(step: dict[str, Any]) -> dict[str, Any]:
    """Build the ``action`` dict that ``record_observation`` expects from a step."""
    return {"action_type": step.get("action_type"), **(step.get("action_params") or {})}


def _clean_page_type(value: Any) -> str:
    return str(value or "").strip()


def extract_observations(traj: Trajectory) -> list[Observation]:
    """Pair adjacent steps into cleaned transition observations.

    Drops an observation when either endpoint's ``page_type`` is empty/unknown,
    or when the source step is a terminal action. Self-loops (same source and
    target page type) are KEPT — they are real observations that
    ``record_observation`` would record on-device.
    """
    observations: list[Observation] = []
    steps = traj.steps
    for i in range(len(steps) - 1):
        cur, nxt = steps[i], steps[i + 1]
        source_pt = _clean_page_type(cur.get("page_type"))
        target_pt = _clean_page_type(nxt.get("page_type"))
        if source_pt.lower() in _EMPTY_PAGE_TYPES or target_pt.lower() in _EMPTY_PAGE_TYPES:
            continue
        action = _step_action(cur)
        if derive_action_type(action).lower() in _TERMINAL_ACTIONS:
            continue
        observations.append(
            Observation(
                traj_id=traj.traj_id,
                step_index=i,
                app=str(cur.get("app") or "").strip(),
                source_page_type=source_pt,
                observed_target_page_type=target_pt,
                action=action,
            )
        )
    return observations
