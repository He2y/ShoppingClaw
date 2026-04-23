"""
Rebuild START/END relationships for TaskTarget nodes from original image files.
This repairs the relationships lost during the initial cleanup.

Usage: python scripts/rebuild_relationships.py
"""
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

try:
    from neo4j import GraphDatabase
except ImportError:
    print("neo4j not installed")
    sys.exit(1)


def img_hash(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def main():
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "password")
    database = os.getenv("NEO4J_DATABASE", "shopping")

    driver = GraphDatabase.driver(uri, auth=(user, password))
    session = driver.session(database=database)

    data_root = Path("MobiAgent/collect/manual/data")
    count = 0

    for app_dir in data_root.iterdir():
        if not app_dir.is_dir():
            continue
        for task_type_dir in app_dir.iterdir():
            if not task_type_dir.is_dir():
                continue
            for task_num_dir in task_type_dir.iterdir():
                if not task_num_dir.is_dir():
                    continue

                actions_file = task_num_dir / "actions.json"
                if not actions_file.exists():
                    continue

                with open(actions_file, "r", encoding="utf-8") as f:
                    ad = json.load(f)

                # Clean descriptions
                raw = ad.get("task_description", [])
                if isinstance(raw, list):
                    descs = [d.strip() for d in raw if d.strip()]
                elif isinstance(raw, str):
                    descs = [raw.strip()] if raw.strip() else []
                else:
                    descs = []

                if not descs:
                    continue

                # State images: first and last
                imgs = sorted(
                    [f for f in task_num_dir.glob("*.jpg") if "_" not in f.name],
                    key=lambda x: int(x.stem)
                )
                if not imgs:
                    continue

                first_state = f"state_{img_hash(imgs[0])}"
                last_state = f"state_{img_hash(imgs[-1])}"

                # Build task IDs — try both base ID (pre-cleanup) and numbered variants
                base = f"{app_dir.name}_{task_type_dir.name}_{task_num_dir.name}"
                if len(descs) <= 1:
                    # Single-desc: cleanup may have renamed to base_1
                    tids = [base, f"{base}_1"]
                else:
                    # Multi-desc: cleanup created base_1 through base_N
                    tids = [f"{base}_{i+1}" for i in range(len(descs))]

                # Deduplicate
                seen = set()
                unique_tids = []
                for t in tids:
                    if t not in seen:
                        seen.add(t)
                        unique_tids.append(t)

                for tid in unique_tids:
                    # Check node exists
                    exists = session.run(
                        "MATCH (t:TaskTarget {target_id: $tid}) RETURN t",
                        tid=tid
                    ).single()
                    if not exists:
                        continue

                    # Create START
                    session.run("""
                        MATCH (t:TaskTarget {target_id: $tid}),
                              (s:UIState {state_id: $sid})
                        MERGE (t)-[:STARTS_AT]->(s)
                    """, tid=tid, sid=first_state)

                    # Create END
                    session.run("""
                        MATCH (t:TaskTarget {target_id: $tid}),
                              (s:UIState {state_id: $sid})
                        MERGE (t)-[:ENDS_AT]->(s)
                    """, tid=tid, sid=last_state)

                    count += 1
                    print(f"  Rebuilt: {tid} → start={first_state}, end={last_state}")

    session.close()
    driver.close()
    print(f"\n✅ Rebuilt {count} START/END relationships")


if __name__ == "__main__":
    main()
