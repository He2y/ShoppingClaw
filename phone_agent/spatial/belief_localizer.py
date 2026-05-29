"""Multi-signal Bayesian belief localizer for AMSG.

Implements Definition 3 (Observation Model) and Definition 7 (Belief Update)
from FORMALIZATION.md.

The observation model decomposes page similarity into four independent
channels (visual, semantic, structural, temporal) and combines them with
learnable weights.  Channels that are unavailable (e.g. no VLM embedding)
gracefully redistribute their weight to the remaining channels.

The belief update is a proper Bayesian posterior:

    B_t(v) = eta * P(o_t | v) * sum_{v'} P(v | v', a_{t-1}) * B_{t-1}(v')

When ``config.belief_update_method == "fixed"``, the legacy fixed-score
behavior (0.92 graph / 1.0 novel / 0.82 observation) is reproduced exactly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .amsg_config import AMSGOptimConfig


# ── Data structures ─────────────────────────────────────────────────


@dataclass(frozen=True)
class ObservationSignals:
    """Multi-channel observation extracted from a single screenshot."""

    app: str
    page_type: str
    landmarks: tuple[str, ...] = ()
    affordances: tuple[str, ...] = ()
    ui_hash: str = ""
    semantic_signature: str = ""
    visual_embedding: tuple[float, ...] = ()
    semantic_embedding: tuple[float, ...] = ()
    step_index: int = 0


@dataclass(frozen=True)
class BeliefEntry:
    """A single (state, probability) pair in the belief distribution."""

    state_id: str
    probability: float
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "state_id": self.state_id,
            "probability": round(self.probability, 6),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class BeliefDistribution:
    """Probability distribution over page states (Definition 7)."""

    entries: tuple[BeliefEntry, ...] = ()

    @property
    def map_state(self) -> BeliefEntry | None:
        """Maximum a-posteriori state."""
        return self.entries[0] if self.entries else None

    @property
    def entropy(self) -> float:
        """Shannon entropy — feeds into the planner's information-gain term."""
        if not self.entries:
            return 0.0
        return -sum(
            e.probability * math.log(e.probability + 1e-12)
            for e in self.entries
        )

    @property
    def confidence(self) -> float:
        """Probability of the MAP state."""
        return self.entries[0].probability if self.entries else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "entries": [e.to_dict() for e in self.entries],
            "entropy": round(self.entropy, 4),
            "confidence": round(self.confidence, 4),
        }


# ── Similarity helpers ──────────────────────────────────────────────


