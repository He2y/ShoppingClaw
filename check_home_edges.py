#!/usr/bin/env python
"""Check edges from Taobao home page."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

from neo4j import GraphDatabase


def check_home_edges():
    """Check outgoing edges from Taobao home page."""
    print("=" * 60)
    print("Taobao Home Page Edges")
    print("=" * 60)

    uri = os.environ.get("NEO4J_URI")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD")
    database = os.environ.get("NEO4J_DATABASE")

    driver = GraphDatabase.driver(uri, auth=(user, password))

    with driver.session(database=database) as session:
        # Get Taobao home state
        print(f"\n📊 Finding Taobao home states:")
        result = session.run("""
            MATCH (s:UIState)
            WHERE s.app = '淘宝' AND s.page_type = 'home'
            RETURN s.state_id as id, s.summary as summary, s.page_type as type
            LIMIT 3
        """)

        home_states = []
        for record in result:
            print(f"  [{record['type']}] {record['summary'][:60]}...")
            print(f"    ID: {record['id']}")
            home_states.append(record["id"])

        if home_states:
            # Check edges from first home state
            state_id = home_states[0]
            print(f"\n📊 Outgoing edges from home state:")
            print(f"  State ID: {state_id[:70]}...")

            result = session.run("""
                MATCH (s:UIState {state_id: $state_id})-[r:NEXT_ACTION]->(a:Action)-[p:PRODUCES]->(t:UIState)
                RETURN t.page_type AS target_type,
                       t.summary AS target_summary,
                       a.type AS action,
                       a.semantic_target AS target,
                       r.confidence AS confidence
                ORDER BY confidence DESC
                LIMIT 10
            """, state_id=state_id)

            for i, record in enumerate(result, 1):
                summary = (record['target_summary'][:40] + "...") if record['target_summary'] and len(record['target_summary']) > 40 else record['target_summary']
                print(f"\n  Edge {i}:")
                print(f"    → Target type: {record['target_type']}")
                print(f"    → Target summary: {summary}")
                print(f"    → Action: {record['action']} on {record['target']}")
                print(f"    → Confidence: {record['confidence']}")

        else:
            print(f"\n  ⚠️ No home states found!")

    driver.close()
    print(f"\n✅ Check complete")


if __name__ == "__main__":
    check_home_edges()
