"""Training-free spatial page graph memory for mobile GUI agents.

This module sits above GraphStore.  It turns low-level graph storage into a
small interface for page localization, route planning, observation recording,
and route repair.  The first implementation is deterministic and heuristic so
it can run with the existing VLM/GUI models without adding model training cost.
"""

from __future__ import annotations

import heapq
import re
from dataclasses import dataclass, field
from typing import Any, Iterable


_SHOPPING_APPS = {
    "taobao",
    "tmall",
    "jd",
    "jingdong",
    "pinduoduo",
    "meituan",
    "eleme",
    "淘宝",
    "天猫",
    "京东",
    "拼多多",
    "美团",
    "饿了么",
}

_HIGH_RISK_PAGE_TYPES = {"checkout", "payment", "address", "login", "confirm"}
_MEDIUM_RISK_PAGE_TYPES = {"spec_selection", "cart"}

_PAGE_TYPE_KEYWORDS = [
    ("spec_selection", ("spec", "sku", "规格", "颜色", "尺码", "容量", "版本", "口味")),
    ("checkout", ("checkout", "order", "结算", "订单", "提交订单", "确认订单")),
    ("payment", ("pay", "payment", "支付", "付款")),
    ("cart", ("cart", "购物车", "加购", "加入购物车")),
    ("product_detail", ("detail", "商品详情", "详情", "立即购买", "加购物车")),
    ("search_result", ("result", "搜索结果", "商品列表", "筛选", "销量", "综合")),
    ("search_input", ("search_input", "搜索框", "搜索页", "输入", "键盘")),
    ("home", ("home", "首页", "推荐", "底部导航")),
    ("login", ("login", "登录", "验证码", "手机号")),
    ("address", ("address", "地址", "收货")),
]

_GOAL_PAGE_KEYWORDS = [
    ("cart", ("购物车", "加购", "加入购物车", "add cart", "cart")),
    ("checkout", ("结算", "下单", "订单", "checkout")),
    ("product_detail", ("详情", "商品", "product")),
    ("search_result", ("搜索", "查找", "search", "找")),
]


def _safe_slug(value: str, limit: int = 48) -> str:
    value = value.strip() or "unknown"
    value = re.sub(r"\s+", "_", value)
    value = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", value)
    return value[:limit].strip("_") or "unknown"


def _contains_any(text: str, keywords: Iterable[str]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


@dataclass(frozen=True)
class PageState:
    """Stable page-level abstraction used as a graph node."""

    state_id: str
    app: str
    page_type: str = "unknown"
    summary: str = ""
    landmarks: tuple[str, ...] = ()
    affordances: tuple[str, ...] = ()
    slots: dict[str, str] = field(default_factory=dict)
    risk_level: str = "normal"
    screenshot_hash: str = ""
    semantic_signature: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "state_id": self.state_id,
            "app": self.app,
            "page_type": self.page_type,
            "summary": self.summary,
            "landmarks": list(self.landmarks),
            "affordances": list(self.affordances),
            "slots": dict(self.slots),
            "risk_level": self.risk_level,
            "screenshot_hash": self.screenshot_hash,
            "semantic_signature": self.semantic_signature,
        }


