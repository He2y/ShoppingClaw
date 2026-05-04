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

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

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


class ExplorationTask(Enum):
    """定向探索任务类型 — 每次运行聚焦一个功能板块。

    多次运行不同 task 后，合并所有 JSON 输出即可构建完整的 App 页面路径图谱。
    """
    SEARCH = "search"             # 搜索商品发现流程: 搜索框→结果→商品详情
    CART_CHECKOUT = "cart"       # 购物车和结算流程: 购物车→管理→结算
    CATEGORY_BROWSE = "category"  # 分类浏览: 分类入口→子分类→分类商品列表
    ACCOUNT_SETTINGS = "account"  # 个人中心: 我的→订单→设置
    PRODUCT_DETAIL = "product"    # 商品详情和规格: 详情页→规格选择→店铺
    GENERAL = "general"           # 通用探索 (默认，广度优先)


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


# ── Exploration System Prompt ──────────────────────────────────

# Task-specific strategy sections injected into the system prompt.
# Each defines what to focus on and what to skip for that task direction.
_TASK_STRATEGIES: Dict[str, str] = {
    "search": (
        "=== 本次探索目标：搜索与商品发现 ===\n"
        "你这次只探索搜索相关的流程，其他区域（购物车、我的、分类）本次不要点击。\n\n"
        "具体步骤：\n"
        "1. 在首页找到搜索框，点击激活搜索\n"
        "2. 输入关键词搜索（尝试热门词如\"手机\"\"衣服\"）\n"
        "3. 浏览搜索结果列表，观察筛选/排序选项\n"
        "4. 点击搜索结果中的第一个商品，进入商品详情页\n"
        "5. 在详情页观察：商品标题、价格、规格按钮、加购按钮、店铺入口\n"
        "6. 如有规格选择入口，点击查看规格弹窗\n"
        "7. Back返回搜索结果，再Back返回首页\n"
        "8. 尝试搜索另一个不同的关键词（如\"耳机\"），观察结果差异\n"
        "9. 共探索10-15步后 finish 报告你发现了哪些页面\n"
    ),
    "cart": (
        "=== 本次探索目标：购物车与结算 ===\n"
        "你这次只探索购物车和结算相关流程，其他区域（搜索、分类、我的）本次不要点击。\n\n"
        "具体步骤：\n"
        "1. 从首页点击底部\"购物车\"Tab进入购物车\n"
        "2. 观察购物车页面结构：商品列表、全选按钮、结算按钮、编辑/管理按钮\n"
        "3. 如有商品，尝试点击商品进入详情，再Back回来\n"
        "4. 尝试选中/取消选中某个商品，观察总价变化\n"
        "5. 点击\"结算\"或\"去结算\"按钮（注意：只观察页面，不要真正下单！）\n"
        "6. 如果进入结算页：观察地址选择、支付方式、订单摘要、提交订单按钮\n"
        "7. Back返回购物车，再Back返回首页\n"
        "8. 共探索10-15步后 finish 报告你发现了哪些页面\n"
    ),
    "category": (
        "=== 本次探索目标：分类浏览 ===\n"
        "你这次只探索分类浏览相关的流程，其他区域（搜索、购物车、我的）本次不要点击。\n\n"
        "具体步骤：\n"
        "1. 在首页找到分类入口（可能在顶部导航、中间图标区、或底部Tab）\n"
        "2. 进入分类页面，观察分类层级结构\n"
        "3. 点击一个一级分类（如\"手机数码\"），观察子分类列表\n"
        "4. 继续深入点击一个子分类（如\"手机\"），观察该分类下的商品列表\n"
        "5. 尝试切换不同的同级分类，对比页面结构\n"
        "6. Back逐层返回，回到首页\n"
        "7. 尝试另一个完全不同的分类路径（如\"服装\"→\"男装\"）\n"
        "8. 共探索10-15步后 finish 报告你发现了哪些页面\n"
    ),
    "account": (
        "=== 本次探索目标：个人中心与设置 ===\n"
        "你这次只探索个人中心相关的流程，其他区域（搜索、购物车、分类）本次不要点击。\n\n"
        "具体步骤：\n"
        "1. 从首页点击底部\"我的\"或\"我的淘宝\"/\"我的京东\"Tab\n"
        "2. 如有弹窗（红包/活动），先关闭弹窗\n"
        "3. 观察个人中心页面结构：会员信息、订单入口、优惠券、收藏、足迹等\n"
        "4. 点击\"我的订单\"或\"查看全部订单\"，进入订单列表页\n"
        "5. 尝试切换订单状态Tab（待付款/待发货/待收货/待评价）\n"
        "6. 如有\"设置\"入口，点击进入设置页面\n"
        "7. 在设置页面浏览：账号安全、地址管理、支付设置等入口\n"
        "8. Back逐层返回个人中心，再回到首页\n"
        "9. 共探索10-15步后 finish 报告你发现了哪些页面\n"
    ),
    "product": (
        "=== 本次探索目标：商品详情与规格选择 ===\n"
        "你这次只探索商品详情相关的流程，来源不限（可从搜索或推荐进入）。\n\n"
        "具体步骤：\n"
        "1. 从首页推荐区或搜索结果进入一个商品详情页\n"
        "2. 观察详情页结构：商品图片/视频、价格、优惠信息、规格选择按钮\n"
        "3. 点击\"规格\"或\"参数选择\"按钮，进入规格选择弹窗\n"
        "4. 观察规格弹窗：颜色选项、容量选项、数量选择、价格变化\n"
        "5. 尝试选择不同的规格组合（注意：不要点\"立即购买\"或\"加入购物车\"！）\n"
        "6. 关闭规格弹窗，回到详情页\n"
        "7. 在详情页向下滑动，查看商品评价、详情参数、店铺信息\n"
        "8. 如有\"进入店铺\"入口，点击进入店铺主页\n"
        "9. Back返回详情，再Back返回上一级\n"
        "10. 共探索10-15步后 finish 报告你发现了哪些页面\n"
    ),
    "general": (
        "=== 本次探索目标：广度优先通用探索 ===\n"
        "你这次做广度优先的通用探索，快速覆盖所有主要页面类型。\n"
        "不要在任何单一页面停留太久，目标是最大化发现的页面类型数量。\n\n"
        "节奏：首页(2步)→搜索(3步)→Tab切换(3步)→分类(2步)→我的(2步)→总结\n"
        "共探索12-16步后 finish 报告你发现了哪些页面。\n"
    ),
}

