"""Create Neo4j constraints/indexes required by the multi-app AMSG runtime.

Usage:
    python -m scripts.create_amsg_indexes [--database shopping-spatial-v4]

Idempotent: every statement uses IF NOT EXISTS.
"""

from __future__ import annotations

import argparse
import os

from phone_agent.memory.graph_store import GraphStore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        default=os.getenv("AMSG_RUNTIME_GRAPH_DATABASE", "shopping-spatial-v4"),
        help="Target Neo4j database (default: runtime graph database)",
    )
    args = parser.parse_args()

    store = GraphStore(database=args.database, enable_task_index=False)
    if not store.driver:
        raise SystemExit("Neo4j is not reachable - check NEO4J_URI/USER/PASSWORD")

    applied = store.ensure_indexes()
    print(f"database: {args.database}")
    for statement in applied:
        print(f"  ✓ {statement}")
    print(f"{len(applied)} statements applied")
    store.close()


if __name__ == "__main__":
    main()
