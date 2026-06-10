"""Tests for schema-driven exploration pipeline (Phase 2b/2c).

Verifies that:
- PageTypeSpace.from_schema(shopping) contains all 18 legacy page types
- build_full_prompt(shopping) contains all legacy definition lines and priority rules
- coverage_from_schema(shopping) matches legacy CoverageTarget defaults
- SafetyPolicy.from_schema(shopping) token sets ⊇ legacy token sets
- build_default_task(shopping) == legacy _build_taobao_task()
- TransitionRuleEngine strict/schema_guided behaves correctly
- PageInfo coerces ShoppingPageType → str
- New: open-vocab page type proposals recorded and transitions rejected
"""

import pytest

from phone_agent.memory.exploration.classifier_prompts import (
    _CLASSIFIER_SYSTEM_PROMPT,
    build_fast_prompt,
    build_full_prompt,
)
from phone_agent.memory.exploration.explorer import OfflineExplorer, _build_taobao_task
from phone_agent.memory.exploration.safety import SafetyPolicy
from phone_agent.memory.exploration.task_builder import build_default_task, coverage_from_schema
from phone_agent.memory.exploration.transition_rules import TransitionRuleEngine
from phone_agent.memory.exploration.types import (
    CoverageTarget,
    PageInfo,
    PageTypeSpace,
    ShoppingPageType,
    _HIGH_RISK_PAGE_TYPES,
    _PAGE_TYPE_MAP,
    _SPEC_TRIGGER_TOKENS,
    _UNSAFE_ACTION_TOKENS,
)
from phone_agent.spatial.schema_registry import get_default_registry


@pytest.fixture(scope="module")
def shopping_schema():
    return get_default_registry().merged("shopping")


@pytest.fixture(scope="module")
def shopping_space(shopping_schema):
    return PageTypeSpace.from_schema(shopping_schema)


# ── PageTypeSpace ──────────────────────────────────────────────────────────────


def test_page_type_space_contains_all_legacy_18_names(shopping_space):
    """PageTypeSpace.from_schema(shopping merged) must contain all 18 legacy enum values."""
    legacy_names = set(_PAGE_TYPE_MAP.keys())
    space_names = set(shopping_space.names)
    missing = legacy_names - space_names
    assert not missing, f"PageTypeSpace missing legacy names: {missing}"


def test_page_type_space_high_risk_contains_legacy_5(shopping_space):
    """high_risk must contain the 5 legacy high-risk page types."""
    expected = {"checkout", "payment", "address", "login", "permission"}
    assert expected <= shopping_space.high_risk, (
        f"Missing from high_risk: {expected - shopping_space.high_risk}"
    )


def test_page_type_space_normalize_known_type(shopping_space):
    assert shopping_space.normalize("home") == "home"
    # normalize() lowercases the input first, so "HOME" maps to "home"
    assert shopping_space.normalize("HOME") == "home"


def test_page_type_space_normalize_new_type_passthrough(shopping_space):
    assert shopping_space.normalize("new:coupon_center") == "new:coupon_center"


def test_page_type_space_normalize_unknown(shopping_space):
    assert shopping_space.normalize("nonexistent_page") == "unknown"
    assert shopping_space.normalize("") == "unknown"


def test_page_type_space_interference_set(shopping_space, shopping_schema):
    """Interference types from exploration block are included in PageTypeSpace."""
    interference = shopping_space.interference
    # Shopping inherits from common_mobile: dialog, permission, login, ad
    assert "dialog" in interference
    assert "permission" in interference


# ── build_full_prompt golden test ───────────────────────────────────────────────


