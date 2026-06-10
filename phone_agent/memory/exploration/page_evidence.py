"""Page-type inference from VLM reasoning text (evidence-token based)."""

from __future__ import annotations

# These functions return str page type names (or None), replacing the legacy
# enum-returning variants in explorer.py.

_EXCLUDED_MARKERS = (
    "classifier hypothesis",
    "verifier note",
    "raw post-action",
    "核心空间骨架",
    "用户要求",
    "任务要求",
    "system-reminder",
)

_VISUAL_MARKERS = (
    "当前截图",
    "当前页面是",
    "当前状态",
    "当前已经",
    "从截图",
    "截图显示",
    "屏幕显示",
    "我可以看到",
    "我看到",
    "看起来",
    "当前界面显示",
    "现在看到",
    "显示的是",
)


def infer_page_type_from_reasoning(text: str) -> str | None:
    """Infer a weak page belief from the action model's current-screen reasoning.

    Returns a page type string (e.g. "home") or None if no match.
    """
    if not text:
        return None

    lines = []
    capturing_visual_context = False
    for raw_line in text.splitlines():
        line = raw_line.strip().lower()
        if not line or any(marker in line for marker in _EXCLUDED_MARKERS):
            continue
        if any(marker in line for marker in _VISUAL_MARKERS):
            capturing_visual_context = True
        if capturing_visual_context:
            lines.append(line)
        if len(lines) >= 12:
            break
    if not lines:
        return None

    for scoped in ["\n".join(lines), *lines[:8]]:
        if "权限" in scoped or "permission" in scoped:
            return "permission"
        if is_settings_page_evidence(scoped):
            return "settings"
        if any(token in scoped for token in ("支付页", "付款页面", "收银台", "支付密码", "付款方式", "payment page")):
            return "payment"
        if any(token in scoped for token in ("地址", "address")):
            return "address"
        if is_login_page_evidence(scoped):
            return "login"
        if any(token in scoped for token in ("订单确认", "确认订单", "checkout")):
            return "checkout"
        if any(
            token in scoped
            for token in ("规格选择", "规格弹窗", "适用手机型号", "颜色分类", "型号选项", "sku")
        ):
            return "spec_selection"
        if any(token in scoped for token in ("弹窗", "优惠券", "广告", "活动面板", "dialog")):
            return "dialog"
        if any(token in scoped for token in ("搜索输入", "搜索建议", "历史搜索", "猜你想搜", "键盘", "search_input")):
            return "search_input"
        if any(token in scoped for token in ("商品详情", "详情页", "product_detail")):
            return "product_detail"
        if any(token in scoped for token in ("规格", "spec_selection")) and any(
            token in scoped for token in ("弹窗", "半屏", "选择", "选项")
        ):
            return "spec_selection"
        if any(token in scoped for token in ("筛选面板", "筛选条件", "filter_panel")):
            return "filter_panel"
        if is_cart_page_evidence(scoped):
            return "cart"
        if any(token in scoped for token in ("搜索结果", "结果页面", "商品列表", "search_result")):
            return "search_result"
        if any(token in scoped for token in ("首页", "home")):
            return "home"
    return None


def is_settings_page_evidence(text: str) -> bool:
    """Return True if the text contains strong settings page evidence."""
    settings_tokens = (
        "设置页",
        "设置页面",
        "账号与安全",
        "隐私设置",
        "通用设置",
        "消息通知",
        "支付设置",
        "国家与地区",
        "切换账号",
        "退出登录",
        "settings page",
    )
    return any(token.lower() in text for token in settings_tokens)


def is_login_page_evidence(text: str) -> bool:
    """Return True if the text contains strong login page evidence."""
    login_page_tokens = (
        "登录页",
        "登录页面",
        "手机号输入",
        "密码输入",
        "验证码",
        "login page",
    )
    return any(token.lower() in text for token in login_page_tokens) or (
        "登录按钮" in text and ("手机号" in text or "验证码" in text or "密码" in text)
    )


def is_cart_page_evidence(text: str) -> bool:
    """Return True only when the text describes the cart page itself.

    Product detail pages often expose a top-right cart entry — that should
    remain product_detail; cart requires page-level evidence such as item
    checkboxes, all-select, or checkout controls.
    """
    cart_page_tokens = (
        "购物车页面",
        "购物车页",
        "购物车列表",
        "我的购物车",
        "购物车中",
        "cart page",
        "cart list",
    )
    cart_control_tokens = (
        "全选",
        "去结算",
        "结算按钮",
        "编辑/管理",
        "管理按钮",
        "商品复选框",
        "checkbox",
        "checkout button",
    )
    return any(token in text for token in cart_page_tokens) or (
        "购物车" in text and any(token in text for token in cart_control_tokens)
    )
