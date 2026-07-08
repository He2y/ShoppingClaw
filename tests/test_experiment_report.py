"""Tests for the data-gap report aggregator + markdown rendering."""

from __future__ import annotations

from phone_agent.experiment.report import build_report, render_markdown
from phone_agent.experiment.trajectory import Trajectory


def _traj(steps, traj_id, task, success=True, apps=("淘宝",)):
    return Trajectory(
        traj_id=traj_id,
        task=task,
        success=success,
        apps=tuple(apps),
        n_steps=len(steps),
        steps=tuple(steps),
    )


def _swipe_steps(page_types):
    """Build steps whose adjacent pairs are swipes through the given page types."""
    return [
        {"action_type": "Swipe", "action_params": {"start": [1, 1], "end": [2, 2]}, "page_type": pt, "app": "淘宝"}
        for pt in page_types
    ]


def test_report_counts_all_success_corpus():
    # Two trajectories of the same task, each a->b->c (so a->b and b->c).
    trajs = [
        _traj(_swipe_steps(["a", "b", "c"]), "t1", "buy monitor"),
        _traj(_swipe_steps(["a", "b", "c"]), "t2", "buy monitor"),
    ]
    r = build_report(trajs)
    assert r.corpus.n_trajectories == 2
    assert r.corpus.n_success == 2
    assert r.corpus.n_failure == 0
    # Each traj yields 2 observations (a->b, b->c); merged across 2 trajs -> vc=2 each.
    assert r.corpus.total_observations == 4
    # a->b and b->c are 2 distinct edges, each verified twice.
    assert r.sava.n_edges == 2
    assert all(e.verification_count == 2 for e in r.sava.edges)
    # vc=2 < N=3 -> nothing promoted under sava; legacy promotes both.
    assert r.sava.n_promoted == 0
    assert r.legacy.n_promoted == 2


def test_report_third_run_tips_sava_over():
    trajs = [_traj(_swipe_steps(["a", "b", "c"]), f"t{i}", "buy monitor") for i in range(3)]
    r = build_report(trajs)
    assert all(e.verification_count == 3 for e in r.sava.edges)
    assert r.sava.n_promoted == 2  # both edges reach vc=3


def test_report_flags_zero_failure_and_renders_markdown():
    trajs = [_traj(_swipe_steps(["a", "b", "c"]), "t1", "buy monitor")]
    r = build_report(trajs)
    # All-success, single-run corpus -> no repeats -> non-determinism claim unsupported,
    # and adversarial-interference claim unsupported (zero failures).
    claims = {v.claim: v.supported for v in r.claims}
    nondet_claim = next(c for c in claims if "NON-DETERMINISTIC" in c)
    interference_claim = next(c for c in claims if "ADVERSARIAL interference" in c)
    assert claims[nondet_claim] is False
    assert claims[interference_claim] is False
    # Collection asks must mention failure collection.
    assert any("failure" in a.lower() for a in r.collection_asks)
    md = render_markdown(r, generated_at="2026-06-30")
    assert "Data-Gap Report" in md
    assert "Honest boundaries" in md
    assert "2026-06-30" in md


def test_report_conservative_ordering():
    trajs = [_traj(_swipe_steps(["a", "b", "c"]), f"t{i}", "buy monitor") for i in range(3)]
    r = build_report(trajs)
    # legacy is never less conservative than sava.
    assert r.legacy.n_promoted >= r.sava.n_promoted
