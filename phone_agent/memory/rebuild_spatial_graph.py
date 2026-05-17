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

        manual_result = ManualTrajectoryImporter(graph_store).import_directory(
            manual_root,
            persist=write,
            limit=limit_manual,
        )

        exploration_results = []
        exploration_memory = SpatialGraphMemory(graph_store)
        for pages_path in find_page_files(exploration_root):
            result = exploration_memory.import_exploration_files(pages_path, persist=write)
            exploration_results.append(result)

        exploration_pages = sum(item.pages_imported for item in exploration_results)
        exploration_transitions = sum(item.transitions_imported for item in exploration_results)
        exploration_unique_pages = len(exploration_memory._local_states)
        raw_pages = manual_result.pages_imported + exploration_pages
        unique_pages = manual_result.unique_pages + exploration_unique_pages
        return {
            "mode": "write" if write else "dry-run",
            "database": database,
            "manual": manual_result.to_dict(),
            "exploration": {
                "files": [item.to_dict() for item in exploration_results],
                "pages_imported": exploration_pages,
                "unique_pages": exploration_unique_pages,
                "transitions_imported": exploration_transitions,
            },
            "totals": {
                "pages": raw_pages,
                "unique_pages": unique_pages,
                "dedupe_ratio": round(1 - (unique_pages / raw_pages), 4) if raw_pages else 0.0,
                "transitions": manual_result.transitions_imported + exploration_transitions,
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
    parser.add_argument("--database", default="shopping-spatial-v1", help="Target Neo4j database")
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
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
