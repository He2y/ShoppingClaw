"""
RetrievalGateway — on-demand memory retrieval engine.

Translates UI-Copilot's "copilot as Retriever" paradigm to inference-time.
Instead of requiring a fine-tuned model that outputs <tool>Retriever</tool>,
we use heuristic intent detection + structured knowledge base queries.

Two trigger paths:
  1. Implicit: parse VLM thinking for uncertainty/confusion signals
  2. Explicit: stagnation detection (same action on same page repeatedly)

When triggered, queries KnowledgeBase and returns formatted context
for injection into the next VLM call.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .knowledge_base import KnowledgeBase, ProductObservation


@dataclass
class RetrievalResult:
    """Result of an on-demand retrieval query."""

    triggered: bool
    intent: str = ""
    context_text: str = ""
    products_found: list = field(default_factory=list)
    source: str = ""

    def __bool__(self) -> bool:
        return self.triggered


class RetrievalGateway:
    """
    On-demand retrieval engine — inference-time equivalent of UI-Copilot's
    Retriever tool. Monitors agent thinking for signals of memory degradation
    or progress confusion, and queries KnowledgeBase when triggered.
    """

    UNCERTAINTY_SIGNALS: list[str] = [
        "不确定", "忘记了", "之前看到", "那个商品", "价格是多少",
        "哪个", "我不确定", "记不清", "刚刚看", "前面看到",
        "not sure", "forgot", "remember", "which one",
    ]
    COMPARISON_SIGNALS: list[str] = [
        "对比", "比较", "哪个更", "性价比", "哪个便宜", "哪个贵",
        "选哪个", "纠结", "compare",
    ]
    CALCULATION_SIGNALS: list[str] = [
        "总共", "合计", "一共", "加起来", "总价", "多少钱",
        "total", "sum", "multiply", "add up",
    ]
    LOOKUP_SIGNALS: list[str] = [
        "价格是多少", "什么颜色", "什么尺码", "什么规格",
        "那个商品", "之前看过", "前面那个",
    ]

    def __init__(self, knowledge_base: "KnowledgeBase"):
        self.kb = knowledge_base
        self._last_retrieval_step: int = -1
        self._retrieval_cooldown: int = 3

    def reset(self) -> None:
        self._last_retrieval_step = -1

    def check_and_retrieve(self, thinking: str, current_step: int) -> RetrievalResult:
        """Analyze thinking for retrieval signals. Returns RetrievalResult."""
        if current_step - self._last_retrieval_step < self._retrieval_cooldown:
            return RetrievalResult(triggered=False)

        if self.kb.is_stagnating():
            return self._do_retrieve("recall", thinking, current_step, source="stagnation")

        thinking_lower = thinking.lower()

        for kw in self.LOOKUP_SIGNALS:
            if kw in thinking_lower:
                return self._do_retrieve("product_lookup", thinking, current_step)

        for kw in self.COMPARISON_SIGNALS:
            if kw in thinking_lower:
                return self._do_retrieve("price_compare", thinking, current_step)

        for kw in self.CALCULATION_SIGNALS:
            if kw in thinking_lower:
                return self._do_retrieve("calculate", thinking, current_step)

        for kw in self.UNCERTAINTY_SIGNALS:
            if kw in thinking_lower:
                return self._do_retrieve("recall", thinking, current_step)

        return RetrievalResult(triggered=False)

    def _do_retrieve(
        self, intent: str, thinking: str, step: int, source: str = "implicit",
    ) -> RetrievalResult:
        self._last_retrieval_step = step
        keywords = self.kb._extract_product_keywords(thinking)

        if intent == "product_lookup":
            context = self._build_product_context(keywords)
        elif intent == "price_compare":
            context = self._build_comparison_context()
        elif intent == "calculate":
            context = self._build_calculation_context()
        else:
            context = self._build_recall_context(keywords)

        return RetrievalResult(
            triggered=True, intent=intent,
            context_text=context,
            products_found=self.kb.products,
            source=source,
        )

    # ------------------------------------------------------------------
    # Context builders
    # ------------------------------------------------------------------

    def _build_product_context(self, keywords: list[str]) -> str:
        parts: list[str] = ["[记忆检索] 已浏览的商品信息:"]
        found = False
        if keywords:
            text = self.kb.retrieve_by_keywords(keywords)
            if text:
                parts.append(text)
                found = True
        if not found:
            all_text = self.kb.retrieve_all_products_text()
            if all_text:
                parts.append(all_text)
            else:
                parts.append("  (暂无商品记录)")
        if self.kb.constraints:
            parts.append("用户约束:")
            for k, v in self.kb.constraints.items():
                parts.append(f"  - {k}: {v}")
        return "\n".join(parts)

    def _build_comparison_context(self) -> str:
        products = self.kb.products
        if len(products) < 2:
            return "[记忆检索] 目前仅浏览了1个商品，无法进行对比。"

        parts: list[str] = ["[记忆检索] 已浏览商品对比:"]
        parts.append(f"{'商品名':<25} {'价格':>10}  {'规格'}")
        parts.append("-" * 50)
        for p in products:
            price = f"¥{p.price}" if p.price else "?"
            specs = ", ".join(f"{k}={v}" for k, v in p.specs.items()) or "-"
            parts.append(f"{p.name:<25} {price:>10}  {specs}")

        priced = [p for p in products if p.price]
        if priced:
            cheapest = min(priced, key=lambda x: x.price)
            parts.append(f"\n最低价: {cheapest.name} (¥{cheapest.price})")
        return "\n".join(parts)

    def _build_calculation_context(self) -> str:
        cart_items = [p for p in self.kb.products if p.status == "added_to_cart"]
        if not cart_items:
            return "[记忆检索] 购物车为空，无法计算。"

        parts: list[str] = ["[记忆检索] 购物车商品:"]
        total = 0.0
        for p in cart_items:
            price = p.price or 0
            total += price
            parts.append(f"  - {p.name}: ¥{price}")
        parts.append(f"  合计: ¥{total}")

        all_priced = [p for p in self.kb.products if p.price]
        if all_priced:
            parts.append("\n所有已浏览商品价格:")
            for p in all_priced:
                parts.append(f"  - {p.name}: ¥{p.price}")
        return "\n".join(parts)

    def _build_recall_context(self, keywords: list[str]) -> str:
        parts: list[str] = ["[记忆检索] 最近操作摘要:"]
        recent_steps = self.kb.steps[-8:]
        if recent_steps:
            for r in recent_steps:
                target = f" → {r.action_target}" if r.action_target else ""
                parts.append(f"  Step {r.step}: {r.action_type}{target}")

        if keywords:
            recall = self.kb.retrieve_by_keywords(keywords)
            if recall:
                parts.append("\n相关信息:")
                parts.append(recall)

        if self.kb.constraints:
            parts.append("\n用户约束:")
            for k, v in self.kb.constraints.items():
                parts.append(f"  - {k}: {v}")
        return "\n".join(parts)
