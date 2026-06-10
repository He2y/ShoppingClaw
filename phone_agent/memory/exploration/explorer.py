"""Coverage-guided offline explorer: VLM-autonomous app exploration loop."""

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from phone_agent.actions.handler import ActionHandler, parse_action
from phone_agent.config.apps import get_package_name
from phone_agent.device_factory import DeviceFactory
from phone_agent.model.client import MessageBuilder, ModelClient
from phone_agent.spatial.active_builder import ActiveGraphBuilder
from phone_agent.spatial.hypothesis import EdgeHypothesisGenerator
from phone_agent.spatial.semantics import ScreenSemanticsExtractor

from .classifier import PageClassifier
from .confidence import PageConfidence, assess_page_confidence
from .human_gate import HumanGate
from .interference import InterferenceHandler, InterferencePolicy
from .page_evidence import infer_page_type_from_reasoning
from .prompts import _build_exploration_system_prompt
from .safety import SafetyPolicy
from .stability import StabilityResult, wait_for_stable_screen
from .task_builder import build_default_task, coverage_from_schema
from .transition_rules import TransitionRuleEngine
from .types import (
    CoverageReport,
    CoverageTarget,
    PageInfo,
    PageTypeSpace,
    ShoppingPageType,
    Trajectory,
    _HIGH_RISK_PAGE_TYPES,
    _PAGE_TYPE_SUMMARY,
    _SCREEN_CHANGE_HASH_LEN,
    _SPEC_TRIGGER_TOKENS,
    _UNSAFE_ACTION_TOKENS,
)
from .watchdog import ProgressWatchdog, StuckDetector, WatchdogConfig


def _get_default_safety() -> SafetyPolicy:
    """Lazy-build a default shopping SafetyPolicy (used for backward-compat method calls)."""
    from phone_agent.spatial.schema_registry import get_default_registry
    schema = get_default_registry().merged("shopping")
    return SafetyPolicy.from_schema(schema)


def _page_type_str(page_info: Any) -> str:
    if page_info is None:
        return ""
    pt = getattr(page_info, "page_type", "")
    if hasattr(pt, "value"):
        return str(pt.value)
    return str(pt)


