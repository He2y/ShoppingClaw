"""Offline experiment harness for AMSG (no real device required).

Replays saved trajectory observation streams into the (P0-C-fixed) edge
lifecycle / promotion pipeline to:

* validate the lifecycle accumulates real verification counts on a real
  observation stream (not the default ``@1`` branch);
* compare the ``sava`` (verified, N=3) vs ``legacy`` (promote-on-first-write)
  promotion policies on identical input;
* quantify the *data gap* — how far an all-success, N≈6 corpus can carry the
  RQ2 mechanism claims, and what failure / multi-modal samples real-device
  collection must still gather.

100% software, deterministic, and re-runnable.
"""

from __future__ import annotations

from .trajectory import (
    Observation,
    Trajectory,
    derive_action_target,
    derive_action_type,
    extract_observations,
    load_trajectories,
)

__all__ = [
    "Observation",
    "Trajectory",
    "derive_action_target",
    "derive_action_type",
    "extract_observations",
    "load_trajectories",
]
