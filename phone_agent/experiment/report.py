"""Aggregate replay results into a data-gap report.

The report answers the question Option A exists for: *how far can an all-success,
N≈6 corpus carry the RQ2 mechanism claims, and what must real-device collection
still gather?* It is deliberately conservative — every claim is tagged supported
/ unsupported by THIS corpus, and the honest boundaries are spelled out.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .replay import (
    P0CRecoveryResult,
    ReplaySnapshot,
    compare_configs,
    n_sensitivity,
    verify_p0c_recovery,
)
from .trajectory import Observation, Trajectory, extract_observations

# sava preset constants (kept local so the report text stays in sync).
_SAVA_N = 3
_SAVA_ENTROPY_VLM = 0.5
_MIN_FAILURE_TRAJECTORIES = 10  # aligned with handoff P2.5 ask


@dataclass(frozen=True)
class CorpusStats:
    n_trajectories: int
    n_success: int
    n_failure: int
    n_trivial: int
    total_steps: int
    total_observations: int
    dropped_observations: int
    n_self_loops: int
    n_cross_page: int
    by_app: dict[str, int] = field(default_factory=dict)
    by_task: dict[str, int] = field(default_factory=dict)
    trajs_per_task: dict[str, int] = field(default_factory=dict)

    @property
    def drop_rate(self) -> float:
        denom = self.total_observations + self.dropped_observations
        return self.dropped_observations / denom if denom else 0.0


@dataclass(frozen=True)
class ClaimVerdict:
    claim: str
    supported: bool
    evidence: str


@dataclass(frozen=True)
class DataGapReport:
    corpus: CorpusStats
    sava: ReplaySnapshot
    legacy: ReplaySnapshot
    sensitivity: dict[int, int]
    p0c: P0CRecoveryResult
    n_vlm_gated: int  # outcome dists with entropy above the sava VLM threshold

    @property
    def claims(self) -> list[ClaimVerdict]:
        s = self.sava
        edges_ge2 = sum(1 for e in s.edges if e.verification_count >= 2)
        n_cross = len(s.promoted_cross_page)
        n_loop = len(s.promoted_self_loops)
        return [
            ClaimVerdict(
                "Lifecycle accumulates real verification counts on a real observation stream",
                edges_ge2 > 0,
                f"{edges_ge2} edge(s) reached verification_count>=2 by merging repeated observations",
            ),
            ClaimVerdict(
                "Lifecycle promotes USEFUL (cross-page) navigation edges from this corpus",
                n_cross > 0,
                f"{n_cross} cross-page edge(s) promoted; {n_loop} promoted edge(s) are degenerate "
                f"self-loops (empty-target Type/Swipe that stay on the page) — cross-page taps key on "
                f"raw pixels that drift between runs (max coordinate vc={self.p0c.max_coord_vc}), so none "
                f"reach vc>={_SAVA_N}",
            ),
            ClaimVerdict(
                "Outcome distributions flag NON-DETERMINISTIC transitions (same action -> >1 outcome)",
                s.n_multimodal > 0,
                f"{s.n_multimodal} multi-modal distribution(s), {self.n_vlm_gated} above the VLM-verify "
                f"entropy threshold ({_SAVA_ENTROPY_VLM}) — but these are BENIGN timing races (identical "
                f"pixel, 'did the tap register'), since all {self.corpus.n_success} trajs are labelled success",
            ),
            ClaimVerdict(
                "Outcome distributions flag ADVERSARIAL interference (popup / login-wall / captcha / wrong-product)",
                self.corpus.n_failure > 0,
                f"{self.corpus.n_failure} failure/interference trajs in corpus — the headline RQ2 'catch "
                f"misbehavior' claim has zero supporting samples until these are collected",
            ),
            ClaimVerdict(
                "sava is strictly more conservative than legacy (fewer promotions)",
                self.legacy.n_promoted > self.sava.n_promoted,
                f"legacy promoted {self.legacy.n_promoted}/{self.legacy.n_edges} (every edge on first write) "
                f"vs sava {self.sava.n_promoted}/{self.sava.n_edges} on identical input",
            ),
        ]

    @property
    def collection_asks(self) -> list[str]:
        asks: list[str] = []
        if self.corpus.n_failure == 0:
            asks.append(
                f"Collect >={_MIN_FAILURE_TRAJECTORIES} real-device trajectories that DELIBERATELY hit "
                "failure / interference (popup, login-wall, captcha, wrong-product selection). The corpus "
                f"already shows {self.sava.n_multimodal} BENIGN (timing-race) multi-modal distribution(s), "
                "but ZERO adversarial-interference ones — the headline RQ2 'catch misbehavior' claim needs "
                "the latter."
            )
        if len(self.sava.promoted_cross_page) == 0:
            asks.append(
                "ZERO cross-page navigation edges promoted under sava: the only promotions are degenerate "
                "empty-target self-loops. Either (a) make promotion key on the enriched SEMANTIC target so "
                "repeated cross-page taps merge, or (b) accept that raw-pixel taps cannot earn promotion and "
                "rely on bootstrap promotion for navigation edges."
            )
        near = sum(1 for e in self.sava.edges if e.verification_count == _SAVA_N - 1)
        if near:
            asks.append(
                f"{near} edge(s) sit at vc={_SAVA_N - 1} (one verified repeat short of sava promotion). For "
                "EMPTY-target edges (Swipe/Type) re-running the same task tips them over; for coordinate taps "
                "it will not, because the pixels drift between runs."
            )
        asks.append(
            "For coordinate-tap edges to accumulate vc across runs, promotion must key on the enriched "
            "SEMANTIC target (region+page affordance), not raw pixels; otherwise each run is a fresh edge."
        )
        return asks


# ── builders ─────────────────────────────────────────────────────────


def _corpus_stats(trajs: list[Trajectory], per_traj_obs: list[list[Observation]]) -> CorpusStats:
    all_obs = [o for sub in per_traj_obs for o in sub]
    adjacent_pairs = sum(max(0, len(t.steps) - 1) for t in trajs)
    by_app = Counter(o.app or "<none>" for o in all_obs)
    by_task = Counter()
    trajs_per_task = Counter()
    for t, obs in zip(trajs, per_traj_obs):
        by_task[t.task] += len(obs)
        trajs_per_task[t.task] += 1
    return CorpusStats(
        n_trajectories=len(trajs),
        n_success=sum(1 for t in trajs if t.success),
        n_failure=sum(1 for t in trajs if not t.success),
        n_trivial=sum(1 for obs in per_traj_obs if len(obs) <= 1),
        total_steps=sum(len(t.steps) for t in trajs),
        total_observations=len(all_obs),
        dropped_observations=adjacent_pairs - len(all_obs),
        n_self_loops=sum(1 for o in all_obs if o.is_self_loop),
        n_cross_page=sum(1 for o in all_obs if not o.is_self_loop),
        by_app=dict(by_app),
        by_task=dict(by_task),
        trajs_per_task=dict(trajs_per_task),
    )


def build_report(trajs: list[Trajectory]) -> DataGapReport:
    per_traj_obs = [extract_observations(t) for t in trajs]
    all_obs = [o for sub in per_traj_obs for o in sub]
    snaps = compare_configs(all_obs)
    sava = snaps["sava"]
    n_vlm = sum(1 for d in sava.outcome_dists if d.entropy > _SAVA_ENTROPY_VLM)
    return DataGapReport(
        corpus=_corpus_stats(trajs, per_traj_obs),
        sava=sava,
        legacy=snaps["legacy"],
        sensitivity=n_sensitivity(all_obs),
        p0c=verify_p0c_recovery(all_obs),
        n_vlm_gated=n_vlm,
    )


# ── rendering ────────────────────────────────────────────────────────


def _summary_lines(r: DataGapReport) -> list[str]:
    c = r.corpus
    return [
        f"trajectories={c.n_trajectories} (success={c.n_success}, failure={c.n_failure}, "
        f"trivial<=1obs={c.n_trivial})",
        f"usable observations={c.total_observations} "
        f"(cross-page={c.n_cross_page}, self-loop={c.n_self_loops}, "
        f"dropped empty/unknown={c.dropped_observations}, drop-rate={c.drop_rate:.0%})",
        f"edges: sava promoted={r.sava.n_promoted}/{r.sava.n_edges}  "
        f"legacy promoted={r.legacy.n_promoted}/{r.legacy.n_edges}",
        f"multi-modal outcome dists={r.sava.n_multimodal} (entropy>VLM-threshold={r.n_vlm_gated})",
        f"P0-C recovery: coord-edges={r.p0c.n_coord_edges} recovered={r.p0c.n_recovered} "
        f"discriminating(vc>=2)={r.p0c.n_discriminating} max-coord-vc={r.p0c.max_coord_vc} "
        f"proven-on-real-data={r.p0c.proven_on_real_data}",
    ]


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        out.append("| " + " | ".join(str(c) for c in row) + " |")
    return out


def render_markdown(r: DataGapReport, *, generated_at: str = "") -> str:
    c = r.corpus
    L: list[str] = []
    L.append("# AMSG TrajReplay — Lifecycle Replay & Data-Gap Report")
    L.append("")
    L.append("> Offline replay of saved trajectory observation streams into the (P0-C-fixed)")
    L.append("> edge-lifecycle pipeline. 100% software, no real device. Ground truth = the")
    L.append("> `page_type` the agent actually observed on-device (exogenous to the graph).")
    if generated_at:
        L.append(f">")
        L.append(f"> Generated: {generated_at}")
    L.append("")

    L.append("## 0  One-line status")
    L.append("")
    for line in _summary_lines(r):
        L.append(f"- {line}")
    L.append("")

    L.append("## 1  Corpus")
    L.append("")
    L.extend(
        _table(
            ["metric", "value"],
            [
                ["trajectories", c.n_trajectories],
                ["success / failure", f"{c.n_success} / {c.n_failure}"],
                ["trivial (<=1 usable obs)", c.n_trivial],
                ["raw steps (all trajs)", c.total_steps],
                ["usable observations", c.total_observations],
                ["  cross-page", c.n_cross_page],
                ["  self-loop", c.n_self_loops],
                ["dropped (empty/unknown page_type)", c.dropped_observations],
                ["drop rate", f"{c.drop_rate:.0%}"],
            ],
        )
    )
    L.append("")
    L.append("**Usable observations per task** (trajectories per task in parens):")
    L.append("")
    rows = [
        [task[:48], n, r.corpus.trajs_per_task.get(task, 0)]
        for task, n in sorted(c.by_task.items(), key=lambda kv: -kv[1])
    ]
    L.extend(_table(["task", "usable obs", "# trajs"], rows))
    L.append("")
    L.append("**Usable observations per app:** " + ", ".join(f"{k}={v}" for k, v in c.by_app.items()))
    L.append("")

    L.append("## 2  sava vs legacy on identical input")
    L.append("")
    L.extend(
        _table(
            ["config", "edges", "promoted", "candidate", "hypothesis", "vc histogram"],
            [
                [
                    s.config_name,
                    s.n_edges,
                    s.n_promoted,
                    s.n_candidate,
                    s.n_hypothesis,
                    str(s.vc_histogram),
                ]
                for s in (r.sava, r.legacy)
            ],
        )
    )
    L.append("")
    L.append("**sava stage by action intent** (exposes the coordinate-tap vs mergeable split):")
    L.append("")
    sbi = r.sava.stage_by_intent()
    rows = []
    for intent, counter in sorted(sbi.items()):
        rows.append(
            [
                intent,
                counter.get("promoted", 0),
                counter.get("candidate", 0),
                counter.get("hypothesis", 0),
            ]
        )
    L.extend(_table(["intent", "promoted", "candidate", "hypothesis"], rows))
    L.append("")
    L.append(
        "> Note: an edge's own `dominance_ratio` is structurally 1.0 (the observed target "
        "is part of the edge key), so under sava the *only* binding promotion constraint is "
        "`verification_count>=3` and `risk!=high`. The dominance / multimodality signal lives "
        "in the OutcomeDistribution (source+action, target-agnostic) and drives "
        "`requires_vlm_verification`, not edge promotion."
    )
    L.append("")
    L.append("**Promoted-edge utility** (are the promotions actually useful navigation?):")
    L.append("")
    cross = r.sava.promoted_cross_page
    loops = r.sava.promoted_self_loops
    L.append(
        f"- sava promoted **{r.sava.n_promoted}** edge(s): "
        f"**{len(cross)} cross-page** (useful navigation), **{len(loops)} self-loop** (degenerate, stay-put)."
    )
    for e in (*cross, *loops):
        kind = "cross-page" if e.source_page_type != e.target_page_type else "self-loop"
        L.append(
            f"  - [{kind}] `{e.source_page_type}` --[{e.intent}:{e.action_target or '∅'}]--> "
            f"`{e.target_page_type}` (vc={e.verification_count})"
        )
    if len(cross) == 0:
        L.append(
            "- **Zero useful navigation edges earned promotion.** Both promotions are empty-target "
            "self-loops (typing keeps you on the input page; scrolling keeps you on the filter panel). "
            "Every cross-page transition in the corpus is a raw-pixel Tap that never repeats exactly, "
            "so none reached vc>=3."
        )
    L.append("")

    L.append("## 3  N-sensitivity (DESCRIPTIVE on this corpus only — not a claim)")
    L.append("")
    L.extend(
        _table(
            ["min_verification_count N", "# promotable edges"],
            [[n, m] for n, m in sorted(r.sensitivity.items())],
        )
    )
    L.append("")
    L.append(
        "> Red line (sim-env analysis): with an all-success corpus the binding constraint is N "
        "(dominance is trivially 1.0), so this table only shows how thin the corpus is. It is NOT "
        "a generalizable N∈{3,5} result and must not appear as a quantitative claim in the paper."
    )
    L.append("")

    L.append("## 4  P0-C verification on real coordinates")
    L.append("")
    p = r.p0c
    L.extend(
        _table(
            ["metric", "value"],
            [
                ["coordinate edges checked", p.n_coord_edges],
                ["recovered real vc (vs default@1)", p.n_recovered],
                ["discriminating (true vc>=2)", p.n_discriminating],
                ["discriminating recovered", p.n_discriminating_recovered],
                ["max coordinate vc", p.max_coord_vc],
                ["proven on real data", p.proven_on_real_data],
            ],
        )
    )
    L.append("")
    if not p.proven_on_real_data:
        L.append(
            "> No coordinate edge reached vc>=2 in this corpus (raw pixels do not repeat across "
            "runs), so the fallback could not be *discriminated* from the default branch on real "
            "data here. The mechanism is still proven by the synthetic regression "
            "(`tests/test_lifecycle_persistence.py`, `tests/test_experiment_replay.py`)."
        )
        L.append("")

    L.append("## 5  RQ2 sub-claim support (this corpus)")
    L.append("")
    L.extend(
        _table(
            ["claim", "supported?", "evidence"],
            [
                [v.claim, "✅ yes" if v.supported else "❌ no", v.evidence]
                for v in r.claims
            ],
        )
    )
    L.append("")

    L.append("### 5.1  The multi-modal distributions actually observed")
    L.append("")
    mm = r.sava.multimodal_dists
    if mm:
        L.append(
            "These are the (page, action) pairs with >1 distinct outcome. They are the *real* RQ2 "
            "non-determinism signal in this corpus — and notably they are coordinate taps at an "
            "IDENTICAL pixel that nonetheless landed on different pages (a tap-registration / loading "
            "race), NOT adversarial interference:"
        )
        L.append("")
        L.extend(
            _table(
                ["source page", "action", "outcomes", "entropy", "> VLM thr?"],
                [
                    [
                        d.source_page_type,
                        d.action_key,
                        str(d.outcomes),
                        f"{d.entropy:.3f}",
                        "yes" if d.entropy > _SAVA_ENTROPY_VLM else "no",
                    ]
                    for d in mm
                ],
            )
        )
    else:
        L.append("None — every (page, action) pair has a single observed outcome.")
    L.append("")

    L.append("## 6  What real-device collection must still gather")
    L.append("")
    for i, ask in enumerate(r.collection_asks, 1):
        L.append(f"{i}. {ask}")
    L.append("")

    L.append("## 7  Honest boundaries (carry into any write-up)")
    L.append("")
    for b in [
        "No end-to-end success rate (zero pixels replayed, grounding untested).",
        "No latency / wall-clock (replay has no real timing).",
        "No quantitative N∈{3,5} claim — the corpus has zero LABELLED failures, and its only "
        "multimodality is benign timing-race non-determinism, not adversarial interference.",
        "RQ2 mechanism findings are QUALITATIVE and from an N≈6, all-success corpus (optimistic).",
        "The 2 promoted edges are degenerate empty-target self-loops; zero useful navigation edges "
        "earned promotion from this corpus.",
        "Ground truth is the trajectory's observed page_type — the only design that escapes the "
        "'test the graph with the graph' circularity.",
    ]:
        L.append(f"- {b}")
    L.append("")
    return "\n".join(L)
