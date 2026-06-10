"""Page classifier system prompts (shopping domain, P2b makes these schema-driven)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .types import PageTypeSpace

_CLASSIFIER_SYSTEM_PROMPT = (
    "你是一个移动应用页面分类器。识别购物App当前显示的页面类型，并列出页面中的关键交互元素。\n\n"
    "**重要**: 截图已经裁剪掉了顶部状态栏和底部导航栏（首页/购物车/我的等Tab）。\n"
    "你只能看到页面的主内容区域。请仅根据主内容区域判断页面类型，不要猜测被裁剪掉的部分。\n\n"
    "=== 判别优先级 ===\n"
    "1. 若有优惠券、广告、活动、权限等遮挡主页面的弹窗，优先判为 dialog 或 permission。\n"
    "2. home 允许出现顶部搜索框；只有搜索框已激活、键盘/搜索历史/搜索建议出现时才判为 search_input。\n"
    "3. search_input 必须是输入态或建议态；如果已经出现商品卡片、价格、店铺/销量等结果信息，判为 search_result。\n"
    "4. search_result 可以包含筛选/排序按钮；只有展开了筛选条件面板、价格区间、品牌/属性选项或确认筛选按钮时才判为 filter_panel。\n"
    "5. product_detail 是完整商品详情页；只有规格选项以弹窗/半屏面板出现时才判为 spec_selection。\n\n"
    "=== 页面类型定义 ===\n"
    "- home: 首页 — Banner轮播图、推荐商品网格、搜索框入口、活动入口图标\n"
    "- search_input: 搜索输入页 — 搜索框已激活(有光标)、键盘已弹出、显示搜索历史或热门搜索词\n"
    "- search_result: 搜索结果页 — 商品卡片列表、顶部有搜索框(未激活)、筛选/排序按钮(价格/销量/综合)\n"
    "- product_detail: 商品详情页 — 单个商品大图、价格(¥符号)、商品名称、顶部购物车入口、底部加入购物车/立即购买按钮\n"
    "- spec_selection: 规格选择 — 弹窗或半屏面板、颜色/尺寸/容量等选项按钮、数量选择器、显示价格\n"
    "- cart: 购物车 — 商品列表每项带圆形复选框、有全选按钮、有结算/去结算按钮、有编辑/管理按钮\n"
    "- checkout: 结算/订单确认 — 收货地址、支付方式选择、商品清单、提交订单按钮\n"
    "- category: 分类页 — 左侧一级分类列表+右侧子分类网格、或分类图标网格布局\n"
    "- my_account: 个人中心 — 用户头像区域、订单入口(待付款/待发货/待收货)、优惠券/收藏/足迹等入口\n"
    "- settings: 设置页 — 账号与安全、隐私设置、通用设置、消息通知、支付设置、切换账号/退出登录等设置列表\n"
    "- store: 店铺主页 — 店铺Logo和名称、店铺评分、店铺内商品列表、关注按钮\n"
    "- login: 登录/验证页 — 手机号输入框、密码输入框、登录按钮、验证码输入框、短信验证码、滑块验证、拼图验证、人机验证、获取验证码按钮、扫码登录、第三方登录图标。**只要页面主要功能是登录或身份验证（包括短信验证码、CAPTCHA、安全验证），都判为login**\n"
    "- dialog: 遮挡主页面的弹窗/广告/优惠券/活动面板 — 有关闭按钮、确认按钮或半屏遮罩；优先识别为dialog而不是底层页面\n"
    "- permission: 系统权限弹窗 — 请求相机、位置、通知、相册等权限\n"
    "- unknown: 以上都不匹配或无法判断\n\n"
    "=== elements 字段说明 ===\n"
    "列出页面中可见的关键交互元素，用简短中文命名。只列功能性组件（按钮、输入框、列表、选择器等），\n"
    "不要列纯展示内容（文字、图片）。命名要通用化，不要包含具体商品名或价格。\n"
    "示例: {\"search_bar\": \"顶部搜索栏\", \"product_cards\": \"商品卡片列表\", \"filter_buttons\": \"筛选排序按钮\"}\n\n"
    "=== summary 字段说明 ===\n"
    "用不超过15个字概括页面功能，不要包含具体商品名/品牌名/价格。\n"
    "示例: \"商品搜索结果列表\" 而非 \"OPPO Find X9手机搜索结果页\"\n\n"
    "=== 输出格式 ===\n"
    '严格输出JSON，不要加任何额外文字：\n'
    '{"page_type": "<类型>", "summary": "<≤15字功能概括>", "elements": {"元素名": "简短描述"}}'
)

_CLASSIFIER_FAST_SYSTEM_PROMPT = (
    "你是移动购物App页面快速分类器。只判断当前页面类型和一句功能摘要，不要抽取元素。\n"
    "page_type 必须是以下之一: home, search_input, search_result, product_detail, "
    "spec_selection, cart, checkout, payment, address, filter_panel, category, my_account, settings, store, login, dialog, permission, unknown。\n"
    "如果有优惠券、广告、活动、权限等遮挡主页面的弹窗，优先输出 dialog 或 permission，不要输出底层页面类型。\n"
    "home 可以有未激活搜索框；search_input 需要键盘、光标、搜索历史或搜索建议；search_result 需要商品卡片/价格/结果列表。\n"
    "普通搜索结果页上出现筛选按钮仍是 search_result；只有筛选条件面板展开时才是 filter_panel。\n"
    "完整商品页是 product_detail；顶部购物车图标只是入口，不能因此判为 cart；只有规格弹窗/半屏规格选择才是 spec_selection。\n"
    "账号与安全、隐私设置、通用设置、消息通知、支付设置等列表页是 settings，不要误判为 product_detail 或 checkout。\n"
    "登录页、短信验证码页、滑块/拼图验证页、人机验证页、扫码登录页都判为 login。\n"
    "严格输出 JSON，不要加额外文字: "
    '{"page_type": "<类型>", "summary": "<≤15字功能概括>"}'
)

# ── Schema-driven prompt generation ──────────────────────────────────────────

_PROMPT_HEADER = (
    "**重要**: 截图已经裁剪掉了顶部状态栏和底部导航栏（首页/购物车/我的等Tab）。\n"
    "你只能看到页面的主内容区域。请仅根据主内容区域判断页面类型，不要猜测被裁剪掉的部分。\n"
)

_ELEMENTS_SECTION = (
    "\n=== elements 字段说明 ===\n"
    "列出页面中可见的关键交互元素，用简短中文命名。只列功能性组件（按钮、输入框、列表、选择器等），\n"
    "不要列纯展示内容（文字、图片）。命名要通用化，不要包含具体商品名或价格。\n"
    '示例: {"search_bar": "顶部搜索栏", "product_cards": "商品卡片列表", "filter_buttons": "筛选排序按钮"}\n\n'
    "=== summary 字段说明 ===\n"
    "用不超过15个字概括页面功能，不要包含具体商品名/品牌名/价格。\n"
    '示例: "商品搜索结果列表" 而非 "OPPO Find X9手机搜索结果页"\n'
)

_FULL_OUTPUT_FORMAT = (
    "\n=== 输出格式 ===\n"
    '严格输出JSON，不要加任何额外文字：\n'
    '{"page_type": "<类型>", "summary": "<≤15字功能概括>", "elements": {"元素名": "简短描述"}}'
)

_OPEN_VOCAB_CLAUSE = (
    '\n如果所有列出的类型都不匹配，输出 "page_type": "new:<英文snake_case名>" 并在 '
    '"new_type_description" 字段给出一句话定义。不要把弹窗/权限/登录页归为 new 类型。'
)

_FAST_OUTPUT_FORMAT = (
    "\n严格输出 JSON，不要加额外文字: "
    '{"page_type": "<类型>", "summary": "<≤15字功能概括>"}'
)


def build_full_prompt(space: "PageTypeSpace", schema: Any) -> str:
    """Generate a full classification prompt from a PageTypeSpace and schema.

    The generated prompt contains every page-type definition line and
    every 判别优先级 rule from the schema's vlm_hints.  For the shopping
    schema this is byte-for-byte equivalent to the legacy constant.
    """
    lines = [
        "你是一个移动应用页面分类器。识别App当前显示的页面类型，并列出页面中的关键交互元素。\n",
        _PROMPT_HEADER,
    ]

    # Collect vlm_hints for disambiguation section
    vlm_hints: list[str] = []
    for name in space.names:
        spec = schema.page_types.get(name)
        if spec and getattr(spec, "vlm_hint", ""):
            vlm_hints.append(spec.vlm_hint)

    if vlm_hints:
        lines.append("\n=== 判别优先级 ===\n")
        # Dedupe while preserving order
        seen_hints: set[str] = set()
        idx = 1
        for hint in vlm_hints:
            if hint not in seen_hints:
                seen_hints.add(hint)
                lines.append(f"{idx}. {hint}\n")
                idx += 1

    lines.append("\n=== 页面类型定义 ===\n")
    for name in space.names:
        spec = schema.page_types.get(name)
        description = (spec and getattr(spec, "description", "")) or name
        lines.append(f"- {name}: {description}\n")

    lines.append(_ELEMENTS_SECTION)
    lines.append(_FULL_OUTPUT_FORMAT)
    lines.append(_OPEN_VOCAB_CLAUSE)

    return "".join(lines)


def build_fast_prompt(space: "PageTypeSpace", schema: Any) -> str:
    """Generate a fast (no elements, no open-vocab) classification prompt.

    Fast mode FORBIDS new: types — must pick from the known list or unknown.
    """
    type_list = ", ".join(space.names)
    lines = [
        "你是移动App页面快速分类器。只判断当前页面类型和一句功能摘要，不要抽取元素。\n",
        _PROMPT_HEADER,
        f"\npage_type 必须是以下之一: {type_list}。\n",
    ]

    # Collect interference / priority hints
    vlm_hints: list[str] = []
    for name in space.names:
        spec = schema.page_types.get(name)
        if spec and getattr(spec, "vlm_hint", ""):
            vlm_hints.append(spec.vlm_hint)

    if vlm_hints:
        seen_hints: set[str] = set()
        for hint in vlm_hints:
            if hint not in seen_hints:
                seen_hints.add(hint)
                lines.append(f"{hint}\n")

    lines.append(_FAST_OUTPUT_FORMAT)
    return "".join(lines)


# Any import needed for type checking
from typing import Any  # noqa: E402 (needed for build_full_prompt/build_fast_prompt signatures at runtime)
