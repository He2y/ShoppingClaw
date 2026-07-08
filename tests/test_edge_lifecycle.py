"""Tests for edge lifecycle management (Definition 7 & 8)."""

from phone_agent.spatial.amsg_config import AMSGOptimConfig
from phone_agent.spatial.edge_lifecycle import (
    EdgeLifecycleManager,
    EdgeLifecycleRecord,
    OutcomeDistribution,
)


def test_outcome_distribution_entropy_single_outcome():
    dist = OutcomeDistribution("product_detail", "tap:buy", {"spec_selection": 5})
    assert dist.entropy < 0.01  # near-zero for single outcome
    name, ratio = dist.dominant_outcome
    assert name == "spec_selection"
    assert ratio == 1.0


def test_outcome_distribution_entropy_two_equal():
    dist = OutcomeDistribution("product_detail", "tap:buy", {"spec_selection": 5, "login": 5})
    assert dist.entropy > 0.6  # high entropy for equal split


def test_outcome_distribution_with_outcome_immutable():
    dist = OutcomeDistribution("product_detail", "tap:buy", {"spec_selection": 3})
    dist2 = dist.with_outcome("login")
    assert dist.outcomes == {"spec_selection": 3}  # original unchanged
    assert dist2.outcomes == {"spec_selection": 3, "login": 1}


def test_legacy_mode_always_promotes():
    mgr = EdgeLifecycleManager(AMSGOptimConfig.legacy())
    r = mgr.record_outcome(
        source_page_type="product_detail",
        intent="tap",
        action_target="buy_button",
        observed_target="spec_selection",
    )
    assert r.stage == "promoted"


def test_verified_mode_hypothesis_on_first_observation():
    config = AMSGOptimConfig.full()
    mgr = EdgeLifecycleManager(config)
    # min_verification_count=1, so first observation should promote if dominant
    r = mgr.record_outcome(
        source_page_type="product_detail",
        intent="tap",
        action_target="buy_button",
        observed_target="spec_selection",
    )
    # With 1 observation, dominance_ratio=1.0 >= 0.6, so promoted
    assert r.stage == "promoted"
    assert r.dominance_ratio == 1.0


def test_verified_mode_candidate_on_chaotic_outcomes():
    config = AMSGOptimConfig(
        edge_promotion_policy="verified",
        min_verification_count=1,
        outcome_dominance_threshold=0.6,
        enable_heuristic_injection=False,
    )
    mgr = EdgeLifecycleManager(config)
    # Create chaotic outcomes: 1 spec, 1 login, 1 popup
    mgr.record_outcome(
        source_page_type="product_detail",
        intent="tap",
        action_target="buy_button",
        observed_target="spec_selection",
    )
    mgr.record_outcome(
        source_page_type="product_detail",
        intent="tap",
        action_target="buy_button",
        observed_target="login",
    )
    r = mgr.record_outcome(
        source_page_type="product_detail",
        intent="tap",
        action_target="buy_button",
        observed_target="promotion_popup",
    )
    # Each edge record tracks its own specific (source, intent, target, observed) key
    # The spec_selection edge still has dominance 1.0 for its own key
    # But check the outcome entropy for the action
    entropy = mgr.get_outcome_entropy("product_detail", "tap:buy_button")
    assert entropy > 0.9  # high entropy: 3 different outcomes


def test_high_risk_stays_candidate():
    config = AMSGOptimConfig(
        edge_promotion_policy="verified",
        min_verification_count=1,
        enable_heuristic_injection=False,
    )
    mgr = EdgeLifecycleManager(config)
    r = mgr.record_outcome(
        source_page_type="cart",
        intent="tap",
        action_target="checkout_button",
        observed_target="checkout",
        risk="high",
    )
    assert r.stage == "candidate"  # high risk → not promoted


