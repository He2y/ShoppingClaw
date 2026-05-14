"""
KnowledgeBase — external session knowledge store with memory decoupling.

Core insight from UI-Copilot (Lu et al., 2026):
  - Detailed observations live OUTSIDE the VLM context window
  - Only concise progress summaries stay in dialogue history
  - Retrieval happens on-demand, not push-injected every step

This replaces the old "always-inject everything" pattern with a
queryable external store that the RetrievalGateway can search when
the agent shows signs of memory degradation or progress confusion.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class ProductObservation:
    """A product observed at a specific step."""

    name: str
    price: float | None = None
    currency: str = "¥"
    specs: dict[str, str] = field(default_factory=dict)
    source_page: str = ""          # "search_result" / "product_detail" / "cart"
    status: str = "viewed"         # "viewed" / "compared" / "added_to_cart"
    step: int = 0
    screenshot_desc: str = ""      # brief visual description for retrieval context


@dataclass
class StepRecord:
    """A single step's essential data, stored externally."""

    step: int
    action_type: str
    action_target: str = ""
    thinking_short: str = ""       # first 1-2 sentences of reasoning
    thinking_full: str = ""        # complete reasoning (stored, NOT in context)
    page_type: str = ""
    app: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class ProgressTracker:
    """Tracks high-level task progress with sub-task decomposition."""

    task: str = ""
    platform: str = ""
    subtasks_completed: list[str] = field(default_factory=list)
    subtasks_remaining: list[str] = field(default_factory=list)
    current_subtask: str = ""
    overall_progress: str = ""     # one-line summary, injected into context

    def mark_subtask_done(self, subtask: str) -> None:
        if subtask in self.subtasks_remaining:
            self.subtasks_remaining.remove(subtask)
        if subtask not in self.subtasks_completed:
            self.subtasks_completed.append(subtask)

    def summary_line(self) -> str:
        remaining = self.subtasks_remaining
        if remaining:
            done_count = len(self.subtasks_completed)
            done = " → ".join(self.subtasks_completed[-3:]) if self.subtasks_completed else "开始"
            return f"[Step {done_count}] {done} | 下一步: {remaining[0]}"
        done_count = len(self.subtasks_completed)
        done = " → ".join(self.subtasks_completed[-3:]) if self.subtasks_completed else "执行中"
        return f"[Step {done_count}] {done}"


# ---------------------------------------------------------------------------
# KnowledgeBase
# ---------------------------------------------------------------------------

