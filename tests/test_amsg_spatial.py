import json
from types import SimpleNamespace

from phone_agent.memory.offline_explorer import (
    OfflineExplorer,
    PageInfo,
    ShoppingPageType,
    _PAGE_TYPE_MAP,
)
from phone_agent.model.protocol_bridge import ModelProtocolBridge
from phone_agent.spatial.active_builder import ActiveGraphBuilder
from phone_agent.spatial.core import BeliefCandidate, BeliefState, PageNode
from phone_agent.spatial.hypothesis import EdgeHypothesis
from phone_agent.spatial.model_bridge import SpatialModelBridge
from phone_agent.spatial.reporting import build_amsg_dry_run_report, format_amsg_markdown
from phone_agent.spatial.schema_registry import SchemaRegistry
from phone_agent.spatial.semantics import ScreenSemanticsExtractor
from phone_agent.spatial.verifier import PostconditionVerifier


def test_schema_registry_merges_common_and_shopping_aliases():
    schema = SchemaRegistry().merged("shopping")

    assert "home" in schema.page_types
    assert "product_detail" in schema.page_types
    assert SchemaRegistry().app_matches("\u6dd8\u5b9d", "\u5a23\u6a3a\u7582")
    assert schema.page_type_covered("list", {"search_result"})
    assert schema.page_type_covered("detail", {"product_detail"})


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


def test_protocol_bridge_compiles_parsed_model_action_to_canonical_memory_action():
    parsed = SimpleNamespace(action_type="click", params={"coordinate": [100, 200]})

    device_action = ModelProtocolBridge.normalize_action(
        parsed,
        model_type="qwenvl",
        screen_size=(1080, 1920),
    )
    canonical = ModelProtocolBridge.to_autoglm_action(device_action)

    assert device_action.action_type == "click"
    assert canonical["_metadata"] == "do"
    assert canonical["action"] == "Tap"
    assert canonical["element"] == [100, 200]


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


def test_amsg_dry_run_report_summarizes_schema_and_frontiers(tmp_path):
    pages_path = tmp_path / "taobao_explore_1.json"
    pages_path.write_text(
        json.dumps(
            {
                "app": "Taobao",
                "pages": [
                    {
                        "app": "Taobao",
                        "page_type": "search_result",
                        "summary": "product list",
                        "elements": {"product_cards": "tap product"},
                        "screenshot_hash": "hash1",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    rebuild_report = {
        "exploration": {
            "quality_reports": [
                {"staging": {"transitions_seen": 4, "transitions_promoted": 3}},
            ]
        }
    }

    report = build_amsg_dry_run_report(
        exploration_root=tmp_path,
        rebuild_report=rebuild_report,
        app_filter="淘宝",
    )
    markdown = format_amsg_markdown(report)

    assert report["schema_coverage"]["observed_page_types"] == ["search_result"]
    assert "list" not in report["schema_coverage"]["missing_safe_page_types"]
    assert report["edge_quality"]["valid_edge_ratio"] == 0.75
    assert report["active_frontiers"]
    assert "AMSG v3 Dry-Run Report" in markdown


def test_offline_explorer_treats_interference_dialog_as_schema_page():
    assert _PAGE_TYPE_MAP["dialog"] == ShoppingPageType.DIALOG

    source = PageInfo(
        page_type=ShoppingPageType.DIALOG,
        semantic_summary="coupon dialog",
        elements={},
        screenshot_hash="a",
        app="Taobao",
    )
    target = PageInfo(
        page_type=ShoppingPageType.SEARCH_INPUT,
        semantic_summary="search",
        elements={},
        screenshot_hash="b",
        app="Taobao",
    )

    reason = OfflineExplorer._transition_rejection_reason(
        None,
        source,
        {"action": "Tap", "element": [500, 780]},
        target,
    )

    assert reason == ""


def test_offline_explorer_repairs_page_belief_from_action_reasoning():
    reasoning = (
        "当前界面显示的是淘宝首页，我可以看到顶部有搜索框和推荐商品。"
        "接下来应该点击搜索框。"
    )

    assert OfflineExplorer._infer_page_type_from_reasoning(reasoning) == ShoppingPageType.HOME


def test_offline_explorer_ignores_goal_text_when_repairing_belief():
    reasoning = (
        "用户要求继续探索 home -> search_input -> search_result -> product_detail -> spec_selection -> cart。\n"
        "Verifier note: raw post-action classifier saw cart:购物车列表。\n"
        "当前截图显示的是搜索结果页面，有商品列表和价格。"
    )

    assert OfflineExplorer._infer_page_type_from_reasoning(reasoning) == ShoppingPageType.SEARCH_RESULT


def test_offline_explorer_prefers_search_input_visual_evidence():
    reasoning = (
        "当前截图显示搜索框中有手机，下方显示历史搜索记录和猜你想搜，"
        "也有搜索相关内容。"
    )

    assert OfflineExplorer._infer_page_type_from_reasoning(reasoning) == ShoppingPageType.SEARCH_INPUT


def test_offline_explorer_blocks_purchase_intent_before_execution():
    page = PageInfo(
        page_type=ShoppingPageType.PRODUCT_DETAIL,
        semantic_summary="detail",
        elements={},
        screenshot_hash="a",
        app="Taobao",
    )

    assert not OfflineExplorer._is_safe_action(
        None,
        page,
        {"action": "Tap", "element": [818, 959]},
        "我将点击领券购买按钮，这应该会进入规格选择页面。",
    )

    assert not OfflineExplorer._is_safe_action(
        None,
        page,
        {"action": "Tap", "element": [672, 959]},
        "我将尝试点击橙色购买按钮，看看是否能找到加入购物车入口。",
    )


def test_offline_explorer_does_not_block_safety_constraints_as_intent():
    page = PageInfo(
        page_type=ShoppingPageType.SEARCH_RESULT,
        semantic_summary="result",
        elements={},
        screenshot_hash="a",
        app="Taobao",
    )

    assert OfflineExplorer._is_safe_action(
        None,
        page,
        {"action": "Tap", "element": [499, 331]},
        "不要点击立即购买、领券购买、结算、支付。\n我将点击第一个商品进入商品详情页。",
    )


def test_offline_explorer_allows_add_to_cart_intent_for_spec_discovery():
    page = PageInfo(
        page_type=ShoppingPageType.PRODUCT_DETAIL,
        semantic_summary="detail",
        elements={},
        screenshot_hash="a",
        app="Taobao",
    )

    assert OfflineExplorer._is_safe_action(
        None,
        page,
        {"action": "Tap", "element": [518, 959]},
        "我将点击加入购物车按钮，这应该会进入规格选择页面。",
    )
