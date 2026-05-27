"""Coverage metrics for self-discovered AMSG functionality."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .functionality import FunctionalityItem
from .functionality_cluster import FunctionalityCluster
from .screen_cluster import duplicate_screen_compression_ratio


@dataclass(frozen=True)
class FunctionalityCoverageMetrics:
    discovered_functionalities: int
    discovered_functionality_clusters: int
    verified_functionality_clusters: int
    stable_functionality_clusters: int
    verified_functionality_ratio: float
    novelty_discovery_rate: float
    saturation_per_50_screens: tuple[int, ...]
    recovery_functionality_coverage: float
    risk_boundary_coverage: float
    duplicate_screen_compression_ratio: float
    functionality_semantics_quality: str = "limited"

    def to_dict(self) -> dict[str, Any]:
        return {
            "discovered_functionalities": self.discovered_functionalities,
            "discovered_functionality_clusters": self.discovered_functionality_clusters,
            "verified_functionality_clusters": self.verified_functionality_clusters,
            "stable_functionality_clusters": self.stable_functionality_clusters,
            "verified_functionality_ratio": self.verified_functionality_ratio,
            "novelty_discovery_rate": self.novelty_discovery_rate,
            "saturation_per_50_screens": list(self.saturation_per_50_screens),
            "recovery_functionality_coverage": self.recovery_functionality_coverage,
            "risk_boundary_coverage": self.risk_boundary_coverage,
            "duplicate_screen_compression_ratio": self.duplicate_screen_compression_ratio,
            "functionality_semantics_quality": self.functionality_semantics_quality,
        }


def compute_functionality_coverage(
    *,
    items: list[FunctionalityItem],
    clusters: list[FunctionalityCluster],
    screenshot_count: int,
    screen_cluster_count: int,
    strong_vlm_configured: bool = False,
    window_size: int = 50,
) -> FunctionalityCoverageMetrics:
    functionality_items = [item for item in items if item.type == "functionality"]
    verified_clusters = [cluster for cluster in clusters if cluster.is_verified]
    stable_clusters = [cluster for cluster in clusters if cluster.is_stable]
    high_risk_clusters = [cluster for cluster in clusters if cluster.risk_level == "high"]
    covered_high_risk = [cluster for cluster in high_risk_clusters if cluster.success_count > 0 or cluster.fail_count > 0]
    recovery_clusters = [
        cluster
        for cluster in clusters
        if any(token in cluster.canonical_name.lower() for token in ("back", "rollback", "close", "dialog", "返回", "关闭"))
    ]
    verified_recovery = [cluster for cluster in recovery_clusters if cluster.is_verified]
    cluster_count = len(clusters)
    verified_ratio = round(len(verified_clusters) / cluster_count, 4) if cluster_count else 0.0
    novelty_rate = round(cluster_count / max(1, screenshot_count), 4)
    saturation = saturation_curve(items, window_size=window_size)
    risk_coverage = round(len(covered_high_risk) / len(high_risk_clusters), 4) if high_risk_clusters else 1.0
    recovery_coverage = round(len(verified_recovery) / len(recovery_clusters), 4) if recovery_clusters else 0.0
    quality = "strong_vlm" if strong_vlm_configured else "limited"
    return FunctionalityCoverageMetrics(
        discovered_functionalities=len(functionality_items),
        discovered_functionality_clusters=cluster_count,
        verified_functionality_clusters=len(verified_clusters),
        stable_functionality_clusters=len(stable_clusters),
        verified_functionality_ratio=verified_ratio,
        novelty_discovery_rate=novelty_rate,
        saturation_per_50_screens=tuple(saturation),
        recovery_functionality_coverage=recovery_coverage,
        risk_boundary_coverage=risk_coverage,
        duplicate_screen_compression_ratio=duplicate_screen_compression_ratio(screenshot_count, screen_cluster_count),
        functionality_semantics_quality=quality,
    )


def saturation_curve(items: list[FunctionalityItem], *, window_size: int = 50) -> list[int]:
    if window_size <= 0:
        window_size = 50
    seen: set[str] = set()
    curve: list[int] = []
    current_new = 0
    for index, item in enumerate((item for item in items if item.type == "functionality"), 1):
        identity = item.cluster_id or item.functionality_id
        if identity not in seen:
            seen.add(identity)
            current_new += 1
        if index % window_size == 0:
            curve.append(current_new)
            current_new = 0
    if current_new or not curve:
        curve.append(current_new)
    return curve
