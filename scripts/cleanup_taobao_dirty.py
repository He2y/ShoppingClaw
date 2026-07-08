#!/usr/bin/env python3
"""Conservative cleanup of taobao's legacy promoted@vc=1 edges.

These 35 edges were written promoted-on-first-write by the old legacy config
(vc=1, no provenance, dirty_legacy=true). They are mostly the real taobao
shopping backbone, but carry redundancy and a few semantic mislabels. This
script (per the chosen "conservative clean + repair action_type" treatment):

  DELETE  (a) exact duplicates  (same src,tgt,action_type,intent twice)
          (b) NULL-vs-Tap duplicates  (keep the Tap variant, drop the NULL one)
          (c) go_back duplicates  (a coord 'tap' variant to the same target exists)
          (d) semantic mislabels  (an intent that cannot produce that transition:
              go_back/scroll going *forward* to a different page)
  REPAIR  action_type NULL -> 'Tap' where intent='tap' AND real coordinates exist,
          so the kept backbone edges become fast-path executable.
  KEEP    the valid backbone as promoted@vc=1 + dirty_legacy (main uses it now;
          the flag still shields it from cross-app priors).

Safety: dry-run by default (+ JSON backup), only mutates with --apply. Targets
ONLY app in {taobao, 淘宝}; never touches jd / system_home.

Usage:
    python scripts/cleanup_taobao_dirty.py            # dry-run + backup
    python scripts/cleanup_taobao_dirty.py --apply
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

try:
    from neo4j import GraphDatabase
except ImportError:
    print("需要安装 neo4j: pip install neo4j")
    sys.exit(1)

TAOBAO = ["taobao", "淘宝"]

# Intents that move backward / in place and therefore cannot be the FORWARD
# action that produces a *different* destination page in these rows.
_IMPOSSIBLE_FORWARD = {
    ("home", "my_account", "go_back"),
    ("product_detail", "search_result", "scroll"),
}


def _has_coords(loc: str | None) -> bool:
    return bool(loc) and loc.strip() not in ("{}", "")


def _fetch(session) -> list[dict]:
    return [
        dict(r)
        for r in session.run(
            """
            MATCH (s:UIState)-[:NEXT_ACTION]->(a:Action)-[:PRODUCES]->(t:UIState)
            WHERE a.lifecycle_stage = 'promoted' AND s.app IN $tb
            RETURN elementId(a) AS eid, s.page_type AS src, t.page_type AS tgt,
                   coalesce(a.action_type, 'NULL') AS atype,
                   coalesce(a.intent, '') AS intent,
                   a.target_locator AS loc
            ORDER BY src, tgt, atype, intent
            """,
            tb=TAOBAO,
        )
    ]


def _plan(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return (to_delete, to_repair) row lists."""
    by_pair: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        by_pair[(r["src"], r["tgt"])].append(r)

    delete_ids: dict[str, dict] = {}

    # (d) semantic mislabels
    for r in rows:
        if (r["src"], r["tgt"], r["intent"]) in _IMPOSSIBLE_FORWARD:
            delete_ids[r["eid"]] = {**r, "reason": "semantic mislabel (impossible forward intent)"}

    for (src, tgt), group in by_pair.items():
        has_tap_type = any(g["atype"] == "Tap" for g in group)
        has_tap_intent_coord = any(
            g["intent"] == "tap" and _has_coords(g["loc"]) for g in group
        )
        seen_sig: set[tuple[str, str]] = set()
        for g in group:
            if g["eid"] in delete_ids:
                continue
            # (b) NULL-vs-Tap duplicate: drop the NULL when a Tap variant exists
            if has_tap_type and g["atype"] == "NULL" and g["intent"] == "tap":
                delete_ids[g["eid"]] = {**g, "reason": "NULL duplicate of a Tap-typed edge"}
                continue
            # (c) go_back duplicate: a real coord tap to same target already exists
            if g["intent"] == "go_back" and has_tap_intent_coord:
                delete_ids[g["eid"]] = {**g, "reason": "go_back duplicate (coord tap variant exists)"}
                continue
            # (a) exact duplicate: same (atype,intent) seen already in this pair
            sig = (g["atype"], g["intent"])
            if sig in seen_sig:
                delete_ids[g["eid"]] = {**g, "reason": "exact duplicate"}
                continue
            seen_sig.add(sig)

    repair = [
        r
        for r in rows
        if r["eid"] not in delete_ids
        and r["atype"] == "NULL"
        and r["intent"] == "tap"
        and _has_coords(r["loc"])
    ]
    return list(delete_ids.values()), repair


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--database", default=os.getenv("NEO4J_DATABASE", "shopping-spatial-v4"))
    ap.add_argument("--apply", action="store_true")
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
            rows = _fetch(session)
            to_delete, to_repair = _plan(rows)

            print(f"\n淘宝 promoted 边: {len(rows)}  →  删除 {len(to_delete)}, "
                  f"修复 action_type {len(to_repair)}, 保留 {len(rows) - len(to_delete)}")
            print("\n🔴 删除:")
            for r in to_delete:
                print(f"  {r['src']:>14} --{r['atype']}[{r['intent']}]--> {r['tgt']:<14} | {r['reason']}")
            print("\n🔧 修复 NULL→Tap (intent=tap + 有坐标, 使可 fast-path):")
            for r in to_repair:
                print(f"  {r['src']:>14} --NULL[{r['intent']}]--> {r['tgt']:<14}")

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = f"memory_db/backups/taobao_dirty_cleanup_{ts}.json"
            os.makedirs(os.path.dirname(backup_path), exist_ok=True)
            with open(backup_path, "w", encoding="utf-8") as f:
                json.dump({"all": rows, "to_delete": to_delete, "to_repair": to_repair,
                           "database": args.database}, f, ensure_ascii=False, indent=2)
            print(f"\n💾 备份: {backup_path}")

            if not args.apply:
                print("\n(dry-run) 加 --apply 实际执行。")
                return 0

            del_ids = [r["eid"] for r in to_delete]
            rep_ids = [r["eid"] for r in to_repair]
            if del_ids:
                session.run(
                    "MATCH (a:Action) WHERE elementId(a) IN $ids DETACH DELETE a",
                    ids=del_ids,
                )
            if rep_ids:
                session.run(
                    "MATCH (a:Action) WHERE elementId(a) IN $ids "
                    "SET a.action_type = 'Tap', a.updated_at = a.updated_at",
                    ids=rep_ids,
                )
            remaining = _fetch(session)
            n_null = sum(1 for r in remaining if r["atype"] == "NULL" and r["intent"] == "tap" and _has_coords(r["loc"]))
            print(f"\n✅ 已删 {len(del_ids)} 条, 修复 {len(rep_ids)} 条。")
            print(f"   淘宝 promoted 现 {len(remaining)} 条; 残留可修的 NULL-tap-coord = {n_null}")
            return 0
    finally:
        driver.close()


if __name__ == "__main__":
    raise SystemExit(main())