def test_build_full_prompt_contains_all_legacy_definition_lines(shopping_space, shopping_schema):
    """Generated full prompt must contain every page-type definition line from legacy constant."""
    prompt = build_full_prompt(shopping_space, shopping_schema)

    # Extract definition lines from legacy prompt
    legacy_lines = _CLASSIFIER_SYSTEM_PROMPT.split("\n")
    legacy_def_lines = [
        line.strip()
        for line in legacy_lines
        if line.strip().startswith("- ") and ":" in line
    ]
    assert legacy_def_lines, "Failed to extract legacy definition lines"
    for line in legacy_def_lines:
        name = line.split(":")[0].strip("- ").strip()
        # Check the line prefix is present
        assert f"- {name}:" in prompt, f"Missing definition line for '{name}' in generated prompt"


def test_build_full_prompt_contains_all_priority_rule_sentences(shopping_space, shopping_schema):
    """Generated full prompt must contain every 判别优先级 sentence from legacy constant."""
    prompt = build_full_prompt(shopping_space, shopping_schema)

    legacy_lines = _CLASSIFIER_SYSTEM_PROMPT.split("\n")
    priority_lines = [
        line.strip()
        for line in legacy_lines
        if line.strip().startswith(("1.", "2.", "3.", "4.", "5."))
    ]
    assert len(priority_lines) == 5, f"Expected 5 priority rules, found {len(priority_lines)}"
    for rule in priority_lines:
        # Strip leading digit+dot and check the key content is present
        rule_content = rule.split(" ", 1)[1] if " " in rule else rule
        assert rule_content in prompt, (
            f"Missing priority rule in generated prompt: {rule_content[:60]}"
        )


def test_build_full_prompt_contains_open_vocab_clause(shopping_space, shopping_schema):
    """Full prompt must contain the open-vocabulary new: escape clause."""
    prompt = build_full_prompt(shopping_space, shopping_schema)
    assert "new:" in prompt
    assert "new_type_description" in prompt


def test_build_fast_prompt_does_not_contain_open_vocab_clause(shopping_space, shopping_schema):
    """Fast prompt must NOT contain the open-vocab clause (strict type list)."""
    prompt = build_fast_prompt(shopping_space, shopping_schema)
    assert "new_type_description" not in prompt


# ── coverage_from_schema ──────────────────────────────────────────────────────


def test_coverage_from_schema_matches_legacy_defaults(shopping_schema):
    """coverage_from_schema(shopping) must return exactly the legacy CoverageTarget defaults."""
    result = coverage_from_schema(shopping_schema)
    legacy = CoverageTarget()
    assert result.page_types == legacy.page_types, (
        f"page_types mismatch:\n  got: {result.page_types}\n  want: {legacy.page_types}"
    )
    assert result.transitions == legacy.transitions, (
        f"transitions mismatch:\n  got: {result.transitions}\n  want: {legacy.transitions}"
    )


# ── SafetyPolicy ──────────────────────────────────────────────────────────────


def test_safety_policy_unsafe_tokens_superset_of_legacy(shopping_schema):
    """SafetyPolicy.from_schema(shopping) unsafe_tokens must be a superset of legacy."""
    policy = SafetyPolicy.from_schema(shopping_schema)
    legacy = set(_UNSAFE_ACTION_TOKENS)
    schema_set = set(policy.unsafe_tokens)
    missing = legacy - schema_set
    assert not missing, f"SafetyPolicy missing legacy unsafe tokens: {missing}"


def test_safety_policy_risky_cta_tokens_superset_of_legacy(shopping_schema):
    """SafetyPolicy.from_schema(shopping) risky_cta_tokens must be a superset of legacy."""
    policy = SafetyPolicy.from_schema(shopping_schema)
    legacy = set(_SPEC_TRIGGER_TOKENS)
    schema_set = set(policy.risky_cta_tokens)
    missing = legacy - schema_set
    assert not missing, f"SafetyPolicy missing legacy risky CTA tokens: {missing}"


# ── build_default_task ────────────────────────────────────────────────────────


