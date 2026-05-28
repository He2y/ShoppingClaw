#!/usr/bin/env python3
"""
清理受污染的 Neo4j 空间图谱。

问题：在线执行 agent.py 时，record_observation() 每步直接写入 Neo4j，
未经 canonicalize_pages() 去重，导致同一语义页面产生大量变体节点。

本脚本对指定数据库执行：
1. 诊断：统计节点/边数量，识别重复节点
2. 去重合并：按 (app, page_type) 分组，保留最佳节点，合并边
3. 清理孤立节点：删除无边的 Action 和 UIState 节点
4. 报告：输出清理前后的对比

用法：
    python scripts/cleanup_polluted_graph.py                    # 仅诊断
    python scripts/cleanup_polluted_graph.py --fix              # 执行清理
    python scripts/cleanup_polluted_graph.py --fix --database shopping-spatial-v4
"""

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


def connect():
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "")
    return GraphDatabase.driver(uri, auth=(user, password))


def diagnose(session):
    """诊断图谱污染程度。"""
    print("\n📊 图谱诊断")
    print("=" * 60)

    # Total counts
    result = session.run(
        "MATCH (n) RETURN labels(n)[0] AS label, count(n) AS cnt ORDER BY cnt DESC"
    )
    total_nodes = 0
    for rec in result:
        print(f"  {rec['label']}: {rec['cnt']} 节点")
        total_nodes += rec["cnt"]
    print(f"  总计: {total_nodes} 节点")

    result = session.run(
        "MATCH ()-[r]->() RETURN type(r) AS rel, count(r) AS cnt ORDER BY cnt DESC"
    )
    total_rels = 0
    for rec in result:
        print(f"  {rec['rel']}: {rec['cnt']} 关系")
        total_rels += rec["cnt"]
    print(f"  总计: {total_rels} 关系")

    # Duplicate detection
    print("\n🔍 重复节点检测 (同一 app+page_type 有多个 UIState)")
    print("-" * 60)
    result = session.run("""
        MATCH (s:UIState)
        WITH s.app AS app, s.page_type AS pt, count(s) AS cnt,
             collect(s.state_id) AS ids,
             collect(coalesce(size(s.landmarks), 0)) AS landmark_counts
        WHERE cnt > 1
        RETURN app, pt, cnt, ids[0..3] AS sample_ids,
               reduce(s=0, x IN landmark_counts | s+x) AS total_landmarks
        ORDER BY cnt DESC
    """)
    dup_groups = []
    total_duplicates = 0
    for rec in result:
        dup_groups.append({
            "app": rec["app"],
            "page_type": rec["pt"],
            "count": rec["cnt"],
            "sample_ids": rec["sample_ids"],
        })
        excess = rec["cnt"] - 1  # Keep 1, rest are duplicates
        total_duplicates += excess
        print(f"  {rec['app']}/{rec['pt']}: {rec['cnt']} 个节点 (多余 {excess})")

    if not dup_groups:
        print("  ✅ 未发现重复节点")
    else:
        print(f"\n  ⚠️ 共发现 {total_duplicates} 个多余节点（可安全删除）")

    # Orphan nodes
    result = session.run("""
        MATCH (s:UIState)
        WHERE NOT (s)-[:NEXT_ACTION]->() AND NOT ()-[:PRODUCES]->(s)
        RETURN count(s) AS cnt
    """)
    orphan_states = result.single()["cnt"]

    result = session.run("""
        MATCH (a:Action)
        WHERE NOT ()-[:NEXT_ACTION]->(a) AND NOT (a)-[:PRODUCES]->()
        RETURN count(a) AS cnt
    """)
    orphan_actions = result.single()["cnt"]
    print(f"\n  孤立 UIState (无边): {orphan_states}")
    print(f"  孤立 Action (无边): {orphan_actions}")

    return {
        "total_nodes": total_nodes,
        "total_rels": total_rels,
        "dup_groups": dup_groups,
        "total_duplicates": total_duplicates,
        "orphan_states": orphan_states,
        "orphan_actions": orphan_actions,
    }


