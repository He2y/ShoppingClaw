"""Three-signal page confidence assessment (Phase 4.5b)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PageConfidence:
    """Confidence assessment for a page classification.

    Attributes:
        level: "confident" | "low_confidence" | "transient"
        signals: Dict mapping signal name → assessment value (for diagnostics).
    """

    level: str
    signals: dict[str, str]


def _landmark_check(page_info: Any, schema: Any) -> str:
    """Check if the page's structural elements match the expected landmarks.

    Returns:
        "ok"       — landmarks present (or no landmarks defined in spec).
        "mismatch" — landmarks defined but none found in elements.
        "abstain"  — elements dict is empty (fast-mode classification).
    """
    if page_info is None or schema is None:
        return "abstain"

    page_type = _page_type_str(page_info)
    spec = schema.page_spec(page_type)
    if spec is None:
        return "abstain"

    landmarks = getattr(spec, "landmarks", ())
    if not landmarks:
        return "ok"  # No landmark constraints defined

    elements = getattr(page_info, "elements", {}) or {}
    if not elements:
        return "abstain"  # Fast mode — no elements extracted

    # Build searchable text from elements dict: keys + string values
    element_text_parts: list[str] = []
    for key, value in elements.items():
        element_text_parts.append(str(key).lower())
        if isinstance(value, str):
            element_text_parts.append(value.lower())

    element_text = " ".join(element_text_parts)

    # For each landmark token, split on "_" and check component words
    for landmark in landmarks:
        # e.g. "search_bar" → tokens ["search", "bar"]
        parts = landmark.lower().replace("-", "_").split("_")
        if all(part in element_text for part in parts):
            return "ok"
        # Also try the raw landmark text (handles Chinese and unsplit names)
        if landmark.lower() in element_text:
            return "ok"

    # None of the landmarks found
    return "mismatch"


def assess_page_confidence(
    page_info: Any,
    schema: Any,
    *,
    reasoning_inferred: str | None,
    stable: bool,
) -> PageConfidence:
    """Assess confidence in a page classification using three signals.

    Signal ①: VLM classifier page_type (from page_info.page_type).
    Signal ②: Reasoning-inferred type (from page_evidence inference; None = abstain).
    Signal ③: Landmark structural check (schema.page_spec(page_type).landmarks vs
               page_info.elements).

    Rules:
      - transient:      not stable (loading frame / animation)
      - low_confidence: reasoning type conflicts with classifier (and neither is
                        an alias of the other per schema.page_type_matches), OR
                        landmark check returns "mismatch"
      - confident:      all signals agree (or abstain)

    Args:
        page_info:          PageInfo instance to assess.
        schema:             MobileSchema instance (for landmark specs and aliases).
        reasoning_inferred: Page type string inferred from VLM reasoning, or None.
        stable:             Whether the screen was stable before classification.

    Returns:
        PageConfidence with level and diagnostic signals dict.
    """
    classifier_type = _page_type_str(page_info)
    signals: dict[str, str] = {
        "classifier": classifier_type,
        "reasoning": reasoning_inferred or "abstain",
        "landmark": "pending",
    }

    # Signal ③: landmark check
    landmark_signal = _landmark_check(page_info, schema)
    signals["landmark"] = landmark_signal

    # Transient always overrides everything
    if not stable:
        return PageConfidence(level="transient", signals=signals)

    # Signal ② conflict check
    reasoning_conflicts = False
    if reasoning_inferred is not None and reasoning_inferred != classifier_type:
        # Check if they are aliases of each other
        if schema is not None and hasattr(schema, "page_type_matches"):
            is_alias = schema.page_type_matches(classifier_type, reasoning_inferred)
        else:
            is_alias = False
        if not is_alias:
            reasoning_conflicts = True

    if reasoning_conflicts or landmark_signal == "mismatch":
        return PageConfidence(level="low_confidence", signals=signals)

    return PageConfidence(level="confident", signals=signals)


def _page_type_str(page_info: Any) -> str:
    """Extract page type string from PageInfo."""
    if page_info is None:
        return ""
    pt = getattr(page_info, "page_type", "")
    if hasattr(pt, "value"):
        return str(pt.value)
    return str(pt)
