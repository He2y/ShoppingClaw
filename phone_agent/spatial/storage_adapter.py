"""Adapters between the legacy memory graph and AMSG structures."""

from __future__ import annotations

from typing import Any

from .core import AffordanceEdge, PageNode


class SpatialStorageAdapter:
    @staticmethod
    def page_state_to_node(page_state: Any) -> PageNode:
        return PageNode.build(
            app=str(getattr(page_state, "app", "") or ""),
            domain="shopping" if str(getattr(page_state, "app", "")).lower() else "general",
            page_type=str(getattr(page_state, "page_type", "") or "unknown"),
            summary=str(getattr(page_state, "summary", "") or ""),
            landmarks=tuple(getattr(page_state, "landmarks", ()) or ()),
            affordances=tuple(getattr(page_state, "affordances", ()) or ()),
            slots=dict(getattr(page_state, "slots", {}) or {}),
            risk_level=str(getattr(page_state, "risk_level", "") or "normal"),
            evidence={
                "legacy_state_id": getattr(page_state, "state_id", ""),
                "screenshot_hash": getattr(page_state, "screenshot_hash", ""),
            },
        )

    @staticmethod
    def transition_edge_to_affordance(edge: Any) -> AffordanceEdge:
        action_params = dict(getattr(edge, "action_params", {}) or {})
        semantic_target = str(action_params.get("semantic_target") or getattr(edge, "action_target", "") or "")
        return AffordanceEdge.build(
            source_node=str(getattr(edge, "source_id", "") or ""),
            target_node=str(getattr(edge, "target_id", "") or ""),
            intent=SpatialStorageAdapter._intent_from_action(str(getattr(edge, "action_type", "") or "")),
            semantic_target=semantic_target,
            expected_postcondition=str(getattr(edge, "postcondition", "") or ""),
            target_locator=SpatialStorageAdapter._locator_from_action_params(action_params),
            risk_level=str(getattr(edge, "risk", "") or "normal"),
            confidence=float(getattr(edge, "confidence", 0.0) or 0.0),
            model_evidence={
                "legacy_action_type": getattr(edge, "action_type", ""),
                "legacy_action_target": getattr(edge, "action_target", ""),
                "action_params": action_params,
            },
        )

    @staticmethod
    def _intent_from_action(action_type: str) -> str:
        lowered = action_type.lower()
        if lowered in {"tap", "click"}:
            return "click"
        if lowered in {"type", "input"}:
            return "type_text"
        if lowered == "swipe":
            return "scroll"
        if lowered == "back":
            return "go_back"
        if lowered == "wait":
            return "wait"
        return lowered or "unknown"

    @staticmethod
    def _locator_from_action_params(action_params: dict[str, Any]) -> dict[str, Any]:
        if "element" in action_params:
            return {"element": action_params["element"], "coordinate_space": "normalized_1000"}
        if "coordinate" in action_params:
            return {"coordinate": action_params["coordinate"], "coordinate_space": "normalized_999"}
        return {}
