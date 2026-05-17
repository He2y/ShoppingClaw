#!/usr/bin/env python
"""Check all Neo4j databases and find where the shopping data is."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

from neo4j import GraphDatabase


def check_all_databases():
    """List all databases and check their contents."""
    print("=" * 60)
    print("Neo4j Database Inventory")
    print("=" * 60)

    # Connect to system database
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD")

    print(f"\n📡 Connecting to Neo4j at {uri}...")
    driver = GraphDatabase.driver(uri, auth=(user, password))

    # List all databases
    print(f"\n📊 Listing all databases:")
    with driver.session(database="system") as session:
        result = session.run("SHOW DATABASES")
        databases = []
        for record in result:
            name = record["name"]
            current_status = record["currentStatus"]
            print(f"  {name}: {current_status}")
            if name not in ["system"]:
                databases.append(name)

    # Check each database for PageState nodes
    print(f"\n📊 Checking PageState nodes in each database:")
    for db_name in databases:
        try:
            with driver.session(database=db_name) as session:
                # Count pages
                result = session.run("MATCH (p:PageState) RETURN count(p) as count")
                page_count = result.single()["count"]

                # Count edges
                result = session.run("MATCH ()-[r:TRANSITION]->() RETURN count(r) as count")
                edge_count = result.single()["count"]

                if page_count > 0 or edge_count > 0:
                    print(f"  ✅ {db_name}: {page_count} pages, {edge_count} edges")

                    # Check if it has 淘宝 data
                    result = session.run("""
                        MATCH (p:PageState)
                        WHERE p.app = '淘宝'
                        RETURN count(p) as count
                    """)
                    taobao_count = result.single()["count"]
                    if taobao_count > 0:
                        print(f"      └─ 淘宝: {taobao_count} pages")
                else:
                    print(f"  ⚪ {db_name}: empty")
        except Exception as e:
            print(f"  ❌ {db_name}: error - {e}")

    driver.close()
    print(f"\n✅ Database inventory complete")


if __name__ == "__main__":
    check_all_databases()
