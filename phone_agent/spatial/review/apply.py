"""Apply reviewed staging batch to the canonical graph.

Only approved transitions are written.  Provides double-apply prevention via
applied.json.  Lifecycle stage = "hypothesis", provenance via action_params.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def apply_review(
    batch_dir: str | Path,
    graph_store: Any = None,
    *,
    reviewer: str = "human",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Apply approved transitions from a reviewed staging batch to the canonical graph.

    Args:
        batch_dir: Path to the staging batch directory.
        graph_store: Optional GraphStore instance.  If None and not dry_run, a
                     default GraphStore() is created internally.
        reviewer: Name/identifier of the reviewer (for provenance).
        dry_run: If True, no Neo4j writes occur and applied.json is NOT written.

    Returns:
        Result dict: {approved, rejected, persisted, dry_run, error?}.

    Raises:
        RuntimeError: if applied.json already exists (double-apply prevention).
    """
    batch_dir = Path(batch_dir)
    batch_id = batch_dir.name

    # Double-apply prevention
    applied_path = batch_dir / "applied.json"
    if applied_path.exists():
        raise RuntimeError(
            f"Batch {batch_id} was already applied. "
            f"See {applied_path} for details."
        )

    # Load staging graph
    staging_graph_path = batch_dir / "staging_graph.json"
    if not staging_graph_path.exists():
        return {"error": f"staging_graph.json not found in {batch_dir}"}
    staging_graph = json.loads(staging_graph_path.read_text(encoding="utf-8"))

    # Load decisions
    from .decisions import load_decisions, validate_decisions
    decisions = load_decisions(batch_dir)
    errors = validate_decisions(decisions)
    if errors:
        return {"error": f"Invalid decisions: {errors}"}

    # Separate approved item_ids
    approved_ids: set[str] = {
        iid for iid, entry in decisions.items()
        if isinstance(entry, dict) and entry.get("decision") == "approve"
    }

    # Load manifest for item_id → edge mapping
    from .batch import load_manifest
    manifest_items = load_manifest(batch_dir)

    approved_count = sum(1 for item in manifest_items if item.item_id in approved_ids)
    rejected_count = len(manifest_items) - approved_count

    if dry_run:
        return {
            "approved": approved_count,
            "rejected": rejected_count,
            "persisted": False,
            "dry_run": True,
        }

    # Rebuild PageState objects from staging_graph
    from phone_agent.memory.spatial_graph_memory import PageState, TransitionEdge, SpatialGraphMemory

    states_by_id: dict[str, PageState] = {}
    for s in staging_graph.get("states", []):
        if not isinstance(s, dict):
            continue
        state = _dict_to_page_state(s)
        states_by_id[state.state_id] = state

    # Build approved TransitionEdge list with provenance
    approved_edges: list[TransitionEdge] = []
    for item in manifest_items:
        if item.item_id not in approved_ids:
            continue
        edge = _find_edge_for_item(item, staging_graph, batch_id)
        if edge is not None:
            approved_edges.append(edge)

    # Resolve graph store
    owns_store = False
    if graph_store is None:
        try:
            from phone_agent.memory.graph_store import GraphStore
            graph_store = GraphStore()
            owns_store = True
        except Exception as exc:
            logger.warning("apply_review: could not create GraphStore: %s", exc)
            graph_store = None

    try:
        from phone_agent.spatial.amsg_config import AMSGOptimConfig
        # Approved edges enter as hypothesis; the verified lifecycle policy
        # must not re-filter what the human just approved.
        memory = SpatialGraphMemory(graph_store, config=AMSGOptimConfig.legacy())
        promote_report = memory.promote_staging_to_canonical(
            states_by_id,
            approved_edges,
            persist=(graph_store is not None),
            # Review approval admits the edge as a hypothesis only - online
            # postcondition verification still has to earn the promotion.
            lifecycle_overrides={
                "lifecycle_stage": "hypothesis",
                "verification_count": 0,
                "dominance_ratio": 0.0,
            },
        )
    finally:
        if owns_store and graph_store is not None:
            try:
                graph_store.close()
            except Exception:
                pass

    persisted = graph_store is not None

    # Write applied.json receipt
    receipt: dict[str, Any] = {
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "reviewer": reviewer,
        "approved": approved_count,
        "rejected": rejected_count,
        "persisted": persisted,
        "promote_report": promote_report.to_dict(),
    }
    applied_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "approved": approved_count,
        "rejected": rejected_count,
        "persisted": persisted,
        "dry_run": False,
        "promote_report": promote_report.to_dict(),
    }


# ── helpers ───────────────────────────────────────────────────────────────


def _dict_to_page_state(data: dict[str, Any]) -> "PageState":
    """Reconstruct a PageState from its to_dict() representation."""
    from phone_agent.memory.spatial_graph_memory import PageState
    landmarks = data.get("landmarks")
    affordances = data.get("affordances")
    return PageState(
        state_id=str(data.get("state_id") or ""),
        app=str(data.get("app") or ""),
        page_type=str(data.get("page_type") or "unknown"),
        summary=str(data.get("summary") or ""),
        landmarks=tuple(landmarks) if isinstance(landmarks, (list, tuple)) else (),
        affordances=tuple(affordances) if isinstance(affordances, (list, tuple)) else (),
        slots=dict(data.get("slots") or {}),
        risk_level=str(data.get("risk_level") or "normal"),
        screenshot_hash=str(data.get("screenshot_hash") or ""),
        semantic_signature=str(data.get("semantic_signature") or ""),
        domain=str(data.get("domain") or ""),
    )


def _find_edge_for_item(item: Any, staging_graph: dict[str, Any], batch_id: str) -> Any:
    """Find the TransitionEdge matching a ReviewItem and attach provenance."""
    from phone_agent.memory.spatial_graph_memory import TransitionEdge

    for edge_dict in staging_graph.get("edges", []):
        if not isinstance(edge_dict, dict):
            continue

        # Match by source/target page type + action_type
        source_id = str(edge_dict.get("source_id") or "")
        target_id = str(edge_dict.get("target_id") or "")
        states = {s["state_id"]: s for s in staging_graph.get("states", []) if isinstance(s, dict)}
        src_state = states.get(source_id, {})
        tgt_state = states.get(target_id, {})

        if (
            str(src_state.get("page_type") or "") == item.source_page_type
            and str(tgt_state.get("page_type") or "") == item.target_page_type
        ):
            # Attach provenance to action_params
            action_params = dict(edge_dict.get("action_params") or {})
            action_params["source_type"] = "exploration_reviewed"
            action_params["review_batch_id"] = batch_id

            return TransitionEdge(
                source_id=source_id,
                target_id=target_id,
                action_type=str(edge_dict.get("action_type") or "unknown"),
                action_target=str(edge_dict.get("action_target") or ""),
                action_params=action_params,
                precondition=str(edge_dict.get("precondition") or ""),
                postcondition=str(edge_dict.get("postcondition") or ""),
                success_count=int(edge_dict.get("success_count") or 1),
                fail_count=int(edge_dict.get("fail_count") or 0),
                rollback_action=str(edge_dict.get("rollback_action") or "Back"),
                cost=float(edge_dict.get("cost") or 1.0),
                risk=str(edge_dict.get("risk") or "normal"),
                confidence=float(edge_dict.get("confidence") or 1.0),
                evidence=str(edge_dict.get("evidence") or ""),
            )
    return None
