"""Graph co-pilot hint must reach the VLM message.

Regression tests for the channel severed in commit 27c5a33: graph-produced
guidance ([图谱路径参考] / [RuntimeDAG] / [Task Plan] / domain priors) now
travels under the dedicated ``context_data["graph_hint"]`` key and the agent
injects it into the per-step user message.  ``semantic_context`` keeps only
the personalized-memory base consumed by ClarificationAgent.
"""

from phone_agent.agent import PhoneAgent
from phone_agent.memory.memory_manager import MemoryManager
from phone_agent.memory.spatial_graph_memory import (
    RuntimeDAG,
    SpatialGraphMemory,
    TransitionEdge,
)


class FakeGraphStore:
    driver = None

    def find_similar_tasks(self, task, app=None, top_k=3):
        return []

    def get_task_trajectory(self, task_id, max_steps=20):
        return {}


def _make_manager(tmp_path) -> MemoryManager:
    manager = MemoryManager(storage_dir=str(tmp_path), user_id="tester")
    manager.graph_store = FakeGraphStore()
    manager.runtime_graph_store = FakeGraphStore()
    manager.spatial_graph_memory = SpatialGraphMemory(manager.graph_store)
    return manager


# ---------------------------------------------------------------------------
# Controller level: hints land in graph_hint, not semantic_context
# ---------------------------------------------------------------------------


def test_vlm_verification_hint_goes_to_graph_hint(tmp_path):
    manager = _make_manager(tmp_path)
    controller = manager._ensure_graph_runtime_controller()

    next_action = {"type": "Tap", "target": "product card"}
    context_data = {"semantic_context": "[会话记忆] base", "graph_hint": ""}

    controller._inject_vlm_verification_hint(
        next_action, "search_result", "product_detail", context_data
    )

    assert next_action["_requires_vlm_verification"] is True
    assert "图谱路径参考" in context_data["graph_hint"]
    assert "product_detail" in context_data["graph_hint"]
    # semantic_context (personalized memory base) must stay untouched
    assert context_data["semantic_context"] == "[会话记忆] base"


def test_full_pipeline_spatial_summary_goes_to_graph_hint(tmp_path):
    manager = _make_manager(tmp_path)

    context = manager.locate_and_get_context(
        ui_hash="44444444dddddddd",
        semantic_layout="淘宝 首页",
        task="搜索耳机",
    )

    assert "SpatialGraph" in context.get("graph_hint", "")
    assert "SpatialGraph" not in context.get("semantic_context", "")


def test_runtime_dag_hint_goes_to_graph_hint(tmp_path):
    manager = _make_manager(tmp_path)
    home = manager.spatial_graph_memory.build_page_state(
        ui_hash="home",
        semantic_layout="淘宝 home",
        app="淘宝",
        page_type="home",
    )
    search_input = manager.spatial_graph_memory.build_page_state(
        ui_hash="search",
        semantic_layout="淘宝 search_input",
        app="淘宝",
        page_type="search_input",
    )
    edge = TransitionEdge(
        source_id=home.state_id,
        target_id=search_input.state_id,
        action_type="Tap",
        action_target="search bar",
        postcondition="search_input",
        confidence=0.9,
    )
    goal = manager.spatial_graph_memory.infer_goal("搜索耳机", app="淘宝")
    manager._runtime_dag = RuntimeDAG(
        plan_id="plan1",
        app="淘宝",
        goal_spec=goal,
        nodes={home.state_id: home, search_input.state_id: search_input},
        route=[edge],
    )

    context = manager.locate_and_get_context(
        ui_hash="home",
        semantic_layout="淘宝 home",
        task="搜索耳机",
        screen_dict={
            "ui_hash": "home",
            "semantic_layout": "淘宝 home",
            "app": "淘宝",
            "page_type": "home",
            "_runtime_hint": True,
        },
    )

    assert context["mode"] == "navigate"
    assert "[RuntimeDAG]" in context.get("graph_hint", "")
    assert "[RuntimeDAG]" not in context.get("semantic_context", "")


# ---------------------------------------------------------------------------
# Agent level: graph_hint reaches the VLM message text
# ---------------------------------------------------------------------------


def _bare_agent() -> PhoneAgent:
    agent = object.__new__(PhoneAgent)
    agent._task_plan = None
    agent._step_summaries = []
    agent.action_advisor = None
    agent.memory_manager = None
    agent._current_task = "搜索耳机"
    agent._last_user_reply = None
    agent._context = []
    return agent


def test_step_user_parts_include_graph_hint():
    agent = _bare_agent()

    hint = "[图谱路径参考] 当前页面: search_result，下一步目标页面: product_detail"
    parts = agent._build_step_user_parts(
        current_app="淘宝",
        page_type="search_result",
        available_actions=None,
        graph_hint=hint,
    )

    text = "\n\n".join(parts)
    assert "【图谱导航】" in text
    assert "图谱路径参考" in text


def test_step_user_parts_omit_empty_graph_hint():
    agent = _bare_agent()

    parts = agent._build_step_user_parts(
        current_app="淘宝",
        page_type="search_result",
        available_actions=None,
        graph_hint="",
    )

    assert "【图谱导航】" not in "\n\n".join(parts)


def test_supplementary_context_prepends_to_last_user_message():
    agent = _bare_agent()
    agent._context = [
        {
            "role": "user",
            "content": [{"type": "text", "text": "original task text"}],
        }
    ]

    agent._inject_supplementary_context(["[图谱路径参考] direction hint"])

    text = agent._context[-1]["content"][0]["text"]
    assert text.startswith("[图谱路径参考] direction hint")
    assert "original task text" in text


def test_supplementary_context_noop_when_empty():
    agent = _bare_agent()
    agent._context = [
        {
            "role": "user",
            "content": [{"type": "text", "text": "original task text"}],
        }
    ]

    agent._inject_supplementary_context([])

    assert agent._context[-1]["content"][0]["text"] == "original task text"
