#!/usr/bin/env python3
"""Purge invalid nodes/edges from the runtime spatial graph.

Pollution sources identified in 2026-06-11 real-device runs:
1. Rollback edges promoted as navigation (Back / rollback_* targets) —
   caused the filter-panel Fast Path death loop.
2. Transient-page states (unknown / dialog) and their edges — written
   during broken-screenshot blind runs and popup interference.
3. Task-specific states (state_id carrying query text like 蓝牙耳机) —
   per-task content that must never be canonical graph nodes.
4. Orphan Action nodes left behind by partial writes.

Everything deleted is backed up to memory_db/graph_backups/ first.

Usage:
    python scripts/purge_invalid_graph_entries.py            # dry-run (audit)
    python scripts/purge_invalid_graph_entries.py --fix      # backup + delete
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

try:
    from neo4j import GraphDatabase
except ImportError:
    print("需要安装 neo4j: pip install neo4j")
    sys.exit(1)

TRANSIENT_PAGE_TYPES = ("unknown", "dialog")
TASK_TOKENS = ("蓝牙耳机", "iPhone", "耳机_q", "_q,")


def connect():
    return GraphDatabase.driver(
        os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        auth=(os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "")),
    )


def audit(session) -> dict:
    found: dict[str, list] = {}

    found["rollback_actions"] = [
        dict(r) for r in session.run(
            """MATCH (a:UIState)-[:NEXT_ACTION]->(act:Action)-[:PRODUCES]->(b:UIState)
               WHERE toLower(act.action_type) = 'back'
                  OR toLower(coalesce(act.semantic_target,'')) CONTAINS 'rollback'
               RETURN act.action_id AS action_id, a.page_type AS src,
                      act.action_type AS action_type, act.semantic_target AS target,
                      b.page_type AS tgt, coalesce(act.lifecycle_stage,'?') AS lifecycle"""
        )
    ]

    found["transient_states"] = [
        dict(r) for r in session.run(
            """MATCH (s:UIState) WHERE s.page_type IN $pts
               OPTIONAL MATCH (s)-[:NEXT_ACTION]->(out:Action)
               OPTIONAL MATCH (in_:Action)-[:PRODUCES]->(s)
               RETURN s.state_id AS state_id, s.page_type AS page_type,
                      count(DISTINCT out) + count(DISTINCT in_) AS degree""",
            pts=list(TRANSIENT_PAGE_TYPES),
        )
    ]

    task_clause = " OR ".join(f"s.state_id CONTAINS '{t}'" for t in TASK_TOKENS)
    found["task_specific_states"] = [
        dict(r) for r in session.run(
            f"MATCH (s:UIState) WHERE {task_clause} "
            "RETURN s.state_id AS state_id, s.page_type AS page_type"
        )
    ]

    found["orphan_actions"] = [
        dict(r) for r in session.run(
            """MATCH (act:Action)
               WHERE NOT (:UIState)-[:NEXT_ACTION]->(act)
                  OR NOT (act)-[:PRODUCES]->(:UIState)
               RETURN act.action_id AS action_id, act.action_type AS action_type,
                      act.semantic_target AS target"""
        )
    ]
    return found


def purge(session, found: dict) -> dict:
    stats = {}
    rollback_ids = [r["action_id"] for r in found["rollback_actions"] if r.get("action_id")]
    stats["rollback_actions"] = session.run(
        "MATCH (act:Action) WHERE act.action_id IN $ids DETACH DELETE act RETURN count(*) AS c",
        ids=rollback_ids,
    ).single()["c"] if rollback_ids else 0

    # Transient states: delete connected Action nodes first, then the state
    stats["transient_edge_actions"] = session.run(
        """MATCH (s:UIState) WHERE s.page_type IN $pts
           OPTIONAL MATCH (s)-[:NEXT_ACTION]->(out:Action)
           OPTIONAL MATCH (in_:Action)-[:PRODUCES]->(s)
           WITH collect(DISTINCT out) + collect(DISTINCT in_) AS acts
           UNWIND acts AS act WITH DISTINCT act WHERE act IS NOT NULL
           DETACH DELETE act RETURN count(*) AS c""",
        pts=list(TRANSIENT_PAGE_TYPES),
    ).single()["c"]
    stats["transient_states"] = session.run(
        "MATCH (s:UIState) WHERE s.page_type IN $pts DETACH DELETE s RETURN count(*) AS c",
        pts=list(TRANSIENT_PAGE_TYPES),
    ).single()["c"]

    task_ids = [r["state_id"] for r in found["task_specific_states"]]
    if task_ids:
        session.run(
            """MATCH (s:UIState) WHERE s.state_id IN $ids
               OPTIONAL MATCH (s)-[:NEXT_ACTION]->(out:Action)
               OPTIONAL MATCH (in_:Action)-[:PRODUCES]->(s)
               WITH collect(DISTINCT out) + collect(DISTINCT in_) AS acts
               UNWIND acts AS act WITH DISTINCT act WHERE act IS NOT NULL
               DETACH DELETE act""",
            ids=task_ids,
        )
        stats["task_specific_states"] = session.run(
            "MATCH (s:UIState) WHERE s.state_id IN $ids DETACH DELETE s RETURN count(*) AS c",
            ids=task_ids,
        ).single()["c"]
    else:
        stats["task_specific_states"] = 0

    stats["orphan_actions"] = session.run(
        """MATCH (act:Action)
           WHERE NOT (:UIState)-[:NEXT_ACTION]->(act)
              OR NOT (act)-[:PRODUCES]->(:UIState)
           DETACH DELETE act RETURN count(*) AS c"""
    ).single()["c"]
    return stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fix", action="store_true", help="备份并删除（默认仅诊断）")
    parser.add_argument("--database", default=os.getenv("AMSG_RUNTIME_GRAPH_DATABASE", "shopping-spatial-v4"))
    args = parser.parse_args()

    driver = connect()
    with driver.session(database=args.database) as session:
        total_before = {r["l"]: r["c"] for r in session.run(
            "MATCH (n) RETURN labels(n)[0] AS l, count(*) AS c")}
        print(f"=== {args.database} 现状: {total_before} ===")

        found = audit(session)
        print(f"回滚/Back 边: {len(found['rollback_actions'])}")
        for r in found["rollback_actions"]:
            print(f"  {r['src']} --{r['action_type']}:{str(r['target'])[:40]}--> {r['tgt']} (lc={r['lifecycle']})")
        print(f"瞬态页节点 (unknown/dialog): {len(found['transient_states'])}")
        for r in found["transient_states"]:
            print(f"  {r['state_id']} ({r['page_type']}, degree={r['degree']})")
        print(f"任务专属节点: {len(found['task_specific_states'])}")
        for r in found["task_specific_states"]:
            print(f"  {r['state_id']} ({r['page_type']})")
        print(f"孤儿 Action: {len(found['orphan_actions'])}")

        if not args.fix:
            print("\n(dry-run，使用 --fix 执行备份+删除)")
            driver.close()
            return

        backup_dir = Path("memory_db/graph_backups")
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"purge_{time.strftime('%Y%m%d_%H%M%S')}_{args.database}.json"
        backup_path.write_text(
            json.dumps(found, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"\n📦 备份已写入: {backup_path}")

        stats = purge(session, found)
        total_after = {r["l"]: r["c"] for r in session.run(
            "MATCH (n) RETURN labels(n)[0] AS l, count(*) AS c")}
        print(f"🧹 删除统计: {stats}")
        print(f"=== 清理后: {total_after} ===")

        print("=== 剩余边 ===")
        for r in session.run(
            """MATCH (a:UIState)-[:NEXT_ACTION]->(act:Action)-[:PRODUCES]->(b:UIState)
               RETURN a.app AS app, a.page_type AS src, act.action_type AS at,
                      b.page_type AS tgt, coalesce(act.lifecycle_stage,'?') AS lc
               ORDER BY a.app, src"""):
            print(f"  [{r['app']}] {r['src']} --{r['at']}--> {r['tgt']} (lc={r['lc']})")
    driver.close()


if __name__ == "__main__":
    main()
