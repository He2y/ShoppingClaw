"""Regression tests for the first real-device run failures (京东 cold start).

Observed failure chain:
1. Default task text said 淘宝 while exploring 京东 (profile/schema priority bug).
2. Belief repair read task-constraint restatements ("不确认地址" / numbered plan
   lines) as current-page evidence, relabeled search_input as address (high
   risk), and the whole exploration stopped after one step.
3. VLM-generated profile put 加入购物车/立即购买 into unsafe_tokens and mixed
   interference/abstract types into coverage.
"""

from types import SimpleNamespace

from phone_agent.memory.exploration.page_evidence import infer_page_type_from_reasoning
from phone_agent.memory.exploration.task_builder import build_default_task
from phone_agent.memory.exploration.cli import (
    _resolve_default_task,
    _resolve_schema_name,
    _resolve_storage_dir,
)
from phone_agent.spatial.schema_registry import get_default_registry


# ── page evidence: constraint/plan text must not become page belief ──


def test_constraint_restatement_does_not_become_address_evidence():
    reasoning = (
        "当前截图显示：\n"
        "- 当前在京东App的搜索页面\n"
        "- 显示搜索框，搜索框中有格力空调\n"
        "- 但用户特别强调\"只执行安全探索动作，不提交订单、不支付、不确认地址\"\n"
        "- 如果进入登录、支付、地址或订单确认页，立刻返回\n"
    )
    assert infer_page_type_from_reasoning(reasoning) != "address"


def test_numbered_plan_lines_are_not_page_evidence():
    reasoning = (
        "当前已经在京东App中，我可以看到京东的首页。我需要：\n"
        "1. 先点击搜索框（搜索输入）\n"
        "2. 搜索某个商品\n"
        "3. 进入搜索结果\n"
    )
    assert infer_page_type_from_reasoning(reasoning) == "home"


def test_bare_address_token_is_no_longer_sufficient():
    reasoning = "当前截图显示订单页面包含地址信息和商品清单。"
    assert infer_page_type_from_reasoning(reasoning) != "address"


def test_real_address_page_evidence_still_detected():
    reasoning = "当前截图显示的是地址管理页面，有添加新地址按钮和地址列表。"
    assert infer_page_type_from_reasoning(reasoning) == "address"


# ── default task: {app} template, profile priority ──


def test_schema_default_task_uses_app_display_name():
    schema = get_default_registry().merged("shopping")
    task = build_default_task(schema, None, None, "京东")
    assert "京东" in task
    assert "淘宝" not in task


def test_schema_default_task_keeps_taobao_text_for_taobao():
    schema = get_default_registry().merged("shopping")
    task = build_default_task(schema, None, None, "淘宝")
    assert task.startswith("覆盖淘宝购物核心空间骨架")


def test_profile_default_task_wins_over_schema():
    schema = get_default_registry().merged("shopping")
    profile = SimpleNamespace(default_task="探索京东的国补频道")
    assert build_default_task(schema, profile, None, "京东") == "探索京东的国补频道"


# ── cli resolution helpers ──


def test_cli_resolves_jd_task_via_registry_schema():
    args = SimpleNamespace(app="京东", schema=None)
    schema_name = _resolve_schema_name(args, None)
    assert schema_name == "shopping"
    task = _resolve_default_task(args, None, schema_name)
    assert "京东" in task
    assert "淘宝" not in task


def test_cli_storage_dir_defaults_to_canonical_app_id():
    args = SimpleNamespace(app="京东", storage_dir=None)
    assert _resolve_storage_dir(args).endswith("exploration/jd")
    args2 = SimpleNamespace(app="京东", storage_dir="custom/dir")
    assert _resolve_storage_dir(args2) == "custom/dir"
