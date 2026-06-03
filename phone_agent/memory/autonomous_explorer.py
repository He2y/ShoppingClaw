"""Model-driven autonomous exploration for mobile apps.

Supervisor-Executor VLM architecture:

  Supervisor (glm-5v-turbo) — the brain:
    Sees screenshot + full exploration progress.
    Classifies page, reasons about what's missing, outputs a concrete
    multi-step action plan.  One call per round.

  Executor (autoglm-phone) — the hands:
    Sees screenshot + one atomic instruction ("点击搜索框").
    Outputs a single do(action=...) coordinate action.  One call per step.

Per-round: 1 supervisor + 1-3 executor = 2-4 VLM calls.
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
from phone_agent.spatial.coverage_metrics import compute_functionality_coverage
from phone_agent.spatial.edge_lifecycle import EdgeLifecycleManager
from phone_agent.spatial.exploration_queue import ExplorationJob, ExplorationQueueBuilder
from phone_agent.spatial.functionality import (
    FunctionalityExtractor,
    FunctionalityItem,
    canonical_role_from_transition,
)
from phone_agent.spatial.functionality_cluster import FunctionalityClusterer
from phone_agent.spatial.quality_gate import FunctionalityQualityGate
from phone_agent.spatial.task_synthesis import resolve_strong_vlm_config


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

_EXECUTOR_SYSTEM_PROMPT = (
    "你是手机操作执行器。根据指令在屏幕上执行一个操作。\n"
    "只输出一个操作，格式：\n"
    "thinking{一句话理由}\n"
    "<answer>do(action=\"Tap\", element=[x,y])</answer>\n\n"
    "可用操作：\n"
    'do(action="Tap", element=[x,y]) 坐标0-999\n'
    'do(action="Type", text="xxx")\n'
    'do(action="Swipe", start=[x1,y1], end=[x2,y2])\n'
    'do(action="Back")\n'
    "禁止输出finish。禁止输出多个操作。禁止描述页面。\n"
)

_CORE_PAGE_TYPES = ("home", "search_input", "search_result", "product_detail", "spec_selection", "cart")


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
    job_description: str
    steps_taken: int
    new_page_types_discovered: tuple[str, ...] = ()
    new_transitions_discovered: int = 0
    new_functionality_clusters: int = 0
    outcome: str = "success"
    deviation_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_description": self.job_description,
            "steps_taken": self.steps_taken,
            "new_page_types_discovered": list(self.new_page_types_discovered),
            "new_transitions_discovered": self.new_transitions_discovered,
            "new_functionality_clusters": self.new_functionality_clusters,
            "outcome": self.outcome,
            "deviation_reason": self.deviation_reason,
        }


@dataclass(frozen=True)
class SupervisorDecision:
    page_type: str
    page_summary: str
    reasoning: str
    plan: tuple[str, ...]
    should_stop: bool = False
    stop_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_type": self.page_type,
            "page_summary": self.page_summary,
            "reasoning": self.reasoning,
            "plan": list(self.plan),
            "should_stop": self.should_stop,
            "stop_reason": self.stop_reason,
        }


# ── Convergence Tracker ──────────────────────────────────────

class ConvergenceTracker:
    """Empirical convergence: stop after N consecutive dry rounds."""

    def __init__(self, dry_round_limit: int = 5, window: int = 3) -> None:
        self._dry_round_limit = dry_round_limit
        self._window = window
        self._history: list[dict[str, int]] = []
        self._consecutive_dry: int = 0

    def record_round(self, new_clusters: int, new_page_types: int, new_transitions: int) -> None:
        self._history.append({"new_clusters": new_clusters, "new_page_types": new_page_types, "new_transitions": new_transitions})
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

def is_safe_exploration_action(page_type: str, action: dict[str, Any], reasoning: str = "") -> bool:
    if action.get("_metadata") == "finish":
        return True
    if page_type in _HIGH_RISK_PAGE_TYPES_STR:
        return False
    action_text = json.dumps(action, ensure_ascii=False).lower()
    if any(token.lower() in action_text for token in _UNSAFE_ACTION_TOKENS):
        return False
    return True


def _generic_transition_rejection(source_type: str, action: dict[str, Any], target_type: str) -> str:
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


# ── Exploration Supervisor ───────────────────────────────────

class ExplorationSupervisor:
    """glm-5v-turbo as the exploration brain.

    Sees the full exploration state + current screenshot.
    Outputs: page classification + reasoning + concrete action plan.
    One VLM call per round replaces both PageClassifier and Planner.
    """

    _SYSTEM_PROMPT = (
        "你是移动应用空间图谱的探索规划师。你的任务是系统性地发现App中所有核心页面类型和页面间的跳转关系。\n\n"
        "核心页面类型（必须全部发现）：\n"
        "- home: 首页/推荐流\n"
        "- search_input: 搜索输入页\n"
        "- search_result: 搜索结果/商品列表\n"
        "- product_detail: 商品详情页\n"
        "- spec_selection: 规格选择弹窗\n"
        "- cart: 购物车\n\n"
        "输出严格JSON格式（不要输出其他内容）：\n"
        "{\n"
        '  "page_type": "当前页面类型（上述之一或other）",\n'
        '  "page_summary": "一句话页面描述",\n'
        '  "reasoning": "分析探索进度，说明为什么选择这个计划",\n'
        '  "plan": ["第一步操作指令", "第二步操作指令"],\n'
        '  "should_stop": false\n'
        "}\n\n"
        "规划规则：\n"
        "1. plan中每条指令必须描述屏幕上可见的具体元素（如'点击屏幕顶部搜索框'而非'搜索'）\n"
        "2. 优先补齐缺失的页面类型，按 home→search_input→search_result→product_detail 顺序推进\n"
        "3. 在非核心页面（如会员中心、设置页、活动页）时，plan第一步必须是'点击返回按钮'回到主流程\n"
        "4. 在search_input页时，plan必须包含输入关键词和点击搜索两步，关键词用'耳机'\n"
        "5. 不要生成支付、结算、登录、地址相关操作\n"
        "6. 所有核心页面类型都已发现且跳转关系完整时，设should_stop=true\n"
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
                    self._client = OpenAI(base_url=base_url, api_key=key, timeout=20.0)
                    self._model = model
                    self._configured = True
                except Exception:
                    pass
                break

    @property
    def configured(self) -> bool:
        return self._configured

    def analyze_and_plan(
        self,
        screenshot_base64: str,
        discovered_pages: list[str],
        recorded_transitions: list[str],
        current_round: int,
        total_steps: int,
        max_steps: int,
        last_result: str,
    ) -> SupervisorDecision:
        discovered_set = {p.split(":")[0] for p in discovered_pages}
        missing = [t for t in _CORE_PAGE_TYPES if t not in discovered_set]

        user_text = (
            f"=== 探索进度 ===\n"
            f"第{current_round}轮 | 已执行{total_steps}/{max_steps}步\n"
            f"已发现页面({len(discovered_pages)})：{'; '.join(discovered_pages) or '无'}\n"
            f"已记录跳转({len(recorded_transitions)})：{'; '.join(recorded_transitions[-8:]) or '无'}\n"
            f"缺失核心页面：{', '.join(missing) or '全部已发现！'}\n"
            f"上轮结果：{last_result}\n\n"
            f"请分析当前截屏，输出JSON规划。"
        )

        if not self._configured or not self._client:
            return self._fallback(discovered_pages, missing)

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                temperature=0,
                max_tokens=600,
                messages=[
                    {"role": "system", "content": self._SYSTEM_PROMPT},
                    {"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_base64}"}},
                        {"type": "text", "text": user_text},
                    ]},
                ],
            )
            content = response.choices[0].message.content or ""
            return self._parse_decision(content, missing)
        except Exception:
            return self._fallback(discovered_pages, missing)

    def _parse_decision(self, content: str, missing: list[str]) -> SupervisorDecision:
        content = content.strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?", "", content).strip()
            content = re.sub(r"```$", "", content).strip()
        match = re.search(r"\{.*\}", content, flags=re.DOTALL)
        if not match:
            return self._fallback([], missing)
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return self._fallback([], missing)

        plan_raw = data.get("plan") or []
        plan = tuple(str(s) for s in plan_raw if isinstance(s, str) and s.strip())[:3]
        if not plan:
            plan = ("点击返回按钮",)

        return SupervisorDecision(
            page_type=str(data.get("page_type") or "unknown"),
            page_summary=str(data.get("page_summary") or ""),
            reasoning=str(data.get("reasoning") or ""),
            plan=plan,
            should_stop=bool(data.get("should_stop")),
            stop_reason=str(data.get("stop_reason") or ""),
        )

    @staticmethod
    def _fallback(discovered_pages: list[str], missing: list[str]) -> SupervisorDecision:
        if "search_input" in missing and "home" not in missing:
            return SupervisorDecision("home", "首页", "需要进入搜索", ("点击屏幕顶部的搜索框",))
        if "search_result" in missing:
            return SupervisorDecision("search_input", "搜索页", "需要执行搜索",
                                      ("在搜索框中输入'耳机'", "点击橙色搜索按钮"))
        if "product_detail" in missing:
            return SupervisorDecision("search_result", "搜索结果", "需要进入商品详情",
                                      ("点击第一个商品卡片的图片",))
        return SupervisorDecision("unknown", "", "fallback", ("点击返回按钮",))


# ── Helpers ──────────────────────────────────────────────────

def _page_info_to_dict(page_info: PageInfo) -> dict[str, Any]:
    return {
        "page_type": page_info.page_type.value,
        "app": page_info.app,
        "elements": page_info.elements,
        "summary": page_info.semantic_summary,
        "screenshot_hash": page_info.screenshot_hash,
        "screenshot_base64": page_info.screenshot_base64,
    }


def _build_autonomous_system_prompt() -> str:
    """Legacy fallback prompt."""
    return _EXECUTOR_SYSTEM_PROMPT


# ── AutonomousExplorer ───────────────────────────────────────

class AutonomousExplorer:
    """Supervisor-Executor exploration: strong VLM plans, action VLM executes."""

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
        self.supervisor = ExplorationSupervisor()

        self.functionality_extractor = FunctionalityExtractor()
        self.clusterer = FunctionalityClusterer()
        self.queue_builder = ExplorationQueueBuilder()
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
        self._log(f"\n{'=' * 60}")
        self._log(f"  Autonomous Explorer: {self.app_name}")
        self._log(f"  Supervisor: {'glm-5v-turbo' if self.supervisor.configured else 'fallback'}")
        self._log(f"  Policy: max_steps={self.policy.max_total_steps}, max_dry={self.policy.max_dry_rounds}")
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

    # ── Main Loop (Supervisor-Driven) ────────────────────────

    def _autonomous_loop(self) -> Trajectory:
        trajectory = Trajectory(task="autonomous_exploration", app=self.app_name)
        last_result = "首次启动"
        current_page: PageInfo | None = None

        while (
            not self._convergence.converged
            and self._total_steps < self.policy.max_total_steps
        ):
            screenshot = self.device.get_screenshot(self.device_id)

            discovered_keys = list(self._discovered_pages.keys())
            transition_strs = [f"{t['from'].split(':')[0]}→{t['to'].split(':')[0]}" for t in self._transitions]

            decision = self.supervisor.analyze_and_plan(
                screenshot_base64=screenshot.base64_data,
                discovered_pages=discovered_keys,
                recorded_transitions=transition_strs,
                current_round=self._convergence.total_rounds + 1,
                total_steps=self._total_steps,
                max_steps=self.policy.max_total_steps,
                last_result=last_result,
            )

            page_type_enum = _str_to_page_type(decision.page_type)
            if current_page is None or current_page.page_type != page_type_enum:
                current_page = PageInfo(
                    page_type=page_type_enum,
                    semantic_summary=decision.page_summary,
                    elements={},
                    screenshot_hash=hashlib.md5(screenshot.base64_data.encode()).hexdigest(),
                    app=self.app_name,
                    width=screenshot.width,
                    height=screenshot.height,
                )
            self._record_page(current_page)

            self._log(f"  [round {self._convergence.total_rounds + 1}] "
                       f"page={decision.page_type} | "
                       f"plan={[s[:20] for s in decision.plan]}")
            self._log(f"    reasoning: {decision.reasoning[:80]}")

            if decision.should_stop:
                self._log(f"    supervisor says stop: {decision.stop_reason}")
                break

            clusters_before = len(self._all_clusters)
            pages_before = len(self._discovered_pages)
            self._extract_and_cluster(current_page)

            result, current_page = self._execute_plan(
                decision, current_page, trajectory,
            )
            self._job_results.append(result)

            new_clusters = len(self._all_clusters) - clusters_before
            new_pages = len(self._discovered_pages) - pages_before
            self._convergence.record_round(
                new_clusters=new_clusters + result.new_functionality_clusters,
                new_page_types=new_pages + len(result.new_page_types_discovered),
                new_transitions=result.new_transitions_discovered,
            )
            self.edge_lifecycle.advance_step()

            last_result = (
                f"{result.outcome}: "
                f"+{len(result.new_page_types_discovered)}页面 "
                f"+{result.new_transitions_discovered}跳转"
            )
            if result.outcome == "blocked":
                self._rollback(screenshot.width, screenshot.height)
                current_page = None
                last_result += " (已回退)"

            self._log(f"    result: {last_result}, dry={self._convergence.dry_rounds}")

        return trajectory

    # ── Execute Plan (Executor-Driven) ───────────────────────

    def _execute_plan(
        self,
        decision: SupervisorDecision,
        start_page: PageInfo,
        trajectory: Trajectory,
    ) -> tuple[JobExecutionResult, PageInfo | None]:
        current_page = start_page
        steps_taken = 0
        new_pages: list[str] = []
        new_transitions = 0
        outcome = "success"

        system_msg = MessageBuilder.create_system_message(_EXECUTOR_SYSTEM_PROMPT)

        for step_idx, instruction in enumerate(decision.plan):
            if self._total_steps >= self.policy.max_total_steps:
                break

            screenshot = self.device.get_screenshot(self.device_id)
            user_text = f"执行操作：{instruction}"

            try:
                response = self.vlm.request([
                    system_msg,
                    MessageBuilder.create_user_message(text=user_text, image_base64=screenshot.base64_data),
                ])
            except Exception as e:
                self._log(f"    step {step_idx + 1}: VLM error: {e}")
                outcome = "stalled"
                break

            try:
                action = parse_action(response.action)
            except ValueError:
                action = {"_metadata": "do", "action": "Back"}

            if action.get("_metadata") == "finish":
                action = {"_metadata": "do", "action": "Back"}
                self._log(f"    step {step_idx + 1}: intercepted finish → Back")

            trajectory.add_step(current_page, action, response.thinking)
            self._total_steps += 1
            steps_taken += 1

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

        if steps_taken > 0:
            time.sleep(1.0)
            next_screenshot = self.device.get_screenshot(self.device_id)
            next_page = self._classify_with_retry(next_screenshot)
            self._record_page(next_page)
            self._extract_and_cluster(next_page)

            last_action = trajectory.steps[-1].action if trajectory.steps else {}
            recorded = self._record_transition(current_page, last_action or {}, next_page)
            if recorded:
                new_transitions += 1

            existing_types = {p.page_type.value for p in self._discovered_pages.values()}
            if next_page.page_type.value not in existing_types:
                new_pages.append(next_page.page_type.value)

            current_page = next_page

        return (
            JobExecutionResult(
                job_description=decision.reasoning[:100],
                steps_taken=steps_taken,
                new_page_types_discovered=tuple(new_pages),
                new_transitions_discovered=new_transitions,
                outcome=outcome,
            ),
            current_page,
        )

    # ── Functionality Extraction ─────────────────────────────

    def _extract_and_cluster(self, page_info: PageInfo) -> int:
        page_dict = _page_info_to_dict(page_info)
        new_items = self.functionality_extractor.from_page(page_dict)

        existing_ids = {item.functionality_id for item in self._all_items}
        added = [item for item in new_items if item.functionality_id not in existing_ids]
        if not added:
            return 0

        self._all_items.extend(added)
        old_count = len(self._all_clusters)
        self._all_items, self._all_clusters = self.clusterer.cluster(self._all_items)
        return max(0, len(self._all_clusters) - old_count)

    # ── Transition Recording ─────────────────────────────────

    def _record_transition(self, source: PageInfo, action: dict[str, Any], target: PageInfo) -> bool:
        source_type = source.page_type.value
        target_type = target.page_type.value
        rejection = _generic_transition_rejection(source_type, action, target_type)
        if rejection:
            self._rejected_transitions.append({
                "from": source.state_key(), "action": action,
                "to": target.state_key(), "reason": rejection,
            })
            return False

        self._transitions.append({
            "from": source.state_key(), "action": action, "to": target.state_key(),
        })

        action_type = str(action.get("action") or "").lower()
        bbox = action.get("element") or action.get("coordinate")
        region = ""
        if isinstance(bbox, list) and len(bbox) >= 2:
            try:
                y = float(bbox[1]) if not isinstance(bbox[0], list) else float(bbox[0][1])
                region = "top" if y < 250 else "bottom" if y > 750 else "middle"
            except (TypeError, ValueError, IndexError):
                pass

        role = canonical_role_from_transition(source_type, target_type, action_type, region, action)
        self.edge_lifecycle.record_outcome(
            source_page_type=source_type,
            intent=role or f"{action_type}_to_{target_type}",
            action_target=json.dumps(bbox or [], ensure_ascii=False)[:80],
            observed_target=target_type,
            risk="normal",
        )

        transition_item = self.functionality_extractor.from_transition(
            {"from": source.state_key(), "action": action, "to": target.state_key()},
            app=self.app_name,
        )
        if transition_item.functionality_id not in {i.functionality_id for i in self._all_items}:
            self._all_items.append(transition_item)

        return True

    # ── Page Management ──────────────────────────────────────

    def _classify_with_retry(self, screenshot: Any) -> PageInfo:
        page_type, summary, elements = self.classifier.classify(
            screenshot.base64_data, screenshot.width, screenshot.height,
        )
        if page_type == ShoppingPageType.UNKNOWN and "error" in summary.lower():
            time.sleep(1.5)
            screenshot = self.device.get_screenshot(self.device_id)
            page_type, summary, elements = self.classifier.classify(
                screenshot.base64_data, screenshot.width, screenshot.height,
            )
        return PageInfo(
            page_type=page_type,
            semantic_summary=summary,
            elements=elements,
            screenshot_hash=hashlib.md5(screenshot.base64_data.encode()).hexdigest(),
            app=self.app_name,
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
            self.action_handler.execute({"_metadata": "do", "action": "Back"}, width, height)
            time.sleep(1)
            self._log("    rollback: backed out")
        except Exception as exc:
            self._log(f"    rollback failed: {exc}")

    # ── Persistence ──────────────────────────────────────────

    def _save_results(self, trajectory: Trajectory) -> None:
        timestamp = int(time.time())

        _write_json(self.storage_dir / f"{self.app_name}_autonomous_pages_{timestamp}.json", {
            "app": self.app_name,
            "task": "autonomous_exploration",
            "explored_at": datetime.now().isoformat(),
            "total_pages": len(self._discovered_pages),
            "pages": [
                {"page_type": p.page_type.value, "summary": p.semantic_summary,
                 "elements": p.elements, "screenshot_hash": p.screenshot_hash, "app": p.app}
                for p in self._discovered_pages.values()
            ],
        })

        _write_json(self.storage_dir / f"{self.app_name}_autonomous_trajectory_{timestamp}.json", {
            "task": trajectory.task, "app": trajectory.app,
            "success": trajectory.success, "total_steps": len(trajectory.steps),
            "steps": [
                {"step": i + 1, "page_type": s.page_info.page_type.value,
                 "page_summary": s.page_info.semantic_summary[:100],
                 "action": s.action, "thinking": (s.action_thinking or "")[:200],
                 "timestamp": s.timestamp}
                for i, s in enumerate(trajectory.steps)
            ],
        })

        _write_json(self.storage_dir / f"{self.app_name}_autonomous_transitions_{timestamp}.json", {
            "app": self.app_name,
            "total_transitions": len(self._transitions),
            "transitions": self._transitions,
            "rejected_transitions": self._rejected_transitions,
        })

        report = self._build_coverage_report()
        _write_json(self.storage_dir / f"{self.app_name}_autonomous_report_{timestamp}.json", {
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
        })
        self._log(f"  saved 4 files to {self.storage_dir}/")

    def _build_coverage_report(self) -> dict[str, Any]:
        screen_count = len(self._discovered_pages)
        unique_hashes = {p.screenshot_hash for p in self._discovered_pages.values()}
        metrics = compute_functionality_coverage(
            items=self._all_items, clusters=self._all_clusters,
            screenshot_count=screen_count, screen_cluster_count=len(unique_hashes),
        )
        gate = FunctionalityQualityGate()
        quality_result = gate.evaluate(self._all_clusters, metrics.to_dict())
        return {**metrics.to_dict(), "quality_gate": quality_result.to_dict()}

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg)


def _str_to_page_type(value: str) -> ShoppingPageType:
    mapping = {e.value: e for e in ShoppingPageType}
    return mapping.get(value, ShoppingPageType.UNKNOWN)


def _write_json(path: Path, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
