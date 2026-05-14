"""Main PhoneAgent class for orchestrating phone automation."""

import json
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
        if self.memory_manager:
            self.memory_manager.start_task(task)

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
        self, current_app: str, screenshot_base64: str
    ) -> list[str]:
        """Return hints injected into VLM context when on a shopping spec page."""
        if not any(app in (current_app or "") for app in self._shopping_config.apps):
            return []

        return [
            "🚨 你正在商品详情/规格选择页面。用户未指定完整规格参数。\n"
            "唯一正确操作：do(action=\"Interact\", message=\"请问您需要哪个规格？\")\n"
            "如果你在想「我帮他选个默认的」——这是错误的，会导致用户收到不想要的商品。\n"
            "立即执行 Interact，不要点任何购买/加入购物车按钮！"
        ]

    def _spec_guard_check(
        self,
        action: dict,
        thinking: str,
        current_app: str,
    ) -> dict | None:
        """
        Code-level safety net: if the model is about to click purchase/confirm
        on a spec page without having issued Interact in this step, override
        the action with a forced Interact.

        Returns a replacement action dict, or None if the action is safe.
        """
        if not any(app in (current_app or "") for app in self._shopping_config.apps):
            return None

        # Already issuing Interact – allow
        if action.get("action") == "Interact" or action.get("action_type") == "Interact":
            return None

        # Check whether the model's thinking discusses specs
        thinking_mentions_specs = any(
            kw in thinking for kw in self._shopping_config.spec_keywords
        )
        if not thinking_mentions_specs:
            return None

        # Check whether the thinking mentions a purchase intent
        thinking_has_purchase_intent = any(
            kw in thinking for kw in self._shopping_config.purchase_keywords
        )
        if not thinking_has_purchase_intent:
            return None

        # Build a contextual question from the thinking
        question = "请问您需要什么规格和配置？"
        if "颜色" in thinking and "容量" in thinking:
            question = "这里有多种颜色和容量可选，请问您需要哪个配置？"
        elif "颜色" in thinking:
            question = "有多种颜色可选，请问您喜欢哪个颜色？"
        elif "容量" in thinking:
            question = "有多种容量可选，请问您需要多大容量？"
        elif "温度" in thinking:
            question = "请问您需要什么温度？"
        elif "糖度" in thinking:
            question = "请问您需要什么糖度？"

        print(f"\n{'─' * 50}")
        print(f"🛑 [SpecGuard] 模型试图跳过规格询问，已拦截")
        print(f"   模型原计划: {thinking[:80]}...")
        print(f"   强制��为: Interact")
        print(f"{'─' * 50}")

        return {
            "_metadata": "do",
            "action": "Interact",
            "action_type": "Interact",
            "message": question,
        }

        return critical_hints

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
        if self.memory_manager:
            import hashlib
            hasher = hashlib.md5()
            hasher.update(screenshot.base64_data.encode('utf-8'))
            ui_hash = hasher.hexdigest()
            self._last_state_hash = f"state_{ui_hash}"

            # 轻量级启发式特征：使用当前 APP 名称作为语义标签
            semantic_layout = current_app if current_app else "home_screen"

            # 基于 GraphRAG 进行匹配 (MD5 逻辑已移除)
            context_data = self.memory_manager.locate_and_get_context(ui_hash, semantic_layout, user_prompt or self._current_task)
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
                    context_data = self.memory_manager.locate_and_get_context(ui_hash, semantic_layout, user_prompt)
                    mode = context_data.get("mode", "explore")
                    current_state_id = context_data.get("current_state_id")

            if mode == "navigate" and context_data.get("next_actions"):
                # Fast track: return the highest confidence action directly without VLM inference
                best_action = context_data["next_actions"][0]

                # Check confidence threshold before executing
                action_confidence = best_action.get("confidence", 1.0)
                if action_confidence < 0.8:
                    # Confidence too low, fall back to explore mode
                    mode = "explore"
                    print(f"[Navigate] Confidence {action_confidence:.2f} < 0.8, falling back to explore mode")
                else:
                    action = {
                        "_metadata": "do",
                        "action_type": best_action["type"],
                    }
                    if best_action.get("target"):
                        action["element"] = best_action["target"]

                    # Quick parse params
                    try:
                        import ast
                        params = ast.literal_eval(best_action.get("target_desc", "{}"))
                        action.update(params)
                    except:
                        pass

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
                return StepResult(
                    success=result.success,
                    finished=finished,
                    action=action,
                    thinking="[Graph Shortcut Navigated]",
                    message=result.message or action.get("message"),
                )
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
            critical_hints = self._detect_critical_scenario(current_app, screenshot.base64_data)
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
                    
                    # Convert MAI-UI action to AutoGLM format for memory tracking
                    if self._model_type == ModelType.MAIUI:
                        from phone_agent.actions.handler_maiui import convert_maiui_to_autoglm
                        action = convert_maiui_to_autoglm(parsed_action, screenshot.width, screenshot.height)
                    else:
                        # Build action dict for other specialized handlers (QwenVL, UI-TARS, etc.)
                        action = {
                            "_metadata": "finish" if parsed_action.action_type in ("terminate", "finished", "finish", "answer") else "do",
                            "action_type": parsed_action.action_type,
                            **parsed_action.params,
                        }
                        if parsed_action.action_type in ("terminate", "finished", "finish", "answer"):
                            action["message"] = parsed_action.params.get("content") or parsed_action.params.get("message", "Task completed")
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
            guarded = self._spec_guard_check(action, thinking, current_app)
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
                    task=self._current_task
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
