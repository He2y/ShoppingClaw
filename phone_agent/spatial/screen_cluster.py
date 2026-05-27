"""Screen clustering utilities for AMSG v4 exploration artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .core import stable_id


@dataclass(frozen=True)
class ScreenCluster:
    cluster_id: str
    representative_hash: str = ""
    app: str = ""
    page_type: str = "unknown"
    summaries: tuple[str, ...] = ()
    screenshot_hashes: tuple[str, ...] = ()
    artifact_paths: tuple[str, ...] = ()
    page_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "representative_hash": self.representative_hash,
            "app": self.app,
            "page_type": self.page_type,
            "summaries": list(self.summaries),
            "screenshot_hashes": list(self.screenshot_hashes),
            "artifact_paths": list(self.artifact_paths),
            "page_count": self.page_count,
        }


class ScreenClusterer:
    """Cluster screens by app, page type, and screenshot hash.

    If future artifacts include perceptual hashes, callers can pass them in the
    ``screenshot_hash`` field.  The current exploration artifacts already store
    stable visual hashes, which are enough for deterministic v4 dry runs.
    """

    def cluster(self, pages: list[dict[str, Any]]) -> list[ScreenCluster]:
        buckets: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for page in pages:
            app = str(page.get("app") or page.get("artifact_app") or "")
            page_type = str(page.get("page_type") or "unknown")
            screenshot_hash = str(page.get("screenshot_hash") or "")
            summary = str(page.get("summary") or "")
            key_hash = screenshot_hash or summary.lower() or page_type
            buckets.setdefault((app.lower(), page_type, key_hash), []).append(page)

        clusters: list[ScreenCluster] = []
        for (app, page_type, key_hash), members in sorted(buckets.items(), key=lambda item: item[0]):
            summaries = tuple(sorted({str(page.get("summary") or "") for page in members if page.get("summary")}))
            hashes = tuple(sorted({str(page.get("screenshot_hash") or "") for page in members if page.get("screenshot_hash")}))
            paths = tuple(sorted({str(page.get("artifact_path") or "") for page in members if page.get("artifact_path")}))
            clusters.append(
                ScreenCluster(
                    cluster_id=stable_id("screen_cluster", app, page_type, key_hash),
                    representative_hash=key_hash,
                    app=app,
                    page_type=page_type,
                    summaries=summaries,
                    screenshot_hashes=hashes,
                    artifact_paths=paths,
                    page_count=len(members),
                )
            )
        return clusters


def duplicate_screen_compression_ratio(page_count: int, cluster_count: int) -> float:
    if page_count <= 0:
        return 0.0
    return round(1.0 - (cluster_count / page_count), 4)