def fix_duplicates(session):
    """合并重复节点：保留 landmarks 最多的节点，迁移边，删除多余节点。"""
    print("\n🔧 开始去重合并")
    print("=" * 60)

    # Step 1: For each (app, page_type) group with >1 node,
    # keep the one with most landmarks, redirect edges, delete rest
    result = session.run("""
        MATCH (s:UIState)
        WITH s.app AS app, s.page_type AS pt, collect(s) AS nodes
        WHERE size(nodes) > 1
        RETURN app, pt, [n IN nodes | {
            id: id(n),
            state_id: n.state_id,
            landmarks: coalesce(size(n.landmarks), 0),
            updated_at: coalesce(n.updated_at, 0)
        }] AS node_info
    """)

    merged_count = 0
    deleted_count = 0

    for rec in result:
        app = rec["app"]
        pt = rec["pt"]
        nodes = sorted(rec["node_info"], key=lambda n: (-n["landmarks"], -n["updated_at"]))
        keeper = nodes[0]
        to_delete = nodes[1:]

        print(f"  {app}/{pt}: 保留 {keeper['state_id'][:30]}... (landmarks={keeper['landmarks']}), 删除 {len(to_delete)} 个")

        for victim in to_delete:
            # Redirect incoming PRODUCES edges
            session.run("""
                MATCH (a:Action)-[r:PRODUCES]->(victim:UIState {state_id: $victim_id})
                MATCH (keeper:UIState {state_id: $keeper_id})
                MERGE (a)-[:PRODUCES]->(keeper)
                DELETE r
            """, victim_id=victim["state_id"], keeper_id=keeper["state_id"])

            # Redirect outgoing NEXT_ACTION edges
            session.run("""
                MATCH (victim:UIState {state_id: $victim_id})-[r:NEXT_ACTION]->(a:Action)
                MATCH (keeper:UIState {state_id: $keeper_id})
                MERGE (keeper)-[:NEXT_ACTION]->(a)
                DELETE r
            """, victim_id=victim["state_id"], keeper_id=keeper["state_id"])

            # Redirect TaskTarget edges
            session.run("""
                MATCH (t:TaskTarget)-[r:STARTS_AT]->(victim:UIState {state_id: $victim_id})
                MATCH (keeper:UIState {state_id: $keeper_id})
                MERGE (t)-[:STARTS_AT]->(keeper)
                DELETE r
            """, victim_id=victim["state_id"], keeper_id=keeper["state_id"])

            session.run("""
                MATCH (t:TaskTarget)-[r:ENDS_AT]->(victim:UIState {state_id: $victim_id})
                MATCH (keeper:UIState {state_id: $keeper_id})
                MERGE (t)-[:ENDS_AT]->(keeper)
                DELETE r
            """, victim_id=victim["state_id"], keeper_id=keeper["state_id"])

            # Delete the victim node
            session.run("""
                MATCH (victim:UIState {state_id: $victim_id})
                DETACH DELETE victim
            """, victim_id=victim["state_id"])

            deleted_count += 1

        merged_count += 1

    print(f"\n  ✅ 合并了 {merged_count} 组，删除了 {deleted_count} 个重复节点")
    return deleted_count


def fix_orphans(session):
    """删除孤立节点（无任何边的 UIState 和 Action）。"""
    print("\n🗑️ 清理孤立节点")
    print("-" * 60)

    result = session.run("""
        MATCH (s:UIState)
        WHERE NOT (s)-[:NEXT_ACTION]->() AND NOT ()-[:PRODUCES]->(s)
              AND NOT ()-[:STARTS_AT]->(s) AND NOT ()-[:ENDS_AT]->(s)
        DETACH DELETE s
        RETURN count(s) AS cnt
    """)
    orphan_states = result.single()["cnt"]

    result = session.run("""
        MATCH (a:Action)
        WHERE NOT ()-[:NEXT_ACTION]->(a) AND NOT (a)-[:PRODUCES]->()
        DETACH DELETE a
        RETURN count(a) AS cnt
    """)
    orphan_actions = result.single()["cnt"]

    print(f"  删除孤立 UIState: {orphan_states}")
    print(f"  删除孤立 Action: {orphan_actions}")
    return orphan_states + orphan_actions


def fix_duplicate_actions(session):
    """合并指向相同 (source, target) 的重复 Action 节点。"""
    print("\n🔧 合并重复 Action 节点")
    print("-" * 60)

    result = session.run("""
        MATCH (s1:UIState)-[:NEXT_ACTION]->(a:Action)-[:PRODUCES]->(s2:UIState)
        WITH s1.state_id AS src, s2.state_id AS tgt, a.semantic_edge_key AS sek,
             collect(a) AS actions
        WHERE size(actions) > 1 AND sek IS NOT NULL
        RETURN src, tgt, sek, [a IN actions | {id: id(a), action_id: a.action_id, confidence: coalesce(a.confidence, 0)}] AS action_info
    """)

    deleted = 0
    for rec in result:
        actions = sorted(rec["action_info"], key=lambda a: -a["confidence"])
        keeper_id = actions[0]["action_id"]
        for victim in actions[1:]:
            session.run("""
                MATCH (a:Action {action_id: $aid})
                DETACH DELETE a
            """, aid=victim["action_id"])
            deleted += 1

    print(f"  删除重复 Action: {deleted}")
    return deleted


def main():
    parser = argparse.ArgumentParser(description="清理受污染的 Neo4j 空间图谱")
    parser.add_argument("--fix", action="store_true", help="执行清理（默认仅诊断）")
    parser.add_argument("--database", default=None, help="指定数据库名（默认清理所有）")
    args = parser.parse_args()

    driver = connect()

    databases = [args.database] if args.database else [
        "shopping-spatial-v4-pipeline-test",
        "shopping-spatial-v4",
    ]

    for db_name in databases:
        print(f"\n{'═' * 60}")
        print(f"  数据库: {db_name}")
        print(f"{'═' * 60}")

        try:
            with driver.session(database=db_name) as session:
                before = diagnose(session)

                if not args.fix:
                    print(f"\n💡 使用 --fix 参数执行清理")
                    continue

                if before["total_duplicates"] == 0 and before["orphan_states"] == 0 and before["orphan_actions"] == 0:
                    print("\n✅ 图谱健康，无需清理")
                    continue

                print(f"\n⚠️ 开始清理 {db_name}...")
                fix_duplicates(session)
                fix_duplicate_actions(session)
                fix_orphans(session)

                print("\n📊 清理后状态:")
                after = diagnose(session)

                print(f"\n📈 对比:")
                print(f"  节点: {before['total_nodes']} → {after['total_nodes']} (减少 {before['total_nodes'] - after['total_nodes']})")
                print(f"  关系: {before['total_rels']} → {after['total_rels']} (减少 {before['total_rels'] - after['total_rels']})")
                print(f"  重复组: {len(before['dup_groups'])} → {len(after['dup_groups'])}")

        except Exception as e:
            print(f"  ❌ 错误: {e}")

    driver.close()


if __name__ == "__main__":
    main()