def cosine_similarity(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """Cosine similarity between two vectors, or 0 if degenerate."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(ai * bi for ai, bi in zip(a, b))
    norm_a = math.sqrt(sum(ai * ai for ai in a))
    norm_b = math.sqrt(sum(bi * bi for bi in b))
    if norm_a < 1e-12 or norm_b < 1e-12:
        return 0.0
    return dot / (norm_a * norm_b)


def structural_similarity(
    obs_app: str,
    obs_page_type: str,
    obs_landmarks: tuple[str, ...] | set[str],
    obs_affordances: tuple[str, ...] | set[str],
    cand_app: str,
    cand_page_type: str,
    cand_landmarks: tuple[str, ...] | set[str],
    cand_affordances: tuple[str, ...] | set[str],
) -> float:
    """Weighted structural similarity — wraps the legacy _page_similarity logic.

    Weights: 0.35 app + 0.35 page_type + 0.20 landmark Jaccard + 0.10 affordance Jaccard
    """
    score = 0.0
    if obs_app and obs_app == cand_app:
        score += 0.35
    if obs_page_type and obs_page_type == cand_page_type:
        score += 0.35
    obs_l = set(obs_landmarks)
    cand_l = set(cand_landmarks)
    if obs_l or cand_l:
        score += 0.20 * (len(obs_l & cand_l) / max(1, len(obs_l | cand_l)))
    obs_a = set(obs_affordances)
    cand_a = set(cand_affordances)
    if obs_a or cand_a:
        score += 0.10 * (len(obs_a & cand_a) / max(1, len(obs_a | cand_a)))
    return score


# ── Multi-signal localizer ──────────────────────────────────────────


class MultiSignalLocalizer:
    """Bayesian belief maintenance with 4-channel observation model.

    Channel weights are ``config.belief_signal_weights`` in the order:
    (visual, semantic, structural, temporal).
    """

    def __init__(self, config: AMSGOptimConfig | None = None) -> None:
        self.config = config or AMSGOptimConfig.legacy()
        self._transition_counts: dict[tuple[str, str], dict[str, int]] = {}
        self._prior: dict[str, float] = {}
        self._last_state_id: str = ""
        self._last_action: str = ""

    # ── Observation likelihood P(o|s) ───────────────────────────────

    def observation_likelihood(self, signals: ObservationSignals, candidate: Any) -> float:
        """Compute P(o | s) as weighted sum of channel similarities (Definition 3)."""
        w = self.config.belief_signal_weights
        channels: list[float | None] = [
            self._visual_similarity(signals, candidate),
            self._semantic_similarity(signals, candidate),
            self._structural_similarity(signals, candidate),
            self._temporal_transition_prob(signals, candidate),
        ]
        # Redistribute weight from unavailable channels.
        available = [
            (w[i], ch) for i, ch in enumerate(channels)
            if ch is not None
        ]
        if not available:
            return 0.0
        total_w = sum(wi for wi, _ in available)
        if total_w < 1e-12:
            return 0.0
        return sum((wi / total_w) * ci for wi, ci in available)

    # ── Bayesian update (Definition 7) ──────────────────────────────

    def update(self, signals: ObservationSignals, candidates: list[Any]) -> BeliefDistribution:
        """Compute posterior belief distribution given observation signals."""
        if self.config.belief_update_method == "fixed":
            return self._legacy_update(signals, candidates)

        posteriors: list[tuple[str, float, str]] = []
        for candidate in candidates:
            state_id = candidate.state_id if hasattr(candidate, "state_id") else str(candidate)
            likelihood = self.observation_likelihood(signals, candidate)
            prior = self._prior.get(state_id, 1.0 / max(1, len(candidates)))
            posteriors.append((state_id, likelihood * prior, "bayesian_update"))

        total = sum(p for _, p, _ in posteriors) or 1.0
        entries = tuple(
            BeliefEntry(sid, p / total, reason)
            for sid, p, reason in sorted(posteriors, key=lambda x: x[1], reverse=True)
        )

        # Update prior for next step.
        self._prior = {e.state_id: e.probability for e in entries}
        return BeliefDistribution(entries=entries)

    def record_transition(self, source_state_id: str, action: str, target_state_id: str) -> None:
        """Feed observation into the frequency-estimated transition model (Definition 4)."""
        key = (source_state_id, action)
        counts = self._transition_counts.setdefault(key, {})
        counts[target_state_id] = counts.get(target_state_id, 0) + 1
        self._last_state_id = source_state_id
        self._last_action = action

    # ── Channel implementations ─────────────────────────────────────

    def _visual_similarity(self, signals: ObservationSignals, candidate: Any) -> float | None:
        """Channel 1: Cosine similarity of VLM screenshot embeddings."""
        obs_emb = signals.visual_embedding
        cand_emb = getattr(candidate, "visual_embedding", ()) or ()
        if not obs_emb or not cand_emb:
            return None
        return max(0.0, cosine_similarity(obs_emb, cand_emb))

    def _semantic_similarity(self, signals: ObservationSignals, candidate: Any) -> float | None:
        """Channel 2: Cosine similarity of text semantic embeddings."""
        obs_emb = signals.semantic_embedding
        cand_emb = getattr(candidate, "semantic_embedding", ()) or ()
        if not obs_emb or not cand_emb:
            return None
        return max(0.0, cosine_similarity(obs_emb, cand_emb))

    def _structural_similarity(self, signals: ObservationSignals, candidate: Any) -> float | None:
        """Channel 3: Wraps legacy _page_similarity (always available)."""
        cand_app = getattr(candidate, "app", "")
        cand_page_type = getattr(candidate, "page_type", "")
        cand_landmarks = getattr(candidate, "landmarks", ())
        cand_affordances = getattr(candidate, "affordances", ())
        return structural_similarity(
            signals.app,
            signals.page_type,
            signals.landmarks,
            signals.affordances,
            cand_app,
            cand_page_type,
            cand_landmarks,
            cand_affordances,
        )

    def _temporal_transition_prob(self, signals: ObservationSignals, candidate: Any) -> float | None:
        """Channel 4: Frequency-estimated P(s | s_prev, a_prev) (Definition 4)."""
        if not self._last_state_id or not self._last_action:
            return None
        key = (self._last_state_id, self._last_action)
        counts = self._transition_counts.get(key)
        if not counts:
            return None
        state_id = candidate.state_id if hasattr(candidate, "state_id") else str(candidate)
        total = sum(counts.values())
        return counts.get(state_id, 0) / total if total > 0 else 0.0

    # ── Legacy fallback ─────────────────────────────────────────────

    def _legacy_update(self, signals: ObservationSignals, candidates: list[Any]) -> BeliefDistribution:
        """Reproduce the exact pre-optimization fixed-score behavior."""
        if not candidates:
            return BeliefDistribution()
        entries = tuple(
            BeliefEntry(
                state_id=c.state_id if hasattr(c, "state_id") else str(c),
                probability=1.0 / len(candidates),
                reason="legacy_fixed",
            )
            for c in candidates
        )
        return BeliefDistribution(entries=entries)
