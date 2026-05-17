import json

from phone_agent.memory.manual_trajectory_importer import ManualTrajectoryImporter
from phone_agent.memory.memory_manager import MemoryManager
from phone_agent.memory.offline_explorer import OfflineExplorer, PageInfo, ShoppingPageType, Trajectory
from phone_agent.memory.rebuild_spatial_graph import rebuild_spatial_graph
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
        task="搜索耳机商品",
    )

    assert state.state_id.startswith("state_")
    assert state.app == "淘宝"
    assert state.page_type == "search_result"
    assert "product_cards" in state.landmarks
    assert "open_product" in state.affordances
    assert state.semantic_signature


def test_page_state_from_exploration_page_extracts_elements():
    memory = SpatialGraphMemory()

    state = memory.page_state_from_exploration_page(
        {
            "app": "淘宝",
            "page_type": "search_result",
            "summary": "耳机搜索结果 商品列表",
            "elements": {
                "search_bar": "contains current query",
                "product_cards": "tap a product card to open detail",
                "filter_tabs": "filter and sort controls",
            },
            "screenshot_hash": "1234567890abcdef",
        }
    )

    assert state.page_type == "search_result"
    assert state.summary == "耳机搜索结果 商品列表"
    assert "search_bar" in state.landmarks
    assert "filter_tabs" in state.landmarks
    assert "tap_search" in state.affordances
    assert "open_product" in state.affordances
    assert "filter" in state.affordances


def test_local_route_planning_prefers_recorded_success_edge():
    memory = SpatialGraphMemory()
    home = memory.build_page_state(
        ui_hash="11111111aaaaaaaa",
        semantic_layout="淘宝 首页",
        task="搜索耳机",
    )
    search_result = memory.build_page_state(
        ui_hash="22222222bbbbbbbb",
        semantic_layout="淘宝 搜索结果",
        task="搜索耳机",
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
    route = memory.plan(belief, memory.infer_goal("搜索耳机", app="淘宝"))

    assert route.mode == "navigate"
    assert route.next_action is not None
    assert route.next_action["type"] == "Tap"
    assert route.steps[0].edge.target_id == search_result.state_id


def test_import_exploration_files_builds_route(tmp_path):
    pages_path = tmp_path / "taobao_explore_1.json"
    transitions_path = tmp_path / "taobao_explore_transitions_1.json"
    pages = {
        "app": "淘宝",
        "pages": [
            {
                "page_type": "home",
                "summary": "首页",
                "elements": {"search_bar": "tap to search"},
                "screenshot_hash": "homehash00000001",
                "app": "淘宝",
            },
            {
                "page_type": "search_result",
                "summary": "耳机搜索结果",
                "elements": {"product_cards": "tap product card"},
                "screenshot_hash": "resulthash000001",
                "app": "淘宝",
            },
            {
                "page_type": "product_detail",
                "summary": "耳机商品详情",
                "elements": {"buy_buttons": "add to cart button"},
                "screenshot_hash": "detailhash00001",
                "app": "淘宝",
            },
            {
                "page_type": "cart",
                "summary": "购物车",
                "elements": {"cart_items": "selected cart items"},
                "screenshot_hash": "carthash0000001",
                "app": "淘宝",
            },
        ],
    }
    transitions = {
        "app": "淘宝",
        "transitions": [
            {
                "from": "home:首页",
                "action": {"action": "Tap", "element": [400, 120]},
                "to": "search_result:耳机搜索结果",
            },
            {
                "from": "search_result:耳机搜索结果",
                "action": {"action": "Tap", "element": [300, 500]},
                "to": "product_detail:耳机商品详情",
            },
            {
                "from": "product_detail:耳机商品详情",
                "action": {"action": "Tap", "semantic_target": "add_to_cart"},
                "to": "cart:购物车",
            },
        ],
    }
    pages_path.write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")
    transitions_path.write_text(json.dumps(transitions, ensure_ascii=False), encoding="utf-8")

    memory = SpatialGraphMemory()
    result = memory.import_exploration_files(pages_path, persist=False)
    home = next(state for state in memory._local_states.values() if state.page_type == "home")
    belief = PageBelief(
        current_state_id=home.state_id,
        candidates=(PageBeliefCandidate(home, 1.0, "imported"),),
        confidence=1.0,
    )
    route = memory.plan(belief, memory.infer_goal("加入购物车", app="淘宝"))

    assert result.pages_imported == 4
    assert result.transitions_imported == 3
    assert route.mode == "navigate"
    assert route.steps[-1].edge.postcondition == "cart"
    assert route.next_action["type"] == "Tap"


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
        task="搜索耳机",
    )

    assert context["current_state_id"].startswith("state_")
    assert context["belief"]["current_state_id"] == context["current_state_id"]
    assert context["goal_spec"]["domain"] == "shopping"
    assert "SpatialGraph" in context["semantic_context"]


def test_memory_manager_marks_pending_transition_failure_on_postcondition_mismatch(tmp_path):
    manager = MemoryManager(storage_dir=str(tmp_path), user_id="tester")
    manager.graph_store = FakeGraphStore()
    manager.spatial_graph_memory = SpatialGraphMemory(manager.graph_store)

    source_id = manager.update_state_and_transition(
        screenshot_hash="sourcehash000001",
        semantic_layout="淘宝 商品详情",
        action={"action": "Tap", "semantic_target": "add_to_cart", "_expected_postcondition": "cart"},
        task="加入购物车",
    )
    context = manager.locate_and_get_context(
        ui_hash="wronghash0000001",
        semantic_layout="淘宝 搜索结果",
        task="加入购物车",
    )

    edge = manager.spatial_graph_memory._local_edges[source_id][0]
    assert edge.fail_count == 1
    assert edge.success_count == 0
    assert context["repair_hint"]["action"] == "replan"
    assert "SpatialGraph Repair" in context["semantic_context"]


