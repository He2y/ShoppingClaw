"""Edge lifecycle management for AMSG verified graph construction.

Implements Definition 7 (Edge Lifecycle) and Definition 8 (Outcome
Distribution) from SYSTEM_DESIGN.md.

Core insight: in dynamic mobile GUIs the same action (e.g. "add to cart")
can produce multiple outcomes (spec dialog, login page, promotion popup,
no-op).  An edge must pass through a verification lifecycle before it is
promoted into the committed graph:

    hypothesis  →  candidate  →  promoted  →  (optional) demoted

This replaces the legacy pattern of writing every observed transition
directly into Neo4j, and eliminates hardcoded heuristic edge injection.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .amsg_config import AMSGOptimConfig


# ── Outcome Distribution (Definition 9) ─────────────────────────────


@dataclass(frozen=True)
class OutcomeDistribution:
    """Empirical distribution over target page types for a (source, action) pair.

    Example: pressing "加入购物车" on product_detail may yield::

        {"spec_selection": 8, "login": 2, "promotion_popup": 1}
    """

    source_page_type: str
    action_key: str
    outcomes: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.outcomes.values())

    @property
    def entropy(self) -> float:
        """Shannon entropy — high value means unpredictable transition."""
        total = self.total or 1
        return -sum(
            (c / total) * math.log(c / total + 1e-12)
            for c in self.outcomes.values()
        )

    @property
    def dominant_outcome(self) -> tuple[str, float]:
        """Returns (page_type, ratio) of the most frequent outcome."""
        if not self.outcomes:
            return ("", 0.0)
        best_key = max(self.outcomes, key=lambda k: self.outcomes[k])
        total = self.total or 1
        return (best_key, self.outcomes[best_key] / total)

    def with_outcome(self, target_page_type: str) -> OutcomeDistribution:
        """Return a new distribution with one more observation."""
        new_counts = dict(self.outcomes)
        new_counts[target_page_type] = new_counts.get(target_page_type, 0) + 1
        return OutcomeDistribution(
            source_page_type=self.source_page_type,
            action_key=self.action_key,
            outcomes=new_counts,
        )

    def to_dict(self) -> dict[str, Any]:
        dominant, ratio = self.dominant_outcome
        return {
            "source_page_type": self.source_page_type,
            "action_key": self.action_key,
            "outcomes": dict(self.outcomes),
            "total": self.total,
            "entropy": round(self.entropy, 4),
            "dominant_outcome": dominant,
            "dominant_ratio": round(ratio, 4),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> OutcomeDistribution:
        return cls(
            source_page_type=d.get("source_page_type", ""),
            action_key=d.get("action_key", ""),
            outcomes=dict(d.get("outcomes") or {}),
        )


# ── Edge Lifecycle Record (Definition 7) ────────────────────────────


LIFECYCLE_STAGES = ("hypothesis", "candidate", "promoted", "demoted")


@dataclass(frozen=True)
class EdgeLifecycleRecord:
    """Tracks a single directed edge through its promotion lifecycle."""

    edge_key: str  # "source_page_type|intent|action_target|target_page_type"
    stage: str  # one of LIFECYCLE_STAGES
    source_page_type: str
    target_page_type: str
    intent: str
    action_target: str

    outcome_counts: dict[str, int] = field(default_factory=dict)
    total_attempts: int = 0
    verification_count: int = 0
    dominant_outcome: str = ""
    dominance_ratio: float = 0.0

    risk_level: str = "normal"
    last_verified_step: int = 0
    created_step: int = 0

    def is_promotable(self, config: AMSGOptimConfig) -> bool:
        """Check whether this edge meets promotion criteria."""
        if config.edge_promotion_policy == "legacy":
            return True
        return (
            self.verification_count >= config.min_verification_count
            and self.dominance_ratio >= config.outcome_dominance_threshold
            and self.risk_level != "high"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge_key": self.edge_key,
            "stage": self.stage,
            "source_page_type": self.source_page_type,
            "target_page_type": self.target_page_type,
            "intent": self.intent,
            "action_target": self.action_target,
            "outcome_counts": dict(self.outcome_counts),
            "total_attempts": self.total_attempts,
            "verification_count": self.verification_count,
            "dominant_outcome": self.dominant_outcome,
            "dominance_ratio": round(self.dominance_ratio, 4),
            "risk_level": self.risk_level,
            "last_verified_step": self.last_verified_step,
            "created_step": self.created_step,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EdgeLifecycleRecord:
        return cls(
            edge_key=d.get("edge_key", ""),
            stage=d.get("stage", "hypothesis"),
            source_page_type=d.get("source_page_type", ""),
            target_page_type=d.get("target_page_type", ""),
            intent=d.get("intent", ""),
            action_target=d.get("action_target", ""),
            outcome_counts=dict(d.get("outcome_counts") or {}),
            total_attempts=d.get("total_attempts", 0),
            verification_count=d.get("verification_count", 0),
            dominant_outcome=d.get("dominant_outcome", ""),
            dominance_ratio=d.get("dominance_ratio", 0.0),
            risk_level=d.get("risk_level", "normal"),
            last_verified_step=d.get("last_verified_step", 0),
            created_step=d.get("created_step", 0),
        )


# ── Edge Lifecycle Manager ──────────────────────────────────────────


def _edge_key(source_page_type: str, intent: str, action_target: str, target_page_type: str) -> str:
    return "|".join([source_page_type, intent, action_target, target_page_type])


def _outcome_key(source_page_type: str, action_key: str) -> str:
    return f"{source_page_type}|{action_key}"


def _dominant(counts: dict[str, int]) -> tuple[str, float]:
    if not counts:
        return ("", 0.0)
    best_key = max(counts, key=lambda k: counts[k])
    total = sum(counts.values()) or 1
    return (best_key, counts[best_key] / total)


class EdgeLifecycleManager:
    """Manages edge promotion from hypothesis through verification to graph.

    Implements the lifecycle state machine (Definition 8) and maintains
    per-(source, action) outcome distributions (Definition 9).
    """

    def __init__(self, config: AMSGOptimConfig | None = None) -> None:
        self.config = config or AMSGOptimConfig.legacy()
        self._records: dict[str, EdgeLifecycleRecord] = {}
        self._outcomes: dict[str, OutcomeDistribution] = {}
        self._step_counter: int = 0

    # ── Core API ────────────────────────────────────────────────────

    def record_outcome(
        self,
        *,
        source_page_type: str,
        intent: str,
        action_target: str,
        observed_target: str,
        action_key: str = "",
        risk: str = "normal",
    ) -> EdgeLifecycleRecord:
        """Record what actually happened after executing an action.

        This is called by ``SpatialGraphMemory.record_observation()`` on
        every real-device execution result, whether success or failure.
        """
        action_key = action_key or f"{intent}:{action_target}"
        key = _edge_key(source_page_type, intent, action_target, observed_target)

        # Update outcome distribution for the (source, action) pair.
        okey = _outcome_key(source_page_type, action_key)
        dist = self._outcomes.get(okey)
        if dist is None:
            dist = OutcomeDistribution(source_page_type, action_key)
        self._outcomes[okey] = dist.with_outcome(observed_target)

        # Update or create edge lifecycle record.
        old = self._records.get(key)
        if old is None:
            old = EdgeLifecycleRecord(
                edge_key=key,
                stage="hypothesis",
                source_page_type=source_page_type,
                target_page_type=observed_target,
                intent=intent,
                action_target=action_target,
                created_step=self._step_counter,
                risk_level=risk,
            )

        new_counts = dict(old.outcome_counts)
        new_counts[observed_target] = new_counts.get(observed_target, 0) + 1
        dominant, dom_ratio = _dominant(new_counts)

        new_record = EdgeLifecycleRecord(
            edge_key=key,
            stage=self._compute_stage(old, new_counts, dom_ratio),
            source_page_type=source_page_type,
            target_page_type=observed_target,
            intent=intent,
            action_target=action_target,
            outcome_counts=new_counts,
            total_attempts=old.total_attempts + 1,
            verification_count=old.verification_count + 1,
            dominant_outcome=dominant,
            dominance_ratio=dom_ratio,
            risk_level=risk,
            last_verified_step=self._step_counter,
            created_step=old.created_step,
        )
        self._records[key] = new_record
        return new_record

    def advance_step(self) -> None:
        """Increment the global step counter (call once per agent step)."""
        self._step_counter += 1

    # ── Query API ───────────────────────────────────────────────────

    def get_promotable_edges(self) -> list[EdgeLifecycleRecord]:
        """Return all edges that meet promotion criteria."""
        return [r for r in self._records.values() if r.is_promotable(self.config)]

    def get_promoted_edges_for_page(self, source_page_type: str) -> list[EdgeLifecycleRecord]:
        """Return promoted edges originating from a specific page type."""
        return [
            r
            for r in self._records.values()
            if r.source_page_type == source_page_type and r.stage == "promoted"
        ]

    def get_outcome_entropy(self, source_page_type: str, action_key: str) -> float:
        """High entropy → agent should use VLM verification for this transition."""
        dist = self._outcomes.get(_outcome_key(source_page_type, action_key))
        return dist.entropy if dist else 0.0

    def get_outcome_distribution(self, source_page_type: str, action_key: str) -> OutcomeDistribution | None:
        """Return the full outcome distribution for a (source, action) pair."""
        return self._outcomes.get(_outcome_key(source_page_type, action_key))

    def requires_vlm_verification(self, source_page_type: str, action_key: str) -> bool:
        """Data-driven replacement for the hardcoded VLM-verify transition set."""
        entropy = self.get_outcome_entropy(source_page_type, action_key)
        return entropy > self.config.outcome_entropy_vlm_threshold

    def is_edge_promoted(self, edge_key: str) -> bool:
        """Check if a specific edge has been promoted."""
        record = self._records.get(edge_key)
        return record is not None and record.stage == "promoted"

    def get_record(self, edge_key: str) -> EdgeLifecycleRecord | None:
        return self._records.get(edge_key)

    # ── Metrics ─────────────────────────────────────────────────────

    def lifecycle_summary(self) -> dict[str, int]:
        """Count edges at each lifecycle stage."""
        counts: dict[str, int] = {stage: 0 for stage in LIFECYCLE_STAGES}
        for record in self._records.values():
            counts[record.stage] = counts.get(record.stage, 0) + 1
        counts["total_outcomes"] = sum(d.total for d in self._outcomes.values())
        return counts

    # ── Persistence ─────────────────────────────────────────────────

    def bulk_load(self, records: list[dict[str, Any]], outcomes: list[dict[str, Any]]) -> None:
        """Load serialized records and outcomes (from Neo4j or JSON).

        Merges with existing in-memory state: incoming records with higher
        verification_count win; outcome counts are summed.
        """
        for d in records:
            rec = EdgeLifecycleRecord.from_dict(d)
            existing = self._records.get(rec.edge_key)
            if existing is None or rec.verification_count > existing.verification_count:
                self._records[rec.edge_key] = rec
        for d in outcomes:
            dist = OutcomeDistribution.from_dict(d)
            okey = _outcome_key(dist.source_page_type, dist.action_key)
            existing = self._outcomes.get(okey)
            if existing is None:
                self._outcomes[okey] = dist
            else:
                merged_counts = dict(existing.outcomes)
                for k, v in dist.outcomes.items():
                    merged_counts[k] = max(merged_counts.get(k, 0), v)
                self._outcomes[okey] = OutcomeDistribution(
                    source_page_type=dist.source_page_type,
                    action_key=dist.action_key,
                    outcomes=merged_counts,
                )

    def bulk_export(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Export all records and outcomes as dicts for persistence."""
        return (
            [r.to_dict() for r in self._records.values()],
            [d.to_dict() for d in self._outcomes.values()],
        )

    def check_demotion(self, threshold: float = 0.4) -> list[EdgeLifecycleRecord]:
        """Demote promoted edges whose dominance has dropped below threshold."""
        demoted: list[EdgeLifecycleRecord] = []
        for key, record in list(self._records.items()):
            if record.stage != "promoted" or record.total_attempts < 2:
                continue
            if record.dominance_ratio < threshold:
                new_record = EdgeLifecycleRecord(
                    edge_key=record.edge_key,
                    stage="demoted",
                    source_page_type=record.source_page_type,
                    target_page_type=record.target_page_type,
                    intent=record.intent,
                    action_target=record.action_target,
                    outcome_counts=record.outcome_counts,
                    total_attempts=record.total_attempts,
                    verification_count=record.verification_count,
                    dominant_outcome=record.dominant_outcome,
                    dominance_ratio=record.dominance_ratio,
                    risk_level=record.risk_level,
                    last_verified_step=record.last_verified_step,
                    created_step=record.created_step,
                )
                self._records[key] = new_record
                demoted.append(new_record)
        return demoted

    # ── Internal ────────────────────────────────────────────────────

    def _compute_stage(
        self,
        old: EdgeLifecycleRecord,
        counts: dict[str, int],
        dom_ratio: float,
    ) -> str:
        if self.config.edge_promotion_policy == "legacy":
            return "promoted"

        total = sum(counts.values())
        if total < self.config.min_verification_count:
            return "hypothesis"
        if dom_ratio < self.config.outcome_dominance_threshold:
            return "candidate"  # chaotic — multiple outcomes, not reliable
        if old.risk_level == "high":
            return "candidate"  # high-risk edges need explicit gate
        return "promoted"
