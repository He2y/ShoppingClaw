"""Import OfflineExplorer JSON artifacts into SpatialGraphMemory.

Default behavior (Phase 5): creates a staging batch under memory_db/staging/
for human review via `python -m phone_agent.spatial.review.webui_review`.

Use --direct for legacy behavior that writes directly to Neo4j.

Usage:
    # Staging (default):
    python -m phone_agent.memory.import_exploration --storage memory_db/exploration
    python -m phone_agent.memory.import_exploration --pages memory_db/exploration/淘宝_explore_1.json

    # Direct legacy import:
    python -m phone_agent.memory.import_exploration --pages X.json --direct
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
    """Import one page file or all page files under a storage directory.

    This is the legacy direct-import function (used by --direct flag).
    """
    store = graph_store if persist else None
    memory = SpatialGraphMemory(store)
    page_files = [Path(pages)] if pages else find_page_files(storage)
    return [memory.import_exploration_files(path, persist=persist) for path in page_files]


def create_staging_batches(
    *,
    pages: str | Path | None = None,
    storage: str | Path = "memory_db/exploration",
    storage_root: str | Path = "memory_db/staging",
) -> list[str]:
    """Create staging batches from exploration artifacts (default Phase 5 behavior)."""
    from phone_agent.spatial.review.batch import create_staging_batch

    page_files = [Path(pages)] if pages else find_page_files(storage)
    batch_dirs = []
    for page_file in page_files:
        batch_dir = create_staging_batch(page_file, storage_root=storage_root)
        batch_dirs.append(str(batch_dir))
    return batch_dirs


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import OfflineExplorer JSON — creates staging batch by default."
    )
    parser.add_argument(
        "--storage", default="memory_db/exploration",
        help="Directory containing exploration JSON files.",
    )
    parser.add_argument("--pages", default=None, help="Specific *_explore_*.json file to import.")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="(direct mode only) Build local graph only; do not write Neo4j.",
    )
    parser.add_argument(
        "--direct", action="store_true",
        help="Legacy mode: write directly to Neo4j instead of creating a staging batch.",
    )
    parser.add_argument(
        "--staging-root", default="memory_db/staging",
        help="Root directory for staging batches (default: memory_db/staging).",
    )
    args = parser.parse_args()

    if args.direct:
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
    else:
        batch_dirs = create_staging_batches(
            pages=args.pages,
            storage=args.storage,
            storage_root=args.staging_root,
        )
        print(json.dumps({"staging_batches": batch_dirs}, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
