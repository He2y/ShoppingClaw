from types import SimpleNamespace

from phone_agent.agent import PhoneAgent
from phone_agent.config.shopping_config import ShoppingConfig
from phone_agent.core.spec_guard import SpecGuard
from phone_agent.memory.memory_manager import MemoryManager
from phone_agent.memory.spatial_graph_memory import RuntimeDAG, SpatialGraphMemory, TransitionEdge


class FakeGraphStore:
    driver = None

    def find_similar_tasks(self, task, app=None, top_k=3):
        return []

    def get_task_trajectory(self, task_id, max_steps=20):
        return {}


def _agent_for_spec_guard(task: str) -> PhoneAgent:
    agent = object.__new__(PhoneAgent)
    agent._current_task = task
    agent._vlm_plan = {}
    agent._shopping_config = ShoppingConfig.load()
    agent._spec_guard = SpecGuard(agent._shopping_config)
    agent.memory_manager = SimpleNamespace(_vlm_plan={})
    return agent


def _spec_guard_check(agent: PhoneAgent, action, thinking, current_app, page_type):
    """Mirror PhoneAgent's runtime SpecGuard invocation (agent.py)."""
    return agent._spec_guard.check(
        action=action,
        thinking=thinking,
        current_app=current_app,
        page_type=page_type,
        task=agent._current_task,
        vlm_plan=agent._vlm_plan,
    )


def _shopping_app(agent: PhoneAgent) -> str:
    return next(iter(agent._shopping_config.apps))


def test_spec_guard_does_not_ask_user_when_task_has_explicit_specs():
    agent = _agent_for_spec_guard("去淘宝买 iPhone 17 pro max，银色 512G，加入购物车")

    action = {
        "_metadata": "do",
        "action": "Tap",
        "semantic_target": "confirm_spec_add_to_cart",
    }
    thinking = "我需要在规格弹窗点击确认加入购物车。"

    guarded = _spec_guard_check(agent, action, thinking, _shopping_app(agent), page_type="spec_selection")

    assert guarded is None


def test_spec_guard_asks_only_when_committing_without_explicit_specs():
    agent = _agent_for_spec_guard("去淘宝买一个手机壳，加入购物车")

    action = {
        "_metadata": "do",
        "action": "Tap",
        "semantic_target": "confirm_spec_add_to_cart",
    }
    thinking = "这里需要选择规格，然后确认加入购物车。"

    guarded = _spec_guard_check(agent, action, thinking, _shopping_app(agent), page_type="spec_selection")

    assert guarded is not None
    assert guarded["action"] == "Interact"


def test_spec_guard_uses_vlm_plan_specs_as_explicit_requirements():
    agent = _agent_for_spec_guard("去淘宝买 iPhone，加入购物车")
    agent._vlm_plan = {"specs": {"color": "银色", "storage": "512G"}}

    action = {
        "_metadata": "do",
        "action": "Tap",
        "semantic_target": "confirm_spec_add_to_cart",
    }
    thinking = "我需要在规格弹窗点击确认加入购物车。"

    guarded = _spec_guard_check(agent, action, thinking, _shopping_app(agent), page_type="spec_selection")

    assert guarded is None


def test_runtime_hint_must_not_advance_pending_transition_without_observation(tmp_path):
    manager = MemoryManager(storage_dir=str(tmp_path), user_id="tester")
    manager.graph_store = FakeGraphStore()
    manager.spatial_graph_memory = SpatialGraphMemory(manager.graph_store)

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
    manager._pending_transition_source = home
    manager._pending_transition_action = {
        "_metadata": "do",
        "action": "Tap",
        "_runtime_plan_id": "plan1",
        "_expected_postcondition": "search_input",
    }
    manager._pending_expected_postcondition = "search_input"

    context = manager.locate_and_get_context(
        ui_hash="observed_home",
        semantic_layout="淘宝 home",
        task="搜索耳机",
        screen_dict={
            "ui_hash": "observed_home",
            "semantic_layout": "淘宝 home",
            "app": "淘宝",
            "page_type": "home",
            "_runtime_hint": False,
        },
    )

    assert manager._runtime_dag.current_index == 0
    assert context["repair_hint"]["action"] != "retry"
