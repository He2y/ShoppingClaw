"""Enhanced route planner for AMSG with A* and belief-aware planning.

Implements Definition 5 (Planning Objective) from FORMALIZATION.md.

Three backends are provided for ablation:

1. ``dijkstra`` — exact reproduction of the legacy ``_shortest_path``
2. ``astar`` — Dijkstra + admissible schema-distance heuristic
3. ``belief_astar`` — A* + temporal decay + exploration bonus + outcome entropy

The planner is stateful: it tracks visit counts and last-traversal steps
to compute temporal decay and exploration bonuses across agent steps.
"""

from __future__ import annotations

import heapq
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .amsg_config import AMSGOptimConfig


@dataclass(frozen=True)
class PlanStep:
    """A single step in a planned route."""

    source_id: str
    target_id: str
    edge: Any  # TransitionEdge
    cost: float


class EnhancedPlanner:
    """Multi-backend route planner with schema heuristic and belief awareness.

    Accepts the same edge interface as ``SpatialGraphMemory._shortest_path``
    but adds three algorithmic innovations:

    - **Admissible heuristic** from schema BFS distances (A*)
    - **Temporal decay** penalizing stale edges
    - **Exploration bonus** rewarding under-visited states
    - **Outcome entropy penalty** penalizing unpredictable transitions
    """

    def __init__(
        self,
        config: AMSGOptimConfig | None = None,
        schema: Any | None = None,
    ) -> None:
        self.config = config or AMSGOptimConfig.legacy()
        self._schema_distance: dict[str, dict[str, int]] = {}
        self._visit_counts: dict[str, int] = {}
        self._last_traversal: dict[str, int] = {}
        self._step_counter: int = 0
        if schema is not None:
            self._precompute_schema_heuristic(schema)

    # ── Schema heuristic precomputation ─────────────────────────────

    def _precompute_schema_heuristic(self, schema: Any) -> None:
        """BFS on schema transitions to compute min hop distances between page types."""
        adjacency: dict[str, set[str]] = {}
        for transition in getattr(schema, "transitions", ()):
            source = transition.source if hasattr(transition, "source") else str(transition.get("source", ""))
            target = transition.target if hasattr(transition, "target") else str(transition.get("target", ""))
            if source and target:
                adjacency.setdefault(source, set()).add(target)

        all_types = set(adjacency.keys())
        for targets in adjacency.values():
            all_types.update(targets)

        for goal_type in all_types:
            # Reverse BFS from goal_type
            dist: dict[str, int] = {goal_type: 0}
            queue: deque[str] = deque([goal_type])
            while queue:
                current = queue.popleft()
                for source, targets in adjacency.items():
                    if current in targets and source not in dist:
                        dist[source] = dist[current] + 1
                        queue.append(source)
            self._schema_distance[goal_type] = dist

    def schema_heuristic(self, page_type: str, target_page_types: set[str]) -> float:
        """Admissible heuristic h(s): min schema distance to any target."""
        if not self._schema_distance or not target_page_types:
            return 0.0
        best = float("inf")
        for goal in target_page_types:
            dist_map = self._schema_distance.get(goal, {})
            d = dist_map.get(page_type, float("inf"))
            if d < best:
                best = d
        return best if best < float("inf") else 0.0

    # ── State tracking ──────────────────────────────────────────────

    def record_visit(self, state_id: str) -> None:
        """Increment visit count for a state."""
        self._visit_counts[state_id] = self._visit_counts.get(state_id, 0) + 1

    def record_traversal(self, edge_key: str) -> None:
        """Record the step at which an edge was last traversed."""
        self._last_traversal[edge_key] = self._step_counter

    def advance_step(self) -> None:
        """Increment global step counter."""
        self._step_counter += 1

    # ── Enhanced cost (Definition 5) ────────────────────────────────

    def enhanced_cost(
        self,
        edge: Any,
        target_state: Any | None = None,
        belief_entropy: float = 0.0,
        outcome_entropy: float = 0.0,
    ) -> float:
        """Compute C'(e) with temporal decay, exploration bonus, and outcome entropy."""
        base = edge.weighted_cost

        # Temporal staleness
        edge_key = f"{edge.source_id}|{edge.action_type}|{edge.action_target}|{edge.postcondition}"
        last = self._last_traversal.get(edge_key, 0)
        delta = self._step_counter - last
        halflife = max(1, self.config.temporal_decay_halflife)
        staleness = 1.0 - math.exp(-delta / halflife) if delta > 0 else 0.0

        # Exploration bonus (UCB-style)
        target_id = edge.target_id if hasattr(edge, "target_id") else ""
        visits = self._visit_counts.get(target_id, 0)
        exploration_bonus = self.config.exploration_bonus_weight / math.sqrt(1 + visits)

        # Information gain: high belief entropy → prefer edges that reduce uncertainty
        info_gain = self.config.information_gain_weight * belief_entropy * staleness

        # Outcome entropy penalty: unpredictable transitions cost more
        entropy_penalty = outcome_entropy * 0.5

        return base + 0.5 * staleness - exploration_bonus - info_gain + entropy_penalty

    # ── Main dispatch ───────────────────────────────────────────────

    def plan(
        self,
        start_id: str,
        target_page_types: set[str],
        load_edges_fn: Any,
        get_state_fn: Any,
        *,
        belief_entropy: float = 0.0,
        outcome_entropy_fn: Any | None = None,
    ) -> list[Any]:
        """Route planning dispatch to the configured backend.

        Args:
            start_id: Starting state ID
            target_page_types: Set of goal page types
            load_edges_fn: Callable(state_id) -> list[TransitionEdge]
            get_state_fn: Callable(state_id) -> PageState | None
            belief_entropy: Current belief distribution entropy
            outcome_entropy_fn: Callable(source_page_type, action_key) -> float
        """
        backend = self.config.planner_backend
        if backend == "astar":
            return self._astar(start_id, target_page_types, load_edges_fn, get_state_fn)
        if backend == "belief_astar":
            return self._belief_astar(
                start_id, target_page_types, load_edges_fn, get_state_fn,
                belief_entropy=belief_entropy,
                outcome_entropy_fn=outcome_entropy_fn,
            )
        return self._dijkstra(start_id, target_page_types, load_edges_fn, get_state_fn)

    # ── Backend 1: Dijkstra (legacy reproduction) ───────────────────

    def _dijkstra(
        self,
        start_id: str,
        target_page_types: set[str],
        load_edges_fn: Any,
        get_state_fn: Any,
    ) -> list[Any]:
        """Exact reproduction of legacy _shortest_path."""
        counter = 0
        queue: list[tuple[float, int, str, list[Any]]] = [(0.0, counter, start_id, [])]
        best_cost: dict[str, float] = {start_id: 0.0}
        visited: set[str] = set()

        while queue:
            cost, _, state_id, path = heapq.heappop(queue)
            if state_id in visited:
                continue
            visited.add(state_id)

            state = get_state_fn(state_id)
            if state and getattr(state, "page_type", "") in target_page_types and path:
                return path

            for edge in load_edges_fn(state_id):
                if edge.postcondition in target_page_types:
                    return path + [edge]
                next_cost = cost + edge.weighted_cost
                if next_cost >= best_cost.get(edge.target_id, float("inf")):
                    continue
                best_cost[edge.target_id] = next_cost
                counter += 1
                heapq.heappush(queue, (next_cost, counter, edge.target_id, path + [edge]))

        return []

    # ── Backend 2: A* with schema heuristic ─────────────────────────

    def _astar(
        self,
        start_id: str,
        target_page_types: set[str],
        load_edges_fn: Any,
        get_state_fn: Any,
    ) -> list[Any]:
        """A* with admissible schema-distance heuristic."""
        counter = 0
        start_state = get_state_fn(start_id)
        start_h = self.schema_heuristic(
            getattr(start_state, "page_type", "") if start_state else "",
            target_page_types,
        )
        # Priority queue: (f=g+h, counter, state_id, g_cost, path)
        queue: list[tuple[float, int, str, float, list[Any]]] = [
            (start_h, counter, start_id, 0.0, [])
        ]
        best_cost: dict[str, float] = {start_id: 0.0}
        visited: set[str] = set()

        while queue:
            _, _, state_id, g_cost, path = heapq.heappop(queue)
            if state_id in visited:
                continue
            visited.add(state_id)

            state = get_state_fn(state_id)
            if state and getattr(state, "page_type", "") in target_page_types and path:
                return path

            for edge in load_edges_fn(state_id):
                if edge.postcondition in target_page_types:
                    return path + [edge]
                next_g = g_cost + edge.weighted_cost
                if next_g >= best_cost.get(edge.target_id, float("inf")):
                    continue
                best_cost[edge.target_id] = next_g
                target_state = get_state_fn(edge.target_id)
                h = self.schema_heuristic(
                    getattr(target_state, "page_type", edge.postcondition) if target_state else edge.postcondition,
                    target_page_types,
                )
                counter += 1
                heapq.heappush(queue, (next_g + h, counter, edge.target_id, next_g, path + [edge]))

        return []

    # ── Backend 3: Belief-aware A* ──────────────────────────────────

    def _belief_astar(
        self,
        start_id: str,
        target_page_types: set[str],
        load_edges_fn: Any,
        get_state_fn: Any,
        *,
        belief_entropy: float = 0.0,
        outcome_entropy_fn: Any | None = None,
    ) -> list[Any]:
        """A* + temporal decay + exploration bonus + outcome entropy (Definition 5)."""
        counter = 0
        start_state = get_state_fn(start_id)
        start_h = self.schema_heuristic(
            getattr(start_state, "page_type", "") if start_state else "",
            target_page_types,
        )
        queue: list[tuple[float, int, str, float, list[Any]]] = [
            (start_h, counter, start_id, 0.0, [])
        ]
        best_cost: dict[str, float] = {start_id: 0.0}
        visited: set[str] = set()

        while queue:
            _, _, state_id, g_cost, path = heapq.heappop(queue)
            if state_id in visited:
                continue
            visited.add(state_id)

            state = get_state_fn(state_id)
            if state and getattr(state, "page_type", "") in target_page_types and path:
                return path

            for edge in load_edges_fn(state_id):
                if edge.postcondition in target_page_types:
                    return path + [edge]

                # Compute outcome entropy for this specific transition
                oe = 0.0
                if outcome_entropy_fn is not None:
                    source_state = get_state_fn(state_id)
                    source_pt = getattr(source_state, "page_type", "") if source_state else ""
                    action_key = f"{edge.action_type}:{edge.action_target}"
                    oe = outcome_entropy_fn(source_pt, action_key)

                edge_cost = self.enhanced_cost(
                    edge,
                    belief_entropy=belief_entropy,
                    outcome_entropy=oe,
                )
                next_g = g_cost + edge_cost
                if next_g >= best_cost.get(edge.target_id, float("inf")):
                    continue
                best_cost[edge.target_id] = next_g
                target_state = get_state_fn(edge.target_id)
                h = self.schema_heuristic(
                    getattr(target_state, "page_type", edge.postcondition) if target_state else edge.postcondition,
                    target_page_types,
                )
                counter += 1
                heapq.heappush(queue, (next_g + h, counter, edge.target_id, next_g, path + [edge]))

        return []