def test_offline_explorer_save_results_auto_imports_spatial_graph(tmp_path):
    explorer = object.__new__(OfflineExplorer)
    explorer.app_name = "淘宝"
    explorer.task_description = "探索淘宝购物路径"
    explorer.storage_dir = tmp_path
    explorer.discovered_pages = {}
    explorer.transitions = []
    explorer.auto_import_graph = True
    explorer.graph_store = FakeGraphStore()
    explorer.last_import_result = None
    explorer.verbose = False

    home = PageInfo(
        page_type=ShoppingPageType.HOME,
        semantic_summary="首页",
        elements={"search_bar": "tap to search"},
        screenshot_hash="homehash00000001",
        app="淘宝",
    )
    cart = PageInfo(
        page_type=ShoppingPageType.CART,
        semantic_summary="购物车",
        elements={"cart_items": "cart items"},
        screenshot_hash="carthash0000001",
        app="淘宝",
    )
    explorer.discovered_pages = {home.state_key(): home, cart.state_key(): cart}
    explorer.transitions = [
        {"from": home.state_key(), "action": {"action": "Tap", "semantic_target": "cart"}, "to": cart.state_key()}
    ]
    trajectory = Trajectory(task="探索淘宝购物路径", app="淘宝")
    trajectory.add_step(home, {"action": "Tap"}, "tap cart")
    trajectory.add_step(cart, {"_metadata": "finish"}, "done")

    explorer._save_results(trajectory)

    assert explorer.last_import_result is not None
    assert explorer.last_import_result.pages_imported == 2
    assert explorer.last_import_result.transitions_imported == 1


def test_manual_trajectory_importer_prefers_react_json(tmp_path):
    run_dir = tmp_path / "manual" / "淘宝" / "基础加购商品" / "1"
    run_dir.mkdir(parents=True)
    (run_dir / "actions.json").write_text(
        json.dumps(
            {
                "app_name": "淘宝",
                "task_type": "基础加购商品",
                "task_description": ["打开淘宝，把耳机加入购物车"],
                "actions": [{"function": {"name": "click", "parameters": {"target_element": "WRONG"}}}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (run_dir / "react.json").write_text(
        json.dumps(
            [
                {
                    "reasoning": "当前位于首页，需要点击搜索栏。",
                    "function": {"name": "click", "parameters": {"target_element": "顶部搜索栏"}},
                    "action_index": 1,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (run_dir / "1.txt").write_text("Taobao home page with top search bar and bottom tabs.", encoding="utf-8")
    (run_dir / "2.txt").write_text("Taobao search_input page with active search box and keyboard.", encoding="utf-8")
    (run_dir / "1.jpg").write_bytes(b"fake-home")
    (run_dir / "2.jpg").write_bytes(b"fake-search")

    importer = ManualTrajectoryImporter()
    result = importer.import_directory(tmp_path / "manual", persist=False)
    source = next(state for state in importer.memory._local_states.values() if state.page_type == "home")
    edge = importer.memory._local_edges[source.state_id][0]

    assert result.trajectories_imported == 1
    assert result.pages_imported == 2
    assert result.transitions_imported == 1
    assert edge.action_type == "Tap"
    assert edge.action_target == "顶部搜索栏"
    assert edge.confidence == 0.75


def test_rebuild_spatial_graph_dry_run_combines_manual_and_exploration(tmp_path):
    manual_run = tmp_path / "manual" / "淘宝" / "基础加购商品" / "1"
    manual_run.mkdir(parents=True)
    (manual_run / "actions.json").write_text(
        json.dumps({"app_name": "淘宝", "task_type": "基础加购商品", "task_description": "搜索耳机"}),
        encoding="utf-8",
    )
    (manual_run / "react.json").write_text(
        json.dumps([{"function": {"name": "click", "parameters": {"target_element": "顶部搜索栏"}}}]),
        encoding="utf-8",
    )
    (manual_run / "1.txt").write_text("Taobao home page with search bar.", encoding="utf-8")
    (manual_run / "2.txt").write_text("Taobao search_input page with active search box.", encoding="utf-8")

    exploration = tmp_path / "exploration"
    exploration.mkdir()
    (exploration / "taobao_explore_1.json").write_text(
        json.dumps(
            {
                "app": "淘宝",
                "pages": [
                    {
                        "page_type": "home",
                        "summary": "首页",
                        "elements": {"search_bar": "tap to search"},
                        "screenshot_hash": "homehash",
                        "app": "淘宝",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (exploration / "taobao_explore_transitions_1.json").write_text(
        json.dumps({"app": "淘宝", "transitions": []}, ensure_ascii=False),
        encoding="utf-8",
    )

    report = rebuild_spatial_graph(
        manual_root=tmp_path / "manual",
        exploration_root=exploration,
        write=False,
    )

    assert report["mode"] == "dry-run"
    assert report["manual"]["trajectories_imported"] == 1
    assert report["manual"]["transitions_imported"] == 1
    assert report["exploration"]["pages_imported"] == 1
    assert report["totals"]["pages"] == 3