@dataclass(frozen=True)
class TransitionEdge:
    """Conditioned transition between page states."""

    source_id: str
    target_id: str
    action_type: str
    action_target: str = ""
    action_params: dict[str, Any] = field(default_factory=dict)
    precondition: str = ""
    postcondition: str = ""
    success_count: int = 1
    fail_count: int = 0
    rollback_action: str = "Back"
    cost: float = 1.0
    risk: str = "normal"
    confidence: float = 1.0
    evidence: str = ""

    @classmethod
    def from_action(
        cls,
        source_id: str,
        target_id: str,
        action: dict[str, Any],
        *,
        risk: str = "normal",
        postcondition: str = "",
        outcome: str = "success",
    ) -> "TransitionEdge":
        action_type = str(action.get("action_type") or action.get("action") or "unknown")
        action_target = str(
            action.get("semantic_target")
            or action.get("target")
            or action.get("element")
            or action.get("text")
            or ""
        )
        success_count = 0 if outcome == "failure" else 1
        fail_count = 1 if outcome == "failure" else 0
        return cls(
            source_id=source_id,
            target_id=target_id,
            action_type=action_type,
            action_target=action_target,
            action_params=dict(action),
            postcondition=postcondition,
            success_count=success_count,
            fail_count=fail_count,
            risk=risk,
            confidence=0.4 if outcome == "failure" else 1.0,
        )

    @property
    def weighted_cost(self) -> float:
        attempts = max(1, self.success_count + self.fail_count)
        fail_rate = self.fail_count / attempts
        risk_penalty = {"normal": 0.0, "medium": 0.8, "high": 2.0}.get(self.risk, 0.5)
        confidence_bonus = max(0.0, min(1.0, self.confidence))
        return self.cost + fail_rate * 3.0 + risk_penalty - confidence_bonus * 0.3

    def to_next_action(self) -> dict[str, Any]:
        return {
            "type": self.action_type,
            "target": self.action_target,
            "target_desc": repr(self.action_params),
            "confidence": self.confidence,
            "source_state_id": self.source_id,
            "target_state_id": self.target_id,
            "postcondition": self.postcondition,
            "risk": self.risk,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "action_type": self.action_type,
            "action_target": self.action_target,
            "action_params": dict(self.action_params),
            "precondition": self.precondition,
            "postcondition": self.postcondition,
            "success_count": self.success_count,
            "fail_count": self.fail_count,
            "rollback_action": self.rollback_action,
            "cost": self.cost,
            "weighted_cost": self.weighted_cost,
            "risk": self.risk,
            "confidence": self.confidence,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class PageBeliefCandidate:
    state: PageState
    score: float
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = self.state.to_dict()
        data.update({"score": self.score, "reason": self.reason})
        return data


@dataclass(frozen=True)
class PageBelief:
    current_state_id: str
    candidates: tuple[PageBeliefCandidate, ...]
    confidence: float
    is_novel: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_state_id": self.current_state_id,
            "confidence": self.confidence,
            "is_novel": self.is_novel,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }


@dataclass(frozen=True)
class GoalSpec:
    domain: str
    target_page_types: tuple[str, ...]
    slots: dict[str, str] = field(default_factory=dict)
    forbidden_actions: tuple[str, ...] = ("pay", "submit_order", "payment")
    missing_info_policy: str = "ask_user"

    @classmethod
    def from_task(cls, task: str, app: str = "") -> "GoalSpec":
        target_page_types: list[str] = []
        for page_type, keywords in _GOAL_PAGE_KEYWORDS:
            if _contains_any(task, keywords):
                target_page_types.append(page_type)
        if not target_page_types:
            target_page_types = ["search_result", "product_detail", "cart"]

        domain = "shopping" if _contains_any(f"{task} {app}", _SHOPPING_APPS) else "general"
        return cls(domain=domain, target_page_types=tuple(dict.fromkeys(target_page_types)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "target_page_types": list(self.target_page_types),
            "slots": dict(self.slots),
            "forbidden_actions": list(self.forbidden_actions),
            "missing_info_policy": self.missing_info_policy,
        }


@dataclass(frozen=True)
class RouteStep:
    edge: TransitionEdge

    def to_dict(self) -> dict[str, Any]:
        return self.edge.to_dict()


@dataclass(frozen=True)
class RoutePlan:
    mode: str
    steps: tuple[RouteStep, ...] = ()
    total_cost: float = 0.0
    confidence: float = 0.0
    risk_summary: str = ""
    goal: GoalSpec | None = None

    @property
    def next_action(self) -> dict[str, Any] | None:
        if not self.steps:
            return None
        return self.steps[0].edge.to_next_action()

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "total_cost": self.total_cost,
            "confidence": self.confidence,
            "risk_summary": self.risk_summary,
            "goal": self.goal.to_dict() if self.goal else None,
            "steps": [step.to_dict() for step in self.steps],
        }


