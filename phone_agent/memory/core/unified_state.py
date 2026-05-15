"""
UnifiedSessionState — single source of truth for session-scoped state.

Merges:
  - KnowledgeBase  (external memory decoupling)
  - SessionMemory  (old pre-decoupling product tracking)
  - StateManager   (state ID lifecycle)

Design principle: ONE write per observation, not dual-write to two components.
"""

import re
from datetime import datetime
from typing import Any

from .product import Product, ProductStatus, product_name_match
from .step_record import StepRecord


class UnifiedSessionState:
    """
    Unified session state — replaces KnowledgeBase + SessionMemory + StateManager.

    Lifecycle:
        reset()              → new task
        record_step()        → after each VLM action
        record_product()     → when a product is observed
        progress_summary()   → injected into every VLM context (lightweight)
        current_focus()      → 1-line current focus for context
        retrieve_by_keywords() → on-demand retrieval when agent is confused
    """

    def __init__(self):
        # ── Task identity ──────────────────────────────────────────
        self.task: str = ""
        self.platform: str = ""

        # ── Products ───────────────────────────────────────────────
        self.products: list[Product] = []
        self._current_product: Product | None = None

        # ── Steps ──────────────────────────────────────────────────
        self.steps: list[StepRecord] = []

        # ── Constraints ────────────────────────────────────────────
        self.constraints: dict[str, str] = {}

        # ── Progress tracking (from ProgressTracker) ───────────────
        self.subtasks_completed: list[str] = []
        self.subtasks_remaining: list[str] = []
        self.current_subtask: str = ""
        self.overall_progress: str = ""

        # ── Current focus ──────────────────────────────────────────
        self._current_page: str = ""
        self._current_app: str = ""

        # ── Stagnation detection ───────────────────────────────────
        self._last_action_type: str = ""
        self._last_page_type: str = ""
        self._last_action_target: str = ""
        self._consecutive_same: int = 0

        # ── State ID tracking (from StateManager) ──────────────────
        self._current_state_id: str | None = None
        self._prev_state_id: str | None = None
        self._task_start_state_id: str | None = None
        self._task_end_state_id: str | None = None
        self._state_history: list[str] = []

        # ── Reasoning archive (for on-demand retrieval) ────────────
        self._reasoning_archive: list[dict[str, str]] = []

    # ────────────────────────────────────────────────────────────────
    # Lifecycle
    # ────────────────────────────────────────────────────────────────

    def reset(self, task: str = "", platform: str = "") -> None:
        """Reset all state for a new task."""
        self.task = task
        self.platform = platform
        self.products.clear()
        self.steps.clear()
        self.constraints.clear()
        self.subtasks_completed.clear()
        self.subtasks_remaining.clear()
        self.current_subtask = ""
        self.overall_progress = ""
        self._current_product = None
        self._current_page = ""
        self._current_app = ""
        self._last_action_type = ""
        self._last_page_type = ""
        self._last_action_target = ""
        self._consecutive_same = 0
        self._current_state_id = None
        self._prev_state_id = None
        self._task_start_state_id = None
        self._task_end_state_id = None
        self._state_history.clear()
        self._reasoning_archive.clear()

    # ────────────────────────────────────────────────────────────────
    # State ID (from StateManager)
    # ────────────────────────────────────────────────────────────────

    @staticmethod
    def compute_state_id(screenshot_hash: str, semantic_layout: str) -> str:
        return f"state_{semantic_layout}_{screenshot_hash[:8]}"

    def update_state(self, new_state_id: str) -> tuple[str | None, str]:
        self._prev_state_id = self._current_state_id
        self._current_state_id = new_state_id
        self._state_history.append(new_state_id)
        return self._prev_state_id, self._current_state_id

    def start_task_state(self, initial_state_id: str) -> None:
        self._task_start_state_id = initial_state_id
        self._current_state_id = initial_state_id
        self._prev_state_id = None
        self._state_history = [initial_state_id]

    def end_task_state(self, final_state_id: str) -> None:
        self._task_end_state_id = final_state_id

    @property
    def current_state_id(self) -> str | None:
        return self._current_state_id

    @property
    def prev_state_id(self) -> str | None:
        return self._prev_state_id

    @property
    def task_start_state_id(self) -> str | None:
        return self._task_start_state_id

    @property
    def task_end_state_id(self) -> str | None:
        return self._task_end_state_id

    def get_state_history(self) -> list[str]:
        return self._state_history.copy()

    # ────────────────────────────────────────────────────────────────
    # Recording (write path)
    # ────────────────────────────────────────────────────────────────

    def record_step(
        self,
        step: int,
        action_type: str,
        action_target: str = "",
        thinking_full: str = "",
        page_type: str = "",
        app: str = "",
    ) -> bool:
        """
        Record a step. Returns True if stagnation is detected.

        thinking_full is archived for retrieval; thinking_short is derived.
        """
        thinking_short = self._truncate_thinking(thinking_full)

        record = StepRecord(
            step=step,
            action_type=action_type,
            action_target=action_target,
            thinking_short=thinking_short,
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

        # Stagnation detection: same action + same page + same target
        if (
            action_type == self._last_action_type
            and page_type == self._last_page_type
            and action_target == self._last_action_target
        ):
            self._consecutive_same += 1
        else:
            self._consecutive_same = 1
        self._last_action_type = action_type
        self._last_page_type = page_type
        self._last_action_target = action_target

        # Auto-update progress
        self._infer_progress(action_type, action_target, thinking_full)

        return self._consecutive_same >= 2

    def record_product(
        self,
        name: str,
        price: float | None = None,
        specs: dict[str, str] | None = None,
        page_type: str = "",
        status: ProductStatus = ProductStatus.VIEWED,
        step: int = 0,
    ) -> Product:
        """Record a product observation. Merges with existing if name matches."""
        for p in self.products:
            if product_name_match(p.name, name):
                if price is not None:
                    p.price = price
                if specs:
                    p.specs.update(specs)
                if page_type:
                    p.source_page = page_type
                if status != ProductStatus.VIEWED:
                    p.status = status
                self._current_product = p
                return p

        product = Product(
            name=name,
            price=price,
            specs=specs or {},
            source_page=page_type,
            status=status,
            first_seen_step=step,
        )
        self.products.append(product)
        self._current_product = product
        return product

    def set_current_product(self, product: Product) -> None:
        """Set the product currently being viewed (also records it)."""
        self._current_product = product
        if product.name:
            self.record_product(
                name=product.name,
                price=product.price,
                specs=product.specs,
                page_type=product.source_page,
                status=product.status,
            )

    def add_to_cart(self, product: Product) -> None:
        """Mark a product as added to cart."""
        product.status = ProductStatus.ADDED_TO_CART
        for existing in self.products:
            if product_name_match(existing.name, product.name):
                existing.status = ProductStatus.ADDED_TO_CART

    def set_constraint(self, key: str, value: str) -> None:
        self.constraints[key] = value

    def set_current_focus(self, app: str = "", page: str = "") -> None:
        if app:
            self._current_app = app
        if page:
            self._current_page = page

    # ────────────────────────────────────────────────────────────────
    # Context generation — lightweight, for EVERY-step injection
    # ────────────────────────────────────────────────────────────────

    def progress_summary(self) -> str:
        """One-line progress for every-step injection. Keeps context minimal."""
        parts: list[str] = []

        if self.overall_progress:
            parts.append(self.overall_progress)
        elif self.subtasks_completed:
            parts.append(self._summary_line())

        if self._current_product:
            p = self._current_product
            parts.append(f"当前: {p.name} [{p.price_display}]")

        cart_items = [p for p in self.products if p.status == ProductStatus.ADDED_TO_CART]
        if cart_items:
            parts.append(f"购物车{len(cart_items)}件")

        recent = self.steps[-3:]
        if recent:
            actions = [r.summary for r in recent]
            parts.append(" → ".join(actions))

        return " | ".join(parts) if parts else ""

    def current_focus(self) -> str:
        """What the agent is currently looking at — 1 line."""
        if self._current_product:
            p = self._current_product
            line = f"当前: {p.name} ({p.price_display}"
            if p.specs:
                line += f", {p.specs_display}"
            line += ")"
            return line
        return f"当前页面: {self._current_app or '未知应用'}"

    # ────────────────────────────────────────────────────────────────
    # Retrieval — on-demand, called by RetrievalGateway
    # ────────────────────────────────────────────────────────────────

    def retrieve_products(
        self, query: str = "", by_status: ProductStatus | None = None, limit: int = 5,
    ) -> list[Product]:
        """Retrieve products, optionally filtered by status or keyword match."""
        results = self.products
        if by_status is not None:
            results = [p for p in results if p.status == by_status]
        if query:
            query_lower = query.lower()
            scored: list[tuple[int, Product]] = []
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
                    findings.append(f"[商品] {p.name} ({p.price_display}, Step {p.first_seen_step})")
                    break

        return "\n".join(findings[:5]) if findings else ""

    def retrieve_all_products_text(self) -> str:
        """Full product list as formatted text for detailed injection."""
        if not self.products:
            return ""
        lines = []
        for p in self.products:
            line = f"  - {p.name} [{p.price_display}]"
            if p.specs:
                line += f" ({p.specs_display})"
            line += f" — {p.status_cn} (Step {p.first_seen_step})"
            lines.append(line)
        return "\n".join(lines)

    # ────────────────────────────────────────────────────────────────
    # Stagnation & compression
    # ────────────────────────────────────────────────────────────────

    def is_stagnating(self) -> bool:
        return self._consecutive_same >= 2

    def should_compress(self) -> bool:
        """True every 5 steps — trigger VLM history compression."""
        return len(self.steps) > 0 and len(self.steps) % 5 == 0

    # ────────────────────────────────────────────────────────────────
    # Intent detection — parse VLM thinking for retrieval signals
    # ────────────────────────────────────────────────────────────────

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

    # ────────────────────────────────────────────────────────────────
    # Serialization
    # ────────────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "task": self.task,
            "platform": self.platform,
            "current_product": _product_to_dict(self._current_product) if self._current_product else None,
            "products": [_product_to_dict(p) for p in self.products],
            "cart_items": [
                _product_to_dict(p)
                for p in self.products
                if p.status == ProductStatus.ADDED_TO_CART
            ],
            "steps": [
                {
                    "step": s.step,
                    "action_type": s.action_type,
                    "action_target": s.action_target,
                    "thinking_short": s.thinking_short,
                    "page_type": s.page_type,
                    "app": s.app,
                    "timestamp": s.timestamp,
                }
                for s in self.steps
            ],
            "constraints": dict(self.constraints),
            "progress": {
                "completed": self.subtasks_completed,
                "remaining": self.subtasks_remaining,
                "current": self.current_subtask,
            },
            "state": {
                "current_state_id": self._current_state_id,
                "prev_state_id": self._prev_state_id,
                "task_start_state_id": self._task_start_state_id,
                "task_end_state_id": self._task_end_state_id,
                "state_history_size": len(self._state_history),
            },
            "statistics": {
                "total_products": len(self.products),
                "cart_items": len([p for p in self.products if p.status == ProductStatus.ADDED_TO_CART]),
                "total_steps": len(self.steps),
                "stagnation_count": self._consecutive_same,
            },
        }

    # ────────────────────────────────────────────────────────────────
    # Internal helpers
    # ────────────────────────────────────────────────────────────────

    def _summary_line(self) -> str:
        remaining = self.subtasks_remaining
        if remaining:
            done_count = len(self.subtasks_completed)
            done = " → ".join(self.subtasks_completed[-3:]) if self.subtasks_completed else "开始"
            return f"[Step {done_count}] {done} | 下一步: {remaining[0]}"
        done_count = len(self.subtasks_completed)
        done = " → ".join(self.subtasks_completed[-3:]) if self.subtasks_completed else "执行中"
        return f"[Step {done_count}] {done}"

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
            label = action_target if action_target else "任务完成"
            self._mark_subtask_done(label)
        elif action_type == "Launch":
            label = f"启动{action_target}" if action_target else "启动应用"
            self._mark_subtask_done(label)

    def _mark_subtask_done(self, subtask: str) -> None:
        if subtask in self.subtasks_remaining:
            self.subtasks_remaining.remove(subtask)
        if subtask not in self.subtasks_completed:
            self.subtasks_completed.append(subtask)


def _product_to_dict(p: Product) -> dict:
    return {
        "name": p.name,
        "price": p.price,
        "currency": p.currency,
        "specs": dict(p.specs),
        "source_page": p.source_page,
        "status": p.status.value,
        "first_seen_step": p.first_seen_step,
    }
