"""ReviewItem manifest — one record per staged transition candidate."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ReviewItem:
    """Immutable record representing one candidate transition awaiting review."""

    item_id: str                  # md5 of "source|action_signature|target"
    kind: str                     # currently always "transition"
    app: str
    domain: str
    source_page_type: str
    target_page_type: str
    source_summary: str
    target_summary: str
    action_description: str       # human-readable: "Tap [893,71] (搜索按钮)"
    action_params: dict           # raw action params dict
    before_screenshot: str        # relative path within batch dir, "" if missing
    after_screenshot: str
    observations: int
    explorer_confidence: str      # "confident" | "low_confidence" | ""
    vlm_verdict: str              # "approve" | "reject" | "unreviewed"
    vlm_reason: str
    blind_page_check: str         # "match" | "mismatch:<seen_src>-><seen_tgt>" | "skipped"
    source_kind: str = ""         # "human_assisted" | "" (autonomous)

    # ── Serialization ──────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "kind": self.kind,
            "app": self.app,
            "domain": self.domain,
            "source_page_type": self.source_page_type,
            "target_page_type": self.target_page_type,
            "source_summary": self.source_summary,
            "target_summary": self.target_summary,
            "action_description": self.action_description,
            "action_params": self.action_params,
            "before_screenshot": self.before_screenshot,
            "after_screenshot": self.after_screenshot,
            "observations": self.observations,
            "explorer_confidence": self.explorer_confidence,
            "source_kind": self.source_kind,
            "vlm_verdict": self.vlm_verdict,
            "vlm_reason": self.vlm_reason,
            "blind_page_check": self.blind_page_check,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReviewItem":
        return cls(
            item_id=str(data.get("item_id") or ""),
            kind=str(data.get("kind") or "transition"),
            app=str(data.get("app") or ""),
            domain=str(data.get("domain") or ""),
            source_page_type=str(data.get("source_page_type") or ""),
            target_page_type=str(data.get("target_page_type") or ""),
            source_summary=str(data.get("source_summary") or ""),
            target_summary=str(data.get("target_summary") or ""),
            action_description=str(data.get("action_description") or ""),
            action_params=dict(data.get("action_params") or {}),
            before_screenshot=str(data.get("before_screenshot") or ""),
            after_screenshot=str(data.get("after_screenshot") or ""),
            observations=int(data.get("observations") or 0),
            explorer_confidence=str(data.get("explorer_confidence") or ""),
            source_kind=str(data.get("source_kind") or ""),
            vlm_verdict=str(data.get("vlm_verdict") or "unreviewed"),
            vlm_reason=str(data.get("vlm_reason") or ""),
            blind_page_check=str(data.get("blind_page_check") or "skipped"),
        )


def _make_item_id(source_page_type: str, action_signature: str, target_page_type: str) -> str:
    """Stable item id: md5 of pipe-separated triple."""
    key = f"{source_page_type}|{action_signature}|{target_page_type}"
    return hashlib.md5(key.encode("utf-8")).hexdigest()


def _action_description(action_params: dict[str, Any]) -> str:
    """Build human-readable action description from params."""
    action = str(action_params.get("action") or action_params.get("action_type") or "unknown")
    element = action_params.get("element") or action_params.get("coordinate") or ""
    semantic = str(action_params.get("semantic_target") or action_params.get("target") or "")
    if element:
        coord_str = str(element)
        if semantic:
            return f"{action} {coord_str} ({semantic})"
        return f"{action} {coord_str}"
    if semantic:
        return f"{action} ({semantic})"
    return action


def _action_signature(action_params: dict[str, Any]) -> str:
    """Deterministic signature for action deduplication."""
    action = str(action_params.get("action") or action_params.get("action_type") or "")
    semantic = str(action_params.get("semantic_target") or action_params.get("target") or "")
    element = str(action_params.get("element") or action_params.get("coordinate") or "")
    return f"{action}|{semantic}|{element}"


def build_manifest(
    batch_dir: Path,
    staging_graph: dict[str, Any],
    vlm_results: dict[str, Any] | None = None,
) -> list[ReviewItem]:
    """Build ReviewItem list from a staging batch.

    Args:
        batch_dir: Path to the staging batch directory (for screenshot path resolution).
        staging_graph: Dict with keys: app, domain, states (list), edges (list).
        vlm_results: Optional mapping item_id -> {"verdict": ..., "reason": ...,
                     "blind_page_check": ...}.
    """
    states_by_id: dict[str, dict] = {
        s["state_id"]: s for s in staging_graph.get("states", []) if isinstance(s, dict)
    }
    app = str(staging_graph.get("app") or "")
    domain = str(staging_graph.get("domain") or "")
    vlm = vlm_results or {}

    items: list[ReviewItem] = []
    for edge in staging_graph.get("edges", []):
        if not isinstance(edge, dict):
            continue

        source_id = str(edge.get("source_id") or "")
        target_id = str(edge.get("target_id") or "")
        source_state = states_by_id.get(source_id, {})
        target_state = states_by_id.get(target_id, {})

        source_pt = str(source_state.get("page_type") or "unknown")
        target_pt = str(target_state.get("page_type") or "unknown")
        action_params = dict(edge.get("action_params") or {})
        sig = _action_signature(action_params)

        item_id = _make_item_id(source_pt, sig, target_pt)

        # Screenshot paths (relative to batch_dir)
        src_hash = str(source_state.get("screenshot_hash") or "")
        tgt_hash = str(target_state.get("screenshot_hash") or "")
        before_path = ""
        after_path = ""
        if src_hash:
            candidate = batch_dir / "screenshots" / f"{src_hash}.png"
            if candidate.exists():
                before_path = f"screenshots/{src_hash}.png"
        if tgt_hash:
            candidate = batch_dir / "screenshots" / f"{tgt_hash}.png"
            if candidate.exists():
                after_path = f"screenshots/{tgt_hash}.png"

        # Observations / confidence from edge evidence or action_params
        observations = int(action_params.get("observations") or edge.get("success_count") or 1)
        explorer_confidence = str(
            action_params.get("confidence_level")
            or action_params.get("confidence")
            or ""
        )
        # Normalize: if it looks like a float, map to label
        if explorer_confidence in ("confident", "low_confidence", ""):
            pass
        else:
            try:
                conf_float = float(explorer_confidence)
                explorer_confidence = "confident" if conf_float >= 0.7 else "low_confidence"
            except (ValueError, TypeError):
                explorer_confidence = ""

        # VLM result
        vlm_entry = vlm.get(item_id) or {}
        vlm_verdict = str(vlm_entry.get("verdict") or "unreviewed")
        vlm_reason = str(vlm_entry.get("reason") or "")
        blind_page_check = str(vlm_entry.get("blind_page_check") or "skipped")

        item = ReviewItem(
            item_id=item_id,
            kind="transition",
            app=app,
            domain=domain,
            source_page_type=source_pt,
            target_page_type=target_pt,
            source_summary=str(source_state.get("summary") or ""),
            target_summary=str(target_state.get("summary") or ""),
            action_description=_action_description(action_params),
            action_params=action_params,
            before_screenshot=before_path,
            after_screenshot=after_path,
            observations=observations,
            explorer_confidence=explorer_confidence,
            source_kind=str(action_params.get("source_kind") or ""),
            vlm_verdict=vlm_verdict,
            vlm_reason=vlm_reason,
            blind_page_check=blind_page_check,
        )
        items.append(item)

    return items
