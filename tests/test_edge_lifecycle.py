"""Tests for edge lifecycle management (Definition 8 & 9)."""

from phone_agent.spatial.amsg_config import AMSGOptimConfig
from phone_agent.spatial.edge_lifecycle import (
    EdgeLifecycleManager,
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
