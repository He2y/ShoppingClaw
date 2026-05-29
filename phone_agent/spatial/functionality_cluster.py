"""Functionality clustering for self-discovered AMSG coverage."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .core import stable_id
from .functionality import FunctionalityItem


@dataclass(frozen=True)
class FunctionalityCluster:
    cluster_id: str
    canonical_name: str
    canonical_description: str
    member_functionality_ids: tuple[str, ...] = ()
    apps: tuple[str, ...] = ()
    page_types: tuple[str, ...] = ()
    regions: tuple[str, ...] = ()
    verified_edges: tuple[str, ...] = ()
    success_count: int = 0
    fail_count: int = 0
    novelty_score: float = 1.0
    risk_level: str = "normal"

    @property
    def is_verified(self) -> bool:
        return self.success_count > 0

    @property
    def is_stable(self) -> bool:
        return self.success_count > 0 and len(self.member_functionality_ids) >= 2

    def to_dict(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "canonical_name": self.canonical_name,
            "canonical_description": self.canonical_description,
            "member_functionality_ids": list(self.member_functionality_ids),
            "apps": list(self.apps),
            "page_types": list(self.page_types),
            "regions": list(self.regions),
            "verified_edges": list(self.verified_edges),
            "success_count": self.success_count,
            "fail_count": self.fail_count,
            "novelty_score": self.novelty_score,
            "risk_level": self.risk_level,
        }


class FunctionalityClusterer:
    def __init__(self, similarity_threshold: float = 0.58):
        self.similarity_threshold = similarity_threshold

    def cluster(self, items: list[FunctionalityItem]) -> tuple[list[FunctionalityItem], list[FunctionalityCluster]]:
        clusters: list[list[FunctionalityItem]] = []
        for item in items:
            if item.type != "functionality" or not item.is_promotable:
                continue
            match_index = self._best_cluster(item, clusters)
            if match_index is None:
                clusters.append([item])
            else:
                clusters[match_index].append(item)

        clustered_items: list[FunctionalityItem] = []
        output_clusters: list[FunctionalityCluster] = []
        for members in clusters:
            cluster = build_cluster(members)
            output_clusters.append(cluster)
            clustered_items.extend(item.with_cluster(cluster.cluster_id) for item in members)

        non_clustered_items = [item for item in items if item.type != "functionality" or not item.is_promotable]
        clustered_items.extend(non_clustered_items)
        return clustered_items, output_clusters

    def _best_cluster(self, item: FunctionalityItem, clusters: list[list[FunctionalityItem]]) -> int | None:
        best_index: int | None = None
        best_score = 0.0
        for index, members in enumerate(clusters):
            score = max(functionality_similarity(item, member) for member in members)
            if score > best_score:
                best_index = index
                best_score = score
        if best_score >= self.similarity_threshold:
            return best_index
        return None


def build_cluster(members: list[FunctionalityItem]) -> FunctionalityCluster:
    representative = max(members, key=lambda item: (item.is_verified, item.confidence, len(item.description)))
    page_types = sorted({source_page_type(item) for item in members if source_page_type(item)})
    regions = sorted({item.region for item in members if item.region and item.region != "unknown"})
    verified_edges = sorted({edge_signature(item) for item in members if item.is_verified})
    success_count = sum(1 for item in members if item.is_verified)
    risk_level = infer_cluster_risk(members)
    role = representative.canonical_role or canonical_name(representative)
    cluster_id = stable_id("fn_cluster", role, representative.observed_postcondition, tuple(page_types), tuple(regions))
    return FunctionalityCluster(
        cluster_id=cluster_id,
        canonical_name=canonical_name(representative),
        canonical_description=representative.description,
        member_functionality_ids=tuple(item.functionality_id for item in members),
        apps=tuple(sorted({item.app or app_from_id(item.page_node_id) for item in members if item.app or app_from_id(item.page_node_id)})),
        page_types=tuple(page_types),
        regions=tuple(regions),
        verified_edges=tuple(verified_edges),
        success_count=success_count,
        fail_count=0,
        novelty_score=round(1.0 / max(1, len(members)), 4),
        risk_level=risk_level,
    )


def functionality_similarity(left: FunctionalityItem, right: FunctionalityItem) -> float:
    if left.type != "functionality" or right.type != "functionality":
        return 0.0
    if not left.is_promotable or not right.is_promotable:
        return 0.0
    left_role = left.canonical_role
    right_role = right.canonical_role
    if left_role and right_role:
        if left_role != right_role:
            return 0.0
        left_source = source_page_type(left)
        right_source = source_page_type(right)
        if left_source and right_source and left_source != right_source:
            return 0.0
        if left.observed_postcondition and right.observed_postcondition and left.observed_postcondition != right.observed_postcondition:
            return 0.0
        return 0.95 if left.region == right.region or not left.region or not right.region else 0.82
    if left.is_verified and right.is_verified:
        left_source = source_page_type(left)
        right_source = source_page_type(right)
        if left_source and right_source and left_source != right_source:
            return 0.0
        left_action = action_type(left)
        right_action = action_type(right)
        if left_action and right_action and left_action != right_action:
            return 0.0
    if left.observed_postcondition and right.observed_postcondition and left.observed_postcondition != right.observed_postcondition:
        return 0.0
    if left.observed_postcondition and left.observed_postcondition == right.observed_postcondition:
        if left.region == right.region or not left.region or not right.region:
            return 0.9
    if left.label == right.label and left.region == right.region:
        return 0.85
    left_tokens = token_set(left.label, left.description, left.text_evidence)
    right_tokens = token_set(right.label, right.description, right.text_evidence)
    if not left_tokens or not right_tokens:
        return 0.0
    jaccard = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    region_bonus = 0.12 if left.region and left.region == right.region else 0.0
    postcondition_bonus = 0.18 if left.observed_postcondition and left.observed_postcondition == right.observed_postcondition else 0.0
    return min(1.0, jaccard + region_bonus + postcondition_bonus)


def token_set(*parts: str) -> set[str]:
    text = " ".join(part for part in parts if part).lower()
    ascii_tokens = set(re.findall(r"[a-z0-9_]+", text))
    cjk_tokens = set(re.findall(r"[\u4e00-\u9fff]{2,}", text))
    return ascii_tokens | cjk_tokens


def canonical_name(item: FunctionalityItem) -> str:
    if item.canonical_role:
        return item.canonical_role
    if item.observed_postcondition:
        return f"{item.label} -> {item.observed_postcondition}"
    return item.label or item.description[:60] or "discovered functionality"


def edge_signature(item: FunctionalityItem) -> str:
    return f"{item.page_node_id}->{item.observed_postcondition}:{item.region or 'unknown'}"


def source_page_type(item: FunctionalityItem) -> str:
    if item.page_type:
        return item.page_type
    label_match = re.search(r"\bfrom ([a-z_]+) to\b", item.label)
    if label_match:
        return label_match.group(1)
    description_match = re.search(r"\bfrom ([a-z_]+) to observed postcondition\b", item.description)
    if description_match:
        return description_match.group(1)
    return page_type_from_id(item.page_node_id)


def action_type(item: FunctionalityItem) -> str:
    label_match = re.match(r"([a-z_]+)", item.label)
    if label_match:
        return label_match.group(1)
    action = item.source_action or {}
    return str(action.get("action") or action.get("action_type") or "").strip().lower()


def infer_cluster_risk(members: list[FunctionalityItem]) -> str:
    safe_roles = {
        "open_search",
        "submit_search",
        "open_product_detail",
        "open_filter_panel",
        "apply_or_close_filter",
        "rollback_to_search_result",
        "rollback_to_product_detail",
    }
    if members and all((item.canonical_role or "") in safe_roles for item in members):
        return "normal"
    risky_postconditions = {"checkout", "payment", "address", "login"}
    if any((item.observed_postcondition or "").lower() in risky_postconditions for item in members):
        return "high"
    high_tokens = ("payment", "checkout", "address", "支付", "付款", "结算", "提交订单", "地址")
    medium_tokens = ("cart", "spec", "购物车", "规格")
    text = " ".join(
        f"{item.label} {item.description} {item.expected_effect} {item.observed_postcondition}"
        for item in members
    ).lower()
    if any(token.lower() in text for token in high_tokens):
        return "high"
    if any(token.lower() in text for token in medium_tokens):
        return "medium"
    return "normal"


def page_type_from_id(page_node_id: str) -> str:
    # Stable ids are lossy labels. This helper is only for report grouping; the
    # exact page type is also preserved in item descriptions and edge evidence.
    for marker in ("search_input", "search_result", "product_detail", "spec_selection", "filter_panel", "checkout", "payment", "cart", "home"):
        if marker in page_node_id:
            return marker
    return ""


def app_from_id(page_node_id: str) -> str:
    for marker in ("taobao", "jd", "pinduoduo", "meituan", "eleme"):
        if marker in page_node_id:
            return marker
    return ""


# ── Embedding-based clusterer (Definition 6) ────────────────────────


def _cosine_sim(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    import math

    dot = sum(ai * bi for ai, bi in zip(a, b))
    norm_a = math.sqrt(sum(ai * ai for ai in a))
    norm_b = math.sqrt(sum(bi * bi for bi in b))
    if norm_a < 1e-12 or norm_b < 1e-12:
        return 0.0
    return dot / (norm_a * norm_b)


class EmbeddingFunctionalityClusterer:
    """Cosine-similarity clustering using FunctionalityItem.embedding.

    Falls back to Jaccard-based ``functionality_similarity`` when embeddings
    are missing, ensuring graceful degradation.  Structural bonuses for
    region and postcondition match are preserved.
    """

    def __init__(self, similarity_threshold: float = 0.72):
        self.similarity_threshold = similarity_threshold
        self._jaccard_fallback = FunctionalityClusterer(similarity_threshold=0.58)

    def cluster(self, items: list[FunctionalityItem]) -> tuple[list[FunctionalityItem], list[FunctionalityCluster]]:
        # Check if any items have embeddings
        has_embeddings = any(item.embedding for item in items if item.type == "functionality" and item.is_promotable)
        if not has_embeddings:
            return self._jaccard_fallback.cluster(items)

        clusters: list[list[FunctionalityItem]] = []
        for item in items:
            if item.type != "functionality" or not item.is_promotable:
                continue
            match_index = self._best_cluster(item, clusters)
            if match_index is None:
                clusters.append([item])
            else:
                clusters[match_index].append(item)

        clustered_items: list[FunctionalityItem] = []
        output_clusters: list[FunctionalityCluster] = []
        for members in clusters:
            cluster_obj = build_cluster(members)
            output_clusters.append(cluster_obj)
            clustered_items.extend(item.with_cluster(cluster_obj.cluster_id) for item in members)

        non_clustered = [item for item in items if item.type != "functionality" or not item.is_promotable]
        clustered_items.extend(non_clustered)
        return clustered_items, output_clusters

    def _best_cluster(self, item: FunctionalityItem, clusters: list[list[FunctionalityItem]]) -> int | None:
        best_index: int | None = None
        best_score = 0.0
        for index, members in enumerate(clusters):
            score = max(self._embedding_similarity(item, member) for member in members)
            if score > best_score:
                best_index = index
                best_score = score
        if best_score >= self.similarity_threshold:
            return best_index
        return None

    @staticmethod
    def _embedding_similarity(left: FunctionalityItem, right: FunctionalityItem) -> float:
        """Cosine similarity with structural bonuses (region, postcondition)."""
        if not left.embedding or not right.embedding:
            return functionality_similarity(left, right)

        base = _cosine_sim(left.embedding, right.embedding)
        region_bonus = 0.08 if left.region and left.region == right.region else 0.0
        postcondition_bonus = 0.12 if (
            left.observed_postcondition
            and left.observed_postcondition == right.observed_postcondition
        ) else 0.0
        return min(1.0, base + region_bonus + postcondition_bonus)