def test_build_default_task_matches_legacy_taobao_task(shopping_schema):
    """build_default_task(shopping) must return the exact legacy _build_taobao_task() text."""
    schema_task = build_default_task(shopping_schema, None, None, "淘宝")
    legacy_task = _build_taobao_task()
    assert schema_task == legacy_task, (
        f"Task mismatch:\n  schema: {schema_task!r}\n  legacy: {legacy_task!r}"
    )


# ── TransitionRuleEngine ──────────────────────────────────────────────────────


def _make_page(page_type: str) -> PageInfo:
    return PageInfo(
        page_type=page_type,
        semantic_summary="test",
        elements={},
        screenshot_hash="x",
        app="test",
    )


@pytest.fixture(scope="module")
def strict_engine(shopping_schema):
    safety = SafetyPolicy.from_schema(shopping_schema)
    return TransitionRuleEngine(shopping_schema, safety, "strict")


@pytest.fixture(scope="module")
def guided_engine(shopping_schema):
    safety = SafetyPolicy.from_schema(shopping_schema)
    return TransitionRuleEngine(shopping_schema, safety, "schema_guided")


def test_engine_dialog_source_accepted(strict_engine):
    """Interference source (dialog) must be accepted regardless of target."""
    dialog = _make_page("dialog")
    target = _make_page("search_input")
    assert strict_engine.rejection_reason(dialog, {"action": "Tap", "element": [500, 500]}, target) == ""


def test_engine_product_detail_to_cart_requires_top_right(strict_engine):
    """product_detail->cart must use top-right cart icon (x>=650, y<=220)."""
    detail = _make_page("product_detail")
    cart = _make_page("cart")
    # Top cart icon: accepted
    assert strict_engine.rejection_reason(detail, {"action": "Tap", "element": [844, 71]}, cart) == ""
    # Bottom CTA: rejected
    bottom = strict_engine.rejection_reason(detail, {"action": "Tap", "element": [518, 959]}, cart)
    assert bottom != ""
    assert "product_detail->cart" in bottom


def test_engine_search_result_to_product_detail_requires_content_list(strict_engine):
    """search_result->product_detail requires y>=250 (content_list region)."""
    sr = _make_page("search_result")
    pd = _make_page("product_detail")
    # Product card below header: accepted
    assert strict_engine.rejection_reason(sr, {"action": "Tap", "element": [400, 300]}, pd) == ""
    # Top bar tap: rejected (y=100 < 250)
    top = strict_engine.rejection_reason(sr, {"action": "Tap", "element": [400, 100]}, pd)
    assert top != ""


def test_engine_type_action_rejected(strict_engine):
    """type actions are non-navigation and should be rejected."""
    home = _make_page("home")
    si = _make_page("search_input")
    reason = strict_engine.rejection_reason(home, {"action": "type", "text": "hello"}, si)
    assert reason == "non-navigation action"


def test_engine_unknown_pair_rejected_in_strict(strict_engine):
    """Unexpected (source, target) pairs rejected in strict mode."""
    home = _make_page("home")
    cart = _make_page("cart")
    reason = strict_engine.rejection_reason(home, {"action": "Tap", "element": [400, 400]}, cart)
    assert "unexpected" in reason


def test_engine_unknown_pair_accepted_in_schema_guided(guided_engine):
    """Unexpected (source, target) pairs accepted in schema_guided mode."""
    home = _make_page("home")
    cart = _make_page("cart")
    assert guided_engine.rejection_reason(home, {"action": "Tap", "element": [400, 400]}, cart) == ""


def test_engine_should_stop_after_high_risk_rejection():
    """should_stop_after_rejection is True only for high-risk boundary."""
    assert TransitionRuleEngine.should_stop_after_rejection("high-risk page boundary") is True
    assert TransitionRuleEngine.should_stop_after_rejection("unexpected shopping flow transition") is False
    assert TransitionRuleEngine.should_stop_after_rejection("non-navigation action") is False


