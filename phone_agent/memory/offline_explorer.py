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
from phone_agent.model.client import MessageBuilder, ModelClient, ModelResponse


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


# ── Exploration System Prompt ──────────────────────────────────

def _build_exploration_system_prompt() -> str:
    """Build the exploration-specific system prompt for AutoGLM VLM."""
    today = datetime.today()
    weekday_names = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    weekday = weekday_names[today.weekday()]
    formatted_date = today.strftime("%Y年%m月%d日") + " " + weekday

    return (
        "今天的日期是: " + formatted_date + "\n"
        "你是一个移动应用探索智能体，专门探索购物App的页面结构。\n"
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
        '- do(action="Home")\n'
        "    Home是回到系统桌面的操作。\n"
        '- finish(message="xxx")\n'
        "    finish是结束探索任务的操作，message中总结你发现了哪些页面类型。\n\n"

        "=== 你的任务 ===\n"
        "你是App探索者。系统会自动启动目标App，你需要在该App内自由探索，\n"
        "发现所有的页面类型和功能入口。把自己想象成一个测试工程师在做探索性测试。\n\n"

        "=== 探索策略 ===\n"
        "1. 首页探索：识别搜索框、底部Tab（首页/分类/购物车/我的）、推荐位、顶部导航\n"
        "2. 搜索流程：点击搜索框→输入关键词（如'手机'）→查看搜索结果→点击一个商品→进入详情\n"
        "3. Tab切换：点击不同的底部Tab（分类、购物车、我的），探索每个Tab\n"
        "4. 品类浏览：如果有分类入口，点击进入查看\n"
        "5. 商品详情：在搜索结果或首页点一个商品，进入详情页查看\n"
        "6. 用Back返回：每深入2-3层就Back返回，确保不迷失\n"
        "7. 遇到弹窗：如果是广告弹窗，点X关闭或用Back；如果是登录弹窗，用Back跳过\n\n"

        "=== 重要约束 ===\n"
        "- 这是一次性探索，不需要登录，遇到登录界面请Back\n"
        "- 不要下单购买任何商品\n"
        "- 不要修改任何个人信息\n"
        "- 每个操作前思考：这个操作能帮我发现新页面吗？\n"
        "- 避免重复点击已经看过的相同入口\n"
        "- 探索12-18步后使用finish(message='探索完成，发现了: ...')结束\n\n"

        "=== 探索节奏建议 ===\n"
        "步骤1-3: 观察首页结构，识别所有入口\n"
        "步骤4-7: 尝试搜索+查看商品详情\n"
        "步骤8-11: 切换Tab探索其他区域\n"
        "步骤12-15: 探索感兴趣的其他入口\n"
        "步骤16+: 总结并用finish结束\n"
    )


# ── Page Classification Prompt ─────────────────────────────────

PAGE_CLASSIFY_PROMPT_TEMPLATE = """你是一个页面分类器。分析这个{app}的截图，判断它属于哪种页面类型。

页面类型定义：
- home: 首页/主页，有搜索框、多个商品推荐、分类入口、底部导航Tab
- search_input: 搜索输入页，搜索框已激活获得焦点，可能键盘弹出
- search_result: 搜索结果列表页，显示多个商品卡片
- product_detail: 商品详情页，显示单个商品的大图、价格、规格按钮、加入购物车/立即购买按钮
- spec_selection: 规格选择弹窗，显示颜色/尺寸/容量等选项供用户选择
- cart: 购物车页面，显示已添加的商品列表，有结算按钮
- checkout: 结算/订单确认页面，显示收货地址、支付方式、提交订单按钮
- category: 商品分类浏览页，显示分类列表或某个分类下的商品
- my_account: 个人中心/我的页面，显示用户头像、订单入口、设置等
- store: 店铺主页，显示某个店铺的首页
- login: 登录/注册页面
- unknown: 无法识别

请直接输出 finish(message="类型名称")，不要输出其他内容。例如：finish(message="home")\n"""


# ── OfflineExplorer ────────────────────────────────────────────

