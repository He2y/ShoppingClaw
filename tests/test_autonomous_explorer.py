"""Tests for the AutonomousExplorer and its supporting components."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from phone_agent.memory.autonomous_explorer import (
    AutonomousExplorer,
    ConvergenceTracker,
    ExplorationPolicy,
    ExplorationSupervisor,
    JobExecutionResult,
    SupervisorDecision,
    _build_autonomous_system_prompt,
    _generic_transition_rejection,
    _page_info_to_dict,
    is_safe_exploration_action,
)
from phone_agent.memory.offline_explorer import PageInfo, ShoppingPageType


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
        assert len(d) >= 8


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

    def test_allows_navigation_on_checkout_page(self) -> None:
        assert is_safe_exploration_action(
            "checkout", {"action": "Tap", "element": [500, 500]},
        )
        assert is_safe_exploration_action(
            "checkout", {"action": "Back"},
        )

    def test_blocks_submit_order_on_checkout(self) -> None:
        assert not is_safe_exploration_action(
            "checkout", {"action": "Tap", "element": [500, 900]},
            instruction="点击提交订单按钮",
        )
        assert not is_safe_exploration_action(
            "checkout", {"action": "Tap", "element": [500, 900]},
            instruction="点击立即下单",
        )

    def test_blocks_payment_on_checkout_page(self) -> None:
        assert not is_safe_exploration_action(
            "checkout", {"action": "Tap"},
            instruction="点击立即支付",
        )

    def test_blocks_unsafe_token_in_action(self) -> None:
        assert not is_safe_exploration_action(
            "home", {"action": "Tap", "text": "立即支付"},
        )
        assert not is_safe_exploration_action(
            "search_result", {"action": "Tap", "text": "确认支付"},
        )

    def test_allows_finish_on_risky_page(self) -> None:
        assert is_safe_exploration_action(
            "payment", {"_metadata": "finish", "message": "done"},
        )

    def test_blocks_unsafe_action_text(self) -> None:
        assert not is_safe_exploration_action(
            "home",
            {"action": "Tap", "element": [500, 500], "text": "确认支付"},
        )

    def test_allows_checkout_navigation(self) -> None:
        assert is_safe_exploration_action(
            "cart", {"action": "Tap", "text": "去结算"},
        )
        assert is_safe_exploration_action(
            "checkout", {"action": "Tap", "text": "返回"},
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

    def test_allows_checkout_target(self) -> None:
        result = _generic_transition_rejection("cart", {"action": "Tap"}, "checkout")
        assert result == ""

    def test_rejects_payment_target(self) -> None:
        result = _generic_transition_rejection("checkout", {"action": "Tap"}, "payment")
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
    def test_executor_prompt_contains_action_syntax(self) -> None:
        prompt = _build_autonomous_system_prompt()
        assert 'do(action="Tap"' in prompt
        assert 'do(action="Back")' in prompt

    def test_executor_prompt_forbids_finish(self) -> None:
        prompt = _build_autonomous_system_prompt()
        assert "禁止" in prompt
        assert "finish" in prompt

    def test_supervisor_prompt_contains_strategy(self) -> None:
        prompt = ExplorationSupervisor._SYSTEM_PROMPT
        assert "导航图谱" in prompt
        assert "未探索" in prompt
        assert "should_stop" in prompt


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
    def test_to_dict(self) -> None:
        result = JobExecutionResult(
            job_description="Explore search function",
            steps_taken=2,
            new_page_types_discovered=("search_result",),
            new_transitions_discovered=1,
            outcome="success",
        )
        d = result.to_dict()
        assert d["job_description"] == "Explore search function"
        assert d["steps_taken"] == 2
        assert d["outcome"] == "success"
        assert "search_result" in d["new_page_types_discovered"]

    def test_stalled_result(self) -> None:
        result = JobExecutionResult(
            job_description="Navigate to product",
            steps_taken=1,
            outcome="stalled",
        )
        d = result.to_dict()
        assert d["outcome"] == "stalled"


class TestSupervisorDecision:
    def test_to_dict(self) -> None:
        d = SupervisorDecision(
            page_type="home", page_summary="首页",
            reasoning="需要搜索", plan=("点击搜索框",),
        )
        assert d.to_dict()["page_type"] == "home"
        assert d.plan == ("点击搜索框",)
        assert not d.should_stop

    def test_parse_valid_json(self) -> None:
        supervisor = ExplorationSupervisor.__new__(ExplorationSupervisor)
        decision, elements = supervisor._parse(
            '{"page_type":"home","page_summary":"首页","visible_elements":["搜索框","购物车"],'
            '"reasoning":"test","plan":["点击搜索框"],"should_stop":false}'
        )
        assert decision.plan == ("点击搜索框",)
        assert decision.page_type == "home"
        assert not decision.should_stop
        assert "搜索框" in elements


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

    def test_allows_checkout_boundary(self) -> None:
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
        assert recorded

    def test_rejects_payment_boundary(self) -> None:
        explorer = self._make_explorer()
        source = PageInfo(
            page_type=ShoppingPageType.CHECKOUT,
            semantic_summary="结算页",
            elements={},
            screenshot_hash="h1",
            app="测试App",
        )
        target = PageInfo(
            page_type=ShoppingPageType.PAYMENT,
            semantic_summary="支付页",
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
