"""Training-free spatial page graph memory for mobile GUI agents.

This module sits above GraphStore. It turns low-level graph storage into a
compact interface for page localization, route planning, observation recording,
offline exploration import, and route repair. The implementation is deliberately
deterministic so it can reuse existing GUI/VLM models without extra training.
"""

from __future__ import annotations

import ast
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
    "淘宝": {"淘宝", "天猫", "娣樺疂", "澶╃尗", "taobao", "tmall"},
    "天猫": {"淘宝", "天猫", "娣樺疂", "澶╃尗", "taobao", "tmall"},
    "京东": {"京东", "浜笢", "jd", "jingdong"},
    "拼多多": {"拼多多", "鎷煎澶?", "pinduoduo"},
    "美团": {"美团", "缇庡洟", "meituan"},
    "饿了么": {"饿了么", "楗夸簡涔?", "eleme"},
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
    ("filter_panel", ("filter_panel", "全部筛选", "筛选选项", "价格区间", "自定最低价", "自定最高价")),
    ("cart", ("cart", "购物车", "加入购物车", "去结算")),
    ("product_detail", ("detail", "product_detail", "商品详情", "详情", "价格", "立即购买")),
    ("search_result", ("result", "search_result", "搜索结果", "商品列表", "筛选", "综合")),
    ("search_input", ("search_input", "搜索框", "搜索输入", "历史搜索", "猜你想搜")),
    ("home", ("home", "首页", "推荐", "搜索栏")),
    ("login", ("login", "登录", "验证码", "账号")),
    ("address", ("address", "地址", "收货人")),
]

_GOAL_PAGE_KEYWORDS = [
    # "加入购物车" / "加购" → the action of adding to cart happens on
    # the spec_selection page, NOT by navigating to the cart page (which
    # is just viewing what's already there).
    ("spec_selection", ("加入购物车", "加购", "加到购物车", "add to cart", "add cart", "立即购买", "购买", "下单")),
    ("cart", ("购物车", "查看购物车", "去购物车", "view cart", "cart")),
    ("checkout", ("结算", "提交订单", "确认订单", "checkout")),
    ("product_detail", ("商品详情", "详情", "product", "detail")),
    ("search_result", ("搜索", "搜索结果", "search", "result")),
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

# Slot extraction is now provided by phone_agent.core.task_spec.TaskSpecExtractor.
# _SLOT_PATTERNS removed — use TaskSpecExtractor.extract(text).slot_dict instead.


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
        evidence = str(action.get("summary") or action.get("reasoning") or "")
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
            evidence=evidence,
        )

    @property
    def weighted_cost(self) -> float:
        attempts = max(1, self.success_count + self.fail_count)
        fail_rate = self.fail_count / attempts
        risk_penalty = {"normal": 0.0, "medium": 0.8, "high": 2.0}.get(self.risk, 0.5)
        confidence_bonus = max(0.0, min(1.0, self.confidence))
        return self.cost + fail_rate * 3.0 + risk_penalty - confidence_bonus * 0.3

    def to_next_action(self) -> dict[str, Any]:
        result = {
            "type": self.action_type,
            "target": self.action_target,
            "target_desc": repr(self.action_params),
            "confidence": self.confidence,
            "source_state_id": self.source_id,
            "target_state_id": self.target_id,
            "postcondition": self.postcondition,
            "risk": self.risk,
            "reasoning": self.evidence,
        }
        # Pass coordinate locator for direct grounding without VLM
        for key in ("element", "coordinate", "bbox"):
            if key in self.action_params:
                result["target_locator"] = {
                    key: self.action_params[key],
                    "coordinate_space": self.action_params.get("coordinate_space", "normalized_1000"),
                }
                break
        return result

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
        elif "spec_selection" in target_page_types:
            # "加入购物车" / "购买" → navigate to spec_selection where the
            # add-to-cart / buy button actually lives (NOT the cart icon).
            target_page_types = ["spec_selection"]
        elif "cart" in target_page_types:
            target_page_types = ["cart"]
        if not target_page_types:
            target_page_types = ["search_result", "product_detail", "spec_selection"]

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
    transitions_compacted: int = 0
    compound_edges: int = 0
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
            "transitions_compacted": self.transitions_compacted,
            "compound_edges": self.compound_edges,
            "app_mismatch_pages": self.app_mismatch_pages,
            "missing_edges": [list(edge) for edge in self.missing_edges],
        }


