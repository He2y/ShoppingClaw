"""
Cleanup script: fix TaskTarget nodes in Neo4j that have concatenated/incorrect descriptions.

Problem 1: Single-string descriptions got space-joined character-by-character
  ("去京东帮我点外卖..." → "去 京 东 帮 我 点 外 卖 ...")
  Fix: restore from the original actions.json (which has the correct string)

Problem 2: List descriptions got " ".join() into one long concatenated string
  Fix: split into separate TaskTarget nodes, one per variant

Usage:
  python scripts/cleanup_neo4j_descriptions.py
"""
import os
import json
from pathlib import Path
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

try:
    from neo4j import GraphDatabase
    HAS_NEO4J = True
except ImportError:
    HAS_NEO4J = False
    print("neo4j not installed, skipping")


def space_tokenize(text: str) -> str:
    """Restore space-separated tokenized text back to continuous Chinese text."""
    return "".join(text.split())


def cleanup_neo4j():
    if not HAS_NEO4J:
        return

    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "password")
    database = os.getenv("NEO4J_DATABASE", "shopping")

    driver = GraphDatabase.driver(uri, auth=(user, password))
    session = driver.session(database=database)

    # 1. Find all TaskTarget nodes with space-separated or concatenated descriptions
    print("Step 1: Identifying corrupted TaskTarget nodes...")
    q = """
    MATCH (t:TaskTarget)
    RETURN t.target_id AS tid, t.app AS app, t.description AS raw_desc
    ORDER BY t.app
    """
    nodes = list(session.run(q))

    # 2. For each node, find the corresponding source data directory
    data_root = Path("MobiAgent/collect/manual/data")
    fix_count = 0
    delete_queue = []

    for record in nodes:
        tid = record["tid"]
        raw_desc = record["raw_desc"] or ""
        app = record["app"]

        print(f"\nProcessing: {tid}")
        print(f"  Raw desc (first 80 chars): {raw_desc[:80]}")

        # Determine the task type and number from tid: "APP_任务类型_N"
        # e.g. "京东_京东外卖_1" → app="京东", task_type="京东外卖", num="1"
        parts = tid.rsplit("_", 1)
        if len(parts) != 2:
            print(f"  Skipping: can't parse tid")
            continue
        task_type_dir, num = parts[0].rsplit("_", 1) if "_" in parts[0] else (parts[0], "1")
        # Actually tid format is "APP_任务类型_N" where APP might have underscores
        # Find first underscore from the right that splits APP from 任务类型
        first_underscore = tid.index("_")
        app_from_tid = tid[:first_underscore]
        rest = tid[first_underscore+1:]
        # rest = "京东外卖_1"
        last_underscore = rest.rfind("_")
        task_type_from_tid = rest[:last_underscore]
        num_from_tid = rest[last_underscore+1:]

        task_dir = data_root / app_from_tid / task_type_from_tid / num_from_tid
        actions_file = task_dir / "actions.json"

        if not actions_file.exists():
            print(f"  Warning: source file not found: {actions_file}")
            delete_queue.append(tid)
            continue

        with open(actions_file, "r", encoding="utf-8") as f:
            actions_data = json.load(f)

        raw_task_desc = actions_data.get("task_description", [])

        # Determine the list of clean descriptions
        if isinstance(raw_task_desc, list):
            clean_descs = [d.strip() for d in raw_task_desc if d.strip()]
        elif isinstance(raw_task_desc, str):
            clean_descs = [raw_task_desc.strip()]
        else:
            clean_descs = []

        if not clean_descs:
            print(f"  Warning: no clean descriptions found")
            continue

        print(f"  Clean descriptions: {len(clean_descs)}")
        for d in clean_descs:
            print(f"    - {d[:60]}")

        if len(clean_descs) == 1 and raw_desc == clean_descs[0]:
            # Already correct (unlikely given the issue)
            print(f"  Already correct, skipping")
            continue

        if len(clean_descs) == 1:
            # Safe path: just update the description, keep existing relationships
            print(f"  Updating description (keeping START/END relationships)")
            session.run("""
                MATCH (t:TaskTarget {target_id: $tid})
                SET t.description = $desc
            """, tid=tid, desc=clean_descs[0])
        else:
            # Multi-variant: capture old START/END relationships before deleting
            old_rels = session.run("""
                MATCH (t:TaskTarget {target_id: $tid})
                OPTIONAL MATCH (t)-[r:STARTS_AT]->(s)
                OPTIONAL MATCH (t)-[e:ENDS_AT]->(en)
                RETURN s.state_id AS start_state, en.state_id AS end_state
            """, tid=tid).single()
            start_state = old_rels["start_state"] if old_rels else None
            end_state = old_rels["end_state"] if old_rels else None

            print(f"  Deleting corrupted node: {tid}")
            session.run("""
                MATCH (t:TaskTarget {target_id: $tid})
                DETACH DELETE t
            """, tid=tid)

            for i, desc in enumerate(clean_descs):
                new_tid = f"{app_from_tid}_{task_type_from_tid}_{num_from_tid}_{i+1}"
                print(f"  Creating: {new_tid} -> {desc[:40]}")
                session.run("""
                    MERGE (t:TaskTarget {target_id: $tid})
                    SET t.app = $app, t.task_type = $ttype, t.description = $desc
                """, tid=new_tid, app=app_from_tid, ttype=task_type_from_tid, desc=desc)

                # Restore START/END relationships for first variant only
                if i == 0 and start_state:
                    session.run("""
                        MATCH (t:TaskTarget {target_id: $tid}), (s:UIState {state_id: $sid})
                        MERGE (t)-[:STARTS_AT]->(s)
                    """, tid=new_tid, sid=start_state)
                if i == 0 and end_state:
                    session.run("""
                        MATCH (t:TaskTarget {target_id: $tid}), (s:UIState {state_id: $sid})
                        MERGE (t)-[:ENDS_AT]->(s)
                    """, tid=new_tid, sid=end_state)

        fix_count += 1

    session.close()
    driver.close()
    print(f"\n✅ Cleaned up {fix_count} corrupted TaskTarget nodes")


if __name__ == "__main__":
    cleanup_neo4j()
