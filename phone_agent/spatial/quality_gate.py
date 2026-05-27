"""Quality gates for AMSG v4 functionality promotion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .functionality_cluster import FunctionalityCluster


@dataclass(frozen=True)
class FunctionalityQualityResult:
    passed: bool
    reasons: tuple[str, ...]
    metrics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "reasons": list(self.reasons),
            "metrics": dict(self.metrics),
        }


class FunctionalityQualityGate:
    def __init__(self, *, min_verified_ratio: float = 0.0, allow_high_risk_verified: bool = False):
        self.min_verified_ratio = min_verified_ratio
        self.allow_high_risk_verified = allow_high_risk_verified

    def evaluate(self, clusters: list[FunctionalityCluster], coverage_metrics: dict[str, Any]) -> FunctionalityQualityResult:
        reasons: list[str] = []
        verified_ratio = float(coverage_metrics.get("verified_functionality_ratio") or 0.0)
        if verified_ratio < self.min_verified_ratio:
            reasons.append(f"verified functionality ratio below threshold: {verified_ratio} < {self.min_verified_ratio}")
        if not self.allow_high_risk_verified:
            high_risk_verified = [cluster.cluster_id for cluster in clusters if cluster.risk_level == "high" and cluster.success_count > 0]
            if high_risk_verified:
                reasons.append(f"high-risk functionality clusters should not be directly promoted: {len(high_risk_verified)}")
        broad_unverified = [
            cluster.cluster_id
            for cluster in clusters
            if cluster.success_count == 0 and len(cluster.page_types) >= 3
        ]
        if broad_unverified:
            reasons.append(f"over-broad unverified functionality clusters: {len(broad_unverified)}")
        return FunctionalityQualityResult(
            passed=not reasons,
            reasons=tuple(reasons),
            metrics=coverage_metrics,
        )
