"""
Unified task slot extraction for ClawGUI-Agent.

Single source of truth for extracting structured slots (color, storage, size,
query, product, price, contact, app) from task text.  Consumed by:

- ClarificationAgent  (pre-execution gap detection)
- SpecGuard            (purchase safety)
- GoalSpec             (spatial graph routing)
- MemoryManager        (preference extraction)
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Canonical pattern registry — merged from agent.py, spatial_graph_memory.py,
# and memory_manager.py
# ---------------------------------------------------------------------------

# Colors — superset from agent.py (24 entries)
COLOR_VALUES: frozenset[str] = frozenset({
    "银色", "蓝色", "黑色", "白色", "红色", "金色", "绿色", "紫色", "灰色",
    "粉色", "橙色", "黄色", "棕色", "深空黑色", "星光色", "午夜色", "远峰蓝",
    "苍岭绿", "暗紫色", "石墨色", "亮黑色", "土豪金", "玫瑰金", "深空灰",
})

# Storage patterns — from agent.py (supports TB/GB/G)
STORAGE_PATTERNS: tuple[str, ...] = (
    r"(\d+\s*(?:TB?|GB?))",
)

# Clothing sizes — from agent.py
SIZE_VALUES: frozenset[str] = frozenset({
    "S", "M", "L", "XL", "XXL", "XXXL", "均码", "大码", "小码",
})

# Bidirectional spec key normalization — from agent.py
SPEC_KEY_ALIASES: dict[str, str] = {
    "color": "颜色",
    "colour": "颜色",
    "颜色": "颜色",
    "机身颜色": "颜色",
    "storage": "容量",
    "capacity": "容量",
    "memory": "容量",
    "容量": "容量",
    "存储容量": "容量",
    "size": "尺码",
    "尺码": "尺码",
    "尺寸": "尺码",
}

# Reverse mapping: Chinese canonical → English key
_CN_TO_EN: dict[str, str] = {"颜色": "color", "容量": "storage", "尺码": "size"}

# Query / product / price patterns — from spatial_graph_memory.py
_QUERY_PATTERNS: tuple[str, ...] = (
    r"query[:=]\s*([^,;]+)",
    r"搜索[：:]\s*([^,;，。]+)",
    r"搜索\s*([^\s,;，。；并到]{1,30})",
    r"买(?:一个|一台|一部|一款|一件|个)?\s*([^,;，。；]+?)(?:[，。；]|加入|提交|下单|购买|去|$)",
    r"(?:找|搜)(?:一下|一搜)?\s*([^,;，。；]{1,40})",
    r"购买\s*([^,;，。；]{1,40})",
)

_PRODUCT_PATTERNS: tuple[str, ...] = (
    r"product[:=]\s*([^,;]+)",
    r"商品[：:]\s*([^,;，。]+)",
    r"买(?:一个|一台|一部|一款|一件|个)?\s*([^,;，。；]+)",
)

_PRICE_PATTERNS: tuple[str, ...] = (
    r"(?:¥|￥)\s*([0-9]+(?:\.[0-9]+)?)",
)

# Contact patterns — from memory_manager.py
_CONTACT_PATTERNS: tuple[str, ...] = (
    r'(?:给|发送?给?|联系|打电话给?|发消息给?)\s*[「『""]?([\u4e00-\u9fa5a-zA-Z]{2,8})[」』""]?(?:发|说|打|$)',
    r'(?:联系人|好友|朋友)\s*[「『""]?([\u4e00-\u9fa5a-zA-Z]{2,8})[」』""]?',
    r"(?:to|contact|call|message)\s+([a-zA-Z\u4e00-\u9fa5]{2,15})(?:\s|$)",
)

# App patterns — from memory_manager.py
_APP_PATTERNS: tuple[str, ...] = (
    r"(?:打开|启动|使用|进入)\s*([\u4e00-\u9fa5a-zA-Z]+)",
    r"(?:open|launch|use)\s+([a-zA-Z\u4e00-\u9fa5]+)",
)

# Known apps — from memory_manager.py
KNOWN_APPS: frozenset[str] = frozenset({
    "微信", "wechat", "支付宝", "alipay", "淘宝", "taobao", "抖音", "tiktok",
    "美团", "meituan", "饿了么", "eleme", "京东", "jd", "拼多多", "pinduoduo",
    "高德地图", "amap", "百度地图", "baidu maps", "微博", "weibo", "qq",
    "钉钉", "dingtalk", "飞书", "feishu", "网易云音乐", "netease music",
    "spotify", "bilibili", "b站", "小红书", "xiaohongshu", "safari", "chrome",
    "设置", "settings", "相机", "camera", "相册", "photos", "备忘录", "notes",
})

# Shopping-related keywords for domain detection
_SHOPPING_KEYWORDS: tuple[str, ...] = (
    "买", "购买", "下单", "加入购物车", "加购", "购物",
    "淘宝", "京东", "拼多多", "天猫", "闲鱼",
    "taobao", "jd", "pinduoduo", "tmall",
)

_FOOD_KEYWORDS: tuple[str, ...] = (
    "外卖", "点餐", "点单", "美团", "饿了么", "meituan", "eleme",
    "奶茶", "咖啡", "lunch", "dinner",
)


# ---------------------------------------------------------------------------
# TaskSlots — canonical extraction result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TaskSlots:
    """Canonical structured slots extracted from a task string."""

    query: str = ""
    product: str = ""
    price: str = ""
    color: str = ""
    storage: str = ""
    size: str = ""
    contact: str = ""
    app: str = ""
    domain: str = "general"

    @property
    def spec_dict(self) -> dict[str, str]:
        """Chinese-keyed spec dict for SpecGuard compatibility.

        Example: {"颜色": "银色", "容量": "512G"}
        """
        result: dict[str, str] = {}
        if self.color:
            result["颜色"] = self.color
        if self.storage:
            result["容量"] = self.storage
        if self.size:
            result["尺码"] = self.size
        return result

    @property
    def slot_dict(self) -> dict[str, str]:
        """English-keyed slot dict for GoalSpec / SpatialGraphMemory.

        Example: {"query": "iPhone 17", "color": "银色", "storage": "512G"}
        """
        result: dict[str, str] = {}
        for key in ("query", "product", "price", "color", "storage", "size",
                     "contact", "app"):
            value = getattr(self, key)
            if value:
                result[key] = value
        return result

    @property
    def has_shopping_specs(self) -> bool:
        """Whether any shopping-relevant spec is present."""
        return bool(self.color or self.storage or self.size)

    @property
    def missing_shopping_specs(self) -> list[str]:
        """Chinese display names of shopping specs NOT present in task.

        Only meaningful when domain is shopping — returns all three spec
        categories so the caller can decide which matter for the product.
        """
        missing: list[str] = []
        if not self.color:
            missing.append("颜色")
        if not self.storage:
            missing.append("容量")
        if not self.size:
            missing.append("尺码")
        return missing

    @property
    def is_shopping(self) -> bool:
        return self.domain in ("shopping", "food_delivery")


# ---------------------------------------------------------------------------
# TaskSpecExtractor — stateless extraction logic
# ---------------------------------------------------------------------------

class TaskSpecExtractor:
    """Single source of truth for extracting structured slots from task text.

    All methods are static/classmethod — no instance state needed.
    """

    @staticmethod
    def extract(task: str) -> TaskSlots:
        """Extract all slots from a task string.

        Returns an immutable ``TaskSlots`` with every recognized field populated.
        """
        color = _extract_color(task)
        storage = _extract_storage(task)
        size = _extract_size(task)
        query = _first_match(_QUERY_PATTERNS, task)
        product = _first_match(_PRODUCT_PATTERNS, task)
        price = _first_match(_PRICE_PATTERNS, task)
        contact = _first_match(_CONTACT_PATTERNS, task)
        app = _extract_app(task)
        domain = _detect_domain(task)

        return TaskSlots(
            query=query,
            product=product,
            price=price,
            color=color,
            storage=storage,
            size=size,
            contact=contact,
            app=app,
            domain=domain,
        )

    @staticmethod
    def normalize_spec_key(key: str) -> str:
        """Normalize a spec key to its Chinese canonical form.

        Examples: "color" → "颜色", "capacity" → "容量", "尺寸" → "尺码"
        """
        return SPEC_KEY_ALIASES.get(key.lower().strip(), key)

    @staticmethod
    def spec_key_to_english(cn_key: str) -> str:
        """Chinese canonical key → English key.

        "颜色" → "color", "容量" → "storage", "尺码" → "size"
        """
        return _CN_TO_EN.get(cn_key, cn_key)

    @staticmethod
    def detect_domain(task: str) -> str:
        """Classify task domain: 'shopping' / 'food_delivery' / 'general'."""
        return _detect_domain(task)

    @staticmethod
    def format_specs_cn(specs: dict[str, str]) -> str:
        """Format spec dict as human-readable Chinese string.

        {"颜色": "银色", "容量": "512G"} → "颜色=银色, 容量=512G"
        """
        if not specs:
            return ""
        return ", ".join(f"{k}={v}" for k, v in specs.items())


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _first_match(patterns: tuple[str, ...], text: str) -> str:
    """Return the first regex capture group match across *patterns*."""
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return ""


def _extract_color(task: str) -> str:
    """Extract color from task, preferring longest match."""
    for color in sorted(COLOR_VALUES, key=len, reverse=True):
        if color in task:
            return color
    return ""


def _extract_storage(task: str) -> str:
    """Extract storage/capacity spec from task."""
    for pattern in STORAGE_PATTERNS:
        m = re.search(pattern, task, re.IGNORECASE)
        if m:
            return m.group(1).upper().replace(" ", "")
    return ""


def _extract_size(task: str) -> str:
    """Extract clothing size from task."""
    for size in sorted(SIZE_VALUES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(size)}\b", task):
            return size
    return ""


def _extract_app(task: str) -> str:
    """Extract app name from task text."""
    for pattern in _APP_PATTERNS:
        m = re.search(pattern, task, re.IGNORECASE)
        if m:
            # The captured group is the app name
            candidate = m.group(1).strip()
            if candidate.lower() in {a.lower() for a in KNOWN_APPS}:
                return candidate
    return ""


def _detect_domain(task: str) -> str:
    """Classify task domain."""
    task_lower = task.lower()
    if any(kw in task_lower for kw in _FOOD_KEYWORDS):
        return "food_delivery"
    if any(kw in task_lower for kw in _SHOPPING_KEYWORDS):
        return "shopping"
    return "general"