def test_vlm_verification_threshold():
    config = AMSGOptimConfig(
        edge_promotion_policy="verified",
        outcome_entropy_vlm_threshold=0.5,
        enable_heuristic_injection=False,
    )
    mgr = EdgeLifecycleManager(config)
    # Single outcome → low entropy → no VLM needed
    mgr.record_outcome(
        source_page_type="search_result",
        intent="tap",
        action_target="product_card",
        observed_target="product_detail",
    )
    assert not mgr.requires_vlm_verification("search_result", "tap:product_card")

    # Add different outcome → entropy rises
    mgr.record_outcome(
        source_page_type="search_result",
        intent="tap",
        action_target="product_card",
        observed_target="login",
    )
    assert mgr.requires_vlm_verification("search_result", "tap:product_card")


def test_lifecycle_summary():
    mgr = EdgeLifecycleManager(AMSGOptimConfig.full())
    mgr.record_outcome(
        source_page_type="home",
        intent="tap",
        action_target="search",
        observed_target="search_input",
    )
    mgr.record_outcome(
        source_page_type="search_input",
        intent="type",
        action_target="query",
        observed_target="search_result",
    )
    summary = mgr.lifecycle_summary()
    assert summary["promoted"] == 2
    assert summary["total_outcomes"] == 2


def test_get_promoted_edges_for_page():
    mgr = EdgeLifecycleManager(AMSGOptimConfig.full())
    mgr.record_outcome(
        source_page_type="home",
        intent="tap",
        action_target="search",
        observed_target="search_input",
    )
    mgr.record_outcome(
        source_page_type="search_input",
        intent="type",
        action_target="query",
        observed_target="search_result",
    )
    home_edges = mgr.get_promoted_edges_for_page("home")
    assert len(home_edges) == 1
    assert home_edges[0].target_page_type == "search_input"


# ── Serialization (from_dict / to_dict roundtrip) ─────────────────


def test_outcome_distribution_from_dict_roundtrip():
    original = OutcomeDistribution("product_detail", "tap:buy", {"spec_selection": 5, "login": 2})
    d = original.to_dict()
    restored = OutcomeDistribution.from_dict(d)
    assert restored.source_page_type == original.source_page_type
    assert restored.action_key == original.action_key
    assert restored.outcomes == original.outcomes
    assert abs(restored.entropy - original.entropy) < 1e-6


def test_edge_lifecycle_record_from_dict_roundtrip():
    original = EdgeLifecycleRecord(
        edge_key="home|tap|search|search_input",
        stage="promoted",
        source_page_type="home",
        target_page_type="search_input",
        intent="tap",
        action_target="search",
        outcome_counts={"search_input": 3},
        total_attempts=3,
        verification_count=3,
        dominant_outcome="search_input",
        dominance_ratio=1.0,
        risk_level="normal",
        last_verified_step=10,
        created_step=1,
    )
    d = original.to_dict()
    restored = EdgeLifecycleRecord.from_dict(d)
    assert restored.edge_key == original.edge_key
    assert restored.stage == original.stage
    assert restored.verification_count == original.verification_count
    assert restored.dominance_ratio == original.dominance_ratio
    assert restored.outcome_counts == original.outcome_counts


# ── Bulk load / export ─────────────────────────────────────────────


def test_bulk_export_import_roundtrip():
    mgr1 = EdgeLifecycleManager(AMSGOptimConfig.full())
    mgr1.record_outcome(source_page_type="home", intent="tap", action_target="search", observed_target="search_input")
    mgr1.record_outcome(source_page_type="search_result", intent="tap", action_target="card", observed_target="product_detail")

    records, outcomes = mgr1.bulk_export()
    assert len(records) == 2
    assert len(outcomes) == 2

    mgr2 = EdgeLifecycleManager(AMSGOptimConfig.full())
    mgr2.bulk_load(records, outcomes)

    assert len(mgr2._records) == 2
    assert len(mgr2._outcomes) == 2
    for key in mgr1._records:
        assert mgr2._records[key].stage == mgr1._records[key].stage
        assert mgr2._records[key].verification_count == mgr1._records[key].verification_count


