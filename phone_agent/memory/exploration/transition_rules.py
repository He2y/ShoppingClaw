"""Schema-driven transition validation engine for offline exploration."""

from __future__ import annotations

from typing import Any, Callable

from .safety import SafetyPolicy, _page_type_str


# Named region predicates: (x, y) → bool
# These are normalised coordinates matching the legacy hardcoded helpers.
REGIONS: dict[str, Callable[[float, float], bool]] = {
    "top_right":      lambda x, y: x >= 650 and y <= 220,
    "top_right_band": lambda x, y: x >= 800 and 150 <= y <= 420,
    "content_list":   lambda x, y: y >= 250,
    "bottom_cta":     lambda x, y: y >= 850,
}


def _extract_coords(action: dict[str, Any]) -> tuple[float, float] | None:
    """Extract (x, y) from an action dict using the same logic as legacy _looks_like_* helpers."""
    element = action.get("element")
    if not isinstance(element, list):
        return None
    if len(element) == 1 and isinstance(element[0], list):
        element = element[0]
    try:
        x = float(element[0])
        y = float(element[1]) if len(element) >= 2 else -1.0
    except (TypeError, ValueError, IndexError):
        return None
    return (x, y)


class TransitionRuleEngine:
    """Validates exploration transitions against schema-derived rules.

    policy values:
      "strict"        — current behavior; unknown pairs rejected
      "schema_guided" — unknown pairs accepted with off_schema=True flag
      "permissive"    — accept everything except high-risk/self-loop/non-nav
    """

    def __init__(self, schema: Any, safety: SafetyPolicy, policy: str = "strict"):
        self.schema = schema
        self.safety = safety
        self.policy = policy

        # Build a set of allowed (source, target) pairs from schema transitions.
        # Key: (source, target), value: target_locator region (or None)
        self._allowed: dict[tuple[str, str], str | None] = {}
        for t in schema.transitions:
            src = str(t.source)
            tgt = str(t.target)
            region = (t.target_locator or {}).get("region") if t.target_locator else None
            # If the same pair appears multiple times with different locators,
            # prefer the one WITH a region (more restrictive is harder to satisfy,
            # but safer). If already stored without region, overwrite with region.
            existing = self._allowed.get((src, tgt))
            if (src, tgt) not in self._allowed or (existing is None and region is not None):
                self._allowed[(src, tgt)] = region

    def rejection_reason(self, source: Any, action: dict[str, Any], target: Any) -> str:
        """Return the rejection reason string, or "" if the transition is accepted."""
        if source is None or target is None:
            return "missing page metadata"

        source_type = _page_type_str(source)
        target_type = _page_type_str(target)

        # High-risk boundary — always rejected
        if source_type in self.safety.high_risk_page_types or target_type in self.safety.high_risk_page_types:
            return "high-risk page boundary"

        # Interference (dialog) source: accept per current behavior.
        # Per Phase 4 plan this becomes evidence-only, but for now (Phase 2) we keep
        # dialog source accepted so existing tests pass.
        interference = set(self.schema.exploration.interference_page_types)
        if source_type in interference:
            return ""

        action_type = str(action.get("action") or action.get("action_type") or "").lower()

        # Non-navigation actions
        if action_type in {"type", "wait"}:
            return "non-navigation action"

        # Self-loop check
        if source_type == target_type:
            self_loop_pages = set(self.schema.exploration.self_loop_input_pages)
            if source_type in self_loop_pages and action_type in {"tap", "type", "type_name", "input"}:
                return ""
            return "self-loop or unchanged screen"

        pair = (source_type, target_type)

        # Check schema-allowed pairs
        if pair in self._allowed:
            region = self._allowed[pair]
            if region is not None:
                predicate = REGIONS.get(region)
                if predicate is not None:
                    coords = _extract_coords(action)
                    if coords is None or not predicate(coords[0], coords[1]):
                        # Special-case the product_detail->cart message for backward compat
                        if pair == ("product_detail", "cart"):
                            return "product_detail->cart must use top cart entry, not bottom add-to-cart CTA"
                        return f"{source_type}->{target_type} requires {region} region"
            return ""

        # Unknown pair handling by policy
        if self.policy == "permissive":
            return ""
        if self.policy == "schema_guided":
            # Accept but caller can check _last_off_schema flag
            return ""

        # strict: reject unknown pairs
        return "unexpected shopping flow transition"

    @staticmethod
    def should_stop_after_rejection(reason: str) -> bool:
        """Return True if the rejection reason warrants stopping exploration."""
        return reason == "high-risk page boundary"
