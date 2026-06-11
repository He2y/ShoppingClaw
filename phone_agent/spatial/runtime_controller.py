"""AMSG v4 runtime controller for graph-guided mobile navigation.

The controller owns the runtime graph contract. It reads only the v4
UIState/Action/Functionality schema and leaves personalized memory/session
bookkeeping in MemoryManager.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Any

from phone_agent.memory.spatial_graph_memory import GoalSpec, PageBelief, PageState, RuntimeDAG

_AMSG_DOMAIN_PRIORS_ENABLED = os.getenv("AMSG_DOMAIN_PRIORS", "1") not in ("0", "false", "False")

V4_GRAPH_CONTRACT_VERSION = "amsg-v4-runtime"
DEFAULT_RUNTIME_GRAPH_DATABASE = "shopping-spatial-v4"
HIGH_RISK_PAGE_TYPES = {"payment", "address", "login"}
VLM_VERIFY_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("search_result", "product_detail"),
        ("product_detail", "spec_selection"),
    }
)


@dataclass
class RuntimeObservation:
    ui_hash: str
    semantic_layout: str
    task: str
    screen_dict: dict[str, Any] = field(default_factory=dict)

    @property
    def page_type(self) -> str:
        return str(self.screen_dict.get("page_type") or "")

    @property
    def app(self) -> str:
        return str(
            self.screen_dict.get("app")
            or self.screen_dict.get("artifact_app")
            or ""
        )


class GraphRuntimeController:
    """V4-only runtime coordinator for spatial graph navigation."""

    def __init__(
        self,
        manager: Any,
        graph_store: Any,
        spatial_graph_memory: Any,
        *,
        verbose: bool = True,
        legacy_fallback: bool = False,
    ) -> None:
        self.manager = manager
        self.graph_store = graph_store
        self.spatial_graph_memory = spatial_graph_memory
        self.verbose = verbose
        self.legacy_fallback = legacy_fallback
        self.contract_version = V4_GRAPH_CONTRACT_VERSION
        self.database = getattr(graph_store, "database", "") or runtime_graph_database()
        self._domain_prior_provider: Any = None  # lazy; set on first explore-mode call
        if verbose:
            print(
                "[GraphRuntime] "
                f"database={self.database} "
                f"contract={self.contract_version} "
                f"legacy_fallback={self.legacy_fallback}"
            )

    def should_use_page_classifier(self, step: int = 0, current_app: str = "") -> bool:
        dag = self.manager._runtime_dag
        if not dag or not dag.is_usable:
            self._metric("runtime_dag_misses")
            return True
        if self.manager._pending_expected_postcondition:
            self._metric("runtime_dag_misses")
            return True
        next_edge = dag.next_edge()
        if not next_edge:
            self._metric("runtime_dag_hits")
            return False
        if next_edge.risk == "high" or next_edge.postcondition in HIGH_RISK_PAGE_TYPES:
            self._metric("runtime_dag_misses")
            return True
        if current_app and dag.app and current_app != dag.app:
            self._metric("runtime_dag_misses")
            return True
        self._metric("runtime_dag_hits")
        return False

    def record_page_classifier_decision(self, used: bool) -> None:
        self._metric("page_classifier_calls" if used else "page_classifier_skips")

    def get_runtime_metrics(self) -> dict[str, int]:
        metrics = dict(getattr(self.manager, "_runtime_metrics", {}))
        dag = getattr(self.manager, "_runtime_dag", None)
        metrics["active_runtime_dag"] = 1 if dag and dag.is_usable else 0
        return metrics

    def runtime_screen_hint(self, current_app: str = "") -> dict[str, Any]:
        dag = self.manager._runtime_dag
        if not dag:
            return {}
        node = dag.nodes.get(dag.current_node_id)
        if not node:
            return {}
        return {
            "app": current_app or node.app,
            "page_type": node.page_type,
            "summary": node.summary,
            "semantic_layout": f"{current_app or node.app} {node.page_type}",
            "elements": None,
        }

    def mark_planned_action_executed(self, action: dict[str, Any], success: bool = True) -> None:
        dag = self.manager._runtime_dag
        if not dag:
            return
        if not success:
            dag.coverage_gaps.append("planned action execution failed")
            return
        if action.get("_runtime_plan_id") != dag.plan_id:
            return

    def locate_and_get_context(
        self,
        ui_hash: str,
        semantic_layout: str,
        task: str,
        screen_dict: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        screen_dict = dict(screen_dict or {"ui_hash": ui_hash, "semantic_layout": semantic_layout})
        observation = RuntimeObservation(
            ui_hash=ui_hash,
            semantic_layout=semantic_layout,
            task=task,
            screen_dict=screen_dict,
        )
        context_data = self._base_context(task)
        context_data["graph_runtime"] = self._runtime_contract()

        if self._try_runtime_dag_hint(context_data, screen_dict):
            return context_data

        belief = self.spatial_graph_memory.locate(
            screen_dict,
            task,
            previous_action=self.manager._pending_transition_action,
        )
        self._sync_current_state(belief)
        context_data["current_state_id"] = belief.current_state_id
        context_data["belief"] = belief.to_dict()

        current_page_state = belief.candidates[0].state if belief.candidates else None

        repair_hint = self._verify_pending_transition(belief, current_page_state)
        goal_spec = self._infer_goal_spec(task, current_page_state, semantic_layout)
        route_plan = self.spatial_graph_memory.plan(belief, goal_spec)

        context_data["goal_spec"] = goal_spec.to_dict()
        context_data["route_plan"] = route_plan.to_dict()
        context_data["repair_hint"] = repair_hint.to_dict() if repair_hint else None
        context_data["runtime_metrics"] = self.get_runtime_metrics()

        if route_plan.mode == "navigate" and route_plan.steps:
            self.manager._runtime_dag = self._runtime_dag_from_route(belief, goal_spec, route_plan)
            context_data["runtime_dag"] = self.manager._runtime_dag.to_dict()
        elif route_plan.mode == "explore" and route_plan.risk_summary:
            self.manager._coverage_gaps.append(route_plan.risk_summary)
            self._metric("coverage_gaps")
            context_data["coverage_gap"] = route_plan.risk_summary

        spatial_context = self.spatial_graph_memory.context_summary(
            belief,
            route_plan,
            repair_hint=repair_hint,
        )
        if spatial_context:
            self._add_graph_hint(context_data, spatial_context)
        self._inject_task_plan_hint(context_data, goal_spec)

        if route_plan.mode == "navigate" and route_plan.next_action:
            next_action = dict(route_plan.next_action)
            if goal_spec.slots:
                next_action = self.spatial_graph_memory._fill_runtime_slots(next_action, goal_spec.slots)
            if self.manager._runtime_dag:
                next_action["_runtime_plan_id"] = self.manager._runtime_dag.plan_id
            source_pt = current_page_state.page_type if current_page_state else ""
            target_pt = next_action.get("postcondition", "")
            if self._requires_vlm_verification(source_pt, target_pt):
                self._inject_vlm_verification_hint(next_action, source_pt, target_pt, context_data)
                context_data["mode"] = "verify_with_vlm"
            else:
                context_data["mode"] = "navigate"
            context_data["next_actions"] = [next_action]
            if self.verbose:
                print(
                    "[GraphRuntime] Navigation Mode "
                    f"mode={context_data['mode']} action={next_action.get('type')} "
                    f"target={str(next_action.get('target', ''))[:40]} "
                    f"postcondition={next_action.get('postcondition', '')}"
                )
            return context_data

        if route_plan.mode == "goal_reached":
            context_data["mode"] = "goal_reached"
            return context_data

        context_data["mode"] = "explore"
        # Domain structural priors: inject text hint for cold-start apps.
        # Only appended to graph_hint — never produces executable actions.
        self._inject_domain_priors(context_data, current_page_state, goal_spec)
        return context_data

    @staticmethod
    def _add_graph_hint(context_data: dict[str, Any], text: str, *, append: bool = False) -> None:
        """Accumulate graph co-pilot text under the dedicated "graph_hint" key.

        Kept separate from "semantic_context" (the personalized-memory base
        consumed by ClarificationAgent) so the agent can inject graph guidance
        into the VLM message as a first-class block.
        """
        existing = context_data.get("graph_hint", "")
        merged = f"{existing}\n{text}" if append else f"{text}\n{existing}"
        context_data["graph_hint"] = merged.strip()

    def _base_context(self, task: str) -> dict[str, Any]:
        return {
            "max_similarity": 0.0,
            "mode": "explore",
            "semantic_context": self.manager.get_relevant_context(task),
            "graph_hint": "",
            "next_actions": [],
            "current_state_id": None,
            "task_trajectory": None,
            "belief": None,
            "goal_spec": None,
            "route_plan": None,
            "repair_hint": None,
            "runtime_metrics": self.get_runtime_metrics(),
        }

    def _runtime_contract(self) -> dict[str, Any]:
        return {
            "database": self.database,
            "contract": self.contract_version,
            "legacy_fallback": self.legacy_fallback,
        }

    def _try_runtime_dag_hint(self, context_data: dict[str, Any], screen_dict: dict[str, Any]) -> bool:
        dag = self.manager._runtime_dag
        if not (dag and dag.is_usable and screen_dict.get("_runtime_hint")):
            return False
        if self.manager._pending_transition_source is not None:
            target_state = dag.nodes.get(dag.current_node_id)
            if target_state is None:
                target_state = PageState(
                    state_id=dag.current_node_id,
                    app=dag.app or "",
                    page_type=(self.manager._pending_transition_action or {}).get("postcondition", ""),
                )
            if self.manager._pending_transition_action is not None:
                self.spatial_graph_memory.record_observation(
                    self.manager._pending_transition_source,
                    self.manager._pending_transition_action,
                    target_state,
                    outcome="success",
                )
            self.manager._pending_transition_source = None
            self.manager._pending_transition_action = None
            self.manager._pending_expected_postcondition = None
            dag.advance()

        next_action = self.spatial_graph_memory.next_planned_action(dag)
        dag_node = dag.nodes.get(dag.current_node_id)
        context_data["runtime_dag"] = dag.to_dict()
        context_data["runtime_metrics"] = self.get_runtime_metrics()
        context_data["goal_spec"] = dag.goal_spec.to_dict()
        context_data["current_state_id"] = dag.current_node_id
        context_data["belief"] = {
            "current_state_id": dag.current_node_id,
            "confidence": 1.0,
            "is_novel": False,
            "candidates": [dag_node.to_dict()] if dag_node else [],
        }
        if next_action:
            next_action["_runtime_plan_id"] = dag.plan_id
            source_pt = dag_node.page_type if dag_node else ""
            target_pt = next_action.get("postcondition", "")
            if self._requires_vlm_verification(source_pt, target_pt):
                self._inject_vlm_verification_hint(next_action, source_pt, target_pt, context_data)
                context_data["mode"] = "verify_with_vlm"
            else:
                context_data["mode"] = "navigate"
            context_data["next_actions"] = [next_action]
            context_data["route_plan"] = {
                "mode": "runtime_dag",
                "confidence": next_action.get("confidence", 0.0),
                "coverage_gaps": [],
            }
            self._add_graph_hint(
                context_data,
                f"[RuntimeDAG] plan={dag.plan_id} "
                f"step={dag.current_index + 1}/{len(dag.route)} "
                f"next={next_action.get('type')}:{next_action.get('target')}",
            )
            return True
        context_data["mode"] = "goal_reached"
        context_data["route_plan"] = {"mode": "goal_reached", "source": "runtime_dag"}
        return True

    def _sync_current_state(self, belief: PageBelief) -> None:
        current_page_state = belief.candidates[0].state if belief.candidates else None
        self.manager._current_page_state = current_page_state
        self.manager._current_state_id = belief.current_state_id
        if not self.manager._task_start_state_id:
            self.manager._task_start_state_id = belief.current_state_id
        if not self.manager.state.current_state_id:
            self.manager.state.start_task_state(belief.current_state_id)
        elif self.manager.state.current_state_id != belief.current_state_id:
            self.manager.state.update_state(belief.current_state_id)

    def _load_functionality_context(
        self,
        observation: RuntimeObservation,
        belief: PageBelief,
        current_page_state: PageState | None,
    ) -> dict[str, Any]:
        page_type = observation.page_type or (current_page_state.page_type if current_page_state else "")
        app = observation.app or (current_page_state.app if current_page_state else "")
        state_id = belief.current_state_id or None
        if not page_type or page_type == "unknown":
            return {}
        if hasattr(self.graph_store, "get_v4_functionality_context"):
            try:
                return self.graph_store.get_v4_functionality_context(
                    page_type=page_type,
                    app=app or "",
                    state_id=state_id,
                )
            except Exception as exc:
                if self.verbose:
                    print(f"[GraphRuntime] V4 functionality query failed: {exc}")
        return {}

    def _verify_pending_transition(
        self,
        belief: PageBelief,
        current_page_state: PageState | None,
    ) -> Any | None:
        if not (
            self.manager._pending_transition_source is not None
            and self.manager._pending_transition_action is not None
            and current_page_state is not None
        ):
            return None

        outcome = "success"
        pending_repair_hint = None
        if self.manager._pending_expected_postcondition:
            pending_repair_hint = self.spatial_graph_memory.repair(
                belief,
                self.manager._pending_expected_postcondition,
                None,
            )
            print(
                "[GraphRuntime] postcondition verification "
                f"expected={self.manager._pending_expected_postcondition} "
                f"actual={current_page_state.page_type} "
                f"decision={pending_repair_hint.action}"
            )
            if pending_repair_hint.action != "retry":
                outcome = "failure"
            elif (
                self.manager._runtime_dag
                and self.manager._pending_transition_action.get("_runtime_plan_id")
                == self.manager._runtime_dag.plan_id
            ):
                self.manager._runtime_dag.advance()
                node = self.manager._runtime_dag.nodes.get(self.manager._runtime_dag.current_node_id)
                if node:
                    self.manager._current_page_state = node
                    self.manager._current_state_id = node.state_id

        self.spatial_graph_memory.record_observation(
            self.manager._pending_transition_source,
            self.manager._pending_transition_action,
            current_page_state,
            outcome=outcome,
        )
        self.manager._last_repair_decision = pending_repair_hint
        self.manager._pending_transition_source = None
        self.manager._pending_transition_action = None
        self.manager._pending_expected_postcondition = None
        return pending_repair_hint

    def _infer_goal_spec(
        self,
        task: str,
        current_page_state: PageState | None,
        semantic_layout: str,
    ) -> GoalSpec:
        app = current_page_state.app if current_page_state else semantic_layout
        goal_spec = self.spatial_graph_memory.infer_goal(task, app=app)
        vlm_plan = getattr(self.manager, "_vlm_plan", {}) or {}

        if not vlm_plan:
            if goal_spec.slots.get("query"):
                overridden = self._search_first_targets(
                    list(goal_spec.target_page_types), current_page_state,
                )
                if overridden != list(goal_spec.target_page_types):
                    return GoalSpec(
                        domain=goal_spec.domain,
                        target_page_types=tuple(overridden),
                        slots=goal_spec.slots,
                        forbidden_actions=goal_spec.forbidden_actions,
                        missing_info_policy=goal_spec.missing_info_policy,
                    )
            return goal_spec

        enriched_slots = dict(goal_spec.slots)
        if vlm_plan.get("search_query"):
            enriched_slots["query"] = str(vlm_plan["search_query"])
        if vlm_plan.get("product"):
            enriched_slots["product"] = str(vlm_plan["product"])
        specs = vlm_plan.get("specs") if isinstance(vlm_plan.get("specs"), dict) else {}
        for key, value in specs.items():
            if value and key not in enriched_slots:
                enriched_slots[key] = str(value)
        targets = list(goal_spec.target_page_types)
        vlm_target = str(vlm_plan.get("target_page") or "")
        if vlm_target and vlm_target not in targets:
            targets.insert(0, vlm_target)

        has_search_query = bool(
            vlm_plan.get("search_query") or enriched_slots.get("query")
        )
        if has_search_query:
            targets = self._search_first_targets(targets, current_page_state)

        return GoalSpec(
            domain=goal_spec.domain,
            target_page_types=tuple(dict.fromkeys(targets)),
            slots=enriched_slots,
            forbidden_actions=goal_spec.forbidden_actions,
            missing_info_policy=goal_spec.missing_info_policy,
        )

    @staticmethod
    def _search_first_targets(
        original_targets: list[str],
        current_page_state: PageState | None,
    ) -> list[str]:
        """Override routing targets for search tasks on pre-search pages.

        Prevents Dijkstra from finding shortcuts (home → product_detail)
        that bypass search and open random products instead of what the
        user actually wants.  The target is advanced progressively:

            home         → force ["search_input"]
            search_input → force ["search_result"]
            anything else → keep original targets (search is done)
        """
        current_type = current_page_state.page_type if current_page_state else ""
        if current_type in ("home", "unknown", ""):
            return ["search_input"]
        if current_type == "search_input":
            return ["search_result"]
        return original_targets

    def _runtime_dag_from_route(self, belief: PageBelief, goal_spec: GoalSpec, route_plan: Any) -> RuntimeDAG:
        app = belief.candidates[0].state.app if belief.candidates else ""
        seed = f"{getattr(self.manager, 'current_task', '')}|{belief.current_state_id}|{len(route_plan.steps)}"
        dag = RuntimeDAG(
            plan_id=hashlib.md5(seed.encode("utf-8")).hexdigest()[:12],
            app=app,
            goal_spec=goal_spec,
        )
        for candidate in belief.candidates:
            dag.nodes[candidate.state.state_id] = candidate.state
        for step in route_plan.steps:
            edge = step.edge
            source = self.spatial_graph_memory._local_states.get(edge.source_id)
            target = self.spatial_graph_memory._local_states.get(edge.target_id)
            if source:
                dag.nodes[source.state_id] = source
            if target:
                dag.nodes[target.state_id] = target
            dag.edges.setdefault(edge.source_id, []).append(edge)
            dag.route.append(edge)
        return dag

    def _inject_task_plan_hint(self, context_data: dict[str, Any], goal_spec: GoalSpec) -> None:
        if not goal_spec.slots:
            return
        parts: list[str] = []
        if goal_spec.slots.get("query"):
            parts.append(f"search query: {goal_spec.slots['query']}")
        if goal_spec.slots.get("product"):
            parts.append(f"target product: {goal_spec.slots['product']}")
        specs: list[str] = []
        if goal_spec.slots.get("color"):
            specs.append(f"color={goal_spec.slots['color']}")
        if goal_spec.slots.get("storage"):
            specs.append(f"storage={goal_spec.slots['storage']}")
        if specs:
            parts.append(f"specs: {', '.join(specs)}")
        if parts:
            self._add_graph_hint(context_data, f"[Task Plan] {' | '.join(parts)}")

    def _requires_vlm_verification(self, source_page_type: str, target_page_type: str) -> bool:
        if hasattr(self.manager, "_requires_vlm_verification"):
            return bool(self.manager._requires_vlm_verification(source_page_type, target_page_type))

        # Definition 9: data-driven VLM verification via outcome entropy.
        # When the EdgeLifecycleManager tracks outcome distributions, high
        # entropy transitions (same action → multiple possible targets) are
        # automatically flagged for VLM verification instead of relying on
        # the hardcoded VLM_VERIFY_TRANSITIONS set.
        lifecycle = getattr(self.spatial_graph_memory, "_edge_lifecycle", None)
        if lifecycle is not None:
            # Check all action keys for this source page type
            for record in lifecycle.get_promoted_edges_for_page(source_page_type):
                if record.target_page_type == target_page_type:
                    action_key = f"{record.intent}:{record.action_target}"
                    if lifecycle.requires_vlm_verification(source_page_type, action_key):
                        return True

        # Use schema-backed pairs; fall back to the module constant for safety.
        try:
            from phone_agent.spatial.schema_registry import vlm_verify_transitions_for
            pairs = vlm_verify_transitions_for("shopping")
        except Exception:
            pairs = VLM_VERIFY_TRANSITIONS
        return (source_page_type, target_page_type) in pairs

    def _inject_vlm_verification_hint(
        self,
        next_action: dict[str, Any],
        source_page_type: str,
        target_page_type: str,
        context_data: dict[str, Any],
    ) -> None:
        next_action["_requires_vlm_verification"] = True
        action_type = next_action.get("type", "")
        hint = (
            f"[图谱路径参考] 当前页面: {source_page_type}，下一步目标页面: {target_page_type}\n"
            f"图谱建议动作类型: {action_type}\n"
            f"⚠️ 此步骤涉及商品/规格选择——图谱仅提供页面导航方向，"
            f"你必须根据当前截图内容和用户任务自行判断点击哪个元素。"
            f"绝不要复用图谱中的历史商品信息！"
        )
        self._add_graph_hint(context_data, hint)

    def _enrich_next_action_with_functionality(
        self,
        next_action: dict[str, Any],
        functionality_context: dict[str, Any],
    ) -> dict[str, Any]:
        if hasattr(self.manager, "_enrich_next_action_with_functionality"):
            return self.manager._enrich_next_action_with_functionality(next_action, functionality_context)
        return next_action

    def _inject_v4_hint(self, context_data: dict[str, Any], functionality_context: dict[str, Any]) -> None:
        semantic_hint = functionality_context.get("semantic_hint") if functionality_context else ""
        if semantic_hint:
            self._add_graph_hint(context_data, f"[V4 Knowledge] {semantic_hint}")

    def _get_domain_prior_provider(self) -> Any:
        """Lazily instantiate the DomainPriorProvider, respecting any test injection."""
        if self._domain_prior_provider is not None:
            return self._domain_prior_provider
        try:
            from phone_agent.spatial.domain_priors import DomainPriorProvider
            self._domain_prior_provider = DomainPriorProvider(
                graph_store=self.graph_store,
            )
        except Exception:
            self._domain_prior_provider = None
        return self._domain_prior_provider

    def _inject_domain_priors(
        self,
        context_data: dict[str, Any],
        current_page_state: PageState | None,
        goal_spec: Any,
    ) -> None:
        """Append domain structural prior hint to graph_hint when app is cold."""
        if not _AMSG_DOMAIN_PRIORS_ENABLED:
            return
        try:
            provider = self._get_domain_prior_provider()
            if provider is None:
                return
            app = current_page_state.app if current_page_state else ""
            page_type = current_page_state.page_type if current_page_state else ""
            if not app or not page_type or page_type == "unknown":
                return
            if not provider.coverage_is_cold(app):
                return
            goal_page_types: tuple[str, ...] = ()
            if goal_spec is not None and hasattr(goal_spec, "target_page_types"):
                goal_page_types = tuple(goal_spec.target_page_types or ())
            priors = provider.priors_for(app, page_type, goal_page_types)
            hint = provider.format_hint(priors, app, page_type)
            if hint:
                self._add_graph_hint(context_data, hint, append=True)
        except Exception:
            pass  # domain priors must never break the main loop

    def _metric(self, key: str, amount: int = 1) -> None:
        metrics = getattr(self.manager, "_runtime_metrics", None)
        if metrics is None:
            return
        metrics[key] = metrics.get(key, 0) + amount


def runtime_graph_database() -> str:
    return os.getenv("AMSG_RUNTIME_GRAPH_DATABASE") or DEFAULT_RUNTIME_GRAPH_DATABASE
