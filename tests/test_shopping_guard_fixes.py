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


# --- Fix: price/feature are search FILTERS, not SKU variants ---------------

def test_requested_specs_excludes_price_filter():
    config = ShoppingConfig.load()
    guard = SpecGuard(config)
    specs = guard._requested_spec_slots(
        "去淘宝买个蓝牙鼠标，价格100-200元，加入购物车",
        {"specs": {"price_range": "100-200元"}},
    )
    # A price range constrains the search, not the SKU variant -> excluded.
    assert "price_range" not in specs
    assert specs == {}


def test_requested_specs_keeps_genuine_variants_drops_filters():
    config = ShoppingConfig.load()
    guard = SpecGuard(config)
    specs = guard._requested_spec_slots(
        "买iPhone 17 银色 512G",
        {"specs": {"color": "银色", "price_range": "5000-8000元", "brand": "Apple", "feature": "降噪"}},
    )
    assert specs.get("颜色") == "银色"
    assert specs.get("容量") == "512G"
    for filtered in ("price_range", "brand", "feature", "Apple"):
        assert filtered not in specs


def test_check_asks_variant_when_only_price_given():
    """Regression: a price-only task must NOT suppress variant clarification.
    Before the fix, price_range counted as 'SKU specified' -> 不追问用户."""
    config = ShoppingConfig.load()
    guard = SpecGuard(config)
    app = next(iter(config.apps))
    guarded = guard.check(
        action={"_metadata": "do", "action": "Tap", "semantic_target": "add_to_cart"},
        thinking="点击加入购物车按钮，有多种颜色可选。",
        current_app=app,
        page_type="product_detail",
        task="去淘宝买个蓝牙鼠标，价格100-200元，加入购物车",
        vlm_plan={"specs": {"price_range": "100-200元"}},
        current_price=114.0,  # in budget -> no price red-line
    )
    assert guarded is not None, "should clarify the unspecified color/variant"
    assert guarded["action"] == "Interact"
    assert "超出" not in guarded.get("message", "")  # not a price interception


def test_check_passes_when_variant_specified_and_selected():
    config = ShoppingConfig.load()
    guard = SpecGuard(config)
    app = next(iter(config.apps))
    guarded = guard.check(
        action={"_metadata": "do", "action": "Tap", "semantic_target": "add_to_cart"},
        thinking="已选中银色，点击加入购物车。",
        current_app=app,
        page_type="spec_selection",
        task="买iPhone 银色 加入购物车",
        vlm_plan={"specs": {"color": "银色"}},
        current_price=None,
    )
    # Variant specified AND thinking shows it's selected -> let it proceed.
    assert guarded is None
