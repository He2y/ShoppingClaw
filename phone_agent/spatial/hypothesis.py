"""Edge hypothesis generation for active spatial graph construction."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .core import PageNode, stable_id
from .schema_registry import MobileSchema, TransitionSpec, get_default_registry


@dataclass(frozen=True)
class EdgeHypothesis:
    hypothesis_id: str
    source_node: str
    source_page_type: str
    expected_page_type: str
    intent: str
    semantic_target: str = ""
    target_locator: dict[str, Any] = field(default_factory=dict)
    risk: str = "normal"
    novelty: float = 1.0
    uncertainty: float = 1.0
    goal_relevance: float = 0.5
    cost: float = 1.0
    repeat_failure: float = 0.0

    @classmethod
    def from_transition(cls, node: PageNode, transition: TransitionSpec, *, goal_page_types: set[str] | None = None) -> "EdgeHypothesis":
        goal_page_types = goal_page_types or set()
        target_relevance = 1.0 if transition.target in goal_page_types else 0.5
        return cls(
            hypothesis_id=stable_id("hyp", node.node_id, transition.intent, transition.target),
            source_node=node.node_id,
            source_page_type=node.page_type,
            expected_page_type=transition.target,
            intent=transition.intent,
            semantic_target=transition.affordance or transition.intent,
            target_locator=dict(transition.target_locator),
            risk=transition.risk or node.risk_level,
            goal_relevance=target_relevance,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "source_node": self.source_node,
            "source_page_type": self.source_page_type,
            "expected_page_type": self.expected_page_type,
            "intent": self.intent,
            "semantic_target": self.semantic_target,
            "target_locator": dict(self.target_locator),
            "risk": self.risk,
            "novelty": self.novelty,
            "uncertainty": self.uncertainty,
            "goal_relevance": self.goal_relevance,
            "cost": self.cost,
            "repeat_failure": self.repeat_failure,
        }


class EdgeHypothesisGenerator:
    def __init__(self, schema_name: str = "shopping", schema: MobileSchema | None = None):
        self.registry = get_default_registry()
        self.schema = schema or self.registry.merged(schema_name)

    def generate(self, node: PageNode, goal_page_types: set[str] | None = None) -> list[EdgeHypothesis]:
        source_types = [node.page_type]
        spec = self.schema.page_spec(node.page_type)
        if spec:
            source_types.extend(spec.aliases)
        hypotheses: list[EdgeHypothesis] = []
        for source_type in dict.fromkeys(source_types):
            for transition in self.schema.outgoing(source_type):
                if transition.affordance and not self._affordance_available(node, transition.affordance):
                    continue
                hypotheses.append(EdgeHypothesis.from_transition(node, transition, goal_page_types=goal_page_types))
        return hypotheses

    @staticmethod
    def _affordance_available(node: PageNode, affordance: str) -> bool:
        if not affordance:
            return True
        candidates = set(node.affordances) | set(node.landmarks)
        if affordance in candidates:
            return True
        # Domain schemas often use a more specific name than the runtime page
        # extractor. Keep this fuzzy but deterministic.
        return any(affordance in value or value in affordance for value in candidates)