# Page-type priority targets for each task — which page types
# count as "success" and should be prioritized during exploration.
_TASK_TARGET_TYPES: Dict[str, List[ShoppingPageType]] = {
    "search": [
        ShoppingPageType.SEARCH_INPUT, ShoppingPageType.SEARCH_RESULT,
        ShoppingPageType.PRODUCT_DETAIL, ShoppingPageType.SPEC_SELECTION,
    ],
    "cart": [
        ShoppingPageType.CART, ShoppingPageType.CHECKOUT,
        ShoppingPageType.PRODUCT_DETAIL, ShoppingPageType.SPEC_SELECTION,
    ],
    "category": [
        ShoppingPageType.CATEGORY, ShoppingPageType.SEARCH_RESULT,
        ShoppingPageType.PRODUCT_DETAIL,
    ],
    "account": [
        ShoppingPageType.MY_ACCOUNT, ShoppingPageType.LOGIN,
        ShoppingPageType.CHECKOUT,
    ],
    "product": [
        ShoppingPageType.PRODUCT_DETAIL, ShoppingPageType.SPEC_SELECTION,
        ShoppingPageType.STORE,
    ],
    "general": [
        ShoppingPageType.HOME, ShoppingPageType.SEARCH_INPUT,
        ShoppingPageType.SEARCH_RESULT, ShoppingPageType.PRODUCT_DETAIL,
        ShoppingPageType.CART, ShoppingPageType.CATEGORY,
        ShoppingPageType.MY_ACCOUNT,
    ],
}

# Task descriptions shown in CLI help and output
_TASK_DESCRIPTIONS: Dict[str, str] = {
    "search": "搜索商品发现 — 搜索框→关键词→搜索结果→商品详情",
    "cart": "购物车结算 — 购物车→商品管理→结算页",
    "category": "分类浏览 — 分类入口→子分类→分类商品列表",
    "account": "个人中心 — 我的页面→订单→设置",
    "product": "商品详情 — 详情页→规格选择→店铺主页",
    "general": "通用探索 — 广度优先覆盖所有主要页面",
}


