"""Regression tests for the 2026-06-11 real-device findings:
query pollution, mechanical price-bound interception."""

from phone_agent.agent import PhoneAgent
from phone_agent.config.shopping_config import ShoppingConfig
from phone_agent.core.spec_guard import SpecGuard


def test_sanitize_search_query_strips_price_and_features():
    cleaned = PhoneAgent._sanitize_search_query(
        "蓝牙耳机 降噪 500-1000元",
        {"price_range": "500-1000元", "feature": "降噪"},
    )
    assert cleaned == "蓝牙耳机"


def test_sanitize_search_query_keeps_clean_query():
    assert PhoneAgent._sanitize_search_query("iPhone 17 pro max", {"storage": "512G"}) == "iPhone 17 pro max"


def test_sanitize_search_query_falls_back_when_emptied():
    assert PhoneAgent._sanitize_search_query("500-1000元", {}) == "500-1000元"


def test_extract_price_bounds():
    f = SpecGuard._extract_price_bounds
    assert f("要求500-1000元内，有降噪功能", None) == (500.0, 1000.0)
    assert f("预算1000元以内", None) == (None, 1000.0)
    assert f("不超过800元", None) == (None, 800.0)
    assert f("300元以上", None) == (300.0, None)
    assert f("买个蓝牙耳机", None) is None
    assert f("", {"specs": {"price_range": "500-1000元"}}) == (500.0, 1000.0)


def test_price_guard_intercepts_out_of_budget_commit():
    config = ShoppingConfig.load()
    guard = SpecGuard(config)
    app = next(iter(config.apps))

    guarded = guard.check(
        action={"_metadata": "do", "action": "Tap", "semantic_target": "add_to_cart"},
        thinking="点击加入购物车按钮。",
        current_app=app,
        page_type="spec_selection",
        task="去淘宝帮我买一个蓝牙耳机，要求500-1000元内，有降噪功能",
        vlm_plan={"specs": {"price_range": "500-1000元", "feature": "降噪"}},
        current_price=1424.05,
    )

    assert guarded is not None
    assert guarded["action"] == "Interact"
    assert "超出" in guarded["message"]


def test_price_guard_passes_in_budget_commit():
    config = ShoppingConfig.load()
    guard = SpecGuard(config)
    app = next(iter(config.apps))

    guarded = guard.check(
        action={"_metadata": "do", "action": "Tap", "semantic_target": "add_to_cart"},
        thinking="点击加入购物车按钮。",
        current_app=app,
        page_type="spec_selection",
        task="去淘宝帮我买一个蓝牙耳机，要求500-1000元内，有降噪功能",
        vlm_plan={"specs": {"price_range": "500-1000元"}},
        current_price=661.0,
    )

    # In-budget price must not trigger the price interception (any further
    # interception, if present, comes from spec logic — never mentions 超出)
    assert guarded is None or "超出" not in guarded.get("message", "")
