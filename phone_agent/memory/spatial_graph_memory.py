"""Training-free spatial page graph memory for mobile GUI agents.

This module sits above GraphStore. It turns low-level graph storage into a
compact interface for page localization, route planning, observation recording,
offline exploration import, and route repair. The implementation is deliberately
deterministic so it can reuse existing GUI/VLM models without extra training.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
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
    # Keep mojibake variants because existing exploration fixtures were saved
    # with a broken encoding and still need to be importable.
    "å¨£æ¨ºç–‚",
}

_APP_ALIASES = {
    "淘宝": {"淘宝", "天猫", "taobao", "tmall"},
    "天猫": {"淘宝", "天猫", "taobao", "tmall"},
    "京东": {"京东", "jd", "jingdong"},
    "美团": {"美团", "meituan"},
    "饿了么": {"饿了么", "eleme"},
    "叮咚买菜": {"叮咚买菜"},
    "盒马": {"盒马"},
    "瑞幸": {"瑞幸", "luckin"},
}
_APP_MENTION_TOKENS = tuple(sorted({token for tokens in _APP_ALIASES.values() for token in tokens}, key=len, reverse=True))

_HIGH_RISK_PAGE_TYPES = {"checkout", "payment", "address", "login", "confirm"}
_MEDIUM_RISK_PAGE_TYPES = {"spec_selection", "cart", "order_list", "refund"}
_TRANSIENT_PAGE_TYPES = {"unknown"}
_TASK_DAG_PAGE_LIMIT = 64

_CORE_SHOPPING_FLOW = (
    "home",
    "search_input",
    "search_result",
    "product_detail",
    "spec_selection",
    "cart",
    "checkout",
)

_PAGE_TYPE_KEYWORDS = [
    ("spec_selection", ("spec", "sku", "规格", "型号", "颜色", "尺码", "数量", "确定")),
    ("checkout", ("checkout", "order", "提交订单", "确认订单", "结算", "收货地址")),
    ("payment", ("pay", "payment", "付款", "支付", "收银台")),
    ("cart", ("cart", "购物车", "加入购物车", "去结算")),
    ("product_detail", ("detail", "product_detail", "商品详情", "详情", "价格", "立即购买")),
    ("search_result", ("result", "search_result", "搜索结果", "商品列表", "筛选", "综合")),
    ("search_input", ("search_input", "搜索框", "搜索输入", "历史搜索", "猜你想搜")),
    ("home", ("home", "首页", "推荐", "搜索栏")),
    ("login", ("login", "登录", "验证码", "账号")),
    ("address", ("address", "地址", "收货人")),
]

_GOAL_PAGE_KEYWORDS = [
    ("cart", ("购物车", "加入购物车", "加购", "加到购物车", "add cart", "cart")),
    ("checkout", ("结算", "提交订单", "确认订单", "checkout")),
    ("product_detail", ("商品详情", "详情", "product", "detail")),
    ("search_result", ("搜索", "搜索结果", "search", "result")),
    ("cart", ("购物车", "加入购物车", "加购", "add cart", "cart")),
    ("checkout", ("结算", "提交订单", "确认订单", "checkout")),
    ("product_detail", ("详情", "商品", "product", "detail")),
    ("search_result", ("搜索", "search", "结果")),
]

_ELEMENT_AFFORDANCE_HINTS = {
    "search": "tap_search",
    "search_bar": "tap_search",
    "suggestion": "submit_search",
    "product": "open_product",
    "card": "open_product",
    "filter": "filter",
    "sort": "sort",
    "button": "tap_button",
    "buy": "open_spec",
    "cart": "add_to_cart",
    "spec": "choose_spec",
    "sku": "choose_spec",
    "confirm": "confirm_spec",
    "checkout": "checkout",
    "back": "back",
}

_SLOT_PATTERNS = {
    "query": (r"query[:=]\s*([^,;]+)", r"搜索[：:]\s*([^,;，。]+)"),
    "product": (r"product[:=]\s*([^,;]+)", r"商品[：:]\s*([^,;，。]+)"),
    "price": (r"(?:¥|￥)\s*([0-9]+(?:\.[0-9]+)?)",),
}


def _safe_slug(value: str, limit: int = 48) -> str:
    value = value.strip() or "unknown"
    value = re.sub(r"\s+", "_", value)
    value = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", value)
    return value[:limit].strip("_") or "unknown"


def _contains_any(text: str, keywords: Iterable[str]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def _dedupe(values: Iterable[str], limit: int | None = None) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
        if limit and len(result) >= limit:
            break
    return tuple(result)


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
        confidence_value = action.get("confidence")
        confidence = float(confidence_value) if confidence_value is not None else (0.4 if outcome == "failure" else 1.0)
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
            confidence=confidence,
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
        positive_clauses = SpatialGraphMemory.positive_goal_clauses(task)
        for clause in positive_clauses:
            for page_type, keywords in _GOAL_PAGE_KEYWORDS:
                if _contains_any(clause, keywords):
                    target_page_types.append(page_type)
        if "checkout" in target_page_types:
            target_page_types = ["checkout"]
        elif "cart" in target_page_types:
            target_page_types = ["cart"]
        if not target_page_types:
            target_page_types = ["search_result", "product_detail", "cart"]

        domain = "shopping" if _contains_any(f"{task} {app}", _SHOPPING_APPS) else "general"
        slots = SpatialGraphMemory.extract_slots_from_text(task)
        return cls(domain=domain, target_page_types=tuple(dict.fromkeys(target_page_types)), slots=slots)

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


@dataclass(frozen=True)
class ExplorationImportResult:
    pages_imported: int
    transitions_imported: int
    pages_path: str
    transitions_path: str = ""
    unique_pages: int = 0
    persisted_to_graph: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "pages_imported": self.pages_imported,
            "transitions_imported": self.transitions_imported,
            "pages_path": self.pages_path,
            "transitions_path": self.transitions_path,
            "unique_pages": self.unique_pages,
            "persisted_to_graph": self.persisted_to_graph,
        }


@dataclass(frozen=True)
class GraphQualityReport:
    pages_seen: int = 0
    canonical_pages: int = 0
    transient_pages: int = 0
    transitions_seen: int = 0
    transitions_promoted: int = 0
    transitions_filtered: int = 0
    app_mismatch_pages: int = 0
    missing_edges: tuple[tuple[str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "pages_seen": self.pages_seen,
            "canonical_pages": self.canonical_pages,
            "transient_pages": self.transient_pages,
            "transitions_seen": self.transitions_seen,
            "transitions_promoted": self.transitions_promoted,
            "transitions_filtered": self.transitions_filtered,
            "app_mismatch_pages": self.app_mismatch_pages,
            "missing_edges": [list(edge) for edge in self.missing_edges],
        }


@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    reason: str = ""
    action: str = "continue"

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "reason": self.reason, "action": self.action}


@dataclass
class RuntimeDAG:
    plan_id: str
    app: str
    goal_spec: GoalSpec
    nodes: dict[str, PageState] = field(default_factory=dict)
    edges: dict[str, list[TransitionEdge]] = field(default_factory=dict)
    route: list[TransitionEdge] = field(default_factory=list)
    current_index: int = 0
    coverage_gaps: list[str] = field(default_factory=list)
    risk_boundaries: tuple[str, ...] = tuple(sorted(_HIGH_RISK_PAGE_TYPES))

    @property
    def is_usable(self) -> bool:
        return bool(self.route) and not self.coverage_gaps

    @property
    def current_node_id(self) -> str:
        if not self.route:
            return ""
        if self.current_index <= 0:
            return self.route[0].source_id
        if self.current_index - 1 < len(self.route):
            return self.route[self.current_index - 1].target_id
        return self.route[-1].target_id

    def next_edge(self) -> TransitionEdge | None:
        if self.current_index >= len(self.route):
            return None
        return self.route[self.current_index]

    def advance(self) -> None:
        if self.current_index < len(self.route):
            self.current_index += 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "app": self.app,
            "goal_spec": self.goal_spec.to_dict(),
            "current_node_id": self.current_node_id,
            "current_index": self.current_index,
            "coverage_gaps": list(self.coverage_gaps),
            "risk_boundaries": list(self.risk_boundaries),
            "nodes": [node.to_dict() for node in self.nodes.values()],
            "route": [edge.to_dict() for edge in self.route],
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
        app: str | None = None,
        page_type: str | None = None,
        summary: str = "",
        elements: dict[str, Any] | None = None,
        state_id_strategy: str = "observation",
    ) -> PageState:
        app_name = app or self._infer_app(semantic_layout) or semantic_layout or "home_screen"
        visual_text = " ".join([semantic_layout, summary, self._elements_text(elements)])
        combined_text = " ".join([visual_text, task])
        inferred_type = page_type or self._infer_page_type(visual_text)

        element_landmarks = self._extract_landmarks_from_elements(elements)
        element_affordances = self._extract_affordances_from_elements(elements)
        landmarks = _dedupe((*self._infer_landmarks(inferred_type), *element_landmarks), limit=12)
        affordances = _dedupe((*self._infer_affordances(inferred_type), *element_affordances), limit=12)
        slots = self.extract_slots_from_text(combined_text)
        risk_level = self._infer_risk_level(inferred_type, combined_text)
        signature = self._semantic_signature(app_name, inferred_type, landmarks, affordances, slots)
        signature_slug = _safe_slug(signature)
        if state_id_strategy == "semantic":
            canonical_suffix = hashlib.md5(signature.encode("utf-8")).hexdigest()[:12]
            state_id = f"state_{signature_slug}_{canonical_suffix}"
        else:
            hash_suffix = (ui_hash or hashlib.md5(signature.encode("utf-8")).hexdigest())[:8]
            state_id = f"state_{signature_slug}_{hash_suffix}"
        return PageState(
            state_id=state_id,
            app=app_name,
            page_type=inferred_type,
            summary=summary or f"{app_name}:{inferred_type}",
            landmarks=landmarks,
            affordances=affordances,
            slots=slots,
            risk_level=risk_level,
            screenshot_hash=ui_hash,
            semantic_signature=signature,
        )

    def page_state_from_exploration_page(self, page: dict[str, Any], fallback_app: str = "") -> PageState:
        """Convert OfflineExplorer page JSON into a stable PageState."""
        app = str(page.get("app") or fallback_app or "")
        page_type = str(page.get("page_type") or "unknown")
        summary = str(page.get("summary") or "")
        elements = page.get("elements") if isinstance(page.get("elements"), dict) else {}
        semantic_layout = " ".join([app, page_type, summary])
        return self.build_page_state(
            ui_hash=str(page.get("screenshot_hash") or ""),
            semantic_layout=semantic_layout,
            task=summary,
            app=app or None,
            page_type=page_type if page_type else None,
            summary=summary,
            elements=elements,
            state_id_strategy="semantic",
        )

    def import_exploration_files(
        self,
        pages_path: str | Path,
        transitions_path: str | Path | None = None,
        *,
        persist: bool = True,
    ) -> ExplorationImportResult:
        """Import OfflineExplorer pages/transitions into local memory and Neo4j when available."""
        pages_file = Path(pages_path)
        transitions_file = Path(transitions_path) if transitions_path else self.match_transitions_path(pages_file)
        pages_data = self._read_json(pages_file)
        transitions_data = self._read_json(transitions_file) if transitions_file and transitions_file.exists() else {}
        app = str(pages_data.get("app") or transitions_data.get("app") or "")

        key_to_state: dict[str, PageState] = {}
        for page in pages_data.get("pages", []):
            if not isinstance(page, dict):
                continue
            state = self.page_state_from_exploration_page(page, fallback_app=app)
            self._local_states[state.state_id] = state
            key_to_state[self._transition_key(state.page_type, state.summary)] = state
            if persist:
                self._persist_page_state(state)

        transition_count = 0
        for item in transitions_data.get("transitions", []):
            if not isinstance(item, dict):
                continue
            source = self._state_for_transition_key(str(item.get("from") or ""), key_to_state, app)
            target = self._state_for_transition_key(str(item.get("to") or ""), key_to_state, app)
            action = item.get("action") if isinstance(item.get("action"), dict) else {"action": "unknown"}
            self.record_observation(source, action, target, outcome=str(item.get("outcome") or "success"))
            transition_count += 1

        return ExplorationImportResult(
            pages_imported=len(key_to_state),
            transitions_imported=transition_count,
            pages_path=str(pages_file),
            transitions_path=str(transitions_file) if transitions_file else "",
            unique_pages=len(self._local_states),
            persisted_to_graph=bool(persist and self.graph_store and getattr(self.graph_store, "driver", None)),
        )

    def import_exploration_staging(
        self,
        pages_path: str | Path,
        transitions_path: str | Path | None = None,
    ) -> tuple[dict[str, PageState], list[TransitionEdge], GraphQualityReport]:
        """Load exploration artifacts into a canonical staging graph.

        Staging is intentionally side-effect free. It canonicalizes pages,
        drops transient pages from the main graph, and filters unsafe/noisy
        transitions before anything is promoted to Neo4j.
        """
        pages_file = Path(pages_path)
        transitions_file = Path(transitions_path) if transitions_path else self.match_transitions_path(pages_file)
        pages_data = self._read_json(pages_file)
        transitions_data = self._read_json(transitions_file) if transitions_file and transitions_file.exists() else {}
        app = str(pages_data.get("app") or transitions_data.get("app") or "")

        raw_key_to_state: dict[str, PageState | None] = {}
        canonical_states = self.canonicalize_pages(pages_data.get("pages", []), fallback_app=app)
        canonical_by_key = {self._canonical_page_key(state): state for state in canonical_states.values()}
        app_mismatch_pages = 0
        for page in pages_data.get("pages", []):
            if not isinstance(page, dict):
                continue
            raw_state = self.page_state_from_exploration_page(page, fallback_app=app)
            raw_key = self._transition_key(raw_state.page_type, raw_state.summary)
            if not self._is_app_consistent(raw_state):
                app_mismatch_pages += 1
                raw_key_to_state[raw_key] = None
                continue
            raw_key_to_state[raw_key] = canonical_by_key.get(self._canonical_page_key(raw_state))

        promoted_edges: list[TransitionEdge] = []
        filtered = 0
        for item in transitions_data.get("transitions", []):
            if not isinstance(item, dict):
                continue
            source = raw_key_to_state.get(str(item.get("from") or ""))
            target = raw_key_to_state.get(str(item.get("to") or ""))
            action = item.get("action") if isinstance(item.get("action"), dict) else {"action": "unknown"}
            if not source or not target:
                filtered += 1
                continue
            edge = TransitionEdge.from_action(
                source.state_id,
                target.state_id,
                action,
                risk=target.risk_level,
                postcondition=target.page_type,
                outcome=str(item.get("outcome") or "success"),
            )
            if not self._is_promotable_edge(edge, source, target):
                filtered += 1
                continue
            promoted_edges.append(edge)

        covered = {(canonical_states[edge.source_id].page_type if edge.source_id in canonical_states else "", edge.postcondition) for edge in promoted_edges}
        target_edges = tuple(zip(_CORE_SHOPPING_FLOW, _CORE_SHOPPING_FLOW[1:]))
        missing = tuple(edge for edge in target_edges if edge not in covered)
        report = GraphQualityReport(
            pages_seen=len([p for p in pages_data.get("pages", []) if isinstance(p, dict)]),
            canonical_pages=len(canonical_states),
            transient_pages=len(raw_key_to_state) - len([state for state in raw_key_to_state.values() if state]),
            transitions_seen=len([t for t in transitions_data.get("transitions", []) if isinstance(t, dict)]),
            transitions_promoted=len(promoted_edges),
            transitions_filtered=filtered,
            app_mismatch_pages=app_mismatch_pages,
            missing_edges=missing,
        )
        return canonical_states, promoted_edges, report

    def canonicalize_pages(
        self,
        pages: Iterable[dict[str, Any]],
        *,
        fallback_app: str = "",
    ) -> dict[str, PageState]:
        """Collapse page variants into stable canonical PageState nodes."""
        canonical: dict[str, PageState] = {}
        for page in pages:
            if not isinstance(page, dict):
                continue
            state = self.page_state_from_exploration_page(page, fallback_app=fallback_app)
            if state.page_type in _TRANSIENT_PAGE_TYPES:
                continue
            if not self._is_app_consistent(state):
                continue
            key = self._canonical_page_key(state)
            if key in canonical:
                canonical[key] = self._merge_page_states(canonical[key], state)
                continue
            canonical[key] = self._canonical_page_state(state, key)
        return {state.state_id: state for state in canonical.values()}

    def canonicalize_state_graph(
        self,
        states: Iterable[PageState],
        edges_by_source: dict[str, list[TransitionEdge]] | None = None,
    ) -> tuple[dict[str, PageState], list[TransitionEdge], GraphQualityReport]:
        """Canonicalize an in-memory graph, preserving promotable transitions."""
        canonical_by_key: dict[str, PageState] = {}
        source_to_canonical: dict[str, PageState] = {}
        pages_seen = 0
        transient = 0
        app_mismatch = 0
        for state in states:
            pages_seen += 1
            if state.page_type in _TRANSIENT_PAGE_TYPES:
                transient += 1
                continue
            if not self._is_app_consistent(state):
                app_mismatch += 1
                continue
            key = self._canonical_page_key(state)
            if key in canonical_by_key:
                canonical_by_key[key] = self._merge_page_states(canonical_by_key[key], state)
            else:
                canonical_by_key[key] = self._canonical_page_state(state, key)
            source_to_canonical[state.state_id] = canonical_by_key[key]

        canonical_states = {state.state_id: state for state in canonical_by_key.values()}
        promoted_edges: list[TransitionEdge] = []
        filtered_edges = 0
        edges_seen = 0
        for source_id, edges in (edges_by_source or {}).items():
            source = source_to_canonical.get(source_id)
            for edge in edges:
                edges_seen += 1
                target = source_to_canonical.get(edge.target_id)
                if not source or not target:
                    filtered_edges += 1
                    continue
                canonical_edge = TransitionEdge(
                    source_id=source.state_id,
                    target_id=target.state_id,
                    action_type=edge.action_type,
                    action_target=edge.action_target,
                    action_params=edge.action_params,
                    precondition=edge.precondition,
                    postcondition=target.page_type,
                    success_count=edge.success_count,
                    fail_count=edge.fail_count,
                    rollback_action=edge.rollback_action,
                    cost=edge.cost,
                    risk=target.risk_level,
                    confidence=edge.confidence,
                    evidence=edge.evidence,
                )
                if not self._is_promotable_edge(canonical_edge, source, target):
                    filtered_edges += 1
                    continue
                promoted_edges.append(canonical_edge)

        report = GraphQualityReport(
            pages_seen=pages_seen,
            canonical_pages=len(canonical_states),
            transient_pages=transient,
            transitions_seen=edges_seen,
            transitions_promoted=len(promoted_edges),
            transitions_filtered=filtered_edges,
            app_mismatch_pages=app_mismatch,
        )
        return canonical_states, promoted_edges, report

    def promote_staging_to_canonical(
        self,
        canonical_states: dict[str, PageState],
        edges: Iterable[TransitionEdge],
        *,
        persist: bool = True,
    ) -> GraphQualityReport:
        """Promote a validated staging graph into local memory and Neo4j."""
        promoted_edges = 0
        filtered_edges = 0
        for state in canonical_states.values():
            self._local_states[state.state_id] = state
            if persist:
                self._persist_page_state(state)
        for edge in edges:
            source = canonical_states.get(edge.source_id)
            target = canonical_states.get(edge.target_id)
            if not source or not target or not self._is_promotable_edge(edge, source, target):
                filtered_edges += 1
                continue
            self._local_edges.setdefault(edge.source_id, []).append(edge)
            promoted_edges += 1
            if persist and self.graph_store and getattr(self.graph_store, "driver", None):
                self.graph_store.add_state_transition(
                    edge.source_id,
                    edge.target_id,
                    edge.action_params,
                    outcome="success" if edge.success_count >= edge.fail_count else "failure",
                    source_metadata=source.to_dict(),
                    target_metadata=target.to_dict(),
                )
        return GraphQualityReport(
            pages_seen=len(canonical_states),
            canonical_pages=len(canonical_states),
            transitions_seen=promoted_edges + filtered_edges,
            transitions_promoted=promoted_edges,
            transitions_filtered=filtered_edges,
        )

    def compile_task_dag(
        self,
        screen: dict[str, Any],
        task: str,
        *,
        policy: str = "optimistic",
    ) -> RuntimeDAG:
        """Compile a task-local in-memory DAG from the canonical graph."""
        belief = self.locate(screen, task)
        goal = self.infer_goal(task, app=screen.get("app") or screen.get("semantic_layout", ""))
        route = self.plan(belief, goal)
        app = ""
        if belief.candidates:
            app = belief.candidates[0].state.app
        plan_id = hashlib.md5(f"{task}|{belief.current_state_id}|{policy}".encode("utf-8")).hexdigest()[:12]
        dag = RuntimeDAG(plan_id=plan_id, app=app, goal_spec=goal)
        for candidate in belief.candidates:
            dag.nodes[candidate.state.state_id] = candidate.state

        if route.mode == "goal_reached":
            return dag
        if route.mode != "navigate" or not route.steps:
            dag.coverage_gaps.append(route.risk_summary or "no graph route")
            return dag

        for step in route.steps[:_TASK_DAG_PAGE_LIMIT]:
            edge = step.edge
            source = self._local_states.get(edge.source_id)
            target = self._local_states.get(edge.target_id)
            if source:
                dag.nodes[source.state_id] = source
            if target:
                dag.nodes[target.state_id] = target
            dag.edges.setdefault(edge.source_id, []).append(edge)
            dag.route.append(edge)
        return dag

    def next_planned_action(self, runtime_dag: RuntimeDAG) -> dict[str, Any] | None:
        edge = runtime_dag.next_edge()
        if not edge:
            return None
        if edge.risk == "high" or edge.postcondition in _HIGH_RISK_PAGE_TYPES:
            runtime_dag.coverage_gaps.append(f"high-risk boundary: {edge.postcondition}")
            return None
        return edge.to_next_action()

    def verify_planned_step(
        self,
        action_result: Any,
        screenshot: dict[str, Any] | None,
        expected_edge: TransitionEdge,
    ) -> VerificationResult:
        """Cheap runtime check for optimistic DAG execution."""
        if action_result is not None and getattr(action_result, "success", True) is False:
            return VerificationResult(False, getattr(action_result, "message", "action failed"), "replan")
        if expected_edge.risk == "high" or expected_edge.postcondition in _HIGH_RISK_PAGE_TYPES:
            return VerificationResult(False, f"high-risk boundary: {expected_edge.postcondition}", "ask_user")
        if screenshot and screenshot.get("app") and expected_edge.action_params.get("app"):
            expected_app = str(expected_edge.action_params.get("app"))
            if expected_app and expected_app != screenshot.get("app"):
                return VerificationResult(False, "app mismatch after planned action", "replan")
        return VerificationResult(True, "optimistic check passed", "continue")

    @staticmethod
    def match_transitions_path(pages_path: str | Path) -> Path:
        pages_file = Path(pages_path)
        name = pages_file.name.replace("_explore_", "_explore_transitions_")
        return pages_file.with_name(name)

    @staticmethod
    def extract_slots_from_text(text: str) -> dict[str, str]:
        slots: dict[str, str] = {}
        for slot, patterns in _SLOT_PATTERNS.items():
            for pattern in patterns:
                match = re.search(pattern, text, flags=re.IGNORECASE)
                if match:
                    slots[slot] = match.group(1).strip()
                    break
        return slots

    @staticmethod
    def positive_goal_clauses(task: str) -> tuple[str, ...]:
        """Keep negative safety constraints from becoming route targets."""
        negation_tokens = (
            "不要",
            "禁止",
            "别",
            "无需",
            "不需要",
            "不得",
            "do not",
            "don't",
            "avoid",
            "without",
        )
        clauses = re.split(r"[,，。.;；\n]+", task)
        positive = [clause.strip() for clause in clauses if clause.strip() and not _contains_any(clause, negation_tokens)]
        return tuple(positive or [task])

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
            app=str(screen.get("app") or "") or None,
            page_type=str(screen.get("page_type") or "") or None,
            summary=str(screen.get("summary") or ""),
            elements=screen.get("elements") if isinstance(screen.get("elements"), dict) else None,
        )
        self._local_states[page_state.state_id] = page_state

        graph_candidate = self._load_graph_candidate(page_state)
        if graph_candidate:
            self._local_states[graph_candidate.state_id] = graph_candidate
            candidates = [
                PageBeliefCandidate(
                    state=graph_candidate,
                    score=0.92,
                    reason="semantic graph localization",
                ),
                PageBeliefCandidate(
                    state=page_state,
                    score=0.82,
                    reason="current observation signature",
                ),
            ]
            current_state_id = graph_candidate.state_id
            is_novel = False
        else:
            candidates = [
                PageBeliefCandidate(
                    state=page_state,
                    score=1.0,
                    reason="current observation signature",
                )
            ]
            current_state_id = page_state.state_id
            is_novel = True

        candidates_tuple = tuple(sorted(candidates, key=lambda item: item.score, reverse=True))
        belief = PageBelief(
            current_state_id=current_state_id,
            candidates=candidates_tuple,
            confidence=candidates_tuple[0].score,
            is_novel=is_novel,
        )

        self._last_belief = belief
        return belief

    def infer_goal(self, task: str, app: str = "") -> GoalSpec:
        return GoalSpec.from_task(task, app)

    def plan(self, belief: PageBelief, goal_spec: GoalSpec) -> RoutePlan:
        start_id = belief.current_state_id
        if not goal_spec.target_page_types:
            route = RoutePlan(mode="explore", confidence=0.0, risk_summary="no goal page type", goal=goal_spec)
            print(f"[Plan Debug] No goal page type, returning explore mode")
            self._last_route = route
            return route

        start_state = self._local_states.get(start_id)
        allowed_app = start_state.app if start_state else ""
        print(f"[Plan Debug] start_id={start_id[:30]}, start_state={start_state is not None}, allowed_app={allowed_app}")
        if start_state and start_state.page_type in goal_spec.target_page_types:
            route = RoutePlan(
                mode="goal_reached",
                confidence=belief.confidence,
                risk_summary="current page satisfies goal",
                goal=goal_spec,
            )
            self._last_route = route
            return route

        graph_edges = self._load_edges(start_id, allowed_app=allowed_app)
        print(f"[Plan Debug] Loaded {len(graph_edges)} edges from graph")
        if not graph_edges:
            route = RoutePlan(
                mode="explore",
                confidence=belief.confidence,
                risk_summary="no graph route from current page",
                goal=goal_spec,
            )
            self._last_route = route
            return route

        best_path = self._shortest_path(start_id, goal_spec.target_page_types, allowed_app=allowed_app)
        print(f"[Plan Debug] Shortest path result: {len(best_path)} steps")
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
        medium_risk = [step.edge.risk for step in best_path if step.edge.risk == "medium"]
        route = RoutePlan(
            mode="navigate",
            steps=tuple(best_path),
            total_cost=total_cost,
            confidence=max(0.0, min(1.0, belief.confidence / max(1.0, total_cost))),
            risk_summary="high risk route" if high_risk else "medium risk route" if medium_risk else "normal route",
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
                f"risk={top.risk_level} confidence={belief.confidence:.2f} "
                f"landmarks={','.join(top.landmarks[:4])}"
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

    def _persist_page_state(self, state: PageState) -> None:
        if not self.graph_store or not getattr(self.graph_store, "driver", None):
            return
        try:
            self.graph_store.upsert_page_state(state.to_dict())
        except AttributeError:
            return

    def _state_for_transition_key(
        self,
        key: str,
        key_to_state: dict[str, PageState],
        app: str,
    ) -> PageState:
        if key in key_to_state:
            return key_to_state[key]

        page_type, _, summary = key.partition(":")
        state = self.build_page_state(
            ui_hash="",
            semantic_layout=" ".join([app, page_type, summary]),
            task=summary,
            app=app or None,
            page_type=page_type or None,
            summary=summary,
            state_id_strategy="semantic",
        )
        key_to_state[key] = state
        self._local_states[state.state_id] = state
        self._persist_page_state(state)
        return state

    @staticmethod
    def _transition_key(page_type: str, summary: str) -> str:
        return f"{page_type}:{summary}"

    @staticmethod
    def _canonical_page_key(state: PageState) -> str:
        landmarks = ",".join(sorted(state.landmarks))
        affordances = ",".join(sorted(state.affordances))
        return "|".join([state.app, state.page_type, landmarks, affordances, state.risk_level])

    def _canonical_page_state(self, state: PageState, canonical_key: str) -> PageState:
        state_id = f"state_{_safe_slug(canonical_key)}_{hashlib.md5(canonical_key.encode('utf-8')).hexdigest()[:12]}"
        semantic_signature = self._semantic_signature(
            state.app,
            state.page_type,
            state.landmarks,
            state.affordances,
            {},
        )
        return PageState(
            state_id=state_id,
            app=state.app,
            page_type=state.page_type,
            summary=state.summary,
            landmarks=state.landmarks,
            affordances=state.affordances,
            slots={},
            risk_level=state.risk_level,
            screenshot_hash=state.screenshot_hash,
            semantic_signature=semantic_signature,
        )

    def _merge_page_states(self, first: PageState, second: PageState) -> PageState:
        merged_landmarks = _dedupe((*first.landmarks, *second.landmarks), limit=12)
        merged_affordances = _dedupe((*first.affordances, *second.affordances), limit=12)
        summary = first.summary if len(first.summary) <= len(second.summary) else second.summary
        canonical_key = self._canonical_page_key(
            PageState(
                state_id=first.state_id,
                app=first.app,
                page_type=first.page_type,
                summary=summary,
                landmarks=merged_landmarks,
                affordances=merged_affordances,
                risk_level=first.risk_level,
            )
        )
        state_id = f"state_{_safe_slug(canonical_key)}_{hashlib.md5(canonical_key.encode('utf-8')).hexdigest()[:12]}"
        return PageState(
            state_id=state_id,
            app=first.app,
            page_type=first.page_type,
            summary=summary,
            landmarks=merged_landmarks,
            affordances=merged_affordances,
            slots={},
            risk_level=first.risk_level,
            screenshot_hash=first.screenshot_hash or second.screenshot_hash,
            semantic_signature=self._semantic_signature(first.app, first.page_type, merged_landmarks, merged_affordances, {}),
        )

    def _is_promotable_edge(
        self,
        edge: TransitionEdge,
        source_state: PageState | None,
        target_state: PageState | None,
    ) -> bool:
        if not source_state or not target_state:
            return False
        if source_state.app != target_state.app:
            return False
        if source_state.page_type in _TRANSIENT_PAGE_TYPES or target_state.page_type in _TRANSIENT_PAGE_TYPES:
            return False
        if edge.confidence < 0.3:
            return False
        if edge.risk == "high" or target_state.page_type in _HIGH_RISK_PAGE_TYPES:
            # High-risk pages can be represented as nodes, but their incoming
            # actions are not promoted as executable shortcut edges.
            return False
        return self._is_plausible_transition(edge, source_state, target_state)

    @staticmethod
    def _is_app_consistent(state: PageState) -> bool:
        app = state.app.strip()
        if not app:
            return True
        allowed = _APP_ALIASES.get(app, {app.lower()})
        text = f"{state.summary} {state.semantic_signature}".lower()
        mentioned = {token for token in _APP_MENTION_TOKENS if token.lower() in text}
        if not mentioned:
            return True
        return all(token.lower() in {item.lower() for item in allowed} for token in mentioned)

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}

    def _load_graph_candidate(self, page_state: PageState) -> PageState | None:
        if not self.graph_store or not getattr(self.graph_store, "driver", None):
            return None
        try:
            data = self.graph_store.get_state_by_semantic(page_state.semantic_signature)
        except Exception:
            data = None
        if data:
            return self._page_state_from_graph(data, fallback=page_state)

        try:
            candidates = self.graph_store.find_page_state_candidates(
                app=page_state.app,
                page_type=page_state.page_type,
                limit=20,
            )
        except AttributeError:
            candidates = []
        except Exception:
            candidates = []

        best_state: PageState | None = None
        best_score = 0.0
        for candidate in candidates:
            candidate_state = self._page_state_from_graph(candidate, fallback=page_state)
            score = self._page_similarity(page_state, candidate_state)
            if score > best_score:
                best_score = score
                best_state = candidate_state
        return best_state if best_score >= 0.65 else None

    @staticmethod
    def _page_similarity(observed: PageState, candidate: PageState) -> float:
        score = 0.0
        if observed.app and observed.app == candidate.app:
            score += 0.35
        if observed.page_type and observed.page_type == candidate.page_type:
            score += 0.35
        observed_landmarks = set(observed.landmarks)
        candidate_landmarks = set(candidate.landmarks)
        if observed_landmarks or candidate_landmarks:
            score += 0.2 * (len(observed_landmarks & candidate_landmarks) / max(1, len(observed_landmarks | candidate_landmarks)))
        observed_affordances = set(observed.affordances)
        candidate_affordances = set(candidate.affordances)
        if observed_affordances or candidate_affordances:
            score += 0.1 * (len(observed_affordances & candidate_affordances) / max(1, len(observed_affordances | candidate_affordances)))
        return score

    def _load_edges(self, state_id: str, allowed_app: str = "") -> list[TransitionEdge]:
        local_edges = []
        for edge in self._local_edges.get(state_id, []):
            target_state = self._local_states.get(edge.target_id)
            if allowed_app and target_state and target_state.app != allowed_app:
                continue
            local_edges.append(edge)
        graph_edges: list[TransitionEdge] = []
        if self.graph_store and getattr(self.graph_store, "driver", None):
            try:
                graph_edges = self.graph_store.get_outgoing_transitions(state_id, app=allowed_app)
            except AttributeError:
                graph_edges = []
            except Exception:
                graph_edges = []

        edges = local_edges + graph_edges

        # Heuristic rule: Inject "加入购物车/立即购买" edges for product_detail pages
        # when graph lacks proper spec_selection transitions
        current_state = self._local_states.get(state_id)
        if current_state and current_state.page_type == "product_detail":
            # Check if any edge leads to spec_selection with correct action
            has_spec_selection_edge = any(
                edge.postcondition == "spec_selection" and
                "购物车" in edge.action_target or "购买" in edge.action_target or
                "cart" in edge.action_target.lower() or "buy" in edge.action_target.lower()
                for edge in edges
            )

            if not has_spec_selection_edge:
                # Inject heuristic edge: product_detail → spec_selection
                # This compensates for missing knowledge in the graph
                heuristic_edge = TransitionEdge(
                    source_id=state_id,
                    target_id=f"{state_id}_heuristic_spec",
                    action_type="Tap",
                    action_target="加入购物车按钮",
                    action_params={},
                    postcondition="spec_selection",
                    success_count=10,  # High prior confidence
                    fail_count=0,
                    risk="normal",
                    confidence=0.70,  # Below graph edges but above explore threshold
                )
                edges.append(heuristic_edge)

                # Also inject "立即购买" option
                heuristic_edge2 = TransitionEdge(
                    source_id=state_id,
                    target_id=f"{state_id}_heuristic_spec",
                    action_type="Tap",
                    action_target="立即购买按钮",
                    action_params={},
                    postcondition="spec_selection",
                    success_count=10,
                    fail_count=0,
                    risk="normal",
                    confidence=0.70,
                )
                edges.append(heuristic_edge2)

        return edges

    def _shortest_path(self, start_id: str, target_page_types: tuple[str, ...], allowed_app: str = "") -> list[RouteStep]:
        counter = 0
        queue: list[tuple[float, int, str, list[TransitionEdge]]] = [(0.0, counter, start_id, [])]
        best_cost: dict[str, float] = {start_id: 0.0}
        visited: set[str] = set()

        while queue:
            cost, _, state_id, path = heapq.heappop(queue)
            if state_id in visited:
                continue
            visited.add(state_id)

            state = self._local_states.get(state_id)
            if state and state.page_type in target_page_types and path:
                return [RouteStep(edge=edge) for edge in path]

            for edge in self._load_edges(state_id, allowed_app=allowed_app):
                source_state = self._local_states.get(state_id)
                target_state = self._local_states.get(edge.target_id)
                if allowed_app and target_state and target_state.app != allowed_app:
                    continue
                if not self._is_plausible_transition(edge, source_state, target_state):
                    continue
                if edge.postcondition in target_page_types:
                    return [RouteStep(edge=item) for item in path + [edge]]
                next_cost = cost + edge.weighted_cost
                if next_cost >= best_cost.get(edge.target_id, float("inf")):
                    continue
                best_cost[edge.target_id] = next_cost
                counter += 1
                heapq.heappush(queue, (next_cost, counter, edge.target_id, path + [edge]))

        return []

    @staticmethod
    def _is_plausible_transition(
        edge: TransitionEdge,
        source_state: PageState | None,
        target_state: PageState | None,
    ) -> bool:
        action_text = " ".join(
            [
                edge.action_type,
                edge.action_target,
            ]
        ).lower()
        target_page_type = target_state.page_type if target_state else edge.postcondition
        source_page_type = source_state.page_type if source_state else ""

        if edge.action_type.lower() in {"type", "input"}:
            # Historic typed text is a task slot value, not a reusable spatial shortcut.
            return False

        cart_tokens = ("cart", "add_to_cart", "add cart", "购物车", "加购", "加入购物车", "加到购物车")
        spec_tokens = ("spec", "sku", "规格", "选择规格", "buy", "购买", "加购", "加入购物车", "add_to_cart", "商品", "item", "product")
        checkout_tokens = ("checkout", "submit_order", "结算", "提交订单", "确认订单")
        payment_tokens = ("pay", "payment", "支付", "付款")

        # Context-aware filtering: allow reasonable transitions
        if target_page_type == "cart":
            # Allow from product_detail or spec_selection (reasonable add-to-cart flows)
            if source_page_type in ("product_detail", "spec_selection"):
                return True
            # Other cases still need keyword validation
            if not _contains_any(action_text, cart_tokens):
                return False

        if target_page_type == "spec_selection":
            # Allow from product_detail or search_result (clicking product enters spec page)
            if source_page_type in ("product_detail", "search_result"):
                return True
            # Other cases still need keyword validation
            if not _contains_any(action_text, spec_tokens):
                return False

        if target_page_type == "checkout" and not _contains_any(action_text, checkout_tokens):
            return False
        if target_page_type == "payment" and not _contains_any(action_text, payment_tokens):
            return False
        if source_page_type == "search_result" and target_page_type in {"cart", "checkout", "payment"}:
            return False
        return True

    def _page_state_from_graph(self, data: dict[str, Any], fallback: PageState) -> PageState:
        slots = data.get("slots") or fallback.slots
        if isinstance(slots, str):
            try:
                slots = json.loads(slots)
            except json.JSONDecodeError:
                slots = fallback.slots
        return PageState(
            state_id=str(data.get("state_id") or fallback.state_id),
            app=str(data.get("app") or data.get("app_name") or fallback.app),
            page_type=str(data.get("page_type") or fallback.page_type),
            summary=str(data.get("summary") or data.get("semantic_layout") or fallback.summary),
            landmarks=tuple(data.get("landmarks") or fallback.landmarks),
            affordances=tuple(data.get("affordances") or fallback.affordances),
            slots=dict(slots or {}),
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
        slots: dict[str, str] | None = None,
    ) -> str:
        slot_text = ",".join(f"{key}={value}" for key, value in sorted((slots or {}).items()))
        return "|".join(
            [
                app or "unknown",
                page_type or "unknown",
                ",".join(landmarks),
                ",".join(affordances),
                slot_text,
            ]
        )

    def _infer_app(self, text: str) -> str:
        for app in _SHOPPING_APPS:
            if app and app in text:
                return app
        return ""

    def _infer_page_type(self, text: str) -> str:
        extra_keywords = [
            ("spec_selection", ("spec", "sku", "规格", "型号", "颜色", "尺码", "数量", "确定")),
            ("checkout", ("checkout", "order", "提交订单", "确认订单", "结算", "收货地址")),
            ("payment", ("pay", "payment", "付款", "支付", "收银台", "确认付款")),
            ("cart", ("cart", "购物车", "加入购物车", "去结算", "结算按钮")),
            ("product_detail", ("detail", "product_detail", "商品详情", "详情页", "价格", "立即购买")),
            ("search_result", ("result", "search_result", "搜索结果", "商品列表", "筛选", "综合排序")),
            ("search_input", ("search_input", "搜索页", "搜索框", "搜索输入", "历史搜索", "键盘")),
            ("home", ("home", "首页", "推荐", "搜索栏", "底部导航栏")),
            ("login", ("login", "登录", "验证码", "账号", "手机号")),
            ("address", ("address", "地址", "收货人", "配送地址")),
            ("refund", ("refund", "return", "退款", "退货", "售后", "申请退款")),
            ("order_list", ("order list", "orders", "我的订单", "订单列表", "全部订单", "待付款", "待发货")),
            ("my_account", ("my account", "profile", "我的", "我的淘宝", "个人中心", "用户信息")),
            ("category", ("category", "类目", "分类", "品类", "分类页")),
            ("store", ("store", "shop", "店铺", "旗舰店", "门店", "店首页")),
        ]
        for page_type, keywords in extra_keywords:
            if _contains_any(text, keywords):
                return page_type
        for page_type, keywords in _PAGE_TYPE_KEYWORDS:
            if _contains_any(text, keywords):
                return page_type
        return "unknown"

    def _infer_risk_level(self, page_type: str, task: str) -> str:
        if page_type in _HIGH_RISK_PAGE_TYPES:
            return "high"
        if page_type in _MEDIUM_RISK_PAGE_TYPES:
            return "medium"
        if _contains_any(task, ("付款", "支付", "提交订单", "confirm order")):
            return "high"
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
            "address": ("address_list", "confirm_button"),
            "my_account": ("user_profile", "order_shortcuts", "bottom_tabs"),
            "order_list": ("order_cards", "order_tabs", "search_or_filter"),
            "refund": ("refund_reason", "refund_amount", "submit_button"),
            "category": ("category_tabs", "category_grid", "product_cards"),
            "store": ("store_header", "store_search", "product_sections"),
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
            "address": ("choose_address", "back", "ask_user"),
            "my_account": ("open_orders", "open_profile_section", "back"),
            "order_list": ("open_order", "filter_order", "back"),
            "refund": ("choose_refund_reason", "submit_refund", "ask_user", "back"),
            "category": ("open_category", "open_product", "scroll", "back"),
            "store": ("store_search", "open_product", "scroll", "back"),
        }
        return mapping.get(page_type, ())

    @staticmethod
    def _extract_landmarks_from_elements(elements: dict[str, Any] | None) -> tuple[str, ...]:
        if not elements:
            return ()
        return _dedupe(elements.keys(), limit=10)

    @staticmethod
    def _extract_affordances_from_elements(elements: dict[str, Any] | None) -> tuple[str, ...]:
        if not elements:
            return ()
        affordances: list[str] = []
        for name, desc in elements.items():
            text = f"{name} {desc}".lower()
            for hint, affordance in _ELEMENT_AFFORDANCE_HINTS.items():
                if hint in text:
                    affordances.append(affordance)
        return _dedupe(affordances, limit=10)

    @staticmethod
    def _elements_text(elements: dict[str, Any] | None) -> str:
        if not elements:
            return ""
        parts: list[str] = []
        for name, desc in elements.items():
            parts.append(str(name))
            parts.append(str(desc))
        return " ".join(parts)
