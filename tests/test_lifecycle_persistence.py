"""P0-C regression: lifecycle write correctness.

Guards the key invariant that a coordinate-tap edge's *real* accumulated
verification stats survive the enrich-before-lookup path in
``_build_lifecycle_dict`` — instead of silently falling through to the default
``hypothesis@1`` / ``promoted@1`` branch (the root cause of JD's stuck-hypothesis
pollution and taobao's promoted@vc=1 mystery).
"""

from __future__ import annotations


def _mem(policy="sava"):
    from phone_agent.memory.spatial_graph_memory import SpatialGraphMemory
    from phone_agent.spatial.amsg_config import AMSGOptimConfig
    cfg = AMSGOptimConfig.sava() if policy == "sava" else AMSGOptimConfig.legacy()
    return SpatialGraphMemory(graph_store=None, config=cfg)


def _states():
    from phone_agent.memory.spatial_graph_memory import PageState
    src = PageState(state_id="s_home", app="jd", page_type="home")
    tgt = PageState(state_id="s_search", app="jd", page_type="search_input")
    return src, tgt


def test_build_lifecycle_dict_recovers_coordinate_edge_record():
    """A coordinate-tap edge keyed by raw element at record time must still be
    found at promote time after action_target is enriched — yielding the real
    verification_count, not the default branch."""
    from phone_agent.memory.spatial_graph_memory import TransitionEdge

    mem = _mem("sava")
    src, tgt = _states()
    action = {"action_type": "Tap", "element": [500, 132]}

    # Record 3 verified successes — builds a real record keyed by str(element).
    for _ in range(3):
        mem.record_observation(src, action, tgt, outcome="success")

    # Reproduce promote_staging_to_canonical's enrichment of the same edge.
    edge = TransitionEdge(
        source_id="s_home", target_id="s_search", action_type="Tap",
        action_target="", action_params=dict(action), postcondition="search_input",
    )
    enriched = mem._enrich_edge_action_params(edge, src, tgt)
    enriched_edge = TransitionEdge(
        source_id="s_home", target_id="s_search", action_type="Tap",
        action_target=str(enriched.get("semantic_target") or edge.action_target),
        action_params=enriched, postcondition="search_input",
    )
    # The enriched target must actually differ from the raw element, otherwise
    # this test would not exercise the fallback path.
    assert enriched_edge.action_target != "[500, 132]"

    lc = mem._build_lifecycle_dict(src, enriched_edge, tgt)

    # Real record found via the raw-evidence fallback: 3 verified observations,
    # promoted under sava (N=3, dominance=1.0) — NOT the default@1 branch.
    assert lc["verification_count"] == 3, (
        f"expected real vc=3 from recovered record, got {lc['verification_count']} "
        "(default branch was taken — fallback lookup failed)"
    )
    assert lc["lifecycle_stage"] == "promoted"


def test_semantic_edge_record_still_matches_without_fallback():
    """Edges that already carry a semantic target must keep matching on the
    primary key (regression guard: the fallback must not change this path)."""
    from phone_agent.memory.spatial_graph_memory import TransitionEdge

    mem = _mem("sava")
    src, tgt = _states()
    action = {"action_type": "Tap", "semantic_target": "搜索框", "element": [500, 132]}

    for _ in range(3):
        mem.record_observation(src, action, tgt, outcome="success")

    edge = TransitionEdge(
        source_id="s_home", target_id="s_search", action_type="Tap",
        action_target="", action_params=dict(action), postcondition="search_input",
    )
    enriched = mem._enrich_edge_action_params(edge, src, tgt)
    enriched_edge = TransitionEdge(
        source_id="s_home", target_id="s_search", action_type="Tap",
        action_target=str(enriched.get("semantic_target") or edge.action_target),
        action_params=enriched, postcondition="search_input",
    )
    # Semantic target is preserved through enrichment → primary key matches.
    assert enriched_edge.action_target == "搜索框"

    lc = mem._build_lifecycle_dict(src, enriched_edge, tgt)
    assert lc["verification_count"] == 3
    assert lc["lifecycle_stage"] == "promoted"
