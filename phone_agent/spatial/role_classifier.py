"""Pluggable role classification for AMSG functionality discovery.

Implements Definition 6 (Functionality Discovery) from FORMALIZATION.md.

Two implementations:

1. ``KeywordRoleClassifier`` — wraps the existing if-else chains in
   ``functionality.py`` verbatim.  Zero behavior change from legacy.

2. ``EmbeddingRoleClassifier`` — prototype-based classification using
   sentence embeddings.  Prototypes grow online as postcondition
   verification confirms roles.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class RoleClassifier(Protocol):
    """Protocol for functionality role classification (Definition 6)."""

    def classify(self, page_type: str, label: str, description: str) -> tuple[str, str]:
        """Classify a UI element into (canonical_role, item_type).

        Returns:
            (role, type) where role is the canonical action name (e.g.
            "open_search", "open_product_detail") and type is "functionality"
            or "data".
        """
        ...


class KeywordRoleClassifier:
    """Wraps the existing keyword-based classification logic.

    Delegates to ``functionality.classify_functionality_type`` and
    ``functionality.canonical_role_from_element`` for exact legacy behavior.
    """

    def classify(self, page_type: str, label: str, description: str) -> tuple[str, str]:
        from .functionality import canonical_role_from_element, classify_functionality_type

        item_type = classify_functionality_type(label, description)
        role = canonical_role_from_element(page_type, label, description, item_type=item_type)
        return (role, item_type)


def _cosine_sim(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(ai * bi for ai, bi in zip(a, b))
    norm_a = math.sqrt(sum(ai * ai for ai in a))
    norm_b = math.sqrt(sum(bi * bi for bi in b))
    if norm_a < 1e-12 or norm_b < 1e-12:
        return 0.0
    return dot / (norm_a * norm_b)


@dataclass
class EmbeddingRoleClassifier:
    """Prototype-based role classification using sentence embeddings.

    Each role is represented by one or more prototype embeddings.  New
    prototypes are added when postcondition verification confirms a role,
    enabling online learning without gradient descent.

    The embedding function is injected at construction time so the
    classifier is model-agnostic (works with any sentence encoder).
    """

    embedding_fn: Any  # Callable[[str], tuple[float, ...]]
    role_threshold: float = 0.5
    type_threshold: float = 0.6
    _prototypes: dict[str, list[tuple[float, ...]]] = field(default_factory=dict)
    _fallback: KeywordRoleClassifier = field(default_factory=KeywordRoleClassifier)

    def classify(self, page_type: str, label: str, description: str) -> tuple[str, str]:
        """Classify using embedding similarity to known role prototypes.

        Falls back to keyword classifier when no prototypes are available
        or similarity is below threshold.
        """
        if not self._prototypes:
            return self._fallback.classify(page_type, label, description)

        text = f"{page_type} {label} {description}"
        try:
            emb = self.embedding_fn(text)
        except Exception:
            return self._fallback.classify(page_type, label, description)

        if not emb:
            return self._fallback.classify(page_type, label, description)

        best_role = ""
        best_sim = 0.0
        for role, protos in self._prototypes.items():
            for proto in protos:
                sim = _cosine_sim(emb, proto)
                if sim > best_sim:
                    best_role = role
                    best_sim = sim

        if best_sim >= self.role_threshold:
            item_type = "functionality" if best_sim >= self.type_threshold else "data"
            return (best_role, item_type)

        # Below threshold — fall back to keyword classifier
        return self._fallback.classify(page_type, label, description)

    def register_verified_role(self, role: str, embedding: tuple[float, ...]) -> None:
        """Promote a verified postcondition into the prototype set.

        Called by the edge lifecycle system when a transition is promoted,
        so the classifier learns from real device interactions.
        """
        if not role or not embedding:
            return
        protos = self._prototypes.setdefault(role, [])
        # Deduplicate near-identical prototypes
        for existing in protos:
            if _cosine_sim(embedding, existing) > 0.95:
                return
        protos.append(embedding)

    def prototype_count(self) -> dict[str, int]:
        """Return number of prototypes per role."""
        return {role: len(protos) for role, protos in self._prototypes.items()}
