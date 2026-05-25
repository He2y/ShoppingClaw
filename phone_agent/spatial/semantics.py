"""Screen semantics extraction helpers for AMSG."""

from __future__ import annotations

import hashlib
from typing import Any

from .core import PageNode
from .schema_registry import MobileSchema, get_default_registry


class ScreenSemanticsExtractor:
    """Build model-agnostic page nodes from runtime or exploration metadata."""

    def __init__(self, schema_name: str = "shopping", schema: MobileSchema | None = None):
        self.schema_name = schema_name
        self.registry = get_default_registry()
        self.schema = schema or self.registry.merged(schema_name)

    def node_from_screen(self, screen: dict[str, Any], task: str = "") -> PageNode:
        app = self.registry.normalize_app(str(screen.get("app") or screen.get("current_app") or ""), self.schema_name)
        semantic_layout = str(screen.get("semantic_layout") or "")
        page_type = self.schema.normalize_page_type(str(screen.get("page_type") or ""))
        if not page_type or page_type == "unknown":
            page_type = self._infer_page_type(" ".join([semantic_layout, str(screen.get("summary") or ""), task]))
        summary = str(screen.get("summary") or semantic_layout or page_type)
        elements = screen.get("elements") if isinstance(screen.get("elements"), dict) else {}
        landmarks = self._merge(self._schema_landmarks(page_type), tuple(elements.keys()))
        affordances = self._merge(self._schema_affordances(page_type), self._affordances_from_elements(elements))
        risk = self._risk_for(page_type)
        evidence = {
            "ui_hash": screen.get("ui_hash") or screen.get("screenshot_hash") or "",
            "semantic_layout": semantic_layout,
            "elements": elements,
        }
        if not evidence["ui_hash"] and screen.get("screenshot_base64"):
            evidence["ui_hash"] = hashlib.md5(str(screen["screenshot_base64"]).encode()).hexdigest()
        return PageNode.build(
            app=app or "unknown_app",
            domain=self.schema.name,
            page_type=page_type,
            summary=summary,
            landmarks=landmarks,
            affordances=affordances,
            slots=self._slots_from_text(" ".join([task, summary, semantic_layout])),
            risk_level=risk,
            evidence=evidence,
        )

    def node_from_exploration_page(self, page: dict[str, Any], fallback_app: str = "") -> PageNode:
        return self.node_from_screen(
            {
                "app": page.get("app") or fallback_app,
                "page_type": page.get("page_type"),
                "summary": page.get("summary"),
                "elements": page.get("elements") if isinstance(page.get("elements"), dict) else {},
                "screenshot_hash": page.get("screenshot_hash") or "",
                "semantic_layout": " ".join(
                    str(item)
                    for item in (page.get("app") or fallback_app, page.get("page_type") or "", page.get("summary") or "")
                    if item
                ),
            }
        )

    def _schema_landmarks(self, page_type: str) -> tuple[str, ...]:
        spec = self.schema.page_spec(page_type)
        return spec.landmarks if spec else ()

    def _schema_affordances(self, page_type: str) -> tuple[str, ...]:
        spec = self.schema.page_spec(page_type)
        return spec.affordances if spec else ()

    def _risk_for(self, page_type: str) -> str:
        spec = self.schema.page_spec(page_type)
        return spec.risk if spec else "medium" if page_type == "unknown" else "normal"

    def _infer_page_type(self, text: str) -> str:
        lowered = (text or "").lower()
        for page_type, spec in self.schema.page_types.items():
            tokens = {page_type, *spec.aliases, *spec.landmarks, *spec.affordances}
            if any(token and token.lower() in lowered for token in tokens):
                return page_type
        return "unknown"

    @staticmethod
    def _affordances_from_elements(elements: dict[str, Any]) -> tuple[str, ...]:
        hints = []
        for key in elements.keys():
            lowered = str(key).lower()
            if "search" in lowered:
                hints.append("open_search")
            if "product" in lowered or "card" in lowered or "item" in lowered:
                hints.append("open_detail")
            if "filter" in lowered:
                hints.append("filter")
            if "cart" in lowered:
                hints.append("add_to_cart")
            if "confirm" in lowered or "submit" in lowered:
                hints.append("confirm")
            if "close" in lowered or "back" in lowered:
                hints.append("go_back")
        return tuple(dict.fromkeys(hints))

    @staticmethod
    def _slots_from_text(text: str) -> dict[str, str]:
        slots: dict[str, str] = {}
        for token in ("query", "product", "price"):
            marker = f"{token}:"
            lowered = text.lower()
            if marker in lowered:
                value = text[lowered.index(marker) + len(marker) :].split()[0]
                if value:
                    slots[token] = value.strip(",;")
        return slots

    @staticmethod
    def _merge(*groups: tuple[str, ...], limit: int = 12) -> tuple[str, ...]:
        values: list[str] = []
        seen: set[str] = set()
        for group in groups:
            for item in group:
                normalized = str(item).strip()
                if normalized and normalized not in seen:
                    values.append(normalized)
                    seen.add(normalized)
                if len(values) >= limit:
                    return tuple(values)
        return tuple(values)
