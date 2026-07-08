"""Replay observation streams into the real edge-lifecycle / promotion pipeline.

The replay constructs a real ``SpatialGraphMemory`` (with no graph_store, so it
stays 100% offline) and feeds each observation through ``record_observation`` —
exactly the call the on-device agent makes per step. It then reads the resulting
edge-lifecycle state back out.

Because the same ``record_observation`` path is exercised, what the replay shows
is what production would do given the same observation stream — including the
asymmetry that coordinate-keyed Tap edges almost never merge (so almost never
clear the sava N=3 bar) while empty-target Swipe/Type/Launch edges do.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from phone_agent.spatial.amsg_config import AMSGOptimConfig

from .trajectory import Observation


def _state_id(app: str, page_type: str) -> str:
    """Stable node id per (app, page_type) so repeated observations merge."""
    return f"{app}::{page_type}"


def _infer_label(config: AMSGOptimConfig) -> str:
    if config.edge_promotion_policy == "legacy":
        return "legacy"
    if config.min_verification_count == 3 and abs(config.outcome_dominance_threshold - 0.8) < 1e-9:
        return "sava"
    return f"verified_N{config.min_verification_count}_d{config.outcome_dominance_threshold:g}"


# ── snapshot data model ──────────────────────────────────────────────


@dataclass(frozen=True)
class EdgeStat:
    """One edge-lifecycle record after replay (keyed incl. observed target)."""

    source_page_type: str
    intent: str
    action_target: str
    target_page_type: str
    stage: str
    verification_count: int
    dominance_ratio: float
    risk_level: str
    outcome_counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class OutcomeStat:
    """One outcome distribution after replay (keyed by source + action only)."""

    source_page_type: str
    action_key: str
    outcomes: dict[str, int]
    total: int
    entropy: float
    dominant_outcome: str
    dominant_ratio: float

    @property
    def is_multimodal(self) -> bool:
        return len([c for c in self.outcomes.values() if c > 0]) > 1


@dataclass(frozen=True)
class ReplaySnapshot:
    """Immutable view of the lifecycle state produced by one replay."""

    config_name: str
    n_observations: int
    stage_counts: dict[str, int]
    edges: tuple[EdgeStat, ...]
    outcome_dists: tuple[OutcomeStat, ...]

    @property
    def n_edges(self) -> int:
        return len(self.edges)

    @property
    def n_promoted(self) -> int:
        return sum(1 for e in self.edges if e.stage == "promoted")

    @property
    def n_candidate(self) -> int:
        return sum(1 for e in self.edges if e.stage == "candidate")

    @property
    def n_hypothesis(self) -> int:
        return sum(1 for e in self.edges if e.stage == "hypothesis")

    @property
    def promoted_edges(self) -> tuple[EdgeStat, ...]:
        return tuple(e for e in self.edges if e.stage == "promoted")

    @property
    def promoted_cross_page(self) -> tuple[EdgeStat, ...]:
        """Promoted edges that are real navigation (source != target)."""
        return tuple(e for e in self.promoted_edges if e.source_page_type != e.target_page_type)

    @property
    def promoted_self_loops(self) -> tuple[EdgeStat, ...]:
        """Promoted edges that stay on the same page (degenerate, non-navigational)."""
        return tuple(e for e in self.promoted_edges if e.source_page_type == e.target_page_type)

    @property
    def multimodal_dists(self) -> tuple["OutcomeStat", ...]:
        return tuple(d for d in self.outcome_dists if d.is_multimodal)

    @property
    def vc_histogram(self) -> dict[int, int]:
        return dict(sorted(Counter(e.verification_count for e in self.edges).items()))

    @property
    def n_multimodal(self) -> int:
        return sum(1 for d in self.outcome_dists if d.is_multimodal)

    def stage_by_intent(self) -> dict[str, Counter]:
        """{intent: Counter(stage -> n)} — surfaces the coord-vs-mergeable split."""
        out: dict[str, Counter] = {}
        for e in self.edges:
            out.setdefault(e.intent, Counter())[e.stage] += 1
        return out


# ── core replay ──────────────────────────────────────────────────────


def _new_memory(config: AMSGOptimConfig):
    from phone_agent.memory.spatial_graph_memory import SpatialGraphMemory

    return SpatialGraphMemory(graph_store=None, config=config)


def _feed(mem, observations: list[Observation], *, verify: bool, advance_step: bool) -> None:
    from phone_agent.memory.spatial_graph_memory import PageState

    outcome = "success" if verify else "unverified"
    for obs in observations:
        before = PageState(
            state_id=_state_id(obs.app, obs.source_page_type),
            app=obs.app,
            page_type=obs.source_page_type,
        )
        after = PageState(
            state_id=_state_id(obs.app, obs.observed_target_page_type),
            app=obs.app,
            page_type=obs.observed_target_page_type,
        )
        mem.record_observation(before, obs.action, after, outcome=outcome)
        if advance_step:
            mem._edge_lifecycle.advance_step()


def _snapshot(mem, label: str, n_obs: int) -> ReplaySnapshot:
    records, outcomes = mem._edge_lifecycle.bulk_export()
    edges = tuple(
        EdgeStat(
            source_page_type=r["source_page_type"],
            intent=r["intent"],
            action_target=r["action_target"],
            target_page_type=r["target_page_type"],
            stage=r["stage"],
            verification_count=r["verification_count"],
            dominance_ratio=r["dominance_ratio"],
            risk_level=r.get("risk_level", "normal"),
            outcome_counts=dict(r.get("outcome_counts") or {}),
        )
        for r in records
    )
    dists = tuple(
        OutcomeStat(
            source_page_type=d["source_page_type"],
            action_key=d["action_key"],
            outcomes=dict(d.get("outcomes") or {}),
            total=d.get("total", 0),
            entropy=d.get("entropy", 0.0),
            dominant_outcome=d.get("dominant_outcome", ""),
            dominant_ratio=d.get("dominant_ratio", 0.0),
        )
        for d in outcomes
    )
    return ReplaySnapshot(
        config_name=label,
        n_observations=n_obs,
        stage_counts=dict(mem._edge_lifecycle.lifecycle_summary()),
        edges=edges,
        outcome_dists=dists,
    )


def replay(
    observations: list[Observation],
    config: AMSGOptimConfig,
    *,
    label: str | None = None,
    verify: bool = True,
    advance_step: bool = True,
) -> ReplaySnapshot:
    """Replay observations into a fresh memory under ``config`` and snapshot it.

    ``verify=False`` is the "no postcondition verification" ablation (F2): the
    transition is staged structurally but casts no lifecycle vote, so nothing can
    promote — the mechanism has nothing to learn from.
    """
    mem = _new_memory(config)
    _feed(mem, observations, verify=verify, advance_step=advance_step)
    return _snapshot(mem, label or _infer_label(config), len(observations))


def compare_configs(observations: list[Observation]) -> dict[str, ReplaySnapshot]:
    """sava (verified, N=3) vs legacy (promote-on-first-write) on identical input."""
    return {
        "sava": replay(observations, AMSGOptimConfig.sava(), label="sava"),
        "legacy": replay(observations, AMSGOptimConfig.legacy(), label="legacy"),
    }


def n_sensitivity(
    observations: list[Observation],
    ns: tuple[int, ...] = (1, 3, 5),
    *,
    dominance: float = 0.8,
) -> dict[int, int]:
    """How many edges would be promotable at each min_verification_count N.

    DESCRIPTIVE diagnostic on this corpus only — NOT a generalizable claim. With
    an all-success corpus the binding constraint is N (dominance is trivially 1.0
    for single-peaked edges), so this shows how thin the corpus is.
    """
    out: dict[int, int] = {}
    for n in ns:
        cfg = AMSGOptimConfig(
            edge_promotion_policy="verified",
            min_verification_count=n,
            outcome_dominance_threshold=dominance,
            enable_heuristic_injection=False,
        )
        snap = replay(observations, cfg, label=f"N={n}")
        out[n] = snap.n_promoted
    return out


# ── P0-C recovery check on real coordinates ──────────────────────────


@dataclass(frozen=True)
class P0CRecoveryResult:
    """Whether _build_lifecycle_dict recovers real vc for coordinate edges.

    The discriminating cases are coordinate edges with true vc >= 2: there the
    pre-fix default branch (vc=1) would be WRONG, so a correct recovery proves
    the P0-C raw-evidence fallback on real trajectory coordinates.
    """

    n_coord_edges: int
    n_recovered: int
    n_discriminating: int
    n_discriminating_recovered: int
    max_coord_vc: int

    @property
    def all_recovered(self) -> bool:
        return self.n_coord_edges > 0 and self.n_recovered == self.n_coord_edges

    @property
    def proven_on_real_data(self) -> bool:
        return self.n_discriminating > 0 and self.n_discriminating_recovered == self.n_discriminating


def verify_p0c_recovery(
    observations: list[Observation],
    config: AMSGOptimConfig | None = None,
) -> P0CRecoveryResult:
    from phone_agent.memory.spatial_graph_memory import PageState, TransitionEdge
    from phone_agent.spatial.edge_lifecycle import _edge_key

    config = config or AMSGOptimConfig.sava()
    mem = _new_memory(config)
    _feed(mem, observations, verify=True, advance_step=True)

    n_coord = n_recovered = n_disc = n_disc_recovered = max_vc = 0
    seen: set[tuple[str, str, str, str]] = set()
    for obs in observations:
        if not obs.is_coordinate_keyed:
            continue
        raw_target = obs.action_target  # e.g. "[389, 114]"
        key = (obs.app, obs.source_page_type, obs.observed_target_page_type, raw_target)
        if key in seen:
            continue
        seen.add(key)

        record = mem._edge_lifecycle.get_record(
            _edge_key(obs.source_page_type, obs.action_type, raw_target, obs.observed_target_page_type)
        )
        if record is None:
            continue
        true_vc = record.verification_count
        max_vc = max(max_vc, true_vc)
        n_coord += 1

        src = PageState(state_id=_state_id(obs.app, obs.source_page_type), app=obs.app, page_type=obs.source_page_type)
        tgt = PageState(
            state_id=_state_id(obs.app, obs.observed_target_page_type),
            app=obs.app,
            page_type=obs.observed_target_page_type,
        )
        base_edge = TransitionEdge(
            source_id=src.state_id,
            target_id=tgt.state_id,
            action_type=obs.action_type,
            action_target="",
            action_params=dict(obs.action),
            postcondition=obs.observed_target_page_type,
        )
        enriched = mem._enrich_edge_action_params(base_edge, src, tgt)
        enriched_edge = TransitionEdge(
            source_id=src.state_id,
            target_id=tgt.state_id,
            action_type=obs.action_type,
            action_target=str(enriched.get("semantic_target") or base_edge.action_target),
            action_params=enriched,
            postcondition=obs.observed_target_page_type,
        )
        lc = mem._build_lifecycle_dict(src, enriched_edge, tgt)
        recovered = lc["verification_count"] == true_vc
        n_recovered += int(recovered)
        if true_vc >= 2:
            n_disc += 1
            n_disc_recovered += int(recovered)
    return P0CRecoveryResult(
        n_coord_edges=n_coord,
        n_recovered=n_recovered,
        n_discriminating=n_disc,
        n_discriminating_recovered=n_disc_recovered,
        max_coord_vc=max_vc,
    )
