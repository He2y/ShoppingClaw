"""Main PhoneAgent class for orchestrating phone automation."""

import json
import re
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    RateLimitError,
)

from phone_agent.actions import ActionHandler
from phone_agent.actions.handler import do, finish, parse_action
from phone_agent.config import get_messages, get_system_prompt
from phone_agent.clarify import ClarificationAgent
from phone_agent.core.spec_guard import SpecGuard
from phone_agent.device_factory import get_device_factory
from phone_agent.model import ModelClient, ModelConfig
from phone_agent.model.adapters import ModelType, detect_model_type, get_adapter
from phone_agent.model.client import MessageBuilder
from phone_agent.memory.core import ProductStatus
from phone_agent.memory.offline_explorer import PageClassifier, ShoppingPageType
from phone_agent.task_plan import TaskPlan
from phone_agent.verification_detector import (
    detect_verification,
    detect_verification_from_vlm,
)


@dataclass
class AgentConfig:
    """Configuration for the PhoneAgent."""

    max_steps: int = 100
    device_id: str | None = None
    lang: str = "cn"
    system_prompt: str | None = None
    verbose: bool = True
    # Memory configuration
    enable_memory: bool = True
    memory_dir: str = "memory_db"
    user_id: str = "default"
    # Model type: "auto" for auto-detect, or explicit type like "qwenvl", "uitars", etc.
    model_type: str = "auto"
    # Trace configuration
    trace_enabled: bool = False
    trace_dir: str = "gui_trace"

    def __post_init__(self):
        if self.system_prompt is None:
            self.system_prompt = get_system_prompt(self.lang)


@dataclass
class StepResult:
    """Result of a single agent step."""

    success: bool
    finished: bool
    action: dict[str, Any] | None
    thinking: str
    message: str | None = None


