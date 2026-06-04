"""Action Advisor — graph as navigation advisor, not controller.

Reads promoted Actions from the Action Library (Neo4j graph +
EdgeLifecycleManager) and provides them as advisory hints to the VLM.
After VLM makes a decision, optionally grounds the action with stored
coordinates from matching Grounded Actions.

This replaces the old graph-as-controller paradigm where the graph
directly executed actions and bypassed VLM.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


# ── Ungrounded transitions: VLM must decide which element to interact with ──

_UNGROUNDED_TRANSITIONS = frozenset({
    ("search_result", "product_detail"),
    ("search_result", "store"),
    ("product_detail", "spec_selection"),
    ("spec_selection", "cart"),
    ("spec_selection", "checkout"),
})


# ── Data types ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ActionHint:
    """Single advisory hint from the Action Library for VLM context."""

    target_page: str
    action_type: str
    region: str
    grounded: bool
    confidence: float
    description: str
    coordinates: tuple[int, int] | None = None
    compound_steps: tuple[dict[str, Any], ...] | None = None

    def is_fast_executable(self) -> bool:
        """Can this hint be auto-executed on the Fast Path?"""
        return (
            self.grounded
            and self.confidence >= 0.9
            and (self.coordinates is not None or self.compound_steps is not None
                 or self.action_type in ("Back", "Home", "Launch", "Wait"))
        )


# ── ActionAdvisor ───────────────────────────────────────────────────────────

class ActionAdvisor:
    """Reads Action Library and provides advisory hints to VLM.

    Two data sources, merged with deduplication:
      1. Neo4j persisted Actions (cross-session, via SpatialGraphMemory)
      2. Current-session lifecycle records (newly promoted)
    """

    def __init__(
        self,
        spatial_graph_memory: Any,
        edge_lifecycle: Any | None = None,
    ) -> None:
        self._graph = spatial_graph_memory
        self._lifecycle = edge_lifecycle

    def query(self, page_type: str, app: str = "") -> list[ActionHint]:
        """Return available Actions for the current page as advisory hints.

        Only ``promoted`` Actions are returned.  Hypothesis/candidate edges
        are invisible to the VLM — it must explore those transitions itself.
        """
        hints: list[ActionHint] = []
        seen_keys: set[str] = set()

        # Source 1: Neo4j persisted edges
        try:
            edges = self._graph._load_edges_by_page_type(
                page_type, app,
                allowed_app=app,
                source_state_id=f"state_{app}_{page_type}_advisor",
            )
            for edge in edges:
                key = f"{edge.action_type}|{edge.postcondition}|{edge.action_target}"
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                hints.append(self._edge_to_hint(edge, page_type))
        except Exception:
            pass

        # Source 2: Current-session lifecycle-promoted records
        if self._lifecycle:
            try:
                for record in self._lifecycle.get_promoted_edges_for_page(page_type):
                    key = f"{record.intent}|{record.target_page_type}|{record.action_target}"
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    hints.append(self._record_to_hint(record))
            except Exception:
                pass

        hints.sort(key=lambda h: (-h.confidence, h.grounded is False))
        return hints[:8]

    def try_ground(
        self,
        vlm_action: dict[str, Any],
        hints: list[ActionHint],
    ) -> dict[str, Any]:
        """After VLM decides, enhance action with stored coordinates if possible.

        Only applies when:
          - VLM action is coordinate-based (Tap, Swipe)
          - A matching Grounded ActionHint exists
          - VLM's intended postcondition aligns with the hint's target_page
        """
        action_name = vlm_action.get("action", "")
        if action_name not in ("Tap", "Swipe"):
            return vlm_action

        expected_post = vlm_action.get("_expected_postcondition", "")
        if not expected_post:
            return vlm_action

        for hint in hints:
            if (
                hint.grounded
                and hint.coordinates
                and hint.action_type == action_name
                and hint.target_page == expected_post
            ):
                enhanced = dict(vlm_action)
                enhanced["element"] = list(hint.coordinates)
                enhanced["_grounded_by"] = "action_library"
                return enhanced

        return vlm_action

    def get_fast_action(
        self,
        hint: ActionHint,
    ) -> dict[str, Any]:
        """Build an executable action dict from a Grounded ActionHint.

        Used by Fast Path when VLM is skipped for mechanical navigation.
        """
        if hint.compound_steps:
            return {
                "_metadata": "do",
                "action": "Compound",
                "steps": list(hint.compound_steps),
            }
        if hint.action_type in ("Back", "Home"):
            return {"_metadata": "do", "action": hint.action_type}
        if hint.action_type == "Launch":
            return {"_metadata": "do", "action": "Launch", "app": hint.description}
        if hint.coordinates:
            return {
                "_metadata": "do",
                "action": hint.action_type,
                "element": list(hint.coordinates),
            }
        return {"_metadata": "do", "action": hint.action_type}

    def format_for_vlm(self, hints: list[ActionHint]) -> str:
        """Render hints as natural language for VLM context injection."""
        if not hints:
            return ""
        lines = []
        for h in hints:
            tag = "" if h.grounded else " [需要你选择具体目标]"
            lines.append(f"- {h.description} → {h.target_page}{tag}")
        return "\n".join(lines)

    # ── Internal helpers ────────────────────────────────────────────────

    def _edge_to_hint(self, edge: Any, source_page_type: str) -> ActionHint:
        """Convert a TransitionEdge to an ActionHint."""
        pair = (source_page_type, edge.postcondition)
        grounded = pair not in _UNGROUNDED_TRANSITIONS

        coords = None
        element = edge.action_params.get("element")
        if isinstance(element, (list, tuple)) and len(element) >= 2:
            coords = (int(element[0]), int(element[1]))

        compound_steps = None
        if edge.action_type == "Compound":
            raw_steps = edge.action_params.get("steps")
            if isinstance(raw_steps, (list, tuple)):
                compound_steps = tuple(
                    dict(s) if isinstance(s, dict) else s for s in raw_steps
                )

        description = self._build_description(edge)
        return ActionHint(
            target_page=edge.postcondition,
            action_type=edge.action_type,
            region=edge.action_params.get("region", ""),
            grounded=grounded,
            confidence=edge.confidence,
            description=description,
            coordinates=coords if grounded else None,
            compound_steps=compound_steps,
        )

    def _record_to_hint(self, record: Any) -> ActionHint:
        """Convert an EdgeLifecycleRecord to an ActionHint."""
        grounded = (record.source_page_type, record.target_page_type) not in _UNGROUNDED_TRANSITIONS
        desc = f"{record.intent} → {record.target_page_type}"
        return ActionHint(
            target_page=record.target_page_type,
            action_type=record.intent,
            region="",
            grounded=grounded,
            confidence=record.dominance_ratio,
            description=desc,
        )

    @staticmethod
    def _build_description(edge: Any) -> str:
        """Build human-readable description from edge metadata."""
        action = edge.action_type
        target = edge.action_target or edge.postcondition
        region = edge.action_params.get("region", "")
        if region:
            return f"{action} {region} ({target})"
        return f"{action} ({target})"
