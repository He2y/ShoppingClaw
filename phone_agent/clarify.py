"""
Clarification Agent - 购物场景主动澄清子代理

A dedicated sub-agent that detects task ambiguity in shopping/food-delivery
scenarios and proactively asks users for missing information before execution.

Reads PHONE_AGENT_MODEL / PHONE_AGENT_BASE_URL / PHONE_AGENT_API_KEY from .env
and creates its own OpenAI client for the clarification calls.
"""

import os
import sys
from dataclasses import dataclass

from openai import OpenAI


@dataclass
class ClarifyResult:
    """Result of task ambiguity check."""

    needs_clarification: bool
    question: str | None = None
    clarified_task: str | None = None


class ClarificationAgent:
    """
    Dedicated sub-agent for detecting ambiguous shopping/food-delivery tasks.

    Runs BEFORE PhoneAgent executes any actions. Uses the same autoglm-phone
    model to independently judge whether a task has enough information to
    proceed, and if not, asks the user targeted clarifying questions.

    Reads model configuration from environment variables set via .env:
        PHONE_AGENT_MODEL, PHONE_AGENT_BASE_URL, PHONE_AGENT_API_KEY
    """

    def __init__(self):
        model_name = os.getenv("PHONE_AGENT_MODEL", "autoglm-phone-9b")
        base_url = os.getenv("PHONE_AGENT_BASE_URL", "http://localhost:8000/v1")
        api_key = os.getenv("PHONE_AGENT_API_KEY", "EMPTY")

        self.model_name = model_name
        self.client = OpenAI(base_url=base_url, api_key=api_key)

    def check_and_clarify(
        self,
        task: str,
        image_base64: str,
        current_app: str = "",
        memory_context: str = "",
        clarification_callback=None,
        verbose: bool = True,
    ) -> ClarifyResult:
        """
        Check if the task is ambiguous. If so, ask the user for clarification
        and reconstruct a clear task.

        Args:
            task: The original user task.
            image_base64: Base64-encoded screenshot for context.
            current_app: Currently visible app name.
            memory_context: Retrieved similar-task context from memory system.
            clarification_callback: Optional async callback for non-TTY use.
            verbose: Print progress messages.

        Returns:
            ClarifyResult with needs_clarification flag and clarified task.
        """
        if verbose:
            print("🤔 [ClarificationAgent] 正在判断任务是否需要补充信息...")

        # Step 1: Detect ambiguity via VLM
        is_ambiguous, question = self._detect_ambiguity(
            task, image_base64, current_app, memory_context, verbose
        )

        if not is_ambiguous:
            if verbose:
                print("   ✅ 任务信息完整，直接执行")
            return ClarifyResult(needs_clarification=False)

        # Step 2: Ask user for missing information
        if verbose:
            print(f"\n{'─' * 50}")
            print(f"🙋 [ClarificationAgent] {question}")
            print(f"{'─' * 50}")

        user_answer = self._ask_user(question, clarification_callback)

        if not user_answer or not user_answer.strip():
            if verbose:
                print("   ⚠️ 用户未提供补充信息，按原任务继续（模型将自行判断）")
            return ClarifyResult(needs_clarification=False)

        # Step 3: Reconstruct clear task
        clarified_task = self._reconstruct_task(
            task, question, user_answer.strip(), verbose
        )

        return ClarifyResult(
            needs_clarification=True,
            question=question,
            clarified_task=clarified_task,
        )

    # ------------------------------------------------------------------
    # Private: ambiguity detection
    # ------------------------------------------------------------------

    def _detect_ambiguity(
        self,
        task: str,
        image_base64: str,
        current_app: str,
        memory_context: str,
        verbose: bool,
    ) -> tuple[bool, str | None]:
        """Call the VLM to judge whether the task is ambiguous."""
        prompt = self._build_ambiguity_prompt(task, current_app, memory_context)

        messages = [
            {
                "role": "system",
                "content": "你是一个专业的手机购物助手任务分析员。你的职责是判断用户任务是否有足够信息直接执行。",
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_base64}"
                        },
                    },
                ],
            },
        ]

        try:
            response = self.client.chat.completions.create(
                messages=messages,
                model=self.model_name,
                temperature=0.1,
            )
            content = (response.choices[0].message.content or "").strip()

            # Robust parsing: search for CLARIFY: or CLEAR anywhere in content
            content_upper = content.upper()
            if "CLARIFY:" in content_upper:
                idx = content_upper.find("CLARIFY:")
                question = content[idx + 9:].strip()
                # Take only the first line as the question
                question = question.split("\n")[0].strip().strip('"').strip("'")
                if question:
                    return True, question
                # Question extraction failed
                return False, None

            # Either "CLEAR" found or unknown response → treat as clear
            return False, None

        except Exception as e:
            if verbose:
                print(f"   ⚠️ ClarificationAgent VLM 调用失败: {e}")
            return False, None

    def _build_ambiguity_prompt(
        self, task: str, current_app: str, memory_context: str
    ) -> str:
        """Build the structured prompt for ambiguity detection."""
        prompt = f"""=== 用户任务 ===
「{task}」

=== 当前屏幕 ===
当前所在APP：{current_app or "桌面"}"""

        if memory_context:
            truncated = memory_context[:400]
            prompt += f"""

=== 历史参考（仅供参考，不等于当前用户意图）===
{truncated}"""

        prompt += f"""

=== 任务明确度判断标准 ===

以下情况属于【任务明确 ✅】，回复 CLEAR：
- 指定了平台+具体商品/菜品/店铺（如"在美团点麦当劳巨无霸套餐"）
- 指定了联系人和内容（如"给张三发微信说你好"）
- 指定了搜索关键词和目标明确（如"搜一下iPhone 16最新价格"）
- 简单的工具类操作（如"打开计算器"、"打开设置"）
- 指定了平台+明确行动但具体选择可灵活判断

以下情况属于【任务模糊 ❌】，回复 CLARIFY: [问题]：
- 只说"帮我点个外卖"、"帮我买个东西"、"帮我点杯咖啡"——缺少平台/商品/店铺
- 指定了平台但没说具体要什么（如"在淘宝上帮我买件衣服"）
- 指定了商品类别但没指定具体品牌/规格/数量
- 涉及多个可选方案且用户未表达偏好

=== 输出格式（必须严格遵守！）===
如果任务明确 → 只回复一个词：CLEAR
如果任务模糊 → 只回复：CLARIFY: [你的具体问题]

【提问要求】：
- 问题必须具体、自然，引导用户补充缺失的关键信息
- 参考当前屏幕上可见的APP来建议可选平台
- 如果历史参考中有相关偏好，可以提及供用户确认
- 不要自问自答，问题是需要用户来回答的

【正确示例】：
「帮我点个外卖」→ CLARIFY: 我看到您手机上有美团、京东和饿了么，请问您想在哪个平台点外卖？想吃什么菜或哪家店呢？
「在淘宝上帮我买件衣服」→ CLARIFY: 请问您想买什么类型的衣服？有偏好的品牌、颜色或尺码要求吗？
「帮我点杯咖啡」→ CLARIFY: 我看到您有瑞幸和星巴克，请问想用哪个APP？想喝什么咖啡呢？
「给妈妈发消息」→ CLARIFY: 请问想通过微信还是其他APP联系妈妈？想发什么内容呢？
「打开计算器」→ CLEAR
「在美团上点麦当劳巨无霸套餐送到默认地址」→ CLEAR

现在请判断：「{task}」"""

        return prompt

    # ------------------------------------------------------------------
    # Private: user interaction
    # ------------------------------------------------------------------

    @staticmethod
    def _ask_user(question: str, clarification_callback=None) -> str:
        """Prompt the user for additional information."""
        if clarification_callback:
            return clarification_callback(question)
        if sys.stdout.isatty():
            return input(">>> (请输入补充信息，或直接回车跳过): ")
        return ""

    # ------------------------------------------------------------------
    # Private: task reconstruction
    # ------------------------------------------------------------------

    def _reconstruct_task(
        self,
        original_task: str,
        question: str,
        user_answer: str,
        verbose: bool,
    ) -> str:
        """Reconstruct a clear, executable task from the Q&A."""
        prompt = f"""请根据以下问答，重写出一个完整、清晰、可直接操作的手机任务指令。

原任务：「{original_task}」
助手提问：「{question}」
用户补充：「{user_answer}」

要求：
1. 整合所有信息为一条流畅的任务指令
2. 明确平台/APP、商品名/店铺名/联系人、关键参数
3. 直接输出重组后的指令（50字以内），不要任何前缀或解释"""

        try:
            response = self.client.chat.completions.create(
                messages=[
                    {
                        "role": "system",
                        "content": "你是任务指令重写专家，输出简洁明确的任务指令。",
                    },
                    {"role": "user", "content": prompt},
                ],
                model=self.model_name,
                temperature=0.3,
            )
            new_task = (response.choices[0].message.content or "").strip()
            if verbose:
                print(f"📝 重组任务: {new_task}\n")
            return new_task
        except Exception as e:
            if verbose:
                print(f"   ⚠️ 任务重组失败: {e}")
            return f"{original_task}（补充：{user_answer}）"
