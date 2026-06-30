#!/usr/bin/env python3
"""Remove orphan pollution written under the wrong app (e.g. shopping pages
created under ``system_home`` by the pre-fix TrajectoryReviewer).

The pre-fix online importer scoped a trajectory's transitions by a single
session app picked via ``apps[0]`` over an unordered set. When that resolved to
the launcher (``system_home``), taobao transitions failed to match the taobao
subgraph and were written as fresh orphan nodes/edges under ``system_home``
(``state_system_home_<shoppingpage>_auto_<ts>``). This script removes those
mis-scoped nodes and their actions.

Safety: dry-run by default (prints + writes a JSON backup), only mutates with
``--apply``. Pure-targeted: only deletes UIState whose app is a *system* app but
whose page_type is a *shopping* page — the launcher's own ``home`` node is kept.

Usage:
    python scripts/cleanup_orphan_pollution.py                 # dry-run + backup
    python scripts/cleanup_orphan_pollution.py --apply         # actually delete
    python scripts/cleanup_orphan_pollution.py --database shopping-spatial-v4
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

try:
    from neo4j import GraphDatabase
except ImportError:
    print("需要安装 neo4j: pip install neo4j")
    sys.exit(1)

# Apps whose domain is the system launcher (not a real content app).
SYSTEM_APPS = ["system_home", "system home", "System Home", "launcher", "桌面"]

# Page types that belong to content apps, never to the launcher. Their presence
# under a system app is the pollution signature.
SHOPPING_PAGE_TYPES = [
    "search_input", "search_result", "filter_panel", "product_detail",
    "spec_selection", "cart", "checkout", "store", "category",
    "channel_home", "shop_list", "my_account",
]


def _find_pollution(session) -> list[dict]:
    return [
        dict(r)
        for r in session.run(
            """
            MATCH (s:UIState)
            WHERE s.app IN $sys AND s.page_type IN $shopping
            OPTIONAL MATCH (s)-[:NEXT_ACTION]->(a:Action)-[:PRODUCES]->(t:UIState)
            RETURN s.state_id AS state_id, s.app AS app, s.page_type AS page_type,
                   collect({action_id: a.action_id, type: a.action_type,
                            stage: a.lifecycle_stage, vc: a.verification_count,
                            target_state_id: t.state_id, target_page_type: t.page_type}) AS out_edges
            ORDER BY s.page_type
            """,
            sys=SYSTEM_APPS, shopping=SHOPPING_PAGE_TYPES,
        )
    ]


def _incoming_actions(session) -> list[dict]:
    """Actions PRODUCES-ing into a polluted node but sourced elsewhere."""
    return [
        dict(r)
        for r in session.run(
            """
            MATCH (src:UIState)-[:NEXT_ACTION]->(a:Action)-[:PRODUCES]->(t:UIState)
            WHERE t.app IN $sys AND t.page_type IN $shopping
              AND NOT (src.app IN $sys AND src.page_type IN $shopping)
            RETURN a.action_id AS action_id, src.app AS src_app,
                   src.page_type AS src_page, t.page_type AS tgt_page
            """,
            sys=SYSTEM_APPS, shopping=SHOPPING_PAGE_TYPES,
        )
    ]


def _delete(session) -> dict:
    summary = session.run(
        """
        MATCH (s:UIState)
        WHERE s.app IN $sys AND s.page_type IN $shopping
        OPTIONAL MATCH (s)-[:NEXT_ACTION]->(a:Action)
        OPTIONAL MATCH (a2:Action)-[:PRODUCES]->(s)
        DETACH DELETE a, a2, s
        """,
        sys=SYSTEM_APPS, shopping=SHOPPING_PAGE_TYPES,
    ).consume()
    return {
        "nodes_deleted": summary.counters.nodes_deleted,
        "relationships_deleted": summary.counters.relationships_deleted,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--database", default=os.getenv("NEO4J_DATABASE", "shopping-spatial-v4"))
    ap.add_argument("--apply", action="store_true", help="actually delete (default: dry-run)")
    args = ap.parse_args()

    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "")
    print(f"连接 {uri}  database={args.database!r}  mode={'APPLY' if args.apply else 'DRY-RUN'}")

    driver = GraphDatabase.driver(uri, auth=(user, password), connection_timeout=8)
    try:
        driver.verify_connectivity()
    except Exception as exc:  # noqa: BLE001
        print(f"❌ Neo4j 不可达: {exc}")
        return 2

    try:
        with driver.session(database=args.database) as session:
            polluted = _find_pollution(session)
            incoming = _incoming_actions(session)

            if not polluted:
                print("✅ 未发现 system app 下的购物页污染节点。")
                return 0

            n_edges = sum(len([e for e in p["out_edges"] if e["action_id"]]) for p in polluted)
            print(f"\n发现污染: {len(polluted)} 个节点, {n_edges} 条出边"
                  f"{f', {len(incoming)} 条入边' if incoming else ''}")
            for p in polluted:
                print(f"  - {p['app']!r}/{p['page_type']!r} ({p['state_id']})")
                for e in p["out_edges"]:
                    if e["action_id"]:
                        print(f"      --{e['type']}[{e['stage']}/vc={e['vc']}]--> "
                              f"{e['target_page_type']}")
            for inc in incoming:
                print(f"  - INCOMING {inc['src_app']!r}/{inc['src_page']} --> "
                      f"system/{inc['tgt_page']} (action {inc['action_id']})")

            # Backup before any mutation.
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = f"memory_db/backups/orphan_pollution_{ts}.json"
            os.makedirs(os.path.dirname(backup_path), exist_ok=True)
            with open(backup_path, "w", encoding="utf-8") as f:
                json.dump({"polluted": polluted, "incoming": incoming,
                           "database": args.database}, f, ensure_ascii=False, indent=2)
            print(f"\n💾 备份已写入: {backup_path}")

            if not args.apply:
                print("\n(dry-run) 加 --apply 实际删除。")
                return 0

            stats = _delete(session)
            print(f"\n🗑️  已删除: {stats['nodes_deleted']} 节点, "
                  f"{stats['relationships_deleted']} 关系")
            remaining = _find_pollution(session)
            print(f"✅ 复检: 剩余污染节点 = {len(remaining)}")
            return 0
    finally:
        driver.close()


if __name__ == "__main__":
    raise SystemExit(main())
