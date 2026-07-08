"""Regression tests for the B-class graph-statistics-truthfulness fixes.

B1 lifecycle persistence matches Action nodes by semantic identity (not a
    re-derived action_id hash that matched 0 nodes).
B2 unverified observations (no expected postcondition) cast no promotion vote.
B3 the in-memory staging graph is cleared at task boundaries.
B4 only promoted edges enter the executable action library (Cypher filter).
"""

import inspect

from phone_agent.memory.spatial_graph_memory import (
    PageState,
    SpatialGraphMemory,
    TransitionEdge,
)


# ── B2: unverified casts no vote ─────────────────────────────────────────


def test_from_action_unverified_casts_no_vote():
    edge = TransitionEdge.from_action(
        "s1", "s2", {"action": "Tap", "semantic_target": "btn"},
        outcome="unverified",
    )
    assert edge.success_count == 0
    assert edge.fail_count == 0


def test_from_action_success_and_failure_still_vote():
    ok = TransitionEdge.from_action("s1", "s2", {"action": "Tap"}, outcome="success")
    bad = TransitionEdge.from_action("s1", "s2", {"action": "Tap"}, outcome="failure")
    assert (ok.success_count, ok.fail_count) == (1, 0)
    assert (bad.success_count, bad.fail_count) == (0, 1)


def _page(pid, ptype):
    return PageState(state_id=pid, app="taobao", page_type=ptype)


def test_record_observation_unverified_skips_lifecycle():
    mem = SpatialGraphMemory()
    src, dst = _page("a", "home"), _page("b", "search_input")

    mem.record_observation(src, {"action": "Tap", "semantic_target": "search"}, dst,
                           outcome="unverified")
    # structural edge staged...
    assert mem._local_edges.get("a")
    # ...but lifecycle got NO verification (would inflate promotion)
    assert len(mem._edge_lifecycle._records) == 0


def test_record_observation_success_feeds_lifecycle():
    mem = SpatialGraphMemory()
    src, dst = _page("a", "home"), _page("b", "search_input")

    mem.record_observation(src, {"action": "Tap", "semantic_target": "search"}, dst,
                           outcome="success")
    assert len(mem._edge_lifecycle._records) == 1
    rec = next(iter(mem._edge_lifecycle._records.values()))
    assert rec.verification_count == 1


# ── B3: staging cleared at task boundaries ───────────────────────────────


def test_clear_staging_empties_local_graph():
    mem = SpatialGraphMemory()
    mem.record_observation(_page("a", "home"), {"action": "Tap"}, _page("b", "search_input"),
                           outcome="success")
    assert mem._local_states and mem._local_edges

    mem.clear_staging()
    assert mem._local_states == {}
    assert mem._local_edges == {}
    # lifecycle records are intentionally NOT cleared (cumulative)
    assert len(mem._edge_lifecycle._records) == 1


def test_start_task_clears_staging(tmp_path):
    from phone_agent.memory.memory_manager import MemoryManager

    mgr = MemoryManager(storage_dir=str(tmp_path), user_id="tester")
    mgr.spatial_graph_memory.record_observation(
        _page("a", "home"), {"action": "Tap"}, _page("b", "search_input"), outcome="success",
    )
    assert mgr.spatial_graph_memory._local_edges

    mgr.start_task("new task")
    assert mgr.spatial_graph_memory._local_edges == {}


# ── B1 / B4: Cypher filters present (source assertions; unit tests don't
#            hit Neo4j, so we pin the query text to catch regressions) ────


def test_b4_edge_loaders_filter_promoted():
    src = inspect.getsource(SpatialGraphMemory._load_edges_by_page_type)
    # both branches (unknown + normal) must carry the promoted filter
    assert src.count("coalesce(a.lifecycle_stage, 'promoted') = 'promoted'") == 2

    from phone_agent.memory.graph_store import GraphStore
    gs_src = inspect.getsource(GraphStore.get_outgoing_transitions)
    assert "coalesce(a.lifecycle_stage, 'promoted') = 'promoted'" in gs_src


def test_b1_lifecycle_persist_matches_by_semantic_identity():
    from phone_agent.memory.graph_lifecycle_store import GraphLifecycleStore

    src = inspect.getsource(GraphLifecycleStore.persist_lifecycle_batch)
    # must MATCH the full transition by page types + intent + semantic target,
    # NOT by a re-derived action_id
    assert "MATCH (a:Action {action_id: row.action_id})" not in src
    assert "s.page_type = row.source_page_type" in src
    assert "t.page_type = row.target_page_type" in src
    assert "coalesce(a.type, '') = row.intent" in src

    flush_src = inspect.getsource(SpatialGraphMemory._flush_lifecycle_to_graph)
    assert '"source_page_type": rec["source_page_type"]' in flush_src
    assert "_lifecycle_record_to_action_id" not in flush_src