@dataclass(frozen=True)
class _StagingTransition:
    source: PageState
    target: PageState
    action: dict[str, Any]
    outcome: str = "success"
    raw_count: int = 1


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
        """Import OfflineExplorer pages/transitions through the canonical staging graph."""
        pages_file = Path(pages_path)
        transitions_file = Path(transitions_path) if transitions_path else self.match_transitions_path(pages_file)
        states, edges, quality = self.import_exploration_staging(pages_file, transitions_file)
        promote_report = self.promote_staging_to_canonical(states, edges, persist=persist)

        return ExplorationImportResult(
            pages_imported=quality.pages_seen,
            transitions_imported=promote_report.transitions_promoted,
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

        raw_steps: list[_StagingTransition] = []
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
            raw_steps.append(
                _StagingTransition(
                    source=source,
                    target=target,
                    action=dict(action),
                    outcome=str(item.get("outcome") or "success"),
                )
            )

        trajectory_file = self.match_trajectory_path(pages_file)
        trajectory_data = self._read_json(trajectory_file) if trajectory_file.exists() else {}
        raw_steps, synthesized_compound_count = self._synthesize_search_compound_steps(
            raw_steps,
            trajectory_data,
        )
        compacted_steps, compacted_raw_count, dropped_self_loops = self._compact_consecutive_page_actions(raw_steps)
        compacted_raw_count += synthesized_compound_count
        filtered += dropped_self_loops

        promoted_edges: list[TransitionEdge] = []
        compound_edges = 0
        for step in compacted_steps:
            edge = TransitionEdge.from_action(
                step.source.state_id,
                step.target.state_id,
                step.action,
                risk=step.target.risk_level,
                postcondition=step.target.page_type,
                outcome=step.outcome,
            )
            if not self._is_promotable_edge(edge, step.source, step.target):
                filtered += step.raw_count
                continue
            if edge.action_type == "Compound":
                compound_edges += 1
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
            transitions_compacted=compacted_raw_count,
            compound_edges=compound_edges,
            app_mismatch_pages=app_mismatch_pages,
            missing_edges=missing,
        )
        return canonical_states, promoted_edges, report

    def _synthesize_search_compound_steps(
        self,
        steps: list[_StagingTransition],
        trajectory_data: dict[str, Any],
    ) -> tuple[list[_StagingTransition], int]:
        """Recover slot-aware search macros from full trajectories.

        OfflineExplorer can occasionally classify the active input screen as
        unknown while the GUI model is typing. The transitions file then keeps
        only the final submit tap. The trajectory still contains the typed
        value, so staging can reconstruct the reusable template:
        Type <query> -> submit.
        """
        if not steps:
            return steps, 0
        search_compounds = self._search_compound_actions_from_trajectory(trajectory_data)
        if not search_compounds:
            return steps, 0

        remaining = list(search_compounds)
        synthesized = 0
        repaired: list[_StagingTransition] = []
        for step in steps:
            if (
                remaining
                and step.source.page_type == "search_input"
                and step.target.page_type == "search_result"
                and str(step.action.get("action") or step.action.get("action_type") or "").lower() in {"tap", "click"}
            ):
                compound_action = remaining.pop(0)
                repaired.append(
                    _StagingTransition(
                        source=step.source,
                        target=step.target,
                        action=compound_action,
                        outcome=step.outcome,
                        raw_count=2,
                    )
                )
                synthesized += 1
                continue
            repaired.append(step)
        return repaired, synthesized

    def _search_compound_actions_from_trajectory(self, trajectory_data: dict[str, Any]) -> list[dict[str, Any]]:
        steps = trajectory_data.get("steps")
        if not isinstance(steps, list):
            return []

        compounds: list[dict[str, Any]] = []
        for index, item in enumerate(steps):
            if not isinstance(item, dict):
                continue
            action = item.get("action") if isinstance(item.get("action"), dict) else {}
            action_type = str(action.get("action") or action.get("action_type") or "").lower()
            if action_type not in {"tap", "click"}:
                continue
            if str(item.get("page_type") or "") != "search_input":
                continue

            type_action = self._nearest_prior_type_action(steps, index)
            if not type_action:
                continue
            submit_action = dict(action)
            submit_action.setdefault("_metadata", "do")
            compound = self._make_compound_action([type_action, submit_action], page_type="search_input")
            compound["semantic_target"] = "submit_search"
            compound["expected_postcondition"] = "search_result"
            compound["source_kind"] = "trajectory_synthesis"
            compound["summary"] = "slot-aware search submit synthesized from trajectory"
            compounds.append(compound)
        return compounds

    @staticmethod
    def _nearest_prior_type_action(steps: list[Any], submit_index: int) -> dict[str, Any] | None:
        for prior in range(submit_index - 1, max(-1, submit_index - 4), -1):
            item = steps[prior]
            if not isinstance(item, dict):
                continue
            action = item.get("action") if isinstance(item.get("action"), dict) else {}
            action_type = str(action.get("action") or action.get("action_type") or "").lower()
            if action_type not in {"type", "type_name", "input"}:
                continue
            page_type = str(item.get("page_type") or "")
            if page_type not in {"search_input", "unknown", ""}:
                continue
            if not str(action.get("text") or "").strip():
                continue
            normalized = dict(action)
            normalized.setdefault("_metadata", "do")
            return normalized
        return None

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

    def _compact_consecutive_page_actions(
        self,
        steps: Iterable[_StagingTransition],
    ) -> tuple[list[_StagingTransition], int, int]:
        """Fold same-page action runs into one edge that exits the page."""
        compacted: list[_StagingTransition] = []
        pending_source: PageState | None = None
        pending_actions: list[dict[str, Any]] = []
        pending_outcomes: list[str] = []
        compacted_raw_count = 0
        dropped_self_loops = 0

        for step in steps:
            is_self_loop = step.source.state_id == step.target.state_id
            if is_self_loop:
                if pending_source and pending_source.state_id != step.source.state_id:
                    dropped_self_loops += len(pending_actions)
                    pending_actions = []
                    pending_outcomes = []
                pending_source = step.source
                pending_actions.append(dict(step.action))
                pending_outcomes.append(step.outcome)
                continue

            if pending_source and pending_source.state_id == step.source.state_id:
                actions = [*pending_actions, dict(step.action)]
                outcomes = [*pending_outcomes, step.outcome]
                compound_action = self._make_compound_action(actions, page_type=step.source.page_type)
                compacted.append(
                    _StagingTransition(
                        source=pending_source,
                        target=step.target,
                        action=compound_action,
                        outcome="failure" if any(outcome == "failure" for outcome in outcomes) else "success",
                        raw_count=len(actions),
                    )
                )
                compacted_raw_count += len(actions) - 1
                pending_source = None
                pending_actions = []
                pending_outcomes = []
                continue

            if pending_actions:
                dropped_self_loops += len(pending_actions)
                pending_source = None
                pending_actions = []
                pending_outcomes = []
            compacted.append(step)

        if pending_actions:
            dropped_self_loops += len(pending_actions)
        return compacted, compacted_raw_count, dropped_self_loops

    def _make_compound_action(self, actions: list[dict[str, Any]], *, page_type: str) -> dict[str, Any]:
        normalized_actions: list[dict[str, Any]] = []
        runtime_slots: list[str] = []
        type_index = 0
        for action in actions:
            normalized = dict(action)
            normalized.setdefault("_metadata", "do")
            action_name = str(normalized.get("action_type") or normalized.get("action") or "")
            if action_name.lower() in {"type", "type_name", "input"}:
                slot = self._compound_input_slot(page_type, type_index)
                type_index += 1
                original_text = str(normalized.get("text") or "")
                normalized["text"] = f"<{slot}>"
                if original_text:
                    normalized["example_text"] = original_text
                runtime_slots.append(slot)
            normalized_actions.append(normalized)

        labels = [self._action_label(action) for action in normalized_actions]
        return {
            "_metadata": "do",
            "action": "Compound",
            "actions": normalized_actions,
            "semantic_target": " -> ".join(labels),
            "compound": True,
            "action_count": len(normalized_actions),
            "requires_runtime_input": bool(runtime_slots),
            "runtime_slots": list(_dedupe(runtime_slots)),
            # Slot-bearing macros are route templates. Keep them below the
            # direct-execution threshold so the VLM can fill task-specific text.
            "confidence": 0.65 if runtime_slots else 1.0,
        }

    @staticmethod
    def _compound_input_slot(page_type: str, type_index: int) -> str:
        if page_type == "search_input":
            return "query"
        if page_type == "filter_panel":
            if type_index == 0:
                return "min_price"
            if type_index == 1:
                return "max_price"
            return f"filter_input_{type_index + 1}"
        return "input_text" if type_index == 0 else f"input_text_{type_index + 1}"

    @staticmethod
    def _action_label(action: dict[str, Any]) -> str:
        action_name = str(action.get("action_type") or action.get("action") or "unknown")
        if action.get("semantic_target"):
            return f"{action_name}:{action['semantic_target']}"
        if action_name.lower() in {"type", "type_name", "input"}:
            return f"{action_name}:{action.get('text', '')}"
        if action.get("element") is not None:
            return f"{action_name}:{action['element']}"
        if action.get("start") is not None and action.get("end") is not None:
            return f"{action_name}:{action['start']}->{action['end']}"
        return action_name

    def _enrich_edge_action_params(
        self,
        edge: TransitionEdge,
        source: PageState,
        target: PageState,
    ) -> dict[str, Any]:
        """Attach model-agnostic semantic identity while preserving raw evidence."""
        params = dict(edge.action_params or {})
        action_type = str(params.get("action_type") or params.get("action") or edge.action_type or "unknown")
        intent = str(params.get("intent") or self._intent_from_action_type(action_type))
        region = str(params.get("region") or self._action_region(params) or "")
        coordinate_only = any(key in params for key in ("element", "coordinate", "point"))
        semantic_target = str(params.get("semantic_target") or params.get("target") or "").strip()
        if not semantic_target and not coordinate_only:
            semantic_target = str(edge.action_target or "").strip()
        if not semantic_target:
            if region:
                semantic_target = f"{region} {target.page_type} affordance"
            else:
                semantic_target = f"{target.page_type} affordance"
        params.setdefault("_metadata", "do")
        params["action"] = str(params.get("action") or action_type)
        params["intent"] = intent
        params["semantic_target"] = semantic_target
        params["expected_postcondition"] = target.page_type
        params["source_page_type"] = source.page_type
        params["target_page_type"] = target.page_type
        params["risk_level"] = target.risk_level
        params["region"] = region
        if "target_locator" not in params:
            params["target_locator"] = self._target_locator_from_action(params)
        params["_semantic_edge_key"] = "|".join(
            [source.page_type, intent, semantic_target, region, target.page_type]
        )
        return params

    @staticmethod
    def _intent_from_action_type(action_type: str) -> str:
        lowered = action_type.lower()
        if lowered in {"tap", "click"}:
            return "tap"
        if lowered in {"type", "type_name", "input"}:
            return "type_text"
        if lowered == "swipe":
            return "scroll"
        if lowered == "back":
            return "go_back"
        if lowered == "compound":
            return "compound"
        return lowered or "unknown"

    @staticmethod
    def _target_locator_from_action(action: dict[str, Any]) -> dict[str, Any]:
        if "element" in action:
            return {"element": action["element"], "coordinate_space": "normalized_1000"}
        if "coordinate" in action:
            return {"coordinate": action["coordinate"], "coordinate_space": "normalized_999"}
        if "point" in action:
            return {"point": action["point"]}
        return {}

    @staticmethod
    def _action_region(action: dict[str, Any]) -> str:
        value = action.get("element") or action.get("coordinate") or action.get("point")
        if isinstance(value, list) and len(value) == 1 and isinstance(value[0], list):
            value = value[0]
        if not isinstance(value, list) or len(value) < 2:
            return ""
        try:
            x = float(value[0])
            y = float(value[1])
        except (TypeError, ValueError):
            return ""
        vertical = "top" if y <= 250 else "bottom" if y >= 750 else "middle"
        horizontal = "left" if x <= 330 else "right" if x >= 670 else "center"
        if vertical == "middle" and horizontal == "center":
            return "center"
        return f"{vertical}_{horizontal}"

    @staticmethod
    def _edge_identity_key(edge: TransitionEdge) -> tuple[str, str, str, str]:
        params = edge.action_params or {}
        semantic_key = str(params.get("_semantic_edge_key") or "")
        return (edge.source_id, edge.target_id, edge.action_type, semantic_key or edge.action_target)

    def _upsert_local_edge(self, edge: TransitionEdge) -> bool:
        edges = self._local_edges.setdefault(edge.source_id, [])
        identity = self._edge_identity_key(edge)
        for index, existing in enumerate(edges):
            if self._edge_identity_key(existing) != identity:
                continue
            attempts = existing.success_count + existing.fail_count + edge.success_count + edge.fail_count
            confidence = (
                ((existing.confidence * max(1, existing.success_count + existing.fail_count)) + (edge.confidence * max(1, edge.success_count + edge.fail_count)))
                / max(1, attempts)
            )
            merged_params = dict(existing.action_params or {})
            merged_params.update(edge.action_params or {})
            edges[index] = TransitionEdge(
                source_id=existing.source_id,
                target_id=existing.target_id,
                action_type=existing.action_type,
                action_target=existing.action_target or edge.action_target,
                action_params=merged_params,
                precondition=existing.precondition or edge.precondition,
                postcondition=existing.postcondition or edge.postcondition,
                success_count=existing.success_count + edge.success_count,
                fail_count=existing.fail_count + edge.fail_count,
                rollback_action=existing.rollback_action or edge.rollback_action,
                cost=min(existing.cost, edge.cost),
                risk=existing.risk or edge.risk,
                confidence=round(confidence, 4),
                evidence=" | ".join(_dedupe([existing.evidence, edge.evidence])),
            )
            return False
        edges.append(edge)
        return True

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
        compound_edges = 0
        edge_list, preferred_filtered = self._prefer_slot_compound_edges(list(edges))
        filtered_edges += preferred_filtered
        state_id_map: dict[str, str] = {}
        resolved_states: dict[str, PageState] = {}
        for state in canonical_states.values():
            resolved = state
            if persist:
                existing = self._load_page_type_graph_candidate(state)
                if existing:
                    resolved = self._merge_into_existing_page_state(existing, state)
            state_id_map[state.state_id] = resolved.state_id
            resolved_states[resolved.state_id] = resolved
            self._local_states[resolved.state_id] = resolved
            if persist:
                self._persist_page_state(resolved)
        for edge in edge_list:
            source_id = state_id_map.get(edge.source_id, edge.source_id)
            target_id = state_id_map.get(edge.target_id, edge.target_id)
            source = resolved_states.get(source_id)
            target = resolved_states.get(target_id)
            if not source or not target or not self._is_promotable_edge(edge, source, target):
                filtered_edges += 1
                continue
            action_params = self._enrich_edge_action_params(edge, source, target)
            resolved_edge = TransitionEdge(
                source_id=source.state_id,
                target_id=target.state_id,
                action_type=edge.action_type,
                action_target=str(action_params.get("semantic_target") or edge.action_target),
                action_params=action_params,
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
            is_new_edge = self._upsert_local_edge(resolved_edge)
            if is_new_edge:
                promoted_edges += 1
            if resolved_edge.action_type == "Compound":
                compound_edges += 1
            if persist and self.graph_store and getattr(self.graph_store, "driver", None):
                self.graph_store.add_state_transition(
                    resolved_edge.source_id,
                    resolved_edge.target_id,
                    resolved_edge.action_params,
                    outcome="success" if resolved_edge.success_count >= resolved_edge.fail_count else "failure",
                    source_metadata=source.to_dict(),
                    target_metadata=target.to_dict(),
                )
        return GraphQualityReport(
            pages_seen=len(canonical_states),
            canonical_pages=len(resolved_states),
            transitions_seen=promoted_edges + filtered_edges,
            transitions_promoted=promoted_edges,
            transitions_filtered=filtered_edges,
            compound_edges=compound_edges,
        )

    def _prefer_slot_compound_edges(self, edges: list[TransitionEdge]) -> tuple[list[TransitionEdge], int]:
        """Prefer reusable slot macros over coordinate-only submit taps."""
        search_compound_pairs = {
            (edge.source_id, edge.target_id)
            for edge in edges
            if self._is_slot_search_compound(edge)
        }
        explicit_semantic_keys = {
            (edge.source_id, edge.target_id, edge.action_type.lower())
            for edge in edges
            if self._is_explicit_semantic_edge(edge)
        }
        if not search_compound_pairs and not explicit_semantic_keys:
            return edges, 0

        preferred: list[TransitionEdge] = []
        filtered = 0
        for edge in edges:
            if (edge.source_id, edge.target_id) in search_compound_pairs and self._is_direct_search_submit_tap(edge):
                filtered += 1
                continue
            if (
                (edge.source_id, edge.target_id, edge.action_type.lower()) in explicit_semantic_keys
                and self._is_generic_affordance_edge(edge)
            ):
                filtered += 1
                continue
            preferred.append(edge)
        return preferred, filtered

    @staticmethod
    def _is_slot_search_compound(edge: TransitionEdge) -> bool:
        params = edge.action_params or {}
        runtime_slots = params.get("runtime_slots") or []
        if isinstance(runtime_slots, str):
            runtime_slots = [runtime_slots]
        semantic_target = str(params.get("semantic_target") or edge.action_target or "")
        return (
            edge.action_type.lower() == "compound"
            and edge.postcondition == "search_result"
            and "query" in {str(slot) for slot in runtime_slots}
            and (semantic_target == "submit_search" or params.get("requires_runtime_input") is True)
        )

    @staticmethod
    def _is_direct_search_submit_tap(edge: TransitionEdge) -> bool:
        params = edge.action_params or {}
        return (
            edge.action_type.lower() in {"tap", "click"}
            and edge.postcondition == "search_result"
            and str(params.get("target_page_type") or "") in {"", "search_result"}
        )

    @staticmethod
    def _is_explicit_semantic_edge(edge: TransitionEdge) -> bool:
        params = edge.action_params or {}
        target = str(params.get("semantic_target") or edge.action_target or "")
        return target in {
            "open_search",
            "submit_search",
            "open_product_detail",
            "open_filter_panel",
            "open_spec_selector",
            "confirm_spec_add_to_cart",
            "confirm_add_to_cart_success",
            "open_cart_from_header",
            "rollback_to_product_detail",
            "rollback_to_search_result",
            "apply_or_close_filter",
        }

    @staticmethod
    def _is_generic_affordance_edge(edge: TransitionEdge) -> bool:
        params = edge.action_params or {}
        target = str(params.get("semantic_target") or edge.action_target or "").strip().lower()
        return target.endswith(" affordance")

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
        return self._fill_runtime_slots(edge.to_next_action(), runtime_dag.goal_spec.slots)

    def _fill_runtime_slots(self, action: dict[str, Any], slots: dict[str, str]) -> dict[str, Any]:
        """Replace reusable compound placeholders with task-specific slots."""
        resolved = dict(action)
        params = self._fill_action_slots(self._decode_embedded_action_params(resolved), slots)
        if params:
            resolved["target_desc"] = repr(params)
            resolved["target"] = params.get("semantic_target", resolved.get("target", ""))
            # Boost confidence when runtime slots are successfully filled —
            # a concrete action with real task data is more reliable than a
            # generic template with placeholder values.
            if slots and params.get("requires_runtime_input"):
                base_conf = float(resolved.get("confidence", 0.8))
                resolved["confidence"] = min(1.0, base_conf * 1.3)
        return resolved

    @classmethod
    def _fill_action_slots(cls, action: dict[str, Any], slots: dict[str, str]) -> dict[str, Any]:
        if not action:
            return {}
        resolved = dict(action)
        if isinstance(resolved.get("text"), str):
            resolved["text"] = cls._replace_slot_value(resolved["text"], slots)
        if isinstance(resolved.get("semantic_target"), str):
            resolved["semantic_target"] = cls._replace_slot_value(
                resolved["semantic_target"], slots
            )
        actions = resolved.get("actions")
        if isinstance(actions, list):
            resolved["actions"] = [
                cls._fill_action_slots(step, slots) if isinstance(step, dict) else step
                for step in actions
            ]
        return resolved

    @staticmethod
    def _replace_slot_value(value: str, slots: dict[str, str]) -> str:
        def _sub(m: re.Match) -> str:
            return str(slots.get(m.group(1)) or m.group(0))
        return re.sub(r"<([a-zA-Z0-9_]+)>", _sub, value)

    @staticmethod
    def _decode_embedded_action_params(action: dict[str, Any]) -> dict[str, Any]:
        value = action.get("target_desc")
        if isinstance(value, dict):
            return dict(value)
        if not isinstance(value, str) or not value.strip():
            return {}
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            try:
                import ast

                decoded = ast.literal_eval(value)
            except (SyntaxError, ValueError, TypeError):
                return {}
        return dict(decoded) if isinstance(decoded, dict) else {}

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
    def match_trajectory_path(pages_path: str | Path) -> Path:
        pages_file = Path(pages_path)
        name = pages_file.name.replace("_explore_", "_explore_trajectory_")
        return pages_file.with_name(name)

    @staticmethod
    def extract_slots_from_text(text: str) -> dict[str, str]:
        from phone_agent.core.task_spec import TaskSpecExtractor
        return TaskSpecExtractor.extract(text).slot_dict

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
            if next_edge.evidence:
                parts.append(f"[SpatialGraph Why] {next_edge.evidence}")
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

    def _load_page_type_graph_candidate(self, state: PageState) -> PageState | None:
        if not self.graph_store or not getattr(self.graph_store, "driver", None):
            return None
        try:
            candidates = self.graph_store.find_page_state_candidates(
                app=state.app,
                page_type=state.page_type,
                limit=20,
            )
        except AttributeError:
            return None
        except Exception:
            return None
        if not candidates:
            return None

        for candidate in candidates:
            candidate_state = self._page_state_from_graph(candidate, fallback=state)
            if candidate_state.app == state.app and candidate_state.page_type == state.page_type:
                return candidate_state
        return None

    def _merge_into_existing_page_state(self, existing: PageState, incoming: PageState) -> PageState:
        merged_landmarks = _dedupe((*existing.landmarks, *incoming.landmarks), limit=12)
        merged_affordances = _dedupe((*existing.affordances, *incoming.affordances), limit=12)
        summary = existing.summary if len(existing.summary) <= len(incoming.summary) else incoming.summary
        merged_slots = {**incoming.slots, **existing.slots}
        return PageState(
            state_id=existing.state_id,
            app=existing.app or incoming.app,
            page_type=existing.page_type or incoming.page_type,
            summary=summary,
            landmarks=merged_landmarks,
            affordances=merged_affordances,
            slots=merged_slots,
            risk_level=existing.risk_level or incoming.risk_level,
            screenshot_hash=existing.screenshot_hash or incoming.screenshot_hash,
            semantic_signature=self._semantic_signature(
                existing.app or incoming.app,
                existing.page_type or incoming.page_type,
                merged_landmarks,
                merged_affordances,
                merged_slots,
            ),
        )

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
        # Offline exploration can see the same logical page with very different
        # extracted element detail. The routing graph wants one reusable node per
        # app/page_type/risk bucket; landmarks and affordances are merged into
        # the node metadata instead of splitting identity.
        return "|".join([state.app, state.page_type, state.risk_level])

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
            if hasattr(self.graph_store, "find_v4_page_candidates"):
                candidates = self.graph_store.find_v4_page_candidates(
                    app=page_state.app,
                    page_type=page_state.page_type,
                    semantic_signature=page_state.semantic_signature,
                    limit=20,
                )
            else:
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
                if hasattr(self.graph_store, "get_v4_outgoing_edges"):
                    graph_edges = self.graph_store.get_v4_outgoing_edges(state_id, app=allowed_app)
                else:
                    graph_edges = self.graph_store.get_outgoing_transitions(state_id, app=allowed_app)
            except AttributeError:
                graph_edges = []
            except Exception:
                graph_edges = []

        edges = local_edges + graph_edges

        # Semantic fallback: when exact state_id match finds nothing OR
        # only self-loop edges (unknown → unknown coordination steps),
        # match graph states by (app, page_type) so the agent can still
        # navigate through the known page graph.
        current_state = self._local_states.get(state_id)
        edges_are_all_self_loops = (
            edges
            and current_state
            and current_state.page_type == "unknown"
            and all(
                e.postcondition == current_state.page_type
                for e in edges
            )
        )
        if (not edges or edges_are_all_self_loops) and current_state and current_state.page_type:
            edges = self._load_edges_by_page_type(
                current_state.page_type, current_state.app, allowed_app, state_id
            )

        # Heuristic rule: Inject "加入购物车/立即购买" edges for product_detail pages
        # when graph lacks proper spec_selection transitions
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

    def _load_edges_by_page_type(
        self, page_type: str, app: str, allowed_app: str, source_state_id: str,
    ) -> list[TransitionEdge]:
        """Fallback: find graph edges from states with matching (app, page_type).

        When the runtime state_id doesn't match any graph state (different
        screenshot hash), this method finds graph states with the same semantic
        identity and returns their outgoing edges with remapped source IDs.

        If page_type is "unknown" (classifier failed), query ALL page_types
        for this app so the BFS has a chance to find a route.
        """
        edges: list[TransitionEdge] = []
        if not self.graph_store or not getattr(self.graph_store, "driver", None):
            return edges

        try:
            with self.graph_store.driver.session(database=self.graph_store.database) as s:
                if page_type == "unknown":
                    # Classifier failed — load edges from likely starting page types
                    # for this app.  Prefer "home" (launch → app home), then
                    # "unknown" (exploration startup), then "search_input" (common entry).
                    # Excludes deep states like "product_detail", "cart", etc. to avoid
                    # unrealistic shortcuts (e.g. "product_detail → cart" from home).
                    result = s.run(
                        """
                        MATCH (src:UIState)-[:NEXT_ACTION]->(a:Action)-[:PRODUCES]->(tgt:UIState)
                        WHERE ($app = '' OR src.app CONTAINS $app OR src.app = '')
                          AND src.page_type IN ['home', 'unknown']
                        RETURN src.state_id AS src_id, src.page_type AS src_pt,
                               a.type AS action_type, a.semantic_target AS action_target,
                               a.region AS region,
                               a.target_locator AS target_locator,
                               a.target_desc AS target_desc,
                               tgt.page_type AS postcondition, tgt.state_id AS tgt_id,
                               coalesce(a.confidence, 0.8) AS confidence,
                               coalesce(a.frequency, 1) AS freq
                        ORDER BY freq DESC, confidence DESC
                        LIMIT 12
                        """,
                        app=app,
                    )
                else:
                    result = s.run(
                        """
                        MATCH (src:UIState)-[:NEXT_ACTION]->(a:Action)-[:PRODUCES]->(tgt:UIState)
                        WHERE src.page_type = $page_type
                          AND ($app = '' OR src.app CONTAINS $app OR src.app = '')
                        RETURN src.state_id AS src_id, a.type AS action_type,
                               a.semantic_target AS action_target, a.region AS region,
                               a.target_locator AS target_locator,
                               a.target_desc AS target_desc,
                               tgt.page_type AS postcondition, tgt.state_id AS tgt_id,
                               coalesce(a.confidence, 0.8) AS confidence,
                               coalesce(a.frequency, 1) AS freq
                        ORDER BY freq DESC, confidence DESC
                        LIMIT 8
                        """,
                        page_type=page_type,
                        app=app,
                    )
                for rec in result:
                    r = dict(rec)
                    src_pt = r.get("src_pt", page_type)
                    # Semantic fallback edges: use higher base confidence for
                    # structurally-correct transitions from known page types.
                    if src_pt == "home":
                        graph_conf = 1.0  # home is the most likely start
                    elif src_pt and src_pt != "unknown":
                        graph_conf = 0.9  # other known page types
                    else:
                        graph_conf = 0.75  # unknown source (coordination steps)
                    # Penalize confidence when source page_type differs from
                    # the runtime state's page_type (classifier mismatch).
                    if src_pt != page_type:
                        graph_conf *= 0.85  # 15% penalty for type mismatch
                    action_params: dict[str, Any] = {
                        "region": r.get("region", ""),
                        "app": app,
                    }
                    # Parse element coordinates from target_locator or target_desc.
                    # target_locator is usually "{}" (empty); coordinates live in
                    # target_desc as a Python repr string, e.g.
                    #   "{'action': 'Tap', 'element': [499, 114], ...}"
                    for raw_key in ("target_locator", "target_desc"):
                        raw = r.get(raw_key)
                        if not raw:
                            continue
                        parsed = None
                        if isinstance(raw, dict):
                            parsed = raw
                        elif isinstance(raw, str) and raw.strip():
                            try:
                                parsed = json.loads(raw)
                            except (json.JSONDecodeError, ValueError):
                                try:
                                    parsed = ast.literal_eval(raw)
                                except (SyntaxError, ValueError):
                                    pass
                        if isinstance(parsed, dict):
                            for loc_key in ("element", "coordinate", "bbox"):
                                if loc_key in parsed:
                                    action_params[loc_key] = parsed[loc_key]
                                    if "coordinate_space" in parsed:
                                        action_params["coordinate_space"] = parsed["coordinate_space"]
                                    break
                            break  # stop after first successful parse
                    edge = TransitionEdge(
                        source_id=source_state_id,  # remap to runtime state
                        target_id=r.get("tgt_id", ""),
                        action_type=r.get("action_type", "Tap"),
                        action_target=r.get("action_target", ""),
                        action_params=action_params,
                        postcondition=r.get("postcondition", ""),
                        success_count=int(r.get("freq", 1)),
                        fail_count=0,
                        risk="normal",
                        confidence=graph_conf,
                    )
                    edges.append(edge)
        except Exception:
            pass
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
            # Product detail buy/cart buttons commonly open specs. Search
            # result pages may also do that from product cards, but top-bar or
            # filter taps should not become reusable spec shortcuts.
            if source_page_type == "product_detail":
                return True
            if source_page_type == "search_result" and (
                _contains_any(action_text, spec_tokens)
                or SpatialGraphMemory._looks_like_product_card_tap(edge.action_params)
            ):
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

    @staticmethod
    def _looks_like_product_card_tap(action_params: dict[str, Any]) -> bool:
        element = action_params.get("element")
        if not isinstance(element, list):
            return False
        if len(element) == 1 and isinstance(element[0], list):
            element = element[0]
        try:
            if len(element) >= 4:
                y = (float(element[1]) + float(element[3])) / 2
            elif len(element) >= 2:
                y = float(element[1])
            else:
                return False
        except (TypeError, ValueError):
            return False
        return y >= 250

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
            "filter_panel": ("price_range", "filter_options", "confirm_button"),
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
            "filter_panel": ("set_price_range", "apply_filter", "back"),
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
