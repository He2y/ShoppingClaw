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
from .manual_trajectory_importer import ManualTrajectoryImporter
from .spatial_graph_memory import SpatialGraphMemory


def rebuild_spatial_graph(
    *,
    manual_root: str | Path,
    exploration_root: str | Path,
    database: str = "shopping-spatial-v1",
    write: bool = False,
    reset: bool = False,
    create_database: bool = False,
    yes: bool = False,
    limit_manual: int | None = None,
    canonical: bool = False,
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
        manual_result = manual_importer.import_directory(
            manual_root,
            persist=write and not canonical,
            limit=limit_manual,
        )

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

        for pages_path in find_page_files(exploration_root):
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
        return {
            "mode": "write" if write else "dry-run",
            "database": database,
            "canonical": canonical,
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
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
