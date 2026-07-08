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


def test_price_guard_engages_on_product_detail_popup():
    """Spec popups are often classified product_detail — the price guard
    must engage there too (a ¥172 add-to-cart slipped through)."""
    guard, app = _spec_guard()

    guarded = guard.check(
        action={"_metadata": "do", "action": "Tap", "semantic_target": "add_to_cart"},
        thinking="点击加入购物车按钮。",
        current_app=app,
        page_type="product_detail",
        task="买蓝牙耳机，要求500-1000元内",
        vlm_plan={"specs": {"price_range": "500-1000元"}},
        current_price=172.21,
    )
    assert guarded is not None
    assert "超出" in guarded["message"]


def test_planner_instruction_leads_step_parts():
    agent = object.__new__(PhoneAgent)
    agent._task_plan = None
    agent._step_summaries = []
    agent.action_advisor = None
    agent.memory_manager = None
    agent._current_task = "买蓝牙耳机"
    agent._last_user_reply = None

    parts = agent._build_step_user_parts(
        current_app="淘宝",
        page_type="filter_panel",
        available_actions=None,
        graph_hint="",
        include_screen_info=False,
        planner_instruction="在最低价输入框输入500",
    )

    assert parts[0].startswith("【当前指令】")
    assert "在最低价输入框输入500" in parts[0]


def test_main_planner_default_off(monkeypatch):
    monkeypatch.delenv("PHONE_AGENT_STRONG_PLANNER", raising=False)
    from phone_agent.step_planner import main_planner_enabled
    assert main_planner_enabled() is False
    monkeypatch.setenv("PHONE_AGENT_STRONG_PLANNER", "1")
    assert main_planner_enabled() is True


def test_completion_evidence_gate():
    agent = object.__new__(PhoneAgent)
    agent._vlm_plan = {"target_page": "spec_selection"}
    agent._visited_pages = {"home", "search_result"}
    assert agent._completion_evidence() is False

    agent._visited_pages.add("spec_selection")
    assert agent._completion_evidence() is True

    agent._vlm_plan = {}
    agent._visited_pages = set()
    assert agent._completion_evidence() is True


def test_mechanical_price_verdict_injected():
    agent = object.__new__(PhoneAgent)
    agent._task_plan = None
    agent._step_summaries = []
    agent.action_advisor = None
    agent._current_task = "买蓝牙耳机，要求500-1000元内"
    agent._last_user_reply = None
    agent._vlm_plan = {}
    agent._spec_guard = SpecGuard(ShoppingConfig.load())
    agent.memory_manager = SimpleNamespace(
        state=SimpleNamespace(products=[SimpleNamespace(price=1424.05)]),
    )

    parts = agent._build_step_user_parts(
        current_app="淘宝", page_type="product_detail",
        available_actions=None, graph_hint="", include_screen_info=False,
    )
    text = "\n\n".join(parts)
    assert "⛔ 系统判定" in text
    assert "1424.05" in text


def test_reset_dialogue_context_rebuilds_system_message():
    """Recovery primitive: drop poisoned dialogue, keep durable memory."""
    agent = object.__new__(PhoneAgent)
    agent.agent_config = SimpleNamespace(system_prompt="SYS", verbose=False)
    agent._context = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": ""},  # poisoned empty turn
    ]
    agent._step_summaries = []
    agent._specialized_handler = None
    agent._adapter = SimpleNamespace()

    agent._reset_dialogue_context("完成声明被否决")

    assert len(agent._context) == 1
    assert agent._context[0]["role"] == "system"
    assert agent._step_summaries[-1].startswith("[上下文重置]")


def test_spec_popup_exit_is_ungrounded():
    from phone_agent.spatial.action_advisor import _UNGROUNDED_TRANSITIONS
    assert ("spec_selection", "product_detail") in _UNGROUNDED_TRANSITIONS
