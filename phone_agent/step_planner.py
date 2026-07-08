"""Strong-VLM task planner for the main agent loop: plan, then ground.

Real-device runs showed the small GUI model cannot be trusted with per-step
reasoning: it hallucinated price compliance three times in one task (¥140 /
¥172 / ¥201 declared "within 500-1000元"), never actually applied the price
filter, and fabricated a success summary. Mirroring the offline explorer's
proven split (memory/exploration/planner.py): the strong VLM sees the
screenshot each step and issues ONE concrete instruction; the small GUI
model only grounds it into coordinates.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from phone_agent.memory.exploration.classifier import PageClassifier


@dataclass(frozen=True)
class TaskPlanStep:
    """One planning decision from the strong VLM."""

    instruction: str            # concrete order for the GUI model, Chinese
    expected_page: str = ""
    finish: bool = False
    success: bool = False
    message: str = ""
    need_human: bool = False
    thought: str = ""


@dataclass
class TaskPlanContext:
    """Rolling execution feedback fed to the planner each step."""

    recent_results: list[str] = field(default_factory=list)

    def push_result(self, line: str, keep: int = 5) -> None:
        self.recent_results.append(line)
        del self.recent_results[:-keep]


_SYSTEM_PROMPT = (
    "你是手机购物任务规划器。你指挥一个只会执行简单操作（点击/输入/滑动）的"
    "GUI模型完成用户任务。每步你会看到当前屏幕截图、页面类型、任务要求和最近"
    "几步的执行结果。\n\n"
    "规则:\n"
    "1. 每次只下达一条具体指令，必须指向截图中可见的元素，例如"
    "『点击右上角的\"筛选\"图标』『在最低价输入框输入500』。\n"
    "2. 约束由你负责核实：价格区间、规格等要求必须以截图为准逐项确认，"
    "执行模型的判断不可信。商品价格不在用户区间内时绝不允许选择或加购。\n"
    "3. 价格筛选必须分别填写最低价和最高价两个输入框并确认；"
    "确认后检查结果列表价格是否真的落在区间内，否则重新筛选。\n"
    "4. 安全约束：绝不指挥提交订单、支付、确认收货、修改地址、登录。"
    "打开规格弹窗可以点『加入购物车』（不会下单）。\n"
    "5. 上一条指令失败（屏幕无变化/落到错误页面）时换一种方式，不要重复。\n"
    "6. 屏幕出现验证码/短信验证/安全弹窗时输出 need_human。\n"
    "7. 任务目标在截图上得到确认（如规格弹窗显示已加入购物车成功）后才输出 "
    "finish，并如实给出 success 与总结；无法继续推进时输出 finish 且 "
    "success=false。\n\n"
    "严格输出 JSON，不要任何额外文字:\n"
    '{"thought": "<简短分析>", "instruction": "<一条具体操作指令>", '
    '"expected_page": "<执行后预期页面类型>", "finish": false, '
    '"success": false, "need_human": false, "message": ""}'
)


class TaskStepPlanner:
    """Per-step task planning via the strong VLM provider chain."""

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 25.0,
        max_image_width: int = 720,
    ):
        # Same provider chain as PageClassifier (AMSG_STRONG_VLM_* →
        # OFFLINE_VLM_* → PHONE_AGENT_*), same screenshot compression.
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

    def plan_step(
        self,
        screenshot_b64: str,
        width: int,
        height: int,
        *,
        task: str,
        current_page_type: str,
        constraints: str = "",
        progress: str = "",
        context: TaskPlanContext | None = None,
    ) -> TaskPlanStep | None:
        """Return the next instruction, or None when the planner is unusable."""
        history = "\n".join((context.recent_results if context else [])[-5:]) or "（尚无执行记录）"
        user_text = (
            f"用户任务: {task}\n"
            + (f"关键约束: {constraints}\n" if constraints else "")
            + (f"任务进度: {progress}\n" if progress else "")
            + f"分类器判定的当前页面类型: {current_page_type or 'unknown'}\n"
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
                        {"role": "system", "content": _SYSTEM_PROMPT},
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
                return TaskPlanStep(
                    instruction=str(data.get("instruction") or "").strip(),
                    expected_page=str(data.get("expected_page") or "").strip(),
                    finish=bool(data.get("finish")),
                    success=bool(data.get("success")),
                    need_human=bool(data.get("need_human")),
                    message=str(data.get("message") or ""),
                    thought=str(data.get("thought") or ""),
                )
            except Exception:
                continue
        return None


def main_planner_enabled() -> bool:
    """Strong step planner is opt-in (PHONE_AGENT_STRONG_PLANNER=1).

    Default OFF: per-step cloud planning tripled latency and contradicts the
    on-device deployment goal — the small model stays primary; kept for
    ablation experiments.
    """
    return os.environ.get("PHONE_AGENT_STRONG_PLANNER", "0").strip() == "1"
