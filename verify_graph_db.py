#!/usr/bin/env python
"""Verify Neo4j graph database connection and check shopping-spatial-v1 data."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

from phone_agent.memory.graph_store import GraphStore


def verify_database():
    """Verify database connection and check data."""
    print("=" * 60)
    print("Neo4j Database Verification")
    print("=" * 60)

    # Check environment
    print(f"\nEnvironment:")
    print(f"  NEO4J_URI: {os.environ.get('NEO4J_URI', 'NOT SET')}")
    print(f"  NEO4J_DATABASE: {os.environ.get('NEO4J_DATABASE', 'NOT SET')}")

    # Connect to database
    print(f"\n📡 Connecting to Neo4j...")
    try:
        gs = GraphStore()
        print(f"✅ Connected to database: {gs.database}")
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        return False

    # Check page states
    print(f"\n📊 Checking page states...")
    try:
        with gs.driver.session(database=gs.database) as session:
            # Count total pages
            result = session.run("MATCH (p:PageState) RETURN count(p) as count")
            count = result.single()["count"]
            print(f"  Total PageStates: {count}")

            # Count by app
            result = session.run("""
                MATCH (p:PageState)
                RETURN p.app as app, count(p) as count
                ORDER BY count DESC
                LIMIT 10
            """)
            print(f"\n  Top 10 apps:")
            for record in result:
                print(f"    {record['app']}: {record['count']} pages")

            # Count by page_type
            result = session.run("""
                MATCH (p:PageState)
                WHERE p.page_type IS NOT NULL
                RETURN p.page_type as type, count(p) as count
                ORDER BY count DESC
                LIMIT 10
            """)
            print(f"\n  Top 10 page types:")
            for record in result:
                print(f"    {record['type']}: {record['count']} pages")

            # Check edges
            result = session.run("MATCH ()-[r:TRANSITION]->() RETURN count(r) as count")
            edge_count = result.single()["count"]
            print(f"\n  Total TRANSITION edges: {edge_count}")

            # Sample Taobao pages
            result = session.run("""
                MATCH (p:PageState)
                WHERE p.app = '淘宝'
                RETURN p.page_type as type, p.summary as summary
                LIMIT 5
            """)
            print(f"\n  Sample 淘宝 pages:")
            for record in result:
                summary = record['summary'][:60] if record['summary'] else 'N/A'
                print(f"    [{record['type']}] {summary}...")

        print(f"\n✅ Database verification complete")
        return True
    except Exception as e:
        print(f"\n❌ Query failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = verify_database()
    sys.exit(0 if success else 1)