class OfflineExplorer:
    """购物场景离线探索器

    VLM-autonomous exploration loop:
    1. Launch app
    2. Screenshot → VLM decides action → Execute → Analyze result → Record → Repeat
    3. VLM can finish() early when exploration is complete
    """

    def __init__(
        self,
        app_name: str,
        device_factory: DeviceFactory,
        model_client: ModelClient,
        storage_dir: str = "memory_db/exploration",
        max_steps: int = 20,
        verbose: bool = True,
    ):
        self.app_name = app_name
        self.device = device_factory
        self.vlm = model_client
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.max_steps = max_steps
        self.verbose = verbose

        # Action handler for executing VLM-decided actions
        self.action_handler = ActionHandler()

        # Collected data
        self.discovered_pages: Dict[str, PageInfo] = {}  # state_key → PageInfo
        self.trajectories: List[Trajectory] = []
        self.transitions: List[Dict[str, Any]] = []  # from → action → to

    # ── Top-Level Entry ────────────────────────────────────────

    def explore(self) -> List[Trajectory]:
        """Run VLM-autonomous exploration of the target app.

        Returns:
            List of exploration trajectories.
        """
        self._log(f"\n{'='*60}")
        self._log(f"  Offline Explorer: {self.app_name}")
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
        """Run the closed-loop VLM exploration.

        Loop: screenshot → classify page → ask VLM for next action → execute → repeat.
        """
        traj = Trajectory(task=f"探索{self.app_name}", app=self.app_name)
        context: List[Dict[str, Any]] = []

        # Build system prompt (sent once)
        system_prompt = _build_exploration_system_prompt()
        context.append(MessageBuilder.create_system_message(system_prompt))

        for step_idx in range(self.max_steps):
            # Capture current screen
            screenshot = self.device.get_screenshot()
            current_app = self.device.get_current_app()

            # Classify the current page (before VLM decides next action)
            page_info = self._classify_current_page(screenshot, current_app)
            self._record_page(page_info)
            self._log(f"  [{step_idx+1}] {page_info.page_type.value}: {page_info.semantic_summary[:60]}")

            # Build user message for this step
            screen_info = MessageBuilder.build_screen_info(current_app)
            discovered_summary = self._build_discovered_summary()

            if step_idx == 0:
                # First step: include the exploration task
                task_text = (
                    f"开始探索{self.app_name}。你已经在该App中。\n\n"
                    f"{discovered_summary}\n\n"
                    f"{screen_info}"
                )
            else:
                task_text = (
                    f"{discovered_summary}\n\n"
                    f"{screen_info}"
                )

            context.append(MessageBuilder.create_user_message(
                text=task_text, image_base64=screenshot.base64_data
            ))

            # Get VLM's decision for next action
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

            # Check if exploration is complete
            if action.get("_metadata") == "finish":
                traj.add_step(page_info, action, response.thinking)
                self._log(f"  VLM finished: {action.get('message', '')[:100]}")
                break

            # Record step (current page + action about to execute)
            traj.add_step(page_info, action, response.thinking)

            # Update context
            context[-1] = MessageBuilder.remove_images_from_message(context[-1])
            assistant_content = f" thinking{response.thinking} response<answer>{response.action}</answer>"
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
            traj.success = True  # Still a valid exploration

        return traj

    # ── Page Classification ────────────────────────────────────

    def _classify_current_page(self, screenshot: Any, current_app: str) -> PageInfo:
        """Use a focused VLM call to classify the current page by type.

        This is a separate, stateless call — not part of the exploration conversation.
        """
        prompt = PAGE_CLASSIFY_PROMPT_TEMPLATE.format(app=current_app or self.app_name)

        messages = [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot.base64_data}"}},
                {"type": "text", "text": prompt},
            ],
        }]

        try:
            response = self.vlm.request(messages)
        except Exception:
            return PageInfo(
                page_type=ShoppingPageType.UNKNOWN,
                semantic_summary="分类失败",
                elements={},
                screenshot_hash=hashlib.md5(screenshot.base64_data.encode()).hexdigest(),
                app=current_app,
                screenshot_base64=screenshot.base64_data,
                width=screenshot.width,
                height=screenshot.height,
            )

        page_type = self._parse_classify_response(response)
        elements = self._extract_elements_for_type(screenshot, page_type)
        summary = self._build_summary(page_type, elements, current_app)

        return PageInfo(
            page_type=page_type,
            semantic_summary=summary,
            elements=elements,
            screenshot_hash=hashlib.md5(screenshot.base64_data.encode()).hexdigest(),
            app=current_app,
            screenshot_base64=screenshot.base64_data,
            width=screenshot.width,
            height=screenshot.height,
        )

    def _parse_classify_response(self, response: ModelResponse) -> ShoppingPageType:
        """Extract page type from finish(message="...") response."""
        text = (response.action or response.raw_content or "").strip().lower()

        # Try finish(message="type_name")
        m = re.search(r'finish\(message="([^"]*)"\)', text)
        if not m:
            m = re.search(r"finish\(message='([^']*)'\)", text)

        if m:
            type_str = m.group(1).strip().lower()
            for pt in ShoppingPageType:
                if pt.value == type_str or pt.value in type_str:
                    return pt

        # Fallback: keyword search in full response
        full = (response.thinking + " " + text).lower()
        for pt in ShoppingPageType:
            if pt.value in full:
                return pt

        return ShoppingPageType.UNKNOWN

    def _extract_elements_for_type(
        self, screenshot: Any, page_type: ShoppingPageType
    ) -> Dict[str, Any]:
        """Extract key UI elements based on page type.

        Uses a separate, focused VLM call for element extraction.
        Only for page types where structured element data is useful.
        """
        prompts = {
            ShoppingPageType.HOME: (
                "识别这个首页中所有可交互的关键元素。\n"
                "返回JSON格式:\n"
                '{"search_box": {"center_x": 0, "center_y": 0, "text": ""},'
                '"tab_buttons": [{"name": "", "center_x": 0, "center_y": 0}],'
                '"category_icons": [{"name": "", "center_x": 0, "center_y": 0}],'
                '"product_cards": [{"title": "", "center_x": 0, "center_y": 0}]}\n'
                "使用1-1000的坐标系统。输出 finish(message='{...JSON...}') 格式。"
            ),
            ShoppingPageType.SEARCH_RESULT: (
                "识别搜索结果页的商品卡片列表。\n"
                "返回JSON格式:\n"
                '{"product_cards":[{"index":0,"title":"","price":"","center_x":0,"center_y":0}]}\n'
                "使用1-1000的坐标系统。输出 finish(message='{...JSON...}') 格式。"
            ),
            ShoppingPageType.PRODUCT_DETAIL: (
                "识别商品详情页的关键交互元素。\n"
                "返回JSON格式:\n"
                '{"product_title": {"text": ""},'
                '"price": {"text": ""},'
                '"spec_button": {"text": "", "center_x": 0, "center_y": 0},'
                '"add_to_cart_button": {"center_x": 0, "center_y": 0},'
                '"buy_now_button": {"center_x": 0, "center_y": 0},'
                '"store_link": {"center_x": 0, "center_y": 0}}\n'
                "使用1-1000的坐标系统。输出 finish(message='{...JSON...}') 格式。"
            ),
            ShoppingPageType.CART: (
                "识别购物车页面的关键元素。\n"
                "返回JSON格式:\n"
                '{"cart_items": [{"title": "", "price": "", "center_x": 0, "center_y": 0}],'
                '"select_all_button": {"center_x": 0, "center_y": 0},'
                '"checkout_button": {"center_x": 0, "center_y": 0}}\n'
                "使用1-1000的坐标系统。输出 finish(message='{...JSON...}') 格式。"
            ),
        }

        prompt = prompts.get(page_type)
        if not prompt:
            return {}

        messages = [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot.base64_data}"}},
                {"type": "text", "text": prompt},
            ],
        }]

        try:
            response = self.vlm.request(messages)
            return self._parse_json_from_response(response)
        except Exception:
            return {}

    def _parse_json_from_response(self, response: ModelResponse) -> Dict[str, Any]:
        """Parse JSON from a finish(message='{...}') response."""
        text = response.action or response.raw_content or ""

        # Handle single-quote wrapped JSON (AutoGLM common pattern)
        for pattern in [r"finish\(message='(\{.*?\})'\)", r'finish\(message="(\{.*?\})"\)']:
            m = re.search(pattern, text, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(1))
                except json.JSONDecodeError:
                    pass

        # Fallback: find any JSON block
        start = text.find('{')
        end = text.rfind('}') + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass

        return {}

    def _build_summary(
        self, page_type: ShoppingPageType, elements: Dict, current_app: str
    ) -> str:
        """Build a human-readable summary of the page."""
        if page_type == ShoppingPageType.HOME:
            tabs = elements.get("tab_buttons", [])
            tab_names = ", ".join(t.get("name", "") for t in tabs[:4])
            return f"{current_app}首页" + (f" (Tab: {tab_names})" if tab_names else "")
        elif page_type == ShoppingPageType.SEARCH_RESULT:
            n = len(elements.get("product_cards", []))
            return f"搜索结果页({n}个商品)"
        elif page_type == ShoppingPageType.PRODUCT_DETAIL:
            title = elements.get("product_title", {}).get("text", "")
            price = elements.get("price", {}).get("text", "")
            return f"商品详情: {title[:30]}" + (f" ¥{price}" if price else "")
        elif page_type == ShoppingPageType.CART:
            n = len(elements.get("cart_items", []))
            return f"购物车({n}件商品)"
        elif page_type == ShoppingPageType.CATEGORY:
            return "分类浏览页"
        elif page_type == ShoppingPageType.CHECKOUT:
            return "结算/订单确认页"
        elif page_type == ShoppingPageType.SPEC_SELECTION:
            return "规格选择弹窗"
        elif page_type == ShoppingPageType.MY_ACCOUNT:
            return "个人中心页"
        elif page_type == ShoppingPageType.LOGIN:
            return "登录页面"
        else:
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
        pages_path = self.storage_dir / f"{self.app_name}_pages_{timestamp}.json"
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
        traj_path = self.storage_dir / f"{self.app_name}_trajectory_{timestamp}.json"
        with open(traj_path, "w", encoding="utf-8") as f:
            json.dump(traj_data, f, ensure_ascii=False, indent=2)
        self._log(f"  saved: {traj_path.name}")

    # Keep the original search_flow for backward compatibility
    def explore_shopping_flows(self) -> List[Trajectory]:
        """Legacy entry point — delegates to new explore()."""
        return self.explore()
