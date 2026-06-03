"""Model-driven autonomous graph exploration for mobile apps.

Supervisor-Executor architecture with graph-coverage-driven planning:

  PageClassifier (glm-5v-turbo, fast mode):
    Classifies pages AND detects interactive elements.
    Called at round start + after each action for element capture.

  Supervisor (glm-5v-turbo):
    Sees the full navigation graph (pages, elements, transitions,
    explored vs unexplored elements). Plans the next exploration
    action based on graph coverage gaps. No hardcoded page types.

  Executor (autoglm-phone):
    Executes one atomic instruction per call with minimal prompt.

  Post-exploration:
    Saves OfflineExplorer-compatible JSON, optionally imports into
    SpatialGraphMemory -> Neo4j via import_exploration_files().
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
from phone_agent.spatial.exploration_queue import ExplorationQueueBuilder
from phone_agent.spatial.functionality import (
    FunctionalityExtractor,
    FunctionalityItem,
    canonical_role_from_transition,
)
from phone_agent.spatial.functionality_cluster import FunctionalityClusterer
from phone_agent.spatial.quality_gate import FunctionalityQualityGate


# ── Constants ─────────────────────────────────────────────────

_HIGH_RISK_PAGE_TYPES_STR = frozenset({
    "payment", "address", "login", "permission",
})

# Tokens that block any action anywhere.
_UNSAFE_ACTION_TOKENS = (
    "立即支付", "确认支付", "确认付款", "指纹支付", "面容支付",
    "pay now", "confirm payment",
    "logout", "log out", "switch account",
    "退出登录", "切换账号", "注销账号",
)

# Extra tokens blocked on checkout/order pages — prevent order submission.
_CHECKOUT_BLOCKED_TOKENS = (
    "提交订单", "确认订单", "立即下单", "立即购买",
    "去支付", "去付款", "submit order", "place order",
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
    auto_import_graph: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


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


# ── Safety ───────────────────────────────────────────────────

def is_safe_exploration_action(
    page_type: str,
    action: dict[str, Any],
    reasoning: str = "",
    instruction: str = "",
) -> bool:
    if action.get("_metadata") == "finish":
        return True
    if page_type in _HIGH_RISK_PAGE_TYPES_STR:
        return False
    combined = (json.dumps(action, ensure_ascii=False) + " " + reasoning + " " + instruction).lower()
    if any(tok.lower() in combined for tok in _UNSAFE_ACTION_TOKENS):
        return False
    if page_type in ("checkout", "order_confirm", "spec_selection", "cart"):
        if any(tok.lower() in combined for tok in _CHECKOUT_BLOCKED_TOKENS):
            return False
    return True


def _generic_transition_rejection(source_type: str, action: dict[str, Any], target_type: str) -> str:
    if source_type in _HIGH_RISK_PAGE_TYPES_STR or target_type in _HIGH_RISK_PAGE_TYPES_STR:
        return "high-risk page boundary"
    action_type = str(action.get("action") or "").lower()
    if source_type == target_type:
        if source_type in {"search_input", "filter_panel"} and action_type in {"tap", "type", "type_name", "input"}:
            return ""
        return "self-loop"
    if action_type == "wait":
        return "non-navigation"
    return ""


# ── Exploration Supervisor ───────────────────────────────────

class ExplorationSupervisor:
    """Sees full navigation graph + screenshot, outputs coverage-driven plan."""

    _SYSTEM_PROMPT = (
        "你是移动应用导航图谱的探索规划师。目标：最大化发现App中不同页面类型和跳转路径。\n\n"
        "输出严格JSON（不要其他内容）：\n"
        "{\n"
        '  "page_type": "当前页面类型（home/search_input/search_result/product_detail/spec_selection/cart/filter_panel/category/store/my_account/order_list/coupon/dialog等）",\n'
        '  "page_summary": "一句话页面描述",\n'
        '  "visible_elements": ["搜索框", "商品卡片", "筛选按钮", "购物车图标"],\n'
        '  "reasoning": "分析图谱中标记为★未探索的元素，说明为什么选择这个动作",\n'
        '  "plan": ["具体操作指令1", "具体操作指令2"],\n'
        '  "should_stop": false,\n'
        '  "stop_reason": ""\n'
        "}\n\n"
        "核心规则：\n"
        "1. **绝不重复已走过的路径。** 图谱中标记了✓的跳转不要再走，专注★未探索的元素\n"
        "2. plan中每条指令必须描述截屏中可见的具体元素（如'点击底部购物车图标'）\n"
        "3. 优先探索底部导航栏Tab（购物车、我的淘宝、消息等）和顶部分类入口\n"
        "4. 在非核心页面（活动、直播、弹窗等）停留不超过1步，立即返回\n"
        "5. 在搜索输入页时使用具体关键词（如'耳机'）\n"
        "6. 允许进入结算/下单页面以覆盖购物流程。禁止：立即支付、确认支付、登录、修改地址\n"
        "7. 当图谱中所有页面的元素都已被标记为✓时，设should_stop=true\n"
    )

    def __init__(self) -> None:
        self._client: Any | None = None
        self._model: str = ""
        self._configured = False
        self._init_client()

    def _init_client(self) -> None:
        from dotenv import load_dotenv
        load_dotenv()
        for base_key, model_key, api_key in [
            ("AMSG_STRONG_VLM_BASE_URL", "AMSG_STRONG_VLM_MODEL", "AMSG_STRONG_VLM_API_KEY"),
            ("OFFLINE_VLM_BASE_URL", "OFFLINE_VLM_MODEL", "OFFLINE_VLM_API_KEY"),
        ]:
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

    def plan(
        self,
        screenshot_base64: str,
        graph_summary: str,
        current_round: int,
        total_steps: int,
        max_steps: int,
        last_result: str,
    ) -> tuple[SupervisorDecision, list[str]]:
        """Returns (decision, visible_elements)."""
        user_text = (
            f"=== 探索进度 ===\n"
            f"第{current_round}轮 | 已执行{total_steps}/{max_steps}步 | 上轮: {last_result}\n\n"
            f"=== 当前导航图谱 ===\n{graph_summary}\n\n"
            f"分析截屏中的当前页面，输出JSON规划。"
        )

        if not self._configured or not self._client:
            return SupervisorDecision("unknown", "", "VLM不可用", ("点击返回按钮",)), []

        try:
            response = self._client.chat.completions.create(
                model=self._model, temperature=0, max_tokens=1200,
                messages=[
                    {"role": "system", "content": self._SYSTEM_PROMPT},
                    {"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_base64}"}},
                        {"type": "text", "text": user_text},
                    ]},
                ],
            )
            return self._parse(response.choices[0].message.content or "")
        except Exception:
            return SupervisorDecision("unknown", "", "VLM调用失败", ("点击返回按钮",)), []

    def _parse(self, content: str) -> tuple[SupervisorDecision, list[str]]:
        """Returns (decision, visible_elements)."""
        content = content.strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?", "", content).strip()
            content = re.sub(r"```$", "", content).strip()
        match = re.search(r"\{.*\}", content, flags=re.DOTALL)
        if not match:
            match = re.search(r"\{.*", content, flags=re.DOTALL)
            if not match:
                return SupervisorDecision("unknown", "", content[:100], ("点击返回按钮",)), []
        raw_json = match.group(0)
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError:
            raw_json = _repair_truncated_json(raw_json)
            try:
                data = json.loads(raw_json)
            except json.JSONDecodeError:
                return SupervisorDecision("unknown", "", "JSON解析失败", ("点击返回按钮",)), []

        plan = tuple(str(s) for s in (data.get("plan") or []) if isinstance(s, str) and s.strip())[:3]
        elements = [str(e) for e in (data.get("visible_elements") or []) if isinstance(e, str)]
        return (
            SupervisorDecision(
                page_type=str(data.get("page_type") or "unknown"),
                page_summary=str(data.get("page_summary") or ""),
                reasoning=str(data.get("reasoning") or ""),
                plan=plan or ("点击返回按钮",),
                should_stop=bool(data.get("should_stop")),
                stop_reason=str(data.get("stop_reason") or ""),
            ),
            elements,
        )


# ── Helpers ──────────────────────────────────────────────────

def _page_info_to_dict(page_info: PageInfo) -> dict[str, Any]:
    return {
        "page_type": page_info.page_type.value,
        "app": page_info.app,
        "elements": page_info.elements,
        "summary": page_info.semantic_summary,
        "screenshot_hash": page_info.screenshot_hash,
    }


def _build_autonomous_system_prompt() -> str:
    return _EXECUTOR_SYSTEM_PROMPT


def _str_to_page_type(value: str) -> ShoppingPageType:
    mapping = {e.value: e for e in ShoppingPageType}
    return mapping.get(value, ShoppingPageType.UNKNOWN)


# ── AutonomousExplorer ───────────────────────────────────────

class AutonomousExplorer:
    """Graph-coverage-driven autonomous exploration."""

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
            api_key=classifier_api_key, base_url=classifier_base_url,
            model=classifier_model, mode=classifier_mode,
            timeout=classifier_timeout, max_image_width=classifier_max_image_width,
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
        self._explored_actions: dict[str, list[str]] = {}
        self._trajectories: list[Trajectory] = []
        self._job_results: list[JobExecutionResult] = []
        self._convergence = ConvergenceTracker(
            dry_round_limit=self.policy.max_dry_rounds,
            window=self.policy.convergence_window,
        )
        self._total_steps: int = 0

    # ── Entry ────────────────────────────────────────────────

    def explore(self) -> list[Trajectory]:
        self._log(f"\n{'=' * 60}")
        self._log(f"  Autonomous Explorer: {self.app_name}")
        self._log(f"  Supervisor: {'active' if self.supervisor.configured else 'fallback'}")
        self._log(f"  Policy: steps={self.policy.max_total_steps}, dry={self.policy.max_dry_rounds}")
        self._log(f"{'=' * 60}\n")

        package = get_package_name(self.app_name)
        if not package:
            self._log(f"  ERROR: Unknown app '{self.app_name}'")
            return []

        self.device.launch_app(self.app_name, self.device_id)
        time.sleep(4)

        trajectory = self._main_loop()
        self._trajectories = [trajectory]
        self._save_results(trajectory)
        self._maybe_import_graph()

        self._log(f"\n{'=' * 60}")
        self._log(f"  Done: {len(self._discovered_pages)} pages, "
                   f"{len(self._transitions)} transitions, "
                   f"{len(self._all_clusters)} clusters")
        self._log(f"  {self._total_steps} steps, "
                   f"{self._convergence.total_rounds} rounds")
        self._log(f"{'=' * 60}\n")
        return self._trajectories

    # ── Main Loop ────────────────────────────────────────────

    def _main_loop(self) -> Trajectory:
        trajectory = Trajectory(task="autonomous_exploration", app=self.app_name)
        current_page: PageInfo | None = None
        last_result = "首次启动"

        while (
            not self._convergence.converged
            and self._total_steps < self.policy.max_total_steps
        ):
            # Build graph summary for supervisor (using current_page if available)
            dummy_page = current_page or PageInfo(
                ShoppingPageType.UNKNOWN, "", {}, "", self.app_name)
            graph_summary = self._build_graph_summary(dummy_page)

            # Supervisor: screenshot → classification + elements + plan (one call)
            screenshot = self.device.get_screenshot(self.device_id)
            decision, visible_elements = self.supervisor.plan(
                screenshot_base64=screenshot.base64_data,
                graph_summary=graph_summary,
                current_round=self._convergence.total_rounds + 1,
                total_steps=self._total_steps,
                max_steps=self.policy.max_total_steps,
                last_result=last_result,
            )

            # Build PageInfo from supervisor classification + elements
            elements_dict = {e: e for e in visible_elements}
            current_page = PageInfo(
                page_type=_str_to_page_type(decision.page_type),
                semantic_summary=decision.page_summary,
                elements=elements_dict,
                screenshot_hash=hashlib.md5(screenshot.base64_data.encode()).hexdigest(),
                app=self.app_name,
                width=screenshot.width,
                height=screenshot.height,
            )
            self._record_page(current_page)
            self._extract_and_cluster(current_page)

            self._log(f"  [round {self._convergence.total_rounds + 1}] "
                       f"page={decision.page_type} ({len(visible_elements)} elements)")
            self._log(f"    plan: {[s[:25] for s in decision.plan]}")
            self._log(f"    reason: {decision.reasoning[:80]}")

            if decision.should_stop:
                self._log(f"    supervisor: stop — {decision.stop_reason[:60]}")
                break

            # Execute plan
            pages_before = set(self._discovered_pages.keys())
            clusters_before = len(self._all_clusters)

            result, current_page = self._execute_plan(
                decision, current_page, trajectory,
            )
            self._job_results.append(result)

            new_pages = set(self._discovered_pages.keys()) - pages_before
            new_clusters = len(self._all_clusters) - clusters_before
            self._convergence.record_round(
                new_clusters=new_clusters,
                new_page_types=len(new_pages),
                new_transitions=result.new_transitions_discovered,
            )
            self.edge_lifecycle.advance_step()

            last_result = (
                f"{result.outcome}: "
                f"+{len(new_pages)}页 +{result.new_transitions_discovered}跳转"
            )
            if result.outcome == "blocked":
                self._rollback(screenshot.width, screenshot.height)
                current_page = None
                last_result += " (回退)"

            self._log(f"    → {last_result} | dry={self._convergence.dry_rounds}")

        return trajectory

    # ── Execute Plan ─────────────────────────────────────────

    def _execute_plan(
        self,
        decision: SupervisorDecision,
        start_page: PageInfo,
        trajectory: Trajectory,
    ) -> tuple[JobExecutionResult, PageInfo | None]:
        current_page = start_page
        steps_taken = 0
        new_transitions = 0
        outcome = "success"
        sys_msg = MessageBuilder.create_system_message(_EXECUTOR_SYSTEM_PROMPT)

        for step_idx, instruction in enumerate(decision.plan):
            if self._total_steps >= self.policy.max_total_steps:
                break

            screenshot = self.device.get_screenshot(self.device_id)

            try:
                response = self.vlm.request([
                    sys_msg,
                    MessageBuilder.create_user_message(
                        text=f"执行操作：{instruction}",
                        image_base64=screenshot.base64_data,
                    ),
                ])
            except Exception as e:
                self._log(f"    step {step_idx+1}: VLM error: {e}")
                outcome = "stalled"
                break

            try:
                action = parse_action(response.action)
            except ValueError:
                action = {"_metadata": "do", "action": "Back"}

            if action.get("_metadata") == "finish":
                action = {"_metadata": "do", "action": "Back"}

            trajectory.add_step(current_page, action, response.thinking)
            self._total_steps += 1
            steps_taken += 1

            if not is_safe_exploration_action(
                current_page.page_type.value, action,
                reasoning=response.thinking or "",
                instruction=instruction,
            ):
                self._log(f"    step {step_idx+1}: blocked (unsafe: {instruction[:30]})")
                outcome = "blocked"
                break

            try:
                result = self.action_handler.execute(action, screenshot.width, screenshot.height)
                if not result.success:
                    outcome = "stalled"
                    break
            except Exception:
                outcome = "stalled"
                break

            # Record explored action on this page
            page_key = current_page.state_key()
            self._explored_actions.setdefault(page_key, []).append(instruction)

            time.sleep(self.policy.settle_delay)

        # Post-execution: classify landing page with PageClassifier (for elements)
        new_page_types: list[str] = []
        if steps_taken > 0:
            time.sleep(1.0)
            next_screenshot = self.device.get_screenshot(self.device_id)
            next_page = self._classify_post_action(next_screenshot)
            self._record_page(next_page)
            self._extract_and_cluster(next_page)

            existing_types = {p.page_type.value for p in self._discovered_pages.values()
                              if p.state_key() != next_page.state_key()}
            if next_page.page_type.value not in existing_types:
                new_page_types.append(next_page.page_type.value)

            last_action = trajectory.steps[-1].action if trajectory.steps else {}
            if self._record_transition(current_page, last_action or {}, next_page):
                new_transitions += 1

            current_page = next_page

        return (
            JobExecutionResult(
                job_description=decision.reasoning[:100],
                steps_taken=steps_taken,
                new_page_types_discovered=tuple(new_page_types),
                new_transitions_discovered=new_transitions,
                outcome=outcome,
            ),
            current_page,
        )

    # ── Graph Summary (Supervisor Context) ───────────────────

    def _build_graph_summary(self, current_page: PageInfo) -> str:
        lines = []
        page_type_groups: dict[str, list[str]] = {}
        for key, page in self._discovered_pages.items():
            pt = page.page_type.value
            page_type_groups.setdefault(pt, []).append(key)

        all_explored = self._get_explored_by_page_type()
        transition_pairs: set[tuple[str, str]] = set()
        for t in self._transitions:
            src = t["from"].split(":")[0]
            tgt = t["to"].split(":")[0]
            transition_pairs.add((src, tgt))

        for pt, keys in sorted(page_type_groups.items()):
            best = max(
                (self._discovered_pages[k] for k in keys),
                key=lambda p: len(p.elements or {}),
            )
            elements = best.elements or {}
            explored_set = all_explored.get(pt, set())

            outgoing = sorted({tgt for src, tgt in transition_pairs if src == pt})

            elem_items = []
            for name in list(elements.keys())[:10]:
                mark = "✓" if name in explored_set else "★"
                elem_items.append(f"{mark}{name}")
            elem_str = ", ".join(elem_items) if elem_items else "无元素"

            target_str = ""
            if outgoing:
                target_str = " → " + ", ".join(f"✓{t}" for t in outgoing)

            is_current = current_page.page_type.value == pt
            marker = " ← 当前页" if is_current else ""
            lines.append(f"[{pt}]{marker} {best.semantic_summary[:40]}")
            lines.append(f"  元素: {elem_str}")
            if target_str:
                lines.append(f"  已知跳转:{target_str}")

        if not lines:
            lines.append("(空图谱，首次探索)")

        unexplored_count = sum(
            len(set((self._discovered_pages[keys[0]].elements or {}).keys()) - all_explored.get(pt, set()))
            for pt, keys in page_type_groups.items()
        )
        lines.append(f"\n统计: {len(page_type_groups)}种页面, {len(self._transitions)}条跳转, "
                      f"★未探索元素约{unexplored_count}个")
        return "\n".join(lines)

    def _get_explored_by_page_type(self) -> dict[str, set[str]]:
        """Aggregate explored element names by page_type.

        Instructions like "点击搜索框" match element name "搜索框"
        via substring containment, so the graph summary can mark
        elements as ✓explored vs ★unexplored.
        """
        instructions_by_type: dict[str, list[str]] = {}
        for key, actions in self._explored_actions.items():
            page = self._discovered_pages.get(key)
            if page:
                pt = page.page_type.value
                instructions_by_type.setdefault(pt, []).extend(actions)

        result: dict[str, set[str]] = {}
        for pt, keys in self._page_type_to_keys().items():
            instructions = instructions_by_type.get(pt, [])
            joined = " ".join(instructions)
            explored_elements: set[str] = set()
            for key in keys:
                page = self._discovered_pages.get(key)
                if page and page.elements:
                    for elem_name in page.elements:
                        if elem_name in joined:
                            explored_elements.add(elem_name)
            result[pt] = explored_elements
        return result

    def _page_type_to_keys(self) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = {}
        for key, page in self._discovered_pages.items():
            groups.setdefault(page.page_type.value, []).append(key)
        return groups

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
            observed_target=target_type, risk="normal",
        )

        item = self.functionality_extractor.from_transition(
            {"from": source.state_key(), "action": action, "to": target.state_key()},
            app=self.app_name,
        )
        if item.functionality_id not in {i.functionality_id for i in self._all_items}:
            self._all_items.append(item)
        return True

    # ── Page Management ──────────────────────────────────────

    def _classify_post_action(self, screenshot: Any) -> PageInfo:
        """PageClassifier for post-action classification (returns structured elements)."""
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
            page_type=page_type, semantic_summary=summary,
            elements=elements,
            screenshot_hash=hashlib.md5(screenshot.base64_data.encode()).hexdigest(),
            app=self.app_name, width=screenshot.width, height=screenshot.height,
        )

    def _record_page(self, page_info: PageInfo) -> None:
        key = page_info.state_key()
        existing = self._discovered_pages.get(key)
        if existing is None:
            self._discovered_pages[key] = page_info
            self._log(f"    new page: {key}")
        elif not existing.elements and page_info.elements:
            self._discovered_pages[key] = page_info

    def _rollback(self, width: int, height: int) -> None:
        try:
            self.action_handler.execute({"_metadata": "do", "action": "Back"}, width, height)
            time.sleep(1)
        except Exception:
            pass

    # ── Persistence & Neo4j ──────────────────────────────────

    def _save_results(self, trajectory: Trajectory) -> None:
        timestamp = int(time.time())

        pages_path = self.storage_dir / f"{self.app_name}_autonomous_pages_{timestamp}.json"
        _write_json(pages_path, {
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
                {"step": i+1, "page_type": s.page_info.page_type.value,
                 "page_summary": s.page_info.semantic_summary[:100],
                 "action": s.action, "thinking": (s.action_thinking or "")[:200],
                 "timestamp": s.timestamp}
                for i, s in enumerate(trajectory.steps)
            ],
        })

        transitions_path = self.storage_dir / f"{self.app_name}_autonomous_transitions_{timestamp}.json"
        _write_json(transitions_path, {
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
            "graph_summary": self._build_graph_summary(
                next(iter(self._discovered_pages.values())) if self._discovered_pages else
                PageInfo(ShoppingPageType.UNKNOWN, "", {}, "", self.app_name)),
            "job_results": [r.to_dict() for r in self._job_results],
            "clusters": [c.to_dict() for c in self._all_clusters],
        })

        lifecycle_path = self.storage_dir / f"{self.app_name}_autonomous_lifecycle_{timestamp}.json"
        records, outcomes = self.edge_lifecycle.bulk_export()
        _write_json(lifecycle_path, {
            "app": self.app_name,
            "lifecycle_summary": self.edge_lifecycle.lifecycle_summary(),
            "records": records,
            "outcomes": outcomes,
        })

        self._last_pages_path = pages_path
        self._last_transitions_path = transitions_path
        self._log(f"  saved 5 files to {self.storage_dir}/")

    def _maybe_import_graph(self) -> None:
        if not self.policy.auto_import_graph:
            return
        try:
            from phone_agent.memory.spatial_graph_memory import SpatialGraphMemory
            memory = SpatialGraphMemory()
            result = memory.import_exploration_files(
                self._last_pages_path, self._last_transitions_path,
            )
            self._log(f"  Neo4j import: {result.pages_imported} pages, "
                       f"{result.transitions_imported} transitions, "
                       f"persisted={result.persisted_to_graph}")
        except Exception as e:
            self._log(f"  Neo4j import failed: {e}")

    def _build_coverage_report(self) -> dict[str, Any]:
        screen_count = len(self._discovered_pages)
        unique_hashes = {p.screenshot_hash for p in self._discovered_pages.values()}
        metrics = compute_functionality_coverage(
            items=self._all_items, clusters=self._all_clusters,
            screenshot_count=screen_count, screen_cluster_count=len(unique_hashes),
        )
        gate = FunctionalityQualityGate()
        quality = gate.evaluate(self._all_clusters, metrics.to_dict())
        return {**metrics.to_dict(), "quality_gate": quality.to_dict()}

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg)


def _repair_truncated_json(raw: str) -> str:
    """Best-effort repair for JSON truncated by max_tokens."""
    raw = raw.rstrip()
    open_braces = raw.count("{") - raw.count("}")
    open_brackets = raw.count("[") - raw.count("]")
    if raw.endswith(","):
        raw = raw[:-1]
    in_string = False
    for i, ch in enumerate(raw):
        if ch == '"' and (i == 0 or raw[i-1] != '\\'):
            in_string = not in_string
    if in_string:
        raw += '"'
    raw += "]" * max(0, open_brackets) + "}" * max(0, open_braces)
    return raw


def _write_json(path: Path, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
