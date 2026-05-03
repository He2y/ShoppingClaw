"""Main PhoneAgent class for orchestrating phone automation."""

import json
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

from phone_agent.actions import ActionHandler
from phone_agent.actions.handler import do, finish, parse_action
from phone_agent.config import get_messages, get_system_prompt
from phone_agent.device_factory import get_device_factory
from phone_agent.model import ModelClient, ModelConfig
from phone_agent.model.adapters import ModelType, detect_model_type, get_adapter
from phone_agent.model.client import MessageBuilder


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


    def _clarify_task_if_needed(self, task: str, image_base64: str, current_app: str, memory_context: str = "") -> str:
        if self.agent_config.verbose:
            print(f"🤔 HITL: 检查任务是否需要主动澄清...")

        prompt = (
            f"用户下达了一个手机操作任务：「{task}」。\n"
            f"当前所在APP：{current_app}\n"
        )

        if memory_context:
            # Truncate memory context to avoid overwhelming the clarification prompt
            truncated_memory = memory_context[:400]
            prompt += (
                f"\n系统检索到以下相似历史记录：\n"
                f"{truncated_memory}\n"
                f"\n⚠️ 以上仅为历史参考，不代表当前用户的确切意图。\n"
            )

        prompt += (
            "\n请判断这个任务是否足够清晰、明确，能够直接执行？\n"
            "如果任务模糊（例如：想买东西但没说具体买什么、想发消息但没说发给谁/发什么、\n"
            "想点外卖但没指定平台/店铺/菜品、只说'帮我点个外卖''帮我买东西'等笼统表述），\n"
            "请回复需要向用户澄清的问题，格式必须为：'CLARIFY: 你的问题'\n"
            "如果任务已经足够清晰（包含具体目标或意图明确），请直接回复：'CLEAR'\n"
            "请严格遵循上述格式，不要输出其他多余内容。"
        )
        messages = [
            {"role": "system", "content": "你是一个严谨的手机助手任务分析员。"},
            {"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}}
            ]}
        ]
        try:
            # Use the raw client to avoid stream formatting issues
            completion = self.model_client.client.chat.completions.create(
                messages=messages,
                model=self.model_client.config.model_name,
                temperature=0.1,  # Low temp for logic task
            )
            res_content = completion.choices[0].message.content.strip()
            if res_content.startswith("CLARIFY:"):
                question = res_content[8:].strip()
                print(f"\n🙋 [主动提问] {question}")

                user_answer = ""
                import sys
                if self.clarification_callback:
                    user_answer = self.clarification_callback(question)
                elif sys.stdout.isatty():
                    user_answer = input(">>> (请输入补充信息，或直接回车跳过): ")

                if user_answer and user_answer.strip():
                    # Reconstruct task
                    reconstruct_prompt = (
                        f"原任务：「{task}」\n"
                        f"助手提问：「{question}」\n"
                        f"用户补充：「{user_answer}」\n"
                        "请根据上述信息，重写出一个完整、清晰的任务指令。直接输出重写后的指令，不要有任何前缀。"
                    )
                    recon_response = self.model_client.request([
                        {"role": "system", "content": "你是一个指令重写专家。"},
                        {"role": "user", "content": reconstruct_prompt}
                    ])
                    new_task = recon_response.raw_content.strip() if hasattr(recon_response, 'raw_content') else str(recon_response).strip()
                    print(f"✨ 任务已重组: {new_task}\n")
                    return new_task
        except Exception as e:
            if self.agent_config.verbose:
                print(f"⚠️ 任务澄清环节出错: {e}")

        return task

    def _detect_critical_scenario(self, current_app: str, screenshot_base64: str) -> list[str]:
        """
        Detect if current page belongs to critical decision nodes
        (e.g. product spec selection, multiple ambiguous options).
        Returns list of critical hints to be prepended to context.
        """
        critical_hints = []

        # Product specification selection pages (shopping/food delivery apps)
        shopping_apps = ["淘宝", "京东", "天猫", "拼多多", "美团", "饿了么", "瑞幸", "星巴克",
                         "Taobao", "JD", "Tmall", "Meituan", "Eleme", "Luckin", "Starbucks"]

        if any(app in (current_app or "") for app in shopping_apps):
            critical_hints.append(
                "【最高优先级规则】如果当前页面包含商品规格选择（颜色、尺码、口味、温度、糖度等），"
                "且用户未在原始指令中明确指定这些参数，你必须立即执行 "
                "do(action=\"Interact\", message=\"请问您需要什么规格/颜色/配置？\") "
                "询问用户。严禁接受系统默认选项或自行决定！"
            )

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

            # [HITL Active Clarification] - Always run on first step
            if is_first:
                clarified_task = self._clarify_task_if_needed(
                    user_prompt or self._current_task,
                    screenshot.base64_data,
                    current_app,
                    context_data.get("semantic_context", "")
                )
                if clarified_task != (user_prompt or self._current_task):  # lower threshold for better HITL trigger rate  # 提高阈��，更���易触发主��提问
                    user_prompt = clarified_task
                    self._current_task = clarified_task
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
        # 注入图谱语义上下文：让 VLM 知道相似任务轨迹
        # =============================================
        extra_context = context_data.get("semantic_context", "") if self.memory_manager else ""

        # =============================================
        # 关键场景检测：动态强化提示
        # =============================================
        if self.memory_manager:
            critical_hints = self._detect_critical_scenario(current_app, screenshot.base64_data)
            if critical_hints:
                critical_hint_text = "\n\n".join(critical_hints)
                # Prepend critical hints to ensure VLM sees them first
                extra_context = f"{critical_hint_text}\n\n{extra_context}" if extra_context else critical_hint_text
                if self.agent_config.verbose:
                    print(f"🎯 检测到关键场景，注入强提示")

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

        # Get model response
        try:
            msgs = get_messages(self.agent_config.lang)
            print("\n" + "=" * 50)
            print(f"💭 {msgs['thinking']}:")
            print("-" * 50)
            
            # # Print messages info for debugging
            # if self.agent_config.verbose:
            #     import json
            #     print(f"\n📨 Messages count: {len(self._context)}")
            #     print("=" * 50)
            #     for msg in self._context:
            #         # 创建一个副本用于打印，避免修改原始消息
            #         msg_to_print = msg.copy()
            #         
            #         # 如果 content 是列表，处理图片 base64（截断显示）
            #         if isinstance(msg_to_print.get("content"), list):
            #             content_copy = []
            #             for item in msg_to_print["content"]:
            #                 if isinstance(item, dict):
            #                     item_copy = item.copy()
            #                     # 如果是图片，截断 base64
            #                     if item_copy.get("type") == "image_url" and isinstance(item_copy.get("image_url"), dict):
            #                         image_url = item_copy["image_url"].copy()
            #                         if "url" in image_url and image_url["url"].startswith("data:"):
            #                             # 只显示前 50 个字符 + "..." + 后 20 个字符
            #                             url = image_url["url"]
            #                             if len(url) > 100:
            #                                 image_url["url"] = url[:50] + "...[base64 data truncated]..." + url[-20:]
            #                         item_copy["image_url"] = image_url
            #                     content_copy.append(item_copy)
            #                 else:
            #                     content_copy.append(item)
            #             msg_to_print["content"] = content_copy
            #         # 如果 content 是字符串且包含 base64，截断显示
            #         elif isinstance(msg_to_print.get("content"), str):
            #             content = msg_to_print["content"]
            #             if "data:image" in content and len(content) > 500:
            #                 # 截断 base64 部分
            #                 import re
            #                 msg_to_print["content"] = re.sub(
            #                     r'data:image/[^;]+;base64,[A-Za-z0-9+/=]{100,}',
            #                     lambda m: m.group(0)[:100] + '...[base64 truncated]...',
            #                     content
            #                 )
            #         
            #         # 直接打印完整的 message JSON 格式
            #         print(json.dumps(msg_to_print, ensure_ascii=False, indent=2))
            #     print("=" * 50)
            
            response = self.model_client.request(self._context)
        except Exception as e:
            if self.agent_config.verbose:
                traceback.print_exc()
            return StepResult(
                success=False,
                finished=True,
                action=None,
                thinking="",
                message=f"Model error: {e}",
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
            self.tracer.record_step(
                step=self._step_count,
                screenshot_base64=screenshot.base64_data,
                model_raw_output=response.raw_content,
                action=action,
                finished=finished,
            )

        if finished and self.agent_config.verbose:
            msgs = get_messages(self.agent_config.lang)
            print("\n" + "🎉 " + "=" * 48)
            print(
                f"✅ {msgs['task_completed']}: {result.message or action.get('message', msgs['done'])}"
            )
            print("=" * 50 + "\n")

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
