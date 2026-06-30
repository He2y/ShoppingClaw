"""Regression tests for TrajectoryReviewer pollution fixes.

Guards the two bugs that polluted shopping-spatial-v4 on the first real-device
collection run:
  1. transitions were scoped by a single session-level app picked via
     ``apps[0]`` over an unordered set — so a taobao transition could be checked
     against the launcher (system_home) subgraph, miss every existing edge, and
     write orphan nodes under the wrong app.
  2. within-trajectory duplicate transitions were imported repeatedly.
"""

from __future__ import annotations

from phone_agent.spatial.trajectory_reviewer import TrajectoryReviewer, TransitionCandidate


def _reviewer(store=None):
    return TrajectoryReviewer(graph_store=store, verbose=False)


# ── _extract_transitions: per-transition app ─────────────────────────


def test_extract_transitions_captures_source_step_app():
    steps = [
        {"action_type": "Tap", "action_params": {"element": [1, 2]}, "page_type": "search_input", "app": "淘宝"},
        {"action_type": "Tap", "action_params": {"element": [3, 4]}, "page_type": "search_result", "app": "淘宝"},
        {"action_type": "Tap", "action_params": {"element": [5, 6]}, "page_type": "product_detail", "app": "淘宝"},
    ]
    cands = _reviewer()._extract_transitions(steps)
    assert [c.app for c in cands] == ["淘宝", "淘宝"]
    assert cands[0].source_page == "search_input" and cands[0].target_page == "search_result"


def test_extract_transitions_uses_each_steps_own_app():
    # A transition that happens inside taobao must carry app=淘宝 even if the
    # session also touched the launcher — never inherit a single session app.
    steps = [
        {"action_type": "Tap", "action_params": {}, "page_type": "search_result", "app": "淘宝"},
        {"action_type": "Tap", "action_params": {}, "page_type": "product_detail", "app": "淘宝"},
    ]
    cands = _reviewer()._extract_transitions(steps)
    assert cands[0].app == "淘宝"
    assert cands[0].app.lower() not in ("system home", "system_home")


# ── _dedup_candidates ────────────────────────────────────────────────


def _c(src, tgt, idx, app="淘宝", action="Tap"):
    return TransitionCandidate(src, tgt, action, {}, "", idx, app=app)


def test_dedup_collapses_repeated_transitions_keeping_first():
    deduped = TrajectoryReviewer._dedup_candidates(
        [_c("search_input", "search_result", 0), _c("search_input", "search_result", 3), _c("search_input", "search_result", 9)],
        default_app="淘宝",
    )
    assert len(deduped) == 1
    assert deduped[0].step_index == 0


def test_dedup_preserves_distinct_edges():
    deduped = TrajectoryReviewer._dedup_candidates(
        [_c("search_input", "search_result", 0), _c("search_result", "product_detail", 1)],
        default_app="淘宝",
    )
    assert len(deduped) == 2


def test_dedup_keeps_same_edge_in_different_apps_separate():
    deduped = TrajectoryReviewer._dedup_candidates(
        [_c("home", "search_input", 0, app="淘宝"), _c("home", "search_input", 1, app="京东")],
        default_app="x",
    )
    assert len(deduped) == 2


# ── per-candidate app reaches the existence query (the core bug) ──────


class _FakeResult:
    def __init__(self, cnt: int) -> None:
        self._cnt = cnt

    def single(self):
        return {"cnt": self._cnt}


class _FakeSession:
    def __init__(self, captured: list) -> None:
        self._captured = captured

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, query, **params):
        self._captured.append(params)
        return _FakeResult(0)


class _FakeDriver:
    def __init__(self, captured: list) -> None:
        self._captured = captured

    def session(self, database=None):
        return _FakeSession(self._captured)


class _FakeStore:
    def __init__(self) -> None:
        self.captured: list = []
        self.driver = _FakeDriver(self.captured)
        self.database = "shopping-spatial-v4"


def test_transition_exists_scopes_query_by_candidate_app_not_launcher():
    store = _FakeStore()
    reviewer = _reviewer(store)
    cand = _c("search_input", "search_result", 0, app="淘宝")

    reviewer._transition_exists(cand, cand.app)

    assert store.captured, "existence query was not issued"
    aliases = [a.lower() for a in store.captured[0]["app_aliases"]]
    # The taobao subgraph must be in scope...
    assert any("taobao" in a or a == "淘宝" for a in aliases), aliases
    # ...and the launcher must NOT be (the exact pollution bug).
    assert not any("system" in a for a in aliases), aliases
