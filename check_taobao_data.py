#!/usr/bin/env python
"""Check if existing UIState nodes can enable graph navigation."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

from neo4j import GraphDatabase


def check_taobao_graph():
    """Check Taobao UI states and transitions."""
    print("=" * 60)
    print("Taobao Graph Navigation Data Check")
    print("=" * 60)

    uri = os.environ.get("NEO4J_URI")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD")
    database = os.environ.get("NEO4J_DATABASE")

    driver = GraphDatabase.driver(uri, auth=(user, password))

    with driver.session(database=database) as session:
        # Check Taobao UI states
        print(f"\n📊 淘宝 UI States:")
        result = session.run("""
            MATCH (s:UIState)
            WHERE s.app = '淘宝'
            RETURN s.page_type as type, s.summary as summary, s.state_id as id
            ORDER BY s.page_type
            LIMIT 10
        """)

        taobao_states = []
        for record in result:
            taobao_states.append(record["id"])
            summary = (record["summary"][:60] + "...") if record["summary"] and len(record["summary"]) > 60 else record["summary"]
            print(f"  [{record['type']}] {summary}")
            print(f"    ID: {record['id']}")

        # Check transitions between Taobao states
        print(f"\n📊 淘宝 Transitions:")
        result = session.run("""
            MATCH (s1:UIState)-[r:NEXT_ACTION]->(s2:UIState)
            WHERE s1.app = '淘宝' AND s2.app = '淘宝'
            RETURN s1.page_type as from_type, s2.page_type as to_type, r.action as action, count(r) as count
            ORDER BY count DESC
            LIMIT 10
        """)

        for record in result:
            print(f"  {record['from_type']} → {record['to_type']}: {record['action']} (x{record['count']})")

        # Check if there's a complete path from home to purchase
        print(f"\n📊 Checking complete shopping path:")
        result = session.run("""
            MATCH path = (start:UIState)-[:NEXT_ACTION*]->(end:UIState)
            WHERE start.app = '淘宝' AND start.page_type = 'home'
                AND end.app = '淘宝' AND end.page_type IN ['cart', 'checkout', 'spec_selection']
            RETURN start.page_type as start_page, end.page_type as end_page, length(path) as steps
            ORDER BY steps
            LIMIT 5
        """)

        paths_found = False
        for record in result:
            paths_found = True
            print(f"  {record['start_page']} → ... → {record['end_page']}: {record['steps']} steps")

        if not paths_found:
            print(f"  ⚠️ No complete paths found")

        # Check total edges
        result = session.run("""
            MATCH (s1:UIState)-[r:NEXT_ACTION]->(s2:UIState)
            WHERE s1.app = '淘宝'
            RETURN count(r) as count
        """)
        edge_count = result.single()["count"]
        print(f"\n  Total 淘宝 NEXT_ACTION edges: {edge_count}")

    driver.close()
    print(f"\n✅ Check complete")


if __name__ == "__main__":
    check_taobao_graph()
