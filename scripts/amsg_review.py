"""CLI for staging batch management.

Commands:
    generate  <pages.json> [transitions.json]  — create a staging batch
    status    <batch_dir>                       — show batch status
    apply     <batch_dir> [--dry-run] [--database X]  — apply to graph
    vlm       <batch_dir>                       — run VLM review headless

Usage:
    python scripts/amsg_review.py generate memory_db/exploration/taobao_explore_1.json
    python scripts/amsg_review.py status memory_db/staging/taobao_20240101T000000Z
    python scripts/amsg_review.py apply memory_db/staging/taobao_20240101T000000Z --dry-run
    python scripts/amsg_review.py vlm memory_db/staging/taobao_20240101T000000Z
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def cmd_generate(args: argparse.Namespace) -> int:
    from phone_agent.spatial.review.batch import create_staging_batch

    pages_path = Path(args.pages_path)
    transitions_path = Path(args.transitions_path) if args.transitions_path else None
    storage_root = args.staging_root or "memory_db/staging"

    try:
        batch_dir = create_staging_batch(
            pages_path, transitions_path, storage_root=storage_root
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    # Load manifest count
    manifest_path = batch_dir / "manifest.json"
    item_count = 0
    if manifest_path.exists():
        item_count = len(json.loads(manifest_path.read_text(encoding="utf-8")))

    print(json.dumps({
        "batch_dir": str(batch_dir),
        "manifest_items": item_count,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    from phone_agent.spatial.review.batch import (
        load_manifest, load_staging_graph, is_applied, load_decisions
    )
    from phone_agent.spatial.review.decisions import summarize

    batch_dir = Path(args.batch_dir)
    if not batch_dir.exists():
        print(f"ERROR: batch dir not found: {batch_dir}", file=sys.stderr)
        return 1

    try:
        staging_graph = load_staging_graph(batch_dir)
    except FileNotFoundError:
        staging_graph = {}

    manifest = load_manifest(batch_dir)
    decisions = load_decisions(batch_dir)
    summary = summarize(manifest, decisions)

    print(json.dumps({
        "batch_id": batch_dir.name,
        "app": staging_graph.get("app", ""),
        "domain": staging_graph.get("domain", ""),
        "applied": is_applied(batch_dir),
        "states": len(staging_graph.get("states", [])),
        "edges": len(staging_graph.get("edges", [])),
        "manifest_items": len(manifest),
        "decisions_summary": summary,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    from phone_agent.spatial.review.apply import apply_review

    batch_dir = Path(args.batch_dir)
    if not batch_dir.exists():
        print(f"ERROR: batch dir not found: {batch_dir}", file=sys.stderr)
        return 1

    graph_store = None
    if not args.dry_run:
        database = args.database or "shopping-spatial-v4"
        try:
            from phone_agent.memory.graph_store import GraphStore
            graph_store = GraphStore(database=database)
        except Exception as exc:
            print(f"WARNING: could not create GraphStore: {exc}", file=sys.stderr)

    try:
        result = apply_review(batch_dir, graph_store=graph_store, dry_run=args.dry_run)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        if graph_store is not None:
            try:
                graph_store.close()
            except Exception:
                pass

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_vlm(args: argparse.Namespace) -> int:
    from phone_agent.spatial.review.webui_review import run_vlm_review_on_batch

    batch_dir = Path(args.batch_dir)
    if not batch_dir.exists():
        print(f"ERROR: batch dir not found: {batch_dir}", file=sys.stderr)
        return 1

    try:
        updated = run_vlm_review_on_batch(batch_dir)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps({"updated": updated}, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="AMSG staging batch CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # generate
    gen_p = subparsers.add_parser("generate", help="Create staging batch from exploration artifacts")
    gen_p.add_argument("pages_path", help="Path to *_explore_*.json pages file")
    gen_p.add_argument("transitions_path", nargs="?", default=None, help="Optional transitions JSON")
    gen_p.add_argument("--staging-root", default="memory_db/staging")

    # status
    status_p = subparsers.add_parser("status", help="Show batch status")
    status_p.add_argument("batch_dir", help="Path to batch directory")

    # apply
    apply_p = subparsers.add_parser("apply", help="Apply approved decisions to graph")
    apply_p.add_argument("batch_dir", help="Path to batch directory")
    apply_p.add_argument("--dry-run", action="store_true")
    apply_p.add_argument("--database", default=None)

    # vlm
    vlm_p = subparsers.add_parser("vlm", help="Run VLM review headless")
    vlm_p.add_argument("batch_dir", help="Path to batch directory")

    args = parser.parse_args()

    dispatch = {
        "generate": cmd_generate,
        "status": cmd_status,
        "apply": cmd_apply,
        "vlm": cmd_vlm,
    }
    return dispatch[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
