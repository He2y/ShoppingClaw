#!/usr/bin/env python
"""Debug graph query to understand why edges aren't loaded."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

from neo4j import GraphDatabase


def debug_edge_query():
    """Debug the exact query used in get_outgoing_transitions."""
    print("=" * 60)
    print("Edge Query Debug")
    print("=" * 60)

    uri = os.environ.get("NEO4J_URI")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD")
    database = os.environ.get("NEO4J_DATABASE")

    driver = GraphDatabase.driver(uri, auth=(user, password))

    with driver.session(database=database) as session:
        # Check Action node properties
        print(f"\n📊 Action node properties:")
        result = session.run("""
            MATCH (a:Action)
            RETURN properties(a) as props
            LIMIT 1
        """)
        record = result.single()
        if record:
            props = record["props"]
            print(f"  Keys: {list(props.keys())}")
            for key, value in props.items():
                print(f"    {key}: {value}")

        # Try the exact query from get_outgoing_transitions
        print(f"\n📊 Testing get_outgoing_transitions query:")

        # First, get a sample state_id
        result = session.run("""
            MATCH (s:UIState {app: '淘宝'})
            RETURN s.state_id as state_id, s.page_type as page_type
            LIMIT 1
        """)
        sample = result.single()
        if sample:
            state_id = sample["state_id"]
            print(f"  Testing with state: {state_id}")
            print(f"  Page type: {sample['page_type']}")

            # Run the actual query
            result2 = session.run("""
                MATCH (s:UIState {state_id: $state_id})-[r:NEXT_ACTION]->(a:Action)-[p:PRODUCES]->(t:UIState)
                WHERE coalesce(t.app, "") = coalesce(s.app, "")
                RETURN s.state_id AS source_id,
                       t.state_id AS target_id,
                       t.page_type AS target_page_type,
                       a.type AS action_type,
                       a.semantic_target AS action_target,
                       r.confidence AS confidence
                LIMIT 5
            """, state_id=state_id)

            edges_found = 0
            for record in result2:
                edges_found += 1
                print(f"\n  Edge {edges_found}:")
                print(f"    Source: {record['source_id'][:50]}...")
                print(f"    Target: {record['target_id'][:50]}...")
                print(f"    Target type: {record['target_page_type']}")
                print(f"    Action type: {record['action_type']}")
                print(f"    Action target: {record['action_target']}")
                print(f"    Confidence: {record['confidence']}")

            if edges_found == 0:
                print(f"\n  ⚠️ No edges found!")

                # Debug: check if NEXT_ACTION edges exist at all
                result3 = session.run("""
                    MATCH (s:UIState)-[r:NEXT_ACTION]->(a:Action)
                    WHERE s.state_id = $state_id
                    RETURN count(r) as count
                """, state_id=state_id)
                count = result3.single()["count"]
                print(f"  NEXT_ACTION edges from this state: {count}")

                # Check Action properties
                result4 = session.run("""
                    MATCH (s:UIState {state_id: $state_id})-[r:NEXT_ACTION]->(a:Action)
                    RETURN properties(a) as action_props
                    LIMIT 1
                """, state_id=state_id)
                record = result4.single()
                if record:
                    print(f"  Action properties: {list(record['action_props'].keys())}")

    driver.close()
    print(f"\n✅ Debug complete")


if __name__ == "__main__":
    debug_edge_query()