class PhoneAgent:
    """
    AI-powered agent for automating Android phone interactions.

    The agent uses a vision-language model to understand screen content
    and decide on actions to complete user tasks.

    Now with personalized memory support for learning user preferences
    and providing more intelligent assistance.

    Args:
        model_config: Configuration for the AI model.
        agent_config: Configuration for the agent behavior.
        confirmation_callback: Optional callback for sensitive action confirmation.
        takeover_callback: Optional callback for takeover requests.

    Example:
        >>> from phone_agent import PhoneAgent
        >>> from phone_agent.model import ModelConfig
        >>>
        >>> model_config = ModelConfig(base_url="http://localhost:8000/v1")
        >>> agent = PhoneAgent(model_config)
        >>> agent.run("Open WeChat and send a message to John")
    """

    def __init__(
        self,
        model_config: ModelConfig | None = None,
        agent_config: AgentConfig | None = None,
        confirmation_callback: Callable[[str], bool] | None = None,
        takeover_callback: Callable[[str], None] | None = None,
        clarification_callback: Callable[[str], str] | None = None,
        status_callback: Callable | None = None,
    ):
        self.clarification_callback = clarification_callback
        self.status_callback = status_callback
        self.model_config = model_config or ModelConfig()
        self.agent_config = agent_config or AgentConfig()

        self.model_client = ModelClient(self.model_config)

        # Determine model type and get adapter
        self._model_type = self._resolve_model_type()
        self._adapter = get_adapter(self._model_type)

        # Initialize the appropriate action handler based on model type
        if self._model_type == ModelType.UITARS:
            from phone_agent.actions.handler_uitars import UITarsActionHandler
            self._specialized_handler = UITarsActionHandler(
                device_id=self.agent_config.device_id,
                confirmation_callback=confirmation_callback,
                takeover_callback=takeover_callback,
            )
        elif self._model_type == ModelType.QWENVL:
            from phone_agent.actions.handler_qwenvl import QwenVLActionHandler
            self._specialized_handler = QwenVLActionHandler(
                device_id=self.agent_config.device_id,
                confirmation_callback=confirmation_callback,
                takeover_callback=takeover_callback,
            )
        elif self._model_type == ModelType.MAIUI:
            from phone_agent.actions.handler_maiui import MAIUIActionHandler
            self._specialized_handler = MAIUIActionHandler(
                device_id=self.agent_config.device_id,
                confirmation_callback=confirmation_callback,
                takeover_callback=takeover_callback,
            )
        elif self._model_type == ModelType.GUIOWL:
            from phone_agent.actions.handler_guiowl import GUIOwlActionHandler
            self._specialized_handler = GUIOwlActionHandler(
                device_id=self.agent_config.device_id,
                confirmation_callback=confirmation_callback,
                takeover_callback=takeover_callback,
            )
        else:
            self._specialized_handler = None

        # Always keep the default AutoGLM handler as fallback
        self.action_handler = ActionHandler(
            device_id=self.agent_config.device_id,
            confirmation_callback=confirmation_callback,
            takeover_callback=takeover_callback,
        )

        self._context: list[dict[str, Any]] = []
        self._step_count = 0
        self._current_task = ""
        self._anomaly_consecutive = 0
        # Cooperative cancellation + per-step telemetry surface for frontends
        # (WebUI streams these; CLI ignores them).
        self.abort_requested = False
        self.step_observer: Callable[[dict[str, Any]], None] | None = None
        self.last_step_info: dict[str, Any] = {}
        self._step_telemetry: dict[str, Any] = {}

        # Load externalized shopping config (JSON with code defaults)
        from phone_agent.config.shopping_config import ShoppingConfig
        self._shopping_config = ShoppingConfig.load()
        self._spec_guard = SpecGuard(self._shopping_config)

        # Initialize tracer if enabled
        self.tracer = None
        if self.agent_config.trace_enabled:
            from phone_agent.tracer import GUITracer
            self.tracer = GUITracer(trace_dir=self.agent_config.trace_dir)

        # Initialize memory manager if enabled
        self.memory_manager = None
        if self.agent_config.enable_memory:
            try:
                from phone_agent.memory import MemoryManager

                # Check if we have offline imported knowledge and use that path
                # Note: memory_manager appends /user_id to the storage_dir, so we need to handle that
                # To read from memory_db_offline_import directly (which has no user_id subfolder),
                # we just pass its parent and the folder name as user_id
                import os
                if os.path.exists("memory_db_offline_import/embeddings.npy"):
                    storage_dir = "."
                    user_id = "memory_db_offline_import"
                else:
                    storage_dir = self.agent_config.memory_dir
                    user_id = self.agent_config.user_id

                self.memory_manager = MemoryManager(
                    storage_dir=storage_dir,
                    user_id=user_id,
                    enable_auto_extract=True,
                )
                if self.agent_config.verbose:
                    model_type_name = self._model_type.value
                    print(f"🧠 个性化记忆系统已启用 | 模型适配器: {model_type_name} | 库: {user_id}")
            except Exception as e:
                if self.agent_config.verbose:
                    print(f"⚠️ 记忆系统初始化失败: {e}")

        # Initialize PageClassifier for semantic extraction
        self.page_classifier: PageClassifier | None = None
        if self.agent_config.enable_memory:
            try:
                self.page_classifier = PageClassifier()
                if self.agent_config.verbose:
                    print(
                        "PageClassifier initialized for semantic extraction "
                        f"| source={self.page_classifier.source} "
                        f"| model={self.page_classifier.model}"
                    )
            except Exception as e:
                if self.agent_config.verbose:
                    print(f"PageClassifier initialization failed: {e}")

        # Initialize clarification sub-agent for shopping task ambiguity detection
        self.clarification_agent: ClarificationAgent | None = None
        try:
            self.clarification_agent = ClarificationAgent()
        except Exception as e:
            if self.agent_config.verbose:
                print(f"⚠️ 澄清子代理初始化失败: {e}")

        # Initialize Action Advisor (graph as navigation advisor)
        self.action_advisor: ActionAdvisor | None = None
        if self.memory_manager:
            try:
                from phone_agent.spatial.action_advisor import ActionAdvisor
                sgm = self.memory_manager.spatial_graph_memory
                lifecycle = getattr(sgm, "_edge_lifecycle", None)
                self.action_advisor = ActionAdvisor(sgm, lifecycle)
            except Exception:
                pass

    def _resolve_model_type(self) -> ModelType:
        """Resolve model type from config or auto-detect from model name."""
        model_type_str = self.agent_config.model_type.lower()

        type_map = {
            "autoglm": ModelType.AUTOGLM,
            "uitars": ModelType.UITARS,
            "qwenvl": ModelType.QWENVL,
            "maiui": ModelType.MAIUI,
            "guiowl": ModelType.GUIOWL,
        }

        if model_type_str in type_map:
            return type_map[model_type_str]

        # Auto-detect from model name
        return detect_model_type(self.model_config.model_name)

    def run(self, task: str) -> str:
        """
        Run the agent to complete a task.

        Args:
            task: Natural language description of the task.

        Returns:
            Final message from the agent.
        """
        self._context = []
        self._step_count = 0
        self._current_task = task
        self.abort_requested = False
        self._last_thinking = ""
        self._last_state_hash: str | None = None
        self._last_user_reply: str | None = None
        self._graph_fail_count: int = 0
        self._graph_fail_page: str = ""
        self._verification_consecutive: int = 0
        self._anomaly_consecutive: int = 0
        self._task_plan: TaskPlan | None = None
        self._step_summaries: list[str] = []

        # Clear action history for QwenVL handler/adapter
        if self._specialized_handler is not None and hasattr(self._specialized_handler, 'clear_history'):
            self._specialized_handler.clear_history()
        if hasattr(self._adapter, 'clear_history'):
            self._adapter.clear_history()

        # Start tracing
        if self.tracer:
            self.tracer.start_task(task, model=self.model_config.model_name)

        # Start memory tracking + task planning
        self._vlm_plan: dict[str, Any] = {}
        if self.memory_manager:
            self.memory_manager.start_task(task)
            self._vlm_plan = self._vlm_pre_plan(task)
            if self._vlm_plan:
                self.memory_manager.set_vlm_plan(self._vlm_plan)
                self._task_plan = TaskPlan.from_vlm_output(task, self._vlm_plan)
            else:
                self._task_plan = TaskPlan(original_task=task)
        else:
            self._task_plan = TaskPlan(original_task=task)

        # First step with user prompt
        result = self._execute_step(task, is_first=True)

        if result.finished:
            if self.memory_manager:
                self.memory_manager.end_task(
                    success=result.success,
                    result=result.message or "Task completed",
                    end_state_id=self._last_state_hash,
                )
            if self.tracer:
                self.tracer.end_task(
                    result=result.message or "Task completed",
                    total_steps=self._step_count,
                )
            return result.message or "Task completed"

        # Continue until finished, aborted, or max steps reached
        while self._step_count < self.agent_config.max_steps:
            if self.abort_requested:
                break
            result = self._execute_step(is_first=False)

            if result.finished:
                if self.memory_manager:
                    self.memory_manager.end_task(
                        success=result.success,
                        result=result.message or "Task completed",
                        end_state_id=self._last_state_hash,
                    )
                if self.tracer:
                    self.tracer.end_task(
                        result=result.message or "Task completed",
                        total_steps=self._step_count,
                    )
                return result.message or "Task completed"

        # Aborted by user (frontend stop button)
        if self.abort_requested:
            if self.memory_manager:
                self.memory_manager.end_task(
                    success=False, result="Aborted by user", end_state_id=self._last_state_hash
                )
            if self.tracer:
                self.tracer.end_task(result="Aborted by user", total_steps=self._step_count)
            return "任务已终止"

        # Task timeout
        if self.memory_manager:
            self.memory_manager.end_task(success=False, result="Max steps reached", end_state_id=self._last_state_hash)
        if self.tracer:
            self.tracer.end_task(result="Max steps reached", total_steps=self._step_count)

        return "Max steps reached"

    def step(self, task: str | None = None) -> StepResult:
        """
        Execute a single step of the agent.

        Useful for manual control or debugging.

        Args:
            task: Task description (only needed for first step).

        Returns:
            StepResult with step details.
        """
        is_first = len(self._context) == 0

        if is_first and not task:
            raise ValueError("Task is required for the first step")

        return self._execute_step(task, is_first)

    def reset(self) -> None:
        """Reset the agent state for a new task."""
        self._context = []
        self._step_count = 0


    # SpecGuard is now in phone_agent.core.spec_guard — initialized in __init__

    def _compress_history(self) -> None:
        """Replace old assistant messages with their summaries.

        Keeps the last ``_KEEP_FULL`` assistant messages in full detail;
        older ones are collapsed to ``[执行摘要] ...`` one-liners.
        This mirrors UI-Copilot's memory decoupling: detailed traces are
        stored in the tracer, only summaries remain in the dialogue.
        """
        _KEEP_FULL = 2
        assistant_indices = [
            i for i, m in enumerate(self._context) if m.get("role") == "assistant"
        ]
        if len(assistant_indices) <= _KEEP_FULL:
            return
        for idx in assistant_indices[:-_KEEP_FULL]:
            msg = self._context[idx]
            content = msg.get("content", "")
            if isinstance(content, str) and not content.startswith("[执行摘要]"):
                summary = self._extract_summary_from_content(content)
                msg["content"] = f"[执行摘要] {summary}" if summary else "[执行摘要] (步骤已压缩)"

    @staticmethod
    def _extract_summary_from_content(content: str) -> str:
        """Extract summary from an assistant message's content string."""
        if "<summary>" in content:
            try:
                start = content.index("<summary>") + len("<summary>")
                end = content.index("</summary>", start)
                return content[start:end].strip()
            except ValueError:
                pass
        # Fallback: use last line of thinking
        if "<think>" in content:
            try:
                start = content.index("<think>") + len("<think>")
                end = content.index("</think>", start)
                thinking = content[start:end].strip()
                lines = [l.strip() for l in thinking.split("\n") if l.strip()]
                return lines[-1] if lines else ""
            except ValueError:
                pass
        return content[:80] if content else ""

    def _needs_vlm(
        self,
        available_actions: list | None,
        page_type: str = "",
    ) -> bool:
        """Decide whether this step requires VLM (Full Path) or can use Fast Path.

        Fast Path is allowed only when a Grounded Action with high confidence
        matches the current plan step's target page AND no pending semantic
        input is required.
        """
        if not self._task_plan or not available_actions:
            return True
        plan_step = self._task_plan.current_step()
        if plan_step is None:
            return True

        effective_target = self._effective_plan_target(page_type)
        for hint in available_actions:
            if (
                hint.target_page == effective_target
                and hint.is_fast_executable()
            ):
                return False
        return True

    def _effective_plan_target(self, page_type: str) -> str | None:
        """Plan target for fast dispatch; advances past already-reached steps.

        The plan only marks a step done on explicit progress, so while we
        stand ON the current step's target page (e.g. search_input), the raw
        target would match arrival edges instead of the next move — the
        compound search edge (→ search_result) was never selected because of
        this.
        """
        if not self._task_plan:
            return None
        step = self._task_plan.current_step()
        if step is None:
            return None
        if page_type and step.target_page == page_type:
            steps = self._task_plan.steps
            try:
                idx = steps.index(step)
            except ValueError:
                return step.target_page
            if idx + 1 < len(steps):
                return steps[idx + 1].target_page
        return step.target_page

    def _select_fast_action(self, available_actions: list, page_type: str = "") -> Any | None:
        """Pick the best Grounded Action matching the effective plan target."""
        effective_target = self._effective_plan_target(page_type)
        if effective_target is None:
            return None
        best = None
        for hint in available_actions:
            if (
                hint.target_page == effective_target
                and hint.is_fast_executable()
                and (best is None or hint.confidence > best.confidence)
            ):
                best = hint
        return best

    _NON_COORDINATE_ACTIONS = frozenset({"Compound", "Type", "Launch", "Back", "Wait", "Home"})

    def _execute_fast_path(
        self,
        hint: Any,
        screenshot: Any,
        current_app: str | None,
        ui_hash: str,
        semantic_layout: str,
        source_page_type: str = "",
    ) -> StepResult | None:
        """Execute a Grounded Action without VLM call (~0.5s).

        Returns StepResult on success, or None if postcondition
        verification fails (caller falls through to Full Path).
        """
        action = self.action_advisor.get_fast_action(hint)
        self._tele(
            dispatch="fast_path",
            fast_action=str(hint.description),
            expected_postcondition=str(hint.target_page),
        )

        # Fill slots for Compound actions (e.g. <query> → "无线耳机")
        if self._task_plan and self._task_plan.goal_slots:
            action = self._fill_action_slots(action, self._task_plan.goal_slots)

        if self.agent_config.verbose:
            print(f"🚀 Fast Path: {hint.action_type} → {hint.target_page} (conf={hint.confidence:.2f})")

        result = self.action_handler.execute(action, screenshot.width, screenshot.height)
        if not result.success:
            # Device-level execution failure is not transition evidence —
            # fall back to the VLM without polluting lifecycle statistics.
            if self.agent_config.verbose:
                print(f"⚠️ Fast Path execution failed: {result.message}")
            self._tele(dispatch="fast_path_fallback", actual_postcondition="(执行失败)")
            return None

        # Postcondition verification
        device_factory = get_device_factory()
        new_screenshot = device_factory.get_screenshot(self.agent_config.device_id)

        new_page_type = None
        if self.page_classifier and new_screenshot and not new_screenshot.is_sensitive:
            try:
                pt, _, _ = self.page_classifier.classify(
                    new_screenshot.base64_data, new_screenshot.width, new_screenshot.height,
                )
                new_page_type = pt.value
            except Exception:
                pass

        if new_page_type and new_page_type != hint.target_page:
            if self.agent_config.verbose:
                print(f"⚠️ Fast Path postcondition mismatch: expected={hint.target_page}, actual={new_page_type}")
            # Clear stale RuntimeDAG to force PageClassifier on next step
            if self.memory_manager and hasattr(self.memory_manager, "_runtime_dag"):
                self.memory_manager._runtime_dag = None
            # Record the observed (wrong) target so the lifecycle can demote
            # the edge — previously failures were never recorded and bad
            # edges stayed promoted forever.
            self._record_and_evolve(source_page_type, action, new_page_type)
            self._tele(dispatch="fast_path_fallback", actual_postcondition=new_page_type or "")
            return None  # Fall through to Full Path

        # Success — record summary (plan advancement handled at next step start)
        desc = f"[Fast] {hint.description}"
        self._step_summaries.append(desc)
        self._tele(actual_postcondition=new_page_type or "")

        if self.memory_manager:
            self.memory_manager.add_step(
                thinking=desc, action=action, screenshot_app=current_app,
            )
            if hasattr(self.memory_manager, "update_state_and_transition"):
                self.memory_manager.update_state_and_transition(
                    screenshot_hash=ui_hash,
                    semantic_layout=semantic_layout,
                    action=action,
                    task=self._current_task,
                    expected_postcondition=hint.target_page,
                )

        # Lifecycle bookkeeping is handled exclusively by the pending
        # transition set above and verified at t+1 (single channel) —
        # recording here as well double-counted every Fast Path success,
        # and `new_page_type or hint.target_page` fabricated observations
        # when classification failed.

        if self.tracer:
            self.tracer.record_step(
                step=self._step_count,
                screenshot_base64=screenshot.base64_data,
                model_raw_output=f"[Fast Path: {hint.action_type} → {hint.target_page}]",
                action=action,
                finished=False,
            )

        return StepResult(
            success=True,
            finished=False,
            action=action,
            thinking=desc,
            message=desc,
        )

    def _record_and_evolve(
        self,
        source_page_type: str,
        action: dict[str, Any],
        observed_page_type: str | None = None,
    ) -> None:
        """Feed an in-step observation to EdgeLifecycleManager.

        Only used when a step has direct evidence the pending-transition
        channel cannot capture — currently the Fast Path postcondition
        mismatch (the wrong observed page must reach the lifecycle so the
        edge can be demoted). Successful transitions are recorded solely
        via the pending transition verified at t+1.
        """
        if not observed_page_type:
            return
        if not self.memory_manager:
            return
        lifecycle = getattr(
            getattr(self.memory_manager, "spatial_graph_memory", None),
            "_edge_lifecycle", None,
        )
        if not lifecycle:
            return

        action_type = str(action.get("action", ""))
        action_target = str(action.get("element", action.get("text", "")))

        try:
            lifecycle.record_outcome(
                source_page_type=source_page_type or "",
                intent=action_type,
                action_target=action_target,
                observed_target=observed_page_type,
            )
        except Exception:
            pass

    def _advance_lifecycle_step(self) -> None:
        """Advance the lifecycle's global step counter (once per agent step)."""
        memory_manager = getattr(self, "memory_manager", None)
        lifecycle = getattr(
            getattr(memory_manager, "spatial_graph_memory", None),
            "_edge_lifecycle", None,
        )
        if lifecycle is None:
            return
        try:
            lifecycle.advance_step()
        except Exception:
            pass

    @staticmethod
    def _fill_action_slots(action: dict, slots: dict[str, str]) -> dict:
        """Fill <placeholder> slots inside Compound action steps.

        Substring-safe: "搜索<query>" is filled too, matching the RuntimeDAG
        path's _fill_runtime_slots semantics (the old whole-string check left
        partial templates unfilled and the handler then rejected them).
        """
        if action.get("action") != "Compound" or not slots:
            return action

        def _sub(text: str) -> str:
            return re.sub(
                r"<([a-zA-Z0-9_]+)>",
                lambda m: str(slots.get(m.group(1)) or m.group(0)),
                text,
            )

        filled_steps = []
        for step in action.get("actions", []):
            step = dict(step)
            if isinstance(step.get("text"), str):
                step["text"] = _sub(step["text"])
            filled_steps.append(step)
        return {**action, "actions": filled_steps}

    def _handle_verification_takeover(
        self,
        screenshot: Any,
        current_app: str | None,
        message: str,
        verification_type: str,
    ) -> StepResult:
        """Auto-trigger Take_over when a verification page is detected.

        Constructs a Take_over action and routes it through the existing
        action handler, which blocks until the user completes the manual
        operation (``input()`` in CLI, ``Event.wait()`` in WebUI).
        """
        if self.agent_config.verbose:
            print(f"\n{'=' * 50}")
            print(f"🔒 人机验证检测 (类型: {verification_type})")
            print(f"   {message}")
            print(f"   连续检测次数: {self._verification_consecutive}")
            print(f"{'=' * 50}")

        action = do(action="Take_over", message=message)

        # Execute through the default handler (has the takeover_callback)
        self.action_handler.execute(action, screenshot.width, screenshot.height)

        # Brief pause for the post-verification page transition
        time.sleep(2)

        thinking = (
            f"[自动检测] 页面类型: {verification_type}，"
            f"检测到需要人工操作，已暂停等待用户完成"
        )

        # Record in memory
        if self.memory_manager:
            self.memory_manager.add_step(
                thinking=thinking, action=action, screenshot_app=current_app,
            )

        # Record in tracer
        if self.tracer:
            self.tracer.record_step(
                step=self._step_count,
                screenshot_base64=screenshot.base64_data,
                model_raw_output=f"[Auto-detected: {verification_type}]",
                action=action,
                finished=False,
            )

        return StepResult(
            success=True,
            finished=False,
            action=action,
            thinking=thinking,
            message=message,
        )

    def _try_graph_shortcut(
        self,
        context_data: dict[str, Any],
        mode: str,
        screenshot: Any,
        current_app: str | None,
        ui_hash: str,
        semantic_layout: str,
    ) -> StepResult | None:
        """Try to execute a graph-planned action directly, skipping VLM.

        Returns StepResult if the graph shortcut was executed, or None to
        fall through to VLM inference.

        Three outcomes:
        1. Direct execution — high-confidence structural action (navigate,
           compound search, back). Returns StepResult.
        2. VLM co-pilot — graph provides direction but the VLM must verify
           the screenshot (product/spec selection). Returns None, but graph
           hint is already in context_data["graph_hint"] and will be
           injected into the per-step VLM message.
        3. Explore mode — no graph route found. Returns None.
        """
        next_action = (
            context_data.get("next_actions", [None])[0]
            if context_data.get("next_actions") else None
        )
        if not next_action or mode not in {"navigate", "verify_with_vlm"}:
            if self.agent_config.verbose:
                print("🧭 图谱: 无路由，VLM 探索模式")
            return None

        # Check if the same graph action failed too many times on this page.
        # After 2 consecutive failures, clear the RuntimeDAG and let the VLM
        # handle this page — the graph coordinates may not match the device.
        current_page = context_data.get("belief", {})
        current_page_type = ""
        if current_page and current_page.get("candidates"):
            current_page_type = current_page["candidates"][0].get("page_type", "")
        repair_hint = context_data.get("repair_hint")
        if repair_hint and repair_hint.get("action") in ("rollback", "replan"):
            if current_page_type == self._graph_fail_page:
                self._graph_fail_count += 1
            else:
                self._graph_fail_count = 1
                self._graph_fail_page = current_page_type
            if self._graph_fail_count >= 2:
                if self.agent_config.verbose:
                    print(
                        f"⚠️ 图谱动作在 {current_page_type} 连续失败 {self._graph_fail_count} 次，"
                        f"切换到 VLM 探索模式"
                    )
                if self.memory_manager and self.memory_manager._runtime_dag:
                    self.memory_manager._runtime_dag = None
                self._graph_fail_count = 0
                return None
        else:
            self._graph_fail_count = 0
            self._graph_fail_page = ""

        if next_action.get("_requires_vlm_verification"):
            if self.agent_config.verbose:
                print(
                    f"🤝 [VLM-Graph Co-pilot] 图谱建议 {next_action.get('type')}"
                    f"→{next_action.get('postcondition', '')}，转交 VLM 验证"
                )
            return None

        confidence = next_action.get("confidence", 1.0)
        action_type = next_action.get("type", "")
        if confidence < 0.7:
            if self.agent_config.verbose:
                print(f"🧭 图谱: 置信度 {confidence:.2f} < 0.7，VLM 探索")
            return None

        can_compile = (
            action_type in self._NON_COORDINATE_ACTIONS
            or self._compile_spatial_shortcut_action(
                next_action,
                screen_width=screenshot.width,
                screen_height=screenshot.height,
            )[0] is not None
        )
        if not can_compile:
            if self.agent_config.verbose:
                print(f"🧭 图谱: 动作 {action_type} 无法编译，VLM 探索")
            return None

        # Fill runtime slots (e.g. <query> → actual search term)
        goal_slots = context_data.get("goal_spec", {}).get("slots", {})
        if goal_slots and self.memory_manager:
            next_action = self.memory_manager.spatial_graph_memory._fill_runtime_slots(
                next_action, goal_slots
            )
        if next_action.get("confidence", 1.0) < 0.7:
            if self.agent_config.verbose:
                print(f"🧭 图谱: 槽位填充后置信度不足，VLM 探索")
            return None

        # Build executable action dict
        action = self._build_executable_action(next_action, screenshot)
        postcondition = next_action.get("postcondition", "")

        # Validate: Compound actions must have sub-actions with filled slots
        if action.get("action") == "Compound":
            sub_actions = action.get("actions", [])
            if not sub_actions:
                if self.agent_config.verbose:
                    print("⚠️ 图谱: Compound 动作缺少子动作列表，VLM 探索")
                return None
            for sub in sub_actions:
                text = sub.get("text", "")
                if isinstance(text, str) and text.startswith("<") and text.endswith(">"):
                    if self.agent_config.verbose:
                        print(f"⚠️ 图谱: Compound 子动作槽位未填充 ({text})，VLM 探索")
                    return None

        if self.agent_config.verbose:
            print(
                f"🚀 图谱导航: {action_type} → {postcondition} "
                f"(conf={next_action.get('confidence', 0):.2f})"
            )

        # Execute
        try:
            result = self.action_handler.execute(
                action, screenshot.width, screenshot.height
            )
        except Exception as e:
            # Never route exceptions through finish(): the handler reports
            # finish as success=True, which would mark the task successful
            # and flush the failed trajectory into Neo4j (quality gate 1).
            if self.agent_config.verbose:
                traceback.print_exc()
            return StepResult(
                success=False,
                finished=True,
                action=action,
                thinking=f"[Graph: {action_type}→{postcondition}]",
                message=f"图谱捷径执行异常: {e}",
            )

        # Record
        finished = action.get("_metadata") == "finish" or result.should_finish
        self._last_thinking = f"[Graph: {action_type}→{postcondition}]"
        if self.memory_manager:
            if hasattr(self.memory_manager, "mark_planned_action_executed"):
                self.memory_manager.mark_planned_action_executed(action, success=result.success)
            self.memory_manager.add_step(
                thinking=self._last_thinking,
                action=action,
                screenshot_app=current_app,
            )
            self.memory_manager.update_state_and_transition(
                screenshot_hash=ui_hash,
                semantic_layout=semantic_layout,
                action=action,
                task=self._current_task,
                expected_postcondition=postcondition,
            )
        return StepResult(
            success=result.success,
            finished=finished,
            action=action,
            thinking=self._last_thinking,
            message=result.message or action.get("message"),
        )

    def _build_executable_action(
        self,
        graph_action: dict[str, Any],
        screenshot: Any,
    ) -> dict[str, Any]:
        """Convert a graph next_action into an executable action dict.

        For Compound actions: parses target_desc to extract the sub-actions
        list. For coordinate-based actions: compiles through SpatialModelBridge.
        """
        import ast as _ast

        action = {
            "_metadata": "do",
            "action": graph_action["type"],
        }
        sanitized = self.memory_manager.spatial_graph_memory.sanitize_action_for_context(
            graph_action
        ) if self.memory_manager else graph_action
        if sanitized.get("target"):
            action["semantic_target"] = sanitized["target"]
        if graph_action.get("postcondition"):
            action["_expected_postcondition"] = graph_action["postcondition"]

        # Merge params from target_desc (contains sub-actions for Compound)
        try:
            params = _ast.literal_eval(graph_action.get("target_desc", "{}"))
            if isinstance(params, dict):
                action.update(params)
        except (SyntaxError, ValueError):
            pass

        compiled, _ = self._compile_spatial_shortcut_action(
            graph_action,
            screen_width=screenshot.width,
            screen_height=screenshot.height,
        )
        if compiled is not None:
            compiled["_expected_postcondition"] = graph_action.get("postcondition", "")
            return compiled

        return action

    def _compile_spatial_shortcut_action(
        self,
        best_action: dict,
        *,
        screen_width: int,
        screen_height: int,
    ):
        """Compile a graph shortcut through the AMSG model-agnostic bridge."""
        from phone_agent.spatial.model_bridge import SpatialModelBridge

        semantic_action = SpatialModelBridge.semantic_action_from_next_action(best_action)
        action = SpatialModelBridge.compile_to_autoglm_action(
            semantic_action,
            screen_width=screen_width,
            screen_height=screen_height,
            source_model=self._model_type,
        )
        if action is None:
            return None, SpatialModelBridge.grounding_instruction(semantic_action)
        return action, ""

    _PRICE_TOKEN_RE = re.compile(
        r"\d+\s*[-~～到至]\s*\d+\s*元?"
        r"|\d+\s*元\s*(?:以内|以下|以上|之内|左右)?"
        r"|(?:低于|高于|不超过|不低于)\s*\d+\s*元?"
    )

    @classmethod
    def _sanitize_search_query(cls, query: str, specs: dict | None = None) -> str:
        """Strip price ranges and spec/feature tokens from the search query.

        Typing "蓝牙耳机 降噪 500-1000元" into the search box artificially
        narrows results; only the product noun belongs in the query — price
        and features are applied afterwards via filter actions.
        """
        cleaned = cls._PRICE_TOKEN_RE.sub(" ", query or "")
        for value in (specs or {}).values():
            token = str(value).strip()
            if token and token in cleaned:
                cleaned = cleaned.replace(token, " ")
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,，、")
        return cleaned or (query or "").strip()

    def _current_product_price(self) -> float | None:
        """Price of the most recently tracked product (for the price guard)."""
        try:
            products = self.memory_manager.state.products
            for product in reversed(products):
                price = getattr(product, "price", None)
                if price:
                    return float(price)
        except (AttributeError, TypeError, ValueError):
            pass
        return None

    def _canonical_action_from_model_output(
        self,
        parsed_action,
        *,
        screen_width: int,
        screen_height: int,
    ) -> dict:
        """Normalize model-native actions for graph/memory recording."""
        from phone_agent.model.protocol_bridge import ModelProtocolBridge

        device_action = ModelProtocolBridge.normalize_action(
            parsed_action,
            model_type=self._model_type,
            screen_size=(screen_width, screen_height),
        )
        action = ModelProtocolBridge.to_autoglm_action(device_action)
        action["_device_action_ir"] = device_action.to_dict()
        action["_source_model_protocol"] = getattr(self._model_type, "value", str(self._model_type))
        return action

    def _vlm_pre_plan(self, task: str) -> dict[str, Any]:
        """Use VLM to decompose the shopping task into a structured plan.

        Called before the first execute_step so the graph router can use
        VLM-enriched goal information instead of only regex-based extraction.
        Returns empty dict on failure (caller falls back to rule-based path).
        """
        prompt = (
            "You are a mobile shopping task planner. Decompose the task into "
            "ordered execution steps and extract structured information.\n\n"
            f'Task: "{task}"\n\n'
            "Return ONLY valid JSON:\n"
            "{\n"
            '  "search_query": "best search keywords",\n'
            '  "product": "target product name",\n'
            '  "specs": {"color": "...", "storage": "...", "size": "..."},\n'
            '  "target_action": "add_to_cart|buy_now|checkout|view_cart",\n'
            '  "target_page": "search_input|search_result|product_detail|spec_selection|cart|checkout",\n'
            '  "steps": [\n'
            '    {"description": "step description", "target_page": "page_type"},\n'
            "    ...\n"
            "  ]\n"
            "}\n\n"
            "Rules:\n"
            "- steps: 3-8 ordered steps. Each step has description + target_page.\n"
            "  target_page is the expected page type AFTER completing that step.\n"
            "  Valid page types: home, search_input, search_result, product_detail,\n"
            "  spec_selection, cart, checkout, store, my_account, filter_panel\n"
            '- search_query: ONLY the bare product noun (e.g. "蓝牙耳机"). NEVER put\n'
            "  price ranges, feature words (降噪/防水/无线...) or qualifiers into the\n"
            '  query — searching "蓝牙耳机 降噪 500-1000元" artificially narrows results.\n'
            "  Prices and features are applied LATER via filter steps.\n"
            "- specs: ONLY explicitly mentioned attributes. Omit unmentioned keys.\n"
            "- If user specified a price range, include a filter step (target_page: filter_panel)\n"
            "  BEFORE browsing products. Also put the price in specs (NOT in search_query).\n\n"
            'Example for "去淘宝买iPhone 17 pro max，银色 512G，加入购物车":\n'
            "{\n"
            '  "search_query": "iPhone 17 pro max",\n'
            '  "product": "iPhone 17 pro max",\n'
            '  "specs": {"color": "银色", "storage": "512G"},\n'
            '  "target_action": "add_to_cart",\n'
            '  "target_page": "spec_selection",\n'
            '  "steps": [\n'
            '    {"description": "搜索iPhone 17 pro max", "target_page": "search_result"},\n'
            '    {"description": "从搜索结果选择合适商品", "target_page": "product_detail"},\n'
            '    {"description": "确认商品参数符合要求", "target_page": "product_detail"},\n'
            '    {"description": "选择银色和512G规格", "target_page": "spec_selection"},\n'
            '    {"description": "点击加入购物车", "target_page": "cart"}\n'
            "  ]\n"
            "}\n\n"
            "JSON:"
        )
        try:
            import os
            from dotenv import load_dotenv
            load_dotenv()
            plan_key = os.getenv("AMSG_STRONG_VLM_API_KEY", "")
            plan_url = os.getenv("AMSG_STRONG_VLM_BASE_URL", "")
            plan_model = os.getenv("AMSG_STRONG_VLM_MODEL", "")
            if plan_key and plan_url and plan_model:
                from openai import OpenAI
                plan_client = OpenAI(api_key=plan_key, base_url=plan_url)
            else:
                plan_client = self.model_client.client
                plan_model = self.model_config.model_name
            response = plan_client.chat.completions.create(
                model=plan_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=512,
                temperature=0.0,
                stream=False,
            )
            content = response.choices[0].message.content or ""
            json_match = re.search(r"\{[\s\S]*\}", content)
            if json_match:
                plan = json.loads(json_match.group())
                if isinstance(plan.get("search_query"), str):
                    plan["search_query"] = self._sanitize_search_query(
                        plan["search_query"], plan.get("specs"),
                    )
                if self.agent_config.verbose:
                    steps = plan.get("steps", [])
                    step_count = len(steps)
                    print(f"[VLM Pre-Plan] {step_count} steps, "
                          f"query={plan.get('search_query', '')[:30]}, "
                          f"target={plan.get('target_page')}, "
                          f"specs={plan.get('specs', {})}")
                return plan
        except Exception:
            if self.agent_config.verbose:
                print("[VLM Pre-Plan] failed, falling back to rule-based extraction")
        return {}

    def _build_step_user_parts(
        self,
        current_app: str,
        page_type: str | None,
        available_actions: list | None,
        graph_hint: str,
        include_screen_info: bool = True,
    ) -> list[str]:
        """Build the structured per-step context parts (all model families).

        AutoGLM embeds them in its per-step user message (with screen info);
        the other adapters receive them via supplementary-context prepend
        (without screen info — their adapters render it themselves).

        Note: consumes self._last_user_reply (cleared after inclusion).
        """
        parts: list[str] = []

        # Task plan with progress markers
        if self._task_plan and self._task_plan.steps:
            parts.append(f"【任务计划】\n{self._task_plan.status_text()}")

        # Key constraints (price, specs) — prominent reminder
        if self._task_plan and self._task_plan.goal_slots:
            constraints = []
            slots = self._task_plan.goal_slots
            for pk in ("price", "price_range", "price_min", "price_max"):
                if slots.get(pk):
                    constraints.append(f"价格要求: {slots[pk]}")
                    break
            for k in ("color", "storage", "size", "brand"):
                if slots.get(k):
                    constraints.append(f"{k}: {slots[k]}")
            if constraints:
                parts.append("【关键约束】⚠️ " + "，".join(constraints)
                             + "\n请严格按照约束选择商品，不符合价格要求的商品不要加入购物车")

        # Execution history (compressed summaries)
        if self._step_summaries:
            history_lines = []
            for i, s in enumerate(self._step_summaries[-8:], 1):
                history_lines.append(f"Step {i}: {s}")
            parts.append("【执行历史】\n" + "\n".join(history_lines))

        # Graph co-pilot hint (route direction, VLM-verification warning,
        # domain priors) — direction only, never historical product data
        if graph_hint:
            parts.append(f"【图谱导航】\n{graph_hint}")

        # Action Library hints (navigation advisory)
        if available_actions and self.action_advisor:
            hints_text = self.action_advisor.format_for_vlm(available_actions)
            if hints_text:
                page_label = page_type or "unknown"
                parts.append(f"【可用操作】(当前: {page_label})\n{hints_text}")

        # SpecGuard safety hints (critical for spec/payment pages)
        if self.memory_manager:
            critical_hints = self._spec_guard.get_context_hints(
                current_app=current_app,
                page_type=page_type,
                task=self._current_task,
                vlm_plan=getattr(self, "_vlm_plan", None),
            )
            if critical_hints:
                parts.append("\n".join(critical_hints))

        # Price red-line on decision pages — last text before screenshot
        if page_type in ("search_result", "product_detail", "spec_selection"):
            price_slot = ""
            if self._task_plan:
                for pk in ("price", "price_range", "price_min", "price_max"):
                    price_slot = self._task_plan.goal_slots.get(pk, "")
                    if price_slot:
                        break
            if price_slot:
                parts.append(
                    f"⛔ 价格红线: {price_slot}。"
                    f"超出此范围的商品不要选择、不要加入购物车。"
                    f"如果当前商品超出预算，立即 Back 返回。"
                    f"注意：筛选器可能未生效——如果结果中出现超出区间的价格，"
                    f"说明价格筛选失败，必须重新打开筛选面板、"
                    f"依次填写最低价和最高价两个输入框并确认后再继续。"
                )

        if include_screen_info:
            screen_info = MessageBuilder.build_screen_info(current_app)
            parts.append(f"** Screen Info **\n\n{screen_info}")

        if getattr(self, "_last_user_reply", None):
            parts.append(f"[用户补充约束]: {self._last_user_reply}")
            self._last_user_reply = None

        return parts

    def _inject_supplementary_context(self, extra_context_parts: list[str]) -> None:
        """Prepend supplementary context to the latest user message text."""
        if not (extra_context_parts and self._context):
            return
        extra_context = "\n\n".join(extra_context_parts)
        last_msg = self._context[-1]
        if isinstance(last_msg.get("content"), list):
            for item in last_msg["content"]:
                if item.get("type") == "text":
                    item["text"] = f"{extra_context}\n\n{item['text']}"
                    break
        elif isinstance(last_msg.get("content"), str):
            last_msg["content"] = f"{extra_context}\n\n{last_msg['content']}"

    def _execute_step(
        self, user_prompt: str | None = None, is_first: bool = False
    ) -> StepResult:
        """Execute a single step and publish per-step telemetry.

        Wraps _execute_step_impl so every return path (Fast Path, graph
        shortcut, VLM path, takeover, errors) emits exactly one telemetry
        event for frontends (mode / dispatch / graph_hint / postcondition).
        """
        self._step_telemetry = {
            "step": self._step_count + 1,
            "dispatch": "vlm",
            "mode": "explore",
            "page_type": "",
            "graph_hint": "",
        }
        result = self._execute_step_impl(user_prompt, is_first)
        self._advance_lifecycle_step()
        info = dict(self._step_telemetry)
        info.update(
            success=result.success,
            finished=result.finished,
            thinking=result.thinking or "",
            action=result.action,
            message=result.message or "",
        )
        self.last_step_info = info
        if self.step_observer:
            try:
                self.step_observer(info)
            except Exception:
                pass
        return result

    def _tele(self, **kwargs: Any) -> None:
        """Record per-step telemetry fields (merged into last_step_info)."""
        self._step_telemetry.update(kwargs)

    def _execute_step_impl(
        self, user_prompt: str | None = None, is_first: bool = False
    ) -> StepResult:
        """Execute a single step of the agent loop."""
        self._step_count += 1

        # Capture current screen state
        device_factory = get_device_factory()
        screenshot = device_factory.get_screenshot(self.agent_config.device_id)
        current_app = device_factory.get_current_app(self.agent_config.device_id)
        self._tele(app=current_app or "", screenshot_b64=screenshot.base64_data)

        # Phase 3: Locate context and switch modes
        mode = "explore"
        current_state_id = None
        context_data = {"mode": "explore", "semantic_context": "", "graph_hint": "", "next_actions": [], "current_state_id": None}
        _available_actions: list | None = None

        # Initialize page_type for SpecGuard
        page_type = None

        if self.memory_manager:
            import hashlib
            hasher = hashlib.md5()
            hasher.update(screenshot.base64_data.encode('utf-8'))
            ui_hash = hasher.hexdigest()
            self._last_state_hash = f"state_{ui_hash}"

            # Extract page semantics using PageClassifier
            page_type = None
            summary = ""
            elements = None
            use_page_classifier = True
            runtime_hint_used = False
            if self.memory_manager and hasattr(self.memory_manager, "should_use_page_classifier"):
                use_page_classifier = self.memory_manager.should_use_page_classifier(
                    step=self._step_count,
                    current_app=current_app or "",
                )
                if not use_page_classifier:
                    hint = self.memory_manager.runtime_screen_hint(current_app or "")
                    hint_page_type = hint.get("page_type")
                    if hint_page_type:
                        runtime_hint_used = True
                        page_type = hint_page_type
                        summary = hint.get("summary", "")
                        elements = hint.get("elements")
                        if self.agent_config.verbose:
                            print(f"⚡ RuntimeDAG hint: type={page_type}, skipping PageClassifier")
                    else:
                        # Hint returned None — DAG is stale, force PageClassifier
                        use_page_classifier = True
                        if self.agent_config.verbose:
                            print("⚠️ RuntimeDAG hint is None, falling back to PageClassifier")

            used_page_classifier = bool(use_page_classifier and self.page_classifier and not screenshot.is_sensitive)
            if self.memory_manager and hasattr(self.memory_manager, "record_page_classifier_decision"):
                self.memory_manager.record_page_classifier_decision(used_page_classifier)

            if used_page_classifier:
                try:
                    pt, sm, el = self.page_classifier.classify(
                        screenshot.base64_data,
                        screenshot.width,
                        screenshot.height
                    )
                    page_type = pt.value  # ShoppingPageType enum → str
                    summary = sm
                    elements = el
                    if self.agent_config.verbose:
                        diagnostics = getattr(self.page_classifier, "last_diagnostics", {}) or {}
                        print(
                            f"Page semantics: type={page_type}, summary={summary[:50]}... "
                            f"| source={diagnostics.get('classifier_source', '')} "
                            f"| model={diagnostics.get('classifier_model', '')} "
                            f"| fallback={diagnostics.get('fallback_used', False)} "
                            f"| max_tokens={diagnostics.get('max_tokens', '')}"
                        )

                    # Check if classification failed (returned UNKNOWN)
                    if pt == ShoppingPageType.UNKNOWN:
                        if self.agent_config.verbose:
                            print(f"⚠️ Page classification returned UNKNOWN, using heuristic fallback")
                        raise ValueError("Classification returned UNKNOWN")
                except Exception as e:
                    if self.agent_config.verbose:
                        diagnostics = getattr(self.page_classifier, "last_diagnostics", {}) or {}
                        print(
                            f"Page classification failed, using heuristic: {e} "
                            f"| raw_error={diagnostics.get('raw_error', '')}"
                        )
                    # Fallback to keyword-based inference using only app name
                    # Don't use task description as it may contain misleading keywords
                    if self.memory_manager:
                        page_type = self.memory_manager.spatial_graph_memory._infer_page_type(
                            current_app or "unknown"
                        )
                        summary = f"{current_app}:{page_type}"

            # Build complete screen dict with semantics
            screen_dict = {
                "ui_hash": ui_hash,
                "semantic_layout": f"{current_app} {page_type}" if page_type else current_app,
                "app": current_app,
                "page_type": page_type,
                "summary": summary,
                "elements": elements,
                "_runtime_hint": runtime_hint_used,
            }

            # Keep semantic_layout variable for backward compatibility
            semantic_layout = screen_dict["semantic_layout"]

            # --- Anomaly watchdog: consecutive broken screens → human ---
            # Real-device failure mode: SMS-verification popup → screenshot
            # capture fails (black fallback, is_sensitive=True) → page stays
            # unknown → the VLM keeps acting blind and reports success.
            if screenshot.is_sensitive or (used_page_classifier and page_type in (None, "unknown")):
                self._anomaly_consecutive += 1
            else:
                self._anomaly_consecutive = 0
            if self._anomaly_consecutive >= 2:
                self._anomaly_consecutive = 0
                return self._handle_verification_takeover(
                    screenshot, current_app,
                    "连续多步无法获取有效屏幕（黑屏或无法识别页面），"
                    "可能出现了验证码、短信验证或安全弹窗。"
                    "请在手机上人工处理后继续。",
                    "anomaly",
                )

            # --- Verification detection (Layer 1): before VLM ---
            if used_page_classifier:
                _verification = detect_verification(page_type, summary, elements)
                if _verification is not None:
                    self._verification_consecutive += 1
                    if self._verification_consecutive <= 3:
                        return self._handle_verification_takeover(
                            screenshot, current_app,
                            _verification.message,
                            _verification.verification_type,
                        )
                    # After 3 consecutive detections, let VLM try once
                    # in case the classifier is giving false positives.
                    if self._verification_consecutive > 5:
                        return StepResult(
                            success=False, finished=True,
                            action=None, thinking="",
                            message="验证页面持续出现，任务无法继续。请手动完成验证后重新运行。",
                        )
                else:
                    self._verification_consecutive = 0

            # Pass to memory manager with complete semantics
            context_data = self.memory_manager.locate_and_get_context(
                ui_hash,
                screen_dict["semantic_layout"],
                user_prompt or self._current_task,
                screen_dict=screen_dict,  # NEW: pass complete semantics
            )
            mode = context_data.get("mode", "explore")
            current_state_id = context_data.get("current_state_id")

            # Phase 2.5: SessionMemory compression (every 5 steps)
            if current_app:
                self.memory_manager.compress_session_history()

            # [HITL Active Clarification] - Use ClarificationAgent on first step
            if is_first and self.clarification_agent:
                # Pass user preferences so ClarificationAgent can fill gaps
                user_prefs = None
                if self.memory_manager:
                    try:
                        summary = self.memory_manager.get_user_summary()
                        user_prefs = summary.get("preferences", [])
                    except Exception:
                        pass
                result = self.clarification_agent.check_and_clarify(
                    task=user_prompt or self._current_task,
                    image_base64=screenshot.base64_data,
                    current_app=current_app,
                    memory_context="\n".join(
                        part
                        for part in (
                            str(context_data.get("graph_hint", "") or ""),
                            str(context_data.get("semantic_context", "") or ""),
                        )
                        if part
                    ),
                    user_preferences=user_prefs,
                    clarification_callback=self.clarification_callback,
                    verbose=self.agent_config.verbose,
                )
                if result.needs_clarification and result.clarified_task:
                    user_prompt = result.clarified_task
                    self._current_task = result.clarified_task  # lower threshold for better HITL trigger rate  # 提高阈��，更���易触发主��提问
                    # Re-evaluate memory with clarified task
                    context_data = self.memory_manager.locate_and_get_context(
                        ui_hash,
                        screen_dict["semantic_layout"],
                        user_prompt,
                        screen_dict=screen_dict,
                    )
                    mode = context_data.get("mode", "explore")
                    current_state_id = context_data.get("current_state_id")

            # ── Sync plan with current page state ──
            # Skip plan steps whose target_page we've already passed through.
            # This handles transitions completed by Fast Path in previous steps.
            if self._task_plan and page_type:
                while (self._task_plan.current_step()
                       and self._task_plan.current_step().target_page
                       and self._task_plan.current_step().target_page != page_type
                       and self._task_plan.current_step().status == "done"):
                    pass  # already done, move on
                # If current page matches a FUTURE step (Fast Path jumped ahead),
                # advance past completed intermediate steps.
                for i, step in enumerate(self._task_plan.steps):
                    if step.status == "pending" and step.target_page == page_type:
                        # Mark all steps before this one as done
                        for j in range(self._task_plan.current_index, i):
                            self._task_plan.steps[j].status = "done"
                        self._task_plan.current_index = i
                        self._task_plan.steps[i].status = "current"
                        break

            # ── Action Library advisory ──
            _available_actions: list | None = None
            if self.action_advisor:
                try:
                    _available_actions = self.action_advisor.query(
                        page_type or "", current_app or "",
                    )
                except Exception:
                    _available_actions = None

            self._tele(
                mode=mode,
                page_type=page_type or "",
                graph_hint=str(context_data.get("graph_hint", "") or ""),
                runtime_metrics=dict(context_data.get("runtime_metrics") or {}),
                available_actions=len(_available_actions or []),
            )

            # ── Dual-speed dispatch: Fast Path for Grounded Actions ──
            if _available_actions and self.agent_config.verbose:
                plan_step = self._task_plan.current_step() if self._task_plan else None
                print(
                    f"[FastPath] page={page_type} hints={len(_available_actions)} "
                    f"plan_target={getattr(plan_step, 'target_page', None)} "
                    f"executable={[h.target_page for h in _available_actions if h.is_fast_executable()]}"
                )
            if _available_actions and not self._needs_vlm(_available_actions, page_type or ""):
                fast_hint = self._select_fast_action(_available_actions, page_type or "")
                if fast_hint is not None:
                    fast_result = self._execute_fast_path(
                        fast_hint, screenshot, current_app, ui_hash, semantic_layout,
                        source_page_type=page_type or "",
                    )
                    if fast_result is not None:
                        return fast_result
                    # Fast Path failed postcondition → fall through to Full Path

            # ── Legacy graph shortcut (kept as fallback, will be removed in Phase 3) ──
            if not _available_actions:
                graph_result = self._try_graph_shortcut(
                    context_data, mode, screenshot, current_app, ui_hash, semantic_layout,
                )
                if graph_result is not None:
                    self._tele(dispatch="graph_shortcut")
                    return graph_result

        # Get model response (Full Path)
        # Graph co-pilot hint (route direction / VLM-verification warning /
        # domain priors), produced by GraphRuntimeController under the
        # "graph_hint" key.  Must reach the VLM regardless of model family.
        graph_hint = str(context_data.get("graph_hint", "") or "")
        graph_hint_in_message = False

        is_non_autoglm = self._model_type in (
            ModelType.UITARS, ModelType.QWENVL,
            ModelType.MAIUI, ModelType.GUIOWL,
        )

        if is_non_autoglm:
            # Use adapter to build messages
            self._context = self._adapter.build_messages(
                task=user_prompt or self._current_task,
                image_base64=screenshot.base64_data,
                current_app=current_app,
                context=self._context,
                lang=self.agent_config.lang,
                screen_width=screenshot.width,
                screen_height=screenshot.height,
            )
            
            # Limit context based on model type
            if self._model_type == ModelType.QWENVL:
                # QwenVL: 只保留 1 张图片（当前），通过 remove_images_from_message 实现
                # build_messages 已经每次重新构建，所以不需要额外处理
                pass
            elif self._model_type == ModelType.GUIOWL:
                # GUI-Owl: 和 QwenVL 一样每次重新构建 messages，不需要额外处理
                pass
            elif self._model_type == ModelType.MAIUI:
                # MAI-UI: 保留最近 3 张图片
                if hasattr(self._adapter, 'limit_context'):
                    self._context = self._adapter.limit_context(self._context, max_images=3)
            elif self._model_type == ModelType.UITARS:
                # UI-TARS: 保留最近 5 张图片
                if hasattr(self._adapter, 'limit_context'):
                    self._context = self._adapter.limit_context(self._context, max_images=5)

            # Model-agnostic structured context (task plan / constraints /
            # history / action hints / SpecGuard / price red-line / user
            # reply). Previously AutoGLM-only — other model families ran
            # without plan progress or safety reminders.
            step_parts = self._build_step_user_parts(
                current_app=current_app,
                page_type=page_type,
                available_actions=_available_actions,
                graph_hint=graph_hint,
                include_screen_info=False,
            )
            if step_parts:
                self._inject_supplementary_context(["\n\n".join(step_parts)])
                graph_hint_in_message = bool(graph_hint)
        else:
            # AutoGLM: original message building logic
            if is_first:
                # Get personalized system prompt with memory context
                system_prompt = self.agent_config.system_prompt
                if self.memory_manager and user_prompt:
                    from phone_agent.memory.memory_manager import build_personalized_prompt
                    system_prompt = build_personalized_prompt(
                        system_prompt, self.memory_manager, user_prompt
                    )
                
                self._context.append(
                    MessageBuilder.create_system_message(system_prompt)
                )

                screen_info = MessageBuilder.build_screen_info(current_app)
                text_content = f"{user_prompt}\n\n{screen_info}"
                if getattr(self, "_last_user_reply", None):
                    text_content += f"\n\n[用户补充约束]: {self._last_user_reply}"
                    self._last_user_reply = None

                self._context.append(
                    MessageBuilder.create_user_message(
                        text=text_content, image_base64=screenshot.base64_data
                    )
                )
            else:
                # Build structured per-step context
                parts = self._build_step_user_parts(
                    current_app=current_app,
                    page_type=page_type,
                    available_actions=_available_actions,
                    graph_hint=graph_hint,
                )
                graph_hint_in_message = bool(graph_hint)

                self._context.append(
                    MessageBuilder.create_user_message(
                        text="\n\n".join(parts), image_base64=screenshot.base64_data
                    )
                )

        # =============================================
        # Phase ⑥: Supplementary context injection (lightweight)
        # Task plan, action hints, and spec guard are already in the per-step
        # user message (built above).  Two supplements are prepended here:
        # the graph co-pilot hint — when the structured builder did not
        # already carry it (first step, non-AutoGLM models) — and on-demand
        # memory retrieval.
        # =============================================
        extra_context_parts: list[str] = []

        if graph_hint and not graph_hint_in_message:
            extra_context_parts.append(graph_hint)

        if self.memory_manager and current_app:
            last_thinking = getattr(self, "_last_thinking", "")
            injection_ctx = self.memory_manager.get_injection_context(
                thinking=last_thinking,
                current_app=current_app,
                step=self._step_count,
            )
            if injection_ctx:
                extra_context_parts.append(injection_ctx)

        self._inject_supplementary_context(extra_context_parts)

        # Get model response (with smart retry)
        msgs = get_messages(self.agent_config.lang)
        max_retries = 3
        response = None

        for attempt in range(max_retries + 1):
            try:
                print("\n" + "=" * 50)
                print(f"💭 {msgs['thinking']}:")
                print("-" * 50)

                response = self.model_client.request(self._context)
                break  # Success

            except (APIConnectionError, APITimeoutError) as e:
                if attempt == max_retries:
                    print(f"❌ 网络错误（已重试{max_retries}次）: {e}")
                    return StepResult(
                        success=False, finished=True,
                        action=None, thinking="",
                        message=f"网络错误（已重试{max_retries}次）: {e}",
                    )
                backoff = 2 ** attempt  # 1s, 2s, 4s
                print(f"⚠️ 网络错误，{backoff}秒后重试 ({attempt + 1}/{max_retries})...")
                time.sleep(backoff)

            except RateLimitError as e:
                if attempt == max_retries:
                    print(f"❌ API 限流: {e}")
                    return StepResult(
                        success=False, finished=True,
                        action=None, thinking="",
                        message=f"API 限流: {e}",
                    )
                print(f"⚠️ API 限流，等待 60 秒后重试 ({attempt + 1}/{max_retries})...")
                time.sleep(60)

            except AuthenticationError as e:
                print(f"❌ 认证失败（检查 PHONE_AGENT_API_KEY）: {e}")
                return StepResult(
                    success=False, finished=True,
                    action=None, thinking="",
                    message=f"认证失败: {e}",
                )

            except Exception as e:
                if self.agent_config.verbose:
                    traceback.print_exc()
                print(f"❌ 模型错误: {e}")
                return StepResult(
                    success=False, finished=True,
                    action=None, thinking="",
                    message=f"模型错误: {e}",
                )

        # --- Verification detection (Layer 2): VLM-output fallback ---
        # Triggers when PageClassifier was skipped (RuntimeDAG hint) but VLM
        # recognized a login/verification page in its thinking.
        _vlm_verification = detect_verification_from_vlm(
            response.thinking or "", response.raw_content or "",
        )
        if _vlm_verification is not None:
            self._verification_consecutive += 1
            if self._verification_consecutive <= 3:
                return self._handle_verification_takeover(
                    screenshot, current_app,
                    _vlm_verification.message,
                    _vlm_verification.verification_type,
                )

        # Parse action and execute based on model type
        thinking = response.thinking  # Default thinking
        action_str = response.action  # Store the original action string for context
        if self._specialized_handler is not None:
            # Use specialized handler (UI-TARS / QwenVL / etc.)
            try:
                parsed_action = self._specialized_handler.parse_response(response.raw_content)
                thinking = parsed_action.thinking or response.thinking
            except Exception:
                if self.agent_config.verbose:
                    traceback.print_exc()
                parsed_action = None
                thinking = response.thinking

            if self.agent_config.verbose and parsed_action:
                print("-" * 50)
                print(f"🎯 {msgs['action']}:")
                print(f"  type: {parsed_action.action_type}")
                print(f"  params: {json.dumps(parsed_action.params, ensure_ascii=False)}")
                print("=" * 50 + "\n")

            # Execute action with specialized handler
            try:
                if parsed_action and parsed_action.action_type and parsed_action.action_type != "unknown":
                    action = self._canonical_action_from_model_output(
                        parsed_action,
                        screen_width=screenshot.width,
                        screen_height=screenshot.height,
                    )
                    # SpecGuard on the canonical action BEFORE execution —
                    # previously only the AutoGLM branch was guarded, so the
                    # other model families could commit purchases unchecked.
                    guarded = self._spec_guard.check(
                        action=action,
                        thinking=thinking,
                        current_app=current_app,
                        page_type=page_type or ("payment" if screenshot.is_sensitive else None),
                        task=self._current_task,
                        vlm_plan=getattr(self, "_vlm_plan", None),
                        current_price=self._current_product_price(),
                    )
                    if guarded is not None:
                        action = guarded
                        result = self.action_handler.execute(
                            guarded, screenshot.width, screenshot.height
                        )
                    else:
                        result = self._specialized_handler.execute(
                            parsed_action, screenshot.width, screenshot.height
                        )
                        # Sync action history to adapter (for QwenVL message building)
                        if result.success and hasattr(self._specialized_handler, 'action_history'):
                            if hasattr(self._adapter, '_action_history'):
                                self._adapter._action_history = list(self._specialized_handler.action_history)
                else:
                    # Fallback to AutoGLM handler
                    action_str = response.action if hasattr(response, 'action') else ""
                    action = parse_action(action_str)
                    result = self.action_handler.execute(
                        action, screenshot.width, screenshot.height
                    )
            except Exception as e:
                if self.agent_config.verbose:
                    traceback.print_exc()
                from phone_agent.actions.handler import ActionResult
                result = ActionResult(success=False, should_finish=True, message=str(e))
                action = finish(message=str(e))
        else:
            # AutoGLM: 使用通用响应解析
            thinking = response.thinking
            action_str = response.action if hasattr(response, 'action') else ""

            try:
                action = parse_action(action_str)
            except ValueError:
                if self.agent_config.verbose:
                    traceback.print_exc()
                action = finish(message=action_str)

            if self.agent_config.verbose:
                print("-" * 50)
                print(f"🎯 {msgs['action']}:")
                print(json.dumps(action, ensure_ascii=False, indent=2))
                print("=" * 50 + "\n")

            # Remove image from context to save space
            self._context[-1] = MessageBuilder.remove_images_from_message(self._context[-1])

            # Action Library grounding: enhance VLM coords with graph coords
            if self.action_advisor and _available_actions:
                action = self.action_advisor.try_ground(action, _available_actions)

            # SpecGuard: prevent model from skipping Interact on spec pages.
            # FLAG_SECURE (sensitive) screenshots skip classification — treat
            # them as payment pages so the guard stays armed where it matters.
            guarded = self._spec_guard.check(
                action=action,
                thinking=thinking,
                current_app=current_app,
                page_type=page_type or ("payment" if screenshot.is_sensitive else None),
                task=self._current_task,
                vlm_plan=getattr(self, "_vlm_plan", None),
                current_price=self._current_product_price(),
            )
            if guarded is not None:
                action = guarded

            # Execute action
            try:
                result = self.action_handler.execute(
                    action, screenshot.width, screenshot.height
                )
            except Exception as e:
                if self.agent_config.verbose:
                    traceback.print_exc()
                # Terminate as a failure — routing through finish() would
                # report success=True and flush the failed trajectory into
                # Neo4j (quality gate 1).
                from phone_agent.actions.handler import ActionResult
                result = ActionResult(success=False, should_finish=True, message=str(e))

        # Add assistant response to context based on model type
        if self._model_type in (ModelType.QWENVL, ModelType.GUIOWL):
            # QwenVL / GUI-Owl: 不添加 assistant 消息到历史
            # 只通过 adapter.add_history() 记录 Action 描述文本
            # 提取 Action: 后面的描述文本（去掉 <tool_call> 部分）
            action_description = ""
            if parsed_action and hasattr(parsed_action, 'action_desc') and parsed_action.action_desc:
                # 使用 parsed_action.action_desc（模型输出的 Action 原文）
                action_description = parsed_action.action_desc.strip()
            elif parsed_action and hasattr(parsed_action, 'description') and parsed_action.description:
                action_description = parsed_action.description.strip()
            else:
                # Fallback: 从 response.raw_content 中提取
                import re
                action_match = re.search(r'Action:\s*"([^"]+)"', response.raw_content)
                if action_match:
                    action_description = action_match.group(1).strip()
                else:
                    # 进一步 fallback: 提取 Action: 行（去掉引号）
                    lines = response.raw_content.split('\n')
                    for line in lines:
                        if line.strip().startswith('Action:'):
                            action_description = line.strip()[7:].strip()
                            # 去掉可能的引号
                            action_description = action_description.strip('"').strip("'")
                            break
            
            # 添加到 adapter 的历史记录
            if action_description and hasattr(self._adapter, 'add_history'):
                self._adapter.add_history(action_description)
        elif self._model_type == ModelType.MAIUI:
            # MAI-UI: 保留模型的全部输出（纯字符串格式，对齐 MAI-UI 官方格式）
            self._context.append({
                "role": "assistant",
                "content": response.raw_content
            })
        elif self._model_type == ModelType.UITARS:
            # UI-TARS: 保留模型的全部输出（纯字符串格式）
            self._context.append({
                "role": "assistant",
                "content": response.raw_content
            })
        else:
            # AutoGLM / GLM-4V: 使用 <think><answer> 格式，保留原始 action 字符串
            # Fallback to empty string if action_str is not defined
            action_str_to_save = action_str if 'action_str' in locals() and action_str else json.dumps(action, ensure_ascii=False) if isinstance(action, dict) else str(action)
            # Make sure action is an explicit action format when saving to history
            if not action_str_to_save.startswith(("do(", "finish(")):
                if action.get("_metadata") == "finish":
                    action_str_to_save = f'finish(message={repr(action.get("message", "") or "")})'
                elif action.get("_metadata") == "do":
                    # Use repr() for all values to ensure proper escaping of quotes, newlines, etc.
                    params_str = ", ".join(f"{k}={repr(v)}" for k, v in action.items() if k not in ("_metadata", "action"))
                    action_str_to_save = f'do(action={repr(action.get("action", ""))}' + (f', {params_str}' if params_str else "") + ')'

            summary_tag = ""
            if response.summary:
                summary_tag = f"<summary>{response.summary}</summary>"
            assistant_content = f"<think>{thinking}</think><answer>{action_str_to_save}</answer>{summary_tag}"
            self._context.append(
                MessageBuilder.create_assistant_message(assistant_content)
            )
        
        # Track step in memory + feed self-evolution
        if self.memory_manager:
            self.memory_manager.add_step(
                thinking=thinking,
                action=action,
                screenshot_app=current_app,
                page_type=page_type or "",
            )

            # Unified state verbose logging
            if self.agent_config.verbose:
                st = self.memory_manager.state
                product_count = len(st.products)
                cart_count = len([p for p in st.products if p.status == ProductStatus.ADDED_TO_CART])
                if product_count > 0 or cart_count > 0:
                    parts = []
                    if st._current_product:
                        p = st._current_product
                        parts.append(f"{p.name}" + (f" ¥{p.price}" if p.price else ""))
                    if cart_count:
                        parts.append(f"购物车({cart_count}件)")
                    if st.products:
                        parts.append(f"已看{product_count}件")
                    print(f"📦 [UnifiedState] {' | '.join(parts)}")

            # Phase 4: Online Dynamic Graph construction
            # When graph can't locate current state, build a synthetic state_id
            # from page_type so the transition still gets recorded.
            effective_state_id = current_state_id
            if not effective_state_id and page_type and current_app:
                effective_state_id = f"state_{current_app}_{page_type}_runtime_{ui_hash[:8]}"
            if effective_state_id:
                self.memory_manager.update_state_and_transition(
                    screenshot_hash=ui_hash,
                    semantic_layout=semantic_layout,
                    action=action,
                    task=self._current_task,
                    expected_postcondition=action.get("_expected_postcondition"),
                )

        # Self-evolution for Full Path: postcondition observation happens in the
        # NEXT step's PageClassifier → record_observation → EdgeLifecycle chain.
        # No extra screenshot needed here (Fast Path handles its own evolve above).


        # Capture interact reply
        if action.get("action_type") == "Interact" or action.get("action") == "Interact" or (action.get("_metadata") == "do" and action.get("action") == "Interact"):
            if hasattr(result, "message") and result.message:
                self._last_user_reply = result.message
                # Fold the answer into the task text so SpecGuard and
                # TaskSpecExtractor see the new constraint — otherwise the
                # guard re-asks the same question until max_steps.
                reply = result.message.strip()
                if reply and reply not in self._current_task:
                    self._current_task = f"{self._current_task}（用户补充：{reply}）"

        # Check if finished
        finished = action.get("_metadata") == "finish" or result.should_finish

        # Record step trace
        if self.tracer:
            session_snapshot = None
            if self.memory_manager:
                session_snapshot = self.memory_manager.state.to_dict()
            self.tracer.record_step(
                step=self._step_count,
                screenshot_base64=screenshot.base64_data,
                model_raw_output=response.raw_content,
                action=action,
                finished=finished,
                session_memory_snapshot=session_snapshot,
            )

        if finished and self.agent_config.verbose:
            msgs = get_messages(self.agent_config.lang)
            print("\n" + "🎉 " + "=" * 48)
            print(
                f"✅ {msgs['task_completed']}: {result.message or action.get('message', msgs['done'])}"
            )
            print("=" * 50 + "\n")

        # Update task plan + step summaries + compress history
        step_summary = response.summary or ""
        if not step_summary and thinking:
            # Fallback: model didn't output <summary>, extract from thinking
            lines = [l.strip() for l in thinking.replace("\n", ". ").split(". ") if l.strip()]
            step_summary = lines[-1][:100] if lines else ""
        if step_summary:
            self._step_summaries.append(step_summary)
        # Plan advancement for Full Path: only when the VLM action clearly
        # completed a plan step (e.g. finish a search, select a product).
        # We do NOT auto-advance based on page_type match because the VLM
        # might still need to perform actions on the current page (like
        # typing a query on search_input before the step is truly done).
        # Fast Path handles its own advancement in _execute_fast_path.
        self._compress_history()

        # Save last thinking for retrieval trigger detection
        self._last_thinking = thinking

        return StepResult(
            success=result.success,
            finished=finished,
            action=action,
            thinking=thinking,
            message=result.message or action.get("message"),
        )

    @property
    def context(self) -> list[dict[str, Any]]:
        """Get the current conversation context."""
        return self._context.copy()

    @property
    def step_count(self) -> int:
        """Get the current step count."""
        return self._step_count
    
    # ==================== Memory Management Methods ====================
    
    def add_user_preference(
        self, preference: str, category: str = "general", importance: float = 0.6
    ):
        """
        Add a user preference to memory.
        
        Args:
            preference: The preference description
            category: Category (e.g., "app", "contact", "habit")
            importance: Importance score (0-1)
        
        Example:
            >>> agent.add_user_preference("喜欢使用深色模式", "ui")
            >>> agent.add_user_preference("常用外卖平台是美团", "app")
        """
        if self.memory_manager:
            self.memory_manager.add_user_preference(preference, category, importance)
    
    def add_user_correction(self, original_action: str, correction: str):
        """
        Record a user correction to help the agent learn.
        
        Args:
            original_action: What the agent did
            correction: What the user wanted
        
        Example:
            >>> agent.add_user_correction("选择了第一个联系人", "应该选择名字完全匹配的联系人")
        """
        if self.memory_manager:
            self.memory_manager.add_user_correction(original_action, correction)
    
    def get_user_summary(self) -> dict | None:
        """
        Get a summary of known user information.
        
        Returns:
            Dictionary with contacts, apps, preferences, and recent tasks
        """
        if self.memory_manager:
            return self.memory_manager.get_user_summary()
        return None
    
    def get_memory_stats(self) -> dict | None:
        """Get memory system statistics."""
        if self.memory_manager:
            return self.memory_manager.get_stats()
        return None
    
    def clear_memories(self):
        """Clear all memories for the current user."""
        if self.memory_manager:
            self.memory_manager.clear_all()
            if self.agent_config.verbose:
                print("🗑️ 所有记忆已清除")
    
    def export_memories(self) -> list[dict] | None:
        """Export all memories for backup."""
        if self.memory_manager:
            return self.memory_manager.export_memories()
        return None
    
    def import_memories(self, memories: list[dict]):
        """Import memories from backup."""
        if self.memory_manager:
            self.memory_manager.import_memories(memories)
