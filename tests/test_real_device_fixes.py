"""Regression tests for the first real-device run failures (京东 cold start).

Observed failure chain:
1. Default task text said 淘宝 while exploring 京东 (profile/schema priority bug).
2. Belief repair read task-constraint restatements ("不确认地址" / numbered plan
   lines) as current-page evidence, relabeled search_input as address (high
   risk), and the whole exploration stopped after one step.
3. VLM-generated profile put 加入购物车/立即购买 into unsafe_tokens and mixed
   interference/abstract types into coverage.
"""

from types import SimpleNamespace

from phone_agent.memory.exploration.page_evidence import infer_page_type_from_reasoning
from phone_agent.memory.exploration.task_builder import build_default_task
from phone_agent.memory.exploration.cli import (
    _resolve_default_task,
    _resolve_schema_name,
    _resolve_storage_dir,
)
from phone_agent.spatial.schema_registry import get_default_registry


# ── page evidence: constraint/plan text must not become page belief ──


def test_constraint_restatement_does_not_become_address_evidence():
    reasoning = (
        "当前截图显示：\n"
        "- 当前在京东App的搜索页面\n"
        "- 显示搜索框，搜索框中有格力空调\n"
        "- 但用户特别强调\"只执行安全探索动作，不提交订单、不支付、不确认地址\"\n"
        "- 如果进入登录、支付、地址或订单确认页，立刻返回\n"
    )
    assert infer_page_type_from_reasoning(reasoning) != "address"


def test_numbered_plan_lines_are_not_page_evidence():
    reasoning = (
        "当前已经在京东App中，我可以看到京东的首页。我需要：\n"
        "1. 先点击搜索框（搜索输入）\n"
        "2. 搜索某个商品\n"
        "3. 进入搜索结果\n"
    )
    assert infer_page_type_from_reasoning(reasoning) == "home"


def test_bare_address_token_is_no_longer_sufficient():
    reasoning = "当前截图显示订单页面包含地址信息和商品清单。"
    assert infer_page_type_from_reasoning(reasoning) != "address"


def test_real_address_page_evidence_still_detected():
    reasoning = "当前截图显示的是地址管理页面，有添加新地址按钮和地址列表。"
    assert infer_page_type_from_reasoning(reasoning) == "address"


# ── default task: {app} template, profile priority ──


def test_schema_default_task_uses_app_display_name():
    schema = get_default_registry().merged("shopping")
    task = build_default_task(schema, None, None, "京东")
    assert "京东" in task
    assert "淘宝" not in task


def test_schema_default_task_keeps_taobao_text_for_taobao():
    schema = get_default_registry().merged("shopping")
    task = build_default_task(schema, None, None, "淘宝")
    assert task.startswith("覆盖淘宝购物核心空间骨架")


def test_profile_default_task_wins_over_schema():
    schema = get_default_registry().merged("shopping")
    profile = SimpleNamespace(default_task="探索京东的国补频道")
    assert build_default_task(schema, profile, None, "京东") == "探索京东的国补频道"


# ── cli resolution helpers ──


def test_cli_resolves_jd_task_via_registry_schema():
    args = SimpleNamespace(app="京东", schema=None)
    schema_name = _resolve_schema_name(args, None)
    assert schema_name == "shopping"
    task = _resolve_default_task(args, None, schema_name)
    assert "京东" in task
    assert "淘宝" not in task


def test_cli_storage_dir_defaults_to_canonical_app_id():
    args = SimpleNamespace(app="京东", storage_dir=None)
    assert _resolve_storage_dir(args).endswith("exploration/jd")
    args2 = SimpleNamespace(app="京东", storage_dir="custom/dir")
    assert _resolve_storage_dir(args2) == "custom/dir"


# ── second real-device run: plan text overriding the classifier ──


def test_plan_enumeration_bullets_are_not_evidence():
    reasoning = (
        "从截图来看，当前页面显示的是搜索结果页面。\n"
        "我需要继续探索到：\n"
        "- product_detail（商品详情页）\n"
        "- spec_selection（规格选择）\n"
        "- cart（购物车）\n"
    )
    assert infer_page_type_from_reasoning(reasoning) == "search_result"


def test_plan_intent_sentences_are_not_evidence():
    reasoning = (
        "从截图来看，当前页面显示的是搜索结果页面，搜索内容是米家空调。"
        "然后继续探索到规格选择页，最后到购物车。"
    )
    assert infer_page_type_from_reasoning(reasoning) == "search_result"


def test_skeleton_restatement_with_arrows_is_not_evidence():
    reasoning = (
        "当前截图显示的是商品搜索结果。\n"
        "核心流程：home -> search_input -> search_result -> product_detail -> spec_selection -> cart\n"
    )
    assert infer_page_type_from_reasoning(reasoning) == "search_result"


