"""Exploration job generation driven by unverified functionality clusters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .core import stable_id
from .functionality_cluster import FunctionalityCluster


@dataclass(frozen=True)
class ExplorationJob:
    job_id: str
    functionality_cluster_id: str
    target_description: str
    reason: str
    allowed_regions: tuple[str, ...] = ()
    forbidden_actions: tuple[str, ...] = ()
    expected_postcondition: str = ""
    max_steps: int = 3
    priority: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "functionality_cluster_id": self.functionality_cluster_id,
            "target_description": self.target_description,
            "reason": self.reason,
            "allowed_regions": list(self.allowed_regions),
            "forbidden_actions": list(self.forbidden_actions),
            "expected_postcondition": self.expected_postcondition,
            "max_steps": self.max_steps,
            "priority": self.priority,
        }


@dataclass(frozen=True)
class FunctionalityFrontierWeights:
    functionality_novelty: float = 1.0
    cluster_uncertainty: float = 0.8
    unverified_affordance: float = 0.8
    graph_connectivity_gain: float = 0.5
    risk: float = 1.2
    repeat_failure: float = 0.9
    cost: float = 0.2


class ExplorationQueueBuilder:
    def __init__(self, weights: FunctionalityFrontierWeights | None = None):
        self.weights = weights or FunctionalityFrontierWeights()

    def build_jobs(self, clusters: list[FunctionalityCluster], *, limit: int = 20) -> list[ExplorationJob]:
        jobs = [self._job_for_cluster(cluster) for cluster in clusters if self._needs_exploration(cluster)]
        return sorted(jobs, key=lambda job: job.priority, reverse=True)[:limit]

    def _needs_exploration(self, cluster: FunctionalityCluster) -> bool:
        return cluster.risk_level != "high" and (cluster.success_count == 0 or not cluster.verified_edges)

    def _job_for_cluster(self, cluster: FunctionalityCluster) -> ExplorationJob:
        priority = score_cluster_frontier(cluster, self.weights)
        forbidden = ("submit_order", "payment", "confirm_address", "pay")
        return ExplorationJob(
            job_id=stable_id("explore_job", cluster.cluster_id, cluster.canonical_name),
            functionality_cluster_id=cluster.cluster_id,
            target_description=cluster.canonical_description or cluster.canonical_name,
            reason="unverified self-discovered functionality cluster",
            allowed_regions=cluster.regions,
            forbidden_actions=forbidden,
            expected_postcondition="",
            max_steps=3,
            priority=priority,
        )


def score_cluster_frontier(cluster: FunctionalityCluster, weights: FunctionalityFrontierWeights | None = None) -> float:
    weights = weights or FunctionalityFrontierWeights()
    attempts = cluster.success_count + cluster.fail_count
    uncertainty = 1.0 / (1 + attempts)
    unverified = 1.0 if cluster.success_count == 0 else 0.0
    connectivity_gain = 1.0 if not cluster.verified_edges else 0.2
    risk_penalty = {"normal": 0.0, "medium": 0.7, "high": 2.0}.get(cluster.risk_level, 0.4)
    repeat_failure = float(cluster.fail_count)
    cost = 1.0 + 0.1 * len(cluster.member_functionality_ids)
    score = (
        weights.functionality_novelty * cluster.novelty_score
        + weights.cluster_uncertainty * uncertainty
        + weights.unverified_affordance * unverified
        + weights.graph_connectivity_gain * connectivity_gain
        - weights.risk * risk_penalty
        - weights.repeat_failure * repeat_failure
        - weights.cost * cost
    )
    return round(score, 4)
