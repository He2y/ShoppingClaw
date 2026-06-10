import base64
import json
from io import BytesIO
from types import SimpleNamespace

from PIL import Image

from phone_agent.memory import offline_explorer
from phone_agent.memory.exploration import classifier as exploration_classifier
from phone_agent.memory.offline_explorer import (
    OfflineExplorer,
    PageClassifier,
    PageInfo,
    ShoppingPageType,
    _PAGE_TYPE_MAP,
)
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
    assert schema.page_type_covered("list", {"search_result"})
    assert schema.page_type_covered("detail", {"product_detail"})
    transitions = {(item.source, item.target, item.intent) for item in schema.transitions}
    assert ("spec_selection", "product_detail", "confirm_add_to_cart") in transitions
    assert ("product_detail", "cart", "go_cart") in transitions
    go_cart = next(item for item in schema.transitions if item.intent == "go_cart")
    open_spec = next(item for item in schema.transitions if item.intent == "open_spec")
    assert go_cart.target_locator["region"] == "top_right"
    assert open_spec.target_locator["region"] == "bottom_cta"


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


def test_offline_explorer_requires_top_cart_entry_for_detail_to_cart():
    source = PageInfo(
        page_type=ShoppingPageType.PRODUCT_DETAIL,
        semantic_summary="detail",
        elements={},
        screenshot_hash="a",
        app="Taobao",
    )
    target = PageInfo(
        page_type=ShoppingPageType.CART,
        semantic_summary="cart",
        elements={},
        screenshot_hash="b",
        app="Taobao",
    )

    top_reason = OfflineExplorer._transition_rejection_reason(
        None,
        source,
        {"action": "Tap", "element": [844, 71]},
        target,
    )
    bottom_reason = OfflineExplorer._transition_rejection_reason(
        None,
        source,
        {"action": "Tap", "element": [518, 959]},
        target,
    )

    assert top_reason == ""
    assert bottom_reason == "product_detail->cart must use top cart entry, not bottom add-to-cart CTA"


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


def test_offline_explorer_repairs_search_input_from_current_status_line():
    reasoning = (
        "当前状态：我看到淘宝的搜索界面，搜索框中已经输入了手机壳，"
        "并且显示了搜索建议列表。ADB Keyboard {ON}显示在底部。"
    )

    assert OfflineExplorer._infer_page_type_from_reasoning(reasoning) == ShoppingPageType.SEARCH_INPUT


def test_offline_explorer_does_not_treat_pay_button_as_payment_page():
    reasoning = (
        "当前屏幕显示的是商品规格选择页面，有颜色和型号选项，"
        "底部有立即支付按钮。"
    )

    assert OfflineExplorer._infer_page_type_from_reasoning(reasoning) == ShoppingPageType.SPEC_SELECTION


def test_offline_explorer_prefers_spec_selection_over_generic_dialog():
    reasoning = (
        "当前已经在淘宝的商品详情页，并且出现了一个规格选择弹窗。"
        "弹窗中有适用手机型号、颜色分类和加入购物车按钮。"
    )

    assert OfflineExplorer._infer_page_type_from_reasoning(reasoning) == ShoppingPageType.SPEC_SELECTION


def test_offline_explorer_does_not_treat_detail_cart_icon_as_cart_page():
    reasoning = (
        "当前截图显示的是淘宝商品详情页，顶部右侧有购物车图标并显示数量40，"
        "底部有加入购物车和立即购买按钮。"
    )

    assert OfflineExplorer._infer_page_type_from_reasoning(reasoning) == ShoppingPageType.PRODUCT_DETAIL


def test_offline_explorer_recognizes_cart_page_from_page_controls():
    reasoning = (
        "当前截图显示的是购物车页面，商品列表每项有圆形复选框，底部有全选和去结算按钮。"
    )

    assert OfflineExplorer._infer_page_type_from_reasoning(reasoning) == ShoppingPageType.CART


def test_offline_explorer_repairs_cart_from_parse_error_text():
    reasoning = "从截图来看，当前页面是购物车页面，显示购物车 (40)，有商品列表、全选和去结算按钮。"

    assert OfflineExplorer._infer_page_type_from_reasoning(reasoning) == ShoppingPageType.CART


def test_offline_explorer_does_not_repair_safety_ban_as_login_page():
    reasoning = (
        "从当前截图来看，我已经在商品详情页了，页面显示荣耀手机的价格、配置和颜色选择。"
        "根据安全规则，不要购买、不要加购、不要登录、不要支付或提交订单。"
    )

    assert OfflineExplorer._infer_page_type_from_reasoning(reasoning) == ShoppingPageType.PRODUCT_DETAIL


def test_offline_explorer_allows_purchase_cta_as_spec_trigger_on_detail():
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
        {"action": "Tap", "element": [818, 959]},
        "我将点击领券购买按钮，这应该会进入规格选择页面。",
    )

    assert OfflineExplorer._is_safe_action(
        None,
        page,
        {"action": "Tap", "element": [672, 959]},
        "我将尝试点击橙色购买按钮，看看是否能找到加入购物车入口。",
    )


def test_offline_explorer_blocks_final_risk_actions():
    page = PageInfo(
        page_type=ShoppingPageType.SPEC_SELECTION,
        semantic_summary="spec",
        elements={},
        screenshot_hash="a",
        app="Taobao",
    )

    assert not OfflineExplorer._is_safe_action(
        None,
        page,
        {"action": "Tap", "element": [672, 959]},
        "我将点击立即支付按钮。",
    )


