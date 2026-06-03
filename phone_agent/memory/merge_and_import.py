#!/usr/bin/env python3
"""Merge exploration artifacts, deduplicate pages, and import into Neo4j.

Usage:
    python -m phone_agent.memory.merge_and_import \
        --input-dirs memory_db/exploration/taobao_spatial_v4 memory_db/exploration/autonomous_v7 \
        --output-dir memory_db/exploration/merged \
        --app 淘宝 \
        --import-graph
"""

from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def load_all_artifacts(input_dirs: list[str], app_filter: str) -> tuple[list[dict], list[dict]]:
    """Load all pages and transitions from multiple exploration directories."""
    all_pages: list[dict] = []
    all_transitions: list[dict] = []
    all_rejected: list[dict] = []
    files_loaded = 0

    for input_dir in input_dirs:
        base = Path(input_dir)
        if not base.exists():
            print(f"  skip: {input_dir} (not found)")
            continue

        for path in sorted(base.glob("*.json")):
            name = path.name
            if "trajectory" in name:
                continue
            try:
                data = json.load(open(path, encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue

            if "pages" in data:
                for p in data["pages"]:
                    p_app = p.get("app", data.get("app", ""))
                    if app_filter and app_filter not in p_app:
                        continue
                    all_pages.append(p)
                files_loaded += 1

            if "transitions" in data:
                for t in data["transitions"]:
                    all_transitions.append(t)
                for r in data.get("rejected_transitions", []):
                    all_rejected.append(r)

    print(f"  loaded {files_loaded} files: {len(all_pages)} pages, {len(all_transitions)} transitions")
    return all_pages, all_transitions


def deduplicate_pages(pages: list[dict]) -> list[dict]:
    """Merge pages by page_type, keeping the richest elements."""
    by_type: dict[str, list[dict]] = defaultdict(list)
    for p in pages:
        pt = p.get("page_type", "unknown")
        if pt == "unknown":
            continue
        by_type[pt].append(p)

    merged: list[dict] = []
    for pt, variants in sorted(by_type.items()):
        best = max(variants, key=lambda p: (
            len(p.get("elements") or {}),
            len(p.get("summary") or ""),
        ))
        all_elements: dict[str, str] = {}
        all_summaries: set[str] = set()
        for v in variants:
            for k, val in (v.get("elements") or {}).items():
                if k not in all_elements:
                    all_elements[k] = val
            summary = (v.get("summary") or "").strip()
            if summary:
                all_summaries.add(summary[:60])

        canonical = {
            "page_type": pt,
            "summary": best.get("summary", ""),
            "elements": all_elements,
            "screenshot_hash": best.get("screenshot_hash", ""),
            "app": best.get("app", ""),
        }
        merged.append(canonical)
        variant_count = len(variants)
        elem_count = len(all_elements)
        print(f"  [{pt}] {variant_count} variants → 1 canonical ({elem_count} elements)")

    return merged


def deduplicate_transitions(
    transitions: list[dict],
    valid_page_types: set[str],
) -> list[dict]:
    """Deduplicate transitions and validate against known page types."""
    seen: set[str] = set()
    result: list[dict] = []
    dropped = 0

    for t in transitions:
        src = t.get("from", "")
        tgt = t.get("to", "")
        src_type = src.split(":")[0] if ":" in src else src
        tgt_type = tgt.split(":")[0] if ":" in tgt else tgt

        if src_type not in valid_page_types or tgt_type not in valid_page_types:
            dropped += 1
            continue
        if src_type == tgt_type:
            dropped += 1
            continue

        action = t.get("action", {})
        action_type = action.get("action", "?")
        key = f"{src_type}|{action_type}|{tgt_type}"
        if key in seen:
            continue
        seen.add(key)

        canonical_from = f"{src_type}:{src.split(':', 1)[1] if ':' in src else ''}"
        canonical_to = f"{tgt_type}:{tgt.split(':', 1)[1] if ':' in tgt else ''}"
        result.append({"from": canonical_from, "action": action, "to": canonical_to})

    print(f"  transitions: {len(transitions)} → {len(result)} unique ({dropped} dropped)")
    return result


def build_quality_report(
    pages: list[dict],
    transitions: list[dict],
) -> dict[str, Any]:
    page_types = {p["page_type"] for p in pages}
    total_elements = sum(len(p.get("elements") or {}) for p in pages)
    pages_with_elements = sum(1 for p in pages if p.get("elements"))
    edge_types = Counter()
    for t in transitions:
        src = t["from"].split(":")[0]
        tgt = t["to"].split(":")[0]
        edge_types[f"{src}→{tgt}"] += 1

    core_edges = [
        "home→search_input", "search_input→search_result",
        "search_result→product_detail", "product_detail→spec_selection",
        "spec_selection→cart",
    ]
    missing_core = [e for e in core_edges if e not in edge_types]

    return {
        "page_types": sorted(page_types),
        "page_count": len(pages),
        "total_elements": total_elements,
        "pages_with_elements": pages_with_elements,
        "transition_count": len(transitions),
        "edge_types": dict(edge_types.most_common()),
        "core_edges_found": [e for e in core_edges if e in edge_types],
        "core_edges_missing": missing_core,
    }


def direct_neo4j_import(
    pages: list[dict],
    transitions: list[dict],
    app: str,
) -> dict[str, int]:
    """Write merged graph directly to Neo4j, bypassing staging pipeline.

    Creates PageNode nodes and TRANSITION edges between them.
    Clears existing PageNode/TRANSITION data for this app first.
    """
    import os
    from dotenv import load_dotenv
    load_dotenv()

    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "password")
    database = os.environ.get("NEO4J_DATABASE", "shopping-spatial-v4")

    from neo4j import GraphDatabase
    driver = GraphDatabase.driver(uri, auth=(user, password))
    driver.verify_connectivity()
    print(f"  Neo4j connected: {uri} / {database}")

    node_count = 0
    edge_count = 0

    with driver.session(database=database) as session:
        session.run(
            "MATCH (n:PageNode {app: $app})-[r:TRANSITION]-() DELETE r",
            app=app,
        )
        session.run("MATCH (n:PageNode {app: $app}) DELETE n", app=app)
        print(f"  Cleared existing PageNode data for {app}")

        for page in pages:
            pt = page["page_type"]
            elements = page.get("elements") or {}
            session.run(
                "MERGE (n:PageNode {app: $app, page_type: $pt}) "
                "SET n.summary = $summary, "
                "    n.elements = $elements, "
                "    n.element_count = $elem_count",
                app=app,
                pt=pt,
                summary=page.get("summary", ""),
                elements=json.dumps(elements, ensure_ascii=False),
                elem_count=len(elements),
            )
            node_count += 1

        for t in transitions:
            src_type = t["from"].split(":")[0]
            tgt_type = t["to"].split(":")[0]
            action = t.get("action", {})
            action_type = action.get("action", "Tap")
            action_json = json.dumps(action, ensure_ascii=False, default=str)

            session.run(
                "MATCH (a:PageNode {app: $app, page_type: $src}) "
                "MATCH (b:PageNode {app: $app, page_type: $tgt}) "
                "MERGE (a)-[r:TRANSITION {action_type: $act_type}]->(b) "
                "SET r.action_detail = $action_json, "
                "    r.from_summary = $from_s, "
                "    r.to_summary = $to_s",
                app=app,
                src=src_type,
                tgt=tgt_type,
                act_type=action_type,
                action_json=action_json,
                from_s=t["from"],
                to_s=t["to"],
            )
            edge_count += 1

    driver.close()
    return {"nodes": node_count, "edges": edge_count}


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge exploration data and import to Neo4j")
    parser.add_argument("--input-dirs", nargs="+", required=True, help="Exploration directories to merge")
    parser.add_argument("--output-dir", type=str, default="memory_db/exploration/merged", help="Output directory")
    parser.add_argument("--app", type=str, default="淘宝", help="App name filter")
    parser.add_argument("--import-graph", action="store_true", help="Import merged data into Neo4j")
    args = parser.parse_args()

    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  Merge & Import Pipeline: {args.app}")
    print(f"  Input: {args.input_dirs}")
    print(f"{'='*60}\n")

    # Step 1: Load
    print("[1/4] Loading artifacts...")
    all_pages, all_transitions = load_all_artifacts(args.input_dirs, args.app)

    # Step 2: Deduplicate pages
    print("\n[2/4] Deduplicating pages...")
    merged_pages = deduplicate_pages(all_pages)
    valid_types = {p["page_type"] for p in merged_pages}

    # Step 3: Deduplicate transitions
    print("\n[3/4] Deduplicating transitions...")
    merged_transitions = deduplicate_transitions(all_transitions, valid_types)

    # Quality report
    report = build_quality_report(merged_pages, merged_transitions)
    print(f"\n[Quality Report]")
    print(f"  Pages: {report['page_count']} types, {report['total_elements']} total elements")
    print(f"  Transitions: {report['transition_count']} unique edges")
    print(f"  Core edges found: {report['core_edges_found']}")
    if report["core_edges_missing"]:
        print(f"  Core edges MISSING: {report['core_edges_missing']}")
    else:
        print(f"  All core edges present!")

    # Save merged files
    timestamp = int(time.time())
    pages_path = output / f"{args.app}_merged_pages_{timestamp}.json"
    trans_path = output / f"{args.app}_merged_transitions_{timestamp}.json"

    with open(pages_path, "w", encoding="utf-8") as f:
        json.dump({
            "app": args.app,
            "task": "merged_exploration",
            "total_pages": len(merged_pages),
            "pages": merged_pages,
            "quality_report": report,
        }, f, ensure_ascii=False, indent=2)

    with open(trans_path, "w", encoding="utf-8") as f:
        json.dump({
            "app": args.app,
            "total_transitions": len(merged_transitions),
            "transitions": merged_transitions,
        }, f, ensure_ascii=False, indent=2)

    print(f"\n[4/4] Saved to {output}/")
    print(f"  {pages_path.name}")
    print(f"  {trans_path.name}")

    # Optional: import to Neo4j
    if args.import_graph:
        print(f"\n[Import] Writing directly to Neo4j...")
        try:
            imported = direct_neo4j_import(merged_pages, merged_transitions, args.app)
            print(f"  Done: {imported['nodes']} page nodes, {imported['edges']} transition edges")
        except Exception as e:
            print(f"  Import failed: {e}")
            import traceback
            traceback.print_exc()

    print(f"\n{'='*60}")
    print(f"  Pipeline complete")
    print(f"{'='*60}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