def _build_exploration_system_prompt(task: ExplorationTask = ExplorationTask.GENERAL) -> str:
    """Build task-directed exploration system prompt for AutoGLM VLM."""
    today = datetime.today()
    weekday_names = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    weekday = weekday_names[today.weekday()]
    formatted_date = today.strftime("%Y年%m月%d日") + " " + weekday

    task_strategy = _TASK_STRATEGIES.get(task.value, _TASK_STRATEGIES["general"])

    return (
        "今天的日期是: " + formatted_date + "\n"
        "你是一个移动应用探索智能体，专门定向探索购物App的特定功能板块。\n"
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
        "你是App定向探索者。系统已启动目标App，你需要聚焦本次指定的任务方向，\n"
        "系统性地探索该方向涉及的所有页面类型和页面跳转关系。\n"
        "把自己想象成测试工程师在做特定模块的探索性测试。\n\n"

        + task_strategy + "\n"

        "=== 重要约束 ===\n"
        "- 不需要登录，遇到登录界面请Back\n"
        "- 不要下单购买任何商品（可以进入结算页观察，但不要提交订单）\n"
        "- 不要修改任何个人信息\n"
        "- 遇到广告弹窗点X关闭或用Back跳过\n"
        "- 聚焦本次任务方向，不要跳到其他板块\n"
        "- 每操作完一步等待页面稳定（约2秒）后再截图\n"
    )


# ── OfflineExplorer ────────────────────────────────────────────

