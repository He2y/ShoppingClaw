"""
Session Product Memory — session-scoped structured memory for shopping tasks.

Provides real-time tracking of products, prices, specs, and progress summaries
within a single task session. Injecting structured context into the VLM execution
loop to prevent progress confusion and memory degradation in long-horizon tasks.

Inspired by UI-Copilot's memory decoupling principle, adapted as an inference-time
component of ClawGUI-Agent's dual-core memory engine.
"""

from dataclasses import dataclass, field
from datetime import datetime

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class ProductInfo:
    """A product observed during the current shopping session."""

    name: str
    price: float | None = None
    specs: dict[str, str] = field(default_factory=dict)  # {"颜色": "黑色", "尺码": "42"}
    source_page: str = ""  # "search_result" / "product_detail" / "cart" / "checkout"
    status: str = "viewed"  # "viewed" / "added_to_cart" / "compared" / "purchased"
    first_seen_step: int = 0


@dataclass
class StepSummary:
    """A single-step progress summary."""

    step: int
    summary: str
    action_type: str
    page_type: str = ""
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()


@dataclass
class SessionMemory:
    """
    Session-scoped structured memory for the current task.

    Lifecycle:
        start_task()  → reset()
        add_step()    → update_from_thinking() + add_step_summary()
        Phase ⑥       → get_context_for_injection()
        end_task()    → serialized into MemoryStore (TASK_HISTORY)
    """

    task: str = ""
    platform: str = ""  # "京东" / "淘宝" / "拼多多" / ...
    current_product: ProductInfo | None = None
    viewed_products: list[ProductInfo] = field(default_factory=list)
    cart_items: list[ProductInfo] = field(default_factory=list)
    completed_steps: list[StepSummary] = field(default_factory=list)
    constraints: dict[str, str] = field(default_factory=dict)  # {"budget": "500", ...}
    # Track action repetition for stagnation detection
    _consecutive_same_action: int = 0
    _last_action_type: str = ""
    _last_page_type: str = ""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Clear all session state for a new task."""
        self.task = ""
        self.platform = ""
        self.current_product = None
        self.viewed_products.clear()
        self.cart_items.clear()
        self.completed_steps.clear()
        self.constraints.clear()
        self._consecutive_same_action = 0
        self._last_action_type = ""
        self._last_page_type = ""

    # ------------------------------------------------------------------
    # Product tracking
    # ------------------------------------------------------------------

    def add_or_update_product(
        self, product: ProductInfo, page_type: str = ""
    ) -> None:
        """Add a new product or update existing one if name matches."""
        if not product.name:
            return
        product.source_page = page_type or product.source_page
        # Check if this product already exists in viewed list
        for existing in self.viewed_products:
            if _product_name_match(existing.name, product.name):
                # Update existing entry
                if product.price is not None:
                    existing.price = product.price
                existing.specs.update(product.specs)
                existing.source_page = product.source_page or existing.source_page
                existing.status = product.status or existing.status
                return
        # New product
        if not product.first_seen_step and self.completed_steps:
            product.first_seen_step = len(self.completed_steps)
        self.viewed_products.append(product)

    def set_current_product(self, product: ProductInfo) -> None:
        """Set the product currently being viewed."""
        self.current_product = product
        if product.name:
            self.add_or_update_product(product, product.source_page)

    def add_to_cart(self, product: ProductInfo) -> None:
        """Mark a product as added to cart."""
        product.status = "added_to_cart"
        # Update in viewed list
        for existing in self.viewed_products:
            if _product_name_match(existing.name, product.name):
                existing.status = "added_to_cart"
        # Add to cart items if not already there
        if not any(_product_name_match(c.name, product.name) for c in self.cart_items):
            self.cart_items.append(product)

    def get_product_by_name(self, name: str) -> ProductInfo | None:
        """Find a product by name (fuzzy match)."""
        for p in self.viewed_products:
            if _product_name_match(p.name, name):
                return p
        return None

    # ------------------------------------------------------------------
    # Step tracking + stagnation detection
    # ------------------------------------------------------------------

    def add_step_summary(
        self,
        summary: str,
        action_type: str = "",
        page_type: str = "",
    ) -> None:
        """Record a completed step summary and update stagnation counters."""
        step_num = len(self.completed_steps) + 1
        self.completed_steps.append(
            StepSummary(
                step=step_num,
                summary=summary,
                action_type=action_type,
                page_type=page_type,
            )
        )
        # Update stagnation detection
        if action_type == self._last_action_type and page_type == self._last_page_type:
            self._consecutive_same_action += 1
        else:
            self._consecutive_same_action = 1
        self._last_action_type = action_type
        self._last_page_type = page_type

    def is_stagnating(self) -> bool:
        """True if agent appears stuck (same action on same page repeatedly)."""
        return self._consecutive_same_action >= 2

    def should_trigger_detailed_injection(self, thinking: str = "") -> bool:
        """Check if detailed memory should be injected into context."""
        # Signal 1: thinking contains uncertainty keywords
        uncertainty_keywords = [
            "不确定", "忘记了", "之前看到", "那个商品", "价格是多少",
            "哪个", "记得", "我不确定", "记不清", "刚刚看", "前面看到",
            "not sure", "forgot", "remember", "which one",
        ]
        if thinking and any(kw.lower() in thinking.lower() for kw in uncertainty_keywords):
            return True
        # Signal 2: stagnation detected
        if self.is_stagnating():
            return True
        return False

    def should_compress(self) -> bool:
        """True every 5 steps — trigger VLM history compression."""
        return len(self.completed_steps) > 0 and len(self.completed_steps) % 5 == 0

    # ------------------------------------------------------------------
    # Context generation for VLM injection
    # ------------------------------------------------------------------

    def get_context_for_injection(self, detail_level: str = "summary") -> str:
        """
        Generate context string for injection into VLM prompt.

        Args:
            detail_level: "summary" (lightweight, always injected) or
                          "detailed" (full product memory, triggered injection)
        """
        if detail_level == "summary":
            return self._build_summary_context()
        else:
            return self._build_detailed_context()

    def _build_summary_context(self) -> str:
        """Build lightweight progress summary for every-step injection."""
        parts: list[str] = []

        # Task line
        if self.task:
            parts.append(f"任务: {self.task}")
        if self.platform:
            parts.append(f"平台: {self.platform}")

        # Current focus
        if self.current_product:
            p = self.current_product
            price_str = f"¥{p.price}" if p.price else "价格未知"
            specs_str = ", ".join(f"{k}={v}" for k, v in p.specs.items())
            focus = f"当前商品: {p.name} ({price_str}"
            if specs_str:
                focus += f", {specs_str}"
            focus += ")"
            parts.append(focus)

        # Cart summary
        if self.cart_items:
            cart_parts = [
                f"{c.name} ¥{c.price}" if c.price else c.name
                for c in self.cart_items
            ]
            parts.append(f"购物车: {', '.join(cart_parts)}")

        # Progress: last 3 steps max
        if self.completed_steps:
            recent = self.completed_steps[-3:]
            steps_text = " → ".join(s.summary for s in recent)
            parts.append(f"最近步骤: {steps_text}")

        return "\n".join(f"[📋 {line}]" for line in parts)

    def _build_detailed_context(self) -> str:
        """Build detailed product memory for triggered injection."""
        parts: list[str] = ["[🔍 会话详细记忆]"]

        if self.task:
            parts.append(f"当前任务: {self.task}")

        # All viewed products
        if self.viewed_products:
            parts.append("已浏览商品:")
            for p in self.viewed_products:
                price_str = f"¥{p.price}" if p.price else "?"
                specs_str = ", ".join(f"{k}={v}" for k, v in p.specs.items())
                status_str = {"viewed": "已看", "added_to_cart": "已加购", "compared": "已对比"}.get(
                    p.status, p.status
                )
                line = f"  - {p.name} [{price_str}]"
                if specs_str:
                    line += f" ({specs_str})"
                line += f" — {status_str} (Step {p.first_seen_step})"
                parts.append(line)

        # Cart
        if self.cart_items:
            parts.append("购物车:")
            for c in self.cart_items:
                parts.append(f"  - {c.name} ¥{c.price if c.price else '?'}")

        # Constraints
        if self.constraints:
            parts.append("用户约束:")
            for k, v in self.constraints.items():
                parts.append(f"  - {k}: {v}")

        # Full step history (compressed entries)
        if self.completed_steps:
            parts.append("完整步骤记录:")
            for s in self.completed_steps:
                parts.append(f"  {s.summary}")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Serialization for tracer / analysis
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialize to dict for tracer and offline analysis."""
        return {
            "task": self.task,
            "platform": self.platform,
            "current_product": _product_to_dict(self.current_product) if self.current_product else None,
            "viewed_products": [_product_to_dict(p) for p in self.viewed_products],
            "cart_items": [_product_to_dict(p) for p in self.cart_items],
            "completed_steps": [
                {
                    "step": s.step,
                    "summary": s.summary,
                    "action_type": s.action_type,
                    "page_type": s.page_type,
                    "timestamp": s.timestamp,
                }
                for s in self.completed_steps
            ],
            "constraints": dict(self.constraints),
            "statistics": {
                "total_products_viewed": len(self.viewed_products),
                "total_cart_items": len(self.cart_items),
                "total_steps": len(self.completed_steps),
                "stagnations": self._consecutive_same_action,
            },
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _product_name_match(a: str, b: str) -> bool:
    """Fuzzy product name match — case-insensitive, whitespace-normalized."""
    a_norm = "".join(a.lower().split())
    b_norm = "".join(b.lower().split())
    if a_norm == b_norm:
        return True
    # One contains the other (for partial matches like "Nike Fly.By" vs "Nike Fly.By Mid 3")
    if len(a_norm) > 4 and len(b_norm) > 4:
        if a_norm in b_norm or b_norm in a_norm:
            return True
    return False


def _product_to_dict(p: ProductInfo) -> dict:
    return {
        "name": p.name,
        "price": p.price,
        "specs": dict(p.specs),
        "source_page": p.source_page,
        "status": p.status,
        "first_seen_step": p.first_seen_step,
    }
