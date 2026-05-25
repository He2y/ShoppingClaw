"""Core data structures for Active Mobile Spatial Graph (AMSG).

The spatial layer is intentionally model-agnostic. It stores semantic pages,
affordance edges, and action IR objects; model-specific action syntax is kept
outside the graph in the protocol bridge.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any


Coordinate = tuple[float, float]
ScreenSize = tuple[int, int]


def safe_slug(value: str, limit: int = 64) -> str:
    value = (value or "unknown").strip()
    value = re.sub(r"\s+", "_", value)
    value = re.sub(r"[^\w.-]+", "_", value)
    return (value[:limit].strip("_") or "unknown").lower()


def stable_id(prefix: str, *parts: Any, length: int = 12) -> str:
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.md5(payload.encode("utf-8")).hexdigest()[:length]
    label = safe_slug("_".join(str(part) for part in parts if part), limit=48)
    return f"{prefix}_{label}_{digest}"


def semantic_signature(
    app: str,
    domain: str,
    page_type: str,
    landmarks: tuple[str, ...] = (),
    affordances: tuple[str, ...] = (),
    slots: dict[str, str] | None = None,
) -> str:
    slot_items = ",".join(f"{key}={value}" for key, value in sorted((slots or {}).items()))
    return "|".join(
        [
            app or "",
            domain or "general",
            page_type or "unknown",
            ",".join(landmarks),
            ",".join(affordances),
            slot_items,
        ]
    )


@dataclass(frozen=True)
class PageNode:
    """Stable page-level node used by the schema-instance graph."""

    node_id: str
    app: str
    domain: str = "general"
    page_type: str = "unknown"
    summary: str = ""
    landmarks: tuple[str, ...] = ()
    affordances: tuple[str, ...] = ()
    slots: dict[str, str] = field(default_factory=dict)
    risk_level: str = "normal"
    semantic_signature: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        *,
        app: str,
        domain: str = "general",
        page_type: str = "unknown",
        summary: str = "",
        landmarks: tuple[str, ...] = (),
        affordances: tuple[str, ...] = (),
        slots: dict[str, str] | None = None,
        risk_level: str = "normal",
        evidence: dict[str, Any] | None = None,
    ) -> "PageNode":
        slots = dict(slots or {})
        signature = semantic_signature(app, domain, page_type, landmarks, affordances, slots)
        return cls(
            node_id=stable_id("page", app, domain, page_type, landmarks, affordances),
            app=app,
            domain=domain,
            page_type=page_type or "unknown",
            summary=summary,
            landmarks=tuple(landmarks),
            affordances=tuple(affordances),
            slots=slots,
            risk_level=risk_level,
            semantic_signature=signature,
            evidence=dict(evidence or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "app": self.app,
            "domain": self.domain,
            "page_type": self.page_type,
            "summary": self.summary,
            "landmarks": list(self.landmarks),
            "affordances": list(self.affordances),
            "slots": dict(self.slots),
            "risk_level": self.risk_level,
            "semantic_signature": self.semantic_signature,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class AffordanceEdge:
    """Semantic transition edge between page nodes."""

    edge_id: str
    source_node: str
    target_node: str = ""
    intent: str = ""
    semantic_target: str = ""
    target_locator: dict[str, Any] = field(default_factory=dict)
    precondition: str = ""
    expected_postcondition: str = ""
    rollback_action: str = "Back"
    risk_level: str = "normal"
    success_count: int = 0
    fail_count: int = 0
    confidence: float = 0.5
    model_evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def attempts(self) -> int:
        return self.success_count + self.fail_count

    @property
    def failure_rate(self) -> float:
        if self.attempts <= 0:
            return 0.0
        return self.fail_count / self.attempts

    @property
    def weighted_cost(self) -> float:
        risk_penalty = {"normal": 0.0, "medium": 0.8, "high": 2.0}.get(self.risk_level, 0.5)
        return 1.0 + self.failure_rate * 3.0 + risk_penalty - max(0.0, min(self.confidence, 1.0)) * 0.3

    @classmethod
    def build(
        cls,
        *,
        source_node: str,
        target_node: str = "",
        intent: str,
        semantic_target: str = "",
        expected_postcondition: str = "",
        target_locator: dict[str, Any] | None = None,
        risk_level: str = "normal",
        confidence: float = 0.5,
        model_evidence: dict[str, Any] | None = None,
    ) -> "AffordanceEdge":
        return cls(
            edge_id=stable_id("edge", source_node, intent, semantic_target, expected_postcondition),
            source_node=source_node,
            target_node=target_node,
            intent=intent,
            semantic_target=semantic_target,
            expected_postcondition=expected_postcondition,
            target_locator=dict(target_locator or {}),
            risk_level=risk_level,
            confidence=confidence,
            model_evidence=dict(model_evidence or {}),
        )

    def to_semantic_action(self) -> "SemanticActionIR":
        return SemanticActionIR(
            intent=self.intent,
            semantic_target=self.semantic_target,
            target_locator=dict(self.target_locator),
            expected_postcondition=self.expected_postcondition,
            risk=self.risk_level,
            confidence=self.confidence,
            source_edge_id=self.edge_id,
            grounding_required=not bool(self.target_locator),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "source_node": self.source_node,
            "target_node": self.target_node,
            "intent": self.intent,
            "semantic_target": self.semantic_target,
            "target_locator": dict(self.target_locator),
            "precondition": self.precondition,
            "expected_postcondition": self.expected_postcondition,
            "rollback_action": self.rollback_action,
            "risk_level": self.risk_level,
            "success_count": self.success_count,
            "fail_count": self.fail_count,
            "confidence": self.confidence,
            "model_evidence": dict(self.model_evidence),
            "weighted_cost": self.weighted_cost,
        }


@dataclass(frozen=True)
class BeliefCandidate:
    node: PageNode
    score: float
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = self.node.to_dict()
        data.update({"score": self.score, "reason": self.reason})
        return data


@dataclass(frozen=True)
class BeliefState:
    current_node_id: str
    candidates: tuple[BeliefCandidate, ...]
    confidence: float
    is_novel: bool = False

    @property
    def top(self) -> PageNode | None:
        return self.candidates[0].node if self.candidates else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_node_id": self.current_node_id,
            "confidence": self.confidence,
            "is_novel": self.is_novel,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }


@dataclass(frozen=True)
class SemanticActionIR:
    """Graph-native action representation independent of model syntax."""

    intent: str
    semantic_target: str = ""
    target_locator: dict[str, Any] = field(default_factory=dict)
    slots: dict[str, str] = field(default_factory=dict)
    expected_postcondition: str = ""
    risk: str = "normal"
    confidence: float = 0.0
    source_edge_id: str = ""
    grounding_required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "semantic_target": self.semantic_target,
            "target_locator": dict(self.target_locator),
            "slots": dict(self.slots),
            "expected_postcondition": self.expected_postcondition,
            "risk": self.risk,
            "confidence": self.confidence,
            "source_edge_id": self.source_edge_id,
            "grounding_required": self.grounding_required,
        }


@dataclass(frozen=True)
class DeviceActionIR:
    """Device-level action after protocol normalization."""

    action_type: str
    coordinate: Coordinate | None = None
    coordinate2: Coordinate | None = None
    text: str = ""
    app_name: str = ""
    duration: float | None = None
    button: str = ""
    coordinate_space: str = "normalized_1000"
    screen_size: ScreenSize | None = None
    source_model: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def with_metadata(self, **metadata: Any) -> "DeviceActionIR":
        merged = dict(self.metadata)
        merged.update(metadata)
        return DeviceActionIR(
            action_type=self.action_type,
            coordinate=self.coordinate,
            coordinate2=self.coordinate2,
            text=self.text,
            app_name=self.app_name,
            duration=self.duration,
            button=self.button,
            coordinate_space=self.coordinate_space,
            screen_size=self.screen_size,
            source_model=self.source_model,
            metadata=merged,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "coordinate": list(self.coordinate) if self.coordinate else None,
            "coordinate2": list(self.coordinate2) if self.coordinate2 else None,
            "text": self.text,
            "app_name": self.app_name,
            "duration": self.duration,
            "button": self.button,
            "coordinate_space": self.coordinate_space,
            "screen_size": list(self.screen_size) if self.screen_size else None,
            "source_model": self.source_model,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    reason: str = ""
    repair_action: str = "continue"
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "repair_action": self.repair_action,
            "confidence": self.confidence,
        }
