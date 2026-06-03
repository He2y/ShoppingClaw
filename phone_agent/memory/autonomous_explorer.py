"""Model-driven autonomous exploration for mobile apps.

Two-tier VLM architecture for efficiency:
  - Strong VLM (glm-5v-turbo): page classification + action planning
  - Action VLM (autoglm-phone): execute specific instructions with minimal prompt

Per-round call pattern (optimized):
  1. Classify page via PageClassifier           [1 VLM call, cached if unchanged]
  2. Extract functionalities                    [0 VLM calls, deterministic]
  3. Plan actions via StrongVLMPlanner          [1 VLM call to glm-5v-turbo]
  4. Execute each step via action VLM           [1-3 VLM calls, minimal prompt]
  5. Classify final page via PageClassifier     [1 VLM call]
  Total: 4-6 calls per round (down from 7+, each faster)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from phone_agent.actions.handler import ActionHandler, parse_action
from phone_agent.config.apps import get_package_name
from phone_agent.device_factory import DeviceFactory
from phone_agent.model.client import MessageBuilder, ModelClient

from phone_agent.memory.offline_explorer import (
    PageClassifier,
    PageInfo,
    ShoppingPageType,
    Trajectory,
)
from phone_agent.spatial.active_builder import ActiveGraphBuilder
from phone_agent.spatial.coverage_metrics import compute_functionality_coverage
from phone_agent.spatial.edge_lifecycle import EdgeLifecycleManager
from phone_agent.spatial.exploration_queue import ExplorationJob, ExplorationQueueBuilder
from phone_agent.spatial.functionality import (
    FunctionalityExtractor,
    FunctionalityItem,
    StrongVLMFunctionalityExtractor,
    canonical_role_from_transition,
)
from phone_agent.spatial.functionality_cluster import FunctionalityCluster, FunctionalityClusterer
from phone_agent.spatial.hypothesis import EdgeHypothesisGenerator
from phone_agent.spatial.quality_gate import FunctionalityQualityGate
from phone_agent.spatial.semantics import ScreenSemanticsExtractor
from phone_agent.spatial.task_synthesis import TaskSynthesizer, resolve_strong_vlm_config


# ── Constants ─────────────────────────────────────────────────

_HIGH_RISK_PAGE_TYPES_STR = frozenset({
    "checkout", "payment", "address", "login", "permission",
})

_UNSAFE_ACTION_TOKENS = (
    "支付", "付款", "立即支付", "提交订单", "确认订单", "下单",
    "结算", "去结算", "pay", "payment", "submit", "checkout",
    "buy now", "logout", "log out", "switch account",
    "退出登录", "切换账号", "注销账号",
)

_SCREEN_CHANGE_HASH_LEN = 2000

_MINIMAL_ACTION_SYSTEM_PROMPT = (
    "你是手机操作执行器。根据指令对当前屏幕执行一个操作。\n"
    "输出格式：thinking{简短理由} <answer>{操作}</answer>\n"
    "操作格式：\n"
    'do(action="Tap", element=[x,y]) 坐标0-999\n'
    'do(action="Type", text="xxx")\n'
    'do(action="Swipe", start=[x1,y1], end=[x2,y2])\n'
    'do(action="Back")\n'
    'finish(message="xxx")\n'
)


# ── Data Structures ───────────────────────────────────────────

@dataclass(frozen=True)
class ExplorationPolicy:
    max_total_steps: int = 80
    max_job_steps: int = 3
    max_dry_rounds: int = 5
    job_queue_limit: int = 20
    settle_delay: float = 2.0
    use_strong_vlm_extractor: bool = False
    convergence_window: int = 3
    active_exploration: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_total_steps": self.max_total_steps,
            "max_job_steps": self.max_job_steps,
            "max_dry_rounds": self.max_dry_rounds,
            "job_queue_limit": self.job_queue_limit,
            "settle_delay": self.settle_delay,
            "use_strong_vlm_extractor": self.use_strong_vlm_extractor,
            "convergence_window": self.convergence_window,
            "active_exploration": self.active_exploration,
        }


@dataclass(frozen=True)
class JobExecutionResult:
    job: ExplorationJob
    steps_taken: int
    new_page_types_discovered: tuple[str, ...] = ()
    new_transitions_discovered: int = 0
    new_functionality_clusters: int = 0
    outcome: str = "success"
    deviation_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job.job_id,
            "target_description": self.job.target_description,
            "steps_taken": self.steps_taken,
            "new_page_types_discovered": list(self.new_page_types_discovered),
            "new_transitions_discovered": self.new_transitions_discovered,
            "new_functionality_clusters": self.new_functionality_clusters,
            "outcome": self.outcome,
            "deviation_reason": self.deviation_reason,
        }


# ── Convergence Tracker ──────────────────────────────────────

class ConvergenceTracker:
    """Empirical convergence: stop after N consecutive dry rounds."""

    def __init__(self, dry_round_limit: int = 5, window: int = 3) -> None:
        self._dry_round_limit = dry_round_limit
        self._window = window
        self._history: list[dict[str, int]] = []
        self._consecutive_dry: int = 0

    def record_round(
        self,
        new_clusters: int,
        new_page_types: int,
        new_transitions: int,
    ) -> None:
        self._history.append({
            "new_clusters": new_clusters,
            "new_page_types": new_page_types,
            "new_transitions": new_transitions,
        })
        is_dry = new_clusters == 0 and new_page_types == 0
        self._consecutive_dry = self._consecutive_dry + 1 if is_dry else 0

    @property
    def converged(self) -> bool:
        return self._consecutive_dry >= self._dry_round_limit

    @property
    def dry_rounds(self) -> int:
        return self._consecutive_dry

    @property
    def total_rounds(self) -> int:
        return len(self._history)

    def summary(self) -> dict[str, Any]:
        return {
            "total_rounds": self.total_rounds,
            "consecutive_dry_rounds": self._consecutive_dry,
            "dry_round_limit": self._dry_round_limit,
            "converged": self.converged,
            "history": list(self._history),
        }


# ── Safety Functions ─────────────────────────────────────────

def is_safe_exploration_action(
    page_type: str,
    action: dict[str, Any],
    reasoning: str = "",
) -> bool:
    """Generic safety filter using string page types."""
    if action.get("_metadata") == "finish":
        return True
    if page_type in _HIGH_RISK_PAGE_TYPES_STR:
        return False
    action_text = json.dumps(action, ensure_ascii=False).lower()
    if any(token.lower() in action_text for token in _UNSAFE_ACTION_TOKENS):
        return False
    if _unsafe_intent_in_reasoning(reasoning, page_type):
        return False
    return True


def _unsafe_intent_in_reasoning(reasoning: str, page_type: str) -> bool:
    action_markers = ("我将", "我要", "准备", "下一步", "接下来", "现在", "让我", "tap")
    negation_markers = ("不要", "不能", "禁止", "避免", "不应该", "不会", "不点击")
    task_markers = ("用户要求", "任务流程", "具体步骤", "根据任务", "给出了", "包括：", "需要：")
    for raw_line in reasoning.splitlines():
        line = raw_line.strip().lower()
        if not line:
            continue
        if any(marker in line for marker in task_markers):
            continue
        if line[:2].rstrip(".、").isdigit():
            continue
        if any(marker in line for marker in negation_markers):
            continue
        if not any(marker in line for marker in action_markers):
            continue
        if any(token.lower() in line for token in _UNSAFE_ACTION_TOKENS):
            return True
    return False


def _generic_transition_rejection(
    source_type: str,
    action: dict[str, Any],
    target_type: str,
) -> str:
    """Relaxed transition filter — no shopping-specific flow whitelist."""
    if source_type in _HIGH_RISK_PAGE_TYPES_STR or target_type in _HIGH_RISK_PAGE_TYPES_STR:
        return "high-risk page boundary"
    action_type = str(action.get("action") or action.get("action_type") or "").lower()
    if source_type == target_type:
        if source_type in {"search_input", "filter_panel"} and action_type in {"tap", "type", "type_name", "input"}:
            return ""
        return "self-loop or unchanged screen"
    if action_type in {"wait"}:
        return "non-navigation action"
    return ""


# ── Strong VLM Planner ──────────────────────────────────────

class StrongVLMPlanner:
    """Uses glm-5v-turbo to generate a concrete multi-step action plan.

    Called once per round before handing steps to the action VLM.
    Returns a list of natural-language instructions that the action
    VLM can execute with minimal context.
    """

    _PLAN_SYSTEM_PROMPT = (
        "你是移动应用UI分析师。分析截屏，根据探索目标生成1-3步具体操作指令。\n"
        "输出纯JSON，不要其他内容：\n"
        '{"steps": [\n'
        '  {"instruction": "具体操作描述，如：点击顶部搜索框", "action_type": "Tap"},\n'
        '  {"instruction": "在搜索框输入耳机", "action_type": "Type"}\n'
        "]}\n\n"
        "规则：\n"
        "- instruction 必须描述屏幕上可见的具体元素位置\n"
        "- action_type: Tap/Type/Swipe/Back 之一\n"
        "- 不要生成支付、结算、登录、地址相关操作\n"
        "- 在搜索页时必须指定搜索关键词（如'耳机''手机壳'）\n"
        "- 遇到弹窗时用Back关闭\n"
    )

    def __init__(self) -> None:
        self._client: Any | None = None
        self._model: str = ""
        self._configured = False
        self._init_client()

    def _init_client(self) -> None:
        from dotenv import load_dotenv
        load_dotenv()
        candidates = [
            ("AMSG_STRONG_VLM_BASE_URL", "AMSG_STRONG_VLM_MODEL", "AMSG_STRONG_VLM_API_KEY"),
            ("OFFLINE_VLM_BASE_URL", "OFFLINE_VLM_MODEL", "OFFLINE_VLM_API_KEY"),
        ]
        for base_key, model_key, api_key in candidates:
            base_url = os.environ.get(base_key, "")
            model = os.environ.get(model_key, "")
            key = os.environ.get(api_key, "")
            if base_url and model and key:
                try:
                    from openai import OpenAI
                    self._client = OpenAI(base_url=base_url, api_key=key, timeout=15.0)
                    self._model = model
                    self._configured = True
                except Exception:
                    pass
                break

    @property
    def configured(self) -> bool:
        return self._configured

    def plan(
        self,
        screenshot_base64: str,
        page_type: str,
        page_elements: dict[str, Any],
        job_description: str,
        discovered_types: list[str],
    ) -> list[dict[str, str]]:
        if not self._configured or not self._client:
            return self._fallback_plan(page_type, page_elements)

        elements_str = ", ".join(f"{k}: {v}" for k, v in (page_elements or {}).items())
        user_text = (
            f"探索目标：{job_description}\n"
            f"当前页面：{page_type}\n"
            f"页面元素：{elements_str or '未知'}\n"
            f"已发现页面类型：{', '.join(discovered_types) or '无'}\n"
            "生成操作计划（JSON）："
        )
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                temperature=0,
                max_tokens=500,
                messages=[
                    {"role": "system", "content": self._PLAN_SYSTEM_PROMPT},
                    {"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_base64}"}},
                        {"type": "text", "text": user_text},
                    ]},
                ],
            )
            content = response.choices[0].message.content or ""
            return self._parse_plan(content)
        except Exception:
            return self._fallback_plan(page_type, page_elements)

    def _parse_plan(self, content: str) -> list[dict[str, str]]:
        content = content.strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?", "", content).strip()
            content = re.sub(r"```$", "", content).strip()
        match = re.search(r"\{.*\}", content, flags=re.DOTALL)
        if not match:
            return []
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []
        steps = data.get("steps") or []
        return [
            {"instruction": str(s.get("instruction", "")), "action_type": str(s.get("action_type", "Tap"))}
            for s in steps
            if isinstance(s, dict) and s.get("instruction")
        ][:3]

    @staticmethod
    def _fallback_plan(page_type: str, elements: dict[str, Any]) -> list[dict[str, str]]:
        if page_type == "home":
            if any(k for k in (elements or {}) if "search" in k.lower() or "搜索" in str(elements.get(k, ""))):
                return [{"instruction": "点击搜索框进入搜索页", "action_type": "Tap"}]
            return [{"instruction": "点击页面中最显眼的功能入口", "action_type": "Tap"}]
        if page_type == "search_input":
            return [
                {"instruction": "在搜索框输入'耳机'", "action_type": "Type"},
                {"instruction": "点击搜索按钮或键盘搜索键", "action_type": "Tap"},
            ]
        if page_type == "search_result":
            return [{"instruction": "点击第一个商品卡片进入详情", "action_type": "Tap"}]
        if page_type == "product_detail":
            return [{"instruction": "点击底部加入购物车或规格选择按钮", "action_type": "Tap"}]
        if page_type in ("dialog", "permission"):
            return [{"instruction": "关闭弹窗", "action_type": "Back"}]
        if page_type in _HIGH_RISK_PAGE_TYPES_STR:
            return [{"instruction": "返回上一页", "action_type": "Back"}]
        return [{"instruction": "探索页面上可见的功能按钮", "action_type": "Tap"}]


# ── Prompt Building ──────────────────────────────────────────

def _build_autonomous_system_prompt() -> str:
    """Full system prompt — used only as legacy fallback."""
    today = datetime.today()
    weekday_names = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    weekday = weekday_names[today.weekday()]
    formatted_date = today.strftime("%Y年%m月%d日") + " " + weekday

    return (
        "今天的日期是: " + formatted_date + "\n"
        "你是一个移动应用自主探索智能体。你的目标是系统性地发现App的页面类型和跳转关系。\n"
        "你必须严格按照要求输出以下格式：\n"
        " thinking{think} response\n"
        "<answer>{action}</answer>\n\n"

        "操作指令及其作用如下：\n"
        'do(action="Tap", element=[x,y])  点击屏幕上的特定点（0,0到999,999）\n'
        'do(action="Swipe", start=[x1,y1], end=[x2,y2])  滑动操作\n'
        'do(action="Back")  导航返回上一个屏幕\n'
        'do(action="Type", text="xxx")  输入文本\n'
        'do(action="Wait", duration="x seconds")  等待页面加载\n'
        'finish(message="xxx")  结束当前探索任务\n\n'

        "=== 重要约束 ===\n"
        "- 不需要登录，遇到登录界面请Back\n"
        "- 不要下单购买任何商品\n"
        "- 不要进行支付、确认地址、结算等不可逆操作\n"
        "- 遇到广告弹窗点X关闭或用Back跳过\n"
    )


# ── Helpers ──────────────────────────────────────────────────

def _page_info_to_dict(page_info: PageInfo) -> dict[str, Any]:
    """Bridge PageInfo (dataclass with enum) to functionality extractor dict."""
    return {
        "page_type": page_info.page_type.value,
        "app": page_info.app,
        "elements": page_info.elements,
        "summary": page_info.semantic_summary,
        "screenshot_hash": page_info.screenshot_hash,
        "screenshot_base64": page_info.screenshot_base64,
    }


# ── AutonomousExplorer ───────────────────────────────────────

class AutonomousExplorer:
    """Model-driven autonomous discovery explorer for mobile apps.

    Two-tier VLM: strong model plans, action model executes.
    """

    def __init__(
        self,
        app_name: str,
        device_factory: DeviceFactory,
        model_client: ModelClient,
        policy: ExplorationPolicy | None = None,
        storage_dir: str = "memory_db/exploration/autonomous",
        classifier_api_key: str | None = None,
        classifier_base_url: str | None = None,
        classifier_model: str | None = None,
        classifier_mode: str = "fast",
        classifier_timeout: float = 8.0,
        classifier_max_image_width: int = 720,
        device_id: str | None = None,
        verbose: bool = True,
    ) -> None:
        self.app_name = app_name
        self.device = device_factory
        self.vlm = model_client
        self.policy = policy or ExplorationPolicy()
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.device_id = device_id
        self.verbose = verbose

        self.action_handler = ActionHandler(device_id=device_id)
        self.classifier = PageClassifier(
            api_key=classifier_api_key,
            base_url=classifier_base_url,
            model=classifier_model,
            mode=classifier_mode,
            timeout=classifier_timeout,
            max_image_width=classifier_max_image_width,
        )
        self.semantics_extractor = ScreenSemanticsExtractor(schema_name="shopping")
        self.edge_hypothesis_gen = EdgeHypothesisGenerator(schema_name="shopping")
        self.active_builder = ActiveGraphBuilder()
        self.planner = StrongVLMPlanner()

        self.functionality_extractor = FunctionalityExtractor()
        self.strong_vlm_extractor: StrongVLMFunctionalityExtractor | None = None
        if self.policy.use_strong_vlm_extractor:
            config = resolve_strong_vlm_config()
            if config.configured:
                self.strong_vlm_extractor = StrongVLMFunctionalityExtractor(config)
        self.clusterer = FunctionalityClusterer()
        self.queue_builder = ExplorationQueueBuilder()
        self.task_synthesizer = TaskSynthesizer()
        self.edge_lifecycle = EdgeLifecycleManager()

        self._all_items: list[FunctionalityItem] = []
        self._all_clusters: list[FunctionalityCluster] = []
        self._discovered_pages: dict[str, PageInfo] = {}
        self._transitions: list[dict[str, Any]] = []
        self._rejected_transitions: list[dict[str, Any]] = []
        self._trajectories: list[Trajectory] = []
        self._job_results: list[JobExecutionResult] = []
        self._convergence = ConvergenceTracker(
            dry_round_limit=self.policy.max_dry_rounds,
            window=self.policy.convergence_window,
        )
        self._total_steps: int = 0

    # ── Top-Level Entry ──────────────────────────────────────

    def explore(self) -> list[Trajectory]:
        """Run model-driven autonomous exploration."""
        self._log(f"\n{'=' * 60}")
        self._log(f"  Autonomous Explorer: {self.app_name}")
        self._log(f"  Policy: max_steps={self.policy.max_total_steps}, "
                   f"max_dry_rounds={self.policy.max_dry_rounds}")
        self._log(f"  Planner: {'glm-5v-turbo' if self.planner.configured else 'fallback'}")
        self._log(f"{'=' * 60}\n")

        package = get_package_name(self.app_name)
        if not package:
            self._log(f"  ERROR: Unknown app '{self.app_name}'")
            return []

        self.device.launch_app(self.app_name, self.device_id)
        time.sleep(4)

        trajectory = self._autonomous_loop()
        self._trajectories = [trajectory]
        self._save_results(trajectory)

        self._log(f"\n{'=' * 60}")
        self._log(f"  Done: {len(self._discovered_pages)} pages, "
                   f"{len(self._transitions)} transitions, "
                   f"{len(self._all_clusters)} clusters")
        self._log(f"  {self._total_steps} steps, "
                   f"{self._convergence.total_rounds} rounds, "
                   f"converged={self._convergence.converged}")
        self._log(f"{'=' * 60}\n")
        return self._trajectories

    # ── Outer Loop (Job-Driven) ──────────────────────────────

    def _autonomous_loop(self) -> Trajectory:
        trajectory = Trajectory(task="autonomous_exploration", app=self.app_name)
        current_page: PageInfo | None = None

        while (
            not self._convergence.converged
            and self._total_steps < self.policy.max_total_steps
        ):
            if current_page is None:
                screenshot = self.device.get_screenshot(self.device_id)
                current_page = self._classify(screenshot)

            self._record_page(current_page)
            clusters_before = len(self._all_clusters)
            pages_before = len(self._discovered_pages)
            self._extract_and_cluster(current_page)

            jobs = self.queue_builder.build_jobs(
                self._all_clusters, limit=self.policy.job_queue_limit,
            )
            if jobs:
                job = jobs[0]
                self._log(f"  [round {self._convergence.total_rounds + 1}] "
                           f"job: {job.target_description[:60]} "
                           f"(priority={job.priority:.2f})")
            else:
                job = self._synthesize_fallback_job(current_page)
                self._log(f"  [round {self._convergence.total_rounds + 1}] "
                           f"fallback: {job.target_description[:60]}")

            result, current_page = self._execute_job(job, current_page, trajectory)
            self._job_results.append(result)

            new_clusters = len(self._all_clusters) - clusters_before
            new_pages = len(self._discovered_pages) - pages_before
            self._convergence.record_round(
                new_clusters=new_clusters + result.new_functionality_clusters,
                new_page_types=new_pages + len(result.new_page_types_discovered),
                new_transitions=result.new_transitions_discovered,
            )
            self.edge_lifecycle.advance_step()

            if result.outcome == "blocked":
                screenshot = self.device.get_screenshot(self.device_id)
                self._rollback(screenshot.width, screenshot.height)
                current_page = None

            self._log(f"    result: {result.outcome}, "
                       f"+{result.new_transitions_discovered} transitions, "
                       f"dry={self._convergence.dry_rounds}")

        return trajectory

    # ── Inner Loop (Plan-Then-Execute) ───────────────────────

    def _execute_job(
        self,
        job: ExplorationJob,
        start_page: PageInfo,
        trajectory: Trajectory,
    ) -> tuple[JobExecutionResult, PageInfo | None]:
        """Execute a job using plan-then-execute pattern.

        Returns (result, last_classified_page) so the outer loop can
        reuse the classification without an extra VLM call.
        """
        current_page = start_page
        steps_taken = 0
        new_pages: list[str] = []
        new_transitions = 0
        outcome = "success"
        deviation_reason = ""

        screenshot = self.device.get_screenshot(self.device_id)
        discovered_types = sorted({p.page_type.value for p in self._discovered_pages.values()})
        action_plan = self.planner.plan(
            screenshot_base64=screenshot.base64_data,
            page_type=current_page.page_type.value,
            page_elements=current_page.elements,
            job_description=job.target_description,
            discovered_types=discovered_types,
        )
        if not action_plan:
            action_plan = [{"instruction": "探索页面上可见的按钮", "action_type": "Tap"}]

        self._log(f"    plan: {[s['instruction'][:30] for s in action_plan]}")

        system_message = MessageBuilder.create_system_message(_MINIMAL_ACTION_SYSTEM_PROMPT)

        for step_idx, planned_step in enumerate(action_plan):
            if self._total_steps >= self.policy.max_total_steps:
                break

            instruction = planned_step["instruction"]
            screenshot = self.device.get_screenshot(self.device_id)

            user_text = f"执行操作：{instruction}"
            request_context = [
                system_message,
                MessageBuilder.create_user_message(
                    text=user_text,
                    image_base64=screenshot.base64_data,
                ),
            ]

            try:
                response = self.vlm.request(request_context)
            except Exception as e:
                self._log(f"    step {step_idx + 1}: VLM error: {e}")
                outcome = "stalled"
                break

            try:
                action = parse_action(response.action)
            except ValueError:
                action = {"_metadata": "finish", "message": "parse_error"}

            trajectory.add_step(current_page, action, response.thinking)
            self._total_steps += 1
            steps_taken += 1

            if action.get("_metadata") == "finish":
                break

            page_type_str = current_page.page_type.value
            if not is_safe_exploration_action(page_type_str, action, response.thinking):
                self._log(f"    step {step_idx + 1}: unsafe action blocked")
                outcome = "blocked"
                break

            try:
                result = self.action_handler.execute(action, screenshot.width, screenshot.height)
                if not result.success:
                    self._log(f"    step {step_idx + 1}: action failed")
                    outcome = "stalled"
                    break
            except Exception as e:
                self._log(f"    step {step_idx + 1}: execute error: {e}")
                outcome = "stalled"
                break

            time.sleep(self.policy.settle_delay)

        if outcome not in ("blocked", "stalled") or steps_taken > 0:
            next_screenshot = self.device.get_screenshot(self.device_id)
            next_page = self._classify(next_screenshot)
            self._record_page(next_page)

            self._extract_and_cluster(next_page)

            if steps_taken > 0:
                last_action = trajectory.steps[-1].action if trajectory.steps else {}
                recorded = self._record_transition(current_page, last_action or {}, next_page)
                if recorded:
                    new_transitions += 1

            if next_page.page_type.value not in {p.page_type.value for p in [start_page]}:
                if next_page.page_type.value not in {p.page_type.value for p in self._discovered_pages.values()
                                                      if p.state_key() != next_page.state_key()}:
                    new_pages.append(next_page.page_type.value)

            current_page = next_page

        job_result = JobExecutionResult(
            job=job,
            steps_taken=steps_taken,
            new_page_types_discovered=tuple(new_pages),
            new_transitions_discovered=new_transitions,
            outcome=outcome,
            deviation_reason=deviation_reason,
        )
        return job_result, current_page

    # ── Functionality Extraction ─────────────────────────────

    def _extract_and_cluster(self, page_info: PageInfo) -> int:
        """Extract functionalities from page and re-cluster. Returns new cluster count."""
        page_dict = _page_info_to_dict(page_info)
        new_items = self.functionality_extractor.from_page(page_dict)
        if self.strong_vlm_extractor is not None:
            new_items.extend(self.strong_vlm_extractor.from_page(page_dict))

        existing_ids = {item.functionality_id for item in self._all_items}
        added = [item for item in new_items if item.functionality_id not in existing_ids]
        if not added:
            return 0

        self._all_items.extend(added)
        old_count = len(self._all_clusters)
        self._all_items, self._all_clusters = self.clusterer.cluster(self._all_items)
        return max(0, len(self._all_clusters) - old_count)

    # ── Transition Recording ─────────────────────────────────

    def _record_transition(
        self,
        source: PageInfo,
        action: dict[str, Any],
        target: PageInfo,
    ) -> bool:
        source_type = source.page_type.value
        target_type = target.page_type.value
        rejection = _generic_transition_rejection(source_type, action, target_type)
        if rejection:
            self._rejected_transitions.append({
                "from": source.state_key(),
                "action": action,
                "to": target.state_key(),
                "reason": rejection,
            })
            return False

        transition = {
            "from": source.state_key(),
            "action": action,
            "to": target.state_key(),
        }
        self._transitions.append(transition)

        action_type = str(action.get("action") or "").lower()
        bbox = action.get("element") or action.get("coordinate")
        region = ""
        if isinstance(bbox, list) and len(bbox) >= 2:
            try:
                y = float(bbox[1]) if not isinstance(bbox[0], list) else float(bbox[0][1])
                region = "top" if y < 250 else "bottom" if y > 750 else "middle"
            except (TypeError, ValueError, IndexError):
                pass

        role = canonical_role_from_transition(
            source_type, target_type, action_type, region, action,
        )

        self.edge_lifecycle.record_outcome(
            source_page_type=source_type,
            intent=role or f"{action_type}_to_{target_type}",
            action_target=json.dumps(bbox or [], ensure_ascii=False)[:80],
            observed_target=target_type,
            risk="normal",
        )

        transition_dict = {
            "from": source.state_key(),
            "action": action,
            "to": target.state_key(),
        }
        transition_item = self.functionality_extractor.from_transition(
            transition_dict, app=self.app_name,
        )
        existing_ids = {item.functionality_id for item in self._all_items}
        if transition_item.functionality_id not in existing_ids:
            self._all_items.append(transition_item)

        return True

    # ── Job Synthesis ────────────────────────────────────────

    def _synthesize_fallback_job(self, current_page: PageInfo) -> ExplorationJob:
        from phone_agent.spatial.core import stable_id
        page_type = current_page.page_type.value
        discovered = sorted({p.page_type.value for p in self._discovered_pages.values()})
        missing_hint = ""
        core_types = {"home", "search_input", "search_result", "product_detail", "spec_selection", "cart"}
        missing = core_types - set(discovered)
        if missing:
            missing_hint = f" 尚未发现的页面类型：{', '.join(missing)}。"

        return ExplorationJob(
            job_id=stable_id("fallback", self.app_name, str(self._convergence.total_rounds)),
            functionality_cluster_id="",
            target_description=(
                f"在{self.app_name}的{page_type}页面探索新功能。{missing_hint}"
                f"优先导航到未发现的页面类型。避免重复已访问的页面。"
            ),
            reason="no unverified clusters remaining",
            max_steps=self.policy.max_job_steps,
            priority=0.1,
        )

    # ── Page Management ──────────────────────────────────────

    def _classify(self, screenshot: Any) -> PageInfo:
        page_type, summary, elements = self.classifier.classify(
            screenshot.base64_data, screenshot.width, screenshot.height,
        )
        return PageInfo(
            page_type=page_type,
            semantic_summary=summary,
            elements=elements,
            screenshot_hash=hashlib.md5(screenshot.base64_data.encode()).hexdigest(),
            app=self.app_name,
            screenshot_base64=screenshot.base64_data if self.policy.use_strong_vlm_extractor else "",
            width=screenshot.width,
            height=screenshot.height,
        )

    def _record_page(self, page_info: PageInfo) -> None:
        key = page_info.state_key()
        if key not in self._discovered_pages:
            self._discovered_pages[key] = page_info
            self._log(f"    new page: {key}")

    # ── Rollback ─────────────────────────────────────────────

    def _rollback(self, width: int, height: int) -> None:
        try:
            self.action_handler.execute(
                {"_metadata": "do", "action": "Back"}, width, height,
            )
            time.sleep(1)
            self._log("    rollback: backed out from risky page")
        except Exception as exc:
            self._log(f"    rollback failed: {exc}")

    # ── Persistence ──────────────────────────────────────────

    def _save_results(self, trajectory: Trajectory) -> None:
        timestamp = int(time.time())

        pages_data = {
            "app": self.app_name,
            "task": "autonomous_exploration",
            "explored_at": datetime.now().isoformat(),
            "total_pages": len(self._discovered_pages),
            "pages": [
                {
                    "page_type": p.page_type.value,
                    "summary": p.semantic_summary,
                    "elements": p.elements,
                    "screenshot_hash": p.screenshot_hash,
                    "app": p.app,
                }
                for p in self._discovered_pages.values()
            ],
        }
        pages_path = self.storage_dir / f"{self.app_name}_autonomous_pages_{timestamp}.json"
        _write_json(pages_path, pages_data)
        self._log(f"  saved: {pages_path.name}")

        traj_data = {
            "task": trajectory.task,
            "app": trajectory.app,
            "success": trajectory.success,
            "total_steps": len(trajectory.steps),
            "steps": [
                {
                    "step": idx + 1,
                    "page_type": step.page_info.page_type.value,
                    "page_summary": step.page_info.semantic_summary[:100],
                    "action": step.action,
                    "thinking": (step.action_thinking or "")[:200],
                    "timestamp": step.timestamp,
                }
                for idx, step in enumerate(trajectory.steps)
            ],
        }
        traj_path = self.storage_dir / f"{self.app_name}_autonomous_trajectory_{timestamp}.json"
        _write_json(traj_path, traj_data)

        transitions_data = {
            "app": self.app_name,
            "total_transitions": len(self._transitions),
            "transitions": self._transitions,
            "rejected_transitions": self._rejected_transitions,
        }
        trans_path = self.storage_dir / f"{self.app_name}_autonomous_transitions_{timestamp}.json"
        _write_json(trans_path, transitions_data)

        report = self._build_coverage_report()
        report_data = {
            "app": self.app_name,
            "explored_at": datetime.now().isoformat(),
            "policy": self.policy.to_dict(),
            "convergence": self._convergence.summary(),
            "coverage_metrics": report,
            "job_results": [r.to_dict() for r in self._job_results],
            "clusters": [c.to_dict() for c in self._all_clusters],
            "edge_lifecycle_summary": {
                "total_records": len(self.edge_lifecycle._records),
                "promotable": len(self.edge_lifecycle.get_promotable_edges()),
            },
        }
        report_path = self.storage_dir / f"{self.app_name}_autonomous_report_{timestamp}.json"
        _write_json(report_path, report_data)
        self._log(f"  saved: {report_path.name}")

    def _build_coverage_report(self) -> dict[str, Any]:
        screen_count = len(self._discovered_pages)
        unique_hashes = {p.screenshot_hash for p in self._discovered_pages.values()}
        metrics = compute_functionality_coverage(
            items=self._all_items,
            clusters=self._all_clusters,
            screenshot_count=screen_count,
            screen_cluster_count=len(unique_hashes),
            strong_vlm_configured=self.strong_vlm_extractor is not None,
        )
        gate = FunctionalityQualityGate()
        quality_result = gate.evaluate(self._all_clusters, metrics.to_dict())
        return {
            **metrics.to_dict(),
            "quality_gate": quality_result.to_dict(),
        }

    # ── Logging ──────────────────────────────────────────────

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg)


def _write_json(path: Path, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
