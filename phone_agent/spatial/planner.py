"""Belief-guided route planning for AMSG."""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field

from .core import AffordanceEdge, BeliefState


@dataclass(frozen=True)
class SpatialRoutePlan:
    mode: str
    route: tuple[AffordanceEdge, ...] = ()
    total_cost: float = 0.0
    confidence: float = 0.0
    risk_summary: str = ""

    @property
    def next_edge(self) -> AffordanceEdge | None:
        return self.route[0] if self.route else None

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "route": [edge.to_dict() for edge in self.route],
            "total_cost": self.total_cost,
            "confidence": self.confidence,
            "risk_summary": self.risk_summary,
        }


@dataclass
class SpatialPlanner:
    edges_by_source: dict[str, list[AffordanceEdge]] = field(default_factory=dict)

    def plan(self, belief: BeliefState, target_page_types: set[str]) -> SpatialRoutePlan:
        if not belief.top:
            return SpatialRoutePlan(mode="explore", risk_summary="no belief")
        if belief.top.page_type in target_page_types:
            return SpatialRoutePlan(mode="goal_reached", confidence=belief.confidence)
        path = self._shortest_path(belief.current_node_id, target_page_types)
        if not path:
            return SpatialRoutePlan(mode="explore", confidence=belief.confidence, risk_summary="no route")
        total_cost = sum(edge.weighted_cost for edge in path)
        risk_summary = "high risk route" if any(edge.risk_level == "high" for edge in path) else "normal route"
        return SpatialRoutePlan(
            mode="navigate",
            route=tuple(path),
            total_cost=total_cost,
            confidence=max(0.0, min(1.0, belief.confidence / max(total_cost, 1.0))),
            risk_summary=risk_summary,
        )

    def _shortest_path(self, start_node: str, target_page_types: set[str]) -> list[AffordanceEdge]:
        queue: list[tuple[float, int, str, list[AffordanceEdge]]] = [(0.0, 0, start_node, [])]
        best_cost = {start_node: 0.0}
        visited: set[str] = set()
        counter = 0
        while queue:
            cost, _, node_id, path = heapq.heappop(queue)
            if node_id in visited:
                continue
            visited.add(node_id)
            for edge in self.edges_by_source.get(node_id, []):
                if edge.expected_postcondition in target_page_types:
                    return path + [edge]
                next_cost = cost + edge.weighted_cost
                if next_cost >= best_cost.get(edge.target_node, float("inf")):
                    continue
                best_cost[edge.target_node] = next_cost
                counter += 1
                heapq.heappush(queue, (next_cost, counter, edge.target_node, path + [edge]))
        return []
