from phone_agent.memory.memory_manager import MemoryManager
from phone_agent.memory.spatial_graph_memory import (
    PageBelief,
    PageBeliefCandidate,
    SpatialGraphMemory,
    TransitionEdge,
)


class FakeGraphStore:
    driver = None

    def find_similar_tasks(self, task, app=None, top_k=3):
        return []

    def get_task_trajectory(self, task_id, max_steps=20):
        return {}


def test_page_state_uses_page_level_signature():
    memory = SpatialGraphMemory()

    state = memory.build_page_state(
        ui_hash="abcdef1234567890",
        semantic_layout="淘宝 搜索结果",
        task="搜索手机并打开商品",
    )

    assert state.state_id.startswith("state_")
    assert state.app == "淘宝 搜索结果"
    assert state.page_type == "search_result"
    assert "product_cards" in state.landmarks
    assert "open_product" in state.affordances
    assert state.semantic_signature


def test_local_route_planning_prefers_recorded_success_edge():
    memory = SpatialGraphMemory()
    home = memory.build_page_state(
        ui_hash="11111111aaaaaaaa",
        semantic_layout="淘宝 首页",
        task="搜索手机",
    )
    search_result = memory.build_page_state(
        ui_hash="22222222bbbbbbbb",
        semantic_layout="淘宝 搜索结果",
        task="搜索手机",
    )

    memory.record_observation(
        home,
        {"_metadata": "do", "action": "Tap", "element": [400, 120]},
        search_result,
        outcome="success",
    )

    belief = PageBelief(
        current_state_id=home.state_id,
        candidates=(PageBeliefCandidate(home, 1.0, "test"),),
        confidence=1.0,
    )
    route = memory.plan(belief, memory.infer_goal("搜索手机", app="淘宝"))

    assert route.mode == "navigate"
    assert route.next_action is not None
    assert route.next_action["type"] == "Tap"
    assert route.steps[0].edge.target_id == search_result.state_id


def test_failed_edge_has_higher_weighted_cost():
    success = TransitionEdge(
        source_id="state_a",
        target_id="state_b",
        action_type="Tap",
        success_count=3,
        fail_count=0,
        risk="normal",
        confidence=1.0,
    )
    failure = TransitionEdge(
        source_id="state_a",
        target_id="state_b",
        action_type="Tap",
        success_count=1,
        fail_count=3,
        risk="high",
        confidence=0.3,
    )

    assert failure.weighted_cost > success.weighted_cost


def test_repair_asks_user_on_high_risk_page():
    memory = SpatialGraphMemory()
    payment = memory.build_page_state(
        ui_hash="33333333cccccccc",
        semantic_layout="淘宝 支付",
        task="加入购物车",
    )
    observed = PageBelief(
        current_state_id=payment.state_id,
        candidates=(PageBeliefCandidate(payment, 1.0, "test"),),
        confidence=1.0,
    )

    decision = memory.repair(observed, expected="cart")

    assert decision.action == "ask_user"
    assert decision.rollback_action == "Back"


def test_memory_manager_returns_spatial_context(tmp_path):
    manager = MemoryManager(storage_dir=str(tmp_path), user_id="tester")
    manager.graph_store = FakeGraphStore()
    manager.spatial_graph_memory = SpatialGraphMemory(manager.graph_store)

    context = manager.locate_and_get_context(
        ui_hash="44444444dddddddd",
        semantic_layout="淘宝 首页",
        task="搜索手机",
    )

    assert context["current_state_id"].startswith("state_")
    assert context["belief"]["current_state_id"] == context["current_state_id"]
    assert context["goal_spec"]["domain"] == "shopping"
    assert "SpatialGraph" in context["semantic_context"]
