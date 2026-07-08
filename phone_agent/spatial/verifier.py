"""Postcondition verification and repair policy for AMSG."""

from __future__ import annotations

from dataclasses import dataclass, field

from .core import BeliefState, VerificationResult


HIGH_RISK_PAGE_TYPES = {"payment", "address", "login", "permission", "confirmation"}


@dataclass
class NegativeEdgeMemory:
    failures: dict[str, int] = field(default_factory=dict)

    def mark_failed(self, edge_id: str) -> None:
        if not edge_id:
            return
        self.failures[edge_id] = self.failures.get(edge_id, 0) + 1

    def failure_count(self, edge_id: str) -> int:
        return self.failures.get(edge_id, 0)


class PostconditionVerifier:
    def __init__(self, negative_memory: NegativeEdgeMemory | None = None):
        self.negative_memory = negative_memory or NegativeEdgeMemory()

    def verify(self, belief: BeliefState, expected_postcondition: str, source_edge_id: str = "") -> VerificationResult:
        observed = {candidate.node.page_type for candidate in belief.candidates}
        if not expected_postcondition:
            return VerificationResult(ok=True, reason="no expected postcondition", repair_action="continue", confidence=belief.confidence)
        if expected_postcondition in observed:
            return VerificationResult(ok=True, reason="postcondition matched", repair_action="continue", confidence=belief.confidence)
        if observed & HIGH_RISK_PAGE_TYPES:
            self.negative_memory.mark_failed(source_edge_id)
            return VerificationResult(ok=False, reason="unexpected high-risk page", repair_action="ask_user", confidence=0.9)
        self.negative_memory.mark_failed(source_edge_id)
        if self.negative_memory.failure_count(source_edge_id) >= 2:
            return VerificationResult(ok=False, reason="repeated postcondition mismatch", repair_action="rollback", confidence=0.8)
        return VerificationResult(ok=False, reason="postcondition mismatch", repair_action="replan", confidence=0.6)
