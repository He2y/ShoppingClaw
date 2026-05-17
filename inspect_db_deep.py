#!/usr/bin/env python
"""Deep inspection of shopping-spatial-v1 database."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

from neo4j import GraphDatabase


def inspect_database():
    """Deep inspection of database structure."""
    print("=" * 60)
    print("Deep Database Inspection")
    print("=" * 60)

    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD")
    database = os.environ.get("NEO4J_DATABASE", "shopping-spatial-v1")

    print(f"\n📡 Connecting to {database}...")
    driver = GraphDatabase.driver(uri, auth=(user, password))

    with driver.session(database=database) as session:
        # List all node labels
        print(f"\n📊 Node labels:")
        result = session.run("CALL db.labels()")
        labels = [r["label"] for r in result]
        for label in labels:
            result2 = session.run(f"MATCH (n:{label}) RETURN count(n) as count")
            count = result2.single()["count"]
            print(f"  {label}: {count} nodes")

        # List all relationship types
        print(f"\n📊 Relationship types:")
        result = session.run("CALL db.relationshipTypes()")
        rel_types = [r["relationshipType"] for r in result]
        for rel_type in rel_types:
            result2 = session.run(f"MATCH ()-[r:{rel_type}]->() RETURN count(r) as count")
            count = result2.single()["count"]
            print(f"  {rel_type}: {count} edges")

        # Check node properties
        if labels:
            print(f"\n📊 Sample node (first label: {labels[0]}):")
            result = session.run(f"MATCH (n:{labels[0]}) RETURN n LIMIT 1")
            record = result.single()
            if record:
                node = dict(record["n"])
                for key, value in list(node.items())[:10]:
                    print(f"  {key}: {str(value)[:80]}")

        # Check if any shopping-related data
        print(f"\n📊 Searching for shopping-related data:")
        for label in labels:
            result = session.run(f"""
                MATCH (n:{label})
                WHERE n.app IS NOT NULL OR n.semantic_signature CONTAINS '淘宝'
                RETURN count(n) as count
            """)
            count = result.single()["count"]
            if count > 0:
                print(f"  {label} with shopping data: {count} nodes")

    driver.close()
    print(f"\n✅ Inspection complete")


if __name__ == "__main__":
    inspect_database()
