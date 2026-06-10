"""Canonicalize the ``app`` property of existing AMSG graph data.

Maps every distinct raw ``app`` value found on UIState/FunctionalityItem
nodes through the AppRegistry and rewrites it to the canonical app id,
preserving the original value in ``app_raw`` and stamping ``domain``.
``state_id`` values are never rewritten (relationships reference them).

Workflow:
    1. python -m scripts.migrate_app_canonicalization --database shopping-spatial-v4 --dry-run
       -> review the mapping table; add unresolved values to
          phone_agent/spatial/schemas/app_registry.yaml (legacy_aliases)
    2. re-run with --apply once every value resolves
    3. verification runs automatically after apply

Idempotent: canonical values resolve to themselves on re-run.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass

from phone_agent.memory.graph_store import GraphStore
from phone_agent.spatial.app_registry import get_default_app_registry

_NODE_LABELS = ("UIState", "FunctionalityItem")


@dataclass(frozen=True)
class AppValuePlan:
    label: str
    raw_value: str
    count: int
    canonical: str
    domain: str
    resolved: bool


def collect_plans(store: GraphStore) -> list[AppValuePlan]:
    registry = get_default_app_registry()
    plans: list[AppValuePlan] = []
    with store.driver.session(database=store.database) as session:
        for label in _NODE_LABELS:
            result = session.run(
                f"MATCH (n:{label}) WHERE n.app IS NOT NULL AND n.app <> '' "
                "RETURN n.app AS app, count(*) AS cnt ORDER BY cnt DESC"
            )
            for record in result:
                raw = str(record["app"])
                resolved = registry.resolve(raw) is not None
                plans.append(
                    AppValuePlan(
                        label=label,
                        raw_value=raw,
                        count=int(record["cnt"] or 0),
                        canonical=registry.canonical_id(raw),
                        domain=registry.domain_of(raw),
                        resolved=resolved,
                    )
                )
    return plans


def apply_plans(store: GraphStore, plans: list[AppValuePlan]) -> int:
    total = 0
    with store.driver.session(database=store.database) as session:
        for plan in plans:
            if plan.raw_value == plan.canonical:
                # Already canonical - just backfill app_raw/domain where missing.
                query = (
                    f"MATCH (n:{plan.label}) WHERE n.app = $raw "
                    "SET n.app_raw = coalesce(n.app_raw, $raw), "
                    "    n.domain = coalesce(n.domain, $domain) "
                    "RETURN count(n) AS updated"
                )
            else:
                query = (
                    f"MATCH (n:{plan.label}) WHERE n.app = $raw "
                    "SET n.app_raw = coalesce(n.app_raw, n.app), "
                    "    n.app = $canonical, "
                    "    n.domain = $domain "
                    "RETURN count(n) AS updated"
                )
            record = session.run(
                query, raw=plan.raw_value, canonical=plan.canonical, domain=plan.domain
            ).single()
            total += int(record["updated"] or 0) if record else 0
    return total


def verify(store: GraphStore) -> bool:
    ok = True
    with store.driver.session(database=store.database) as session:
        for label in _NODE_LABELS:
            record = session.run(
                f"MATCH (n:{label}) WHERE n.app IS NOT NULL AND n.app <> '' "
                "AND (n.domain IS NULL OR n.domain = '') RETURN count(n) AS missing"
            ).single()
            missing = int(record["missing"] or 0) if record else 0
            if missing:
                print(f"  ✗ {label}: {missing} nodes still missing domain")
                ok = False
            else:
                print(f"  ✓ {label}: every app-bearing node has a domain")
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        default=os.getenv("AMSG_RUNTIME_GRAPH_DATABASE", "shopping-spatial-v4"),
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Report mapping only")
    mode.add_argument("--apply", action="store_true", help="Rewrite app properties")
    args = parser.parse_args()

    store = GraphStore(database=args.database, enable_task_index=False)
    if not store.driver:
        raise SystemExit("Neo4j is not reachable - check NEO4J_URI/USER/PASSWORD")

    plans = collect_plans(store)
    if not plans:
        print(f"database {args.database}: no app-bearing nodes found")
        return

    unresolved = [plan for plan in plans if not plan.resolved]
    print(f"database: {args.database}\n")
    print(f"{'label':<18} {'raw app':<24} {'count':>6}  {'canonical':<16} {'domain':<10} resolved")
    for plan in plans:
        flag = "yes" if plan.resolved else "NO  <-- add to app_registry.yaml"
        print(
            f"{plan.label:<18} {plan.raw_value:<24} {plan.count:>6}  "
            f"{plan.canonical:<16} {plan.domain:<10} {flag}"
        )

    if args.dry_run:
        if unresolved:
            print(f"\n{len(unresolved)} unresolved value(s). Add them to "
                  "phone_agent/spatial/schemas/app_registry.yaml before --apply.")
        else:
            print("\nAll values resolve - safe to re-run with --apply.")
        return

    if unresolved:
        raise SystemExit(
            f"Refusing to apply: {len(unresolved)} unresolved app value(s). "
            "Run --dry-run, update app_registry.yaml, then retry."
        )

    updated = apply_plans(store, plans)
    print(f"\n{updated} nodes updated. Ensuring indexes...")
    store.ensure_indexes()
    print("Verification:")
    if not verify(store):
        raise SystemExit("Verification failed - inspect the database manually.")
    print("Migration complete.")
    store.close()


if __name__ == "__main__":
    main()
