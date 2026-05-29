"""Self-discovered functionality primitives for AMSG v4.

The v4 graph does not assume a hand-written list of app-specific feature
buckets. It extracts candidate functions and data from observed screens and
verified transitions, then lets clustering and postcondition evidence decide
what is worth promoting into the graph.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .core import stable_id


DATA_HINTS = (
    "price",
    "amount",
    "title",
    "product_title",
    "shop",
    "store",
    "sales",
    "rating",
    "review",
    "delivery",
    "coupon",
    "promotion",
    "sku_text",
    "query",
    "search_query",
    "keyword",
    "address_data",
    "商品名",
    "商品标题",
    "标题",
    "价格",
    "价",
    "销量",
    "店铺",
    "评分",
    "评价",
    "配送",
    "优惠",
    "规格文本",
    "¥",
    "￥",
    "楼",
    "浠锋牸",
    "閿€閲",
    "搴楅摵",
    "璇勫垎",
    "璇勪环",
)

FUNCTION_HINTS = (
    "search",
    "filter",
    "sort",
    "product",
    "card",
    "cart",
    "add",
    "buy",
    "spec",
    "sku",
    "confirm",
    "close",
    "back",
    "button",
    "cta",
    "tab",
    "搜索",
    "筛选",
    "排序",
    "商品卡",
    "购物车",
    "加购",
    "购买",
    "规格",
    "确定",
    "确认",
    "关闭",
    "返回",
    "按钮",
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
    source_kind: str = ""
    canonical_role: str = ""
    is_promotable: bool = True
    screen_cluster_id: str = ""
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
            "source_kind": self.source_kind,
            "canonical_role": self.canonical_role,
            "is_promotable": self.is_promotable,
            "screen_cluster_id": self.screen_cluster_id,
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
            source_kind=str(data.get("source_kind") or ""),
            canonical_role=str(data.get("canonical_role") or ""),
            is_promotable=bool(data.get("is_promotable", True)),
            screen_cluster_id=str(data.get("screen_cluster_id") or ""),
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
    """Deterministic extractor for v4 functionality and data discovery.

    Strong VLM extraction can feed richer page ``elements`` or text evidence.
    This class keeps a deterministic fallback that refuses to promote vague
    page-level placeholders such as ``search_result visible functions``.

    An optional ``role_classifier`` (Definition 6) can be injected to replace
    the keyword-based classification with embedding-based prototype matching.
    When ``role_classifier`` is None, the legacy inline logic is used.
    """

    def __init__(self, role_classifier: Any | None = None) -> None:
        self._role_classifier = role_classifier

    def _classify(self, page_type: str, label: str, description: str) -> tuple[str, str]:
        """Route classification through injected classifier or legacy inline."""
        if self._role_classifier is not None:
            return self._role_classifier.classify(page_type, label, description)
        item_type = classify_functionality_type(label, description)
        canonical_role = canonical_role_from_element(page_type, label, description, item_type=item_type)
        return (canonical_role, item_type)

    def from_page(self, page: dict[str, Any], *, artifact_path: str = "") -> list[FunctionalityItem]:
        page_node_id = page_identity(page)
        app = str(page.get("app") or page.get("artifact_app") or "")
        page_type = str(page.get("page_type") or "unknown")
        screen_cluster_id = str(page.get("screen_cluster_id") or "")
        items: list[FunctionalityItem] = []

        elements = page.get("elements") or {}
        if isinstance(elements, dict):
            for key, value in elements.items():
                label = str(key)
                description = str(value or key)
                canonical_role, item_type = self._classify(page_type, label, description)
                items.append(
                    FunctionalityItem(
                        functionality_id=stable_id("fn", app, page_node_id, label, description),
                        page_node_id=page_node_id,
                        app=app,
                        page_type=page_type,
                        type=item_type,
                        source_kind="element",
                        canonical_role=canonical_role,
                        is_promotable=item_type == "functionality" and bool(canonical_role),
                        screen_cluster_id=screen_cluster_id,
                        label=label,
                        description=description,
                        text_evidence=description,
                        visual_evidence=f"{page_type}:{page.get('summary') or ''}",
                        confidence=0.72 if item_type == "functionality" else 0.6,
                    )
                )

        evidence_text = " ".join(
            str(page.get(key) or "")
            for key in ("summary", "task", "thinking_evidence", "text_evidence")
        )
        items.extend(
            extract_data_items_from_text(
                evidence_text,
                app=app,
                page_type=page_type,
                page_node_id=page_node_id,
                screen_cluster_id=screen_cluster_id,
                artifact_path=artifact_path,
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
        role = canonical_role_from_transition(source_type, target_type, action_type, region, action)
        label = role or transition_label(action_type, source_type, target_type, region)
        description = (
            f"{label} by {action_type} action from {source_type} to observed postcondition {target_type}. "
            f"Source summary: {source_summary or source_type}; target summary: {target_summary or target_type}."
        )
        return FunctionalityItem(
            functionality_id=stable_id(
                "fn",
                app,
                source_type,
                action_type,
                role,
                region,
                target_type,
                json.dumps(action, sort_keys=True, ensure_ascii=False),
            ),
            page_node_id=stable_id("page", app, source_type, source_summary),
            app=app,
            page_type=source_type,
            type="functionality",
            source_kind="transition",
            canonical_role=role,
            is_promotable=True,
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


class StrongVLMFunctionalityExtractor:
    """Optional OpenAI-compatible extractor for screenshot-backed pages."""

    def __init__(self, config: Any):
        self.config = config

    def from_page(self, page: dict[str, Any]) -> list[FunctionalityItem]:
        image_base64 = str(page.get("screenshot_base64") or page.get("image_base64") or "")
        if not image_base64 and page.get("screenshot_path"):
            image_base64 = _read_image_base64(str(page.get("screenshot_path") or ""))
        if not image_base64 or not getattr(self.config, "configured", False):
            return []
        try:
            from openai import OpenAI

            client = OpenAI(base_url=self.config.base_url, api_key=self.config.api_key)
            response = client.chat.completions.create(
                model=self.config.model,
                temperature=0,
                max_tokens=1400,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Extract mobile UI functionality and data items. Return JSON only with an items array. "
                            "Each item must include type(functionality|data), label, description, canonical_role, "
                            "bbox(optional [x1,y1,x2,y2] normalized 0-1000), confidence."
                        ),
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    f"app={page.get('app') or page.get('artifact_app')}; "
                                    f"page_type={page.get('page_type')}; summary={page.get('summary')}. "
                                    "Separate clickable controls from data such as title, price, shop, sales, reviews, "
                                    "promotion, delivery, and SKU text. Do not invent hidden functions."
                                ),
                            },
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_base64}"}},
                        ],
                    },
                ],
            )
            content = response.choices[0].message.content or ""
            data = _extract_json_object(content)
        except Exception:
            return []
        if not isinstance(data, dict):
            return []
        return self._items_from_vlm(page, data.get("items") or [])

    def _items_from_vlm(self, page: dict[str, Any], raw_items: list[Any]) -> list[FunctionalityItem]:
        page_node_id = page_identity(page)
        app = str(page.get("app") or page.get("artifact_app") or "")
        page_type = str(page.get("page_type") or "unknown")
        screen_cluster_id = str(page.get("screen_cluster_id") or "")
        items: list[FunctionalityItem] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            item_type = "data" if str(raw.get("type") or "").lower() == "data" else "functionality"
            label = str(raw.get("label") or raw.get("canonical_role") or "")
            description = str(raw.get("description") or label)
            role = str(raw.get("canonical_role") or canonical_role_from_element(page_type, label, description, item_type=item_type))
            if item_type == "functionality" and not role:
                continue
            bbox = raw.get("bbox")
            items.append(
                FunctionalityItem(
                    functionality_id=stable_id("fn_vlm", app, page_node_id, item_type, role, label, description),
                    page_node_id=page_node_id,
                    app=app,
                    page_type=page_type,
                    type=item_type,
                    source_kind="strong_vlm",
                    canonical_role=role,
                    is_promotable=item_type == "functionality",
                    screen_cluster_id=screen_cluster_id,
                    label=label or role,
                    description=description,
                    bbox=tuple(float(value) for value in bbox) if isinstance(bbox, list) and len(bbox) >= 4 else None,
                    region=infer_region(tuple(float(value) for value in bbox)) if isinstance(bbox, list) and len(bbox) >= 4 else "",
                    visual_evidence=str(page.get("screenshot_hash") or ""),
                    text_evidence=description,
                    confidence=float(raw.get("confidence") or (0.8 if item_type == "functionality" else 0.65)),
                )
            )
        return items


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
    if any(hint.lower() in text for hint in DATA_HINTS):
        return "data"
    if any(hint.lower() in text for hint in FUNCTION_HINTS):
        return "functionality"
    return "data"


def canonical_role_from_element(page_type: str, label: str, description: str, *, item_type: str) -> str:
    if item_type == "data":
        return canonical_data_role(label, description)
    text = f"{label} {description}".lower()
    if any(token in text for token in ("search", "搜索")):
        return "open_search" if page_type == "home" else "submit_search"
    if any(token in text for token in ("filter", "筛选")):
        return "open_filter_panel" if page_type == "search_result" else "apply_or_close_filter"
    if any(token in text for token in ("top_cart", "cart_icon", "购物车")) and page_type == "product_detail":
        return "open_cart_from_header"
    if any(token in text for token in ("product", "card", "商品卡")):
        return "open_product_detail"
    if any(token in text for token in ("bottom_cta", "buy", "add", "购买", "加购", "cta")):
        return "open_spec_selector" if page_type == "product_detail" else "confirm_spec_add_to_cart"
    if any(token in text for token in ("spec", "sku", "规格")):
        return "choose_spec"
    if any(token in text for token in ("confirm", "确定", "确认")):
        return "confirm_spec_add_to_cart" if page_type == "spec_selection" else "apply_or_close_filter"
    if any(token in text for token in ("close", "back", "返回", "关闭")):
        return f"rollback_from_{page_type}"
    return ""


def canonical_data_role(label: str, description: str) -> str:
    text = f"{label} {description}".lower()
    if any(token in text for token in ("price", "价格", "价", "¥", "￥", "楼", "浠锋牸")):
        return "price"
    if any(token in text for token in ("title", "商品名", "商品标题", "标题")):
        return "product_title"
    if any(token in text for token in ("shop", "store", "店铺", "搴楅摵")):
        return "shop_name"
    if any(token in text for token in ("sales", "销量", "閿€閲")):
        return "sales_volume"
    if any(token in text for token in ("review", "rating", "评价", "评分", "璇勪环", "璇勫垎")):
        return "review_signal"
    if any(token in text for token in ("spec", "sku", "规格")):
        return "spec_text"
    if any(token in text for token in ("coupon", "promotion", "优惠", "补贴")):
        return "promotion"
    return "page_data"


def canonical_role_from_transition(
    source_type: str,
    target_type: str,
    action_type: str,
    region: str,
    action: dict[str, Any],
) -> str:
    if source_type == "home" and target_type == "search_input":
        return "open_search"
    if source_type == "search_input" and target_type == "search_result":
        return "submit_search"
    if source_type == "search_result" and target_type == "product_detail":
        return "open_product_detail"
    if source_type == "search_result" and target_type == "filter_panel":
        return "open_filter_panel"
    if source_type == "filter_panel" and target_type == "search_result":
        return "apply_or_close_filter"
    if source_type == "product_detail" and target_type == "spec_selection":
        return "open_spec_selector"
    if source_type == "product_detail" and target_type == "cart":
        return "open_cart_from_header" if region.startswith("top") else "open_cart"
    if source_type == "spec_selection" and target_type == "cart":
        return "confirm_spec_add_to_cart"
    if source_type == "spec_selection" and target_type == "product_detail":
        return "rollback_to_product_detail" if action_type == "back" else "confirm_add_to_cart_success"
    if source_type == "cart":
        return f"rollback_from_cart_to_{target_type}"
    if action_type == "back":
        return f"rollback_to_{target_type}"
    return f"{action_type}_{target_type}".strip("_")


def extract_data_items_from_text(
    text: str,
    *,
    app: str,
    page_type: str,
    page_node_id: str,
    screen_cluster_id: str = "",
    artifact_path: str = "",
) -> list[FunctionalityItem]:
    text = text or ""
    items: list[FunctionalityItem] = []
    seen: set[tuple[str, str]] = set()

    def add(role: str, value: str, confidence: float = 0.58) -> None:
        value = re.sub(r"\s+", " ", value).strip()
        if not value:
            return
        key = (role, value[:80])
        if key in seen:
            return
        seen.add(key)
        items.append(
            FunctionalityItem(
                functionality_id=stable_id("data", app, page_node_id, role, value, artifact_path),
                page_node_id=page_node_id,
                app=app,
                page_type=page_type,
                type="data",
                source_kind="text_evidence",
                canonical_role=role,
                is_promotable=False,
                screen_cluster_id=screen_cluster_id,
                label=role,
                description=value[:160],
                text_evidence=value[:300],
                visual_evidence=f"{page_type}:{role}",
                confidence=confidence,
            )
        )

    for match in re.finditer(r"(?:[¥￥$楼]\s*\d+(?:\.\d+)?|\d+(?:\.\d+)?\s*元)", text):
        add("price", match.group(0), 0.72)
    if any(token in text for token in ("店", "搴楅摵", "shop", "store")):
        add("shop_name", "shop/store signal observed", 0.5)
    if any(token in text for token in ("销量", "已售", "閿€閲", "sales")):
        add("sales_volume", "sales signal observed", 0.5)
    if any(token in text for token in ("评价", "评分", "璇勪环", "璇勫垎", "review", "rating")):
        add("review_signal", "review/rating signal observed", 0.5)
    if page_type in {"product_detail", "search_result"} and any(token in text.lower() for token in ("iphone", "商品", "product", "鍟嗗搧")):
        add("product_title", "product title or card text observed", 0.52)
    if page_type in {"spec_selection", "filter_panel"} and any(token in text for token in ("规格", "筛选", "价格区间", "瑙勬牸", "绛涢€")):
        add("spec_text" if page_type == "spec_selection" else "filter_option", "option text observed", 0.52)
    return items


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


def _read_image_base64(path: str) -> str:
    try:
        import base64

        with open(path, "rb") as handle:
            return base64.b64encode(handle.read()).decode("ascii")
    except OSError:
        return ""


def _extract_json_object(content: str) -> Any:
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?", "", content).strip()
        content = re.sub(r"```$", "", content).strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, flags=re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
