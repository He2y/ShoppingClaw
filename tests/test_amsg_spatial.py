from phone_agent.model.protocol_bridge import ModelProtocolBridge
from phone_agent.spatial.active_builder import ActiveGraphBuilder
from phone_agent.spatial.core import BeliefCandidate, BeliefState, PageNode
from phone_agent.spatial.hypothesis import EdgeHypothesis
from phone_agent.spatial.model_bridge import SpatialModelBridge
from phone_agent.spatial.schema_registry import SchemaRegistry
from phone_agent.spatial.semantics import ScreenSemanticsExtractor
from phone_agent.spatial.verifier import PostconditionVerifier


def test_schema_registry_merges_common_and_shopping_aliases():
    schema = SchemaRegistry().merged("shopping")

    assert "home" in schema.page_types
    assert "product_detail" in schema.page_types
    assert SchemaRegistry().app_matches("\u6dd8\u5b9d", "\u5a23\u6a3a\u7582")


def test_semantics_extractor_builds_schema_instance_node():
    extractor = ScreenSemanticsExtractor(schema_name="shopping")
    node = extractor.node_from_screen(
        {
            "app": "Taobao",
            "page_type": "search_result",
            "summary": "product list with filters",
            "elements": {
                "search_bar": "current query",
                "product_cards": "tap product card",
                "filter_tabs": "sort and filter",
            },
            "ui_hash": "abc",
        },
        task="open a product detail page",
    )

    assert node.app == "taobao"
    assert node.page_type == "search_result"
    assert "product_cards" in node.landmarks
    assert "open_detail" in node.affordances
    assert node.evidence["ui_hash"] == "abc"


def test_active_builder_penalizes_high_risk_frontiers():
    safe = EdgeHypothesis(
        hypothesis_id="safe",
        source_node="page_a",
        source_page_type="search_result",
        expected_page_type="product_detail",
        intent="open_product",
        risk="normal",
    )
    risky = EdgeHypothesis(
        hypothesis_id="risky",
        source_node="page_a",
        source_page_type="cart",
        expected_page_type="checkout",
        intent="checkout",
        risk="high",
    )

    ranked = ActiveGraphBuilder().rank([risky, safe])

    assert ranked[0].hypothesis.hypothesis_id == "safe"
    assert ranked[0].score > ranked[1].score


def test_spatial_model_bridge_compiles_graph_action_to_handler_format():
    semantic = SpatialModelBridge.semantic_action_from_next_action(
        {
            "type": "Tap",
            "target": "first product card",
            "target_desc": "{'element': [100, 200]}",
            "postcondition": "product_detail",
            "confidence": 0.91,
            "edge_id": "edge_1",
        }
    )
    action = SpatialModelBridge.compile_to_autoglm_action(
        semantic,
        screen_width=1080,
        screen_height=1920,
    )

    assert action["action"] == "Tap"
    assert action["element"] == [100, 200]
    assert action["semantic_target"] == "first product card"
    assert action["_expected_postcondition"] == "product_detail"
    assert action["source_edge_id"] == "edge_1"


def test_protocol_bridge_normalizes_current_model_formats():
    autoglm = ModelProtocolBridge.normalize_action(
        {"_metadata": "do", "action": "Tap", "element": [500, 250]},
        model_type="autoglm",
        screen_size=(1080, 1920),
    )
    uitars = ModelProtocolBridge.normalize_action(
        "click(point='<point>540 960</point>')",
        model_type="uitars",
        screen_size=(1080, 1920),
    )
    qwen = ModelProtocolBridge.normalize_action(
        '<tool_call>{"name":"mobile_use","arguments":{"action":"click","coordinate":[100,200]}}</tool_call>',
        model_type="qwenvl",
        screen_size=(1080, 1920),
    )

    assert autoglm.action_type == "click"
    assert autoglm.coordinate == (500.0, 250.0)
    assert uitars.action_type == "click"
    assert uitars.coordinate is not None
    assert uitars.coordinate_space == "absolute"
    assert qwen.action_type == "click"
    assert qwen.coordinate == (100.0, 200.0)
    assert qwen.coordinate_space == "normalized_999"


def test_postcondition_verifier_marks_negative_high_risk_mismatch():
    verifier = PostconditionVerifier()
    payment_node = PageNode.build(app="taobao", domain="shopping", page_type="payment", risk_level="high")
    belief = BeliefState(
        current_node_id=payment_node.node_id,
        candidates=(BeliefCandidate(payment_node, 0.92, "localized"),),
        confidence=0.92,
    )

    result = verifier.verify(belief, expected_postcondition="product_detail", source_edge_id="edge_bad")

    assert result.ok is False
    assert result.repair_action == "ask_user"
    assert verifier.negative_memory.failure_count("edge_bad") == 1
