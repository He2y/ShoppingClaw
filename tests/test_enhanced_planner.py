"""Tests for enhanced planner (Definition 5)."""

from phone_agent.spatial.amsg_config import AMSGOptimConfig
from phone_agent.spatial.enhanced_planner import EnhancedPlanner


class FakeEdge:
    def __init__(self, source_id, target_id, postcondition, cost=1.0, action_type="tap", action_target=""):
        self.source_id = source_id
        self.target_id = target_id
        self.postcondition = postcondition
        self.action_type = action_type
        self.action_target = action_target
        self.risk = "normal"
        self.confidence = 0.8
        self.success_count = 1
        self.fail_count = 0

    @property
    def weighted_cost(self):
        return 1.0


class FakeState:
    def __init__(self, state_id, page_type):
        self.state_id = state_id
        self.page_type = page_type


class FakeTransition:
    def __init__(self, source, target):
        self.source = source
        self.target = target


class FakeSchema:
    transitions = [
        FakeTransition("home", "search_input"),
        FakeTransition("search_input", "search_result"),
        FakeTransition("search_result", "product_detail"),
        FakeTransition("product_detail", "spec_selection"),
        FakeTransition("spec_selection", "cart"),
    ]


def _build_graph():
    """Build a simple graph: home → search_input → search_result → product_detail → spec_selection."""
    edges = {
        "s_home": [FakeEdge("s_home", "s_search_input", "search_input")],
        "s_search_input": [FakeEdge("s_search_input", "s_search_result", "search_result")],
        "s_search_result": [FakeEdge("s_search_result", "s_product_detail", "product_detail")],
        "s_product_detail": [FakeEdge("s_product_detail", "s_spec_selection", "spec_selection")],
    }
    states = {
        "s_home": FakeState("s_home", "home"),
        "s_search_input": FakeState("s_search_input", "search_input"),
        "s_search_result": FakeState("s_search_result", "search_result"),
        "s_product_detail": FakeState("s_product_detail", "product_detail"),
        "s_spec_selection": FakeState("s_spec_selection", "spec_selection"),
    }
    return edges, states


def test_dijkstra_finds_path():
    planner = EnhancedPlanner(AMSGOptimConfig(planner_backend="dijkstra"))
    edges, states = _build_graph()
    path = planner.plan(
        "s_home", {"spec_selection"},
        lambda sid: edges.get(sid, []),
        lambda sid: states.get(sid),
    )
    assert len(path) == 4
    assert path[-1].postcondition == "spec_selection"


def test_astar_finds_path():
    planner = EnhancedPlanner(AMSGOptimConfig(planner_backend="astar"), schema=FakeSchema())
    edges, states = _build_graph()
    path = planner.plan(
        "s_home", {"spec_selection"},
        lambda sid: edges.get(sid, []),
        lambda sid: states.get(sid),
    )
    assert len(path) == 4
    assert path[-1].postcondition == "spec_selection"


def test_belief_astar_finds_path():
    planner = EnhancedPlanner(AMSGOptimConfig(planner_backend="belief_astar"), schema=FakeSchema())
    edges, states = _build_graph()
    path = planner.plan(
        "s_home", {"spec_selection"},
        lambda sid: edges.get(sid, []),
        lambda sid: states.get(sid),
        belief_entropy=0.5,
    )
    assert len(path) == 4
    assert path[-1].postcondition == "spec_selection"


def test_schema_heuristic_admissible():
    planner = EnhancedPlanner(schema=FakeSchema())
    # Heuristic should never overestimate: h(s) <= true cost
    assert planner.schema_heuristic("home", {"spec_selection"}) == 4
    assert planner.schema_heuristic("search_result", {"spec_selection"}) == 2
    assert planner.schema_heuristic("spec_selection", {"spec_selection"}) == 0


def test_schema_heuristic_unknown_page():
    planner = EnhancedPlanner(schema=FakeSchema())
    # Unknown page type → 0 (safe: underestimates)
    assert planner.schema_heuristic("unknown", {"spec_selection"}) == 0.0


def test_no_path_returns_empty():
    planner = EnhancedPlanner(AMSGOptimConfig(planner_backend="astar"))
    path = planner.plan(
        "s_isolated", {"spec_selection"},
        lambda sid: [],
        lambda sid: None,
    )
    assert path == []


def test_enhanced_cost_temporal_decay():
    planner = EnhancedPlanner(AMSGOptimConfig(planner_backend="belief_astar"))
    edge = FakeEdge("s1", "s2", "product_detail")

    # Fresh edge: no staleness
    planner._step_counter = 0
    cost0 = planner.enhanced_cost(edge)

    # After many steps: staleness increases cost
    planner._step_counter = 100
    cost100 = planner.enhanced_cost(edge)
    assert cost100 > cost0


def test_enhanced_cost_exploration_bonus():
    planner = EnhancedPlanner(AMSGOptimConfig(
        planner_backend="belief_astar",
        exploration_bonus_weight=1.0,
    ))
    edge = FakeEdge("s1", "s2", "product_detail")

    # Unvisited target: high exploration bonus → lower cost
    cost_unvisited = planner.enhanced_cost(edge)

    # Visited target: lower bonus → higher cost
    planner._visit_counts["s2"] = 10
    cost_visited = planner.enhanced_cost(edge)
    assert cost_visited > cost_unvisited


def test_enhanced_cost_outcome_entropy_penalty():
    planner = EnhancedPlanner(AMSGOptimConfig(planner_backend="belief_astar"))
    edge = FakeEdge("s1", "s2", "product_detail")

    cost_low_entropy = planner.enhanced_cost(edge, outcome_entropy=0.0)
    cost_high_entropy = planner.enhanced_cost(edge, outcome_entropy=1.5)
    assert cost_high_entropy > cost_low_entropy


def test_all_backends_same_result_on_linear_graph():
    """All three backends should find the same path on a linear graph."""
    edges, states = _build_graph()
    load_fn = lambda sid: edges.get(sid, [])
    state_fn = lambda sid: states.get(sid)
    targets = {"spec_selection"}

    for backend in ("dijkstra", "astar", "belief_astar"):
        planner = EnhancedPlanner(AMSGOptimConfig(planner_backend=backend), schema=FakeSchema())
        path = planner.plan("s_home", targets, load_fn, state_fn)
        assert len(path) == 4, f"{backend} found {len(path)} steps"