def test_offline_explorer_blocks_purchase_cta_outside_spec_flow():
    page = PageInfo(
        page_type=ShoppingPageType.HOME,
        semantic_summary="home",
        elements={},
        screenshot_hash="a",
        app="Taobao",
    )

    assert not OfflineExplorer._is_safe_action(
        None,
        page,
        {"action": "Tap", "element": [672, 959]},
        "我将点击去购买按钮。",
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


def test_offline_explorer_does_not_block_task_flow_lists_as_intent():
    page = PageInfo(
        page_type=ShoppingPageType.HOME,
        semantic_summary="home",
        elements={},
        screenshot_hash="a",
        app="Taobao",
    )

    assert OfflineExplorer._is_safe_action(
        None,
        page,
        {"action": "Tap", "element": [429, 114]},
        "用户要求我执行任务流程：点击搜索框，输入手机壳，点击底部加购/购买类 CTA。",
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


def test_page_classifier_defaults_to_configured_strong_vlm(monkeypatch):
    _disable_real_vlm_env(monkeypatch)
    monkeypatch.setenv("AMSG_STRONG_VLM_BASE_URL", "https://strong.example/v1")
    monkeypatch.setenv("AMSG_STRONG_VLM_MODEL", "strong-vlm")
    monkeypatch.setenv("AMSG_STRONG_VLM_API_KEY", "secret")

    classifier = PageClassifier(mode="off")

    assert classifier.base_url == "https://strong.example/v1"
    assert classifier.model == "strong-vlm"
    assert classifier.source == "amsg_strong_vlm"


def test_page_classifier_falls_back_between_providers_and_uses_runtime_budget(monkeypatch):
    _disable_real_vlm_env(monkeypatch)
    monkeypatch.setenv("AMSG_STRONG_VLM_BASE_URL", "https://strong.example/v1")
    monkeypatch.setenv("AMSG_STRONG_VLM_MODEL", "strong-vlm")
    monkeypatch.setenv("AMSG_STRONG_VLM_API_KEY", "secret")
    monkeypatch.setenv("OFFLINE_VLM_BASE_URL", "https://offline.example/v1")
    monkeypatch.setenv("OFFLINE_VLM_MODEL", "offline-vlm")
    monkeypatch.setenv("OFFLINE_VLM_API_KEY", "offline-secret")
    monkeypatch.setattr(exploration_classifier, "load_dotenv", lambda: None)

    calls: list[dict] = []
    responses = {
        "https://strong.example/v1": [SimpleNamespace(content="")],
        "https://offline.example/v1": [
            SimpleNamespace(content='{"page_type":"search_result","summary":"results"}')
        ],
    }

    class FakeCompletions:
        def __init__(self, base_url):
            self.base_url = base_url

        def create(self, **kwargs):
            calls.append({"base_url": self.base_url, **kwargs})
            message = responses[self.base_url].pop(0)
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    class FakeOpenAI:
        def __init__(self, base_url, api_key, timeout):
            self.chat = SimpleNamespace(completions=FakeCompletions(base_url))

    monkeypatch.setattr(exploration_classifier, "OpenAI", FakeOpenAI)

    classifier = PageClassifier(mode="fast")
    page_type, summary, elements = classifier.classify(_tiny_png_b64(), 120, 120)

    assert page_type == ShoppingPageType.SEARCH_RESULT
    assert summary == "results"
    assert elements == {}
    assert classifier.source == "offline_vlm"
    assert classifier.last_diagnostics["fallback_used"] is True
    assert calls[0]["max_tokens"] == 512
    assert calls[1]["max_tokens"] == 512


def test_page_classifier_returns_unknown_with_diagnostics_when_all_providers_fail(monkeypatch):
    _disable_real_vlm_env(monkeypatch)
    monkeypatch.setenv("AMSG_STRONG_VLM_BASE_URL", "https://strong.example/v1")
    monkeypatch.setenv("AMSG_STRONG_VLM_MODEL", "strong-vlm")
    monkeypatch.setenv("AMSG_STRONG_VLM_API_KEY", "secret")
    monkeypatch.setattr(exploration_classifier, "load_dotenv", lambda: None)

    class FakeCompletions:
        def create(self, **kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=""))])

    class FakeOpenAI:
        def __init__(self, base_url, api_key, timeout):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(exploration_classifier, "OpenAI", FakeOpenAI)

    classifier = PageClassifier(mode="full")
    page_type, summary, elements = classifier.classify(_tiny_png_b64(), 120, 120)

    assert page_type == ShoppingPageType.UNKNOWN
    assert elements == {}
    assert "empty content" in summary
    assert "empty content" in classifier.last_diagnostics["raw_error"]


def test_offline_explorer_recognizes_settings_page_as_common_mobile_state():
    reasoning = (
        "当前屏幕显示的是淘宝设置页面，包含账号与安全、隐私设置、通用设置、消息通知和支付设置。"
        "底部还有切换账号和退出登录按钮。"
    )

    assert OfflineExplorer._infer_page_type_from_reasoning(reasoning) == ShoppingPageType.SETTINGS
    fallback = PageClassifier._infer_result_from_text(reasoning)
    assert fallback["page_type"] == "settings"


def _disable_real_vlm_env(monkeypatch):
    for key in (
        "AMSG_STRONG_VLM_BASE_URL",
        "AMSG_STRONG_VLM_MODEL",
        "AMSG_STRONG_VLM_API_KEY",
        "OFFLINE_VLM_BASE_URL",
        "OFFLINE_VLM_MODEL",
        "OFFLINE_VLM_API_KEY",
        "PHONE_AGENT_BASE_URL",
        "PHONE_AGENT_MODEL",
        "PHONE_AGENT_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)


def _tiny_png_b64() -> str:
    buffer = BytesIO()
    Image.new("RGB", (120, 120), color="white").save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")
