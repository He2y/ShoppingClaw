"""Coverage-guided offline explorer: VLM-autonomous app exploration loop."""

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from phone_agent.actions.handler import ActionHandler, parse_action
from phone_agent.config.apps import get_package_name
from phone_agent.device_factory import DeviceFactory
from phone_agent.model.client import MessageBuilder, ModelClient
from phone_agent.spatial.active_builder import ActiveGraphBuilder
from phone_agent.spatial.hypothesis import EdgeHypothesisGenerator
from phone_agent.spatial.semantics import ScreenSemanticsExtractor

from .classifier import PageClassifier
from .prompts import _build_exploration_system_prompt
from .types import (
    CoverageReport,
    CoverageTarget,
    PageInfo,
    ShoppingPageType,
    Trajectory,
    _HIGH_RISK_PAGE_TYPES,
    _PAGE_TYPE_SUMMARY,
    _SCREEN_CHANGE_HASH_LEN,
    _SPEC_TRIGGER_TOKENS,
    _UNSAFE_ACTION_TOKENS,
)


class OfflineExplorer:
    """购物场景离线探索器 — 自然语言任务驱动

    每次运行接受一个自然语言任务描述，VLM 根据描述定向探索 App。
    多次运行不同任务后，合并所有 JSON 输出即可构建完整的 App 页面路径图谱。

    探索循环:
    1. Launch app
    2. Screenshot → VLM decides action → Execute → Analyze result → Record → Repeat
    3. VLM can finish() early when exploration is complete
    """

    # ── Constructor ────────────────────────────────────────────

    def __init__(
        self,
        app_name: str,
        device_factory: DeviceFactory,
        model_client: ModelClient,
        storage_dir: str = "memory_db/exploration",
        max_steps: int = 15,
        task_description: str = "广度优先探索所有主要页面类型",
        classifier_api_key: str | None = "",
        classifier_base_url: str | None = None,
        classifier_model: str = "qwen3-vl-flash",
        auto_import_graph: bool = False,
        graph_store: Any | None = None,
        coverage_targets: CoverageTarget | None = None,
        device_id: str | None = None,
        classifier_mode: str = "fast",
        classifier_timing: str = "after_action",
        classifier_timeout: float = 8.0,
        classifier_max_image_width: int = 720,
        active_exploration: bool = False,
        verbose: bool = True,
    ):
        self.app_name = app_name
        self.device = device_factory
        self.vlm = model_client
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.max_steps = max_steps
        self.task_description = task_description
        self.auto_import_graph = auto_import_graph
        self.graph_store = graph_store
        self.device_id = device_id
        self.classifier_timing = classifier_timing
        self.coverage_targets = coverage_targets or CoverageTarget()
        self.coverage_report = CoverageReport((), self.coverage_targets.page_types, (), self.coverage_targets.transitions)
        self.last_import_result = None
        self.verbose = verbose
        self.active_exploration = active_exploration
        self.semantics_extractor = ScreenSemanticsExtractor(schema_name="shopping")
        self.edge_hypothesis_generator = EdgeHypothesisGenerator(schema_name="shopping")
        self.active_builder = ActiveGraphBuilder()

        # Action handler for executing VLM-decided actions
        self.action_handler = ActionHandler(device_id=device_id)

        # Page classifier using dedicated fast VLM with cropped screenshots
        self.classifier = PageClassifier(
            api_key=classifier_api_key,
            base_url=classifier_base_url,
            model=classifier_model,
            mode=classifier_mode,
            timeout=classifier_timeout,
            max_image_width=classifier_max_image_width,
        )

        # Collected data
        self.discovered_pages: Dict[str, PageInfo] = {}  # state_key → PageInfo
        self.trajectories: List[Trajectory] = []
        self.rejected_transitions: List[Dict[str, Any]] = []
        self.last_rejection_reason = ""
        self.transitions: List[Dict[str, Any]] = []  # from → action → to

    # ── Top-Level Entry ────────────────────────────────────────

    def explore(self) -> List[Trajectory]:
        """Run VLM exploration guided by natural language task description.

        Returns:
            List of exploration trajectories.
        """
        self._log(f"\n{'='*60}")
        self._log(f"  Offline Explorer: {self.app_name}")
        self._log(f"  Task: {self.task_description}")
        self._log(f"{'='*60}\n")

        # Launch the target app
        self._log(f"[0] Launching {self.app_name}...")
        package = get_package_name(self.app_name)
        if not package:
            self._log(f"  ERROR: Unknown app '{self.app_name}', not in APP_PACKAGES")
            return []

        self.device.launch_app(self.app_name, self.device_id)
        time.sleep(4)  # Wait for app to fully load

        # Run the VLM-driven exploration loop
        traj = self._exploration_loop()
        self.trajectories = [traj]

        # Save results
        self._save_results(traj)

        self._log(f"\n{'='*60}")
        self._log(f"  Done: {len(self.discovered_pages)} unique pages discovered")
        self._log(f"  {len(traj.steps)} exploration steps taken")
        self._log(f"{'='*60}\n")
        return self.trajectories

    # ── VLM-Driven Exploration Loop ────────────────────────────

    def _exploration_loop(self) -> Trajectory:
        """Run the task-directed closed-loop VLM exploration.

        Loop: screenshot → VLM decides action → classify page via
        dedicated classifier VLM (cropped screenshot) → record transition
        → execute action → repeat.
        """
        if self.classifier_timing == "after_action":
            return self._exploration_loop_action_first()

        task_desc = self.task_description
        traj = Trajectory(
            task=task_desc,
            app=self.app_name,
        )
        context: List[Dict[str, Any]] = []

        # Build system prompt from natural language task description
        system_prompt = _build_exploration_system_prompt(task_desc)
        context.append(MessageBuilder.create_system_message(system_prompt))

        # Track previous page and action for transition recording
        prev_page_key: Optional[str] = None
        prev_action: Optional[Dict[str, Any]] = None
        prev_screenshot_hash: str = ""

        for step_idx in range(self.max_steps):
            # Capture current screen
            screenshot = self.device.get_screenshot(self.device_id)
            current_app = self.device.get_current_app(self.device_id)

            # ── Screen change detection ──
            cur_hash = screenshot.base64_data[:_SCREEN_CHANGE_HASH_LEN]
            if step_idx > 0 and prev_screenshot_hash and cur_hash == prev_screenshot_hash:
                self._log(f"  ⚠ 屏幕无变化，上次操作可能未生效")
            prev_screenshot_hash = cur_hash

            # Build user message for this step
            screen_info = MessageBuilder.build_screen_info(current_app)
            discovered_summary = self._build_discovered_summary()

            if step_idx == 0:
                task_text = (
                    f"【本次任务】{task_desc}\n"
                    f"开始探索{self.app_name}。你已经在该App中。\n"
                    f"请聚焦任务描述中的方向，不要跳到无关板块。\n\n"
                    f"{discovered_summary}\n\n"
                    f"{screen_info}"
                )
            else:
                task_text = (
                    f"继续探索{self.app_name}。记住：聚焦\"{task_desc}\"方向。\n"
                    f"{discovered_summary}\n\n"
                    f"{screen_info}"
                )

            context.append(MessageBuilder.create_user_message(
                text=task_text, image_base64=screenshot.base64_data
            ))

            # Get VLM's decision for next action (thinking describes current page)
            try:
                response = self.vlm.request(context)
            except Exception as e:
                self._log(f"  VLM error: {e}")
                break

            # Parse the action
            try:
                action = parse_action(response.action)
            except ValueError as e:
                self._log(f"  Parse error: {e}")
                action = {"_metadata": "finish", "message": str(e)}

            # ── Classify page via dedicated VLM with cropped screenshot ──
            page_info = self._classify_page_info(screenshot, current_app or self.app_name, step_idx + 1)
            self._record_page(page_info)
            self._log(f"  [{step_idx+1}] {page_info.page_type.value}: {page_info.semantic_summary[:60]}")

            # ── Record transition from previous step ──
            if prev_page_key is not None and prev_action is not None:
                recorded = self._record_transition(prev_page_key, prev_action, page_info.state_key())
                if not recorded and self._should_stop_after_rejected_transition(self.last_rejection_reason):
                    self._rollback_from_risky_page(screenshot.width, screenshot.height)
                    self._log(f"  stop exploration after rejected transition: {self.last_rejection_reason}")
                    break
            self._update_coverage_report()

            # Check if exploration is complete
            if action.get("_metadata") == "finish":
                traj.add_step(page_info, action, response.thinking)
                self._log(f"  VLM finished: {action.get('message', '')[:100]}")
                break

            # Record step (current page + action about to execute)
            traj.add_step(page_info, action, response.thinking)

            if self.coverage_report.complete:
                self._log("  Coverage target reached; stopping exploration.")
                break

            # Update context
            context[-1] = MessageBuilder.remove_images_from_message(context[-1])
            assistant_content = (
                f" thinking{response.thinking} response<answer>{response.action}</answer>"
            )
            context.append(MessageBuilder.create_assistant_message(assistant_content))

            # Execute the action
            if not self._is_safe_action(page_info, action):
                self._log("  Unsafe exploration action blocked; recording page only.")
                prev_page_key = None
                prev_action = None
                continue
            try:
                result = self.action_handler.execute(
                    action, screenshot.width, screenshot.height
                )
                if self.verbose and not result.success:
                    self._log(f"  Action result: {result.message}")
            except Exception as e:
                self._log(f"  Execute error: {e}")
                prev_page_key = None  # Invalidate transition tracking on failure
                prev_action = None
                continue

            # Brief pause for UI to settle
            time.sleep(2)

            # Track for next transition
            prev_page_key = page_info.state_key()
            prev_action = action

        else:
            # Max steps reached
            self._log(f"  Max steps ({self.max_steps}) reached")
            traj.success = True

        return traj

    def _exploration_loop_action_first(self) -> Trajectory:
        """Run exploration with action execution before post-action classification."""
        task_desc = self.task_description
        traj = Trajectory(task=task_desc, app=self.app_name)
        system_message = MessageBuilder.create_system_message(_build_exploration_system_prompt(task_desc))
        current_page: Optional[PageInfo] = None
        prev_screenshot_hash = ""
        last_step_note = "No action has been verified yet."
        pending_transition: Optional[Dict[str, Any]] = None

        for step_idx in range(self.max_steps):
            screenshot = self.device.get_screenshot(self.device_id)
            current_app = self.device.get_current_app(self.device_id)
            cur_hash = screenshot.base64_data[:_SCREEN_CHANGE_HASH_LEN]
            if step_idx > 0 and prev_screenshot_hash and cur_hash == prev_screenshot_hash:
                self._log("  ⚠ 屏幕无变化，上次操作可能未生效")
            prev_screenshot_hash = cur_hash

            if current_page is None or current_page.screenshot_hash != hashlib.md5(screenshot.base64_data.encode()).hexdigest():
                current_page = self._classify_page_info(screenshot, current_app or self.app_name, step_idx + 1)
            if step_idx > 0 and self.coverage_report.complete:
                self._log("  Coverage target reached; stopping exploration.")
                break

            screen_info = MessageBuilder.build_screen_info(current_app)
            discovered_summary = self._build_discovered_summary()
            current_state_note = self._build_current_state_note(current_page, last_step_note)
            active_frontier_hint = self._build_active_frontier_hint(current_page)
            if step_idx == 0:
                task_text = (
                    f"【本次任务】{task_desc}\n"
                    f"开始探索{self.app_name}。你已经在该App中。\n"
                    f"请聚焦任务描述中的方向，不要跳到无关板块。\n\n"
                    f"{current_state_note}\n\n"
                    f"{discovered_summary}\n\n"
                    f"{screen_info}"
                )
            else:
                task_text = (
                    f"继续探索{self.app_name}。记住：聚焦\"{task_desc}\"方向。\n"
                    f"{current_state_note}\n\n"
                    f"{discovered_summary}\n\n"
                    f"{screen_info}"
                )
            if active_frontier_hint:
                task_text = f"{task_text}\n\n{active_frontier_hint}"
            request_context = [
                system_message,
                MessageBuilder.create_user_message(text=task_text, image_base64=screenshot.base64_data),
            ]

            try:
                response = self.vlm.request(request_context)
            except Exception as e:
                self._log(f"  VLM error: {e}")
                break

            try:
                action = parse_action(response.action)
            except ValueError as e:
                self._log(f"  Parse error: {e}")
                action = {"_metadata": "finish", "message": str(e)}

            reasoning_for_repair = response.thinking
            if action.get("_metadata") == "finish" and str(action.get("message") or "").startswith("Failed to parse action"):
                reasoning_for_repair = f"{response.thinking}\n{response.action}"
            inferred_page_type = self._infer_page_type_from_reasoning(reasoning_for_repair)
            if inferred_page_type and inferred_page_type != current_page.page_type:
                self._log(
                    f"  belief repair: classifier={current_page.page_type.value} "
                    f"reasoning={inferred_page_type.value}"
                )
                current_page = self._relabel_page_info(current_page, inferred_page_type)
            self._record_page(current_page)

            if pending_transition:
                recorded, last_step_note = self._finalize_pending_transition(
                    pending_transition,
                    current_page,
                )
                pending_transition = None
                if not recorded and self._should_stop_after_rejected_transition(self.last_rejection_reason):
                    self._log(f"  stop exploration after rejected transition: {self.last_rejection_reason}")
                    break

            traj.add_step(current_page, action, response.thinking)
            self._log(f"  [{step_idx+1}] {current_page.page_type.value}: {current_page.semantic_summary[:60]}")

            if action.get("_metadata") == "finish":
                self._log(f"  VLM finished: {action.get('message', '')[:100]}")
                break

            if self.coverage_report.complete:
                self._log("  Coverage target reached; stopping exploration.")
                break

            if not self._is_safe_action(current_page, action, response.thinking):
                self._log("  Unsafe exploration action blocked; recording page only.")
                if current_page.page_type in _HIGH_RISK_PAGE_TYPES:
                    self._rollback_from_risky_page(screenshot.width, screenshot.height)
                last_step_note = (
                    f"Last action was blocked as unsafe on {current_page.page_type.value}. "
                    "Choose a rollback, close, or safe navigation action from the current screenshot."
                )
                current_page = None
                continue

            try:
                result = self.action_handler.execute(action, screenshot.width, screenshot.height)
                if self.verbose and not result.success:
                    self._log(f"  Action result: {result.message}")
            except Exception as e:
                self._log(f"  Execute error: {e}")
                last_step_note = f"Last action execution failed: {e}. Do not repeat the same action blindly."
                current_page = None
                continue

            time.sleep(2)
            if not result.success:
                last_step_note = f"Last action failed: {result.message}. Do not repeat the same action blindly."
                continue

            next_screenshot = self.device.get_screenshot(self.device_id)
            next_app = self.device.get_current_app(self.device_id) or self.app_name
            next_page = self._classify_page_info(next_screenshot, next_app, step_idx + 1, prefix="post-action")
            pending_transition = {
                "from_key": current_page.state_key(),
                "action": action,
            }
            last_step_note = (
                f"Last action executed from {current_page.state_key()}; raw post-action "
                f"classifier saw {next_page.page_type.value}:{next_page.semantic_summary}. "
                "The next step must verify the actual landing page before planning."
            )
            current_page = next_page

        else:
            self._log(f"  Max steps ({self.max_steps}) reached")
            traj.success = True

        if pending_transition and current_page is not None:
            _, last_step_note = self._finalize_pending_transition(pending_transition, current_page)

        return traj

    # ── Helpers ─────────────────────────────────────────────────

    def _classify_page_info(self, screenshot: Any, app: str, step: int, prefix: str = "classifier") -> PageInfo:
        page_type, summary, elements = self.classifier.classify(
            screenshot.base64_data,
            screenshot.width,
            screenshot.height,
        )
        page_info = PageInfo(
            page_type=page_type,
            semantic_summary=summary,
            elements=elements,
            screenshot_hash=hashlib.md5(screenshot.base64_data.encode()).hexdigest(),
            app=app,
            screenshot_base64=screenshot.base64_data,
            width=screenshot.width,
            height=screenshot.height,
        )
        self._log(
            f"  {prefix} step={step} mode={self.classifier.mode} "
            f"source={self.classifier.source} model={self.classifier.model} "
            f"time={self.classifier.last_duration:.2f}s "
            f"-> {page_info.page_type.value}"
        )
        return page_info

    def _record_page(self, page_info: PageInfo):
        """Record a discovered page, deduplicating by state_key."""
        key = page_info.state_key()
        if key not in self.discovered_pages:
            self.discovered_pages[key] = page_info
            if self.verbose:
                self._log(f"    NEW: {page_info.page_type.value}")

    @staticmethod
    def _build_current_state_note(page_info: PageInfo | None, last_step_note: str) -> str:
        if page_info is None:
            return (
                "Classifier hypothesis (may be wrong): unknown.\n"
                f"Verifier note: {last_step_note}\n"
                "Use the screenshot as ground truth; explicitly state what the current screen appears to be."
            )
        return (
            f"Classifier hypothesis (may be wrong): {page_info.page_type.value} - {page_info.semantic_summary}.\n"
            f"Verifier note: {last_step_note}\n"
            "Use the screenshot as ground truth; if the screenshot conflicts with the hypothesis, correct it."
        )

    @staticmethod
    def _infer_page_type_from_reasoning(text: str) -> ShoppingPageType | None:
        """Infer a weak page belief from the action model's current-screen reasoning."""
        if not text:
            return None
        excluded_markers = (
            "classifier hypothesis",
            "verifier note",
            "raw post-action",
            "核心空间骨架",
            "用户要求",
            "任务要求",
            "system-reminder",
        )
        visual_markers = (
            "当前截图",
            "当前页面是",
            "当前状态",
            "当前已经",
            "从截图",
            "截图显示",
            "屏幕显示",
            "我可以看到",
            "我看到",
            "看起来",
            "当前界面显示",
            "现在看到",
            "显示的是",
        )
        lines = []
        capturing_visual_context = False
        for raw_line in text.splitlines():
            line = raw_line.strip().lower()
            if not line or any(marker in line for marker in excluded_markers):
                continue
            if any(marker in line for marker in visual_markers):
                capturing_visual_context = True
            if capturing_visual_context:
                lines.append(line)
            if len(lines) >= 12:
                break
        if not lines:
            return None

        for scoped in ["\n".join(lines), *lines[:8]]:
            if "权限" in scoped or "permission" in scoped:
                return ShoppingPageType.PERMISSION
            if OfflineExplorer._is_settings_page_evidence(scoped):
                return ShoppingPageType.SETTINGS
            if any(token in scoped for token in ("支付页", "付款页面", "收银台", "支付密码", "付款方式", "payment page")):
                return ShoppingPageType.PAYMENT
            if any(token in scoped for token in ("地址", "address")):
                return ShoppingPageType.ADDRESS
            if OfflineExplorer._is_login_page_evidence(scoped):
                return ShoppingPageType.LOGIN
            if any(token in scoped for token in ("订单确认", "确认订单", "checkout")):
                return ShoppingPageType.CHECKOUT
            if any(
                token in scoped
                for token in ("规格选择", "规格弹窗", "适用手机型号", "颜色分类", "型号选项", "sku")
            ):
                return ShoppingPageType.SPEC_SELECTION
            if any(token in scoped for token in ("弹窗", "优惠券", "广告", "活动面板", "dialog")):
                return ShoppingPageType.DIALOG
            if any(token in scoped for token in ("搜索输入", "搜索建议", "历史搜索", "猜你想搜", "键盘", "search_input")):
                return ShoppingPageType.SEARCH_INPUT
            if any(token in scoped for token in ("商品详情", "详情页", "product_detail")):
                return ShoppingPageType.PRODUCT_DETAIL
            if any(token in scoped for token in ("规格", "spec_selection")) and any(
                token in scoped for token in ("弹窗", "半屏", "选择", "选项")
            ):
                return ShoppingPageType.SPEC_SELECTION
            if any(token in scoped for token in ("筛选面板", "筛选条件", "filter_panel")):
                return ShoppingPageType.FILTER_PANEL
            if OfflineExplorer._is_cart_page_evidence(scoped):
                return ShoppingPageType.CART
            if any(token in scoped for token in ("搜索结果", "结果页面", "商品列表", "search_result")):
                return ShoppingPageType.SEARCH_RESULT
            if any(token in scoped for token in ("首页", "home")):
                return ShoppingPageType.HOME
        return None

    @staticmethod
    def _is_settings_page_evidence(text: str) -> bool:
        settings_tokens = (
            "设置页",
            "设置页面",
            "账号与安全",
            "隐私设置",
            "通用设置",
            "消息通知",
            "支付设置",
            "国家与地区",
            "切换账号",
            "退出登录",
            "settings page",
        )
        return any(token.lower() in text for token in settings_tokens)

    @staticmethod
    def _is_login_page_evidence(text: str) -> bool:
        login_page_tokens = (
            "登录页",
            "登录页面",
            "手机号输入",
            "密码输入",
            "验证码",
            "login page",
        )
        return any(token.lower() in text for token in login_page_tokens) or (
            "登录按钮" in text and ("手机号" in text or "验证码" in text or "密码" in text)
        )

    @staticmethod
    def _is_cart_page_evidence(text: str) -> bool:
        """Return true only when the line describes the cart page itself.

        Product detail pages often expose a top-right cart entry with a badge.
        That should remain product_detail; cart requires page-level evidence
        such as item checkboxes, all-select, or checkout controls.
        """
        cart_page_tokens = (
            "购物车页面",
            "购物车页",
            "购物车列表",
            "我的购物车",
            "购物车中",
            "cart page",
            "cart list",
        )
        cart_control_tokens = (
            "全选",
            "去结算",
            "结算按钮",
            "编辑/管理",
            "管理按钮",
            "商品复选框",
            "checkbox",
            "checkout button",
        )
        return any(token in text for token in cart_page_tokens) or (
            "购物车" in text and any(token in text for token in cart_control_tokens)
        )

    @staticmethod
    def _relabel_page_info(page_info: PageInfo, page_type: ShoppingPageType) -> PageInfo:
        summary = _PAGE_TYPE_SUMMARY.get(page_type, page_info.semantic_summary)
        return PageInfo(
            page_type=page_type,
            semantic_summary=summary,
            elements=page_info.elements,
            screenshot_hash=page_info.screenshot_hash,
            app=page_info.app,
            screenshot_base64=page_info.screenshot_base64,
            width=page_info.width,
            height=page_info.height,
        )

    def _finalize_pending_transition(
        self,
        pending_transition: Dict[str, Any],
        current_page: PageInfo,
    ) -> tuple[bool, str]:
        self._record_page(current_page)
        from_key = str(pending_transition.get("from_key") or "")
        action = pending_transition.get("action") or {}
        to_key = current_page.state_key()
        recorded = self._record_transition(from_key, action, to_key)
        self._update_coverage_report()
        if recorded:
            note = f"Last verified transition accepted: {from_key} -> {to_key}."
        else:
            note = (
                f"Last transition rejected: {from_key} -> {to_key}; "
                f"reason={self.last_rejection_reason}. Re-localize from the current screenshot "
                "and avoid repeating the same coordinate."
            )
        return recorded, note

    def _record_transition(self, from_key: str, action: Dict[str, Any], to_key: str) -> bool:
        """Record a page transition: from_page → action → to_page."""
        source = self.discovered_pages.get(from_key)
        target = self.discovered_pages.get(to_key)
        rejection = self._transition_rejection_reason(source, action, target)
        self.last_rejection_reason = rejection
        if rejection:
            item = {
                "from": from_key,
                "action": action,
                "to": to_key,
                "reason": rejection,
            }
            self.rejected_transitions.append(item)
            self._log(f"  rejected transition: {from_key} -> {to_key}; {rejection}")
            return False
        self.transitions.append({
            "from": from_key,
            "action": action,
            "to": to_key,
        })
        self._update_coverage_report()
        return True

    def _update_coverage_report(self) -> CoverageReport:
        if not hasattr(self, "coverage_targets"):
            self.coverage_targets = CoverageTarget()
        covered_pages = tuple(sorted({page.page_type.value for page in self.discovered_pages.values()}))
        missing_pages = tuple(page_type for page_type in self.coverage_targets.page_types if page_type not in covered_pages)

        covered_edges_set: set[tuple[str, str]] = set()
        for item in self.transitions:
            source_type = str(item.get("from") or "").partition(":")[0]
            target_type = str(item.get("to") or "").partition(":")[0]
            if source_type and target_type:
                covered_edges_set.add((source_type, target_type))
        covered_edges = tuple(sorted(covered_edges_set))
        missing_edges = tuple(edge for edge in self.coverage_targets.transitions if edge not in covered_edges_set)
        self.coverage_report = CoverageReport(
            covered_page_types=covered_pages,
            missing_page_types=missing_pages,
            covered_transitions=covered_edges,
            missing_transitions=missing_edges,
        )
        return self.coverage_report

    def _is_safe_action(self, page_info: PageInfo, action: Dict[str, Any], reasoning: str = "") -> bool:
        if page_info.page_type in _HIGH_RISK_PAGE_TYPES:
            return False
        if action.get("_metadata") == "finish":
            return True
        action_text = json.dumps(action, ensure_ascii=False).lower()
        if OfflineExplorer._action_text_has_unsafe_token(action_text, page_info):
            return False
        return not OfflineExplorer._unsafe_intent_mentioned(reasoning or "", page_info)

    @staticmethod
    def _action_text_has_unsafe_token(text: str, page_info: PageInfo) -> bool:
        if any(token.lower() in text for token in _UNSAFE_ACTION_TOKENS):
            return True
        if page_info.page_type in {ShoppingPageType.PRODUCT_DETAIL, ShoppingPageType.SPEC_SELECTION}:
            return False
        return any(token.lower() in text for token in _SPEC_TRIGGER_TOKENS)

    @staticmethod
    def _unsafe_intent_mentioned(reasoning: str, page_info: PageInfo | None = None) -> bool:
        action_markers = ("我将", "我要", "准备", "下一步", "接下来", "现在", "让我", "tap")
        negation_markers = ("不要", "不能", "禁止", "避免", "不应该", "不会", "不点击", "不要点击")
        task_markers = ("用户要求", "任务流程", "具体步骤", "根据任务", "给出了", "包括：", "需要：")
        page_type = page_info.page_type if page_info else None
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
            if page_type not in {ShoppingPageType.PRODUCT_DETAIL, ShoppingPageType.SPEC_SELECTION}:
                if any(token.lower() in line for token in _SPEC_TRIGGER_TOKENS):
                    return True
        return False

    def _rollback_from_risky_page(self, screen_width: int, screen_height: int) -> None:
        try:
            self.action_handler.execute({"_metadata": "do", "action": "Back"}, screen_width, screen_height)
            time.sleep(1)
            self._log("  repair: backed out from risky page")
        except Exception as exc:
            self._log(f"  repair failed: {exc}")

    def _transition_rejection_reason(
        self,
        source: PageInfo | None,
        action: Dict[str, Any],
        target: PageInfo | None,
    ) -> str:
        """Reject noisy exploration edges before they reach saved artifacts."""
        if not source or not target:
            return "missing page metadata"
        source_type = source.page_type.value
        target_type = target.page_type.value
        if source.page_type in _HIGH_RISK_PAGE_TYPES or target.page_type in _HIGH_RISK_PAGE_TYPES:
            return "high-risk page boundary"
        if source_type == "dialog":
            return ""
        action_type = str(action.get("action") or action.get("action_type") or "").lower()
        if source_type == target_type:
            if source_type in {"search_input", "filter_panel"} and action_type in {"tap", "type", "type_name", "input"}:
                return ""
            return "self-loop or unchanged screen"
        if action_type in {"type", "wait"}:
            return "non-navigation action"

        pair = (source_type, target_type)
        if pair in {
            ("home", "search_input"),
            ("search_input", "search_result"),
            ("filter_panel", "search_result"),
            ("product_detail", "spec_selection"),
            ("spec_selection", "cart"),
            ("spec_selection", "product_detail"),
        }:
            return ""
        if pair == ("product_detail", "cart"):
            if OfflineExplorer._looks_like_top_cart_entry_tap(action):
                return ""
            return "product_detail->cart must use top cart entry, not bottom add-to-cart CTA"
        if pair == ("search_result", "product_detail") and self._looks_like_product_card_tap(action):
            return ""
        if pair == ("search_result", "filter_panel") and self._looks_like_filter_button_tap(action):
            return ""
        if pair == ("search_result", "spec_selection") and self._looks_like_product_card_tap(action):
            return ""
        return "unexpected shopping flow transition"

    @staticmethod
    def _looks_like_product_card_tap(action: Dict[str, Any]) -> bool:
        element = action.get("element")
        if not isinstance(element, list):
            return False
        if len(element) == 1 and isinstance(element[0], list):
            element = element[0]
        try:
            y = float(element[1]) if len(element) >= 2 else -1
        except (TypeError, ValueError):
            return False
        return y >= 250

    @staticmethod
    def _looks_like_filter_button_tap(action: Dict[str, Any]) -> bool:
        element = action.get("element")
        if not isinstance(element, list):
            return False
        if len(element) == 1 and isinstance(element[0], list):
            element = element[0]
        try:
            x = float(element[0])
            y = float(element[1])
        except (TypeError, ValueError, IndexError):
            return False
        return x >= 800 and 150 <= y <= 420

    @staticmethod
    def _looks_like_top_cart_entry_tap(action: Dict[str, Any]) -> bool:
        element = action.get("element")
        if not isinstance(element, list):
            return False
        if len(element) == 1 and isinstance(element[0], list):
            element = element[0]
        try:
            x = float(element[0])
            y = float(element[1])
        except (TypeError, ValueError, IndexError):
            return False
        return x >= 650 and y <= 220

    @staticmethod
    def _should_stop_after_rejected_transition(reason: str) -> bool:
        return reason == "high-risk page boundary"

    def _build_discovered_summary(self) -> str:
        """Build a summary of discovered pages for the VLM context."""
        if not self.discovered_pages:
            missing = ", ".join(self.coverage_targets.page_types)
            return f"尚未发现任何页面。请优先覆盖这些页面类型: {missing}"

        by_type: Dict[str, int] = {}
        for p in self.discovered_pages.values():
            t = p.page_type.value
            by_type[t] = by_type.get(t, 0) + 1

        type_lines = "\n".join(f"  - {t}: {c}个" for t, c in sorted(by_type.items()))
        coverage = self._update_coverage_report()
        missing_pages = ", ".join(coverage.missing_page_types) or "无"
        missing_edges = ", ".join(f"{a}->{b}" for a, b in coverage.missing_transitions) or "无"
        return (
            f"已发现 {len(self.discovered_pages)} 个页面:\n{type_lines}\n"
            f"缺失页面类型: {missing_pages}\n"
            f"缺失转移: {missing_edges}\n"
            "请优先选择能补齐缺失页面或缺失转移的安全动作，避免支付、提交订单和地址确认。"
        )

    def _build_active_frontier_hint(self, page_info: PageInfo | None) -> str:
        """Return a compact AMSG frontier hint for active exploration."""
        if not self.active_exploration or page_info is None:
            return ""
        node = self.semantics_extractor.node_from_exploration_page(
            {
                "app": page_info.app,
                "page_type": page_info.page_type.value,
                "summary": page_info.semantic_summary,
                "elements": page_info.elements,
                "screenshot_hash": page_info.screenshot_hash,
            },
            fallback_app=self.app_name,
        )
        goal_page_types = set(self._update_coverage_report().missing_page_types)
        hypotheses = self.edge_hypothesis_generator.generate(node, goal_page_types=goal_page_types)
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
        if page_info.page_type == ShoppingPageType.PRODUCT_DETAIL:
            lines.append(
                "For product_detail -> spec_selection, Taobao usually opens the spec sheet only after "
                "tapping a bottom CTA such as '加入购物车', '立即购买', '领券购买', or a campaign-specific "
                "buy CTA. Do not hard-code the label; tap the CTA only to verify that the postcondition "
                "is spec_selection, and roll back if it lands on checkout/payment/address."
            )
            lines.append(
                "For product_detail -> cart, use only the top-right cart entry/icon. Do not use the bottom "
                "加入购物车/立即购买 CTA for this edge, because that CTA opens spec_selection."
            )
        if page_info.page_type == ShoppingPageType.SPEC_SELECTION:
            lines.append(
                "For spec_selection -> cart/checkout, choose required specs first, then tap the second-stage "
                "confirm CTA. Do not submit order, pay, or confirm address after landing."
            )
        lines.append("After action, stop before payment, order submission, login, or address confirmation.")
        return "\n".join(lines)

    def _log(self, msg: str):
        if self.verbose:
            print(msg)

    # ── Persistence ─────────────────────────────────────────────

    def _save_results(self, traj: Trajectory):
        """Save all exploration data to JSON files."""
        timestamp = int(time.time())

        # Save pages catalog
        pages_data = {
            "app": self.app_name,
            "task": self.task_description,
            "explored_at": datetime.now().isoformat(),
            "total_pages": len(self.discovered_pages),
            "coverage": self._update_coverage_report().to_dict(),
            "pages": [
                {
                    "page_type": p.page_type.value,
                    "summary": p.semantic_summary,
                    "elements": p.elements,
                    "screenshot_hash": p.screenshot_hash,
                    "app": p.app,
                }
                for p in self.discovered_pages.values()
            ],
        }
        pages_path = self.storage_dir / f"{self.app_name}_explore_{timestamp}.json"
        with open(pages_path, "w", encoding="utf-8") as f:
            json.dump(pages_data, f, ensure_ascii=False, indent=2)
        self._log(f"  saved: {pages_path.name}")

        # Save trajectory
        traj_data = {
            "task": traj.task,
            "app": traj.app,
            "success": traj.success,
            "total_steps": len(traj.steps),
            "steps": [
                {
                    "step": i + 1,
                    "page_type": s.page_info.page_type.value,
                    "page_summary": s.page_info.semantic_summary,
                    "page_elements": s.page_info.elements,
                    "screenshot_hash": s.page_info.screenshot_hash,
                    "action": s.action,
                    "thinking": s.action_thinking[:200],
                    "timestamp": s.timestamp,
                }
                for i, s in enumerate(traj.steps)
            ],
        }
        traj_path = self.storage_dir / f"{self.app_name}_explore_trajectory_{timestamp}.json"
        with open(traj_path, "w", encoding="utf-8") as f:
            json.dump(traj_data, f, ensure_ascii=False, indent=2)
        self._log(f"  saved: {traj_path.name}")

        # Save transitions (edges)
        trans_path = None
        if self.transitions or self.rejected_transitions:
            transitions_data = {
                "app": self.app_name,
                "task": self.task_description,
                "total_transitions": len(self.transitions),
                "coverage": self._update_coverage_report().to_dict(),
                "transitions": self.transitions,
                "rejected_transitions": self.rejected_transitions,
            }
            trans_path = self.storage_dir / f"{self.app_name}_explore_transitions_{timestamp}.json"
            with open(trans_path, "w", encoding="utf-8") as f:
                json.dump(transitions_data, f, ensure_ascii=False, indent=2)
            self._log(f"  saved: {trans_path.name} ({len(self.transitions)} transitions)")

        if self.auto_import_graph:
            self._import_saved_graph(pages_path, trans_path)

    def _import_saved_graph(self, pages_path: Path, transitions_path: Path | None) -> None:
        """Import saved exploration artifacts into SpatialGraphMemory."""
        owns_graph_store = self.graph_store is None
        graph_store = self.graph_store
        if graph_store is None:
            from ..graph_store import GraphStore

            graph_store = GraphStore()

        try:
            from ..spatial_graph_memory import SpatialGraphMemory

            memory = SpatialGraphMemory(graph_store)
            states, edges, staging_report = memory.import_exploration_staging(pages_path, transitions_path)
            promote_report = memory.promote_staging_to_canonical(states, edges, persist=True)
            self.last_import_result = {
                "staging": staging_report.to_dict(),
                "promoted": promote_report.to_dict(),
                "pages_imported": staging_report.canonical_pages,
                "transitions_imported": staging_report.transitions_promoted,
            }
            self._log(
                "  imported spatial graph: "
                f"{self.last_import_result['pages_imported']} pages, "
                f"{self.last_import_result['transitions_imported']} transitions"
            )
        finally:
            if owns_graph_store and graph_store:
                graph_store.close()


def _build_taobao_task() -> str:
    return (
        "覆盖淘宝购物核心空间骨架：home -> search_input -> search_result -> "
        "product_detail -> spec_selection -> cart。"
        "只执行安全探索动作，不提交订单、不支付、不确认地址。"
        "如果进入登录、支付、地址或订单确认页，立刻返回。"
    )
