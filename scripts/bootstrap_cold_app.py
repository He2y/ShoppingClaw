#!/usr/bin/env python3
"""Bootstrap-promote a cold app's reviewed hypothesis edges to promoted.

Cold-start fix (P0-A) for EXISTING graph edges: an app whose human-reviewed
structure is already in Neo4j but stuck at ``hypothesis`` (invisible to the
agent → can never accrue the online verifications needed for promotion). This
promotes those edges in place so the app becomes navigable immediately.

Respects the safety invariant: high-risk edges (risk='high' or target page_type
in {payment,address,login,confirm}) are NEVER bootstrapped — purchase/payment
transitions must stay non-executable-shortcut by design.

Gated on coldness: by default only runs when the app has 0 promoted edges.
Backs up the app's current Action lifecycle state to memory_db/graph_backups/
before any write.

Usage:
    python scripts/bootstrap_cold_app.py --app jd            # dry-run (audit)
    python scripts/bootstrap_cold_app.py --app jd --apply    # backup + promote
"""

from __future__ import annotations

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

# Mirror phone_agent.memory.spatial_graph_memory._HIGH_RISK_PAGE_TYPES
HIGH_RISK_PAGE_TYPES = ["payment", "address", "login", "confirm"]

PROMOTABLE = """
MATCH (s:UIState)-[:NEXT_ACTION]->(a:Action)-[:PRODUCES]->(t:UIState)
WHERE s.app = $app
  AND coalesce(a.lifecycle_stage,'') = 'hypothesis'
  AND coalesce(a.risk,'') <> 'high'
  AND NOT t.page_type IN $high_risk_pts
RETURN a.action_id AS aid, s.page_type AS src, a.action_type AS act,
       t.page_type AS tgt, coalesce(a.source_type,'') AS source_type
ORDER BY src
"""

EXCLUDED = """
MATCH (s:UIState)-[:NEXT_ACTION]->(a:Action)-[:PRODUCES]->(t:UIState)
WHERE s.app = $app
  AND coalesce(a.lifecycle_stage,'') = 'hypothesis'
  AND (coalesce(a.risk,'') = 'high' OR t.page_type IN $high_risk_pts)
RETURN s.page_type AS src, a.action_type AS act, t.page_type AS tgt,
       coalesce(a.risk,'') AS risk
ORDER BY src
"""


def _active_min_vc() -> int:
    try:
        from phone_agent.spatial.amsg_config import AMSGOptimConfig
        return AMSGOptimConfig.from_env().min_verification_count
    except Exception:
        return 3


def _promoted_count(session, app: str) -> int:
    rec = session.run(
        """MATCH (s:UIState)-[:NEXT_ACTION]->(a:Action)
           WHERE s.app=$app AND coalesce(a.lifecycle_stage,'')='promoted'
           RETURN count(a) AS n""",
        app=app,
    ).single()
    return int(rec["n"]) if rec else 0


def _backup(session, app: str) -> Path:
    rows = [
        dict(r) for r in session.run(
            """MATCH (s:UIState)-[:NEXT_ACTION]->(a:Action)-[:PRODUCES]->(t:UIState)
               WHERE s.app=$app
               RETURN a.action_id AS aid, s.page_type AS src, t.page_type AS tgt,
                      coalesce(a.lifecycle_stage,'') AS lifecycle_stage,
                      coalesce(a.verification_count,0) AS verification_count,
                      coalesce(a.dominance_ratio,0.0) AS dominance_ratio""",
            app=app,
        )
    ]
    backup_dir = Path("memory_db/graph_backups")
    backup_dir.mkdir(parents=True, exist_ok=True)
    path = backup_dir / f"bootstrap_{app}_{int(time.time())}.json"
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", default="jd", help="canonical app id (UIState.app)")
    ap.add_argument("--database", default=os.getenv("NEO4J_DATABASE", "shopping-spatial-v4"))
    ap.add_argument("--apply", action="store_true", help="execute (default: dry-run)")
    ap.add_argument("--allow-warm", action="store_true",
                    help="bootstrap even if app already has promoted edges")
    args = ap.parse_args()

    vc = _active_min_vc()
    driver = GraphDatabase.driver(
        os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        auth=(os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "")),
        connection_timeout=8,
    )
    try:
        driver.verify_connectivity()
    except Exception as exc:  # noqa: BLE001
        print(f"❌ Neo4j 不可达: {exc}")
        return 2

    try:
        with driver.session(database=args.database) as session:
            promoted_now = _promoted_count(session, args.app)
            promotable = [dict(r) for r in session.run(
                PROMOTABLE, app=args.app, high_risk_pts=HIGH_RISK_PAGE_TYPES)]
            excluded = [dict(r) for r in session.run(
                EXCLUDED, app=args.app, high_risk_pts=HIGH_RISK_PAGE_TYPES)]

            print(f"app={args.app!r}  database={args.database!r}  active min_vc={vc}")
            print(f"当前 promoted 边数={promoted_now}  可 bootstrap 的 hypothesis 边={len(promotable)}"
                  f"  高风险排除={len(excluded)}")
            print("\n[将提升为 promoted]")
            for r in promotable:
                print(f"  {r['src']:>15} --{r['act']}--> {r['tgt']:<16} "
                      f"(source_type={r['source_type'] or '<none>'})")
            if excluded:
                print("\n[高风险，保持 hypothesis 不提升]")
                for r in excluded:
                    print(f"  {r['src']:>15} --{r['act']}--> {r['tgt']:<16} (risk={r['risk'] or '?'})")

            if promoted_now > 0 and not args.allow_warm:
                print(f"\n⏭  app 已有 {promoted_now} 条 promoted 边（非冷启动），跳过。"
                      f"如确需 bootstrap 请加 --allow-warm。")
                return 0

            if not promotable:
                print("\n无可 bootstrap 的边。")
                return 0

            if not args.apply:
                print(f"\n[DRY-RUN] 未写入。加 --apply 执行（会先备份到 memory_db/graph_backups/）。")
                return 0

            backup_path = _backup(session, args.app)
            print(f"\n已备份当前状态 → {backup_path}")
            res = session.run(
                """
                MATCH (s:UIState)-[:NEXT_ACTION]->(a:Action)-[:PRODUCES]->(t:UIState)
                WHERE s.app = $app
                  AND coalesce(a.lifecycle_stage,'') = 'hypothesis'
                  AND coalesce(a.risk,'') <> 'high'
                  AND NOT t.page_type IN $high_risk_pts
                SET a.lifecycle_stage = 'promoted',
                    a.verification_count = $vc,
                    a.dominance_ratio = 1.0,
                    a.bootstrap = true
                RETURN count(a) AS promoted
                """,
                app=args.app, high_risk_pts=HIGH_RISK_PAGE_TYPES, vc=vc,
            ).single()
            print(f"✅ 已提升 {res['promoted']} 条边为 promoted（vc={vc}, dominance=1.0, bootstrap=true）。")
            print(f"   回滚：用 {backup_path} 的 lifecycle_stage/verification_count/dominance_ratio 还原。")
            print(f"   验证：python scripts/inspect_graph_state.py")
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
