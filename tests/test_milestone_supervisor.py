"""Milestone trigger decision table + checkpoint parsing with mocked VLM."""

import json
from types import SimpleNamespace

from phone_agent.milestone_supervisor import (
    CheckpointResult,
    MilestoneSupervisor,
    MilestoneTrigger,
    milestone_enabled,
)


# ── trigger decision table ───────────────────────────────────────────────


def _trigger(**kw) -> MilestoneTrigger:
    return MilestoneTrigger(interval=5, min_gap=2, max_calls=10, **kw)


def test_interval_trigger_fires_after_n_steps():
    t = _trigger()
    assert t.evaluate(4) is None
    assert t.evaluate(5) == "interval"
    t.record_fired(5, "interval")
    assert t.evaluate(7) is None
    assert t.evaluate(10) == "interval"


def test_subtask_done_respects_min_gap():
    t = _trigger()
    t.record_fired(4, "interval")
    assert t.evaluate(5, subtask_done=True) is None      # gap 1 < 2 → defer
    assert t.evaluate(6, subtask_done=True) == "subtask_done"


def test_stagnation_debounced_within_same_streak():
    t = _trigger()
    assert t.evaluate(3, stagnating=True) == "stagnation"
    t.record_fired(3, "stagnation")
    assert t.evaluate(6, stagnating=True) is None        # same streak
    t.evaluate(7, stagnating=False)                       # streak broken
    assert t.evaluate(9, stagnating=True) == "stagnation"


def test_finish_gate_bypasses_min_gap_and_caps_at_two():
    t = _trigger()
    t.record_fired(5, "interval")
    t.pending_finish_gate = True
    assert t.evaluate(6) == "finish_gate"                 # gap 1, still fires
    t.record_fired(6, "finish_gate")
    t.pending_finish_gate = True
    assert t.evaluate(7) == "finish_gate"
    t.record_fired(7, "finish_gate")
    t.pending_finish_gate = True
    assert t.evaluate(8) != "finish_gate"                 # cap = 2


def test_max_calls_silences_all_triggers():
    t = MilestoneTrigger(interval=5, min_gap=2, max_calls=1)
    t.record_fired(5, "interval")
    assert t.evaluate(20, subtask_done=True, stagnating=True) is None


def test_two_failures_disable():
    t = _trigger()
    t.record_failure()
    assert not t.disabled
    t.record_failure()
    assert t.disabled
    assert t.evaluate(99) is None


# ── checkpoint parsing with mocked provider chain ────────────────────────

_CKPT_JSON = json.dumps({
    "thought": "搜索已完成",
    "current_situation": "已进入搜索结果页",
    "completed_step_ids": [1, "2", "bad"],
    "new_facts": [{"fact": "已搜索蓝牙耳机", "kind": "other"}, "ignored"],
    "revised_subtasks": [{"description": "打开筛选面板", "target_page": "filter_panel"}],
    "task_complete": False,
    "task_success": False,
    "task_blocked": False,
    "message": "",
}, ensure_ascii=False)


def _fake_client(content: str):
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )
    return SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=lambda **kw: response)
    ))


def _supervisor_with(clients: list) -> MilestoneSupervisor:
    sup = object.__new__(MilestoneSupervisor)
    providers = [SimpleNamespace(model=f"m{i}") for i in range(len(clients))]
    client_map = dict(zip((p.model for p in providers), clients))
    sup._proxy = SimpleNamespace(
        providers=providers,
        max_image_width=720,
        _client_for_provider=lambda p: client_map[p.model],
        _crop_screenshot=lambda b64, w, h, mw: b64,
    )
    sup.last_raw = ""
    return sup


def test_checkpoint_parses_valid_json():
    sup = _supervisor_with([_fake_client(_CKPT_JSON)])
    result = sup.checkpoint(
        "b64", 1000, 2000,
        memory_render="【会话记忆】...", recent_steps=["Step 1: 搜索"],
        page_type="search_result", trigger="interval",
    )
    assert isinstance(result, CheckpointResult)
    assert result.completed_step_ids == [1, 2]            # "bad" filtered
    assert result.new_facts == [{"fact": "已搜索蓝牙耳机", "kind": "other"}]
    assert result.revised_subtasks[0]["description"] == "打开筛选面板"
    assert result.trigger == "interval"


def test_checkpoint_parses_fenced_json():
    fenced = f"```json\n{_CKPT_JSON}\n```"
    sup = _supervisor_with([_fake_client(fenced)])
    result = sup.checkpoint(
        "b64", 1000, 2000, memory_render="", recent_steps=[],
        page_type="home", trigger="interval",
    )
    assert result is not None and result.current_situation == "已进入搜索结果页"


def test_checkpoint_falls_back_across_providers():
    class _Boom:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    raise RuntimeError("provider down")

    sup = _supervisor_with([_Boom(), _fake_client(_CKPT_JSON)])
    result = sup.checkpoint(
        "b64", 1000, 2000, memory_render="", recent_steps=[],
        page_type="home", trigger="stagnation",
    )
    assert result is not None


def test_checkpoint_returns_none_when_all_fail():
    sup = _supervisor_with([_fake_client("not json at all")])
    result = sup.checkpoint(
        "b64", 1000, 2000, memory_render="", recent_steps=[],
        page_type="home", trigger="interval",
    )
    assert result is None


def test_milestone_default_on(monkeypatch):
    monkeypatch.delenv("PHONE_AGENT_MILESTONE", raising=False)
    assert milestone_enabled() is True
    monkeypatch.setenv("PHONE_AGENT_MILESTONE", "0")
    assert milestone_enabled() is False
