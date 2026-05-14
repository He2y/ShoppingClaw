"""
Offline Explorer for Shopping Apps.

Systematically explores shopping apps using a VLM-autonomous loop:
    1. Launch target app
    2. Screenshot → VLM decides next exploration action → Execute → Repeat
    3. After each navigation, classify the resulting page
    4. Record all discovered pages, elements, and transitions

Architecture:
    OfflineExplorer
        ├─ DeviceFactory  (screenshots, taps, app launch)
        ├─ ModelClient    (VLM inference)
        ├─ ActionHandler  (action execution + coordinate conversion)
        └─ 产出: pages.json + transitions.json + trajectories.json
"""

import base64
import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

from openai import OpenAI
from PIL import Image

from phone_agent.actions.handler import ActionHandler, parse_action
from phone_agent.config.apps import get_package_name
from phone_agent.device_factory import DeviceFactory
from phone_agent.model.client import MessageBuilder, ModelClient


# ── Enums & Data Classes ───────────────────────────────────────

class ShoppingPageType(Enum):
    """购物场景的页面类型"""
    HOME = "home"
    SEARCH_INPUT = "search_input"
    SEARCH_RESULT = "search_result"
    PRODUCT_DETAIL = "product_detail"
    SPEC_SELECTION = "spec_selection"
    CART = "cart"
    CHECKOUT = "checkout"
    CATEGORY = "category"
    MY_ACCOUNT = "my_account"
    STORE = "store"
    LOGIN = "login"
    UNKNOWN = "unknown"


@dataclass
class PageInfo:
    """Page analysis result."""
    page_type: ShoppingPageType
    semantic_summary: str
    elements: Dict[str, Any]
    screenshot_hash: str
    app: str
    screenshot_base64: str = ""
    width: int = 0
    height: int = 0

    def state_key(self) -> str:
        """Generate a stable key for deduplication."""
        return f"{self.page_type.value}:{self.semantic_summary[:60]}"


@dataclass
class ExplorationStep:
    """Single exploration step."""
    page_info: PageInfo
    action: Optional[Dict[str, Any]] = None
    action_thinking: str = ""
    timestamp: str = ""


@dataclass
class Trajectory:
    """Exploration trajectory through the app."""
    task: str
    app: str
    steps: List[ExplorationStep] = field(default_factory=list)
    success: bool = True

    def add_step(self, page_info: PageInfo, action: Optional[Dict] = None, thinking: str = ""):
        self.steps.append(ExplorationStep(
            page_info=page_info,
            action=action,
            action_thinking=thinking,
            timestamp=datetime.now().isoformat(),
        ))


# ── Page Classifier ─────────────────────────────────────────────

# Crop ratios for removing persistent UI chrome before classification.
# Bottom nav bar (首页/购物车/我的 tabs) appears on every screen and
# pollutes both regex matching and VLM classification. By cropping it
# out, the classifier sees only the actual page content.
_CROP_TOP_RATIO = 0.04    # Status bar
_CROP_BOTTOM_RATIO = 0.12  # Bottom nav bar + safe area

# Screen change detection: compare first N chars of base64 screenshots.
# Two identical screenshots mean the last action had no visible effect.
_SCREEN_CHANGE_HASH_LEN = 2000

# Map JSON strings back to ShoppingPageType enum
_PAGE_TYPE_MAP: Dict[str, ShoppingPageType] = {
    "home": ShoppingPageType.HOME,
    "search_input": ShoppingPageType.SEARCH_INPUT,
    "search_result": ShoppingPageType.SEARCH_RESULT,
    "product_detail": ShoppingPageType.PRODUCT_DETAIL,
    "spec_selection": ShoppingPageType.SPEC_SELECTION,
    "cart": ShoppingPageType.CART,
    "checkout": ShoppingPageType.CHECKOUT,
    "category": ShoppingPageType.CATEGORY,
    "my_account": ShoppingPageType.MY_ACCOUNT,
    "store": ShoppingPageType.STORE,
    "login": ShoppingPageType.LOGIN,
    "unknown": ShoppingPageType.UNKNOWN,
}

