"""CRITICAL safety: never let the agent submit an order / pay.

Regression for the JD incident — the agent reached the 结算/order-confirmation
page (misclassified as product_detail), correctly tried to finish, but
final_confirm rejected it and the agent then tapped 打白条 (a payment button).
The hard safety stop must terminate on any checkout/payment context, keyed on
multiple signals (page type, sensitive flag, the model's own screen reading).
"""

from __future__ import annotations

import types

from phone_agent.agent import PhoneAgent, StepResult


# ── static detector ──────────────────────────────────────────────────


def test_detect_checkout_and_payment_page_types():
    assert PhoneAgent._is_payment_or_checkout_context({}, "", "checkout", False) is True
    assert PhoneAgent._is_payment_or_checkout_context({}, "", "payment", False) is True
    assert PhoneAgent._is_payment_or_checkout_context({}, "", "order_confirmation", False) is True


def test_detect_by_flag_secure_sensitive():
    assert PhoneAgent._is_payment_or_checkout_context({}, "点击", "product_detail", True) is True


def test_detect_jd_order_confirmation_from_model_thinking():
    # The exact failure: classifier said product_detail; the model's thinking
    # revealed the order-confirmation page.
    thinking = (
        "现在页面已经跳转到了订单确认页面。收货地址：铜川路... "
        "底部有立即支付按钮。商品已成功加入购物车并进入了结算页面。"
    )
    assert PhoneAgent._is_payment_or_checkout_context(
        {"action": "Tap"}, thinking, "product_detail", False
    ) is True


def test_detect_da_baitiao_payment_tap():
    thinking = "当前在京东的结算页面，页面底部有红色按钮'打白条'。我将尝试点击页面底部的'打白条'按钮。"
    assert PhoneAgent._is_payment_or_checkout_context(
        {"action": "Tap", "element": [499, 939]}, thinking, "product_detail", False
    ) is True


def test_planning_mention_does_not_false_trigger():
    # Early planning mentions neither checkout-page markers nor payment commits.
    thinking = "我需要搜索4K显示器，筛选价格3000以内，然后选一个商品。"
    assert PhoneAgent._is_payment_or_checkout_context(
        {"action": "Tap"}, thinking, "search_result", False
    ) is False


def test_add_to_cart_does_not_trigger():
    thinking = "所有参数符合要求，现在点击加入购物车按钮。"
    assert PhoneAgent._is_payment_or_checkout_context(
        {"action": "Tap"}, thinking, "product_detail", False
    ) is False


# ── the method (stub self) ───────────────────────────────────────────


def _stub_agent(verbose=False, shopping=True):
    return types.SimpleNamespace(
        _spec_guard=types.SimpleNamespace(_is_shopping_app=lambda app: shopping),
        agent_config=types.SimpleNamespace(verbose=verbose),
    )


def test_safety_stop_terminates_on_checkout_context():
    stub = _stub_agent()
    r = PhoneAgent._payment_safety_stop(
        stub, {"action": "Tap"}, "已进入结算页面，点击立即支付", "product_detail", "京东",
        is_sensitive=False,
    )
    assert isinstance(r, StepResult)
    assert r.finished is True
    assert r.success is True
    assert "安全" in (r.message or "")
    assert r.action and r.action.get("_metadata") == "finish"


def test_safety_stop_none_for_non_shopping_app():
    stub = _stub_agent(shopping=False)
    r = PhoneAgent._payment_safety_stop(
        stub, {"action": "Tap"}, "立即支付", "payment", "微信", is_sensitive=False,
    )
    assert r is None


def test_safety_stop_none_for_normal_browsing():
    stub = _stub_agent()
    r = PhoneAgent._payment_safety_stop(
        stub, {"action": "Tap"}, "点击搜索框输入关键词", "search_input", "京东", is_sensitive=False,
    )
    assert r is None
