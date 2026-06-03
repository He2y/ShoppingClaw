"""Model-driven autonomous exploration for mobile apps.

Unlike OfflineExplorer which executes a single human-written task description,
AutonomousExplorer self-discovers what to explore by extracting functionalities
from each screen, clustering them, and generating exploration jobs for unverified
clusters.  The exploration loop is:

    screenshot -> classify -> extract functionalities -> cluster ->
    build jobs -> pick highest-priority job -> execute (1-3 steps) ->
    record transitions -> check convergence -> repeat

No preset task description, coverage target, or page-type enum is required.
Convergence is detected empirically: N consecutive rounds with no new
functionality clusters discovered.
"""

from __future__ import annotations

import hashlib
import json
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
    _build_exploration_system_prompt,
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


# ── Prompt Building ──────────────────────────────────────────

def _build_autonomous_system_prompt() -> str:
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

        "=== 页面类型特殊指引 ===\n"
        "- 搜索输入页（search_input）：用Type输入一个常见关键词（如\"耳机\"\"手机壳\"\"连衣裙\"），"
        "然后点击搜索按钮或键盘搜索键提交。不要在搜索页停留，输入后立刻提交。\n"
        "- 商品详情页（product_detail）：观察页面元素，可尝试点击规格选择或加购按钮探索跳转。\n"
        "- 弹窗/权限页（dialog/permission）：用Back关闭或点击关闭按钮。\n\n"

        "=== 重要约束 ===\n"
        "- 不需要登录，遇到登录界面请Back\n"
        "- 不要下单购买任何商品（可以进入观察，但不要提交订单）\n"
        "- 不要修改任何个人信息\n"
        "- 不要进行支付、确认地址、结算等不可逆操作\n"
        "- 遇到广告弹窗点X关闭或用Back跳过\n"
        "- 每操作完一步等待页面稳定后再截图\n"
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

    Composes existing AMSG pipeline components:
        FunctionalityExtractor -> Clusterer -> QueueBuilder ->
        TaskSynthesizer -> EdgeLifecycleManager
    into a self-driving exploration loop that requires no preset
    task description or coverage target.
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
        system_message = MessageBuilder.create_system_message(_build_autonomous_system_prompt())
        current_page: PageInfo | None = None
        prev_hash = ""

        while (
            not self._convergence.converged
            and self._total_steps < self.policy.max_total_steps
        ):
            screenshot = self.device.get_screenshot(self.device_id)
            cur_hash = screenshot.base64_data[:_SCREEN_CHANGE_HASH_LEN]

            if current_page is None or cur_hash != prev_hash:
                current_page = self._classify(screenshot)
            prev_hash = cur_hash

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
                           f"fallback job: {job.target_description[:60]}")

            result = self._execute_job(
                job, current_page, trajectory, system_message,
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

            if result.outcome == "blocked":
                self._rollback(screenshot.width, screenshot.height)
                current_page = None

            self._log(f"    result: {result.outcome}, "
                       f"+{result.new_transitions_discovered} transitions, "
                       f"dry={self._convergence.dry_rounds}")

        return trajectory

    # ── Inner Loop (Step-Driven per Job) ─────────────────────

    def _execute_job(
        self,
        job: ExplorationJob,
        start_page: PageInfo,
        trajectory: Trajectory,
        system_message: dict[str, Any],
    ) -> JobExecutionResult:
        current_page = start_page
        steps_taken = 0
        new_pages: list[str] = []
        new_transitions = 0
        new_clusters_in_job = 0
        outcome = "success"
        deviation_reason = ""
        prev_hash = ""

        for step_idx in range(job.max_steps):
            if self._total_steps >= self.policy.max_total_steps:
                break

            task_text = self._build_job_task_text(job, current_page, step_idx)
            screenshot = self.device.get_screenshot(self.device_id)

            request_context = [
                system_message,
                MessageBuilder.create_user_message(
                    text=task_text,
                    image_base64=screenshot.base64_data,
                ),
            ]

            try:
                response = self.vlm.request(request_context)
            except Exception as e:
                self._log(f"    VLM error: {e}")
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
                self._log(f"    step {step_idx + 1}: unsafe action blocked on {page_type_str}")
                outcome = "blocked"
                break

            try:
                result = self.action_handler.execute(action, screenshot.width, screenshot.height)
                if not result.success:
                    self._log(f"    step {step_idx + 1}: action failed: {result.message}")
                    outcome = "stalled"
                    break
            except Exception as e:
                self._log(f"    step {step_idx + 1}: execute error: {e}")
                outcome = "stalled"
                break

            time.sleep(self.policy.settle_delay)

            next_screenshot = self.device.get_screenshot(self.device_id)
            next_hash = next_screenshot.base64_data[:_SCREEN_CHANGE_HASH_LEN]
            if next_hash == prev_hash:
                self._log(f"    step {step_idx + 1}: screen unchanged, stalling")
                outcome = "stalled"
                break
            prev_hash = next_hash

            next_page = self._classify(next_screenshot)
            self._record_page(next_page)

            clusters_before = len(self._all_clusters)
            self._extract_and_cluster(next_page)
            new_clusters_in_job += len(self._all_clusters) - clusters_before

            recorded = self._record_transition(current_page, action, next_page)
            if recorded:
                new_transitions += 1

            if next_page.state_key() not in {p.state_key() for p in [start_page, current_page]}:
                if next_page.page_type.value not in {p.page_type.value for p in self._discovered_pages.values()}:
                    new_pages.append(next_page.page_type.value)

            if (
                step_idx > 0
                and job.expected_postcondition
                and next_page.page_type.value != job.expected_postcondition
            ):
                deviation_reason = (
                    f"expected {job.expected_postcondition}, "
                    f"got {next_page.page_type.value}"
                )
                outcome = "deviation"

            current_page = next_page

        return JobExecutionResult(
            job=job,
            steps_taken=steps_taken,
            new_page_types_discovered=tuple(new_pages),
            new_transitions_discovered=new_transitions,
            new_functionality_clusters=new_clusters_in_job,
            outcome=outcome,
            deviation_reason=deviation_reason,
        )

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

    # ── Job & Prompt Construction ────────────────────────────

    def _synthesize_fallback_job(self, current_page: PageInfo) -> ExplorationJob:
        """Generate an open-ended exploration job when the queue is empty."""
        from phone_agent.spatial.core import stable_id
        return ExplorationJob(
            job_id=stable_id("fallback", self.app_name, str(self._convergence.total_rounds)),
            functionality_cluster_id="",
            target_description=(
                f"Explore undiscovered functions on the current {current_page.page_type.value} "
                f"page in {self.app_name}. Try tapping unexplored buttons or navigating to "
                f"new sections. Avoid repeating previously visited pages."
            ),
            reason="no unverified clusters remaining",
            max_steps=self.policy.max_job_steps,
            priority=0.1,
        )

    def _build_job_task_text(
        self,
        job: ExplorationJob,
        current_page: PageInfo,
        step_idx: int,
    ) -> str:
        discovered_types = sorted({p.page_type.value for p in self._discovered_pages.values()})
        discovered_summary = (
            f"已发现 {len(self._discovered_pages)} 个页面 "
            f"({', '.join(discovered_types)}), "
            f"{len(self._transitions)} 条有效跳转, "
            f"{len(self._all_clusters)} 个功能簇"
        )

        frontier_hint = ""
        if self.policy.active_exploration:
            frontier_hint = self._build_frontier_hint(current_page)

        task_text = (
            f"【当前探索目标】{job.target_description}\n"
            f"当前页面: {current_page.page_type.value} — {current_page.semantic_summary[:60]}\n"
            f"探索进度: {discovered_summary}\n"
        )
        if job.forbidden_actions:
            task_text += f"禁止操作: {', '.join(job.forbidden_actions)}\n"

        page_type_str = current_page.page_type.value
        if page_type_str == "search_input":
            task_text += (
                "【搜索页操作指引】你现在在搜索输入页。请执行以下操作：\n"
                '1. 用 do(action="Type", text="耳机") 输入一个常见商品关键词\n'
                "2. 然后点击搜索按钮或键盘上的搜索键提交搜索\n"
                "不要在搜索页停留，输入后立即提交。\n"
            )
        elif page_type_str in {"dialog", "permission"}:
            task_text += "【弹窗处理】请用Back关闭弹窗或点击关闭/取消按钮。\n"

        if step_idx == 0:
            task_text += "请根据探索目标选择最有信息增益的安全操作。\n"
        else:
            task_text += "继续执行当前探索目标，或在目标已达成时 finish。\n"
        if frontier_hint:
            task_text += f"\n{frontier_hint}"
        return task_text

    def _build_frontier_hint(self, page_info: PageInfo) -> str:
        node = self.semantics_extractor.node_from_exploration_page(
            _page_info_to_dict(page_info),
            fallback_app=self.app_name,
        )
        uncovered_types = {
            cluster.canonical_name
            for cluster in self._all_clusters
            if not cluster.is_verified
        }
        hypotheses = self.edge_hypothesis_gen.generate(node, goal_page_types=uncovered_types)
        ranked = self.active_builder.rank(hypotheses)[:3]
        if not ranked:
            return ""
        lines = [
            "[AMSG Active Exploration]",
            "Prefer validating one safe edge hypothesis that improves graph coverage:",
        ]
        for item in ranked:
            hyp = item.hypothesis
            lines.append(
                f"- {hyp.source_page_type} --{hyp.intent}/{hyp.semantic_target}--> "
                f"{hyp.expected_page_type}; score={item.score:.2f}; risk={hyp.risk}"
            )
        lines.append("After action, stop before payment, order submission, or address confirmation.")
        return "\n".join(lines)

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
