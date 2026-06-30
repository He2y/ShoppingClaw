"""Fix: the milestone supervisor's verdict at a REGULAR checkpoint must finish
the task — it was previously discarded, so a screenshot-confirmed add-to-cart
got re-tapped repeatedly (over-adding) before the executor noticed completion.
"""

from __future__ import annotations

from phone_agent.agent import PhoneAgent, StepResult
from phone_agent.milestone_supervisor import CheckpointResult


def test_task_complete_verdict_finishes_with_success():
    ckpt = CheckpointResult(
        task_complete=True, task_success=True,
        current_situation="目标蓝牙鼠标已成功加入购物车",
    )
    r = PhoneAgent._supervisor_finish_verdict(ckpt)
    assert isinstance(r, StepResult)
    assert r.finished is True
    assert r.success is True
    assert "购物车" in (r.message or "")
    assert r.action and r.action.get("_metadata") == "finish"


def test_task_blocked_verdict_finishes_as_failure():
    ckpt = CheckpointResult(task_blocked=True, message="验证码循环无法继续")
    r = PhoneAgent._supervisor_finish_verdict(ckpt)
    assert r is not None
    assert r.finished is True
    assert r.success is False
    assert "验证码" in (r.message or "")


def test_incomplete_verdict_does_not_finish():
    ckpt = CheckpointResult(task_complete=False, task_blocked=False, current_situation="仍在筛选")
    assert PhoneAgent._supervisor_finish_verdict(ckpt) is None


def test_optimistic_situation_without_flag_does_not_finish():
    # Prose claims completion but the structured flag is false — must NOT finish.
    ckpt = CheckpointResult(task_complete=False, current_situation="任务核心要求已完成")
    assert PhoneAgent._supervisor_finish_verdict(ckpt) is None


def test_none_checkpoint_does_not_finish():
    assert PhoneAgent._supervisor_finish_verdict(None) is None


def test_complete_but_unsuccessful_finishes_unsuccessful():
    ckpt = CheckpointResult(task_complete=True, task_success=False, message="目标商品缺货")
    r = PhoneAgent._supervisor_finish_verdict(ckpt)
    assert r is not None
    assert r.finished is True
    assert r.success is False
