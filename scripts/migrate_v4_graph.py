"""Copy all nodes and relationships from shopping-spatial-v4-pipeline-test to shopping-spatial-v4."""

from neo4j import GraphDatabase

SOURCE = "shopping-spatial-v4-pipeline-test"
TARGET = "shopping-spatial-v4"
URI = "bolt://localhost:7687"
AUTH = ("neo4j", "20010823")


def migrate():
    driver = GraphDatabase.driver(URI, auth=AUTH)

    # 1) Read all nodes from source
    with driver.session(database=SOURCE) as s:
        nodes = list(s.run("MATCH (n) RETURN id(n) AS old_id, labels(n) AS labels, properties(n) AS props"))
        print(f"Read {len(nodes)} nodes from {SOURCE}")

    # 2) Read all relationships from source
    with driver.session(database=SOURCE) as s:
        rels = list(s.run("""
            MATCH (a)-[r]->(b)
            RETURN id(a) AS src_id, id(b) AS dst_id, type(r) AS rel_type, properties(r) AS props
        """))
        print(f"Read {len(rels)} relationships from {SOURCE}")

    # 3) Clear target database
    with driver.session(database=TARGET) as s:
        s.run("MATCH (n) DETACH DELETE n")
        print(f"Cleared {TARGET}")

    # 4) Write nodes to target, tracking old_id -> new element_id
    node_id_map = {}
    labels_used = set()
    with driver.session(database=TARGET) as s:
        for rec in nodes:
            labels = rec["labels"]
            labels_used.update(labels)
            props = dict(rec["props"])
            # Build CREATE statement dynamically
            label_str = ":".join(labels)
            result = s.run(
                f"CREATE (n:{label_str}) SET n = $props RETURN elementId(n) AS new_id",
                props=props,
            )
            node_id_map[rec["old_id"]] = result.single()["new_id"]
        print(f"Created {len(node_id_map)} nodes in {TARGET}")
        print(f"Labels: {labels_used}")

    # 5) Write relationships to target
    with driver.session(database=TARGET) as s:
        created = 0
        for rec in rels:
            src_new = node_id_map.get(rec["src_id"])
            dst_new = node_id_map.get(rec["dst_id"])
            if not src_new or not dst_new:
                continue
            rel_type = rec["rel_type"]
            props = dict(rec["props"])
            s.run(
                f"""
                MATCH (a) WHERE elementId(a) = $src
                MATCH (b) WHERE elementId(b) = $dst
                CREATE (a)-[r:{rel_type} $props]->(b)
                """,
                src=src_new,
                dst=dst_new,
                props=props,
            )
            created += 1
        print(f"Created {created} relationships in {TARGET}")

    # 6) Verify
    with driver.session(database=TARGET) as s:
        node_count = s.run("MATCH (n) RETURN count(n) AS cnt").single()["cnt"]
        rel_count = s.run("MATCH ()-[r]->() RETURN count(r) AS cnt").single()["cnt"]
        print(f"Verification: {node_count} nodes, {rel_count} relationships in {TARGET}")

    driver.close()
    print("Migration complete.")


if __name__ == "__main__":
    migrate()
