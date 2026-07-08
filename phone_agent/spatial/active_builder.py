"""Active frontier scoring for AMSG graph construction."""

from __future__ import annotations

from dataclasses import dataclass

from .hypothesis import EdgeHypothesis


@dataclass(frozen=True)
class FrontierWeights:
    novelty: float = 1.0
    uncertainty: float = 1.0
    goal_relevance: float = 1.0
    risk: float = 1.2
    repeat_failure: float = 1.0
    cost: float = 0.4


@dataclass(frozen=True)
class ScoredHypothesis:
    hypothesis: EdgeHypothesis
    score: float

    def to_dict(self) -> dict:
        data = self.hypothesis.to_dict()
        data["score"] = self.score
        return data


class ActiveGraphBuilder:
    """Ranks edge hypotheses by expected graph-construction value."""

    def __init__(self, weights: FrontierWeights | None = None):
        self.weights = weights or FrontierWeights()

    def score(self, hypothesis: EdgeHypothesis) -> float:
        risk_value = {"normal": 0.0, "medium": 0.5, "high": 1.0}.get(hypothesis.risk, 0.5)
        return (
            self.weights.novelty * hypothesis.novelty
            + self.weights.uncertainty * hypothesis.uncertainty
            + self.weights.goal_relevance * hypothesis.goal_relevance
            - self.weights.risk * risk_value
            - self.weights.repeat_failure * hypothesis.repeat_failure
            - self.weights.cost * hypothesis.cost
        )

    def rank(self, hypotheses: list[EdgeHypothesis]) -> list[ScoredHypothesis]:
        ranked = [ScoredHypothesis(item, self.score(item)) for item in hypotheses]
        return sorted(ranked, key=lambda item: item.score, reverse=True)

    def choose_next(self, hypotheses: list[EdgeHypothesis]) -> ScoredHypothesis | None:
        ranked = self.rank(hypotheses)
        return ranked[0] if ranked else None