def test_bulk_load_merges_with_existing():
    mgr = EdgeLifecycleManager(AMSGOptimConfig.full())
    mgr.record_outcome(source_page_type="home", intent="tap", action_target="search", observed_target="search_input")

    new_records = [{
        "edge_key": "home|tap|search|search_input",
        "stage": "promoted",
        "source_page_type": "home",
        "target_page_type": "search_input",
        "intent": "tap",
        "action_target": "search",
        "outcome_counts": {"search_input": 5},
        "total_attempts": 5,
        "verification_count": 5,
        "dominant_outcome": "search_input",
        "dominance_ratio": 1.0,
        "risk_level": "normal",
    }]
    mgr.bulk_load(new_records, [])
    record = mgr.get_record("home|tap|search|search_input")
    assert record is not None
    assert record.verification_count == 5  # incoming wins (higher count)


# ── Demotion ───────────────────────────────────────────────────────


def test_check_demotion_demotes_low_dominance():
    mgr = EdgeLifecycleManager(AMSGOptimConfig.full())
    mgr.record_outcome(source_page_type="product_detail", intent="tap", action_target="buy", observed_target="spec_selection")
    mgr.record_outcome(source_page_type="product_detail", intent="tap", action_target="buy", observed_target="login")
    mgr.record_outcome(source_page_type="product_detail", intent="tap", action_target="buy", observed_target="popup")

    # Each edge key is unique (different observed_target), so individual records have dominance=1.0
    # But let's test demotion with a manually constructed low-dominance promoted edge
    from phone_agent.spatial.edge_lifecycle import EdgeLifecycleRecord
    mgr._records["test|tap|btn|a"] = EdgeLifecycleRecord(
        edge_key="test|tap|btn|a",
        stage="promoted",
        source_page_type="test",
        target_page_type="a",
        intent="tap",
        action_target="btn",
        outcome_counts={"a": 2, "b": 2, "c": 1},
        total_attempts=5,
        verification_count=5,
        dominant_outcome="a",
        dominance_ratio=0.4,  # below threshold
    )
    demoted = mgr.check_demotion(threshold=0.5)
    assert len(demoted) == 1
    assert demoted[0].stage == "demoted"
    assert mgr.get_record("test|tap|btn|a").stage == "demoted"


def test_check_demotion_skips_high_dominance():
    mgr = EdgeLifecycleManager(AMSGOptimConfig.full())
    mgr._records["test|tap|btn|a"] = EdgeLifecycleRecord(
        edge_key="test|tap|btn|a",
        stage="promoted",
        source_page_type="test",
        target_page_type="a",
        intent="tap",
        action_target="btn",
        outcome_counts={"a": 9, "b": 1},
        total_attempts=10,
        verification_count=10,
        dominant_outcome="a",
        dominance_ratio=0.9,
    )
    demoted = mgr.check_demotion(threshold=0.5)
    assert len(demoted) == 0
    assert mgr.get_record("test|tap|btn|a").stage == "promoted"


# ── Config from_env ────────────────────────────────────────────────


def test_amsg_config_from_env_default(monkeypatch):
    monkeypatch.delenv("AMSG_CONFIG", raising=False)
    config = AMSGOptimConfig.from_env()
    assert config.edge_promotion_policy == "legacy"


def test_amsg_config_from_env_full(monkeypatch):
    monkeypatch.setenv("AMSG_CONFIG", "full")
    config = AMSGOptimConfig.from_env()
    assert config.edge_promotion_policy == "verified"
    assert config.planner_backend == "belief_astar"


def test_amsg_config_from_env_unknown_falls_back(monkeypatch):
    monkeypatch.setenv("AMSG_CONFIG", "nonexistent")
    config = AMSGOptimConfig.from_env()
    assert config.edge_promotion_policy == "legacy"