def test_engine_high_risk_page_boundary_rejected(strict_engine):
    """Transitions involving high-risk pages must be rejected."""
    home = _make_page("home")
    checkout = _make_page("checkout")
    reason = strict_engine.rejection_reason(home, {"action": "Tap", "element": [400, 400]}, checkout)
    assert reason == "high-risk page boundary"


# ── PageInfo coercion ─────────────────────────────────────────────────────────


def test_page_info_coerces_shopping_page_type_enum_to_str():
    """PageInfo constructed with ShoppingPageType coerces to str in __post_init__."""
    page = PageInfo(
        page_type=ShoppingPageType.HOME,
        semantic_summary="home page",
        elements={},
        screenshot_hash="abc",
        app="test",
    )
    assert page.page_type == "home"
    assert isinstance(page.page_type, str)


def test_page_info_state_key_works_after_coercion():
    page = PageInfo(
        page_type=ShoppingPageType.SEARCH_RESULT,
        semantic_summary="results",
        elements={},
        screenshot_hash="xyz",
        app="test",
    )
    key = page.state_key()
    assert key.startswith("search_result:")


# ── open-vocab proposal recording ────────────────────────────────────────────


def test_explorer_records_new_type_proposal_and_rejects_transition():
    """When classify_page returns new:X, explorer records the proposal and rejects the transition."""
    explorer = object.__new__(OfflineExplorer)

    # Minimal setup for testing the internal methods without full __init__
    from phone_agent.spatial.schema_registry import get_default_registry
    from phone_agent.memory.exploration.safety import SafetyPolicy
    from phone_agent.memory.exploration.transition_rules import TransitionRuleEngine
    from phone_agent.memory.exploration.types import PageTypeSpace, CoverageTarget, CoverageReport

    schema = get_default_registry().merged("shopping")
    explorer.schema = schema
    explorer.space = PageTypeSpace.from_schema(schema)
    explorer.safety = SafetyPolicy.from_schema(schema)
    explorer.rules = TransitionRuleEngine(schema, explorer.safety, "strict")
    explorer.coverage_targets = CoverageTarget()
    explorer.coverage_report = CoverageReport((), explorer.coverage_targets.page_types, (), explorer.coverage_targets.transitions)
    explorer.verbose = False
    explorer.transitions = []
    explorer.rejected_transitions = []
    explorer.last_rejection_reason = ""
    explorer.discovered_pages = {}
    explorer.page_type_proposals = {}

    # Simulate recording a new: type page
    new_type_page = PageInfo(
        page_type="new:coupon_center",
        semantic_summary="优惠券中心",
        elements={},
        screenshot_hash="hash1",
        app="test",
    )
    known_page = PageInfo(
        page_type="home",
        semantic_summary="home",
        elements={},
        screenshot_hash="hash2",
        app="test",
    )

    # Record the proposal via the private method
    explorer._record_page_type_proposal(
        "new:coupon_center",
        {"new_type_description": "优惠券领取中心页面"},
        "fake_b64",
    )
    assert "new:coupon_center" in explorer.page_type_proposals
    assert explorer.page_type_proposals["new:coupon_center"]["count"] == 1

    # Record pages
    explorer.discovered_pages[new_type_page.state_key()] = new_type_page
    explorer.discovered_pages[known_page.state_key()] = known_page

    # Transition from home → new: should be rejected as unreviewed
    recorded = explorer._record_transition(
        known_page.state_key(),
        {"action": "Tap", "element": [400, 400]},
        new_type_page.state_key(),
    )
    assert recorded is False
    assert explorer.last_rejection_reason == "unreviewed page type"
    assert explorer.rejected_transitions[0]["reason"] == "unreviewed page type"


# ── import check ──────────────────────────────────────────────────────────────


def test_agent_module_importable():
    """phone_agent.agent must still be importable after all changes."""
    import importlib
    mod = importlib.import_module("phone_agent.agent")
    assert mod is not None
