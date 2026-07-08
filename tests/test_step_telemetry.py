"""Per-step telemetry surface (PhoneAgent.step_observer) and WebUI rendering.

The WebUI no longer duplicates the agent loop — it wraps PhoneAgent and
consumes telemetry events.  These tests pin the contract between them:
every _execute_step emits exactly one event carrying mode / dispatch /
graph_hint, and the WebUI panel renders it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from phone_agent.agent import PhoneAgent, StepResult


def _bare_agent() -> PhoneAgent:
    agent = object.__new__(PhoneAgent)
    agent._step_count = 0
    agent._step_telemetry = {}
    agent.last_step_info = {}
    agent.step_observer = None
    return agent


def test_execute_step_emits_one_telemetry_event():
    agent = _bare_agent()
    events = []
    agent.step_observer = events.append

    def fake_impl(user_prompt=None, is_first=False):
        agent._tele(
            dispatch="fast_path",
            mode="navigate",
            page_type="home",
            graph_hint="[RuntimeDAG] plan=abc step=1/3",
        )
        return StepResult(
            success=True, finished=False,
            action={"action": "Tap"}, thinking="thought", message="ok",
        )

    agent._execute_step_impl = fake_impl
    result = agent._execute_step(None, False)

    assert result.success is True
    assert len(events) == 1
    info = events[0]
    assert info["dispatch"] == "fast_path"
    assert info["mode"] == "navigate"
    assert "[RuntimeDAG]" in info["graph_hint"]
    assert info["success"] is True
    assert info["finished"] is False
    assert info["thinking"] == "thought"
    assert agent.last_step_info == info


def test_step_observer_exception_does_not_break_step():
    agent = _bare_agent()

    def boom(info):
        raise RuntimeError("observer crashed")

    agent.step_observer = boom
    agent._execute_step_impl = lambda u=None, f=False: StepResult(
        success=True, finished=True, action=None, thinking="", message="done",
    )

    result = agent._execute_step(None, False)
    assert result.finished is True
    assert agent.last_step_info["message"] == "done"


def test_telemetry_defaults_when_impl_records_nothing():
    agent = _bare_agent()
    agent._execute_step_impl = lambda u=None, f=False: StepResult(
        success=False, finished=False, action=None, thinking="", message=None,
    )

    agent._execute_step(None, False)
    info = agent.last_step_info
    assert info["dispatch"] == "vlm"
    assert info["mode"] == "explore"
    assert info["graph_hint"] == ""


# ---------------------------------------------------------------------------
# WebUI rendering of telemetry
# ---------------------------------------------------------------------------

import webui  # noqa: E402


def test_format_graph_panel_renders_copilot_step():
    info = {
        "step": 3,
        "page_type": "search_result",
        "mode": "verify_with_vlm",
        "dispatch": "vlm",
        "graph_hint": "[图谱路径参考] 当前页面: search_result，下一步目标页面: product_detail",
        "available_actions": 2,
        "runtime_metrics": {
            "runtime_dag_hits": 4,
            "runtime_dag_misses": 2,
            "page_classifier_skips": 3,
        },
    }
    panel = webui.format_graph_panel(info, {"vlm": 1, "copilot": 2, "fast_path": 3})

    assert "Co-pilot" in panel
    assert "图谱路径参考" in panel
    assert "DAG 命中 4/6" in panel
    assert "⚡ Fast×3" in panel
    assert "🤝 Co-pilot×2" in panel


def test_format_graph_panel_fast_path_postcondition():
    info = {
        "step": 2,
        "page_type": "home",
        "mode": "navigate",
        "dispatch": "fast_path",
        "expected_postcondition": "search_input",
        "actual_postcondition": "search_input",
    }
    panel = webui.format_graph_panel(info, {"fast_path": 1})
    assert "Fast Path" in panel
    assert "`search_input` ✅" in panel


def test_count_dispatch_maps_verify_with_vlm_to_copilot():
    sa = object.__new__(webui.StreamingAgent)
    sa._dispatch_counters = {}

    sa._count_dispatch({"dispatch": "vlm", "mode": "verify_with_vlm"})
    sa._count_dispatch({"dispatch": "vlm", "mode": "explore"})
    sa._count_dispatch({"dispatch": "fast_path"})
    sa._count_dispatch({"dispatch": "fast_path_fallback"})

    assert sa._dispatch_counters == {
        "copilot": 1, "vlm": 1, "fast_path": 1, "fast_path_fallback": 1,
    }


def test_fmt_step_action_shows_dispatch_badge():
    line = webui.StreamingAgent._fmt_step_action({
        "step": 5,
        "dispatch": "fast_path",
        "page_type": "home",
        "success": True,
        "action": {"action": "Tap", "element": [100, 200]},
    })
    assert "Step 5" in line
    assert "Fast Path" in line
    assert "`Tap`" in line
