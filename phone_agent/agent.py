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
from phone_agent.device_factory import get_device_factory
from phone_agent.model import ModelClient, ModelConfig
from phone_agent.model.adapters import ModelType, detect_model_type, get_adapter
from phone_agent.model.client import MessageBuilder
from phone_agent.memory.core import ProductStatus
from phone_agent.memory.offline_explorer import PageClassifier, ShoppingPageType


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
    ):
        self.clarification_callback = clarification_callback
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

        # Load externalized shopping config (JSON with code defaults)
        from phone_agent.config.shopping_config import ShoppingConfig
        self._shopping_config = ShoppingConfig.load()

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
        self._last_state_hash: str | None = None
        self._last_user_reply: str | None = None

        # Clear action history for QwenVL handler/adapter
        if self._specialized_handler is not None and hasattr(self._specialized_handler, 'clear_history'):
            self._specialized_handler.clear_history()
        if hasattr(self._adapter, 'clear_history'):
            self._adapter.clear_history()

        # Start tracing
        if self.tracer:
            self.tracer.start_task(task, model=self.model_config.model_name)

        # Start memory tracking
        self._vlm_plan: dict[str, Any] = {}
        if self.memory_manager:
            self.memory_manager.start_task(task)
            # VLM pre-planning: decompose task before graph execution so the
            # graph router uses accurate target page types and spec slots
            # instead of relying solely on regex-based heuristics.
            self._vlm_plan = self._vlm_pre_plan(task)
            if self._vlm_plan:
                self.memory_manager.set_vlm_plan(self._vlm_plan)

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

        # Continue until finished or max steps reached
        while self._step_count < self.agent_config.max_steps:
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


    # ------------------------------------------------------------------
    # Spec-page action guard: apps where skipping Interact is critical
    # (loaded from config/shopping.json with code defaults as fallback)
    # ------------------------------------------------------------------

    def _detect_critical_scenario(
        self,
        current_app: str,
        screenshot_base64: str,
        page_type: str | None = None,
    ) -> list[str]:
        """Return hints injected into VLM context when on a shopping spec page."""
        if not any(app in (current_app or "") for app in self._shopping_config.apps):
            return []

        if page_type not in {"spec_selection", "checkout", "payment"}:
            return []

        requested_specs = self._requested_spec_slots()
        if requested_specs:
            return [
                "[SpecGuard]\n"
                f"用户已明确指定规格：{self._format_specs(requested_specs)}。\n"
                "不要再询问用户。请先在当前页面选择这些规格；只有在规格已经匹配后，才能点击确认/加入购物车/立即购买。\n"
                "如果页面没有对应规格或无法判断是否匹配，先选择可见的匹配项或回退重试，不要进入结算、支付、地址或登录页面。"
            ]

        return [
            "[SpecGuard]\n"
            "当前可能处于规格选择或下单确认页面，但用户没有明确指定规格。\n"
            "不要默认替用户选择具体 SKU；如果下一步会确认规格、加入购物车或购买，应先询问用户需要哪个规格。"
        ]

    # ── SKU extraction patterns ──
    _COLOR_VALUES = {
        "银色", "蓝色", "黑色", "白色", "红色", "金色", "绿色", "紫色", "灰色",
        "粉色", "橙色", "黄色", "棕色", "深空黑色", "星光色", "午夜色", "远峰蓝",
        "苍岭绿", "暗紫色", "石墨色", "亮黑色", "土豪金", "玫瑰金", "深空灰",
    }
    _STORAGE_PATTERNS = [
        r"(\d+\s*(?:TB?|GB?))",  # 512G, 512GB, 1TB, 256G
    ]
    _SIZE_VALUES = {"S", "M", "L", "XL", "XXL", "XXXL", "均码", "大码", "小码"}
    _SPEC_KEY_ALIASES = {
        "color": "颜色",
        "colour": "颜色",
        "颜色": "颜色",
        "机身颜色": "颜色",
        "storage": "容量",
        "capacity": "容量",
        "memory": "容量",
        "容量": "容量",
        "存储容量": "容量",
        "size": "尺码",
        "尺码": "尺码",
        "尺寸": "尺码",
    }
    _SPEC_COMMIT_TARGETS = {
        "confirm_spec_add_to_cart",
        "confirm_add_to_cart_success",
        "confirm_spec",
        "add_to_cart",
        "buy_now",
        "submit_order",
        "checkout",
        "payment",
    }

    def _extract_specs_from_task(self, task: str) -> dict[str, str]:
        """
        Parse the user's original task for explicit SKU specifications.
        Returns a mapping from spec category to value, e.g.:
        {'颜色': '银色', '容量': '512G'}
        """
        specs: dict[str, str] = {}

        # Extract color
        for color in sorted(self._COLOR_VALUES, key=len, reverse=True):
            if color in task:
                specs["颜色"] = color
                break

        # Extract storage
        for pattern in self._STORAGE_PATTERNS:
            m = re.search(pattern, task, re.IGNORECASE)
            if m:
                specs["容量"] = m.group(1).upper().replace(" ", "").replace("B", "B")
                break

        # Extract size
        for size in sorted(self._SIZE_VALUES, key=len, reverse=True):
            rsize = rf"\b{re.escape(size)}\b"
            if re.search(rsize, task):
                specs["尺码"] = size
                break

        return specs

    @classmethod
    def _normalize_spec_key(cls, key: str) -> str:
        normalized = str(key or "").strip()
        return cls._SPEC_KEY_ALIASES.get(
            normalized.lower(),
            cls._SPEC_KEY_ALIASES.get(normalized, normalized),
        )

    @classmethod
    def _format_specs(cls, specs: dict[str, str]) -> str:
        return "，".join(
            f"{cls._normalize_spec_key(key)}={value}"
            for key, value in specs.items()
            if value
        )

    def _requested_spec_slots(self) -> dict[str, str]:
        """Merge explicit SKU constraints from task text and VLM pre-plan."""
        specs: dict[str, str] = {}
        for key, value in self._extract_specs_from_task(getattr(self, "_current_task", "")).items():
            if value:
                specs[self._normalize_spec_key(key)] = str(value)

        plans = []
        if isinstance(getattr(self, "_vlm_plan", None), dict):
            plans.append(self._vlm_plan)
        memory_plan = getattr(getattr(self, "memory_manager", None), "_vlm_plan", None)
        if isinstance(memory_plan, dict):
            plans.append(memory_plan)
        for plan in plans:
            plan_specs = plan.get("specs", {})
            if not isinstance(plan_specs, dict):
                continue
            for key, value in plan_specs.items():
                if value:
                    specs[self._normalize_spec_key(key)] = str(value)
        return specs

    def _is_spec_selected(
        self, thinking: str, spec_key: str, spec_value: str
    ) -> bool:
        """
        Check whether the thinking reflects that a specific SKU value
        is already selected on the current page.
        """
        # Normalize spec_value for fuzzy matching
        val = spec_value.upper().replace(" ", "")
        # e.g. "512GB" -> also try "512G"
        val_short = val.replace("GB", "G").replace("TB", "T")

        # The thinking must mention the value
        if spec_value not in thinking and val not in thinking and val_short not in thinking:
            return False

        # And it must appear near a "selected" marker
        for sv in (spec_value, val, val_short):
            # Pattern: value appears within 20 chars of a selection marker
            if re.search(
                rf"{re.escape(sv)}.{{0,20}}(?:已选中|已选[^项]|已选择|已勾选|✔|✓|\bselected\b|当前)",
                thinking,
            ):
                return True
            # Pattern: spec_key then value then selection marker
            if re.search(
                rf"{re.escape(spec_key)}.*?{re.escape(sv)}",
                thinking,
            ) and any(m in thinking for m in ("已选中", "已选", "已选择", "已勾选")):
                return True

        return False

    def _build_spec_question(self, thinking: str) -> str:
        """Build a contextual question when the user hasn't specified exact SKU."""
        if "颜色" in thinking and "容量" in thinking:
            return "这里有多种颜色和容量可选，请问您需要哪个配置？"
        elif "颜色" in thinking:
            return "有多种颜色可选，请问您喜欢哪个颜色？"
        elif "容量" in thinking:
            return "有多种容量可选，请问您需要多大容量？"
        elif "温度" in thinking:
            return "请问您需要什么温度？"
        elif "糖度" in thinking:
            return "请问您需要什么糖度？"
        return "请问您需要什么规格和配置？"

    def _is_spec_commit_action(self, action: dict, thinking: str, page_type: str | None) -> bool:
        """Return True only for actions that commit a spec/purchase choice."""
        if page_type not in {"spec_selection", "checkout", "payment"}:
            return False

        action_name = str(action.get("action") or action.get("action_type") or "").lower()
        if action_name in {"interact", "back", "wait", "type", "launch", "home"}:
            return False

        semantic_target = str(action.get("semantic_target") or action.get("target") or "").lower()
        if semantic_target in self._SPEC_COMMIT_TARGETS:
            return True
        if any(token in semantic_target for token in self._SPEC_COMMIT_TARGETS):
            return True

        selection_tokens = ("choose_spec", "select_spec", "颜色", "容量", "尺码", "规格项", "option")
        if any(token.lower() in semantic_target for token in selection_tokens):
            return False

        action_text = json.dumps(action, ensure_ascii=False).lower()
        commit_keywords = (
            "确定",
            "确认",
            "加入购物车",
            "加购",
            "立即购买",
            "购买",
            "提交订单",
            "结算",
            "支付",
            "confirm",
            "add to cart",
            "buy now",
            "checkout",
            "submit order",
            "pay",
        )
        combined = f"{action_text}\n{thinking}".lower()
        return any(keyword.lower() in combined for keyword in commit_keywords)

    def _spec_guard_check(
        self,
        action: dict,
        thinking: str,
        current_app: str,
        page_type: str | None = None,
    ) -> dict | None:
        """
        Code-level safety net: before purchase/confirm on a spec page,
        cross-reference the user's original task requirements against
        what's currently selected. Intercepts only when:
        - The user didn't specify exact SKU → must ask
        - The user's specified SKU doesn't match what's selected → must correct

        Args:
            action: The action to check
            thinking: The model's reasoning
            current_app: Current app name
            page_type: Current page type (from PageClassifier). If None, spec guard is disabled.
        """
        if not any(app in (current_app or "") for app in self._shopping_config.apps):
            return None

        if action.get("action") == "Interact" or action.get("action_type") == "Interact":
            return None

        # Terminal actions (finish/terminate/answer) mean the model has decided
        # the task is complete — don't second-guess that decision here
        _metadata = (action.get("_metadata") or "").lower()
        if _metadata == "finish":
            return None
        _action_name = (action.get("action") or "").lower()
        if _action_name in ("terminate", "answer"):
            return None

        # Only guard actual spec commit / high-risk confirmation pages.
        # Product-detail CTA is allowed because it opens the spec sheet.
        if not page_type or page_type not in ("spec_selection", "checkout", "payment"):
            return None

        if not self._is_spec_commit_action(action, thinking, page_type):
            return None

        # ── Self-reflection: cross-reference user's original task ──
        original_task = self._current_task
        user_requested_specs = self._requested_spec_slots()

        if user_requested_specs:
            # User explicitly specified SKU — verify they're selected
            missing = []
            for spec_key, spec_value in user_requested_specs.items():
                if not self._is_spec_selected(thinking, spec_key, spec_value):
                    missing.append(f"{spec_key}={spec_value}")

            if not missing:
                print(
                    f"✅ [SpecGuard] 用户指定SKU已全部选中 "
                    f"({user_requested_specs})，放行"
                )
                return None

            if page_type not in {"checkout", "payment"}:
                print(
                    f"⚠️ [SpecGuard] 用户已指定SKU但模型思考未证明已选中 "
                    f"({user_requested_specs})，不追问用户；交由VLM按显式规格继续选择"
                )
                return None

            question = (
                f"您要求的是{'，'.join(missing)}，"
                f"但当前页面尚未选择。请确认规格后继续。"
            )
        else:
            # User didn't specify exact SKU — must ask
            question = self._build_spec_question(thinking)

        print(f"\n{'─' * 50}")
        print(f"🛑 [SpecGuard] 规格确认拦截")
        print(f"   用户任务: {original_task[:100]}")
        if user_requested_specs:
            print(f"   用户指定SKU: {user_requested_specs}")
        print(f"   模型试图: {thinking[:100]}...")
        print(f"   强制为: Interact → {question}")
        print(f"{'─' * 50}")

        return {
            "_metadata": "do",
            "action": "Interact",
            "action_type": "Interact",
            "message": question,
        }

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
            "You are a mobile shopping task planner. Extract structured "
            "information from the user's task.\n\n"
            f'Task: "{task}"\n\n'
            "Return ONLY valid JSON (no other text):\n"
            '{"search_query": "...", "product": "...",'
            ' "specs": {"color": "...", "storage": "...", "size": "..."},'
            ' "target_action": "add_to_cart|buy_now|checkout|view_cart",'
            ' "target_page": "spec_selection|cart|checkout|product_detail|search_result",'
            ' "steps": ["step1", "step2", ...]}\n\n'
            "Rules:\n"
            "- search_query: best search keywords for finding the product\n"
            "- product: target product name\n"
            "- specs: ONLY attributes the user explicitly mentioned. Omit keys with no value.\n"
            "- target_action: what the user wants to do at the end\n"
            "- target_page: the page type where target_action is performed\n"
            "- steps: 3-5 ordered steps to complete the task\n\n"
            'Example for "去淘宝买iPhone 17 pro max，银色 512G，加入购物车":\n'
            '{"search_query": "iPhone 17 pro max", "product": "iPhone 17 pro max",'
            ' "specs": {"color": "银色", "storage": "512G"},'
            ' "target_action": "add_to_cart", "target_page": "spec_selection",'
            ' "steps": ["搜索iPhone 17 pro max", "从搜索结果选择合适商品",'
            ' "选择银色和512G规格", "点击加入购物车"]}\n\n'
            "JSON:"
        )
        try:
            response = self.model_client.client.chat.completions.create(
                model=self.model_config.model_name,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=512,
                temperature=0.0,
                stream=False,
            )
            content = response.choices[0].message.content or ""
            json_match = re.search(r"\{[\s\S]*\}", content)
            if json_match:
                plan = json.loads(json_match.group())
                if self.agent_config.verbose:
                    print(f"[VLM Pre-Plan] target={plan.get('target_page')}, "
                          f"query={plan.get('search_query', '')[:30]}, "
                          f"specs={plan.get('specs', {})}")
                return plan
        except Exception:
            if self.agent_config.verbose:
                print("[VLM Pre-Plan] failed, falling back to rule-based extraction")
        return {}

    def _execute_step(
        self, user_prompt: str | None = None, is_first: bool = False
    ) -> StepResult:
        """Execute a single step of the agent loop."""
        self._step_count += 1

        # Capture current screen state
        device_factory = get_device_factory()
        screenshot = device_factory.get_screenshot(self.agent_config.device_id)
        current_app = device_factory.get_current_app(self.agent_config.device_id)
        
        # Phase 3: Locate context and switch modes
        mode = "explore"
        current_state_id = None
        context_data = {"mode": "explore", "semantic_context": "", "next_actions": [], "current_state_id": None}

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
                    runtime_hint_used = True
                    hint = self.memory_manager.runtime_screen_hint(current_app or "")
                    page_type = hint.get("page_type")
                    summary = hint.get("summary", "")
                    elements = hint.get("elements")
                    if self.agent_config.verbose:
                        print(f"⚡ RuntimeDAG hint: type={page_type}, skipping PageClassifier")

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
                result = self.clarification_agent.check_and_clarify(
                    task=user_prompt or self._current_task,
                    image_base64=screenshot.base64_data,
                    current_app=current_app,
                    memory_context=context_data.get("semantic_context", ""),
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

            if mode == "navigate" and context_data.get("next_actions"):
                _, grounding_hint = self._compile_spatial_shortcut_action(
                    context_data["next_actions"][0],
                    screen_width=screenshot.width,
                    screen_height=screenshot.height,
                )
                if grounding_hint:
                    context_data["semantic_context"] = "\n\n".join(
                        part for part in (context_data.get("semantic_context", ""), grounding_hint) if part
                    )

            _next_action = context_data.get("next_actions", [None])[0] if context_data.get("next_actions") else None
            _action_type = _next_action.get("type", "") if _next_action else ""
            # Actions that don't need coordinate compilation (compound macros,
            # text input, app launch, system navigation).
            _NON_COORDINATE_ACTIONS = frozenset({"Compound", "Type", "Launch", "Back", "Wait", "Home"})
            # VLM verification gate: when the graph action involves a semantic
            # choice (e.g. picking the right product from search results), the
            # graph only provides a spatial suggestion — the VLM must verify
            # the screenshot matches the user's task before acting.
            if (
                mode in {"navigate", "verify_with_vlm"}
                and _next_action
                and _next_action.get("confidence", 1.0) >= 0.7
                and not _next_action.get("_requires_vlm_verification")
                and (
                    _action_type in _NON_COORDINATE_ACTIONS
                    or self._compile_spatial_shortcut_action(
                        _next_action,
                        screen_width=screenshot.width,
                        screen_height=screenshot.height,
                    )[0] is not None
                )
            ):
                # Debug: log graph navigation attempt
                if self.agent_config.verbose:
                    print(f"[Graph Nav] mode={mode}, confidence={_next_action.get('confidence', 1.0):.2f}, action={_next_action.get('type', 'unknown')}")
                # Fast track: return the highest confidence action directly without VLM inference
                best_action = _next_action

                # Fill runtime slots for compound actions with task-specific values
                goal_slots = context_data.get("goal_spec", {}).get("slots", {})
                if goal_slots and self.memory_manager:
                    best_action = self.memory_manager.spatial_graph_memory._fill_runtime_slots(
                        best_action, goal_slots
                    )

                # Check confidence threshold before executing
                action_confidence = best_action.get("confidence", 1.0)
                if action_confidence < 0.7:  # Lowered from 0.8 to allow graph navigation
                    # Confidence too low, fall back to explore mode
                    mode = "explore"
                    print(f"[Navigate] Confidence {action_confidence:.2f} < 0.7, falling back to explore mode")
                else:
                    action = {
                        "_metadata": "do",
                        "action": best_action["type"],
                    }
                    if best_action.get("target"):
                        action["semantic_target"] = best_action["target"]
                    if best_action.get("postcondition"):
                        action["_expected_postcondition"] = best_action["postcondition"]

                    # Quick parse params
                    try:
                        import ast
                        params = ast.literal_eval(best_action.get("target_desc", "{}"))
                        action.update(params)
                    except:
                        pass
                    compiled_action, _ = self._compile_spatial_shortcut_action(
                        best_action,
                        screen_width=screenshot.width,
                        screen_height=screenshot.height,
                    )
                    if compiled_action is not None:
                        action = compiled_action

                print(f"🚀 Navigation Mode Triggered: Found Graph Shortcut: {best_action['type']}")

                # Execute it directly
                try:
                    if self._specialized_handler:
                        # Convert back to parsed action format if needed, simplistic execution here
                        pass
                    result = self.action_handler.execute(
                        action, screenshot.width, screenshot.height
                    )
                except Exception as e:
                    result = self.action_handler.execute(
                        finish(message=str(e)), screenshot.width, screenshot.height
                    )

                finished = action.get("_metadata") == "finish" or result.should_finish
                self._last_thinking = "[Graph Shortcut Navigated]"
                if self.memory_manager:
                    if hasattr(self.memory_manager, "mark_planned_action_executed"):
                        self.memory_manager.mark_planned_action_executed(action, success=result.success)
                    self.memory_manager.add_step(
                        thinking="[Graph Shortcut Navigated]",
                        action=action,
                        screenshot_app=current_app,
                    )
                    self.memory_manager.update_state_and_transition(
                        screenshot_hash=ui_hash,
                        semantic_layout=semantic_layout,
                        action=action,
                        task=self._current_task,
                        expected_postcondition=action.get("_expected_postcondition"),
                    )
                return StepResult(
                    success=result.success,
                    finished=finished,
                    action=action,
                    thinking="[Graph Shortcut Navigated]",
                    message=result.message or action.get("message"),
                )
            elif (
                mode in {"navigate", "verify_with_vlm"}
                and _next_action
                and _next_action.get("_requires_vlm_verification")
            ):
                if self.agent_config.verbose:
                    print(f"🤝 [VLM-Graph Co-pilot] 图谱建议 {_next_action.get('type')}"
                          f"→{_next_action.get('postcondition','')}，转交VLM验证确认")
            else:
                if self.agent_config.verbose:
                    print(f"🧭 知识图谱查询: 未匹配到可信历史动作，使用视觉大模型进行推理 (Explore Mode)")

        # Get model response
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
                screen_info = MessageBuilder.build_screen_info(current_app)
                text_content = f"** Screen Info **\n\n{screen_info}"
                if getattr(self, "_last_user_reply", None):
                    text_content += f"\n\n[用户补充约束]: {self._last_user_reply}"
                    self._last_user_reply = None

                self._context.append(
                    MessageBuilder.create_user_message(
                        text=text_content, image_base64=screenshot.base64_data
                    )
                )

        # =============================================
        # Phase ⑥: Memory Decoupling Context (UI-Copilot paradigm)
        # MINIMAL by default — only progress + on-demand retrieval.
        # Detailed observations live in KnowledgeBase, not in VLM context.
        # =============================================
        extra_context_parts: list[str] = []
        if context_data.get("semantic_context"):
            extra_context_parts.append(str(context_data["semantic_context"]))

        if self.memory_manager and current_app:
            # Core: lightweight progress + on-demand retrieval
            last_thinking = getattr(self, "_last_thinking", "")
            injection_ctx = self.memory_manager.get_injection_context(
                thinking=last_thinking,
                current_app=current_app,
                step=self._step_count,
            )
            if injection_ctx:
                extra_context_parts.append(injection_ctx)

        # Critical scenario detection (spec-guard — keep, it prevents bad purchases)
        if self.memory_manager:
            critical_hints = self._detect_critical_scenario(
                current_app,
                screenshot.base64_data,
                page_type=page_type,
            )
            if critical_hints:
                extra_context_parts.append("\n\n".join(critical_hints))
                if self.agent_config.verbose:
                    print("🎯 检测到关键场景，注入强提示")

        extra_context = "\n\n".join(extra_context_parts) if extra_context_parts else ""

        if extra_context and self._context:
            last_msg = self._context[-1]
            if isinstance(last_msg.get("content"), list):
                for item in last_msg["content"]:
                    if item.get("type") == "text":
                        # Inject at text beginning, not end
                        item["text"] = f"{extra_context}\n\n{item['text']}"
                        break
            elif isinstance(last_msg.get("content"), str):
                # Inject at text beginning, not end
                last_msg["content"] = f"{extra_context}\n\n{last_msg['content']}"

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
                    result = self._specialized_handler.execute(
                        parsed_action, screenshot.width, screenshot.height
                    )
                    # Sync action history to adapter (for QwenVL message building)
                    if result.success and hasattr(self._specialized_handler, 'action_history'):
                        if hasattr(self._adapter, '_action_history'):
                            self._adapter._action_history = list(self._specialized_handler.action_history)
                    
                    action = self._canonical_action_from_model_output(
                        parsed_action,
                        screen_width=screenshot.width,
                        screen_height=screenshot.height,
                    )
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

            # SpecGuard: prevent model from skipping Interact on spec pages
            # Only triggers on spec-related pages (spec_selection, product_detail, checkout)
            guarded = self._spec_guard_check(action, thinking, current_app, page_type)
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
                result = self.action_handler.execute(
                    finish(message=str(e)), screenshot.width, screenshot.height
                )

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

            assistant_content = f"<think>{thinking}</think><answer>{action_str_to_save}</answer>"
            self._context.append(
                MessageBuilder.create_assistant_message(assistant_content)
            )
        
        # Track step in memory
        if self.memory_manager:
            self.memory_manager.add_step(
                thinking=thinking,
                action=action,
                screenshot_app=current_app,
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

            # Phase 4: Online Dynamic Graph construction - 使用统��接口
            if current_state_id:
                self.memory_manager.update_state_and_transition(
                    screenshot_hash=ui_hash,
                    semantic_layout=semantic_layout,
                    action=action,
                    task=self._current_task,
                    expected_postcondition=action.get("_expected_postcondition"),
                )


        # Capture interact reply
        if action.get("action_type") == "Interact" or action.get("action") == "Interact" or (action.get("_metadata") == "do" and action.get("action") == "Interact"):
            if hasattr(result, "message") and result.message:
                self._last_user_reply = result.message

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
