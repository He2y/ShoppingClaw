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


# ── fourth issue: no round-end mechanism, Ctrl+C lost all data ──


def test_select_session_goal_picks_uncovered_skeleton_first():
    from phone_agent.memory.exploration.task_builder import select_session_goal
    from phone_agent.memory.exploration.types import CoverageTarget

    coverage = CoverageTarget(
        page_types=("home", "search_input", "search_result", "category"),
        transitions=(
            ("home", "search_input"),
            ("search_input", "search_result"),
            ("home", "category"),
        ),
    )
    # First round: nothing covered → skeleton head
    goal = select_session_goal(coverage, set(), set(), max_edges=2)
    assert goal.transitions == (("home", "search_input"), ("search_input", "search_result"))
    # Second round: skeleton done → remaining gap
    covered = {("home", "search_input"), ("search_input", "search_result")}
    goal2 = select_session_goal(coverage, set(), covered, max_edges=2)
    assert goal2.transitions == (("home", "category"),)


def test_select_session_goal_honors_focus():
    from phone_agent.memory.exploration.task_builder import select_session_goal
    from phone_agent.memory.exploration.types import CoverageTarget

    coverage = CoverageTarget(
        page_types=("home", "category", "my_account"),
        transitions=(("home", "category"), ("home", "my_account")),
    )
    goal = select_session_goal(coverage, set(), set(), focus="category")
    assert goal.transitions == (("home", "category"),)
    goal2 = select_session_goal(coverage, set(), set(), focus="home->my_account")
    assert ("home", "my_account") in goal2.transitions


def test_load_historical_coverage_merges_previous_runs(tmp_path):
    import json as _json
    from phone_agent.memory.exploration.task_builder import load_historical_coverage

    (tmp_path / "jd_explore_transitions_1.json").write_text(_json.dumps({
        "coverage": {
            "covered_page_types": ["home", "search_input"],
            "covered_transitions": [["home", "search_input"]],
        }
    }), encoding="utf-8")
    (tmp_path / "jd_explore_transitions_2.json").write_text(_json.dumps({
        "coverage": {
            "covered_page_types": ["search_result"],
            "covered_transitions": [["search_input", "search_result"]],
        }
    }), encoding="utf-8")
    pages, transitions = load_historical_coverage(tmp_path)
    assert pages == {"home", "search_input", "search_result"}
    assert transitions == {("home", "search_input"), ("search_input", "search_result")}


def test_session_goal_reached_ends_round():
    from phone_agent.memory.exploration.explorer import OfflineExplorer
    from phone_agent.memory.exploration.types import CoverageTarget

    explorer = object.__new__(OfflineExplorer)
    explorer.session_goal = CoverageTarget(
        page_types=("home", "search_input"),
        transitions=(("home", "search_input"),),
    )
    explorer.transitions = []
    explorer.verbose = False
    assert not explorer._session_goal_reached()
    explorer.transitions = [{"from": "home:首页", "action": {}, "to": "search_input:搜索"}]
    assert explorer._session_goal_reached()


# ── fifth issue: model forgets the round focus and loops the main chain ──


def _explorer_with_goal(transitions_done):
    from phone_agent.memory.exploration.explorer import OfflineExplorer
    from phone_agent.memory.exploration.types import CoverageTarget

    explorer = object.__new__(OfflineExplorer)
    explorer.session_goal = CoverageTarget(
        page_types=("home", "search_input", "search_result", "filter_panel"),
        transitions=(
            ("home", "search_input"),
            ("search_input", "search_result"),
            ("search_result", "filter_panel"),
        ),
    )
    explorer.transitions = [
        {"from": f"{a}:x", "action": {}, "to": f"{b}:y"} for a, b in transitions_done
    ]
    explorer.verbose = False
    return explorer


def test_session_goal_progress_lists_only_remaining_edges():
    explorer = _explorer_with_goal([("home", "search_input"), ("search_input", "search_result")])
    note = explorer._build_session_goal_progress()
    assert "search_result->filter_panel" in note
    assert "已完成" in note and "home->search_input" in note
    assert "不要重复" in note


def test_session_goal_progress_empty_when_goal_done():
    explorer = _explorer_with_goal([
        ("home", "search_input"),
        ("search_input", "search_result"),
        ("search_result", "filter_panel"),
    ])
    assert explorer._build_session_goal_progress() == ""


