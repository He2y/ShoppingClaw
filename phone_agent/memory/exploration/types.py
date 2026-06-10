"""Shared dataclasses and constant tables for offline exploration."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class ShoppingPageType(Enum):
    """购物场景的页面类型"""
    HOME = "home"
    SEARCH_INPUT = "search_input"
    SEARCH_RESULT = "search_result"
    PRODUCT_DETAIL = "product_detail"
    SPEC_SELECTION = "spec_selection"
    CART = "cart"
    CHECKOUT = "checkout"
    PAYMENT = "payment"
    ADDRESS = "address"
    FILTER_PANEL = "filter_panel"
    CATEGORY = "category"
    MY_ACCOUNT = "my_account"
    SETTINGS = "settings"
    STORE = "store"
    LOGIN = "login"
    DIALOG = "dialog"
    PERMISSION = "permission"
    CAPTCHA = "captcha"
    UNKNOWN = "unknown"


@dataclass
class PageInfo:
    """Page analysis result.

    ``page_type`` is stored as a plain string.  Legacy code that constructs
    ``PageInfo`` with a ``ShoppingPageType`` enum is handled in
    ``__post_init__``: the enum is coerced to its ``.value`` string so that
    all downstream code works uniformly with strings.
    """

    page_type: Any  # str in practice; ShoppingPageType accepted at construction
    semantic_summary: str
    elements: Dict[str, Any]
    screenshot_hash: str
    app: str
    screenshot_base64: str = ""
    width: int = 0
    height: int = 0

    def __post_init__(self) -> None:
        # Coerce Enum → str so callers that still pass ShoppingPageType work.
        if hasattr(self.page_type, "value"):
            self.page_type = self.page_type.value

    def state_key(self) -> str:
        """Generate a stable key for deduplication."""
        pt = self.page_type
        if hasattr(pt, "value"):
            pt = pt.value
        return f"{pt}:{self.semantic_summary[:60]}"


@dataclass
class ExplorationStep:
    """Single exploration step."""
    page_info: PageInfo
    action: Optional[Dict[str, Any]] = None
    action_thinking: str = ""
    timestamp: str = ""


@dataclass
class Trajectory:
    """Exploration trajectory through the app."""
    task: str
    app: str
    steps: List[ExplorationStep] = field(default_factory=list)
    success: bool = True

    def add_step(self, page_info: PageInfo, action: Optional[Dict] = None, thinking: str = ""):
        self.steps.append(ExplorationStep(
            page_info=page_info,
            action=action,
            action_thinking=thinking,
            timestamp=datetime.now().isoformat(),
        ))


@dataclass(frozen=True)
class CoverageTarget:
    """Coverage contract for task-directed offline exploration."""

    page_types: tuple[str, ...] = (
        "home",
        "search_input",
        "search_result",
        "product_detail",
        "spec_selection",
        "cart",
        "checkout",
    )
    transitions: tuple[tuple[str, str], ...] = (
        ("home", "search_input"),
        ("search_input", "search_result"),
        ("search_result", "product_detail"),
        ("product_detail", "spec_selection"),
        ("spec_selection", "product_detail"),
        ("product_detail", "cart"),
        ("cart", "checkout"),
    )


@dataclass(frozen=True)
class CoverageReport:
    """Current coverage of page types and transitions."""

    covered_page_types: tuple[str, ...]
    missing_page_types: tuple[str, ...]
    covered_transitions: tuple[tuple[str, str], ...]
    missing_transitions: tuple[tuple[str, str], ...]

    @property
    def complete(self) -> bool:
        return not self.missing_page_types and not self.missing_transitions

    def to_dict(self) -> dict[str, Any]:
        return {
            "covered_page_types": list(self.covered_page_types),
            "missing_page_types": list(self.missing_page_types),
            "covered_transitions": [list(edge) for edge in self.covered_transitions],
            "missing_transitions": [list(edge) for edge in self.missing_transitions],
            "complete": self.complete,
        }


@dataclass(frozen=True)
class PageTypeSpace:
    """Schema-driven page type space for a given app domain.

    Attributes:
        schema_name: The schema this space was built from.
        names: All known page type names in this space.
        high_risk: Set of page type names classified as high-risk.
        interference: Set of page type names that are interference types.
        summaries: Mapping from name → short Chinese label.
    """

    schema_name: str
    names: tuple[str, ...]
    high_risk: frozenset[str]
    interference: frozenset[str]
    summaries: dict[str, str]

    @classmethod
    def from_schema(cls, schema: Any, extra_types: tuple[str, ...] = ()) -> "PageTypeSpace":
        """Build a PageTypeSpace from a merged MobileSchema."""
        names: list[str] = list(schema.page_types.keys())
        for et in extra_types:
            if et not in names:
                names.append(et)

        high_risk = frozenset(
            name
            for name, spec in schema.page_types.items()
            if getattr(spec, "risk", "normal") == "high"
        )
        interference = frozenset(schema.exploration.interference_page_types)

        # Build summaries: use _PAGE_TYPE_SUMMARY for shopping types, fallback to name
        summaries: dict[str, str] = {}
        for name in names:
            # Check if there's a legacy summary for this type
            enum_val = _PAGE_TYPE_MAP.get(name)
            if enum_val is not None:
                summaries[name] = _PAGE_TYPE_SUMMARY.get(enum_val, name)
            else:
                summaries[name] = name

        return cls(
            schema_name=schema.name,
            names=tuple(names),
            high_risk=high_risk,
            interference=interference,
            summaries=summaries,
        )

    def normalize(self, raw: str) -> str:
        """Normalize a raw page type string.

        Returns:
          - The canonical name if it's a known type.
          - The canonical name if ``raw`` is an alias for a known type.
          - ``raw`` unchanged if it starts with ``new:`` (open-vocab passthrough).
          - ``"unknown"`` otherwise.
        """
        value = (raw or "").strip().lower()
        if not value:
            return "unknown"
        if value in self.names:
            return value
        if value.startswith("new:"):
            return raw  # passthrough
        # Check _PAGE_TYPE_MAP aliases (via the enum)
        if value in _PAGE_TYPE_MAP:
            return value
        return "unknown"


# ── Screen change detection ────────────────────────────────────────────────────
# Compare first N chars of base64 screenshots.
# Two identical screenshots mean the last action had no visible effect.
_SCREEN_CHANGE_HASH_LEN = 2000

# Map JSON strings back to ShoppingPageType enum
_PAGE_TYPE_MAP: Dict[str, ShoppingPageType] = {
    "home": ShoppingPageType.HOME,
    "search_input": ShoppingPageType.SEARCH_INPUT,
    "search_result": ShoppingPageType.SEARCH_RESULT,
    "product_detail": ShoppingPageType.PRODUCT_DETAIL,
    "spec_selection": ShoppingPageType.SPEC_SELECTION,
    "cart": ShoppingPageType.CART,
    "checkout": ShoppingPageType.CHECKOUT,
    "payment": ShoppingPageType.PAYMENT,
    "address": ShoppingPageType.ADDRESS,
    "filter_panel": ShoppingPageType.FILTER_PANEL,
    "category": ShoppingPageType.CATEGORY,
    "my_account": ShoppingPageType.MY_ACCOUNT,
    "settings": ShoppingPageType.SETTINGS,
    "store": ShoppingPageType.STORE,
    "login": ShoppingPageType.LOGIN,
    "dialog": ShoppingPageType.DIALOG,
    "permission": ShoppingPageType.PERMISSION,
    "captcha": ShoppingPageType.CAPTCHA,
    "unknown": ShoppingPageType.UNKNOWN,
}

_PAGE_TYPE_SUMMARY: Dict[ShoppingPageType, str] = {
    ShoppingPageType.HOME: "首页",
    ShoppingPageType.SEARCH_INPUT: "搜索输入页",
    ShoppingPageType.SEARCH_RESULT: "商品搜索结果",
    ShoppingPageType.PRODUCT_DETAIL: "商品详情页",
    ShoppingPageType.SPEC_SELECTION: "规格选择弹窗",
    ShoppingPageType.CART: "购物车列表",
    ShoppingPageType.CHECKOUT: "订单确认页",
    ShoppingPageType.PAYMENT: "支付页",
    ShoppingPageType.ADDRESS: "地址页",
    ShoppingPageType.FILTER_PANEL: "筛选面板",
    ShoppingPageType.CATEGORY: "分类页",
    ShoppingPageType.MY_ACCOUNT: "个人中心",
    ShoppingPageType.SETTINGS: "设置页",
    ShoppingPageType.STORE: "店铺页",
    ShoppingPageType.LOGIN: "登录页",
    ShoppingPageType.DIALOG: "干扰弹窗",
    ShoppingPageType.PERMISSION: "权限弹窗",
    ShoppingPageType.CAPTCHA: "人机验证页",
    ShoppingPageType.UNKNOWN: "未知页面",
}

_HIGH_RISK_PAGE_TYPES = {
    ShoppingPageType.CHECKOUT,
    ShoppingPageType.PAYMENT,
    ShoppingPageType.ADDRESS,
    ShoppingPageType.LOGIN,
    ShoppingPageType.PERMISSION,
}

_UNSAFE_ACTION_TOKENS = (
    "支付",
    "付款",
    "立即支付",
    "提交订单",
    "确认订单",
    "下单",
    "结算",
    "去结算",
    "pay",
    "payment",
    "submit",
    "checkout",
    "buy now",
    "logout",
    "log out",
    "switch account",
    "退出登录",
    "切换账号",
    "注销账号",
)

_SPEC_TRIGGER_TOKENS = (
    "加入购物车",
    "立即购买",
    "领券购买",
    "去购买",
    "购买按钮",
    "买贵必赔",
    "购买",
    "规格",
    "颜色",
    "版本",
)
