"""Research-oriented dry-run reports for AMSG graph construction."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from phone_agent.memory.import_exploration import find_page_files

from .active_builder import ActiveGraphBuilder
from .coverage_metrics import compute_functionality_coverage
from .exploration_queue import ExplorationQueueBuilder
from .functionality import FunctionalityExtractor
from .functionality_cluster import FunctionalityClusterer
from .hypothesis import EdgeHypothesisGenerator
from .quality_gate import FunctionalityQualityGate
from .schema_registry import SchemaRegistry
from .screen_cluster import ScreenClusterer
from .semantics import ScreenSemanticsExtractor
from .task_synthesis import TaskSynthesizer, resolve_embedding_config, resolve_strong_vlm_config


def build_amsg_dry_run_report(
    *,
    exploration_root: str | Path,
    rebuild_report: dict[str, Any] | None = None,
    app_filter: str | None = None,
    domain: str = "shopping",
    max_frontiers_per_page: int = 3,
) -> dict[str, Any]:
    """Summarize existing artifacts through the AMSG schema/frontier lens."""
    registry = SchemaRegistry()
    schema = registry.merged(domain)
    extractor = ScreenSemanticsExtractor(schema_name=domain)
    generator = EdgeHypothesisGenerator(schema_name=domain)
    scorer = ActiveGraphBuilder()

    safe_schema_page_types = sorted(
        page_type
        for page_type, spec in schema.page_types.items()
        if page_type != "unknown" and spec.risk != "high"
    )
    pages = _load_pages(exploration_root, registry=registry, app_filter=app_filter)
    observed_page_types = sorted({str(page.get("page_type") or "unknown") for page in pages})
    missing_safe_page_types = [
        page_type
        for page_type in safe_schema_page_types
        if not schema.page_type_covered(page_type, observed_page_types)
    ]
    safe_schema_coverage = (
        round((len(safe_schema_page_types) - len(missing_safe_page_types)) / len(safe_schema_page_types), 4)
        if safe_schema_page_types
        else 0.0
    )

    frontier_items: list[dict[str, Any]] = []
    total_hypotheses = 0
    high_risk_hypotheses = 0
    safe_hypotheses = 0
    goal_page_types = set(missing_safe_page_types)
    for page in pages:
        node = extractor.node_from_exploration_page(page, fallback_app=str(page.get("artifact_app") or ""))
        hypotheses = generator.generate(node, goal_page_types=goal_page_types)
        total_hypotheses += len(hypotheses)
        high_risk_hypotheses += sum(1 for item in hypotheses if item.risk == "high")
        safe_hypotheses += sum(1 for item in hypotheses if item.risk != "high")
        ranked = scorer.rank(hypotheses)[:max_frontiers_per_page]
        if ranked:
            frontier_items.append(
                {
                    "page_type": node.page_type,
                    "summary": node.summary[:120],
                    "node_id": node.node_id,
                    "frontiers": [item.to_dict() for item in ranked],
                }
            )

    promoted_edges, transitions_seen = _edge_counts(rebuild_report or {})
    valid_edge_ratio = round(promoted_edges / transitions_seen, 4) if transitions_seen else 0.0
    return {
        "method": "AMSG dry-run",
        "domain": domain,
        "app_filter": app_filter,
        "artifact_count": len({str(page.get("artifact_path") or "") for page in pages}),
        "graph_construction_cost": {
            "screenshots": len(pages),
            "candidate_edge_hypotheses": total_hypotheses,
            "promoted_edges": promoted_edges,
            "transitions_seen": transitions_seen,
        },
        "schema_coverage": {
            "safe_schema_page_types": safe_schema_page_types,
            "observed_page_types": observed_page_types,
            "missing_safe_page_types": missing_safe_page_types,
            "safe_schema_coverage": safe_schema_coverage,
        },
        "edge_quality": {
            "valid_edge_ratio": valid_edge_ratio,
            "safe_hypotheses": safe_hypotheses,
            "high_risk_hypotheses": high_risk_hypotheses,
            "high_risk_hypothesis_ratio": round(high_risk_hypotheses / total_hypotheses, 4) if total_hypotheses else 0.0,
        },
        "active_frontiers": frontier_items,
    }


def format_amsg_markdown(report: dict[str, Any]) -> str:
    """Render an AMSG dry-run report for docs/paper note taking."""
    cost = report.get("graph_construction_cost", {})
    coverage = report.get("schema_coverage", {})
    quality = report.get("edge_quality", {})
    lines = [
        "# AMSG v3 Dry-Run Report",
        "",
        "## Summary",
        "",
        f"- Domain: `{report.get('domain', '')}`",
        f"- App filter: `{report.get('app_filter') or 'all'}`",
        f"- Artifacts: `{report.get('artifact_count', 0)}`",
        f"- Screenshots/pages: `{cost.get('screenshots', 0)}`",
        f"- Candidate edge hypotheses: `{cost.get('candidate_edge_hypotheses', 0)}`",
        f"- Promoted edges: `{cost.get('promoted_edges', 0)}` / transitions seen `{cost.get('transitions_seen', 0)}`",
        f"- Valid edge ratio: `{quality.get('valid_edge_ratio', 0.0)}`",
        f"- Safe schema coverage: `{coverage.get('safe_schema_coverage', 0.0)}`",
        "",
        "## Schema Coverage",
        "",
        f"- Observed: {', '.join(coverage.get('observed_page_types') or []) or 'none'}",
        f"- Missing safe page types: {', '.join(coverage.get('missing_safe_page_types') or []) or 'none'}",
        "",
        "## Active Frontier Samples",
        "",
    ]
    for item in (report.get("active_frontiers") or [])[:12]:
        lines.append(f"### {item.get('page_type', 'unknown')}: {item.get('summary', '')}")
        for frontier in item.get("frontiers", []):
            lines.append(
                "- "
                f"`{frontier.get('source_page_type')}` -> `{frontier.get('expected_page_type')}` "
                f"via `{frontier.get('intent')}` / `{frontier.get('semantic_target')}`, "
                f"score `{frontier.get('score')}`, risk `{frontier.get('risk')}`"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_amsg_v4_functionality_report(
    *,
    exploration_root: str | Path,
    rebuild_report: dict[str, Any] | None = None,
    app_filter: str | None = None,
    domain: str = "shopping",
    max_clusters: int = 20,
    max_jobs: int = 20,
) -> dict[str, Any]:
    """Build an OpenMobile-inspired self-discovered functionality report.

    Unlike schema coverage, this report does not assume a manual list of target
    feature buckets.  It extracts candidate functionality from observed pages
    and verified transitions, clusters them, and reports discovery/verification
    saturation.
    """
    registry = SchemaRegistry()
    pages = _load_pages(exploration_root, registry=registry, app_filter=app_filter)
    transitions = _load_transitions(exploration_root, registry=registry, app_filter=app_filter)
    functionality_extractor = FunctionalityExtractor()
    functionality_items = []
    for page in pages:
        functionality_items.extend(functionality_extractor.from_page(page, artifact_path=str(page.get("artifact_path") or "")))
    for transition in transitions:
        functionality_items.append(functionality_extractor.from_transition(transition, app=str(transition.get("artifact_app") or app_filter or "")))

    clustered_items, clusters = FunctionalityClusterer().cluster(functionality_items)
    screen_clusters = ScreenClusterer().cluster(pages)
    strong_vlm = resolve_strong_vlm_config()
    embedding = resolve_embedding_config()
    coverage = compute_functionality_coverage(
        items=clustered_items,
        clusters=clusters,
        screenshot_count=len(pages),
        screen_cluster_count=len(screen_clusters),
        strong_vlm_configured=strong_vlm.configured,
    )
    jobs = ExplorationQueueBuilder().build_jobs(clusters, limit=max_jobs)
    quality = FunctionalityQualityGate().evaluate(clusters, coverage.to_dict())
    promoted_edges, transitions_seen = _edge_counts(rebuild_report or {})
    task_synthesizer = TaskSynthesizer(strong_vlm)
    sample_tasks = [
        {
            "cluster_id": cluster.cluster_id,
            "task": task_synthesizer.fallback_task(cluster, app=app_filter or "target app"),
        }
        for cluster in clusters[: min(5, len(clusters))]
    ]
    return {
        "method": "AMSG v4 self-discovered functionality coverage",
        "domain": domain,
        "app_filter": app_filter,
        "artifact_count": len({str(page.get("artifact_path") or "") for page in pages}),
        "graph_construction_cost": {
            "screenshots": len(pages),
            "screen_clusters": len(screen_clusters),
            "functionality_items": len(clustered_items),
            "functionality_clusters": len(clusters),
            "promoted_edges": promoted_edges,
            "transitions_seen": transitions_seen,
        },
        "functionality_coverage": coverage.to_dict(),
        "quality_gate": quality.to_dict(),
        "model_config": {
            "strong_vlm": strong_vlm.to_dict(),
            "embedding": embedding.to_dict(),
        },
        "functionality_clusters": [cluster.to_dict() for cluster in clusters[:max_clusters]],
        "exploration_jobs": [job.to_dict() for job in jobs],
        "sample_synthesized_tasks": sample_tasks,
    }


def format_amsg_v4_markdown(report: dict[str, Any]) -> str:
    cost = report.get("graph_construction_cost", {})
    coverage = report.get("functionality_coverage", {})
    quality = report.get("quality_gate", {})
    lines = [
        "# AMSG v4 Self-Discovered Functionality Report",
        "",
        "## Summary",
        "",
        f"- Domain: `{report.get('domain', '')}`",
        f"- App filter: `{report.get('app_filter') or 'all'}`",
        f"- Artifacts: `{report.get('artifact_count', 0)}`",
        f"- Screenshots/pages: `{cost.get('screenshots', 0)}`",
        f"- Screen clusters: `{cost.get('screen_clusters', 0)}`",
        f"- Functionality items: `{cost.get('functionality_items', 0)}`",
        f"- Functionality clusters: `{cost.get('functionality_clusters', 0)}`",
        f"- Verified/functionality ratio: `{coverage.get('verified_functionality_ratio', 0.0)}`",
        f"- Novelty discovery rate: `{coverage.get('novelty_discovery_rate', 0.0)}`",
        f"- Saturation per 50 screens: `{coverage.get('saturation_per_50_screens', [])}`",
        f"- Semantics quality: `{coverage.get('functionality_semantics_quality', '')}`",
        f"- Quality gate: `{'passed' if quality.get('passed') else 'failed'}`",
        "",
        "## Discovered Functionality Clusters",
        "",
    ]
    for cluster in report.get("functionality_clusters") or []:
        lines.append(
            "- "
            f"`{cluster.get('canonical_name')}` "
            f"risk=`{cluster.get('risk_level')}` "
            f"verified=`{cluster.get('success_count')}` "
            f"members=`{len(cluster.get('member_functionality_ids') or [])}`"
        )
    lines.extend(["", "## Exploration Jobs", ""])
    for job in report.get("exploration_jobs") or []:
        lines.append(
            "- "
            f"`{job.get('functionality_cluster_id')}` "
            f"priority=`{job.get('priority')}` "
            f"reason={job.get('reason')}"
        )
    return "\n".join(lines).rstrip() + "\n"


def _load_pages(
    exploration_root: str | Path,
    *,
    registry: SchemaRegistry,
    app_filter: str | None,
) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    for pages_path in find_page_files(exploration_root):
        try:
            data = json.loads(Path(pages_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        artifact_app = str(data.get("app") or "")
        if app_filter and not registry.app_matches(artifact_app, app_filter):
            continue
        for page in data.get("pages") or []:
            if not isinstance(page, dict):
                continue
            item = dict(page)
            item.setdefault("app", artifact_app)
            item["artifact_app"] = artifact_app
            item["artifact_path"] = str(pages_path)
            pages.append(item)
    return pages


def _load_transitions(
    exploration_root: str | Path,
    *,
    registry: SchemaRegistry,
    app_filter: str | None,
) -> list[dict[str, Any]]:
    transitions: list[dict[str, Any]] = []
    root = Path(exploration_root)
    for transitions_path in sorted(root.rglob("*_transitions_*.json")):
        try:
            data = json.loads(Path(transitions_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        artifact_app = str(data.get("app") or "")
        if app_filter and not registry.app_matches(artifact_app, app_filter):
            continue
        for item in data.get("transitions") or []:
            if not isinstance(item, dict):
                continue
            transition = dict(item)
            transition["artifact_app"] = artifact_app
            transition["artifact_path"] = str(transitions_path)
            transitions.append(transition)
    return transitions


def _edge_counts(rebuild_report: dict[str, Any]) -> tuple[int, int]:
    promoted = 0
    seen = 0
    quality_reports = ((rebuild_report.get("exploration") or {}).get("quality_reports") or [])
    for item in quality_reports:
        staging = item.get("staging") or {}
        promoted += int(staging.get("transitions_promoted") or 0)
        seen += int(staging.get("transitions_seen") or 0)
    if not quality_reports:
        exploration = rebuild_report.get("exploration") or {}
        promoted = int(exploration.get("transitions_imported") or 0)
        for item in exploration.get("files") or []:
            quality = item.get("quality") or {}
            seen += int(quality.get("transitions_seen") or 0)
    return promoted, seen