class KnowledgeBase:
    """
    External session knowledge — the "K" file from UI-Copilot's paradigm.

    Stores ALL detailed observations from the session. The VLM context
    only gets:
      - progress_summary() — 1-2 lines
      - current_focus() — what product/page we're on
      - retrieve() — on-demand results triggered by confusion signals

    Lifecycle:
        reset()           → new task
        record_step()     → called after each VLM action
        progress_summary()→ injected into every VLM context (lightweight)
        retrieve()        → on-demand search when agent is lost
    """

    def __init__(self):
        self.task: str = ""
        self.platform: str = ""

        # Core storage
        self.products: list[ProductObservation] = []
        self.steps: list[StepRecord] = []
        self.constraints: dict[str, str] = {}

        # Progress
        self.progress = ProgressTracker()

        # Current focus (lightweight, injected every step)
        self._current_product: ProductObservation | None = None
        self._current_page: str = ""
        self._current_app: str = ""

        # Stagnation detection
        self._last_action_type: str = ""
        self._last_page_type: str = ""
        self._consecutive_same: int = 0

        # Full reasoning archive (pure text, for RetrievalGateway)
        self._reasoning_archive: list[dict[str, str]] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def reset(self, task: str = "", platform: str = "") -> None:
        self.task = task
        self.platform = platform
        self.products.clear()
        self.steps.clear()
        self.constraints.clear()
        self.progress = ProgressTracker(task=task, platform=platform)
        self._current_product = None
        self._current_page = ""
        self._current_app = ""
        self._last_action_type = ""
        self._last_page_type = ""
        self._consecutive_same = 0
        self._reasoning_archive.clear()

    # ------------------------------------------------------------------
    # Recording (write path)
    # ------------------------------------------------------------------

    def record_step(
        self,
        step: int,
        action_type: str,
        action_target: str = "",
        thinking_full: str = "",
        thinking_short: str = "",
        page_type: str = "",
        app: str = "",
    ) -> None:
        """Record step data. thinking_full is archived; thinking_short stays."""
        record = StepRecord(
            step=step,
            action_type=action_type,
            action_target=action_target,
            thinking_short=thinking_short or self._truncate_thinking(thinking_full),
            thinking_full=thinking_full,
            page_type=page_type,
            app=app,
        )
        self.steps.append(record)

        # Archive full reasoning for retrieval
        if thinking_full:
            self._reasoning_archive.append({
                "step": str(step),
                "thinking": thinking_full,
            })

        # Update stagnation detection
        if action_type == self._last_action_type and page_type == self._last_page_type:
            self._consecutive_same += 1
        else:
            self._consecutive_same = 1
        self._last_action_type = action_type
        self._last_page_type = page_type

        # Auto-update progress
        self._infer_progress(action_type, action_target, thinking_full)

    def record_product(
        self,
        name: str,
        price: float | None = None,
        specs: dict[str, str] | None = None,
        page_type: str = "",
        status: str = "viewed",
        step: int = 0,
    ) -> "ProductObservation":
        """Record a product observation. Merges with existing if name matches."""
        for p in self.products:
            if self._product_name_match(p.name, name):
                if price is not None:
                    p.price = price
                if specs:
                    p.specs.update(specs)
                p.source_page = page_type or p.source_page
                p.status = status or p.status
                return p

        product = ProductObservation(
            name=name, price=price, specs=specs or {},
            source_page=page_type, status=status, step=step,
        )
        self.products.append(product)
        self._current_product = product
        return product

    def set_constraint(self, key: str, value: str) -> None:
        self.constraints[key] = value

    def set_current_focus(self, app: str = "", page: str = "") -> None:
        if app:
            self._current_app = app
        if page:
            self._current_page = page

    # ------------------------------------------------------------------
    # Context generation — lightweight, for EVERY-step injection
    # ------------------------------------------------------------------

    def progress_summary(self) -> str:
        """One-line progress for every-step injection. Keeps context minimal."""
        parts: list[str] = []

        if self.progress.overall_progress:
            parts.append(self.progress.overall_progress)
        elif self.progress.subtasks_completed:
            parts.append(self.progress.summary_line())

        if self._current_product:
            p = self._current_product
            price_str = f"¥{p.price}" if p.price else "?"
            parts.append(f"当前: {p.name} [{price_str}]")

        cart_items = [p for p in self.products if p.status == "added_to_cart"]
        if cart_items:
            parts.append(f"购物车{len(cart_items)}件")

        recent = self.steps[-3:]
        if recent:
            actions = []
            for r in recent:
                if r.action_target:
                    actions.append(f"{r.action_type}({r.action_target})")
                else:
                    actions.append(r.action_type)
            parts.append(" → ".join(actions))

        return " | ".join(parts) if parts else ""

    def current_focus(self) -> str:
        """What the agent is currently looking at — 1 line."""
        if self._current_product:
            p = self._current_product
            price = f"¥{p.price}" if p.price else "价格未知"
            specs = ", ".join(f"{k}={v}" for k, v in p.specs.items())
            line = f"当前: {p.name} ({price}"
            if specs:
                line += f", {specs}"
            line += ")"
            return line
        return f"当前页面: {self._current_app or '未知应用'}"

    # ------------------------------------------------------------------
    # Retrieval — on-demand, called by RetrievalGateway
    # ------------------------------------------------------------------

    def retrieve_products(
        self, query: str = "", by_status: str | None = None, limit: int = 5,
    ) -> list[ProductObservation]:
        """Retrieve products, optionally filtered by status or keyword match."""
        results = self.products
        if by_status:
            results = [p for p in results if p.status == by_status]
        if query:
            query_lower = query.lower()
            scored = []
            for p in results:
                score = 0
                if query_lower in p.name.lower():
                    score += 10
                for v in p.specs.values():
                    if query_lower in v.lower():
                        score += 3
                if score > 0:
                    scored.append((score, p))
            scored.sort(key=lambda x: x[0], reverse=True)
            results = [p for _, p in scored]
        return results[:limit]

    def retrieve_by_keywords(self, keywords: list[str]) -> str:
        """Search reasoning archive + products for keywords. Returns formatted text."""
        findings: list[str] = []

        for entry in self._reasoning_archive:
            for kw in keywords:
                if kw.lower() in entry["thinking"].lower():
                    snippet = self._extract_sentence(entry["thinking"], kw)
                    if snippet and snippet not in findings:
                        findings.append(f"[Step {entry['step']}] {snippet}")
                        break

        for p in self.products:
            for kw in keywords:
                if kw.lower() in p.name.lower():
                    price = f"¥{p.price}" if p.price else "?"
                    findings.append(f"[商品] {p.name} ({price}, Step {p.step})")
                    break

        return "\n".join(findings[:5]) if findings else ""

    def retrieve_all_products_text(self) -> str:
        """Full product list as formatted text for detailed injection."""
        if not self.products:
            return ""
        lines = []
        for p in self.products:
            price = f"¥{p.price}" if p.price else "?"
            specs = ", ".join(f"{k}={v}" for k, v in p.specs.items())
            status_map = {"viewed": "已看", "added_to_cart": "已加购", "compared": "对比中"}
            status = status_map.get(p.status, p.status)
            line = f"  - {p.name} [{price}]"
            if specs:
                line += f" ({specs})"
            line += f" — {status} (Step {p.step})"
            lines.append(line)
        return "\n".join(lines)

    def is_stagnating(self) -> bool:
        return self._consecutive_same >= 2

    # ------------------------------------------------------------------
    # Intent detection — parse VLM thinking for retrieval signals
    # ------------------------------------------------------------------

    def detect_retrieval_intent(self, thinking: str) -> dict[str, Any]:
        """
        Analyze VLM thinking for implicit retrieval intents.

        Returns:
          should_retrieve: bool
          intent: "product_lookup" | "price_compare" | "recall" | "calculate" | ""
          keywords: list[str] to search for
        """
        thinking_lower = thinking.lower()

        uncertainty_kw = [
            "不确定", "忘记了", "之前看到", "那个商品", "价格是多少",
            "哪个", "我不确定", "记不清", "刚刚看", "前面看到",
            "not sure", "forgot", "remember", "which one",
        ]
        for kw in uncertainty_kw:
            if kw.lower() in thinking_lower:
                return {
                    "should_retrieve": True,
                    "intent": "recall",
                    "keywords": self._extract_product_keywords(thinking),
                }

        compare_kw = ["对比", "比较", "哪个更", "性价比", "便宜", "贵", "compare"]
        for kw in compare_kw:
            if kw.lower() in thinking_lower:
                return {
                    "should_retrieve": True,
                    "intent": "price_compare",
                    "keywords": self._extract_product_keywords(thinking),
                }

        calc_kw = ["总共", "合计", "一共", "加起来", "总价", "total", "sum", "multiply"]
        for kw in calc_kw:
            if kw.lower() in thinking_lower:
                return {"should_retrieve": True, "intent": "calculate", "keywords": []}

        lookup_kw = ["商品名", "价格", "多少钱", "规格", "什么颜色", "什么尺码"]
        for kw in lookup_kw:
            if kw.lower() in thinking_lower:
                return {
                    "should_retrieve": True,
                    "intent": "product_lookup",
                    "keywords": self._extract_product_keywords(thinking),
                }

        if self.is_stagnating():
            return {"should_retrieve": True, "intent": "recall", "keywords": []}

        return {"should_retrieve": False, "intent": "", "keywords": []}

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "task": self.task,
            "platform": self.platform,
            "products": [
                {
                    "name": p.name, "price": p.price, "specs": p.specs,
                    "source_page": p.source_page, "status": p.status, "step": p.step,
                }
                for p in self.products
            ],
            "steps_count": len(self.steps),
            "constraints": dict(self.constraints),
            "progress": {
                "completed": self.progress.subtasks_completed,
                "remaining": self.progress.subtasks_remaining,
                "current": self.progress.current_subtask,
            },
            "statistics": {
                "total_products": len(self.products),
                "cart_items": len([p for p in self.products if p.status == "added_to_cart"]),
                "total_steps": len(self.steps),
                "stagnation_count": self._consecutive_same,
            },
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _product_name_match(a: str, b: str) -> bool:
        a_norm = "".join(a.lower().split())
        b_norm = "".join(b.lower().split())
        if a_norm == b_norm:
            return True
        if len(a_norm) > 4 and len(b_norm) > 4:
            if a_norm in b_norm or b_norm in a_norm:
                return True
        return False

    @staticmethod
    def _truncate_thinking(thinking: str, max_chars: int = 120) -> str:
        if not thinking:
            return ""
        for sep in ["。", ".", "\n", "；"]:
            if sep in thinking[:max_chars]:
                return thinking[:thinking.index(sep)] + sep
        return thinking[:max_chars] + "..."

    @staticmethod
    def _extract_sentence(text: str, keyword: str) -> str:
        idx = text.lower().find(keyword.lower())
        if idx < 0:
            return ""
        start = max(0, idx - 40)
        end = min(len(text), idx + len(keyword) + 80)
        snippet = text[start:end].strip()
        for sep in ["。", ".", "\n"]:
            if sep in snippet:
                parts = snippet.split(sep)
                for p in parts:
                    if keyword.lower() in p.lower():
                        return p.strip()[:200]
        return snippet[:200]

    def _extract_product_keywords(self, thinking: str) -> list[str]:
        import re
        keywords: list[str] = []
        patterns = [
            r'[""「『](.{2,30})[""」『]',
            r'(?:Nike|Adidas|Apple|Samsung|华为|小米|OPPO|vivo)[\w\s\-一-龥]{2,30}',
            r'(?:商品|产品|东西)\s*[叫做是]?\s*(.{2,20})',
        ]
        for pattern in patterns:
            matches = re.findall(pattern, thinking)
            for m in matches:
                kw = m.strip() if isinstance(m, str) else m[0].strip()
                if kw and len(kw) >= 2 and kw not in keywords:
                    keywords.append(kw)
        return keywords[:5]

    def _infer_progress(
        self, action_type: str, action_target: str, thinking: str,
    ) -> None:
        if action_type in ("finish", "terminate", "answer"):
            self.progress.mark_subtask_done(
                f"{action_target}" if action_target else "任务完成"
            )
        elif action_type == "Launch":
            self.progress.mark_subtask_done(
                f"启动{action_target}" if action_target else "启动应用"
            )
