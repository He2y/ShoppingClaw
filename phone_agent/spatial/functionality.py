"""Self-discovered functionality primitives for AMSG v4.

The v4 graph does not assume a hand-written list of app-specific feature
buckets.  It extracts candidate functions from observed screens and verified
transitions, then lets clustering and postcondition evidence decide what is
worth promoting into the graph.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .core import stable_id


DATA_HINTS = (
    "price",
    "商品名",
    "标题",
    "title",
    "金额",
    "价格",
    "销量",
    "店铺",
    "评分",
    "评价",
    "sku文案",
)

ACTION_VERBS = {
    "tap": "tap",
    "click": "tap",
    "type": "type",
    "input": "type",
    "swipe": "swipe",
    "back": "back",
    "wait": "wait",
}


@dataclass(frozen=True)
class FunctionalityItem:
    functionality_id: str
    page_node_id: str
    app: str = ""
    page_type: str = ""
    type: str = "functionality"
    label: str = ""
    description: str = ""
    bbox: tuple[float, float, float, float] | None = None
    region: str = ""
    visual_evidence: str = ""
    text_evidence: str = ""
    source_action: dict[str, Any] = field(default_factory=dict)
    expected_effect: str = ""
    observed_postcondition: str = ""
    confidence: float = 0.5
    embedding: tuple[float, ...] = ()
    cluster_id: str = ""

    @property
    def is_verified(self) -> bool:
        return bool(self.observed_postcondition)

    def with_cluster(self, cluster_id: str) -> "FunctionalityItem":
        data = self.to_dict()
        data["cluster_id"] = cluster_id
        return FunctionalityItem.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "functionality_id": self.functionality_id,
            "page_node_id": self.page_node_id,
            "app": self.app,
            "page_type": self.page_type,
            "type": self.type,
            "label": self.label,
            "description": self.description,
            "bbox": list(self.bbox) if self.bbox else None,
            "region": self.region,
            "visual_evidence": self.visual_evidence,
            "text_evidence": self.text_evidence,
            "source_action": dict(self.source_action),
            "expected_effect": self.expected_effect,
            "observed_postcondition": self.observed_postcondition,
            "confidence": self.confidence,
            "embedding": list(self.embedding),
            "cluster_id": self.cluster_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FunctionalityItem":
        bbox = data.get("bbox")
        return cls(
            functionality_id=str(data.get("functionality_id") or ""),
            page_node_id=str(data.get("page_node_id") or ""),
            app=str(data.get("app") or ""),
            page_type=str(data.get("page_type") or ""),
            type=str(data.get("type") or "functionality"),
            label=str(data.get("label") or ""),
            description=str(data.get("description") or ""),
            bbox=tuple(float(value) for value in bbox) if bbox else None,
            region=str(data.get("region") or ""),
            visual_evidence=str(data.get("visual_evidence") or ""),
            text_evidence=str(data.get("text_evidence") or ""),
            source_action=dict(data.get("source_action") or {}),
            expected_effect=str(data.get("expected_effect") or ""),
            observed_postcondition=str(data.get("observed_postcondition") or ""),
            confidence=float(data.get("confidence") or 0.0),
            embedding=tuple(float(value) for value in (data.get("embedding") or ())),
            cluster_id=str(data.get("cluster_id") or ""),
        )


class FunctionalityExtractor:
    """Deterministic fallback extractor for v4 functionality discovery.

    A strong VLM can later replace or augment this class.  The fallback remains
    useful for tests, dry runs, and low-cost operation because it derives
    functionality candidates from existing page metadata and verified actions.
    """

    def from_page(self, page: dict[str, Any], *, artifact_path: str = "") -> list[FunctionalityItem]:
        page_node_id = page_identity(page)
        app = str(page.get("app") or page.get("artifact_app") or "")
        page_type = str(page.get("page_type") or "unknown")
        items: list[FunctionalityItem] = []
        elements = page.get("elements") or {}
        if isinstance(elements, dict):
            for key, value in elements.items():
                label = str(key)
                description = str(value or key)
                item_type = classify_functionality_type(label, description)
                item = FunctionalityItem(
                    functionality_id=stable_id("fn", app, page_node_id, label, description),
                    page_node_id=page_node_id,
                    app=app,
                    page_type=page_type,
                    type=item_type,
                    label=label,
                    description=description,
                    text_evidence=description,
                    visual_evidence=f"{page_type}:{page.get('summary') or ''}",
                    confidence=0.65 if item_type == "functionality" else 0.55,
                )
                items.append(item)

        if not items:
            summary = str(page.get("summary") or page_type or "unknown screen")
            items.append(
                FunctionalityItem(
                    functionality_id=stable_id("fn", app, page_node_id, page_type, summary, artifact_path),
                    page_node_id=page_node_id,
                    app=app,
                    page_type=page_type,
                    type="functionality",
                    label=f"{page_type} visible functions",
                    description=f"Observed screen-level functionality on {page_type}: {summary}",
                    visual_evidence=summary,
                    text_evidence=summary,
                    confidence=0.4,
                )
            )
        return items

    def from_transition(self, transition: dict[str, Any], *, app: str = "") -> FunctionalityItem:
        source_type, source_summary = split_state_key(str(transition.get("from") or "unknown:"))
        target_type, target_summary = split_state_key(str(transition.get("to") or "unknown:"))
        action = dict(transition.get("action") or {})
        action_type = normalize_action_type(str(action.get("action") or action.get("action_type") or "action"))
        bbox = action_bbox(action)
        region = infer_region(bbox)
        label = transition_label(action_type, source_type, target_type, region)
        description = (
            f"{action_type} action from {source_type} to observed postcondition {target_type}. "
            f"Source summary: {source_summary or source_type}; target summary: {target_summary or target_type}."
        )
        return FunctionalityItem(
            functionality_id=stable_id("fn", app, source_type, action_type, region, target_type, json.dumps(action, sort_keys=True, ensure_ascii=False)),
            page_node_id=stable_id("page", app, source_type, source_summary),
            app=app,
            page_type=source_type,
            type="functionality",
            label=label,
            description=description,
            bbox=bbox,
            region=region,
            visual_evidence=f"{source_type}->{target_type}",
            text_evidence=target_summary or target_type,
            source_action=action,
            expected_effect=target_type,
            observed_postcondition=target_type,
            confidence=0.9,
        )


def page_identity(page: dict[str, Any]) -> str:
    app = str(page.get("app") or page.get("artifact_app") or "")
    page_type = str(page.get("page_type") or "unknown")
    summary = str(page.get("summary") or "")
    screenshot_hash = str(page.get("screenshot_hash") or "")
    return stable_id("page", app, page_type, summary, screenshot_hash)


def split_state_key(value: str) -> tuple[str, str]:
    source, _, summary = value.partition(":")
    return (source or "unknown", summary)


def normalize_action_type(value: str) -> str:
    key = (value or "").strip().lower()
    return ACTION_VERBS.get(key, key or "action")


def classify_functionality_type(label: str, description: str) -> str:
    text = f"{label} {description}".lower()
    return "data" if any(hint.lower() in text for hint in DATA_HINTS) else "functionality"


def action_bbox(action: dict[str, Any]) -> tuple[float, float, float, float] | None:
    element = action.get("element") or action.get("coordinate") or action.get("point")
    if isinstance(element, list) and len(element) == 1 and isinstance(element[0], list):
        element = element[0]
    if isinstance(element, list) and len(element) >= 2:
        try:
            x = float(element[0])
            y = float(element[1])
        except (TypeError, ValueError):
            return None
        return (x, y, x, y)
    return None


def infer_region(bbox: tuple[float, float, float, float] | None, *, normalized_size: tuple[int, int] = (1000, 1000)) -> str:
    if not bbox:
        return "unknown"
    width, height = normalized_size
    x = (bbox[0] + bbox[2]) / 2
    y = (bbox[1] + bbox[3]) / 2
    vertical = "top" if y <= height * 0.25 else "bottom" if y >= height * 0.75 else "middle"
    horizontal = "left" if x <= width * 0.33 else "right" if x >= width * 0.67 else "center"
    if vertical == "middle" and horizontal == "center":
        return "center"
    return f"{vertical}_{horizontal}"


def transition_label(action_type: str, source_type: str, target_type: str, region: str) -> str:
    region_label = "" if not region or region == "unknown" else f" {region}"
    return f"{action_type}{region_label} from {source_type} to {target_type}"
