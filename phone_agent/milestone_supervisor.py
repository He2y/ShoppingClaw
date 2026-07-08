"""Strong-VLM milestone supervisor: low-frequency oversight of the GUI model.

Per-step strong-VLM planning tripled latency and contradicts the on-device
goal; a pure small model forgets and hallucinates on long tasks. This module
implements the middle path: the strong VLM decomposes the task ONCE at start
(creating the session memory file), then revisits only at milestones (every
N steps / subtask completion / stagnation / finish-gate rejection) to revise
the memory file and re-plan the remaining subtasks. Budget per task:
~1 + steps/N strong calls (N defaults to 5).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from phone_agent.memory.exploration.classifier import PageClassifier


# ── env switches ─────────────────────────────────────────────────────────


def milestone_enabled() -> bool:
    """Milestone supervision defaults ON; PHONE_AGENT_MILESTONE=0 disables."""
    return os.environ.get("PHONE_AGENT_MILESTONE", "1").strip() != "0"


def milestone_interval() -> int:
    try:
        return max(2, int(os.environ.get("PHONE_AGENT_MILESTONE_INTERVAL", "5")))
    except ValueError:
        return 5


def milestone_min_gap() -> int:
    try:
        return max(1, int(os.environ.get("PHONE_AGENT_MILESTONE_MIN_GAP", "2")))
    except ValueError:
        return 2


def milestone_max_calls(max_steps: int) -> int:
    try:
        configured = int(os.environ.get("PHONE_AGENT_MILESTONE_MAX_CALLS", "0"))
    except ValueError:
        configured = 0
    if configured > 0:
        return configured
    return 2 + max_steps // milestone_interval()


# ── shared pre-plan prompt (also used by agent._vlm_pre_plan fallback) ───


def build_preplan_prompt(task: str) -> str:
    return (
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


_CHECKPOINT_SYSTEM_PROMPT = (
    "你是手机购物任务的里程碑监督者。一个GUI执行模型正在分步执行任务，"
    "你每隔几步检查一次它的进度、修订会话记忆并重新规划剩余子任务。\n\n"
    "规则:\n"
    "1. 执行模型的自述不可信——已观察到它把超预算商品说成合规、虚报任务完成。"
    "一切判断以截图和已验证事实为准。\n"
    "2. 原始任务永远不变，你只能修订子任务和记录事实。\n"
    "3. completed_step_ids 只包含有截图或事实证据支持的子任务 id。\n"
    "4. 剩余子任务需要调整时给出 revised_subtasks（全量替换未完成部分）；"
    "无需调整时返回空数组 []。target_page 只能取: home, search_input, "
    "search_result, product_detail, spec_selection, cart, checkout, store, "
    "my_account, filter_panel（注意是 search_result 不是 search_results）。\n"
    "5. 安全约束：绝不指示提交订单、支付、确认收货、修改地址、登录。\n"
    "6. 任务目标在截图上得到确认时才可 task_complete=true；"
    "无法推进（验证码循环/商品不存在/反复失败）时 task_blocked=true。\n\n"
    "严格输出 JSON，不要任何额外文字:\n"
    '{"thought": "<简短分析>", "current_situation": "<一句话当前进展>", '
    '"completed_step_ids": [1, 2], "new_facts": [{"fact": "...", "kind": '
    '"product_selected|price_confirmed|cart_added|filter_applied|other"}], '
    '"revised_subtasks": [{"description": "...", "target_page": "..."}], '
    '"task_complete": false, "task_success": false, "task_blocked": false, '
    '"message": ""}'
)


@dataclass(frozen=True)
class CheckpointResult:
    thought: str = ""
    current_situation: str = ""
    completed_step_ids: list[int] = field(default_factory=list)
    new_facts: list[dict] = field(default_factory=list)
    revised_subtasks: list[dict] = field(default_factory=list)
    task_complete: bool = False
    task_success: bool = False
    task_blocked: bool = False
    message: str = ""
    trigger: str = ""
    changes: str = ""


@dataclass
class MilestoneTrigger:
    """Pure-logic trigger evaluation with debouncing. No VLM dependency."""

    interval: int = 5
    min_gap: int = 2
    max_calls: int = 22
    last_checkpoint_step: int = 0
    call_count: int = 0
    consecutive_failures: int = 0
    pending_finish_gate: bool = False
    finish_gate_count: int = 0
    stagnation_fired: bool = False
    disabled: bool = False

    def evaluate(
        self,
        step: int,
        *,
        subtask_done: bool = False,
        stagnating: bool = False,
    ) -> str | None:
        """Return the highest-priority trigger name, or None."""
        if self.disabled or self.call_count >= self.max_calls:
            return None
        gap = step - self.last_checkpoint_step

        if self.pending_finish_gate and self.finish_gate_count < 2:
            return "finish_gate"
        if stagnating and not self.stagnation_fired and gap >= self.min_gap:
            return "stagnation"
        if not stagnating:
            self.stagnation_fired = False
        if subtask_done and gap >= self.min_gap:
            return "subtask_done"
        if gap >= self.interval:
            return "interval"
        return None

    def record_fired(self, step: int, trigger: str) -> None:
        self.last_checkpoint_step = step
        self.call_count += 1
        if trigger == "finish_gate":
            self.pending_finish_gate = False
            self.finish_gate_count += 1
        if trigger == "stagnation":
            self.stagnation_fired = True

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= 2:
            self.disabled = True

    def record_success(self) -> None:
        self.consecutive_failures = 0


class MilestoneSupervisor:
    """Strong-VLM calls: initial decomposition + milestone checkpoints."""

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 30.0,
        max_image_width: int = 720,
    ):
        # Same provider chain as PageClassifier/TaskStepPlanner
        # (AMSG_STRONG_VLM_* → OFFLINE_VLM_* → PHONE_AGENT_*).
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

    # ── task-start decomposition ────────────────────────────────────────

    def initialize(self, task: str) -> dict:
        """Decompose the task; same output schema as agent._vlm_pre_plan."""
        prompt = build_preplan_prompt(task)
        for provider in self._proxy.providers:
            try:
                client = self._proxy._client_for_provider(provider)
                response = client.chat.completions.create(
                    model=provider.model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=512,
                    temperature=0.0,
                )
                raw = response.choices[0].message.content or ""
                self.last_raw = raw
                data = PageClassifier._parse_json_object(raw)
                if data:
                    return data
            except Exception:
                continue
        return {}

    # ── milestone checkpoint ────────────────────────────────────────────

    def checkpoint(
        self,
        screenshot_b64: str,
        width: int,
        height: int,
        *,
        memory_render: str,
        recent_steps: list[str],
        page_type: str,
        trigger: str,
        mechanical_facts: str = "",
    ) -> CheckpointResult | None:
        """Revise memory + re-plan. Returns None when all providers fail."""
        trigger_hint = {
            "stagnation": "注意：执行模型可能在原地打转，请考虑指示换一种路径。",
            "finish_gate": "注意：执行模型声称任务完成但缺少机械证据，请严格核实截图。",
            "final_confirm": "执行模型声称任务完成。请以截图为准核实，确认则 task_complete=true。",
        }.get(trigger, "")
        history = "\n".join(recent_steps[-8:]) or "（尚无执行记录）"
        user_text = (
            f"{memory_render}\n\n"
            f"分类器判定的当前页面类型: {page_type or 'unknown'}\n"
            + (f"机械事实快照: {mechanical_facts}\n" if mechanical_facts else "")
            + f"最近执行摘要:\n{history}\n"
            f"触发原因: {trigger}。{trigger_hint}\n"
            "请根据截图修订进度并输出 JSON。"
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
                        {"role": "system", "content": _CHECKPOINT_SYSTEM_PROMPT},
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
                    max_tokens=800,
                    temperature=0.0,
                )
                raw = response.choices[0].message.content or ""
                self.last_raw = raw
                data = PageClassifier._parse_json_object(raw)
                if not data:
                    continue
                return self.parse_checkpoint(data, trigger)
            except Exception:
                continue
        return None

    @staticmethod
    def parse_checkpoint(data: dict, trigger: str) -> CheckpointResult:
        completed: list[int] = []
        for item in data.get("completed_step_ids") or []:
            try:
                completed.append(int(item))
            except (TypeError, ValueError):
                continue
        facts = [f for f in (data.get("new_facts") or []) if isinstance(f, dict)]
        revised = [s for s in (data.get("revised_subtasks") or []) if isinstance(s, dict)]
        return CheckpointResult(
            thought=str(data.get("thought") or ""),
            current_situation=str(data.get("current_situation") or ""),
            completed_step_ids=completed,
            new_facts=facts,
            revised_subtasks=revised,
            task_complete=bool(data.get("task_complete")),
            task_success=bool(data.get("task_success")),
            task_blocked=bool(data.get("task_blocked")),
            message=str(data.get("message") or ""),
            trigger=trigger,
        )