@dataclass(frozen=True)
class RepairDecision:
    action: str
    reason: str
    confidence: float = 0.0
    rollback_action: str = "Back"

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "reason": self.reason,
            "confidence": self.confidence,
            "rollback_action": self.rollback_action,
        }


class SpatialGraphMemory:
    """Deep graph-memory module for page localization and route planning."""

    def __init__(self, graph_store: Any | None = None):
        self.graph_store = graph_store
        self._last_belief: PageBelief | None = None
        self._last_route: RoutePlan | None = None
        self._local_states: dict[str, PageState] = {}
        self._local_edges: dict[str, list[TransitionEdge]] = {}

    def build_page_state(
        self,
        *,
        ui_hash: str,
        semantic_layout: str,
        task: str = "",
    ) -> PageState:
        app = semantic_layout or "home_screen"
        page_type = self._infer_page_type(semantic_layout)
        if page_type == "unknown":
            page_type = self._infer_page_type(task)
        risk_level = self._infer_risk_level(page_type, task)
        landmarks = self._infer_landmarks(page_type)
        affordances = self._infer_affordances(page_type)
        signature = self._semantic_signature(app, page_type, landmarks, affordances)
        state_id = f"state_{_safe_slug(signature)}_{ui_hash[:8]}"
        return PageState(
            state_id=state_id,
            app=app,
            page_type=page_type,
            summary=f"{app}:{page_type}",
            landmarks=landmarks,
            affordances=affordances,
            risk_level=risk_level,
            screenshot_hash=ui_hash,
            semantic_signature=signature,
        )

    def locate(
        self,
        screen: dict[str, Any],
        task: str,
        previous_action: dict[str, Any] | None = None,
    ) -> PageBelief:
        page_state = self.build_page_state(
            ui_hash=str(screen.get("ui_hash", "")),
            semantic_layout=str(screen.get("semantic_layout", "")),
            task=task,
        )
        self._local_states[page_state.state_id] = page_state

        candidates = [
            PageBeliefCandidate(
                state=page_state,
                score=1.0,
                reason="current observation signature",
            )
        ]

        graph_candidate = self._load_graph_candidate(page_state)
        if graph_candidate and graph_candidate.state_id != page_state.state_id:
            candidates.append(
                PageBeliefCandidate(
                    state=graph_candidate,
                    score=0.78,
                    reason="semantic graph match",
                )
            )

        candidates_tuple = tuple(sorted(candidates, key=lambda item: item.score, reverse=True))
        belief = PageBelief(
            current_state_id=page_state.state_id,
            candidates=candidates_tuple,
            confidence=candidates_tuple[0].score,
            is_novel=graph_candidate is None,
        )
        self._last_belief = belief
        return belief

    def infer_goal(self, task: str, app: str = "") -> GoalSpec:
        return GoalSpec.from_task(task, app)

    def plan(self, belief: PageBelief, goal_spec: GoalSpec) -> RoutePlan:
        start_id = belief.current_state_id
        if not goal_spec.target_page_types:
            route = RoutePlan(mode="explore", confidence=0.0, risk_summary="no goal page type", goal=goal_spec)
            self._last_route = route
            return route

        graph_edges = self._load_edges(start_id)
        if not graph_edges:
            route = RoutePlan(
                mode="explore",
                confidence=belief.confidence,
                risk_summary="no graph route from current page",
                goal=goal_spec,
            )
            self._last_route = route
            return route

        best_path = self._shortest_path(start_id, goal_spec.target_page_types)
        if not best_path:
            route = RoutePlan(
                mode="explore",
                confidence=belief.confidence,
                risk_summary="graph has no route to target page",
                goal=goal_spec,
            )
            self._last_route = route
            return route

        total_cost = sum(step.edge.weighted_cost for step in best_path)
        high_risk = [step.edge.risk for step in best_path if step.edge.risk == "high"]
        route = RoutePlan(
            mode="navigate",
            steps=tuple(best_path),
            total_cost=total_cost,
            confidence=max(0.0, min(1.0, belief.confidence / max(1.0, total_cost))),
            risk_summary="high risk route" if high_risk else "normal route",
            goal=goal_spec,
        )
        self._last_route = route
        return route

    def record_observation(
        self,
        before: str | PageState,
        action: dict[str, Any],
        after: str | PageState,
        outcome: str = "success",
    ) -> None:
        source_id = before.state_id if isinstance(before, PageState) else str(before)
        target_id = after.state_id if isinstance(after, PageState) else str(after)
        target_page = after.page_type if isinstance(after, PageState) else ""
        if isinstance(before, PageState):
            self._local_states[before.state_id] = before
        if isinstance(after, PageState):
            self._local_states[after.state_id] = after
        risk = self._infer_risk_level(target_page, "")
        edge = TransitionEdge.from_action(
            source_id,
            target_id,
            action,
            risk=risk,
            postcondition=target_page,
            outcome=outcome,
        )
        self._local_edges.setdefault(source_id, []).append(edge)

        if self.graph_store and getattr(self.graph_store, "driver", None):
            self.graph_store.add_state_transition(
                source_id,
                target_id,
                action,
                outcome=outcome,
                source_metadata=before.to_dict() if isinstance(before, PageState) else None,
                target_metadata=after.to_dict() if isinstance(after, PageState) else None,
            )

    def repair(
        self,
        observed: PageBelief,
        expected: str | None,
        route_plan: RoutePlan | None = None,
    ) -> RepairDecision:
        plan = route_plan or self._last_route
        if not expected:
            return RepairDecision("fallback_to_vlm", "no expected postcondition", 0.4)

        observed_types = {candidate.state.page_type for candidate in observed.candidates}
        if expected in observed_types or observed.current_state_id == expected:
            return RepairDecision("retry", "expected state already observed", observed.confidence)

        if any(page_type in {"login", "payment", "checkout", "address"} for page_type in observed_types):
            return RepairDecision("ask_user", "high risk or user-owned page detected", 0.8)

        if plan and plan.steps:
            return RepairDecision(
                "rollback",
                "observed page does not match planned postcondition",
                0.7,
                rollback_action=plan.steps[0].edge.rollback_action,
            )

        return RepairDecision("replan", "route deviated without rollback edge", 0.5)

    def context_summary(
        self,
        belief: PageBelief,
        route_plan: RoutePlan,
        repair_hint: RepairDecision | None = None,
    ) -> str:
        top = belief.candidates[0].state if belief.candidates else None
        parts: list[str] = []
        if top:
            parts.append(
                f"[SpatialGraph] current={top.app}/{top.page_type} "
                f"risk={top.risk_level} confidence={belief.confidence:.2f}"
            )
        if route_plan.mode == "navigate" and route_plan.steps:
            next_edge = route_plan.steps[0].edge
            parts.append(
                "[SpatialGraph Route] "
                f"{next_edge.source_id} --{next_edge.action_type}:{next_edge.action_target}--> "
                f"{next_edge.target_id}; cost={route_plan.total_cost:.2f}; risk={route_plan.risk_summary}"
            )
        elif route_plan.risk_summary:
            parts.append(f"[SpatialGraph Route] explore: {route_plan.risk_summary}")
        if repair_hint:
            parts.append(f"[SpatialGraph Repair] {repair_hint.action}: {repair_hint.reason}")
        return "\n".join(parts)

    def _load_graph_candidate(self, page_state: PageState) -> PageState | None:
        if not self.graph_store or not getattr(self.graph_store, "driver", None):
            return None
        try:
            data = self.graph_store.get_state_by_semantic(page_state.semantic_signature)
        except Exception:
            return None
        if not data:
            return None
        return self._page_state_from_graph(data, fallback=page_state)

    def _load_edges(self, state_id: str) -> list[TransitionEdge]:
        local_edges = list(self._local_edges.get(state_id, []))
        graph_edges: list[TransitionEdge] = []
        if self.graph_store and getattr(self.graph_store, "driver", None):
            try:
                graph_edges = self.graph_store.get_outgoing_transitions(state_id)
            except AttributeError:
                graph_edges = []
            except Exception:
                graph_edges = []
        return local_edges + graph_edges

    def _shortest_path(self, start_id: str, target_page_types: tuple[str, ...]) -> list[RouteStep]:
        queue: list[tuple[float, str, list[TransitionEdge]]] = [(0.0, start_id, [])]
        best_cost: dict[str, float] = {start_id: 0.0}
        visited: set[str] = set()

        while queue:
            cost, state_id, path = heapq.heappop(queue)
            if state_id in visited:
                continue
            visited.add(state_id)

            state = self._local_states.get(state_id)
            if state and state.page_type in target_page_types and path:
                return [RouteStep(edge=edge) for edge in path]

            for edge in self._load_edges(state_id):
                if edge.postcondition in target_page_types:
                    return [RouteStep(edge=item) for item in path + [edge]]
                next_cost = cost + edge.weighted_cost
                if next_cost >= best_cost.get(edge.target_id, float("inf")):
                    continue
                best_cost[edge.target_id] = next_cost
                heapq.heappush(queue, (next_cost, edge.target_id, path + [edge]))

        return []

    def _page_state_from_graph(self, data: dict[str, Any], fallback: PageState) -> PageState:
        return PageState(
            state_id=str(data.get("state_id") or fallback.state_id),
            app=str(data.get("app") or data.get("app_name") or fallback.app),
            page_type=str(data.get("page_type") or fallback.page_type),
            summary=str(data.get("summary") or data.get("semantic_layout") or fallback.summary),
            landmarks=tuple(data.get("landmarks") or fallback.landmarks),
            affordances=tuple(data.get("affordances") or fallback.affordances),
            slots=dict(data.get("slots") or fallback.slots),
            risk_level=str(data.get("risk_level") or fallback.risk_level),
            screenshot_hash=str(data.get("screenshot_hash") or fallback.screenshot_hash),
            semantic_signature=str(data.get("semantic_signature") or fallback.semantic_signature),
        )

    def _semantic_signature(
        self,
        app: str,
        page_type: str,
        landmarks: tuple[str, ...],
        affordances: tuple[str, ...],
    ) -> str:
        return "|".join([app or "unknown", page_type or "unknown", ",".join(landmarks), ",".join(affordances)])

    def _infer_page_type(self, text: str) -> str:
        for page_type, keywords in _PAGE_TYPE_KEYWORDS:
            if _contains_any(text, keywords):
                return page_type
        return "unknown"

    def _infer_risk_level(self, page_type: str, task: str) -> str:
        if page_type in _HIGH_RISK_PAGE_TYPES or _contains_any(task, ("支付", "付款", "提交订单")):
            return "high"
        if page_type in _MEDIUM_RISK_PAGE_TYPES:
            return "medium"
        return "normal"

    def _infer_landmarks(self, page_type: str) -> tuple[str, ...]:
        mapping = {
            "home": ("search_bar", "bottom_tabs"),
            "search_input": ("active_search_bar", "keyboard_or_suggestions"),
            "search_result": ("search_bar", "product_cards", "filters"),
            "product_detail": ("product_image", "price", "buy_buttons"),
            "spec_selection": ("spec_options", "quantity_selector", "confirm_button"),
            "cart": ("cart_items", "select_all", "checkout_button"),
            "checkout": ("address", "order_items", "submit_order_button"),
            "login": ("phone_input", "verification_code"),
        }
        return mapping.get(page_type, ())

    def _infer_affordances(self, page_type: str) -> tuple[str, ...]:
        mapping = {
            "home": ("tap_search", "open_tab"),
            "search_input": ("type_query", "submit_search"),
            "search_result": ("open_product", "filter", "sort", "scroll"),
            "product_detail": ("open_spec", "add_to_cart", "back"),
            "spec_selection": ("choose_spec", "confirm_spec", "back"),
            "cart": ("select_item", "checkout", "back"),
            "checkout": ("review_order", "back", "ask_user"),
            "login": ("back", "ask_user"),
        }
        return mapping.get(page_type, ())
