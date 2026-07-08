"""Strong-VLM exploration planner: plans, the GUI model only executes.

Real-device testing showed the small GUI action model cannot plan: it knows
how to ground "点击右上角的筛选按钮" into coordinates, but not that
"covering search_result->filter_panel" REQUIRES tapping the filter button.
This module gives the planning job to the strong VLM (same provider chain
as PageClassifier): each step it sees the screenshot plus the remaining
focus transitions and emits ONE short, concrete instruction for the GUI
model to execute.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from .classifier import PageClassifier


@dataclass(frozen=True)
class PlanStep:
    """One planning decision from the strong VLM."""

    instruction: str                 # concrete order for the GUI model, Chinese
    target_transition: tuple[str, str] | None = None
    expected_page: str = ""
    finish: bool = False
    message: str = ""
    thought: str = ""


@dataclass
class PlanContext:
    """Rolling context fed back to the planner each step."""

    remaining_edges: tuple[tuple[str, str], ...] = ()
    covered_edges: tuple[tuple[str, str], ...] = ()
    natural_goal: str = ""        # free-form goal, e.g. "探索个人中心和订单列表"
    recent_results: list[str] = field(default_factory=list)  # last few outcome lines

    def push_result(self, line: str, keep: int = 4) -> None:
        self.recent_results.append(line)
        del self.recent_results[:-keep]


_PLANNER_SYSTEM_PROMPT = (
    "你是手机App探索规划器。你的任务是指挥一个只会执行简单操作的GUI模型，"
    "用最少的步骤覆盖指定的页面转移（page transition）。\n"
    "每次你会看到当前屏幕截图、当前页面类型、剩余待覆盖的转移和最近几步的执行结果。\n\n"
    "规则:\n"
    "1. 每次只下达一条具体的操作指令，指令必须指向截图中可见的元素，"
    "例如『点击右上角的\"筛选\"按钮』『点击底部导航栏的购物车图标』。\n"
    "2. 优先选择能直接推进剩余转移的操作；当前页面不在任何剩余转移的源端时，"
    "指挥模型导航到最近的源端页面。\n"
    "3. 安全约束：绝不指挥提交订单、支付、确认收货、修改地址、登录。"
    "需要打开规格弹窗时可以点『加入购物车』（这不会下单）。\n"
    "4. 上一条指令失败（屏幕无变化/落到错误页面）时换一种方式，不要重复同样的指令。\n"
    "5. 所有剩余转移都已覆盖、或连续多次无法推进时，输出 finish。\n\n"
    "严格输出 JSON，不要任何额外文字:\n"
    '{"thought": "<简短分析>", "instruction": "<给执行模型的一条具体操作指令>", '
    '"target_transition": ["<源页面类型>", "<目标页面类型>"], '
    '"expected_page": "<执行后预期的页面类型>", "finish": false, "message": ""}\n'
    '完成时: {"thought": "...", "instruction": "", "target_transition": null, '
    '"expected_page": "", "finish": true, "message": "<总结>"}'
)


class StrongPlanner:
    """Per-step planning + round summary via the strong VLM provider chain."""

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 20.0,
        max_image_width: int = 720,
    ):
        # Reuse the classifier's provider chain resolution (AMSG_STRONG_VLM_*
        # -> OFFLINE_VLM_* -> PHONE_AGENT_*) and screenshot compression.
        self._proxy = PageClassifier(
            api_key=api_key,
            base_url=base_url,
            model=model,
            mode="full",
            timeout=timeout,
            max_image_width=max_image_width,
        )
        self.last_raw = ""

    def available(self) -> bool:
        return bool(self._proxy.providers)

    # ------------------------------------------------------------------
    # Per-step planning
    # ------------------------------------------------------------------

    def plan_step(
        self,
        screenshot_b64: str,
        width: int,
        height: int,
        *,
        current_page_type: str,
        context: PlanContext,
    ) -> PlanStep | None:
        """Return the next instruction, or None when the planner is unusable."""
        if not context.remaining_edges and not context.natural_goal:
            return PlanStep(instruction="", finish=True, message="所有目标转移已覆盖")

        covered = "、".join(f"{a}->{b}" for a, b in context.covered_edges) or "无"
        history = "\n".join(context.recent_results[-4:]) or "（本轮尚无执行记录）"
        if context.remaining_edges:
            remaining = "、".join(f"{a}->{b}" for a, b in context.remaining_edges)
            goal_line = f"剩余待覆盖转移: {remaining}"
            if context.natural_goal:
                goal_line += f"\n附加目标(自然语言): {context.natural_goal}"
        else:
            goal_line = (
                f"本轮目标(自然语言): {context.natural_goal}\n"
                "请围绕该目标探索相关功能模块，发现并走通其中的页面转移；"
                "目标已充分覆盖或无法继续推进时输出 finish。"
            )
        user_text = (
            f"{goal_line}\n"
            f"本轮已覆盖: {covered}\n"
            f"分类器判定的当前页面类型: {current_page_type}\n"
            f"最近执行结果:\n{history}\n"
            "请根据截图下达下一条指令。"
        )

        try:
            cropped = self._proxy._crop_screenshot(
                screenshot_b64, width, height, self._proxy.max_image_width
            )
        except Exception:
            cropped = screenshot_b64

        for provider in self._proxy.providers:
            try:
                client = self._proxy._client_for_provider(provider)
                response = client.chat.completions.create(
                    model=provider.model,
                    messages=[
                        {"role": "system", "content": _PLANNER_SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {"url": f"data:image/png;base64,{cropped}"},
                                },
                                {"type": "text", "text": user_text},
                            ],
                        },
                    ],
                    max_tokens=600,
                    temperature=0.0,
                )
                raw = response.choices[0].message.content or ""
                self.last_raw = raw
                data = PageClassifier._parse_json_object(raw)
                if not data:
                    continue
                pair = data.get("target_transition")
                target = None
                if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                    target = (str(pair[0]), str(pair[1]))
                return PlanStep(
                    instruction=str(data.get("instruction") or "").strip(),
                    target_transition=target,
                    expected_page=str(data.get("expected_page") or "").strip(),
                    finish=bool(data.get("finish")),
                    message=str(data.get("message") or ""),
                    thought=str(data.get("thought") or ""),
                )
            except Exception:
                continue
        return None

    # ------------------------------------------------------------------
    # Round summary
    # ------------------------------------------------------------------

    def summarize_round(self, app: str, goal_edges: tuple, covered_edges: tuple, steps: list[dict]) -> str:
        """One-paragraph round summary for the saved artifacts. Best effort."""
        try:
            step_lines = "\n".join(
                f"{s.get('step')}. [{s.get('page_type')}] "
                + json.dumps(s.get("action") or {}, ensure_ascii=False)[:80]
                for s in steps[:20]
            )
        except Exception:
            step_lines = ""
        prompt = (
            f"以下是对手机App「{app}」一轮空间图谱探索的记录。\n"
            f"本轮目标转移: {'、'.join(f'{a}->{b}' for a, b in goal_edges)}\n"
            f"实际覆盖: {'、'.join(f'{a}->{b}' for a, b in covered_edges) or '无'}\n"
            f"步骤:\n{step_lines}\n\n"
            "请用不超过100字总结本轮探索：完成了什么、卡在哪里、下一轮建议聚焦什么。只输出总结文字。"
        )
        for provider in self._proxy.providers:
            try:
                client = self._proxy._client_for_provider(provider)
                response = client.chat.completions.create(
                    model=provider.model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=300,
                    temperature=0.0,
                )
                text = (response.choices[0].message.content or "").strip()
                if text:
                    return text
            except Exception:
                continue
        return ""


def strong_planner_enabled() -> bool:
    """Planner defaults ON; AMSG_STRONG_PLANNER=0 disables."""
    return os.environ.get("AMSG_STRONG_PLANNER", "1").strip() != "0"