_CLASSIFIER_SYSTEM_PROMPT = (
    "你是一个移动应用页面分类器。识别购物App当前显示的页面类型，并列出页面中的关键交互元素。\n\n"
    "**重要**: 截图已经裁剪掉了顶部状态栏和底部导航栏（首页/购物车/我的等Tab）。\n"
    "你只能看到页面的主内容区域。请仅根据主内容区域判断页面类型，不要猜测被裁剪掉的部分。\n\n"
    "=== 页面类型定义 ===\n"
    "- home: 首页 — Banner轮播图、推荐商品网格、搜索框入口、活动入口图标\n"
    "- search_input: 搜索输入页 — 搜索框已激活(有光标)、键盘已弹出、显示搜索历史或热门搜索词\n"
    "- search_result: 搜索结果页 — 商品卡片列表、顶部有搜索框(未激活)、筛选/排序按钮(价格/销量/综合)\n"
    "- product_detail: 商品详情页 — 单个商品大图、价格(¥符号)、商品名称、加入购物车/立即购买按钮\n"
    "- spec_selection: 规格选择 — 弹窗或半屏面板、颜色/尺寸/容量等选项按钮、数量选择器、显示价格\n"
    "- cart: 购物车 — 商品列表每项带圆形复选框、有全选按钮、有结算/去结算按钮、有编辑/管理按钮\n"
    "- checkout: 结算/订单确认 — 收货地址、支付方式选择、商品清单、提交订单按钮\n"
    "- category: 分类页 — 左侧一级分类列表+右侧子分类网格、或分类图标网格布局\n"
    "- my_account: 个人中心 — 用户头像区域、订单入口(待付款/待发货/待收货)、优惠券/收藏/足迹等入口\n"
    "- store: 店铺主页 — 店铺Logo和名称、店铺评分、店铺内商品列表、关注按钮\n"
    "- login: 登录页 — 手机号输入框、密码输入框、登录按钮、验证码、第三方登录图标\n"
    "- unknown: 以上都不匹配或无法判断\n\n"
    "=== elements 字段说明 ===\n"
    "列出页面中可见的关键交互元素，用简短中文命名。只列功能性组件（按钮、输入框、列表、选择器等），\n"
    "不要列纯展示内容（文字、图片）。命名要通用化，不要包含具体商品名或价格。\n"
    "示例: {\"search_bar\": \"顶部搜索栏\", \"product_cards\": \"商品卡片列表\", \"filter_buttons\": \"筛选排序按钮\"}\n\n"
    "=== summary 字段说明 ===\n"
    "用不超过15个字概括页面功能，不要包含具体商品名/品牌名/价格。\n"
    "示例: \"商品搜索结果列表\" 而非 \"OPPO Find X9手机搜索结果页\"\n\n"
    "=== 输出格式 ===\n"
    '严格输出JSON，不要加任何额外文字：\n'
    '{"page_type": "<类型>", "summary": "<≤15字功能概括>", "elements": {"元素名": "简短描述"}}'
)