class OfflineExplorer:
    """购物场景离线探索器 — 任务导向型

    每次运行聚焦一个功能板块（ExplorationTask），多次运行不同 task 后
    合并所有 JSON 输出即可构建完整的 App 页面路径图谱。

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
        exploration_task: ExplorationTask = ExplorationTask.GENERAL,
        verbose: bool = True,
    ):
        self.app_name = app_name
        self.device = device_factory
        self.vlm = model_client
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.max_steps = max_steps
        self.exploration_task = exploration_task
        self.verbose = verbose

        # Action handler for executing VLM-decided actions
        self.action_handler = ActionHandler()

        # Collected data
        self.discovered_pages: Dict[str, PageInfo] = {}  # state_key → PageInfo
        self.trajectories: List[Trajectory] = []
        self.transitions: List[Dict[str, Any]] = []  # from → action → to

    # ── Top-Level Entry ────────────────────────────────────────

    def explore(self) -> List[Trajectory]:
        """Run task-directed VLM exploration of the target app.

        Returns:
            List of exploration trajectories.
        """
        task_desc = _TASK_DESCRIPTIONS.get(self.exploration_task.value, "探索")
        self._log(f"\n{'='*60}")
        self._log(f"  Offline Explorer: {self.app_name}")
        self._log(f"  Task: {self.exploration_task.value} — {task_desc}")
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

        # Save results with task label in filename
        self._save_results(traj)

        target_types = _TASK_TARGET_TYPES.get(self.exploration_task.value, [])
        target_names = {t.value for t in target_types}
        found_targets = {
            k: v for k, v in self.discovered_pages.items()
            if v.page_type.value in target_names
        }

        self._log(f"\n{'='*60}")
        self._log(f"  Done: {len(self.discovered_pages)} unique pages discovered")
        self._log(f"  Task-relevant: {len(found_targets)} pages")
        self._log(f"  {len(traj.steps)} exploration steps taken")
        self._log(f"{'='*60}\n")
        return self.trajectories

    # ── VLM-Driven Exploration Loop ────────────────────────────

    # Keywords for extracting page type from VLM thinking text.
    # Each page type maps to a list of Chinese phrase patterns the VLM
    # naturally uses when describing what it sees.
    _PAGE_TYPE_PATTERNS: Dict[ShoppingPageType, List[str]] = {
        ShoppingPageType.HOME: [
            r"首页", r"主页", r"主屏幕",
        ],
        ShoppingPageType.SEARCH_INPUT: [
            r"搜索输入", r"搜索框.*激活", r"键盘.*弹出", r"搜索页面",
            r"输入.*搜索", r"ADB Keyboard",
        ],
        ShoppingPageType.SEARCH_RESULT: [
            r"搜索结果.*商品", r"搜索结果页", r"商品列表",
            r"多个商品卡片", r"搜索结果列表",
        ],
        ShoppingPageType.PRODUCT_DETAIL: [
            r"商品详情", r"详情页", r"单品购买",
            r"加入购物车.*按钮", r"立即购买.*按钮",
            r"规格.*选择|产品详情",
        ],
        ShoppingPageType.SPEC_SELECTION: [
            r"规格选择", r"颜色.*容量.*选项", r"弹窗.*规格", r"选择.*规格",
            r"可选规格", r"SKU",
        ],
        ShoppingPageType.CART: [
            r"购物车页面", r"购物车.*商品", r"进入了?购物车",
            r"在购物车[^按]", r"购物车\(\d+\)",
        ],
        ShoppingPageType.CHECKOUT: [
            r"结算", r"订单确认", r"提交订单", r"收货地址", r"支付方式",
        ],
        ShoppingPageType.CATEGORY: [
            r"分类浏览", r"分类页", r"品类", r"商品分类",
        ],
        ShoppingPageType.MY_ACCOUNT: [
            r"我的页面", r"我的淘宝", r"我的京东",
            r"个人中心", r"账号", r"会员中心",
            r"我的订单", r"设置.*页", r"我的\b",
        ],
        ShoppingPageType.STORE: [
            r"店铺主页", r"店铺首页", r"旗舰店", r"店铺.*页",
        ],
        ShoppingPageType.LOGIN: [
            r"登录", r"注册", r"验证码", r"密码.*输入",
        ],
    }

    # Order matters: check more specific patterns first.
    # Cart is intentionally low — the VLM mentions "购物车" in nav bar
    # descriptions and action plans, so it's a very noisy keyword.
    _PRIORITY_ORDER: List[ShoppingPageType] = [
        ShoppingPageType.SPEC_SELECTION,
        ShoppingPageType.LOGIN,
        ShoppingPageType.CHECKOUT,
        ShoppingPageType.PRODUCT_DETAIL,
        ShoppingPageType.SEARCH_RESULT,
        ShoppingPageType.SEARCH_INPUT,
        ShoppingPageType.CATEGORY,
        ShoppingPageType.STORE,
        ShoppingPageType.MY_ACCOUNT,
        ShoppingPageType.HOME,
        ShoppingPageType.CART,       # Lowest: "购物车" keyword is too common in nav bar
    ]

    def _exploration_loop(self) -> Trajectory:
        """Run the task-directed closed-loop VLM exploration.

        Loop: screenshot → VLM decides action (thinking describes page)
              → extract page info from thinking → execute action → repeat.

        Page type is parsed from the exploration VLM's own thinking text.
        """
        task_label = self.exploration_task.value
        task_desc = _TASK_DESCRIPTIONS.get(task_label, "探索")
        traj = Trajectory(
            task=f"{task_label}: {task_desc} — {self.app_name}",
            app=self.app_name,
        )
        context: List[Dict[str, Any]] = []

        # Build task-directed system prompt
        system_prompt = _build_exploration_system_prompt(self.exploration_task)
        context.append(MessageBuilder.create_system_message(system_prompt))

        for step_idx in range(self.max_steps):
            # Capture current screen
            screenshot = self.device.get_screenshot()
            current_app = self.device.get_current_app()

            # Build user message for this step
            screen_info = MessageBuilder.build_screen_info(current_app)
            discovered_summary = self._build_discovered_summary()

            if step_idx == 0:
                task_text = (
                    f"【本次任务】{task_desc}\n"
                    f"开始探索{self.app_name}。你已经在该App中。\n"
                    f"请聚焦本次任务方向，不要跳到无关板块。\n\n"
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

            # ── Extract page info from VLM's thinking ──
            page_info = self._extract_page_info_from_thinking(
                response.thinking, current_app or self.app_name, screenshot
            )
            self._record_page(page_info)
            self._log(f"  [{step_idx+1}] {page_info.page_type.value}: {page_info.semantic_summary[:60]}")

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
                continue

            # Brief pause for UI to settle
            time.sleep(2)

        else:
            # Max steps reached
            self._log(f"  Max steps ({self.max_steps}) reached")
            traj.success = True

        return traj

    # ── Page Info Extraction from Thinking ─────────────────────

    def _extract_page_info_from_thinking(
        self, thinking: str, current_app: str, screenshot: Any
    ) -> PageInfo:
        """Extract page type and summary from VLM's thinking text.

        The exploration VLM naturally describes the current page in its thinking
        (e.g. "现在进入了搜索结果页面", "我在购物车页面").  We match these
        descriptions against keyword patterns — no extra VLM calls needed.
        """
        page_type = self._match_page_type(thinking)
        summary = self._build_thinking_summary(page_type, thinking, current_app)

        return PageInfo(
            page_type=page_type,
            semantic_summary=summary,
            elements={},  # Elements are extracted in post-processing if needed
            screenshot_hash=hashlib.md5(screenshot.base64_data.encode()).hexdigest(),
            app=current_app,
            screenshot_base64=screenshot.base64_data,
            width=screenshot.width,
            height=screenshot.height,
        )

    def _match_page_type(self, thinking: str) -> ShoppingPageType:
        """Match thinking text against page type keyword patterns.

        Checks patterns in priority order — more specific types first.
        """
        cleaned = self._strip_nav_bar(thinking)
        cleaned_lower = cleaned.lower()

        for pt in self._PRIORITY_ORDER:
            patterns = self._PAGE_TYPE_PATTERNS.get(pt, [])
            for pattern in patterns:
                if re.search(pattern, cleaned_lower):
                    return pt

        return ShoppingPageType.UNKNOWN

    @staticmethod
    def _strip_nav_bar(text: str) -> str:
        """Remove bottom-nav-bar and action-intent descriptions from thinking.

        The VLM frequently describes the bottom nav bar ('底部有导航栏：首页、
        购物车、我的...') and action plans ('我需要点击购物车按钮') as part of
        its reasoning. These contain keywords for multiple page types and cause
        false matches.

        We strip these lines before matching so only actual current-page
        descriptions remain.
        """
        # Remove nav bar descriptions
        nav_line_patterns = [
            r'底部.{0,5}(?:导航栏|导航|Tab|标签栏).{0,40}(?:首页|消息|购物车|我的)',
            r'底部有.*(?:首页|消息|购物车|我的).*(?:首页|消息|购物车|我的)',
            r'(?:底部|下方).*(?:导航按钮|导航图标|Tab按钮)',
        ]
        for pat in nav_line_patterns:
            text = re.sub(pat, '', text)

        # Remove action-intent lines (describing what to do next, not current page)
        action_intent_patterns = [
            r'(?:需要|可以|尝试|打算|准备|想|要|让我|我要|我来).{0,20}(?:点击|进入|打开|查看|跳转).{0,30}(?:购物车|首页|分类|详情|结算|消息|我的)',
        ]
        for pat in action_intent_patterns:
            text = re.sub(pat, '', text)

        return text

    def _build_thinking_summary(
        self, page_type: ShoppingPageType, thinking: str, current_app: str
    ) -> str:
        """Build a page summary from thinking text."""
        # Extract the first meaningful sentence about the current page
        # Look for lines that describe the page state
        page_clues = [
            r'(?:进入|回到|在|到了|打开)[了]?[^\n。]{0,30}(?:页面|界面|页|屏幕)',
            r'(?:当前|现在|目前)[^\n。]{0,30}(?:页面|界面|显示)',
        ]

        for clue_pattern in page_clues:
            m = re.search(clue_pattern, thinking)
            if m:
                snippet = m.group(0).strip()
                if len(snippet) > 4:
                    return snippet[:80]

        # Fallback
        if page_type == ShoppingPageType.UNKNOWN:
            return f"{current_app} - 未知页面"
        return f"{current_app} - {page_type.value}"

    # ── Helpers ─────────────────────────────────────────────────

    def _record_page(self, page_info: PageInfo):
        """Record a discovered page, deduplicating by state_key."""
        key = page_info.state_key()
        if key not in self.discovered_pages:
            self.discovered_pages[key] = page_info
            if self.verbose:
                self._log(f"    NEW: {page_info.page_type.value}")

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
            "task": self.exploration_task.value,
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
        pages_path = self.storage_dir / f"{self.app_name}_{self.exploration_task.value}_pages_{timestamp}.json"
        with open(pages_path, "w", encoding="utf-8") as f:
            json.dump(pages_data, f, ensure_ascii=False, indent=2)
        self._log(f"  saved: {pages_path.name}")

        # Save trajectory
        traj_data = {
            "task": traj.task,
            "task_type": self.exploration_task.value,
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
        traj_path = self.storage_dir / f"{self.app_name}_{self.exploration_task.value}_trajectory_{timestamp}.json"
        with open(traj_path, "w", encoding="utf-8") as f:
            json.dump(traj_data, f, ensure_ascii=False, indent=2)
        self._log(f"  saved: {traj_path.name}")

