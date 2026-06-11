"""Regression tests for SpecGuard HITL backfill / sensitive-page guarding /
model-agnostic structured context (P4/P5/P9/E7 fixes)."""

from types import SimpleNamespace

from phone_agent.agent import PhoneAgent
from phone_agent.config.shopping_config import ShoppingConfig
from phone_agent.core.spec_guard import SpecGuard


def _spec_guard() -> tuple[SpecGuard, str]:
    config = ShoppingConfig.load()
    return SpecGuard(config), next(iter(config.apps))


_COMMIT_ACTION = {
    "_metadata": "do",
    "action": "Tap",
    "semantic_target": "confirm_spec_add_to_cart",
}


def test_interact_reply_folded_into_task_satisfies_spec_guard():
    """P9: after the reply is folded into the task text, SpecGuard must stop
    re-asking — the folded specs count as explicit requirements."""
    guard, app = _spec_guard()
    thinking = "这里需要选择规格，然后确认加入购物车。"

    # Without specs the guard intercepts (the question gets asked once)
    before = guard.check(
        action=dict(_COMMIT_ACTION), thinking=thinking, current_app=app,
        page_type="spec_selection", task="买一个 iPhone，加入购物车", vlm_plan={},
    )
    assert before is not None and before["action"] == "Interact"

    # The folded reply makes the constraint explicit → no second question
    after = guard.check(
        action=dict(_COMMIT_ACTION), thinking="我需要在规格弹窗点击确认加入购物车。",
        current_app=app, page_type="spec_selection",
        task="买一个 iPhone，加入购物车（用户补充：银色 512G）", vlm_plan={},
    )
    assert after is None


def test_spec_guard_engages_on_payment_page():
    """E7: sensitive (FLAG_SECURE) screenshots are treated as payment pages —
    the guard must actually engage for page_type='payment'."""
    guard, app = _spec_guard()

    guarded = guard.check(
        action=dict(_COMMIT_ACTION),
        thinking="点击提交订单。",
        current_app=app,
        page_type="payment",
        task="买一个 iPhone，加入购物车",
        vlm_plan={},
    )
    assert guarded is not None
    assert guarded["action"] == "Interact"


def test_step_parts_without_screen_info_for_adapter_models():
    """P5: non-AutoGLM models receive the structured parts without the
    Screen Info block (their adapters render screen state themselves)."""
    agent = object.__new__(PhoneAgent)
    agent._task_plan = SimpleNamespace(
        steps=[1],
        status_text=lambda: "1. 搜索 [进行中]",
        goal_slots={"color": "银色"},
    )
    agent._step_summaries = ["打开了淘宝"]
    agent.action_advisor = None
    agent.memory_manager = None
    agent._current_task = "买银色 iPhone"
    agent._last_user_reply = "预算 5000 以内"

    parts = agent._build_step_user_parts(
        current_app="淘宝",
        page_type="search_result",
        available_actions=None,
        graph_hint="[图谱路径参考] hint",
        include_screen_info=False,
    )

    text = "\n\n".join(parts)
    assert "【任务计划】" in text
    assert "【关键约束】" in text
    assert "【执行历史】" in text
    assert "【图谱导航】" in text
    assert "[用户补充约束]: 预算 5000 以内" in text
    assert "** Screen Info **" not in text
    # reply is consumed exactly once
    assert agent._last_user_reply is None