def test_back_action_is_always_safe_even_with_checkout_reasoning():
    from phone_agent.memory.exploration.safety import SafetyPolicy

    schema = get_default_registry().merged("shopping")
    policy = SafetyPolicy.from_schema(schema)
    page = SimpleNamespace(page_type="cart")
    reasoning = "下一步应该是到 checkout（结算）页面。让我先返回上一级。"
    assert policy.is_safe_action(page, {"_metadata": "do", "action": "Back"}, reasoning)
    # Tapping a checkout button with that reasoning must still be blocked.
    assert not policy.is_safe_action(
        page, {"_metadata": "do", "action": "Tap", "element": [500, 900]}, reasoning
    )


def test_cart_entry_region_accepts_jd_bottom_dock_and_taobao_top_icon():
    from phone_agent.memory.exploration.transition_rules import REGIONS

    cart_entry = REGIONS["cart_entry"]
    assert cart_entry(297, 952)      # JD bottom dock cart tab
    assert cart_entry(844, 71)       # Taobao top-right cart icon
    assert not cart_entry(518, 959)  # bottom-right add-to-cart CTA
    assert not cart_entry(700, 900)  # buy-now CTA zone


# ── third issue: captcha must hand over to the human, not Back out ──


def test_captcha_is_first_class_page_type_and_interference():
    from phone_agent.memory.exploration.types import PageTypeSpace, ShoppingPageType, _PAGE_TYPE_MAP

    schema = get_default_registry().merged("shopping")
    space = PageTypeSpace.from_schema(schema)
    assert "captcha" in space.names
    assert "captcha" in space.interference
    assert _PAGE_TYPE_MAP["captcha"] is ShoppingPageType.CAPTCHA


def test_classifier_prompt_mentions_captcha():
    from phone_agent.memory.exploration.classifier_prompts import build_full_prompt
    from phone_agent.memory.exploration.types import PageTypeSpace

    schema = get_default_registry().merged("shopping")
    prompt = build_full_prompt(PageTypeSpace.from_schema(schema), schema)
    assert "captcha" in prompt
    assert "滑块" in prompt


def test_page_evidence_detects_captcha_challenge():
    reasoning = "当前屏幕显示京东验证页面，有一个安全验证弹窗，要求按照图中轨迹绘制。"
    assert infer_page_type_from_reasoning(reasoning) == "captcha"


def _make_captcha_handler(human_gate, classify_results):
    from phone_agent.memory.exploration.interference import InterferenceHandler, InterferencePolicy
    from phone_agent.memory.exploration.types import PageTypeSpace

    schema = get_default_registry().merged("shopping")
    space = PageTypeSpace.from_schema(schema)
    results = iter(classify_results)

    class FakeActionHandler:
        executed = []

        def execute(self, action, w, h):
            self.executed.append(action)
            return SimpleNamespace(success=True, message="")

    def classify_fn(screenshot, app, step):
        return next(results)

    def capture_fn():
        return SimpleNamespace(base64_data="", width=100, height=100)

    return InterferenceHandler(
        InterferencePolicy(captcha_action="pause_for_human"),
        space,
        FakeActionHandler(),
        classify_fn,
        capture_fn,
        lambda msg: None,
        human_gate=human_gate,
    )


def test_captcha_page_pauses_for_human_and_resumes():
    resumed_page = SimpleNamespace(page_type="search_result", app="京东", semantic_summary="搜索结果",
                                   screenshot_base64="")
    captcha_page = SimpleNamespace(page_type="captcha", app="京东",
                                   semantic_summary="安全验证弹窗，需按轨迹绘制", screenshot_base64="")

    class FakeGate:
        requests = []

        def request(self, req):
            self.requests.append(req)
            return "resumed"

    gate = FakeGate()
    handler = _make_captcha_handler(gate, [resumed_page])
    outcome = handler.handle(captcha_page, 100, 100, "search_result:搜索结果")

    assert outcome.status == "human_resumed"
    assert outcome.resumed_page is resumed_page
    assert gate.requests[0].kind == "captcha"


def test_dialog_with_captcha_summary_pauses_for_human():
    """Even when the classifier says dialog, the captcha summary triggers the gate."""
    captcha_dialog = SimpleNamespace(page_type="dialog", app="京东",
                                     semantic_summary="安全验证弹窗，需按轨迹绘制", screenshot_base64="")
    resumed_page = SimpleNamespace(page_type="search_result", app="京东", semantic_summary="搜索结果",
                                   screenshot_base64="")

    class FakeGate:
        def request(self, req):
            return "resumed"

    handler = _make_captcha_handler(FakeGate(), [resumed_page])
    outcome = handler.handle(captcha_dialog, 100, 100, "src")
    assert outcome.status == "human_resumed"


def test_captcha_without_gate_falls_back_to_dismissal():
    captcha_page = SimpleNamespace(page_type="captcha", app="京东",
                                   semantic_summary="安全验证", screenshot_base64="")
    after_back = SimpleNamespace(page_type="search_result", app="京东", semantic_summary="搜索结果",
                                 screenshot_base64="")
    handler = _make_captcha_handler(None, [after_back])
    outcome = handler.handle(captcha_page, 100, 100, "src")
    assert outcome.status in {"resolved", "needs_restart"}