class PageClassifier:
    """Dedicated page classifier using a fast VLM with cropped screenshots.

    The bottom navigation bar ("首页", "购物车", "我的" tabs) appears on
    every screen and is the root cause of false-positive page classification.
    We crop it out before sending the image to the classifier, so the VLM
    only sees the actual page content area.

    Uses a separate lightweight model (qwen3-vl-flash) for speed and cost.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://dashscope.aliyuncs.com/apps/anthropic",
        model: str = "qwen3-vl-flash",
    ):
        self.client = OpenAI(base_url=base_url, api_key=api_key)
        self.model = model

    def classify(self, screenshot_base64: str, width: int, height: int) -> tuple[ShoppingPageType, str, Dict[str, str]]:
        """Classify page type and extract elements from a cropped screenshot.

        Args:
            screenshot_base64: Full screenshot as base64 string.
            width: Screenshot width in pixels.
            height: Screenshot height in pixels.

        Returns:
            (ShoppingPageType, summary, elements_dict). Returns (UNKNOWN, reason, {})
            on any failure.
        """
        try:
            cropped_b64 = self._crop_screenshot(screenshot_base64, width, height)
        except Exception as e:
            return ShoppingPageType.UNKNOWN, f"crop error: {e}", {}

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": _CLASSIFIER_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{cropped_b64}"},
                            },
                            {"type": "text", "text": "请分类这个页面"},
                        ],
                    },
                ],
                max_tokens=500,
                temperature=0.0,
            )
            raw = response.choices[0].message.content or ""
            result = json.loads(raw.strip())
        except (json.JSONDecodeError, Exception) as e:
            return ShoppingPageType.UNKNOWN, f"API/parse error: {e}", {}

        page_type = _PAGE_TYPE_MAP.get(result.get("page_type", "").strip(), ShoppingPageType.UNKNOWN)
        summary = result.get("summary", "").strip() or f"未命名-{page_type.value}"
        elements = result.get("elements", {})
        if not isinstance(elements, dict):
            elements = {}
        return page_type, summary, elements

    @staticmethod
    def _crop_screenshot(base64_str: str, width: int, height: int) -> str:
        """Crop out status bar (top) and nav bar (bottom) from screenshot.

        Keeps only the main content area so the classifier isn't confused
        by persistent UI chrome.
        """
        img_data = base64.b64decode(base64_str)
        img = Image.open(BytesIO(img_data))

        crop_top = int(height * _CROP_TOP_RATIO)
        crop_bottom = int(height * (1.0 - _CROP_BOTTOM_RATIO))

        if crop_top >= crop_bottom or crop_bottom - crop_top < 100:
            # Screen too small to crop meaningfully, use as-is
            return base64_str

        cropped = img.crop((0, crop_top, width, crop_bottom))

        buf = BytesIO()
        cropped.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()


# ── Exploration System Prompt ──────────────────────────────────

def _build_exploration_system_prompt(task_description: str) -> str:
    """Build exploration system prompt from natural language task description."""
    today = datetime.today()
    weekday_names = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    weekday = weekday_names[today.weekday()]
    formatted_date = today.strftime("%Y年%m月%d日") + " " + weekday

    return (
        "今天的日期是: " + formatted_date + "\n"
        "你是一个移动应用探索智能体，根据用户的任务描述定向探索购物App。\n"
        "你必须严格按照要求输出以下格式：\n"
        " thinking{think} response\n"
        "<answer>{action}</answer>\n\n"

        "操作指令及其作用如下：\n"
        '- do(action="Tap", element=[x,y])\n'
        "    Tap是点击操作，点击屏幕上的特定点。坐标系统从左上角(0,0)到右下角(999,999)。\n"
        '- do(action="Swipe", start=[x1,y1], end=[x2,y2])\n'
        "    Swipe是滑动操作，用于滚动内容。\n"
        '- do(action="Back")\n'
        "    导航返回上一个屏幕，关闭弹窗。\n"
        '- do(action="Type", text="xxx")\n'
        "    Type是输入操作，在当前聚焦的输入框中输入文本。\n"
        '- do(action="Wait", duration="x seconds")\n'
        "    等待页面加载。\n"
        '- finish(message="xxx")\n'
        "    finish是结束探索任务的操作，message中列出你发现的所有页面类型。\n\n"

        "=== 你的身份 ===\n"
        "你是App探索者。系统已启动目标App，你需要根据用户的任务描述，\n"
        "聚焦指定方向，系统性地探索涉及的页面类型和页面跳转关系。\n"
        "把自己想象成测试工程师在做探索性测试。\n\n"

        "=== 本次探索任务 ===\n"
        + task_description + "\n\n"

        "=== 重要约束 ===\n"
        "- 不需要登录，遇到登录界面请Back\n"
        "- 不要下单购买任何商品（可以进入结算页观察，但不要提交订单）\n"
        "- 不要修改任何个人信息\n"
        "- 遇到广告弹窗点X关闭或用Back跳过\n"
        "- 聚焦任务描述中的方向，不要跳到无关板块\n"
        "- 共探索10-15步后 finish 报告你发现了哪些页面\n"
        "- 每操作完一步等待页面稳定（约2秒）后再截图\n"
    )


# ── OfflineExplorer ────────────────────────────────────────────

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
        classifier_api_key: str = "",
        classifier_base_url: str = "https://dashscope.aliyuncs.com/apps/anthropic",
        classifier_model: str = "qwen3-vl-flash",
        verbose: bool = True,
    ):
        self.app_name = app_name
        self.device = device_factory
        self.vlm = model_client
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.max_steps = max_steps
        self.task_description = task_description
        self.verbose = verbose

        # Action handler for executing VLM-decided actions
        self.action_handler = ActionHandler()

        # Page classifier using dedicated fast VLM with cropped screenshots
        self.classifier = PageClassifier(
            api_key=classifier_api_key,
            base_url=classifier_base_url,
            model=classifier_model,
        )

        # Collected data
        self.discovered_pages: Dict[str, PageInfo] = {}  # state_key → PageInfo
        self.trajectories: List[Trajectory] = []
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

        self.device.launch_app(self.app_name)
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
            screenshot = self.device.get_screenshot()
            current_app = self.device.get_current_app()

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
            page_type, summary, elements = self.classifier.classify(
                screenshot.base64_data, screenshot.width, screenshot.height
            )
            page_info = PageInfo(
                page_type=page_type,
                semantic_summary=summary,
                elements=elements,
                screenshot_hash=hashlib.md5(screenshot.base64_data.encode()).hexdigest(),
                app=current_app or self.app_name,
                screenshot_base64=screenshot.base64_data,
                width=screenshot.width,
                height=screenshot.height,
            )
            self._record_page(page_info)
            self._log(f"  [{step_idx+1}] {page_info.page_type.value}: {page_info.semantic_summary[:60]}")

            # ── Record transition from previous step ──
            if prev_page_key is not None and prev_action is not None:
                self._record_transition(prev_page_key, prev_action, page_info.state_key())

            # Check if exploration is complete
            if action.get("_metadata") == "finish":
                traj.add_step(page_info, action, response.thinking)
                self._log(f"  VLM finished: {action.get('message', '')[:100]}")
                break

            # Record step (current page + action about to execute)
            traj.add_step(page_info, action, response.thinking)

            # Update context
            context[-1] = MessageBuilder.remove_images_from_message(context[-1])
            assistant_content = (
                f" thinking{response.thinking} response<answer>{response.action}</answer>"
            )
            context.append(MessageBuilder.create_assistant_message(assistant_content))

            # Execute the action
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

    # ── Helpers ─────────────────────────────────────────────────

    def _record_page(self, page_info: PageInfo):
        """Record a discovered page, deduplicating by state_key."""
        key = page_info.state_key()
        if key not in self.discovered_pages:
            self.discovered_pages[key] = page_info
            if self.verbose:
                self._log(f"    NEW: {page_info.page_type.value}")

    def _record_transition(self, from_key: str, action: Dict[str, Any], to_key: str):
        """Record a page transition: from_page → action → to_page."""
        self.transitions.append({
            "from": from_key,
            "action": action,
            "to": to_key,
        })

    def _build_discovered_summary(self) -> str:
        """Build a summary of discovered pages for the VLM context."""
        if not self.discovered_pages:
            return "尚未发现任何页面。请继续探索。"

        by_type: Dict[str, int] = {}
        for p in self.discovered_pages.values():
            t = p.page_type.value
            by_type[t] = by_type.get(t, 0) + 1

        type_lines = "\n".join(f"  - {t}: {c}个" for t, c in sorted(by_type.items()))
        return f"已发现 {len(self.discovered_pages)} 个页面:\n{type_lines}"

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
        if self.transitions:
            transitions_data = {
                "app": self.app_name,
                "task": self.task_description,
                "total_transitions": len(self.transitions),
                "transitions": self.transitions,
            }
            trans_path = self.storage_dir / f"{self.app_name}_explore_transitions_{timestamp}.json"
            with open(trans_path, "w", encoding="utf-8") as f:
                json.dump(transitions_data, f, ensure_ascii=False, indent=2)
            self._log(f"  saved: {trans_path.name} ({len(self.transitions)} transitions)")

