"""Coverage target and default task construction from domain schemas."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .types import CoverageTarget


def coverage_from_schema(schema: Any, profile: Any = None) -> CoverageTarget:
    """Build a CoverageTarget from a merged MobileSchema.

    Priority: profile override → schema.exploration → derived from schema.transitions.
    """
    exp = schema.exploration

    # Profile override (Phase 3+)
    if profile is not None:
        prof_pages = tuple(getattr(profile, "coverage_page_types", ()))
        prof_trans = tuple(
            (str(a), str(b))
            for a, b in getattr(profile, "coverage_transitions", ())
        )
        if prof_pages:
            return CoverageTarget(page_types=prof_pages, transitions=prof_trans)

    # Schema exploration block
    if exp.coverage_page_types:
        return CoverageTarget(
            page_types=exp.coverage_page_types,
            transitions=exp.coverage_transitions,
        )

    # Derive from schema transitions (fallback for schemas without explicit coverage)
    pages: list[str] = []
    edges: list[tuple[str, str]] = []
    seen_pages: set[str] = set()
    for t in schema.transitions:
        for pt in (t.source, t.target):
            if pt and pt not in seen_pages:
                seen_pages.add(pt)
                pages.append(pt)
        if t.source and t.target:
            edges.append((t.source, t.target))
    return CoverageTarget(page_types=tuple(pages), transitions=tuple(edges))


def build_default_task(schema: Any, profile: Any = None, coverage: Any = None, app_display_name: str = "") -> str:
    """Return the default exploration task description.

    Priority: profile.default_task → schema.exploration.default_task → generic fallback.
    Schema task text may contain an ``{app}`` placeholder so one domain task
    works for every app in the domain.
    """
    if profile is not None:
        task = str(getattr(profile, "default_task", "") or "")
        if task:
            return task

    task = str((schema.exploration.default_task or ""))
    if task:
        return task.replace("{app}", app_display_name or "目标App")

    # Generic fallback
    return "广度优先探索所有主要页面类型"


# ── Session goals: one focused sub-target per exploration round ──────
#
# A 24-step run cannot cover 11 page types + 10 transitions, and a model
# chasing "everything" loops on the main chain forever. Graph building is
# multi-round by design: each round gets a small focused goal (finish and
# save when reached); the next round automatically picks the next gap.


def load_historical_coverage(storage_dir: str | Path) -> tuple[set[str], set[tuple[str, str]]]:
    """Merge covered pages/transitions from all previous runs in storage_dir."""
    pages: set[str] = set()
    transitions: set[tuple[str, str]] = set()
    root = Path(storage_dir)
    if not root.exists():
        return pages, transitions
    for path in sorted(root.glob("*_explore_transitions_*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        coverage = data.get("coverage") or {}
        pages.update(str(p) for p in (coverage.get("covered_page_types") or []))
        for pair in coverage.get("covered_transitions") or []:
            if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                transitions.add((str(pair[0]), str(pair[1])))
    return pages, transitions


def parse_focus(focus: str | None) -> tuple[set[str], set[tuple[str, str]]]:
    """Parse a --focus string: "category,my_account" or "home->category,search_result->filter_panel"."""
    pages: set[str] = set()
    transitions: set[tuple[str, str]] = set()
    if not focus:
        return pages, transitions
    for item in focus.replace("，", ",").split(","):
        item = item.strip()
        if not item:
            continue
        if "->" in item:
            src, _, tgt = item.partition("->")
            transitions.add((src.strip(), tgt.strip()))
        else:
            pages.add(item)
    return pages, transitions


def select_session_goal(
    coverage: CoverageTarget,
    covered_pages: set[str],
    covered_transitions: set[tuple[str, str]],
    *,
    focus: str | None = None,
    max_edges: int = 5,
) -> CoverageTarget:
    """Pick this round's focused sub-goal from the remaining coverage gaps.

    Priority: explicit --focus → first ``max_edges`` uncovered transitions
    (declaration order = skeleton first) → whole remaining gap.
    """
    focus_pages, focus_transitions = parse_focus(focus)
    if focus_pages or focus_transitions:
        goal_transitions = tuple(
            pair for pair in coverage.transitions
            if pair in focus_transitions
            or pair[0] in focus_pages
            or pair[1] in focus_pages
        ) or tuple(focus_transitions)
        goal_pages = tuple(dict.fromkeys(
            [*focus_pages, *(p for pair in goal_transitions for p in pair)]
        ))
        return CoverageTarget(page_types=goal_pages, transitions=goal_transitions)

    missing_transitions = [
        pair for pair in coverage.transitions if pair not in covered_transitions
    ][:max_edges]
    if not missing_transitions:
        # Everything covered before — re-verify the skeleton head.
        missing_transitions = list(coverage.transitions[:max_edges])
    goal_pages = tuple(dict.fromkeys(p for pair in missing_transitions for p in pair))
    return CoverageTarget(page_types=goal_pages, transitions=tuple(missing_transitions))


def describe_session_goal(goal: CoverageTarget) -> str:
    """Chinese description of this round's focus, appended to the task text."""
    if not goal.transitions:
        return ""
    edges = "、".join(f"{a}->{b}" for a, b in goal.transitions)
    return (
        f"【本轮焦点】本轮只需覆盖这些页面转移: {edges}。"
        "全部完成后立即调用 finish(message=...) 结束本轮探索，不要继续闲逛。"
    )
