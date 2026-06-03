"""Tests for the AutonomousExplorer and its supporting components."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from phone_agent.memory.autonomous_explorer import (
    AutonomousExplorer,
    ConvergenceTracker,
    ExplorationPolicy,
    JobExecutionResult,
    _build_autonomous_system_prompt,
    _generic_transition_rejection,
    _page_info_to_dict,
    is_safe_exploration_action,
)
from phone_agent.memory.offline_explorer import PageInfo, ShoppingPageType
from phone_agent.spatial.exploration_queue import ExplorationJob


# ── ExplorationPolicy ────────────────────────────────────────


class TestExplorationPolicy:
    def test_defaults(self) -> None:
        policy = ExplorationPolicy()
        assert policy.max_total_steps == 80
        assert policy.max_job_steps == 3
        assert policy.max_dry_rounds == 5

    def test_frozen(self) -> None:
        policy = ExplorationPolicy()
        with pytest.raises(AttributeError):
            policy.max_total_steps = 100  # type: ignore[misc]

    def test_to_dict_roundtrip(self) -> None:
        policy = ExplorationPolicy(max_total_steps=50, max_dry_rounds=3)
        d = policy.to_dict()
        assert d["max_total_steps"] == 50
        assert d["max_dry_rounds"] == 3
        assert len(d) == 8


# ── ConvergenceTracker ───────────────────────────────────────


class TestConvergenceTracker:
    def test_not_converged_initially(self) -> None:
        tracker = ConvergenceTracker(dry_round_limit=3)
        assert not tracker.converged
        assert tracker.dry_rounds == 0
        assert tracker.total_rounds == 0

    def test_converges_after_dry_rounds(self) -> None:
        tracker = ConvergenceTracker(dry_round_limit=3)
        tracker.record_round(new_clusters=2, new_page_types=1, new_transitions=3)
        assert not tracker.converged
        for _ in range(3):
            tracker.record_round(0, 0, 0)
        assert tracker.converged
        assert tracker.dry_rounds == 3

    def test_discovery_resets_dry_count(self) -> None:
        tracker = ConvergenceTracker(dry_round_limit=3)
        tracker.record_round(0, 0, 0)
        tracker.record_round(0, 0, 0)
        assert tracker.dry_rounds == 2
        tracker.record_round(1, 0, 0)
        assert tracker.dry_rounds == 0
        assert not tracker.converged

    def test_new_pages_reset_dry_count(self) -> None:
        tracker = ConvergenceTracker(dry_round_limit=2)
        tracker.record_round(0, 0, 0)
        tracker.record_round(0, 1, 0)
        assert tracker.dry_rounds == 0

    def test_transitions_alone_are_dry(self) -> None:
        tracker = ConvergenceTracker(dry_round_limit=2)
        tracker.record_round(0, 0, 5)
        tracker.record_round(0, 0, 10)
        assert tracker.converged

    def test_summary(self) -> None:
        tracker = ConvergenceTracker(dry_round_limit=3)
        tracker.record_round(1, 1, 1)
        summary = tracker.summary()
        assert summary["total_rounds"] == 1
        assert summary["converged"] is False
        assert len(summary["history"]) == 1


# ── Safety Functions ─────────────────────────────────────────


class TestSafetyFunctions:
    def test_safe_tap_on_home(self) -> None:
        assert is_safe_exploration_action(
            "home", {"action": "Tap", "element": [500, 500]},
        )

    def test_blocks_action_on_payment_page(self) -> None:
        assert not is_safe_exploration_action(
            "payment", {"action": "Tap", "element": [500, 500]},
        )

    def test_blocks_action_on_checkout_page(self) -> None:
        assert not is_safe_exploration_action(
            "checkout", {"action": "Tap", "element": [500, 500]},
        )

    def test_blocks_unsafe_token_in_action(self) -> None:
        assert not is_safe_exploration_action(
            "home", {"action": "Tap", "text": "支付"},
        )
        assert not is_safe_exploration_action(
            "search_result", {"action": "Tap", "text": "checkout"},
        )

    def test_allows_finish_on_risky_page(self) -> None:
        assert is_safe_exploration_action(
            "payment", {"_metadata": "finish", "message": "done"},
        )

    def test_blocks_unsafe_reasoning(self) -> None:
        assert not is_safe_exploration_action(
            "home",
            {"action": "Tap", "element": [500, 500]},
            reasoning="我将点击支付按钮",
        )

    def test_allows_negated_unsafe_reasoning(self) -> None:
        assert is_safe_exploration_action(
            "home",
            {"action": "Tap", "element": [500, 500]},
            reasoning="不要点击支付按钮，我选择返回",
        )


# ── Transition Rejection ─────────────────────────────────────


class TestTransitionRejection:
    def test_allows_different_page_types(self) -> None:
        assert _generic_transition_rejection("home", {"action": "Tap"}, "search_input") == ""

    def test_rejects_self_loop(self) -> None:
        result = _generic_transition_rejection("home", {"action": "Tap"}, "home")
        assert "self-loop" in result

    def test_allows_self_loop_on_search_input(self) -> None:
        assert _generic_transition_rejection(
            "search_input", {"action": "Tap"}, "search_input",
        ) == ""

    def test_rejects_high_risk_source(self) -> None:
        result = _generic_transition_rejection("payment", {"action": "Tap"}, "cart")
        assert "high-risk" in result

    def test_rejects_high_risk_target(self) -> None:
        result = _generic_transition_rejection("cart", {"action": "Tap"}, "checkout")
        assert "high-risk" in result

    def test_rejects_wait_action(self) -> None:
        result = _generic_transition_rejection("home", {"action": "Wait"}, "search")
        assert "non-navigation" in result

    def test_allows_type_action(self) -> None:
        assert _generic_transition_rejection(
            "search_input", {"action": "Type"}, "search_result",
        ) == ""


# ── System Prompt ────────────────────────────────────────────


class TestSystemPrompt:
    def test_contains_action_syntax(self) -> None:
        prompt = _build_autonomous_system_prompt()
        assert 'do(action="Tap"' in prompt
        assert 'do(action="Back")' in prompt
        assert 'finish(message=' in prompt

    def test_contains_safety_constraints(self) -> None:
        prompt = _build_autonomous_system_prompt()
        assert "不要下单" in prompt
        assert "不要进行支付" in prompt

    def test_contains_autonomous_identity(self) -> None:
        prompt = _build_autonomous_system_prompt()
        assert "自主探索" in prompt


# ── PageInfo Bridge ──────────────────────────────────────────


class TestPageInfoBridge:
    def test_converts_enum_to_string(self) -> None:
        page = PageInfo(
            page_type=ShoppingPageType.HOME,
            semantic_summary="电商首页推荐流",
            elements={"search_bar": "搜索框"},
            screenshot_hash="abc123",
            app="淘宝",
        )
        d = _page_info_to_dict(page)
        assert d["page_type"] == "home"
        assert d["app"] == "淘宝"
        assert d["summary"] == "电商首页推荐流"
        assert d["elements"]["search_bar"] == "搜索框"


# ── JobExecutionResult ───────────────────────────────────────


class TestJobExecutionResult:
    def _make_job(self) -> ExplorationJob:
        return ExplorationJob(
            job_id="test-job-1",
            functionality_cluster_id="cluster-1",
            target_description="Explore search function",
            reason="unverified cluster",
            max_steps=3,
            priority=0.8,
        )

    def test_to_dict(self) -> None:
        result = JobExecutionResult(
            job=self._make_job(),
            steps_taken=2,
            new_page_types_discovered=("search_result",),
            new_transitions_discovered=1,
            outcome="success",
        )
        d = result.to_dict()
        assert d["job_id"] == "test-job-1"
        assert d["steps_taken"] == 2
        assert d["outcome"] == "success"
        assert "search_result" in d["new_page_types_discovered"]

    def test_deviation_result(self) -> None:
        result = JobExecutionResult(
            job=self._make_job(),
            steps_taken=1,
            outcome="deviation",
            deviation_reason="expected product_detail, got cart",
        )
        d = result.to_dict()
        assert d["outcome"] == "deviation"
        assert "product_detail" in d["deviation_reason"]


# ── AutonomousExplorer._extract_and_cluster ──────────────────


class TestExtractAndCluster:
    def _make_explorer(self) -> AutonomousExplorer:
        with (
            patch("phone_agent.memory.autonomous_explorer.ActionHandler"),
            patch("phone_agent.memory.autonomous_explorer.PageClassifier"),
        ):
            explorer = AutonomousExplorer(
                app_name="测试App",
                device_factory=MagicMock(),
                model_client=MagicMock(),
                policy=ExplorationPolicy(),
                storage_dir="/tmp/test_autonomous",
                verbose=False,
            )
        return explorer

    def test_extracts_from_page_with_elements(self) -> None:
        explorer = self._make_explorer()
        page = PageInfo(
            page_type=ShoppingPageType.HOME,
            semantic_summary="首页推荐流",
            elements={
                "search_bar": "搜索框，点击可以进入搜索",
                "product_card": "商品卡片，耳机",
                "cart_icon": "购物车入口",
            },
            screenshot_hash="hash1",
            app="测试App",
        )
        new_clusters = explorer._extract_and_cluster(page)
        assert len(explorer._all_items) > 0
        functionality_items = [
            item for item in explorer._all_items
            if item.type == "functionality" and item.is_promotable
        ]
        assert len(functionality_items) >= 2

    def test_no_duplicates_on_repeat(self) -> None:
        explorer = self._make_explorer()
        page = PageInfo(
            page_type=ShoppingPageType.HOME,
            semantic_summary="首页推荐流",
            elements={"search_bar": "搜索框"},
            screenshot_hash="hash1",
            app="测试App",
        )
        explorer._extract_and_cluster(page)
        count_after_first = len(explorer._all_items)
        explorer._extract_and_cluster(page)
        assert len(explorer._all_items) == count_after_first

    def test_different_pages_add_items(self) -> None:
        explorer = self._make_explorer()
        page1 = PageInfo(
            page_type=ShoppingPageType.HOME,
            semantic_summary="首页",
            elements={"search_bar": "搜索框"},
            screenshot_hash="h1",
            app="测试App",
        )
        page2 = PageInfo(
            page_type=ShoppingPageType.SEARCH_RESULT,
            semantic_summary="搜索结果",
            elements={"product_card": "商品卡片", "filter_btn": "筛选按钮"},
            screenshot_hash="h2",
            app="测试App",
        )
        explorer._extract_and_cluster(page1)
        count1 = len(explorer._all_items)
        explorer._extract_and_cluster(page2)
        assert len(explorer._all_items) > count1


# ── AutonomousExplorer._record_transition ────────────────────


class TestRecordTransition:
    def _make_explorer(self) -> AutonomousExplorer:
        with (
            patch("phone_agent.memory.autonomous_explorer.ActionHandler"),
            patch("phone_agent.memory.autonomous_explorer.PageClassifier"),
        ):
            explorer = AutonomousExplorer(
                app_name="测试App",
                device_factory=MagicMock(),
                model_client=MagicMock(),
                policy=ExplorationPolicy(),
                storage_dir="/tmp/test_autonomous",
                verbose=False,
            )
        return explorer

    def test_records_valid_transition(self) -> None:
        explorer = self._make_explorer()
        source = PageInfo(
            page_type=ShoppingPageType.HOME,
            semantic_summary="首页",
            elements={},
            screenshot_hash="h1",
            app="测试App",
        )
        target = PageInfo(
            page_type=ShoppingPageType.SEARCH_INPUT,
            semantic_summary="搜索页",
            elements={},
            screenshot_hash="h2",
            app="测试App",
        )
        recorded = explorer._record_transition(source, {"action": "Tap", "element": [500, 100]}, target)
        assert recorded
        assert len(explorer._transitions) == 1
        assert len(explorer._rejected_transitions) == 0

    def test_rejects_self_loop(self) -> None:
        explorer = self._make_explorer()
        page = PageInfo(
            page_type=ShoppingPageType.HOME,
            semantic_summary="首页",
            elements={},
            screenshot_hash="h1",
            app="测试App",
        )
        recorded = explorer._record_transition(page, {"action": "Tap"}, page)
        assert not recorded
        assert len(explorer._rejected_transitions) == 1

    def test_rejects_high_risk_boundary(self) -> None:
        explorer = self._make_explorer()
        source = PageInfo(
            page_type=ShoppingPageType.CART,
            semantic_summary="购物车",
            elements={},
            screenshot_hash="h1",
            app="测试App",
        )
        target = PageInfo(
            page_type=ShoppingPageType.CHECKOUT,
            semantic_summary="结算页",
            elements={},
            screenshot_hash="h2",
            app="测试App",
        )
        recorded = explorer._record_transition(source, {"action": "Tap"}, target)
        assert not recorded

    def test_creates_functionality_item_from_transition(self) -> None:
        explorer = self._make_explorer()
        source = PageInfo(
            page_type=ShoppingPageType.HOME,
            semantic_summary="首页",
            elements={},
            screenshot_hash="h1",
            app="测试App",
        )
        target = PageInfo(
            page_type=ShoppingPageType.SEARCH_INPUT,
            semantic_summary="搜索页",
            elements={},
            screenshot_hash="h2",
            app="测试App",
        )
        explorer._record_transition(source, {"action": "Tap", "element": [500, 100]}, target)
        assert any(
            item.source_kind == "transition" for item in explorer._all_items
        )

    def test_feeds_edge_lifecycle(self) -> None:
        explorer = self._make_explorer()
        source = PageInfo(
            page_type=ShoppingPageType.HOME,
            semantic_summary="首页",
            elements={},
            screenshot_hash="h1",
            app="测试App",
        )
        target = PageInfo(
            page_type=ShoppingPageType.SEARCH_INPUT,
            semantic_summary="搜索页",
            elements={},
            screenshot_hash="h2",
            app="测试App",
        )
        explorer._record_transition(source, {"action": "Tap", "element": [500, 100]}, target)
        assert len(explorer.edge_lifecycle._records) > 0
