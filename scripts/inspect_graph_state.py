#!/usr/bin/env python3
"""Read-only audit of the AMSG spatial graph in Neo4j.

Prints per-app node/edge counts, lifecycle-stage distribution, page-type
coverage, and promoted-edge inventory — the ground truth needed before
writing any number into the paper (design-doc figures are suspected stale).

Pure read-only: no MERGE/CREATE/DELETE. Connects to NEO4J_DATABASE (e.g.
shopping-spatial-v4) from .env.

Usage:
    python scripts/inspect_graph_state.py
    python scripts/inspect_graph_state.py --database shopping-spatial-v4
"""

from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

load_dotenv()

try:
    from neo4j import GraphDatabase
except ImportError:
    print("需要安装 neo4j: pip install neo4j")
    sys.exit(1)


def _run(session, query: str, **params) -> list[dict]:
    return [dict(r) for r in session.run(query, **params)]


def audit(session) -> None:
    # --- 1. totals -------------------------------------------------------
    totals = _run(
        session,
        """
        CALL { MATCH (s:UIState) RETURN count(s) AS states }
        CALL { MATCH (a:Action) RETURN count(a) AS actions }
        CALL { MATCH (:UIState)-[:NEXT_ACTION]->(:Action)-[:PRODUCES]->(:UIState)
               RETURN count(*) AS transitions }
        RETURN states, actions, transitions
        """,
    )[0]
    print("=" * 64)
    print(f"图谱总览  UIState={totals['states']}  Action={totals['actions']}  "
          f"完整转移(NEXT_ACTION->PRODUCES)={totals['transitions']}")
    print("=" * 64)

    # --- 2. per-app node coverage + page types ---------------------------
    print("\n[每 app] 页面节点数 + 覆盖的 page_type")
    for r in _run(
        session,
        """
        MATCH (s:UIState)
        RETURN coalesce(s.app,'<null>') AS app,
               coalesce(s.app_raw,'') AS app_raw,
               coalesce(s.domain,'') AS domain,
               count(*) AS nodes,
               size(collect(DISTINCT s.page_type)) AS n_page_types,
               collect(DISTINCT s.page_type) AS page_types
        ORDER BY nodes DESC
        """,
    ):
        print(f"  app={r['app']!r} (raw={r['app_raw']!r}, domain={r['domain']!r}) "
              f"nodes={r['nodes']} page_types={r['n_page_types']}")
        print(f"      {sorted(p for p in r['page_types'] if p)}")

    # --- 3. lifecycle-stage distribution per app (edge = source UIState app) --
    print("\n[每 app] 边生命周期分布 (lifecycle_stage, 经源 UIState 取 app)")
    rows = _run(
        session,
        """
        MATCH (s:UIState)-[:NEXT_ACTION]->(a:Action)
        RETURN coalesce(s.app,'<null>') AS app,
               coalesce(a.lifecycle_stage,'<none>') AS stage,
               count(*) AS n
        ORDER BY app, stage
        """,
    )
    by_app: dict[str, dict[str, int]] = {}
    for r in rows:
        by_app.setdefault(r["app"], {})[r["stage"]] = r["n"]
    stages = ["hypothesis", "candidate", "promoted", "demoted", "<none>"]
    for app, d in sorted(by_app.items(), key=lambda kv: -sum(kv[1].values())):
        cells = "  ".join(f"{s}={d.get(s, 0)}" for s in stages if d.get(s))
        total = sum(d.values())
        print(f"  app={app!r:14} total_edges={total:4}  {cells}")

    # --- 4. promoted edges per app per source page_type ------------------
    print("\n[每 app] promoted 边明细 (源页面 -> 动作 -> 目标页面)")
    prom = _run(
        session,
        """
        MATCH (s:UIState)-[:NEXT_ACTION]->(a:Action)-[:PRODUCES]->(t:UIState)
        WHERE a.lifecycle_stage = 'promoted'
        RETURN coalesce(s.app,'<null>') AS app,
               s.page_type AS src, a.action_type AS act,
               coalesce(a.intent, a.semantic_target, '') AS intent,
               t.page_type AS tgt,
               coalesce(a.verification_count, 0) AS vc,
               coalesce(a.confidence, a.dominance_ratio, 0.0) AS conf
        ORDER BY app, src
        """,
    )
    if not prom:
        print("  (无 promoted 边)")
    else:
        cur = None
        for r in prom:
            if r["app"] != cur:
                cur = r["app"]
                print(f"  --- app={cur!r} ({sum(1 for x in prom if x['app']==cur)} 条 promoted) ---")
            print(f"      {r['src']:>16} --{r['act']}[{r['intent']}]--> {r['tgt']:<16}"
                  f"  vc={r['vc']} conf={r['conf']:.2f}")

    # --- 5. app_raw / app alias spread (detect canonicalization drift) ---
    print("\n[别名漂移检查] 同一 app 是否有多个 app/app_raw 变体")
    aliases = _run(
        session,
        """
        MATCH (s:UIState)
        RETURN coalesce(s.app,'<null>') AS app,
               coalesce(s.app_raw,'') AS app_raw, count(*) AS n
        ORDER BY app, app_raw
        """,
    )
    seen: dict[str, set] = {}
    for r in aliases:
        seen.setdefault(r["app"], set()).add(r["app_raw"])
    for app, raws in seen.items():
        flag = "  ⚠️ 多 raw 变体" if len(raws) > 1 else ""
        print(f"  app={app!r} app_raw={sorted(raws)}{flag}")

    # --- 6. source_type provenance (online vs offline_reviewed vs ...) ---
    print("\n[来源溯源] Action.source_type 分布")
    for r in _run(
        session,
        """
        MATCH (a:Action)
        RETURN coalesce(a.source_type, a.source_kind, '<none>') AS src, count(*) AS n
        ORDER BY n DESC
        """,
    ):
        print(f"  source_type={r['src']!r:24} {r['n']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--database", default=os.getenv("NEO4J_DATABASE", "shopping-spatial-v4"))
    args = ap.parse_args()

    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "")
    print(f"连接 {uri}  database={args.database!r}")

    driver = GraphDatabase.driver(uri, auth=(user, password), connection_timeout=8)
    try:
        driver.verify_connectivity()
    except Exception as exc:  # noqa: BLE001
        print(f"❌ Neo4j 不可达: {exc}")
        print("   请确认 Neo4j 已启动 (bolt://localhost:7687) 并已加载 shopping-spatial-v4 库。")
        return 2
    try:
        with driver.session(database=args.database) as session:
            audit(session)
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
