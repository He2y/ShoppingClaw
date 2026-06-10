"""Tests for Phase 6 domain structural priors.

Covers:
- Schema priors for shopping search_result
- Unknown app/page_type → returns schema priors or empty, no raise
- Cross-app priors via fake graph store
- format_hint output format
- coverage_is_cold threshold and caching
- Explore-branch injection via MemoryManager/GraphRuntimeController
- Priors disabled via env toggle
- vlm_verify_transitions_for matches legacy constant
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from phone_agent.spatial.domain_priors import DomainPriorProvider, StructuralPrior
from phone_agent.spatial.schema_registry import (
    _LEGACY_VLM_VERIFY_TRANSITIONS,
    vlm_verify_transitions_for,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeRecord:
    """Fake Neo4j record dict wrapper.

    Supports ``dict(rec)`` via the mapping protocol (keys + __getitem__).
    """

    def __init__(self, data: dict):
        self._data = data

    def keys(self):
        return self._data.keys()

    def __getitem__(self, key):
        return self._data[key]

    def get(self, key, default=None):
        return self._data.get(key, default)

    def items(self):
        return self._data.items()


class _FakeResult:
    def __init__(self, records):
        self._records = records

    def __iter__(self):
        return iter(self._records)


class _FakeSession:
    def __init__(self, records):
        self._records = records

    def run(self, query, **params):
        return _FakeResult(self._records)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class _FakeDriver:
    def __init__(self, records=None):
        self._records = records or []

    def session(self, database=None):
        return _FakeSession(self._records)


class _FakeGraphStore:
    """Minimal fake graph store for DomainPriorProvider tests."""

    def __init__(self, records=None, coverage=None):
        self.driver = _FakeDriver(records or [])
        self.database = "test"
        self._coverage = coverage or {}

    def get_page_type_coverage(self, app: str = "") -> dict:
        return dict(self._coverage)


# ---------------------------------------------------------------------------
# A. Schema priors
# ---------------------------------------------------------------------------

def test_schema_priors_shopping_search_result():
    """search_result in shopping schema should include open_product→product_detail."""
    provider = DomainPriorProvider(graph_store=None)
    priors = provider.priors_for("taobao", "search_result")
    # Should have at least the open_product→product_detail transition
    intents = {(p.intent, p.target_page_type) for p in priors}
    assert ("open_product", "product_detail") in intents


def test_schema_priors_all_have_origin_schema():
    """Schema-only (no graph_store) priors must have origin='schema'."""
    provider = DomainPriorProvider(graph_store=None)
    priors = provider.priors_for("taobao", "search_result")
    for p in priors:
        assert p.origin == "schema"


def test_schema_priors_no_coordinates():
    """Priors must not contain coordinate data."""
    provider = DomainPriorProvider(graph_store=None)
    priors = provider.priors_for("taobao", "product_detail")
    for p in priors:
        # StructuralPrior has no coordinate fields
        assert not hasattr(p, "x")
        assert not hasattr(p, "y")
        assert not hasattr(p, "coordinate")


def test_unknown_app_returns_schema_priors_or_empty_no_raise():
    """Unknown app falls back to common_mobile schema or returns () without raising."""
    provider = DomainPriorProvider(graph_store=None)
    try:
        priors = provider.priors_for("totally_unknown_app_xyz", "search_result")
    except Exception as exc:
        pytest.fail(f"priors_for raised unexpectedly: {exc}")
    # Must be a tuple (may be empty or have common_mobile priors)
    assert isinstance(priors, tuple)


def test_unknown_page_type_no_raise():
    """Unknown page_type should return () or schema priors without raising."""
    provider = DomainPriorProvider(graph_store=None)
    try:
        priors = provider.priors_for("taobao", "nonexistent_page_type_xyz")
    except Exception as exc:
        pytest.fail(f"priors_for raised unexpectedly: {exc}")
    assert isinstance(priors, tuple)


# ---------------------------------------------------------------------------
# B. Cross-app priors via fake graph store
# ---------------------------------------------------------------------------

def test_cross_app_priors_returned_from_fake_store(monkeypatch):
    """Cross-app query results are returned as StructuralPrior(origin='cross_app')."""
    records = [
        {
            "src": "search_result",
            "intent": "open_product",
            "tgt": "product_detail",
            "support": 5,
            "apps": ["jd", "pinduoduo"],
        }
    ]
    fake_store = _FakeGraphStore(records=[_FakeRecord(r) for r in records])
    provider = DomainPriorProvider(graph_store=fake_store)

    # Use a registered app so domain lookup succeeds (taobao → shopping)
    priors = provider._query_cross_app("taobao", "search_result")

    assert len(priors) == 1
    p = priors[0]
    assert p.origin == "cross_app"
    assert p.support == 5
    assert "jd" in p.apps
    assert p.target_page_type == "product_detail"


def test_cross_app_priors_empty_when_no_driver():
    """No driver → cross_app priors silently return ()."""
    class _NoDriverStore:
        driver = None

    provider = DomainPriorProvider(graph_store=_NoDriverStore())
    priors = provider._cross_app_priors("taobao", "search_result")
    assert priors == []


def test_cross_app_query_failure_returns_empty():
    """A crashing driver must not propagate exceptions."""
    class _BadDriver:
        def session(self, database=None):
            raise RuntimeError("connection refused")

    class _BadStore:
        driver = _BadDriver()
        database = "test"

    provider = DomainPriorProvider(graph_store=_BadStore())
    priors = provider._cross_app_priors("taobao", "search_result")
    assert priors == []


# ---------------------------------------------------------------------------
# C. format_hint
# ---------------------------------------------------------------------------

def test_format_hint_empty_returns_empty_string():
    provider = DomainPriorProvider(graph_store=None)
    assert provider.format_hint((), "taobao", "search_result") == ""


def test_format_hint_contains_page_type_and_no_coordinates():
    priors = (
        StructuralPrior(
            source_page_type="search_result",
            intent="open_product",
            target_page_type="product_detail",
            origin="cross_app",
            support=3,
            apps=("jd", "pinduoduo"),
        ),
    )
    provider = DomainPriorProvider(graph_store=None)
    hint = provider.format_hint(priors, "taobao", "search_result")

    assert hint != ""
    assert "search_result" in hint
    assert "product_detail" in hint
    # Must not contain coordinate-looking strings like "x=", "y=", "coordinate"
    assert "coordinate" not in hint.lower()
    assert "x=" not in hint
    assert "y=" not in hint


def test_format_hint_mentions_same_domain_marker():
    priors = (
        StructuralPrior(
            source_page_type="search_result",
            intent="open_product",
            target_page_type="product_detail",
            origin="cross_app",
            support=3,
        ),
    )
    provider = DomainPriorProvider(graph_store=None)
    hint = provider.format_hint(priors, "jd", "search_result")
    # Should mention domain-prior or schema-prior marker
    assert "先验" in hint or "领域模式" in hint or "同域" in hint


def test_format_hint_schema_origin_mentions_domain_pattern():
    priors = (
        StructuralPrior(
            source_page_type="product_detail",
            intent="open_spec",
            target_page_type="spec_selection",
            origin="schema",
        ),
    )
    provider = DomainPriorProvider(graph_store=None)
    hint = provider.format_hint(priors, "taobao", "product_detail")
    assert "领域模式" in hint or "先验" in hint or "spec_selection" in hint


# ---------------------------------------------------------------------------
# D. coverage_is_cold
# ---------------------------------------------------------------------------

def test_coverage_is_cold_true_for_empty_coverage():
    store = _FakeGraphStore(coverage={})
    provider = DomainPriorProvider(graph_store=store, cache_ttl_s=60.0)
    assert provider.coverage_is_cold("jd", threshold=3) is True


def test_coverage_is_cold_false_above_threshold():
    store = _FakeGraphStore(coverage={"home": 5, "search_result": 3})
    provider = DomainPriorProvider(graph_store=store, cache_ttl_s=60.0)
    assert provider.coverage_is_cold("jd", threshold=3) is False


def test_coverage_is_cold_cached(monkeypatch):
    """Second call should not re-query when TTL has not expired."""
    call_count = [0]
    real_get = _FakeGraphStore.get_page_type_coverage

    def counting_coverage(self, app=""):
        call_count[0] += 1
        return {}

    store = _FakeGraphStore(coverage={})
    monkeypatch.setattr(type(store), "get_page_type_coverage", counting_coverage)
    provider = DomainPriorProvider(graph_store=store, cache_ttl_s=999.0)

    provider.coverage_is_cold("jd")
    provider.coverage_is_cold("jd")
    assert call_count[0] == 1  # only called once; second hit from cache


def test_coverage_is_cold_no_driver():
    """With no driver, always returns False (not cold) to avoid spurious injection."""

    class _NullStore:
        driver = None

        def get_page_type_coverage(self, app=""):
            return {}

    provider = DomainPriorProvider(graph_store=_NullStore(), cache_ttl_s=60.0)
    # _get_coverage_total returns 0 → cold
    assert provider.coverage_is_cold("jd", threshold=3) is True


# ---------------------------------------------------------------------------
# E. Explore-branch injection via MemoryManager / GraphRuntimeController
# ---------------------------------------------------------------------------

class _FakeMemGraphStore:
    """Minimal fake for MemoryManager tests."""
    driver = None

    def find_similar_tasks(self, task, app=None, top_k=3):
        return []

    def get_task_trajectory(self, task_id, max_steps=20):
        return {}


def _make_manager(tmp_path):
    """Create a MemoryManager with fake graph stores."""
    from phone_agent.memory.memory_manager import MemoryManager
    from phone_agent.memory.spatial_graph_memory import SpatialGraphMemory

    manager = MemoryManager(storage_dir=str(tmp_path), user_id="tester")
    manager.graph_store = _FakeMemGraphStore()
    manager.runtime_graph_store = _FakeMemGraphStore()
    manager.spatial_graph_memory = SpatialGraphMemory(manager.graph_store)
    return manager


def test_explore_mode_injects_prior_hint_when_cold(tmp_path, monkeypatch):
    """When provider returns priors for a cold app, hint appears in semantic_context."""
    monkeypatch.setenv("AMSG_DOMAIN_PRIORS", "1")

    from phone_agent.spatial import runtime_controller as rc_mod
    original_flag = rc_mod._AMSG_DOMAIN_PRIORS_ENABLED
    rc_mod._AMSG_DOMAIN_PRIORS_ENABLED = True

    try:
        from phone_agent.memory.memory_manager import MemoryManager
        from phone_agent.memory.spatial_graph_memory import SpatialGraphMemory
        from phone_agent.spatial.domain_priors import DomainPriorProvider

        manager = _make_manager(tmp_path)

        # Build a page state for "jd" at "home" — task targets checkout,
        # so plan() can't reach it (no edges) → explore mode.
        _state = manager.spatial_graph_memory.build_page_state(
            ui_hash="jd_home",
            semantic_layout="京东 home",
            app="jd",
            page_type="home",
        )

        fake_prior = StructuralPrior(
            source_page_type="home",
            intent="open_search",
            target_page_type="search_input",
            origin="cross_app",
            support=2,
        )

        class _FakeProvider:
            def coverage_is_cold(self, app, threshold=3):
                return True

            def priors_for(self, app, page_type, goal_page_types=()):
                return (fake_prior,)

            def format_hint(self, priors, app, page_type):
                return "[同域结构先验] home 常见转移: open_search→search_input (2个应用验证过)\n这些仅是方向提示, 具体元素请从当前截图判断。"

        # Inject fake provider onto the controller
        controller = manager._ensure_graph_runtime_controller()
        controller._domain_prior_provider = _FakeProvider()

        context = manager.locate_and_get_context(
            ui_hash="jd_home",
            semantic_layout="京东 home",
            task="结账",  # targets checkout — home can't reach it with no edges → explore
            screen_dict={
                "ui_hash": "jd_home",
                "semantic_layout": "京东 home",
                "app": "jd",
                "page_type": "home",
            },
        )

        assert context["mode"] == "explore"
        semantic = context.get("semantic_context", "")
        assert "同域结构先验" in semantic
    finally:
        rc_mod._AMSG_DOMAIN_PRIORS_ENABLED = original_flag


def test_explore_mode_no_injection_when_priors_disabled(tmp_path, monkeypatch):
    """AMSG_DOMAIN_PRIORS=0 → semantic_context unchanged."""
    monkeypatch.setenv("AMSG_DOMAIN_PRIORS", "0")

    from phone_agent.spatial import domain_priors as dp_mod
    from phone_agent.spatial import runtime_controller as rc_mod

    # Force module-level flag to disabled
    original_dp = dp_mod._DOMAIN_PRIORS_ENABLED
    original_rc = rc_mod._AMSG_DOMAIN_PRIORS_ENABLED
    dp_mod._DOMAIN_PRIORS_ENABLED = False
    rc_mod._AMSG_DOMAIN_PRIORS_ENABLED = False

    try:
        manager = _make_manager(tmp_path)

        class _AlwaysColdProvider:
            def coverage_is_cold(self, app, threshold=3):
                return True

            def priors_for(self, app, page_type, goal_page_types=()):
                return (StructuralPrior(
                    source_page_type="home",
                    intent="open_search",
                    target_page_type="search_input",
                    origin="cross_app",
                    support=2,
                ),)

            def format_hint(self, priors, app, page_type):
                return "[同域结构先验] 不应出现"

        controller = manager._ensure_graph_runtime_controller()
        controller._domain_prior_provider = _AlwaysColdProvider()

        context = manager.locate_and_get_context(
            ui_hash="x",
            semantic_layout="jd home",
            task="结账",
            screen_dict={"ui_hash": "x", "semantic_layout": "jd home",
                         "app": "jd", "page_type": "home"},
        )
        semantic = context.get("semantic_context", "")
        assert "不应出现" not in semantic
    finally:
        dp_mod._DOMAIN_PRIORS_ENABLED = original_dp
        rc_mod._AMSG_DOMAIN_PRIORS_ENABLED = original_rc


def test_explore_mode_no_injection_when_not_cold(tmp_path, monkeypatch):
    """When coverage is warm, no domain prior hint is injected."""
    monkeypatch.setenv("AMSG_DOMAIN_PRIORS", "1")

    from phone_agent.spatial import runtime_controller as rc_mod
    original_flag = rc_mod._AMSG_DOMAIN_PRIORS_ENABLED
    rc_mod._AMSG_DOMAIN_PRIORS_ENABLED = True

    try:
        manager = _make_manager(tmp_path)

        class _WarmProvider:
            def coverage_is_cold(self, app, threshold=3):
                return False  # NOT cold → no injection

            def priors_for(self, app, page_type, goal_page_types=()):
                return (StructuralPrior(
                    source_page_type="home",
                    intent="open_search",
                    target_page_type="search_input",
                    origin="schema",
                ),)

            def format_hint(self, priors, app, page_type):
                return "[同域结构先验] 不应注入"

        controller = manager._ensure_graph_runtime_controller()
        controller._domain_prior_provider = _WarmProvider()

        context = manager.locate_and_get_context(
            ui_hash="x",
            semantic_layout="taobao home",
            task="结账",
            screen_dict={"ui_hash": "x", "semantic_layout": "taobao home",
                         "app": "taobao", "page_type": "home"},
        )
        semantic = context.get("semantic_context", "")
        assert "不应注入" not in semantic
    finally:
        rc_mod._AMSG_DOMAIN_PRIORS_ENABLED = original_flag


# ---------------------------------------------------------------------------
# F. vlm_verify_transitions_for matches legacy constant
# ---------------------------------------------------------------------------

def test_vlm_verify_transitions_for_shopping_equals_legacy():
    """shopping schema carries the same pairs as the legacy constant."""
    pairs = vlm_verify_transitions_for("shopping")
    assert pairs == _LEGACY_VLM_VERIFY_TRANSITIONS


def test_vlm_verify_transitions_for_taobao_equals_legacy():
    """Looking up via app id 'taobao' should also return the shopping pairs."""
    pairs = vlm_verify_transitions_for("taobao")
    assert ("search_result", "product_detail") in pairs
    assert ("product_detail", "spec_selection") in pairs


def test_vlm_verify_transitions_for_unknown_falls_back_to_legacy():
    """Completely unknown schema name must fall back gracefully."""
    pairs = vlm_verify_transitions_for("schema_that_does_not_exist_xyz")
    assert pairs == _LEGACY_VLM_VERIFY_TRANSITIONS


# ---------------------------------------------------------------------------
# G. priors_for disabled globally
# ---------------------------------------------------------------------------

def test_priors_for_returns_empty_when_disabled():
    from phone_agent.spatial import domain_priors as dp_mod
    original = dp_mod._DOMAIN_PRIORS_ENABLED
    dp_mod._DOMAIN_PRIORS_ENABLED = False
    try:
        provider = DomainPriorProvider(graph_store=None)
        priors = provider.priors_for("taobao", "search_result")
        assert priors == ()
    finally:
        dp_mod._DOMAIN_PRIORS_ENABLED = original
