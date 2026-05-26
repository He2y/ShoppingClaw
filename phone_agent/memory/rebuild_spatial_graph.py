"""Rebuild a canonical Spatial Page Graph from legacy trajectory sources.

Default mode is a dry-run report. Use ``--write`` to persist into Neo4j and
``--reset --yes`` only when you intentionally want to clear the target database.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .graph_store import GraphStore
from .import_exploration import find_page_files
from .manual_trajectory_importer import ManualTrajectoryImporter, ManualTrajectoryImportResult
from .spatial_graph_memory import SpatialGraphMemory
from phone_agent.spatial.reporting import build_amsg_dry_run_report, format_amsg_markdown
from phone_agent.spatial.schema_registry import SchemaRegistry

_SAFE_CORE_FLOW = (
    "home",
    "search_input",
    "search_result",
    "product_detail",
    "spec_selection",
    "cart",
)


def _quality_gate(memory: SpatialGraphMemory, *, app: str = "淘宝") -> dict:
    registry = SchemaRegistry()
    schema = registry.merged("shopping")
    states = list(memory._local_states.values())
    edges = [edge for edges in memory._local_edges.values() for edge in edges]
    page_types = sorted({state.page_type for state in states if not app or registry.app_matches(state.app, app)})
    safe_schema_page_types = sorted(
        page_type
        for page_type, spec in schema.page_types.items()
        if spec.risk != "high" and page_type != "unknown"
    )
    missing_schema_page_types = [
        page_type
        for page_type in safe_schema_page_types
        if not schema.page_type_covered(page_type, page_types)
    ]
    schema_coverage = round(
        (len(safe_schema_page_types) - len(missing_schema_page_types)) / len(safe_schema_page_types),
        4,
    ) if safe_schema_page_types else 0.0
    edge_pairs = {
        (
            memory._local_states.get(edge.source_id).page_type if memory._local_states.get(edge.source_id) else "",
            memory._local_states.get(edge.target_id).page_type if memory._local_states.get(edge.target_id) else edge.postcondition,
        )
        for edge in edges
        if not app or (
            memory._local_states.get(edge.source_id)
            and registry.app_matches(memory._local_states.get(edge.source_id).app, app)
        )
    }
    missing_safe_edges = [
        {"source": source, "target": target, "edge": f"{source}->{target}"}
        for source, target in zip(_SAFE_CORE_FLOW, _SAFE_CORE_FLOW[1:])
        if (source, target) not in edge_pairs
    ]
    cross_app_edges = 0
    high_risk_edges = 0
    for edge in edges:
        source = memory._local_states.get(edge.source_id)
        target = memory._local_states.get(edge.target_id)
        if source and target and source.app != target.app:
            cross_app_edges += 1
        if edge.risk == "high" or edge.postcondition in {"checkout", "payment", "address", "login"}:
            high_risk_edges += 1

    transient_nodes = sum(1 for state in states if state.page_type == "unknown")
    app_mismatch_nodes = sum(1 for state in states if not memory._is_app_consistent(state))
    reasons = []
    if cross_app_edges:
        reasons.append(f"cross-app edges: {cross_app_edges}")
    if high_risk_edges:
        reasons.append(f"high-risk executable edges: {high_risk_edges}")
    if transient_nodes:
        reasons.append(f"transient unknown nodes: {transient_nodes}")
    if app_mismatch_nodes:
        reasons.append(f"app-mismatch nodes: {app_mismatch_nodes}")
    if missing_safe_edges:
        reasons.append("missing safe core edges: " + ", ".join(item["edge"] for item in missing_safe_edges))

    return {
        "app": app,
        "passed": not reasons,
        "reasons": reasons,
        "nodes": len(states),
        "edges": len(edges),
        "page_types": page_types,
        "schema_coverage": schema_coverage,
        "missing_schema_page_types": missing_schema_page_types,
        "missing_safe_core_edges": missing_safe_edges,
        "cross_app_edges": cross_app_edges,
        "high_risk_executable_edges": high_risk_edges,
        "transient_nodes": transient_nodes,
        "app_mismatch_nodes": app_mismatch_nodes,
    }


def _read_artifact_app(path: Path) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(data.get("app") or "")


def _app_matches_filter(app: str, app_filter: str | None) -> bool:
    if not app_filter:
        return True
    if not app:
        return False
    try:
        return SchemaRegistry().app_matches(app, app_filter)
    except Exception:
        return app.strip().lower() == app_filter.strip().lower()


def rebuild_spatial_graph(
    *,
    manual_root: str | Path | None,
    exploration_root: str | Path,
    database: str = "shopping-spatial-v1",
    write: bool = False,
    reset: bool = False,
    create_database: bool = False,
    yes: bool = False,
    limit_manual: int | None = None,
    canonical: bool = False,
    quality_app: str = "淘宝",
    include_manual: bool = True,
    app_filter: str | None = None,
) -> dict:
    graph_store = GraphStore(database=database) if write else None
    try:
        if write and not graph_store.driver:
            raise RuntimeError(f"Neo4j is unavailable for database={database}")
        if write and create_database:
            graph_store.ensure_database(database)
        if reset:
            if not write:
                raise ValueError("--reset requires --write")
            if not yes:
                raise ValueError("--reset requires --yes")
            graph_store.reset_spatial_graph()

        manual_importer = ManualTrajectoryImporter(graph_store if write and not canonical else None)
        if include_manual and manual_root:
            manual_result = manual_importer.import_directory(
                manual_root,
                persist=write and not canonical,
                limit=limit_manual,
            )
        else:
            manual_result = ManualTrajectoryImportResult()

        exploration_results = []
        exploration_memory = SpatialGraphMemory(graph_store if write and not canonical else None)
        canonical_memory = SpatialGraphMemory(graph_store)
        manual_quality = None
        exploration_quality_reports = []

        if canonical:
            manual_states, manual_edges, manual_quality = canonical_memory.canonicalize_state_graph(
                manual_importer.memory._local_states.values(),
                manual_importer.memory._local_edges,
            )
            canonical_memory.promote_staging_to_canonical(manual_states, manual_edges, persist=write)

        skipped_exploration_files = []
        for pages_path in find_page_files(exploration_root):
            artifact_app = _read_artifact_app(pages_path)
            if not _app_matches_filter(artifact_app, app_filter):
                skipped_exploration_files.append({"pages_path": str(pages_path), "app": artifact_app})
                continue
            if canonical:
                states, edges, quality = exploration_memory.import_exploration_staging(pages_path)
                promote_report = canonical_memory.promote_staging_to_canonical(states, edges, persist=write)
                exploration_quality_reports.append(
                    {
                        "pages_path": str(pages_path),
                        "staging": quality.to_dict(),
                        "promoted": promote_report.to_dict(),
                    }
                )
                exploration_results.append(
                    {
                        "pages_imported": quality.pages_seen,
                        "unique_pages": quality.canonical_pages,
                        "transitions_imported": quality.transitions_promoted,
                        "quality": quality.to_dict(),
                    }
                )
            else:
                result = exploration_memory.import_exploration_files(pages_path, persist=write)
                exploration_results.append(result)

        if canonical:
            exploration_pages = sum(item["pages_imported"] for item in exploration_results)
            exploration_transitions = sum(item["transitions_imported"] for item in exploration_results)
            exploration_unique_pages = sum(item["unique_pages"] for item in exploration_results)
            exploration_file_reports = exploration_results
            unique_pages = len(canonical_memory._local_states)
        else:
            exploration_pages = sum(item.pages_imported for item in exploration_results)
            exploration_transitions = sum(item.transitions_imported for item in exploration_results)
            exploration_unique_pages = len(exploration_memory._local_states)
            exploration_file_reports = [item.to_dict() for item in exploration_results]
            unique_pages = manual_result.unique_pages + exploration_unique_pages
        raw_pages = manual_result.pages_imported + exploration_pages
        raw_transitions = manual_result.transitions_imported + exploration_transitions
        quality_gate = _quality_gate(canonical_memory if canonical else exploration_memory, app=quality_app)
        return {
            "mode": "write" if write else "dry-run",
            "database": database,
            "canonical": canonical,
            "source_policy": {
                "include_manual": include_manual,
                "app_filter": app_filter,
                "skipped_exploration_files": skipped_exploration_files,
            },
            "quality_gate": quality_gate,
            "manual": manual_result.to_dict(),
            "manual_quality": manual_quality.to_dict() if manual_quality else None,
            "exploration": {
                "files": exploration_file_reports,
                "quality_reports": exploration_quality_reports,
                "pages_imported": exploration_pages,
                "unique_pages": exploration_unique_pages,
                "transitions_imported": exploration_transitions,
            },
            "totals": {
                "pages": raw_pages,
                "unique_pages": unique_pages,
                "dedupe_ratio": round(1 - (unique_pages / raw_pages), 4) if raw_pages else 0.0,
                "transitions": raw_transitions,
                "tasks": manual_result.tasks_imported,
            },
        }
    finally:
        if graph_store:
            graph_store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild Spatial Page Graph from legacy data.")
    parser.add_argument("--manual", default="MobiAgent/collect/manual/data", help="Manual trajectory root")
    parser.add_argument("--exploration", default="memory_db/exploration", help="Offline exploration root")
    parser.add_argument("--database", default="shopping-spatial-v2", help="Target Neo4j database")
    parser.add_argument("--legacy", action="store_true", help="Use legacy importer without canonical staging")
    parser.add_argument("--write", action="store_true", help="Persist to Neo4j instead of dry-run")
    parser.add_argument("--create-database", action="store_true", help="Create target Neo4j database if supported")
    parser.add_argument("--reset", action="store_true", help="Clear target database before writing")
    parser.add_argument("--yes", action="store_true", help="Confirm destructive reset")
    parser.add_argument("--limit-manual", type=int, default=None, help="Limit manual trajectories for smoke tests")
    parser.add_argument("--quality-app", default="淘宝", help="App name used for canonical graph quality gate")
    parser.add_argument(
        "--exploration-only",
        action="store_true",
        help="Ignore manual trajectory history and rebuild only from OfflineExplorer artifacts.",
    )
    parser.add_argument(
        "--app-filter",
        default=None,
        help="Only import exploration artifacts whose top-level app matches this value.",
    )
    parser.add_argument(
        "--amsg-report",
        default=None,
        help="Optional path for a research-oriented AMSG dry-run report (.md or .json).",
    )
    args = parser.parse_args()

    report = rebuild_spatial_graph(
        manual_root=args.manual,
        exploration_root=args.exploration,
        database=args.database,
        write=args.write,
        reset=args.reset,
        create_database=args.create_database,
        yes=args.yes,
        limit_manual=args.limit_manual,
        canonical=not args.legacy,
        quality_app=args.quality_app,
        include_manual=not args.exploration_only,
        app_filter=args.app_filter,
    )
    if args.amsg_report:
        amsg_report = build_amsg_dry_run_report(
            exploration_root=args.exploration,
            rebuild_report=report,
            app_filter=args.app_filter or args.quality_app,
        )
        report_path = Path(args.amsg_report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        if report_path.suffix.lower() == ".json":
            report_path.write_text(json.dumps(amsg_report, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            report_path.write_text(format_amsg_markdown(amsg_report), encoding="utf-8")
        report["amsg_report_path"] = str(report_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
