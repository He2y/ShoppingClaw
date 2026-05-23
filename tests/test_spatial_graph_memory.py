import json

from phone_agent.memory.manual_trajectory_importer import ManualTrajectoryImporter
from phone_agent.memory.graph_store import GraphStore
from phone_agent.memory.memory_manager import MemoryManager
from phone_agent.memory.offline_explorer import CoverageTarget, OfflineExplorer, PageInfo, ShoppingPageType, Trajectory
from phone_agent.memory.rebuild_spatial_graph import rebuild_spatial_graph
from phone_agent.memory.spatial_graph_memory import (
    PageBelief,
    PageBeliefCandidate,
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


class FakeSpatialGraphStore:
    driver = object()

    def __init__(self, state):
        self.state = state

    def get_state_by_semantic(self, semantic_layout, limit=1):
        return None

    def find_page_state_candidates(self, app="", page_type="", limit=10):
        return [self.state] if self.state["app"] == app and self.state["page_type"] == page_type else []

    def get_outgoing_transitions(self, state_id, limit=20):
        return []


class FakeMergeGraphStore:
    driver = object()

    def __init__(self, candidates):
        self.candidates = candidates
        self.upserted = []
        self.transitions = []

    def find_page_state_candidates(self, app="", page_type="", limit=10):
        return [
            state
            for state in self.candidates
            if state["app"] == app and state["page_type"] == page_type
        ][:limit]

    def upsert_page_state(self, state_metadata):
        self.upserted.append(state_metadata)

    def add_state_transition(
        self,
        source_state_hash,
        target_state_hash,
        action_data,
        task_id=None,
        outcome="success",
        source_metadata=None,
        target_metadata=None,
    ):
        self.transitions.append(
            {
                "source": source_state_hash,
                "target": target_state_hash,
                "action": action_data,
                "source_metadata": source_metadata,
                "target_metadata": target_metadata,
            }
        )


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


def test_unknown_page_type_is_not_inferred_from_task_goal():
    state = SpatialGraphMemory().build_page_state(
        ui_hash="splash",
        semantic_layout="Taobao launch splash",
        task="不要结算，不要支付。",
        app="Taobao",
    )

    assert state.page_type == "unknown"


def test_semantic_page_state_id_ignores_screenshot_hash():
    memory = SpatialGraphMemory()

    first = memory.build_page_state(
        ui_hash="hash_one",
        semantic_layout="Taobao search_result product cards",
        task="search shampoo",
        app="Taobao",
        page_type="search_result",
        elements={"product_cards": "tap product card"},
        state_id_strategy="semantic",
    )
    second = memory.build_page_state(
        ui_hash="hash_two",
        semantic_layout="Taobao search_result product cards",
        task="search shampoo",
        app="Taobao",
        page_type="search_result",
        elements={"product_cards": "tap product card"},
        state_id_strategy="semantic",
    )

    assert first.state_id == second.state_id
    assert first.screenshot_hash != second.screenshot_hash


def test_graph_store_decodes_persisted_action_params_for_replay():
    params = GraphStore._decode_action_params("{'_metadata': 'do', 'action': 'Tap', 'element': [429, 114]}")

    assert params["action"] == "Tap"
    assert params["element"] == [429, 114]


def test_locate_uses_graph_state_as_current_when_localized():
    graph_state = {
        "state_id": "state_taobao_home_graph",
        "app": "Taobao",
        "page_type": "home",
        "summary": "Taobao home page",
        "landmarks": ["search_bar", "bottom_tabs"],
        "affordances": ["tap_search", "open_tab"],
        "slots": "{}",
        "risk_level": "normal",
        "semantic_signature": "Taobao|home|search_bar,bottom_tabs|tap_search,open_tab|",
    }
    memory = SpatialGraphMemory(FakeSpatialGraphStore(graph_state))

    belief = memory.locate(
        {
            "ui_hash": "new_runtime_hash",
            "app": "Taobao",
            "page_type": "home",
            "semantic_layout": "Taobao home page",
            "summary": "Taobao home page",
            "elements": {"search_bar": "tap to search", "bottom_tabs": "home cart profile"},
        },
        task="add product to cart",
    )

    assert belief.current_state_id == "state_taobao_home_graph"
    assert belief.is_novel is False
    assert belief.candidates[0].reason == "semantic graph localization"


def test_chinese_add_to_cart_task_targets_cart():
    goal = SpatialGraphMemory().infer_goal("在淘宝搜索一个商品并加入购物车", app="淘宝")

    assert goal.domain == "shopping"
    assert goal.target_page_types == ("cart",)


def test_negative_cart_clause_does_not_override_search_result_goal():
    goal = SpatialGraphMemory().infer_goal(
        "打开淘宝，搜索MacBook笔记本并停在搜索结果页。不要点击商品，不要加入购物车，不要结算，不要支付。",
        app="淘宝",
    )

    assert goal.target_page_types == ("search_result",)


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


def test_spec_selection_payment_text_stays_medium_risk():
    state = SpatialGraphMemory().build_page_state(
        ui_hash="sku",
        semantic_layout="Taobao spec_selection",
        app="Taobao",
        page_type="spec_selection",
        summary="Choose product spec",
        elements={"payment_options": "installment payment options", "confirm_button": "confirm spec"},
        state_id_strategy="semantic",
    )

    assert state.risk_level == "medium"


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


def test_route_planning_stops_when_current_page_satisfies_goal():
    memory = SpatialGraphMemory()
    search_result = memory.build_page_state(
        ui_hash="taobao-result",
        semantic_layout="Taobao search_result",
        app="Taobao",
        page_type="search_result",
    )
    memory._local_states[search_result.state_id] = search_result
    belief = PageBelief(
        current_state_id=search_result.state_id,
        candidates=(PageBeliefCandidate(search_result, 1.0, "test"),),
        confidence=1.0,
    )

    route = memory.plan(belief, memory.infer_goal("search MacBook", app="Taobao"))

    assert route.mode == "goal_reached"
    assert route.steps == ()


def test_route_planning_does_not_cross_app_boundary():
    memory = SpatialGraphMemory()
    taobao_home = memory.build_page_state(
        ui_hash="taobao-home",
        semantic_layout="Taobao home",
        app="Taobao",
        page_type="home",
    )
    jd_cart = memory.build_page_state(
        ui_hash="jd-cart",
        semantic_layout="JD cart",
        app="JD",
        page_type="cart",
    )

    memory.record_observation(
        taobao_home,
        {"_metadata": "do", "action": "Tap", "semantic_target": "cart"},
        jd_cart,
        outcome="success",
    )

    belief = PageBelief(
        current_state_id=taobao_home.state_id,
        candidates=(PageBeliefCandidate(taobao_home, 1.0, "test"),),
        confidence=1.0,
    )
    route = memory.plan(belief, memory.infer_goal("add to cart", app="Taobao"))

    assert route.mode == "explore"
    assert route.risk_summary == "no graph route from current page"


def test_route_planning_rejects_implausible_search_result_to_cart_edge():
    memory = SpatialGraphMemory()
    search_result = memory.build_page_state(
        ui_hash="taobao-result",
        semantic_layout="Taobao search_result",
        app="Taobao",
        page_type="search_result",
    )
    cart = memory.build_page_state(
        ui_hash="taobao-cart",
        semantic_layout="Taobao cart",
        app="Taobao",
        page_type="cart",
    )

    memory.record_observation(
        search_result,
        {
            "_metadata": "do",
            "action": "Tap",
            "semantic_target": "top product card",
            "source_path": "MobiAgent/collect/manual/data/淘宝/基础加购商品/2",
        },
        cart,
        outcome="success",
    )

    belief = PageBelief(
        current_state_id=search_result.state_id,
        candidates=(PageBeliefCandidate(search_result, 1.0, "test"),),
        confidence=1.0,
    )
    route = memory.plan(belief, memory.infer_goal("add to cart", app="Taobao"))

    assert route.mode == "explore"


def test_route_planning_rejects_filter_as_spec_selection_edge():
    memory = SpatialGraphMemory()
    search_result = memory.build_page_state(
        ui_hash="taobao-result",
        semantic_layout="Taobao search_result",
        app="Taobao",
        page_type="search_result",
    )
    spec_selection = memory.build_page_state(
        ui_hash="taobao-spec",
        semantic_layout="Taobao spec selection",
        app="Taobao",
        page_type="spec_selection",
    )

    memory.record_observation(
        search_result,
        {"_metadata": "do", "action": "Tap", "semantic_target": "filter button"},
        spec_selection,
        outcome="success",
    )

    belief = PageBelief(
        current_state_id=search_result.state_id,
        candidates=(PageBeliefCandidate(search_result, 1.0, "test"),),
        confidence=1.0,
    )
    route = memory.plan(belief, memory.infer_goal("add to cart", app="Taobao"))

    assert route.mode == "explore"


def test_promotable_edge_rejects_top_bar_tap_as_spec_selection():
    memory = SpatialGraphMemory()
    search_result = memory.build_page_state(
        ui_hash="taobao-result",
        semantic_layout="Taobao search_result",
        app="Taobao",
        page_type="search_result",
    )
    spec_selection = memory.build_page_state(
        ui_hash="taobao-spec",
        semantic_layout="Taobao spec selection",
        app="Taobao",
        page_type="spec_selection",
    )

    top_bar_edge = TransitionEdge.from_action(
        search_result.state_id,
        spec_selection.state_id,
        {"action": "Tap", "element": [429, 114]},
        risk=spec_selection.risk_level,
        postcondition=spec_selection.page_type,
    )
    product_card_edge = TransitionEdge.from_action(
        search_result.state_id,
        spec_selection.state_id,
        {"action": "Tap", "element": [499, 383]},
        risk=spec_selection.risk_level,
        postcondition=spec_selection.page_type,
    )

    assert memory._is_promotable_edge(top_bar_edge, search_result, spec_selection) is False
    assert memory._is_promotable_edge(product_card_edge, search_result, spec_selection) is True


def test_route_planning_does_not_replay_historic_type_text():
    memory = SpatialGraphMemory()
    search_input = memory.build_page_state(
        ui_hash="taobao-search-input",
        semantic_layout="Taobao search_input",
        app="Taobao",
        page_type="search_input",
    )
    search_result = memory.build_page_state(
        ui_hash="taobao-search-result",
        semantic_layout="Taobao search_result",
        app="Taobao",
        page_type="search_result",
    )

    memory.record_observation(
        search_input,
        {"_metadata": "do", "action": "Type", "text": "historic query"},
        search_result,
        outcome="success",
    )

    belief = PageBelief(
        current_state_id=search_input.state_id,
        candidates=(PageBeliefCandidate(search_input, 1.0, "test"),),
        confidence=1.0,
    )
    route = memory.plan(belief, memory.infer_goal("search MacBook", app="Taobao"))

    assert route.mode == "explore"


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


def test_staging_import_canonicalizes_query_variants_and_filters_unknown(tmp_path):
    pages_path = tmp_path / "taobao_explore_1.json"
    transitions_path = tmp_path / "taobao_explore_transitions_1.json"
    pages_path.write_text(
        json.dumps(
            {
                "app": "淘宝",
                "pages": [
                    {
                        "page_type": "search_result",
                        "summary": "耳机搜索结果",
                        "elements": {"product_cards": "tap product card", "search_bar": "query=耳机"},
                        "screenshot_hash": "hash1",
                        "app": "淘宝",
                    },
                    {
                        "page_type": "search_result",
                        "summary": "MacBook搜索结果",
                        "elements": {"product_cards": "tap product card", "search_bar": "query=MacBook"},
                        "screenshot_hash": "hash2",
                        "app": "淘宝",
                    },
                    {
                        "page_type": "unknown",
                        "summary": "618活动页",
                        "elements": {},
                        "screenshot_hash": "hash3",
                        "app": "淘宝",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    transitions_path.write_text(
        json.dumps(
            {
                "app": "淘宝",
                "transitions": [
                    {
                        "from": "search_result:耳机搜索结果",
                        "action": {"action": "Tap", "semantic_target": "product card"},
                        "to": "search_result:MacBook搜索结果",
                    },
                    {
                        "from": "search_result:MacBook搜索结果",
                        "action": {"action": "Tap", "semantic_target": "popup"},
                        "to": "unknown:618活动页",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    states, edges, report = SpatialGraphMemory().import_exploration_staging(pages_path, transitions_path)

    assert report.pages_seen == 3
    assert report.canonical_pages == 1
    assert report.transient_pages == 1
    assert report.transitions_promoted == 0
    assert report.transitions_filtered == 2
    assert len(states) == 1
    assert next(iter(states.values())).page_type == "search_result"


def test_canonical_pages_merge_by_page_type_and_preserve_semantics():
    memory = SpatialGraphMemory()

    states = memory.canonicalize_pages(
        [
            {
                "page_type": "home",
                "summary": "Taobao home with search and promos",
                "elements": {"search_bar": "tap to search", "promo_banner": "sales entry"},
                "screenshot_hash": "home-a",
                "app": "Taobao",
            },
            {
                "page_type": "home",
                "summary": "Taobao home with product feed",
                "elements": {"product_grid": "recommended products", "bottom_tabs": "home cart profile"},
                "screenshot_hash": "home-b",
                "app": "Taobao",
            },
        ],
        fallback_app="Taobao",
    )

    assert len(states) == 1
    state = next(iter(states.values()))
    assert state.page_type == "home"
    assert "search_bar" in state.landmarks
    assert "product_grid" in state.landmarks
    assert "tap_search" in state.affordances


def test_staging_compacts_search_input_type_and_submit(tmp_path):
    pages_path = tmp_path / "taobao_explore_1.json"
    transitions_path = tmp_path / "taobao_explore_transitions_1.json"
    pages_path.write_text(
        json.dumps(
            {
                "app": "Taobao",
                "pages": [
                    {
                        "page_type": "search_input",
                        "summary": "Search input",
                        "elements": {"search_bar": "active input"},
                        "screenshot_hash": "input",
                        "app": "Taobao",
                    },
                    {
                        "page_type": "search_result",
                        "summary": "Search results",
                        "elements": {"product_cards": "tap product card"},
                        "screenshot_hash": "result",
                        "app": "Taobao",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    transitions_path.write_text(
        json.dumps(
            {
                "app": "Taobao",
                "transitions": [
                    {
                        "from": "search_input:Search input",
                        "action": {"action": "Type", "text": "headphones"},
                        "to": "search_input:Search input",
                    },
                    {
                        "from": "search_input:Search input",
                        "action": {"action": "Tap", "element": [893, 71]},
                        "to": "search_result:Search results",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    states, edges, report = SpatialGraphMemory().import_exploration_staging(pages_path, transitions_path)

    assert len(states) == 2
    assert report.transitions_seen == 2
    assert report.transitions_promoted == 1
    assert report.transitions_compacted == 1
    assert report.compound_edges == 1
    edge = edges[0]
    assert edge.action_type == "Compound"
    assert edge.confidence == 0.65
    assert edge.action_params["requires_runtime_input"] is True
    assert edge.action_params["runtime_slots"] == ["query"]
    assert edge.action_params["actions"][0]["text"] == "<query>"
    assert edge.postcondition == "search_result"


def test_staging_compacts_filter_panel_multi_step_flow(tmp_path):
    pages_path = tmp_path / "taobao_explore_1.json"
    transitions_path = tmp_path / "taobao_explore_transitions_1.json"
    pages_path.write_text(
        json.dumps(
            {
                "app": "Taobao",
                "pages": [
                    {
                        "page_type": "filter_panel",
                        "summary": "Filter panel",
                        "elements": {"price_range": "custom price inputs", "confirm_button": "apply filters"},
                        "screenshot_hash": "filter",
                        "app": "Taobao",
                    },
                    {
                        "page_type": "search_result",
                        "summary": "Filtered results",
                        "elements": {"product_cards": "tap product card"},
                        "screenshot_hash": "result",
                        "app": "Taobao",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    transitions_path.write_text(
        json.dumps(
            {
                "app": "Taobao",
                "transitions": [
                    {
                        "from": "filter_panel:Filter panel",
                        "action": {"action": "Tap", "element": [408, 485]},
                        "to": "filter_panel:Filter panel",
                    },
                    {
                        "from": "filter_panel:Filter panel",
                        "action": {"action": "Type", "text": "500"},
                        "to": "filter_panel:Filter panel",
                    },
                    {
                        "from": "filter_panel:Filter panel",
                        "action": {"action": "Tap", "element": [795, 485]},
                        "to": "filter_panel:Filter panel",
                    },
                    {
                        "from": "filter_panel:Filter panel",
                        "action": {"action": "Type", "text": "1000"},
                        "to": "filter_panel:Filter panel",
                    },
                    {
                        "from": "filter_panel:Filter panel",
                        "action": {"action": "Tap", "element": [739, 922]},
                        "to": "search_result:Filtered results",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    _, edges, report = SpatialGraphMemory().import_exploration_staging(pages_path, transitions_path)

    assert report.transitions_seen == 5
    assert report.transitions_promoted == 1
    assert report.transitions_compacted == 4
    assert report.compound_edges == 1
    assert edges[0].action_params["runtime_slots"] == ["min_price", "max_price"]
    assert edges[0].action_params["actions"][1]["text"] == "<min_price>"
    assert edges[0].action_params["actions"][3]["text"] == "<max_price>"


def test_promote_reuses_existing_graph_page_type_nodes():
    existing_home = {
        "state_id": "state_existing_home",
        "app": "Taobao",
        "page_type": "home",
        "summary": "Existing home",
        "landmarks": ["search_bar"],
        "affordances": ["tap_search"],
        "slots": "{}",
        "risk_level": "normal",
        "semantic_signature": "Taobao|home|search_bar|tap_search|",
    }
    store = FakeMergeGraphStore([existing_home])
    memory = SpatialGraphMemory(store)
    home = memory.build_page_state(
        ui_hash="new-home",
        semantic_layout="Taobao home",
        app="Taobao",
        page_type="home",
        elements={"bottom_tabs": "home cart profile"},
        state_id_strategy="semantic",
    )
    search_input = memory.build_page_state(
        ui_hash="search-input",
        semantic_layout="Taobao search_input",
        app="Taobao",
        page_type="search_input",
        state_id_strategy="semantic",
    )
    edge = TransitionEdge.from_action(
        home.state_id,
        search_input.state_id,
        {"action": "Tap", "element": [429, 114]},
        postcondition="search_input",
    )

    report = memory.promote_staging_to_canonical(
        {home.state_id: home, search_input.state_id: search_input},
        [edge],
        persist=True,
    )

    assert report.transitions_promoted == 1
    assert "state_existing_home" in memory._local_states
    assert memory._local_states["state_existing_home"].landmarks == ("search_bar", "bottom_tabs")
    assert store.transitions[0]["source"] == "state_existing_home"
    assert store.transitions[0]["source_metadata"]["state_id"] == "state_existing_home"


def test_canonicalize_filters_app_mismatch_pages():
    memory = SpatialGraphMemory()
    states, edges, report = memory.canonicalize_state_graph(
        [
            memory.build_page_state(
                ui_hash="taobao-home",
                semantic_layout="淘宝 首页",
                app="淘宝",
                page_type="home",
                summary="淘宝APP首页",
            ),
            memory.build_page_state(
                ui_hash="taobao-polluted",
                semantic_layout="淘宝 搜索结果",
                app="淘宝",
                page_type="search_result",
                summary="京东APP搜索结果页",
            ),
        ],
        {},
    )

    assert len(states) == 1
    assert edges == []
    assert report.app_mismatch_pages == 1


def test_runtime_dag_exposes_next_action_and_blocks_high_risk():
    memory = SpatialGraphMemory()
    home = memory.build_page_state(
        ui_hash="home",
        semantic_layout="淘宝 home",
        app="淘宝",
        page_type="home",
    )
    search = memory.build_page_state(
        ui_hash="search",
        semantic_layout="淘宝 search_input",
        app="淘宝",
        page_type="search_input",
    )
    dag = RuntimeDAG(
        plan_id="plan1",
        app="淘宝",
        goal_spec=memory.infer_goal("搜索耳机", app="淘宝"),
        nodes={home.state_id: home, search.state_id: search},
        route=[
            TransitionEdge(
                source_id=home.state_id,
                target_id=search.state_id,
                action_type="Tap",
                action_target="search bar",
                postcondition="search_input",
                confidence=0.9,
            )
        ],
    )

    action = memory.next_planned_action(dag)

    assert action["type"] == "Tap"
    assert action["postcondition"] == "search_input"

    payment = TransitionEdge(
        source_id=search.state_id,
        target_id="payment",
        action_type="Tap",
        action_target="pay",
        postcondition="payment",
        risk="high",
    )
    dag.route = [payment]
    dag.current_index = 0

    assert memory.next_planned_action(dag) is None
    assert dag.coverage_gaps


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


def test_memory_manager_runtime_dag_fast_path_skips_relocalization(tmp_path):
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
    manager._runtime_dag = RuntimeDAG(
        plan_id="plan1",
        app="淘宝",
        goal_spec=manager.spatial_graph_memory.infer_goal("搜索耳机", app="淘宝"),
        nodes={home.state_id: home, search_input.state_id: search_input},
        route=[edge],
    )

    assert manager.should_use_page_classifier(step=2, current_app="淘宝") is False

    context = manager.locate_and_get_context(
        ui_hash="runtimehash",
        semantic_layout="淘宝 home",
        task="搜索耳机",
        screen_dict={"ui_hash": "runtimehash", "app": "淘宝", "_runtime_hint": True},
    )

    assert context["mode"] == "navigate"
    assert context["next_actions"][0]["_runtime_plan_id"] == "plan1"
    assert context["route_plan"]["mode"] == "runtime_dag"
    manager.record_page_classifier_decision(False)
    metrics = manager.get_runtime_metrics()
    assert metrics["runtime_dag_hits"] >= 1
    assert metrics["page_classifier_skips"] == 1


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
    assert explorer.last_import_result["pages_imported"] == 2
    assert explorer.last_import_result["transitions_imported"] == 1


def test_offline_explorer_reports_coverage_gaps():
    explorer = object.__new__(OfflineExplorer)
    explorer.coverage_targets = CoverageTarget(
        page_types=("home", "search_input", "search_result"),
        transitions=(("home", "search_input"), ("search_input", "search_result")),
    )
    home = PageInfo(
        page_type=ShoppingPageType.HOME,
        semantic_summary="首页",
        elements={"search_bar": "tap to search"},
        screenshot_hash="homehash",
        app="淘宝",
    )
    search_input = PageInfo(
        page_type=ShoppingPageType.SEARCH_INPUT,
        semantic_summary="搜索输入",
        elements={"search_bar": "active"},
        screenshot_hash="inputhash",
        app="淘宝",
    )
    explorer.discovered_pages = {home.state_key(): home, search_input.state_key(): search_input}
    explorer.transitions = [
        {"from": home.state_key(), "action": {"action": "Tap"}, "to": search_input.state_key()}
    ]

    report = explorer._update_coverage_report()

    assert report.covered_page_types == ("home", "search_input")
    assert report.missing_page_types == ("search_result",)
    assert report.covered_transitions == (("home", "search_input"),)
    assert report.missing_transitions == (("search_input", "search_result"),)


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
    assert result.unique_pages == 2
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
    assert report["totals"]["unique_pages"] <= report["totals"]["pages"]
    assert "dedupe_ratio" in report["totals"]


def test_rebuild_spatial_graph_canonical_mode_reports_quality(tmp_path):
    manual_run = tmp_path / "manual" / "淘宝" / "基础搜索商品" / "1"
    manual_run.mkdir(parents=True)
    (manual_run / "actions.json").write_text(
        json.dumps({"app_name": "淘宝", "task_type": "基础搜索商品", "task_description": "搜索耳机"}),
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
                        "page_type": "search_result",
                        "summary": "耳机搜索结果",
                        "elements": {"product_cards": "tap product card"},
                        "screenshot_hash": "r1",
                        "app": "淘宝",
                    },
                    {
                        "page_type": "search_result",
                        "summary": "MacBook搜索结果",
                        "elements": {"product_cards": "tap product card"},
                        "screenshot_hash": "r2",
                        "app": "淘宝",
                    },
                    {
                        "page_type": "unknown",
                        "summary": "活动页",
                        "elements": {},
                        "screenshot_hash": "r3",
                        "app": "淘宝",
                    },
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
        canonical=True,
    )

    assert report["canonical"] is True
    assert report["quality_gate"]["app"] == "淘宝"
    assert "missing_safe_core_edges" in report["quality_gate"]
    assert report["manual_quality"]["canonical_pages"] <= report["manual"]["pages_imported"]
    assert report["exploration"]["files"][0]["quality"]["transient_pages"] == 1
    assert report["totals"]["unique_pages"] < report["totals"]["pages"]


def test_rebuild_spatial_graph_exploration_only_filters_app(tmp_path):
    manual_run = tmp_path / "manual" / "Taobao" / "manual_task" / "1"
    manual_run.mkdir(parents=True)
    (manual_run / "actions.json").write_text(
        json.dumps({"app_name": "Taobao", "task_type": "manual_task", "task_description": "manual"}),
        encoding="utf-8",
    )
    (manual_run / "1.txt").write_text("Taobao home page with search bar.", encoding="utf-8")

    exploration = tmp_path / "exploration"
    exploration.mkdir()
    (exploration / "taobao_explore_1.json").write_text(
        json.dumps(
            {
                "app": "Taobao",
                "pages": [
                    {
                        "page_type": "home",
                        "summary": "Home",
                        "elements": {"search_bar": "tap to search"},
                        "screenshot_hash": "homehash",
                        "app": "Taobao",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (exploration / "taobao_explore_transitions_1.json").write_text(
        json.dumps({"app": "Taobao", "transitions": []}),
        encoding="utf-8",
    )
    (exploration / "jd_explore_1.json").write_text(
        json.dumps(
            {
                "app": "JD",
                "pages": [
                    {
                        "page_type": "home",
                        "summary": "JD home",
                        "elements": {"search_bar": "tap to search"},
                        "screenshot_hash": "jdhome",
                        "app": "JD",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = rebuild_spatial_graph(
        manual_root=tmp_path / "manual",
        exploration_root=exploration,
        write=False,
        canonical=True,
        quality_app="Taobao",
        include_manual=False,
        app_filter="Taobao",
    )

    assert report["source_policy"]["include_manual"] is False
    assert report["manual"]["pages_imported"] == 0
    assert report["exploration"]["pages_imported"] == 1
    assert report["totals"]["pages"] == 1
    assert report["source_policy"]["skipped_exploration_files"][0]["app"] == "JD"