class OfflineExplorer:
    """购物场景离线探索器 — 自然语言任务驱动

    每次运行接受一个自然语言任务描述，VLM 根据描述定向探索 App。
    多次运行不同任务后，合并所有 JSON 输出即可构建完整的 App 页面路径图谱。

    探索循环:
    1. Launch app
    2. Screenshot → VLM decides action → Execute → Analyze result → Record → Repeat
    3. VLM can finish() early when exploration is complete
    """

    # ── Constructor ────────────────────────────────────────────

    def __init__(
        self,
        app_name: str,
        device_factory: DeviceFactory,
        model_client: ModelClient,
        storage_dir: str = "memory_db/exploration",
        max_steps: int = 15,
        task_description: str = "广度优先探索所有主要页面类型",
        classifier_api_key: str | None = "",
        classifier_base_url: str | None = None,
        classifier_model: str = "qwen3-vl-flash",
        auto_import_graph: bool = False,
        graph_store: Any | None = None,
        coverage_targets: CoverageTarget | None = None,
        device_id: str | None = None,
        classifier_mode: str = "fast",
        classifier_timing: str = "after_action",
        classifier_timeout: float = 8.0,
        classifier_max_image_width: int = 720,
        active_exploration: bool = False,
        verbose: bool = True,
        schema_name: str | None = None,
        transition_policy: str = "strict",
        *,
        interference_policy: InterferencePolicy | None = None,
        watchdog_config: WatchdogConfig | None = None,
        human_gate: HumanGate | None = None,
        wait_stable: bool = True,
    ):
        self.app_name = app_name
        self.device = device_factory
        self.vlm = model_client
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.max_steps = max_steps
        self.task_description = task_description
        self.auto_import_graph = auto_import_graph
        self.graph_store = graph_store
        self.device_id = device_id
        self.classifier_timing = classifier_timing
        self.verbose = verbose
        self.active_exploration = active_exploration
        self.wait_stable = wait_stable

        # Resolve schema
        resolved_schema_name = schema_name
        if resolved_schema_name is None:
            from phone_agent.spatial.app_registry import get_default_app_registry
            record = get_default_app_registry().resolve(app_name)
            if record:
                resolved_schema_name = record.schema
        if resolved_schema_name is None:
            resolved_schema_name = "shopping"
        self._schema_name = resolved_schema_name

        from phone_agent.spatial.schema_registry import get_default_registry
        self.schema = get_default_registry().merged(self._schema_name)
        self.space = PageTypeSpace.from_schema(self.schema)
        self.safety = SafetyPolicy.from_schema(self.schema)
        self.rules = TransitionRuleEngine(self.schema, self.safety, transition_policy)

        # Coverage and task
        self.coverage_targets = coverage_targets or coverage_from_schema(self.schema)
        self.coverage_report = CoverageReport((), self.coverage_targets.page_types, (), self.coverage_targets.transitions)
        self.last_import_result = None

        self.semantics_extractor = ScreenSemanticsExtractor(schema_name=self._schema_name)
        self.edge_hypothesis_generator = EdgeHypothesisGenerator(schema_name=self._schema_name)
        self.active_builder = ActiveGraphBuilder()

        # Action handler for executing VLM-decided actions
        self.action_handler = ActionHandler(device_id=device_id)

        # Page classifier
        self.classifier = PageClassifier(
            api_key=classifier_api_key,
            base_url=classifier_base_url,
            model=classifier_model,
            mode=classifier_mode,
            timeout=classifier_timeout,
            max_image_width=classifier_max_image_width,
            page_type_space=self.space,
            schema=self.schema,
        )

        # Collected data
        self.discovered_pages: Dict[str, PageInfo] = {}  # state_key → PageInfo
        self.trajectories: List[Trajectory] = []
        self.rejected_transitions: List[Dict[str, Any]] = []
        self.last_rejection_reason = ""
        self.transitions: List[Dict[str, Any]] = []  # from → action → to

        # Open-vocabulary page type proposals
        self.page_type_proposals: Dict[str, Dict[str, Any]] = {}

        # Phase 4: interference, watchdog, trap tracking
        self.interference_events: List[Dict[str, Any]] = []
        self.interference_log: List[Dict[str, Any]] = []  # interference-rejected transitions
        self.trap_edges: List[Dict[str, Any]] = []
        self.transition_observations: Dict[tuple, int] = {}  # (src_type, action_intent, tgt_type) → count
        self._restart_count: int = 0

        # Phase 4 component setup
        _wdog_cfg = watchdog_config or WatchdogConfig()
        self.watchdog = StuckDetector(_wdog_cfg)
        self.progress_watchdog = ProgressWatchdog(_wdog_cfg)
        self.interference_policy = interference_policy or InterferencePolicy()

        def _capture_fn() -> Any:
            return self.device.get_screenshot(self.device_id)

        def _classify_fn(screenshot: Any, app: str, step: int) -> Any:
            return self._classify_page_info(screenshot, app, step)

        self.interference = InterferenceHandler(
            policy=self.interference_policy,
            space=self.space,
            action_handler=self.action_handler,
            classify_fn=_classify_fn,
            capture_fn=_capture_fn,
            log_fn=self._log,
            human_gate=human_gate,
            vlm_close_fn=self._vlm_find_close_button,
        )

    # ── Top-Level Entry ────────────────────────────────────────

    def explore(self) -> List[Trajectory]:
        """Run VLM exploration guided by natural language task description."""
        self._log(f"\n{'='*60}")
        self._log(f"  Offline Explorer: {self.app_name}")
        self._log(f"  Task: {self.task_description}")
        self._log(f"{'='*60}\n")

        self._log(f"[0] Launching {self.app_name}...")
        package = get_package_name(self.app_name)
        if not package:
            self._log(f"  ERROR: Unknown app '{self.app_name}', not in APP_PACKAGES")
            return []

        self.device.launch_app(self.app_name, self.device_id)
        time.sleep(4)

        traj = self._exploration_loop()
        self.trajectories = [traj]

        self._save_results(traj)

        self._log(f"\n{'='*60}")
        self._log(f"  Done: {len(self.discovered_pages)} unique pages discovered")
        self._log(f"  {len(traj.steps)} exploration steps taken")
        self._log(f"{'='*60}\n")
        return self.trajectories

    # ── VLM-Driven Exploration Loop ────────────────────────────

    def _exploration_loop(self) -> Trajectory:
        """Run the task-directed closed-loop VLM exploration."""
        if self.classifier_timing == "after_action":
            return self._exploration_loop_action_first()

        task_desc = self.task_description
        traj = Trajectory(task=task_desc, app=self.app_name)
        context: List[Dict[str, Any]] = []

        system_prompt = _build_exploration_system_prompt(task_desc)
        context.append(MessageBuilder.create_system_message(system_prompt))

        prev_page_key: Optional[str] = None
        prev_action: Optional[Dict[str, Any]] = None
        prev_screenshot_hash: str = ""

        for step_idx in range(self.max_steps):
            screenshot = self.device.get_screenshot(self.device_id)
            current_app = self.device.get_current_app(self.device_id)

            cur_hash = screenshot.base64_data[:_SCREEN_CHANGE_HASH_LEN]
            if step_idx > 0 and prev_screenshot_hash and cur_hash == prev_screenshot_hash:
                self._log("  ⚠ 屏幕无变化，上次操作可能未生效")
            prev_screenshot_hash = cur_hash

            screen_info = MessageBuilder.build_screen_info(current_app)
            discovered_summary = self._build_discovered_summary()

            if step_idx == 0:
                task_text = (
                    f"【本次任务】{task_desc}\n"
                    f"开始探索{self.app_name}。你已经在该App中。\n"
                    f"请聚焦任务描述中的方向，不要跳到无关板块。\n\n"
                    f"{discovered_summary}\n\n"
                    f"{screen_info}"
                )
            else:
                task_text = (
                    f"继续探索{self.app_name}。记住：聚焦\"{task_desc}\"方向。\n"
                    f"{discovered_summary}\n\n"
                    f"{screen_info}"
                )

            context.append(MessageBuilder.create_user_message(
                text=task_text, image_base64=screenshot.base64_data
            ))

            try:
                response = self.vlm.request(context)
            except Exception as e:
                self._log(f"  VLM error: {e}")
                break

            try:
                action = parse_action(response.action)
            except ValueError as e:
                self._log(f"  Parse error: {e}")
                action = {"_metadata": "finish", "message": str(e)}

            page_info = self._classify_page_info(screenshot, current_app or self.app_name, step_idx + 1)
            self._record_page(page_info)
            self._log(f"  [{step_idx+1}] {_page_type_str(page_info)}: {page_info.semantic_summary[:60]}")

            if prev_page_key is not None and prev_action is not None:
                recorded = self._record_transition(prev_page_key, prev_action, page_info.state_key())
                if not recorded and self._should_stop_after_rejected_transition(self.last_rejection_reason):
                    self._rollback_from_risky_page(screenshot.width, screenshot.height)
                    self._log(f"  stop exploration after rejected transition: {self.last_rejection_reason}")
                    break
            self._update_coverage_report()

            if action.get("_metadata") == "finish":
                traj.add_step(page_info, action, response.thinking)
                self._log(f"  VLM finished: {action.get('message', '')[:100]}")
                break

            traj.add_step(page_info, action, response.thinking)

            if self.coverage_report.complete:
                self._log("  Coverage target reached; stopping exploration.")
                break

            context[-1] = MessageBuilder.remove_images_from_message(context[-1])
            assistant_content = (
                f" thinking{response.thinking} response<answer>{response.action}</answer>"
            )
            context.append(MessageBuilder.create_assistant_message(assistant_content))

            if not self._is_safe_action(page_info, action):
                self._log("  Unsafe exploration action blocked; recording page only.")
                prev_page_key = None
                prev_action = None
                continue
            try:
                result = self.action_handler.execute(
                    action, screenshot.width, screenshot.height
                )
                if self.verbose and not result.success:
                    self._log(f"  Action result: {result.message}")
            except Exception as e:
                self._log(f"  Execute error: {e}")
                prev_page_key = None
                prev_action = None
                continue

            time.sleep(2)

            prev_page_key = page_info.state_key()
            prev_action = action

        else:
            self._log(f"  Max steps ({self.max_steps}) reached")
            traj.success = True

        return traj

    def _exploration_loop_action_first(self) -> Trajectory:
        """Run exploration with action execution before post-action classification.

        Phase 4 additions:
        - StuckDetector: phash ring buffer; escalation: Back → Back → relaunch
        - Foreground drift detection: if current app ≠ target, press Back; relaunch if persists
        - Interference landing handling via InterferenceHandler
        - Stability-based wait (wait_for_stable_screen) instead of fixed sleep(2)
        - Trap-token pre-filter before action execution
        - ProgressWatchdog: relaunch if no new coverage for no_progress_limit steps
        Phase 4.5 additions:
        - assess_page_confidence for each landing page
        - Transient landings rejected from transitions
        - trap_edges / avoid-hint injection
        """
        task_desc = self.task_description
        traj = Trajectory(task=task_desc, app=self.app_name)
        system_message = MessageBuilder.create_system_message(_build_exploration_system_prompt(task_desc))
        current_page: Optional[PageInfo] = None
        prev_screenshot_hash = ""
        last_step_note = "No action has been verified yet."
        pending_transition: Optional[Dict[str, Any]] = None
        pending_confidence: Optional[PageConfidence] = None  # Phase 4.5
        restarts = 0
        _wdog_cfg = getattr(self.watchdog, "_config", WatchdogConfig())

        def _capture_fn() -> Any:
            return self.device.get_screenshot(self.device_id)

        for step_idx in range(self.max_steps):
            screenshot = self.device.get_screenshot(self.device_id)
            current_app = self.device.get_current_app(self.device_id)
            cur_hash = screenshot.base64_data[:_SCREEN_CHANGE_HASH_LEN]
            if step_idx > 0 and prev_screenshot_hash and cur_hash == prev_screenshot_hash:
                self._log("  ⚠ 屏幕无变化，上次操作可能未生效")
            prev_screenshot_hash = cur_hash

            # Phase 4: StuckDetector
            wd = getattr(self, "watchdog", None)
            if wd is not None:
                wd.observe(screenshot.base64_data)
                if wd.is_stuck():
                    self._log("  [watchdog] stuck detected — escalating")
                    # Level 1: Back
                    self._rollback_from_risky_page(screenshot.width, screenshot.height)
                    screenshot = self.device.get_screenshot(self.device_id)
                    wd.observe(screenshot.base64_data)
                    if wd.is_stuck():
                        # Level 2: another Back
                        self._rollback_from_risky_page(screenshot.width, screenshot.height)
                        screenshot = self.device.get_screenshot(self.device_id)
                        wd.observe(screenshot.base64_data)
                        if wd.is_stuck():
                            # Level 3: relaunch
                            ok = self._escape_and_restart("stuck detector")
                            if not ok:
                                traj.success = True
                                break
                            restarts += 1
                            pending_transition = None
                            current_page = None
                            pending_confidence = None
                            continue

            # Phase 4: foreground drift check
            if current_app and current_app.lower() and step_idx > 0:
                try:
                    from phone_agent.spatial.app_registry import get_default_app_registry
                    reg = get_default_app_registry()
                    target_canon = reg.canonical_id(self.app_name)
                    current_canon = reg.canonical_id(current_app)
                    is_system = any(
                        tok in current_app.lower()
                        for tok in ("home", "launcher", "systemui", "settings")
                    )
                    if not is_system and current_canon != target_canon:
                        self._log(
                            f"  [watchdog] foreground drift: expected={self.app_name} "
                            f"got={current_app} — pressing Back"
                        )
                        self._rollback_from_risky_page(screenshot.width, screenshot.height)
                        screenshot = self.device.get_screenshot(self.device_id)
                        current_app2 = self.device.get_current_app(self.device_id)
                        if current_app2:
                            canon2 = reg.canonical_id(current_app2)
                            is_system2 = any(
                                tok in current_app2.lower()
                                for tok in ("home", "launcher", "systemui", "settings")
                            )
                            if not is_system2 and canon2 != target_canon:
                                ok = self._escape_and_restart("foreground drift")
                                if not ok:
                                    traj.success = True
                                    break
                                restarts += 1
                                pending_transition = None
                                current_page = None
                                pending_confidence = None
                                continue
                except Exception as exc:
                    self._log(f"  [watchdog] foreground check error: {exc}")

            if current_page is None or current_page.screenshot_hash != hashlib.md5(screenshot.base64_data.encode()).hexdigest():
                current_page = self._classify_page_info(screenshot, current_app or self.app_name, step_idx + 1)
            if step_idx > 0 and self.coverage_report.complete:
                self._log("  Coverage target reached; stopping exploration.")
                break

            screen_info = MessageBuilder.build_screen_info(current_app)
            discovered_summary = self._build_discovered_summary()
            current_state_note = self._build_current_state_note(current_page, last_step_note)
            active_frontier_hint = self._build_active_frontier_hint(current_page)

            # Phase 4: inject trap-avoid hint into task_text
            trap_hint = self._build_trap_avoid_hint()
            if step_idx == 0:
                task_text = (
                    f"【本次任务】{task_desc}\n"
                    f"开始探索{self.app_name}。你已经在该App中。\n"
                    f"请聚焦任务描述中的方向，不要跳到无关板块。\n\n"
                    f"{current_state_note}\n\n"
                    f"{discovered_summary}\n\n"
                    f"{screen_info}"
                )
            else:
                task_text = (
                    f"继续探索{self.app_name}。记住：聚焦\"{task_desc}\"方向。\n"
                    f"{current_state_note}\n\n"
                    f"{discovered_summary}\n\n"
                    f"{screen_info}"
                )
            if active_frontier_hint:
                task_text = f"{task_text}\n\n{active_frontier_hint}"
            if trap_hint:
                task_text = f"{task_text}\n\n{trap_hint}"

            request_context = [
                system_message,
                MessageBuilder.create_user_message(text=task_text, image_base64=screenshot.base64_data),
            ]

            try:
                response = self.vlm.request(request_context)
            except Exception as e:
                self._log(f"  VLM error: {e}")
                break

            try:
                action = parse_action(response.action)
            except ValueError as e:
                self._log(f"  Parse error: {e}")
                action = {"_metadata": "finish", "message": str(e)}

            reasoning_for_repair = response.thinking
            if action.get("_metadata") == "finish" and str(action.get("message") or "").startswith("Failed to parse action"):
                reasoning_for_repair = f"{response.thinking}\n{response.action}"
            inferred_page_type = OfflineExplorer._infer_page_type_from_reasoning(reasoning_for_repair)
            inferred_str_raw: Optional[str] = None  # Phase 4.5: pure str for confidence
            current_pt_str = _page_type_str(current_page)
            inferred_str = inferred_page_type.value if hasattr(inferred_page_type, "value") else inferred_page_type
            if inferred_str:
                inferred_str_raw = str(inferred_str)
            if inferred_str and inferred_str != current_pt_str:
                self._log(
                    f"  belief repair: classifier={current_pt_str} "
                    f"reasoning={inferred_str}"
                )
                current_page = self._relabel_page_info(current_page, inferred_page_type)
            self._record_page(current_page)

            if pending_transition:
                recorded, last_step_note = self._finalize_pending_transition(
                    pending_transition,
                    current_page,
                    landing_confidence=pending_confidence,
                )
                pending_transition = None
                pending_confidence = None
                if not recorded and self._should_stop_after_rejected_transition(self.last_rejection_reason):
                    self._log(f"  stop exploration after rejected transition: {self.last_rejection_reason}")
                    break

            traj.add_step(current_page, action, response.thinking)
            self._log(f"  [{step_idx+1}] {_page_type_str(current_page)}: {current_page.semantic_summary[:60]}")

            if action.get("_metadata") == "finish":
                self._log(f"  VLM finished: {action.get('message', '')[:100]}")
                break

            if self.coverage_report.complete:
                self._log("  Coverage target reached; stopping exploration.")
                break

            # Phase 4: trap-token pre-filter
            if self._action_has_trap_token(action, response.thinking):
                self._log("  [trap] action blocked by trap-token pre-filter; skipping.")
                current_pt = _page_type_str(current_page)
                element_desc = json.dumps(action.get("element", ""), ensure_ascii=False)
                getattr(self, "trap_edges", []).append({
                    "semantic_target": element_desc,
                    "page": current_page.state_key() if current_page else "",
                    "reason": "trap_token",
                })
                last_step_note = (
                    f"Last action blocked by trap-token on {current_pt}. "
                    "Choose a different safe action."
                )
                current_page = None
                continue

            if not self._is_safe_action(current_page, action, response.thinking):
                self._log("  Unsafe exploration action blocked; recording page only.")
                pt_str = _page_type_str(current_page)
                if pt_str in self.safety.high_risk_page_types:
                    self._rollback_from_risky_page(screenshot.width, screenshot.height)
                last_step_note = (
                    f"Last action was blocked as unsafe on {pt_str}. "
                    "Choose a rollback, close, or safe navigation action from the current screenshot."
                )
                current_page = None
                continue

            try:
                result = self.action_handler.execute(action, screenshot.width, screenshot.height)
                if self.verbose and not result.success:
                    self._log(f"  Action result: {result.message}")
            except Exception as e:
                self._log(f"  Execute error: {e}")
                last_step_note = f"Last action execution failed: {e}. Do not repeat the same action blindly."
                current_page = None
                continue

            # Phase 4.5a: stability-based wait (replaces fixed sleep(2))
            _stability_result: Optional[StabilityResult] = None
            if getattr(self, "wait_stable", False):
                try:
                    next_screenshot, _stability_result = wait_for_stable_screen(_capture_fn)
                except Exception as exc:
                    self._log(f"  [stability] wait_for_stable_screen error: {exc}")
                    time.sleep(2)
                    next_screenshot = self.device.get_screenshot(self.device_id)
            else:
                time.sleep(2)
                next_screenshot = self.device.get_screenshot(self.device_id)

            if not result.success:
                last_step_note = f"Last action failed: {result.message}. Do not repeat the same action blindly."
                continue

            next_app = self.device.get_current_app(self.device_id) or self.app_name
            next_page = self._classify_page_info(next_screenshot, next_app, step_idx + 1, prefix="post-action")

            # Phase 4.5b: assess confidence of landing page
            _stable_flag = (_stability_result.stable if _stability_result is not None else True)
            landing_confidence = assess_page_confidence(
                next_page,
                self.schema,
                reasoning_inferred=inferred_str_raw,
                stable=_stable_flag,
            )
            if landing_confidence.level != "confident":
                self._log(
                    f"  [confidence] landing={landing_confidence.level} "
                    f"signals={landing_confidence.signals}"
                )

            # Phase 4: interference landing check
            source_key = current_page.state_key() if current_page else ""
            interference_handler = getattr(self, "interference", None)
            if interference_handler is not None and interference_handler.is_interference(next_page):
                self._log(
                    f"  [interference] landing on {_page_type_str(next_page)} — handling"
                )
                # Log to interference_log (not transitions)
                getattr(self, "interference_log", []).append({
                    "from": source_key,
                    "action": action,
                    "to": next_page.state_key(),
                    "reason": "interference",
                })
                outcome = interference_handler.handle(
                    next_page,
                    next_screenshot.width,
                    next_screenshot.height,
                    source_page_key=source_key,
                )
                for evt in outcome.events:
                    getattr(self, "interference_events", []).append(evt.to_dict())

                if outcome.status in ("resolved", "human_resumed"):
                    # Human gate: caller must clear pending_transition
                    current_page = outcome.resumed_page
                    pending_transition = None
                    pending_confidence = None
                    self._log(f"  [interference] resolved → resumed at {_page_type_str(current_page)}")
                    continue
                else:
                    # needs_restart / unresolved
                    ok = self._escape_and_restart("interference unresolved")
                    if not ok:
                        traj.success = True
                        break
                    restarts += 1
                    pending_transition = None
                    pending_confidence = None
                    current_page = None
                    continue

            pending_transition = {
                "from_key": source_key,
                "action": action,
            }
            pending_confidence = landing_confidence  # Phase 4.5
            last_step_note = (
                f"Last action executed from {source_key}; raw post-action "
                f"classifier saw {_page_type_str(next_page)}:{next_page.semantic_summary}. "
                "The next step must verify the actual landing page before planning."
            )
            current_page = next_page

            # Phase 4c: progress watchdog
            pw = getattr(self, "progress_watchdog", None)
            if pw is not None:
                coverage_key_count = (
                    len(getattr(self, "transitions", []))
                    + len(getattr(self, "discovered_pages", {}))
                )
                pw.observe(coverage_key_count)
                if pw.is_wandering():
                    self._log("  [watchdog] no coverage progress — restarting")
                    ok = self._escape_and_restart("no coverage progress")
                    if not ok:
                        traj.success = True
                        break
                    restarts += 1
                    pending_transition = None
                    pending_confidence = None
                    current_page = None

        else:
            self._log(f"  Max steps ({self.max_steps}) reached")
            traj.success = True

        if pending_transition and current_page is not None:
            _, last_step_note = self._finalize_pending_transition(
                pending_transition, current_page, landing_confidence=pending_confidence
            )

        return traj

    # ── Helpers ─────────────────────────────────────────────────

    def _classify_page_info(self, screenshot: Any, app: str, step: int, prefix: str = "classifier") -> PageInfo:
        page_type_str, summary, elements, extras = self.classifier.classify_page(
            screenshot.base64_data,
            screenshot.width,
            screenshot.height,
        )
        # Handle new: open-vocabulary types
        if page_type_str.startswith("new:"):
            self._record_page_type_proposal(page_type_str, extras, screenshot.base64_data)

        page_info = PageInfo(
            page_type=page_type_str,
            semantic_summary=summary,
            elements=elements,
            screenshot_hash=hashlib.md5(screenshot.base64_data.encode()).hexdigest(),
            app=app,
            screenshot_base64=screenshot.base64_data,
            width=screenshot.width,
            height=screenshot.height,
        )
        self._log(
            f"  {prefix} step={step} mode={self.classifier.mode} "
            f"source={self.classifier.source} model={self.classifier.model} "
            f"time={self.classifier.last_duration:.2f}s "
            f"-> {page_type_str}"
        )
        return page_info

    def _record_page_type_proposal(self, page_type: str, extras: Dict[str, Any], screenshot_b64: str) -> None:
        """Track open-vocabulary page type proposals from the classifier."""
        name = page_type  # e.g. "new:coupon_center"
        description = str(extras.get("new_type_description") or "")
        screenshot_hash = hashlib.md5(screenshot_b64.encode()).hexdigest()
        if name not in self.page_type_proposals:
            self.page_type_proposals[name] = {
                "description": description,
                "count": 0,
                "screenshot_hashes": [],
            }
        proposal = self.page_type_proposals[name]
        proposal["count"] = proposal["count"] + 1  # type: ignore[assignment]
        if screenshot_hash not in proposal["screenshot_hashes"]:  # type: ignore[operator]
            proposal["screenshot_hashes"].append(screenshot_hash)  # type: ignore[union-attr]
        if description and not proposal["description"]:
            proposal["description"] = description  # type: ignore[assignment]

    def _record_page(self, page_info: PageInfo):
        """Record a discovered page, deduplicating by state_key."""
        key = page_info.state_key()
        if key not in self.discovered_pages:
            self.discovered_pages[key] = page_info
            if self.verbose:
                self._log(f"    NEW: {_page_type_str(page_info)}")

    @staticmethod
    def _build_current_state_note(page_info: PageInfo | None, last_step_note: str) -> str:
        if page_info is None:
            return (
                "Classifier hypothesis (may be wrong): unknown.\n"
                f"Verifier note: {last_step_note}\n"
                "Use the screenshot as ground truth; explicitly state what the current screen appears to be."
            )
        pt_str = _page_type_str(page_info)
        return (
            f"Classifier hypothesis (may be wrong): {pt_str} - {page_info.semantic_summary}.\n"
            f"Verifier note: {last_step_note}\n"
            "Use the screenshot as ground truth; if the screenshot conflicts with the hypothesis, correct it."
        )

    @staticmethod
    def _infer_page_type_from_reasoning(text: str) -> Any:
        """Infer page type from reasoning text; returns ShoppingPageType for backward compat."""
        from .types import _PAGE_TYPE_MAP
        result_str = infer_page_type_from_reasoning(text)
        if result_str is None:
            return None
        # Return enum for backward compat (existing tests compare to ShoppingPageType.X)
        return _PAGE_TYPE_MAP.get(result_str)

    @staticmethod
    def _is_settings_page_evidence(text: str) -> bool:
        from .page_evidence import is_settings_page_evidence
        return is_settings_page_evidence(text)

    @staticmethod
    def _is_login_page_evidence(text: str) -> bool:
        from .page_evidence import is_login_page_evidence
        return is_login_page_evidence(text)

    @staticmethod
    def _is_cart_page_evidence(text: str) -> bool:
        from .page_evidence import is_cart_page_evidence
        return is_cart_page_evidence(text)

    def _relabel_page_info(self, page_info: PageInfo, page_type: Any) -> PageInfo:
        """Relabel a page with a new type (accepts enum or str)."""
        pt_str: str
        if hasattr(page_type, "value"):
            pt_str = page_type.value
        else:
            pt_str = str(page_type)
        # Use summaries from space if available
        summary = self.space.summaries.get(pt_str, page_info.semantic_summary)
        return PageInfo(
            page_type=pt_str,
            semantic_summary=summary,
            elements=page_info.elements,
            screenshot_hash=page_info.screenshot_hash,
            app=page_info.app,
            screenshot_base64=page_info.screenshot_base64,
            width=page_info.width,
            height=page_info.height,
        )

    def _finalize_pending_transition(
        self,
        pending_transition: Dict[str, Any],
        current_page: PageInfo,
        landing_confidence: Optional["PageConfidence"] = None,
    ) -> tuple[bool, str]:
        self._record_page(current_page)
        from_key = str(pending_transition.get("from_key") or "")
        action = pending_transition.get("action") or {}
        to_key = current_page.state_key()
        recorded = self._record_transition(from_key, action, to_key, landing_confidence)
        self._update_coverage_report()
        if recorded:
            note = f"Last verified transition accepted: {from_key} -> {to_key}."
        else:
            note = (
                f"Last transition rejected: {from_key} -> {to_key}; "
                f"reason={self.last_rejection_reason}. Re-localize from the current screenshot "
                "and avoid repeating the same coordinate."
            )
        return recorded, note

    def _record_transition(
        self,
        from_key: str,
        action: Dict[str, Any],
        to_key: str,
        landing_confidence: Optional["PageConfidence"] = None,
    ) -> bool:
        """Record a page transition: from_page → action → to_page.

        Phase 4.5c: increments transition_observations counter; tags saved
        transitions with observations count and confidence level; routes
        transient-landing transitions to rejected_transitions.
        """
        source = self.discovered_pages.get(from_key)
        target = self.discovered_pages.get(to_key)

        # Reject transitions involving new: page types (open-vocab) before other checks
        source_pt = _page_type_str(source) if source else ""
        target_pt = _page_type_str(target) if target else ""
        if source_pt.startswith("new:") or target_pt.startswith("new:"):
            item = {
                "from": from_key,
                "action": action,
                "to": to_key,
                "reason": "unreviewed page type",
            }
            self.rejected_transitions.append(item)
            self.last_rejection_reason = "unreviewed page type"
            self._log(f"  rejected transition (new: type): {from_key} -> {to_key}")
            return False

        rejection = self._transition_rejection_reason(source, action, target)
        self.last_rejection_reason = rejection
        if rejection:
            item = {
                "from": from_key,
                "action": action,
                "to": to_key,
                "reason": rejection,
            }
            self.rejected_transitions.append(item)
            self._log(f"  rejected transition: {from_key} -> {to_key}; {rejection}")
            return False

        # Phase 4.5c: update transition_observations
        action_intent = str(action.get("action") or action.get("action_type") or "")
        obs_key = (source_pt, action_intent, target_pt)
        obs_count = getattr(self, "transition_observations", {})
        obs_count[obs_key] = obs_count.get(obs_key, 0) + 1
        self.transition_observations = obs_count
        n_obs = obs_count[obs_key]

        conf_level = landing_confidence.level if landing_confidence is not None else "confident"

        trans_item: Dict[str, Any] = {
            "from": from_key,
            "action": action,
            "to": to_key,
            "observations": n_obs,
            "confidence": conf_level,
        }

        if conf_level == "transient":
            # Route to rejected_transitions — transient landing is too unreliable
            trans_item["reason"] = "low-confidence landing page"
            trans_item["low_confidence"] = True
            getattr(self, "rejected_transitions", []).append(trans_item)
            self._log(
                f"  rejected transition (transient landing): {from_key} -> {to_key}"
            )
            return False

        if conf_level == "low_confidence":
            trans_item["low_confidence"] = True

        self.transitions.append(trans_item)
        self._update_coverage_report()
        return True

    def _update_coverage_report(self) -> CoverageReport:
        if not hasattr(self, "coverage_targets"):
            self.coverage_targets = CoverageTarget()
        covered_pages = tuple(sorted({
            _page_type_str(page)
            for page in self.discovered_pages.values()
        }))
        missing_pages = tuple(
            page_type for page_type in self.coverage_targets.page_types
            if page_type not in covered_pages
        )

        covered_edges_set: set[tuple[str, str]] = set()
        for item in self.transitions:
            source_type = str(item.get("from") or "").partition(":")[0]
            target_type = str(item.get("to") or "").partition(":")[0]
            if source_type and target_type:
                covered_edges_set.add((source_type, target_type))
        covered_edges = tuple(sorted(covered_edges_set))
        missing_edges = tuple(edge for edge in self.coverage_targets.transitions if edge not in covered_edges_set)
        self.coverage_report = CoverageReport(
            covered_page_types=covered_pages,
            missing_page_types=missing_pages,
            covered_transitions=covered_edges,
            missing_transitions=missing_edges,
        )
        return self.coverage_report

    def _is_safe_action(self, page_info: Any, action: Dict[str, Any], reasoning: str = "") -> bool:
        """Delegate to SafetyPolicy; lazy-default for backward-compat calls with self=None."""
        safety = getattr(self, "safety", None) or _get_default_safety()
        return safety.is_safe_action(page_info, action, reasoning)

    @staticmethod
    def _action_text_has_unsafe_token(text: str, page_info: Any) -> bool:
        """Legacy static wrapper — uses default shopping SafetyPolicy."""
        safety = _get_default_safety()
        return safety.action_text_has_unsafe_token(text, page_info)

    @staticmethod
    def _unsafe_intent_mentioned(reasoning: str, page_info: Any = None) -> bool:
        """Legacy static wrapper — uses default shopping SafetyPolicy."""
        safety = _get_default_safety()
        return safety.unsafe_intent_mentioned(reasoning, page_info)

    def _rollback_from_risky_page(self, screen_width: int, screen_height: int) -> None:
        try:
            self.action_handler.execute({"_metadata": "do", "action": "Back"}, screen_width, screen_height)
            time.sleep(1)
            self._log("  repair: backed out from risky page")
        except Exception as exc:
            self._log(f"  repair failed: {exc}")

    def _transition_rejection_reason(
        self,
        source: Any,
        action: Dict[str, Any],
        target: Any,
    ) -> str:
        """Reject noisy exploration edges. Delegates to TransitionRuleEngine.

        NOTE: Also called as OfflineExplorer._transition_rejection_reason(None, source, action, target)
        in tests (first arg is 'self'=None).  Handles that by lazily building a default engine.
        """
        rules = getattr(self, "rules", None)
        if rules is None:
            from phone_agent.spatial.schema_registry import get_default_registry
            schema = get_default_registry().merged("shopping")
            safety = SafetyPolicy.from_schema(schema)
            rules = TransitionRuleEngine(schema, safety, "strict")
        return rules.rejection_reason(source, action, target)

    @staticmethod
    def _should_stop_after_rejected_transition(reason: str) -> bool:
        return TransitionRuleEngine.should_stop_after_rejection(reason)

    def _build_discovered_summary(self) -> str:
        """Build a summary of discovered pages for the VLM context."""
        if not self.discovered_pages:
            missing = ", ".join(self.coverage_targets.page_types)
            return f"尚未发现任何页面。请优先覆盖这些页面类型: {missing}"

        by_type: Dict[str, int] = {}
        for p in self.discovered_pages.values():
            t = _page_type_str(p)
            by_type[t] = by_type.get(t, 0) + 1

        type_lines = "\n".join(f"  - {t}: {c}个" for t, c in sorted(by_type.items()))
        coverage = self._update_coverage_report()
        missing_pages = ", ".join(coverage.missing_page_types) or "无"
        missing_edges = ", ".join(f"{a}->{b}" for a, b in coverage.missing_transitions) or "无"
        return (
            f"已发现 {len(self.discovered_pages)} 个页面:\n{type_lines}\n"
            f"缺失页面类型: {missing_pages}\n"
            f"缺失转移: {missing_edges}\n"
            "请优先选择能补齐缺失页面或缺失转移的安全动作，避免支付、提交订单和地址确认。"
        )

    def _build_active_frontier_hint(self, page_info: PageInfo | None) -> str:
        """Return a compact AMSG frontier hint for active exploration."""
        if not self.active_exploration or page_info is None:
            return ""
        node = self.semantics_extractor.node_from_exploration_page(
            {
                "app": page_info.app,
                "page_type": _page_type_str(page_info),
                "summary": page_info.semantic_summary,
                "elements": page_info.elements,
                "screenshot_hash": page_info.screenshot_hash,
            },
            fallback_app=self.app_name,
        )
        goal_page_types = set(self._update_coverage_report().missing_page_types)
        hypotheses = self.edge_hypothesis_generator.generate(node, goal_page_types=goal_page_types)
        ranked = self.active_builder.rank(hypotheses)[:3]
        if not ranked:
            return ""
        lines = [
            "[AMSG Active Exploration]",
            "Prefer validating one safe edge hypothesis that improves graph coverage:",
        ]
        for item in ranked:
            hyp = item.hypothesis
            lines.append(
                f"- {hyp.source_page_type} --{hyp.intent}/{hyp.semantic_target}--> "
                f"{hyp.expected_page_type}; score={item.score:.2f}; risk={hyp.risk}"
            )

        pt_str = _page_type_str(page_info)
        # Build hints from schema transitions for the current page type
        for transition in self.schema.outgoing(pt_str):
            hint = getattr(transition, "exploration_hint", "")
            if hint:
                lines.append(hint)

        lines.append("After action, stop before payment, order submission, login, or address confirmation.")
        return "\n".join(lines)

    # ── Phase 4 helpers ─────────────────────────────────────────

    def _action_has_trap_token(self, action: Dict[str, Any], reasoning: str = "") -> bool:
        """Return True if the action or reasoning contains a trap token."""
        safety = getattr(self, "safety", None)
        if safety is None:
            return False
        trap_tokens = getattr(safety, "trap_tokens", ())
        if not trap_tokens:
            return False
        action_text = json.dumps(action, ensure_ascii=False).lower()
        for token in trap_tokens:
            if token.lower() in action_text:
                return True
        # Also check element description in reasoning
        if reasoning:
            reasoning_lower = reasoning.lower()
            for token in trap_tokens:
                if token.lower() in reasoning_lower:
                    return True
        return False

    def _build_trap_avoid_hint(self) -> str:
        """Build a short avoid-hint based on recent trap edges."""
        trap_edges = getattr(self, "trap_edges", [])
        if not trap_edges:
            return ""
        recent_targets = []
        seen: set[str] = set()
        for edge in reversed(trap_edges[-10:]):
            tgt = str(edge.get("semantic_target", ""))
            if tgt and tgt not in seen:
                seen.add(tgt)
                recent_targets.append(tgt)
            if len(recent_targets) >= 3:
                break
        if not recent_targets:
            return ""
        targets_str = "、".join(recent_targets)
        return f"[注意] 请避免点击以下已知陷阱目标：{targets_str}"

    def _vlm_find_close_button(
        self, screenshot_b64: str, w: int, h: int, hint: str = ""
    ) -> tuple[int, int] | None:
        """Use the main VLM to find a close-button tap coordinate in a dialog.

        Returns (x, y) tap coords or None on any error / no result.
        """
        hint_text = f" {hint}" if hint else ""
        micro_prompt = (
            f"这是一个弹窗截图。{hint_text}"
            "请输出关闭/退出该弹窗的Tap坐标，格式为：do(action=\"Tap\", element=[x,y])。"
            "只输出操作，不要其他文字。"
        )
        try:
            context = [
                MessageBuilder.create_user_message(
                    text=micro_prompt, image_base64=screenshot_b64
                )
            ]
            response = self.vlm.request(context)
            action = parse_action(response.action)
            element = action.get("element")
            if isinstance(element, list) and len(element) >= 2:
                if isinstance(element[0], list):
                    element = element[0]
                x = int(float(element[0]))
                y = int(float(element[1]))
                return (x, y)
        except Exception as exc:
            self._log(f"  [vlm_find_close_button] error: {exc}")
        return None

    def _escape_and_restart(self, reason: str) -> bool:
        """Attempt to escape the current stuck/wandering state by restarting the app.

        Returns:
            True  — restart succeeded; exploration may continue.
            False — restart budget exhausted; exploration should end.
        """
        wdog_cfg = getattr(self, "watchdog", None)
        wdog_cfg_obj: WatchdogConfig = WatchdogConfig()
        if wdog_cfg is not None and hasattr(wdog_cfg, "_config"):
            wdog_cfg_obj = wdog_cfg._config

        max_restarts = wdog_cfg_obj.max_restarts
        restarts = getattr(self, "_restart_count", 0)

        if restarts >= max_restarts:
            self._log(
                f"  [escape] restart budget exhausted ({restarts}/{max_restarts}); "
                f"ending trajectory. reason={reason}"
            )
            return False

        self._log(f"  [escape] restarting app due to: {reason} (restart {restarts + 1}/{max_restarts})")
        try:
            self.device.launch_app(self.app_name, self.device_id)
            time.sleep(4)
        except Exception as exc:
            self._log(f"  [escape] launch_app failed: {exc}")

        self._restart_count = restarts + 1
        wd = getattr(self, "watchdog", None)
        if wd is not None:
            wd.reset()
        pw = getattr(self, "progress_watchdog", None)
        if pw is not None:
            pw.reset_after_restart()
        return True

    def _log(self, msg: str):
        if self.verbose:
            print(msg)

    # ── Persistence ─────────────────────────────────────────────

    def _save_results(self, traj: Trajectory):
        """Save all exploration data to JSON files."""
        timestamp = int(time.time())

        pages_data = {
            "app": self.app_name,
            "task": self.task_description,
            "explored_at": datetime.now().isoformat(),
            "total_pages": len(self.discovered_pages),
            "coverage": self._update_coverage_report().to_dict(),
            "pages": [
                {
                    "page_type": _page_type_str(p),
                    "summary": p.semantic_summary,
                    "elements": p.elements,
                    "screenshot_hash": p.screenshot_hash,
                    "app": p.app,
                }
                for p in self.discovered_pages.values()
            ],
        }
        pages_path = self.storage_dir / f"{self.app_name}_explore_{timestamp}.json"
        with open(pages_path, "w", encoding="utf-8") as f:
            json.dump(pages_data, f, ensure_ascii=False, indent=2)
        self._log(f"  saved: {pages_path.name}")

        traj_data = {
            "task": traj.task,
            "app": traj.app,
            "success": traj.success,
            "total_steps": len(traj.steps),
            "steps": [
                {
                    "step": i + 1,
                    "page_type": _page_type_str(s.page_info),
                    "page_summary": s.page_info.semantic_summary,
                    "page_elements": s.page_info.elements,
                    "screenshot_hash": s.page_info.screenshot_hash,
                    "action": s.action,
                    "thinking": s.action_thinking[:200],
                    "timestamp": s.timestamp,
                }
                for i, s in enumerate(traj.steps)
            ],
        }
        traj_path = self.storage_dir / f"{self.app_name}_explore_trajectory_{timestamp}.json"
        with open(traj_path, "w", encoding="utf-8") as f:
            json.dump(traj_data, f, ensure_ascii=False, indent=2)
        self._log(f"  saved: {traj_path.name}")

        trans_path = None
        if self.transitions or self.rejected_transitions:
            transitions_data: Dict[str, Any] = {
                "app": self.app_name,
                "task": self.task_description,
                "total_transitions": len(self.transitions),
                "coverage": self._update_coverage_report().to_dict(),
                "transitions": self.transitions,
                "rejected_transitions": self.rejected_transitions,
            }
            proposals = getattr(self, "page_type_proposals", {})
            if proposals:
                transitions_data["page_type_proposals"] = proposals

            # Phase 4: interference + trap metadata
            interference_events = getattr(self, "interference_events", [])
            if interference_events:
                transitions_data["interference_events"] = interference_events

            trap_edges = getattr(self, "trap_edges", [])
            if trap_edges:
                transitions_data["trap_edges"] = trap_edges

            interference_log = getattr(self, "interference_log", [])
            if interference_log:
                transitions_data["interference_log"] = interference_log

            # Aggregate interference sources
            src_counts: Dict[str, int] = {}
            for evt in interference_events:
                src_pt = evt.get("page_type", "unknown") if isinstance(evt, dict) else "unknown"
                src_counts[src_pt] = src_counts.get(src_pt, 0) + 1
            if src_counts:
                transitions_data["interference_sources"] = src_counts

            # Phase 4.5c: transition_observations summary (convert tuple keys to strings)
            obs = getattr(self, "transition_observations", {})
            if obs:
                obs_summary = {
                    f"{k[0]}|{k[1]}|{k[2]}": v
                    for k, v in obs.items()
                }
                transitions_data["transition_observations"] = obs_summary

            trans_path = self.storage_dir / f"{self.app_name}_explore_transitions_{timestamp}.json"
            with open(trans_path, "w", encoding="utf-8") as f:
                json.dump(transitions_data, f, ensure_ascii=False, indent=2)
            self._log(f"  saved: {trans_path.name} ({len(self.transitions)} transitions)")

        if self.auto_import_graph:
            self._import_saved_graph(pages_path, trans_path)

    def _import_saved_graph(self, pages_path: Path, transitions_path: Path | None) -> None:
        """Import saved exploration artifacts into SpatialGraphMemory."""
        owns_graph_store = self.graph_store is None
        graph_store = self.graph_store
        if graph_store is None:
            from ..graph_store import GraphStore

            graph_store = GraphStore()

        try:
            from ..spatial_graph_memory import SpatialGraphMemory

            memory = SpatialGraphMemory(graph_store)
            states, edges, staging_report = memory.import_exploration_staging(pages_path, transitions_path)
            promote_report = memory.promote_staging_to_canonical(states, edges, persist=True)
            self.last_import_result = {
                "staging": staging_report.to_dict(),
                "promoted": promote_report.to_dict(),
                "pages_imported": staging_report.canonical_pages,
                "transitions_imported": staging_report.transitions_promoted,
            }
            self._log(
                "  imported spatial graph: "
                f"{self.last_import_result['pages_imported']} pages, "
                f"{self.last_import_result['transitions_imported']} transitions"
            )
        finally:
            if owns_graph_store and graph_store:
                graph_store.close()


def _build_taobao_task() -> str:
    """Return the default Taobao exploration task (from shopping schema YAML)."""
    try:
        from phone_agent.spatial.schema_registry import get_default_registry
        schema = get_default_registry().load("shopping")
        task = schema.exploration.default_task
        if task:
            return task
    except Exception:
        pass
    return (
        "覆盖淘宝购物核心空间骨架：home -> search_input -> search_result -> "
        "product_detail -> spec_selection -> cart。"
        "只执行安全探索动作，不提交订单、不支付、不确认地址。"
        "如果进入登录、支付、地址或订单确认页，立刻返回。"
    )
