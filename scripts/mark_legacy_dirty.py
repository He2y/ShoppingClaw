#!/usr/bin/env python3
"""Mark legacy-dirty promoted edges so cross-app priors can shield against them.

A promoted edge that was NOT earned under the active verified policy is dirty:
under sava (N=3) a genuine promoted edge has verification_count>=3, and a
cold-start bootstrap edge carries bootstrap=true (vc=min_vc). Any other promoted
edge with verification_count < N and no bootstrap marker is a legacy-era
default-branch artifact (e.g. taobao's 35 promoted@vc=1). These are kept for now
(rebuild later) but flagged ``dirty_legacy=true`` so DomainPriorProvider excludes
them from cross-app structural priors — preventing unverified edges from
propagating as "trusted" hints to new apps.

Usage:
    python scripts/mark_legacy_dirty.py            # dry-run (audit)
    python scripts/mark_legacy_dirty.py --apply    # backup + mark
    python scripts/mark_legacy_dirty.py --unmark   # clear all dirty_legacy flags
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


def _min_vc() -> int:
    try:
        from phone_agent.spatial.amsg_config import AMSGOptimConfig
        return AMSGOptimConfig.from_env().min_verification_count
    except Exception:
        return 3


DIRTY_MATCH = """
MATCH (s:UIState)-[:NEXT_ACTION]->(a:Action)
WHERE coalesce(a.lifecycle_stage,'') = 'promoted'
  AND coalesce(a.bootstrap, false) <> true
  AND coalesce(a.verification_count, 0) < $min_vc
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--database", default=os.getenv("NEO4J_DATABASE", "shopping-spatial-v4"))
    ap.add_argument("--apply", action="store_true", help="execute (default: dry-run)")
    ap.add_argument("--unmark", action="store_true", help="clear all dirty_legacy flags instead")
    args = ap.parse_args()

    vc = _min_vc()
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
            if args.unmark:
                if not args.apply:
                    n = session.run("MATCH (a:Action) WHERE a.dirty_legacy = true RETURN count(a) AS n").single()["n"]
                    print(f"[DRY-RUN] 将清除 {n} 个 dirty_legacy 标记。加 --apply 执行。")
                    return 0
                res = session.run("MATCH (a:Action) WHERE a.dirty_legacy = true REMOVE a.dirty_legacy RETURN count(a) AS n").single()
                print(f"✅ 已清除 {res['n']} 个 dirty_legacy 标记。")
                return 0

            print(f"database={args.database!r}  active min_vc={vc}")
            rows = [dict(r) for r in session.run(
                DIRTY_MATCH + """
                RETURN coalesce(s.app,'<null>') AS app, s.page_type AS src,
                       a.action_type AS act, coalesce(a.verification_count,0) AS vc,
                       coalesce(a.bootstrap,false) AS bootstrap
                ORDER BY app, src
                """, min_vc=vc)]
            by_app: dict[str, int] = {}
            for r in rows:
                by_app[r["app"]] = by_app.get(r["app"], 0) + 1
            print(f"匹配 legacy-dirty promoted 边 = {len(rows)}  按 app: "
                  + ", ".join(f"{k}={v}" for k, v in sorted(by_app.items())))
            for r in rows[:50]:
                print(f"  app={r['app']:>8}  {r['src']:>15} --{r['act']}--> (vc={r['vc']})")

            if not rows:
                print("无 legacy-dirty 边。")
                return 0
            if not args.apply:
                print("\n[DRY-RUN] 未写入。加 --apply 执行（会先备份）。")
                return 0

            backup = [dict(r) for r in session.run(
                DIRTY_MATCH + " RETURN a.action_id AS aid, coalesce(s.app,'') AS app", min_vc=vc)]
            backup_dir = Path("memory_db/graph_backups")
            backup_dir.mkdir(parents=True, exist_ok=True)
            bpath = backup_dir / f"mark_dirty_{int(time.time())}.json"
            bpath.write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8")

            res = session.run(DIRTY_MATCH + " SET a.dirty_legacy = true RETURN count(a) AS n", min_vc=vc).single()
            print(f"\n已备份 → {bpath}")
            print(f"✅ 已标记 {res['n']} 条边 dirty_legacy=true（domain_priors 跨 app 先验将排除它们）。")
            print(f"   回滚：python scripts/mark_legacy_dirty.py --unmark --apply")
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
