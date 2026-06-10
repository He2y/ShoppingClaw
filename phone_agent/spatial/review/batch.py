"""Staging batch lifecycle management.

A staging batch lives at memory_db/staging/<batch_id>/ and contains:
  - staging_graph.json  — canonical states + edges from import_exploration_staging
  - manifest.json       — ReviewItem list
  - decisions.json      — {item_id: {decision, note}} pre-filled defaults
  - screenshots/        — PNGs copied from the exploration storage dir
  - applied.json        — present only after apply_review (prevents double-apply)
"""

from __future__ import annotations

import base64
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Fields present on TransitionEdge (verify against spatial_graph_memory.py)
_EDGE_FIELDS = (
    "source_id",
    "target_id",
    "action_type",
    "action_target",
    "action_params",
    "precondition",
    "postcondition",
    "success_count",
    "fail_count",
    "rollback_action",
    "cost",
    "risk",
    "confidence",
    "evidence",
)


def create_staging_batch(
    pages_path: str | Path,
    transitions_path: str | Path | None = None,
    *,
    storage_root: str | Path = "memory_db/staging",
    batch_id: str | None = None,
) -> Path:
    """Create a new staging batch from exploration artifact paths.

    Args:
        pages_path: Path to *_explore_*.json pages file.
        transitions_path: Optional transitions JSON; auto-detected if None.
        storage_root: Root directory for staging batches.
        batch_id: Override batch id; defaults to "<app>_<UTC timestamp>".

    Returns:
        Path to the created batch directory.
    """
    from phone_agent.memory.spatial_graph_memory import SpatialGraphMemory

    pages_path = Path(pages_path)

    # Run staging (side-effect-free — no Neo4j)
    memory = SpatialGraphMemory(graph_store=None)
    states, edges, quality_report = memory.import_exploration_staging(
        pages_path, transitions_path
    )

    # Read app name from pages file for batch_id
    try:
        pages_data = json.loads(pages_path.read_text(encoding="utf-8"))
    except Exception:
        pages_data = {}
    app_raw = str(pages_data.get("app") or "unknown")

    # Resolve canonical app id + domain
    try:
        from phone_agent.spatial.app_registry import get_default_app_registry
        registry = get_default_app_registry()
        app = registry.canonical_id(app_raw) or app_raw
        domain = registry.domain_of(app_raw) or ""
    except Exception:
        app = app_raw
        domain = ""

    # Create batch directory
    if batch_id is None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        batch_id = f"{app}_{ts}"
    batch_dir = Path(storage_root) / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    (batch_dir / "screenshots").mkdir(exist_ok=True)

    # Serialize states
    states_list = [s.to_dict() for s in states.values()]

    # Serialize edges using known fields
    edges_list = [_edge_to_dict(e) for e in edges]

    staging_graph: dict[str, Any] = {
        "app": app,
        "domain": domain,
        "states": states_list,
        "edges": edges_list,
        "quality": quality_report.to_dict(),
        "source_pages_path": str(pages_path),
        "source_transitions_path": str(
            transitions_path
        ) if transitions_path else "",
    }
    (batch_dir / "staging_graph.json").write_text(
        json.dumps(staging_graph, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Copy screenshots from exploration storage dir
    exploration_dir = pages_path.parent
    _copy_screenshots(exploration_dir, batch_dir, states)

    # Update staging_graph.json with screenshot_file fields now that screenshots are copied
    _annotate_states_with_screenshot_file(states_list, batch_dir)
    staging_graph["states"] = states_list
    (batch_dir / "staging_graph.json").write_text(
        json.dumps(staging_graph, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Build manifest
    from .manifest import build_manifest, ReviewItem
    manifest_items = build_manifest(batch_dir, staging_graph, vlm_results=None)

    manifest_data = [item.to_dict() for item in manifest_items]
    (batch_dir / "manifest.json").write_text(
        json.dumps(manifest_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Build default decisions
    from .decisions import build_default_decisions, save_decisions
    decisions = build_default_decisions(manifest_items)
    save_decisions(batch_dir, decisions)

    logger.info(
        "Staging batch created: %s  (%d states, %d edges, %d manifest items)",
        batch_dir,
        len(states),
        len(edges),
        len(manifest_items),
    )
    return batch_dir


# ── helpers ───────────────────────────────────────────────────────────────


def _edge_to_dict(edge: Any) -> dict[str, Any]:
    """Serialize a TransitionEdge to dict via known fields."""
    result: dict[str, Any] = {}
    for field in _EDGE_FIELDS:
        val = getattr(edge, field, None)
        if isinstance(val, dict):
            result[field] = dict(val)
        elif isinstance(val, (list, tuple)):
            result[field] = list(val)
        else:
            result[field] = val
    return result


def _copy_screenshots(
    exploration_dir: Path,
    batch_dir: Path,
    states: dict[str, Any],
) -> None:
    """Copy screenshots referenced by states from exploration_dir into batch_dir/screenshots/.

    Looks for:
      - <exploration_dir>/screenshots/<hash>.png  (Phase 5A screenshot persistence)
      - inline base64 in page data (if PageState carries screenshot_hash only, we can't decode)
    """
    screenshots_dir = batch_dir / "screenshots"
    for state in states.values():
        h = str(getattr(state, "screenshot_hash", "") or "")
        if not h:
            continue
        src = exploration_dir / "screenshots" / f"{h}.png"
        dst = screenshots_dir / f"{h}.png"
        if dst.exists():
            continue
        if src.exists():
            try:
                shutil.copy2(src, dst)
            except OSError as exc:
                logger.debug("Could not copy screenshot %s: %s", src, exc)


def _annotate_states_with_screenshot_file(
    states_list: list[dict[str, Any]],
    batch_dir: Path,
) -> None:
    """Add screenshot_file field to each state dict if the file exists in batch."""
    for s in states_list:
        h = str(s.get("screenshot_hash") or "")
        if not h:
            continue
        rel = f"screenshots/{h}.png"
        if (batch_dir / rel).exists():
            s["screenshot_file"] = rel


def load_staging_graph(batch_dir: Path) -> dict[str, Any]:
    """Load staging_graph.json from batch directory."""
    path = batch_dir / "staging_graph.json"
    if not path.exists():
        raise FileNotFoundError(f"staging_graph.json not found in {batch_dir}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_manifest(batch_dir: Path) -> list[Any]:
    """Load manifest.json items as ReviewItem list."""
    from .manifest import ReviewItem
    path = batch_dir / "manifest.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [ReviewItem.from_dict(item) for item in data if isinstance(item, dict)]


def save_manifest(batch_dir: Path, items: list[Any]) -> None:
    """Write manifest.json."""
    path = batch_dir / "manifest.json"
    path.write_text(
        json.dumps([item.to_dict() for item in items], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def is_applied(batch_dir: Path) -> bool:
    """Return True if applied.json exists (batch was already applied to graph)."""
    return (batch_dir / "applied.json").exists()


def list_batches(storage_root: str | Path = "memory_db/staging") -> list[dict[str, Any]]:
    """List staging batches newest-first."""
    root = Path(storage_root)
    if not root.exists():
        return []
    batches = []
    for d in sorted(root.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        batches.append({
            "batch_id": d.name,
            "path": str(d),
            "applied": is_applied(d),
            "has_manifest": (d / "manifest.json").exists(),
        })
    return batches