def test_discovered_summary_shows_round_focus_not_full_gap():
    from phone_agent.memory.exploration.types import PageInfo

    explorer = _explorer_with_goal([("home", "search_input")])
    explorer.discovered_pages = {
        "home:x": PageInfo(page_type="home", semantic_summary="x", elements={},
                           screenshot_hash="h", app="京东"),
    }
    explorer.coverage_targets = explorer.session_goal
    summary = explorer._build_discovered_summary()
    assert "本轮剩余焦点转移" in summary
    assert "search_result->filter_panel" in summary
    assert "缺失页面类型" not in summary


def test_trap_filter_ignores_reasoning_text():
    from phone_agent.memory.exploration.explorer import OfflineExplorer
    from phone_agent.memory.exploration.safety import SafetyPolicy

    explorer = object.__new__(OfflineExplorer)
    explorer.safety = SafetyPolicy(
        high_risk_page_types=frozenset(),
        unsafe_tokens=(),
        risky_cta_tokens=(),
        risky_cta_allowed_pages=frozenset(),
        trap_tokens=("以旧换新",),
    )
    action = {"_metadata": "do", "action": "Tap", "element": [518, 949]}
    reasoning = "商品标题是小米空调 以旧换新 高效节能，我点击加入购物车按钮。"
    assert not explorer._action_has_trap_token(action, reasoning)
    trap_action = {"_metadata": "do", "action": "Tap", "semantic_target": "以旧换新入口"}
    assert explorer._action_has_trap_token(trap_action, "")


# ── sixth issue: small model can't plan — strong VLM plans, GUI model executes ──


def test_parse_focus_supports_natural_language():
    from phone_agent.memory.exploration.task_builder import parse_focus

    pages, transitions, natural = parse_focus("探索个人中心和订单列表,category,home->search_input")
    assert pages == {"category"}
    assert transitions == {("home", "search_input")}
    assert natural == "探索个人中心和订单列表"


def test_focus_task_has_no_skeleton_chain():
    from phone_agent.memory.exploration.task_builder import build_focus_task

    task = build_focus_task("search_result->filter_panel", "京东")
    assert "核心空间骨架" not in task
    assert "home ->" not in task and "home->search_input" not in task
    assert "search_result->filter_panel" in task

    natural_task = build_focus_task("探索京东的国家补贴频道", "京东")
    assert "国家补贴频道" in natural_task
    assert "核心空间骨架" not in natural_task


def test_pure_natural_focus_yields_empty_structured_goal():
    from phone_agent.memory.exploration.task_builder import select_session_goal
    from phone_agent.memory.exploration.types import CoverageTarget

    coverage = CoverageTarget(
        page_types=("home", "category"), transitions=(("home", "category"),),
    )
    goal = select_session_goal(coverage, set(), set(), focus="探索个人中心")
    assert goal.transitions == ()  # planner owns the natural goal


def test_planner_plan_step_parses_vlm_json(monkeypatch):
    from types import SimpleNamespace
    from phone_agent.memory.exploration.planner import PlanContext, StrongPlanner

    planner = StrongPlanner.__new__(StrongPlanner)
    fake_raw = (
        '{"thought": "需要打开筛选", "instruction": "点击右上角的\\"筛选\\"按钮", '
        '"target_transition": ["search_result", "filter_panel"], '
        '"expected_page": "filter_panel", "finish": false, "message": ""}'
    )

    class FakeCompletions:
        def create(self, **kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=fake_raw))])

    class FakeProxy:
        providers = [SimpleNamespace(source="fake", model="m", base_url="u", api_key="k")]
        max_image_width = 720

        def _crop_screenshot(self, b64, w, h, mw):
            return b64

        def _client_for_provider(self, p):
            return SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

    planner._proxy = FakeProxy()
    planner.last_raw = ""
    ctx = PlanContext(remaining_edges=(("search_result", "filter_panel"),))
    step = planner.plan_step("b64", 100, 100, current_page_type="search_result", context=ctx)
    assert step is not None
    assert "筛选" in step.instruction
    assert step.target_transition == ("search_result", "filter_panel")
    assert step.expected_page == "filter_panel"
    assert not step.finish


def test_planner_finishes_when_no_goal_left():
    from phone_agent.memory.exploration.planner import PlanContext, StrongPlanner

    planner = StrongPlanner.__new__(StrongPlanner)
    planner._proxy = None
    step = planner.plan_step("b64", 100, 100, current_page_type="home", context=PlanContext())
    assert step is not None and step.finish


def test_plan_context_natural_goal_keeps_planner_running():
    from phone_agent.memory.exploration.planner import PlanContext

    ctx = PlanContext(natural_goal="探索个人中心")
    assert ctx.natural_goal
    ctx.push_result("指令『点击我的』→ 落到 my_account")
    assert len(ctx.recent_results) == 1
