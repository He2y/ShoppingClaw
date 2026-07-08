"""Decisions JSON load/save/validate and summary helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .manifest import ReviewItem

VALID_DECISIONS = {"approve", "reject"}


def _default_decision(item: ReviewItem) -> str:
    """Pre-fill default decision from item attributes.

    Approve if:
      - vlm_verdict == "approve", OR
      - unreviewed AND observations >= 2 AND explorer_confidence != "low_confidence"
    Otherwise reject.
    """
    if item.vlm_verdict == "approve":
        return "approve"
    # Human-assisted collection IS a human verification of the edge — the
    # operator drove the device and confirmed both endpoint pages visually.
    if (
        getattr(item, "source_kind", "") == "human_assisted"
        and item.explorer_confidence != "low_confidence"
    ):
        return "approve"
    if (
        item.vlm_verdict == "unreviewed"
        and item.observations >= 2
        and item.explorer_confidence != "low_confidence"
    ):
        return "approve"
    return "reject"


def build_default_decisions(items: list[ReviewItem]) -> dict[str, dict[str, Any]]:
    """Build decisions.json dict with pre-filled defaults."""
    return {
        item.item_id: {"decision": _default_decision(item), "note": ""}
        for item in items
    }


def load_decisions(batch_dir: Path) -> dict[str, dict[str, Any]]:
    """Load decisions.json from batch directory. Returns empty dict if missing."""
    path = batch_dir / "decisions.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_decisions(batch_dir: Path, decisions: dict[str, dict[str, Any]]) -> None:
    """Write decisions.json atomically."""
    path = batch_dir / "decisions.json"
    path.write_text(
        json.dumps(decisions, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def validate_decisions(decisions: dict[str, dict[str, Any]]) -> list[str]:
    """Return list of validation error messages (empty = valid)."""
    errors = []
    for item_id, entry in decisions.items():
        if not isinstance(entry, dict):
            errors.append(f"decisions[{item_id!r}] must be a dict")
            continue
        decision = entry.get("decision")
        if decision not in VALID_DECISIONS:
            errors.append(
                f"decisions[{item_id!r}].decision={decision!r} must be one of {sorted(VALID_DECISIONS)}"
            )
    return errors


def summarize(manifest: list[ReviewItem], decisions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Return counts dict: total, approved, rejected, unresolved, warnings."""
    total = len(manifest)
    approved = 0
    rejected = 0
    unresolved = 0
    warnings: list[str] = []

    for item in manifest:
        entry = decisions.get(item.item_id)
        if entry is None:
            unresolved += 1
            continue
        decision = entry.get("decision")
        if decision == "approve":
            approved += 1
            if item.explorer_confidence == "low_confidence":
                warnings.append(f"{item.item_id}: approved with low_confidence")
            if item.blind_page_check.startswith("mismatch"):
                warnings.append(f"{item.item_id}: approved despite blind_page_check={item.blind_page_check}")
        elif decision == "reject":
            rejected += 1
        else:
            unresolved += 1

    return {
        "total": total,
        "approved": approved,
        "rejected": rejected,
        "unresolved": unresolved,
        "warnings": warnings,
    }
