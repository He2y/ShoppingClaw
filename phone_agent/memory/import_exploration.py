"""Import OfflineExplorer JSON artifacts into SpatialGraphMemory.

Usage:
    python -m phone_agent.memory.import_exploration --storage memory_db/exploration
    python -m phone_agent.memory.import_exploration --pages memory_db/exploration/淘宝_explore_1777869293.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .graph_store import GraphStore
from .spatial_graph_memory import ExplorationImportResult, SpatialGraphMemory


def find_page_files(storage: str | Path) -> list[Path]:
    """Return OfflineExplorer page JSON files, excluding transition artifacts."""
    root = Path(storage)
    if root.is_file():
        return [root]
    return sorted(
        path
        for path in root.glob("*_explore_*.json")
        if "_transitions_" not in path.name and "_trajectory_" not in path.name
    )


def import_exploration_artifacts(
    *,
    pages: str | Path | None = None,
    storage: str | Path = "memory_db/exploration",
    persist: bool = True,
    graph_store: GraphStore | None = None,
) -> list[ExplorationImportResult]:
    """Import one page file or all page files under a storage directory."""
    store = graph_store if persist else None
    memory = SpatialGraphMemory(store)
    page_files = [Path(pages)] if pages else find_page_files(storage)
    return [memory.import_exploration_files(path, persist=persist) for path in page_files]


def main() -> int:
    parser = argparse.ArgumentParser(description="Import OfflineExplorer JSON into SpatialGraphMemory.")
    parser.add_argument("--storage", default="memory_db/exploration", help="Directory containing exploration JSON files.")
    parser.add_argument("--pages", default=None, help="Specific *_explore_*.json file to import.")
    parser.add_argument("--dry-run", action="store_true", help="Build local graph only; do not write Neo4j.")
    args = parser.parse_args()

    graph_store = None if args.dry_run else GraphStore()
    results = import_exploration_artifacts(
        pages=args.pages,
        storage=args.storage,
        persist=not args.dry_run,
        graph_store=graph_store,
    )
    print(json.dumps([result.to_dict() for result in results], ensure_ascii=False, indent=2))
    if graph_store:
        graph_store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
