"""Coverage target and default task construction from domain schemas."""

from __future__ import annotations

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
    """
    if profile is not None:
        task = str(getattr(profile, "default_task", "") or "")
        if task:
            return task

    task = str((schema.exploration.default_task or ""))
    if task:
        return task

    # Generic fallback
    return "广度优先探索所有主要页面类型"
