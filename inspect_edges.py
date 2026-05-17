#!/usr/bin/env python
"""Inspect relationship properties."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

from neo4j import GraphDatabase


def inspect_edges():
    """Check all relationship types and their properties."""
    print("=" * 60)
    print("Relationship Properties Inspection")
    print("=" * 60)

    uri = os.environ.get("NEO4J_URI")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD")
    database = os.environ.get("NEO4J_DATABASE")

    driver = GraphDatabase.driver(uri, auth=(user, password))

    with driver.session(database=database) as session:
        # Check NEXT_ACTION edges
        print(f"\n📊 NEXT_ACTION edges:")
        result = session.run("""
            MATCH ()-[r:NEXT_ACTION]->()
            RETURN properties(r) as props
            LIMIT 1
        """)
        record = result.single()
        if record:
            props = record["props"]
            print(f"  Properties: {list(props.keys())}")
            for key, value in props.items():
                print(f"    {key}: {value}")

        # Check PRODUCES edges
        print(f"\n📊 PRODUCES edges:")
        result = session.run("""
            MATCH ()-[r:PRODUCES]->()
            RETURN properties(r) as props
            LIMIT 1
        """)
        record = result.single()
        if record:
            props = record["props"]
            print(f"  Properties: {list(props.keys())}")
            for key, value in props.items():
                print(f"    {key}: {value}")

        # Check STARTS_AT edges
        print(f"\n📊 STARTS_AT edges:")
        result = session.run("""
            MATCH ()-[r:STARTS_AT]->()
            RETURN properties(r) as props
            LIMIT 1
        """)
        record = result.single()
        if record and record["props"]:
            props = record["props"]
            print(f"  Properties: {list(props.keys())}")
            for key, value in props.items():
                print(f"    {key}: {value}")
        else:
            print(f"  No properties")

        # Sample path with edges
        print(f"\n📊 Sample path (3 hops):")
        result = session.run("""
            MATCH path = (s1:UIState)-[r1:NEXT_ACTION]->(a:Action)-[r2:PRODUCES]->(s2:UIState)
            RETURN s1.page_type as from_page, s2.page_type as to_page, a.action_type as action
            LIMIT 3
        """)
        for record in result:
            print(f"  {record['from_page']} --[{record['action']}]--> {record['to_page']}")

    driver.close()
    print(f"\n✅ Inspection complete")


if __name__ == "__main__":
    inspect_edges()
