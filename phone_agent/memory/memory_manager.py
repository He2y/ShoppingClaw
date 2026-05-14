"""
Memory Manager - High-level interface for agent memory operations.

Handles automatic extraction of memories from conversations,
context enrichment, and integration with the agent loop.
"""

import json
import re
from datetime import datetime
from typing import Any

from .memory_store import MemoryStore, Memory, MemoryType
from .core import UnifiedSessionState, Product, ProductStatus
from .retrieval_gateway import RetrievalGateway, RetrievalResult


# Patterns for extracting user preferences (non-shopping: contacts, apps)
PREFERENCE_PATTERNS = {
    "contact": [
        # 改进：更精确的联系人提取，限制长度，排除动词
        r"(?:给|发送?给?|联系|打电话给?|发消息给?)\s*[「『""]?([\u4e00-\u9fa5a-zA-Z]{2,8})[」』""]?(?:发|说|打|$)",
        r"(?:联系人|好友|朋友)\s*[「『""]?([\u4e00-\u9fa5a-zA-Z]{2,8})[」』""]?",
        r"(?:to|contact|call|message)\s+([a-zA-Z\u4e00-\u9fa5]{2,15})(?:\s|$)",
    ],
    "app": [
        r"(打开|启动|使用|进入)[\s]*([\u4e00-\u9fa5a-zA-Z]+)",
        r"(open|launch|use)[\s]+([a-zA-Z\u4e00-\u9fa5]+)",
    ],
    "time_preference": [
        r"(每天|每周|每月|通常|一般)[\s]*([\u4e00-\u9fa5a-zA-Z]+)",
        r"(usually|always|often)[\s]+([a-zA-Z\u4e00-\u9fa5]+)",
    ],
}

# Common apps to recognize
KNOWN_APPS = {
    "微信", "wechat", "支付宝", "alipay", "淘宝", "taobao", "抖音", "tiktok",
    "美团", "meituan", "饿了么", "eleme", "京东", "jd", "拼多多", "pinduoduo",
    "高德地图", "amap", "百度地图", "baidu maps", "微博", "weibo", "qq",
    "钉钉", "dingtalk", "飞书", "feishu", "网易云音乐", "netease music",
    "spotify", "bilibili", "b站", "小红书", "xiaohongshu", "safari", "chrome",
    "设置", "settings", "相机", "camera", "相册", "photos", "备忘录", "notes",
}


class MemoryManager:
    """
    High-level memory management for the phone agent.
    
    Responsibilities:
    - Extract memories from user inputs and agent outputs
    - Provide relevant context for new tasks
    - Track user preferences and habits
    - Learn from successful task completions
    - Automatically learn from agent's thinking process
    """
    
    def __init__(
        self,
        storage_dir: str = "memory_db",
        user_id: str = "default",
        enable_auto_extract: bool = True,
        enable_thinking_analysis: bool = True,
    ):
        """
        Initialize memory manager.

        Args:
            storage_dir: Base directory for memory storage
            user_id: User identifier for personalization
            enable_auto_extract: Auto-extract memories from conversations
            enable_thinking_analysis: Auto-learn from agent's thinking process
        """
        self.user_id = user_id
        self.enable_auto_extract = enable_auto_extract
        self.enable_thinking_analysis = enable_thinking_analysis

        # Create user-specific storage
        user_storage = f"{storage_dir}/{user_id}"
        self.store = MemoryStore(storage_dir=user_storage)

        # Initialize GraphStore (Spatial Memory)
        from .graph_store import GraphStore
        self.graph_store = GraphStore()

        # Initialize UnifiedSessionState — single source of truth
        # (replaces StateManager + SessionMemory + KnowledgeBase)
        self.state = UnifiedSessionState()

        # Initialize RetrievalGateway — on-demand memory retrieval
        self.retrieval_gateway = RetrievalGateway(self.state)

        # Session history for context
        self.session_history: list[dict] = []

        # Current task context
        self.current_task: str = ""
        self.task_start_time: str = ""

        # Track extracted info in current session to avoid duplicates
        self._session_contacts: set[str] = set()
        self._session_apps: set[str] = set()

    def start_task(self, task: str, start_state_id: str | None = None):
        """Called when a new task begins.

        Args:
            task: 任��描述
            start_state_id: 任务开始��的状���ID
        """
        self.current_task = task
        self.task_start_time = datetime.now().isoformat()
        self.session_history.clear()
        self._task_start_state_id = start_state_id
        self._current_state_id = start_state_id

        # Reset session tracking
        self._session_contacts.clear()
        self._session_apps.clear()

        # Reset unified state for new task
        platform = self._detect_platform(task)
        self.state.reset(task=task, platform=platform)
        self.retrieval_gateway.reset()

        # Initialize state tracking
        if start_state_id:
            self.state.start_task_state(start_state_id)

        if self.enable_auto_extract:
            self._extract_from_task(task)
    
    def end_task(self, success: bool, result: str = "", end_state_id: str | None = None):
        """Called when a task completes."""
        if self.current_task:
            # Record task history with success/failure info
            importance = 0.6 if success else 0.4

            self.store.add(
                content=f"任务: {self.current_task} | 结果: {result} | {'成功' if success else '失败'}",
                memory_type=MemoryType.TASK_HISTORY,
                metadata={
                    "task": self.current_task,
                    "result": result,
                    "success": success,
                    "duration": self._calculate_duration(),
                    "steps": len(self.session_history),
                    "apps_used": list(self._session_apps),
                    "contacts_mentioned": list(self._session_contacts),
                    "session_state": self.state.to_dict(),
                },
                importance=importance,
            )

            # If task was successful, learn patterns (写入 FAISS，无需审核)
            if success and len(self.session_history) > 0:
                self._learn_successful_pattern()

        # 持久化本次轨迹到待审核文件，供人工审核后提交
        if self.session_history:
            self._save_pending_trajectory(
                task=self.current_task,
                success=success,
                result=result,
                steps=self.session_history,
                apps=list(self._session_apps),
                start_state=self._task_start_state_id,
                end_state=end_state_id or self._current_state_id,
            )

        self.current_task = ""
        self.task_start_time = ""

    def update_state_and_transition(
        self,
        screenshot_hash: str,
        semantic_layout: str,
        action: dict,
        task: str
    ) -> str:
        """���一的��态更新 + 图转���记录接口

        这是 agent.py 应该调用��唯一���态管��方法���封装了：
        1. 计算新状��ID
        2. ���新 StateManager
        3. 记���图转��到 GraphStore

        Args:
            screenshot_hash: 截图哈希��
            semantic_layout: 语义布��描述
            action: 执行的动��
            task: 当前��务描���

        Returns:
            新��状态ID
        """
        # 计算新状��ID
        new_state_id = self.state.compute_state_id(screenshot_hash, semantic_layout)

        # 更新状态��理器
        prev_state, current_state = self.state.update_state(new_state_id)

        # 记录图��换（只有��有前���状态时才记��）
        if prev_state and self.graph_store.driver:
            self.graph_store.add_state_transition(
                prev_state, current_state, action, task
            )

        return current_state

    def get_current_state_id(self) -> str | None:
        """获取当��状态ID"""
        return self.state.get_current_state()

    def _save_pending_trajectory(self, task: str, success: bool, result: str,
                                 steps: list, apps: list, start_state: str | None,
                                 end_state: str | None):
        """Save completed trajectory to pending file for manual review."""
        import json
        from pathlib import Path

        pending_file = Path(self.store.storage_dir) / "pending_trajectories.json"
        pending_file.parent.mkdir(parents=True, exist_ok=True)

        existing = []
        if pending_file.exists():
            try:
                with open(pending_file, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except Exception:
                existing = []

        entry = {
            "task": task,
            "success": success,
            "result": result,
            "apps": apps,
            "steps": len(steps),
            "step_details": [
                {
                    "action_type": s.get("action", {}).get("action", "unknown"),
                    "action_params": {k: v for k, v in s.get("action", {}).items()
                                     if k not in ("action", "_metadata")},
                    "thinking": s.get("thinking", "")[:200],
                }
                for s in steps
            ],
            "start_state_id": start_state,
            "end_state_id": end_state,
            "saved_at": datetime.now().isoformat(),
        }
        existing.insert(0, entry)  # newest first
        existing = existing[:20]   # keep last 20

        try:
            with open(pending_file, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
            if success:
                print(f"📝 轨迹已保存至 pending_trajectories.json（共 {len(steps)} 步）")
        except Exception as e:
            print(f"Warning: failed to save pending trajectory: {e}")

    def commit_pending(self, index: int = 0) -> bool:
        """
        Commit a pending trajectory from the pending file to Neo4j.
        Call with index=0 (default) for the most recent one.
        """
        import json
        from pathlib import Path

        pending_file = Path(self.store.storage_dir) / "pending_trajectories.json"
        if not pending_file.exists():
            print("No pending trajectories found.")
            return False

        with open(pending_file, "r", encoding="utf-8") as f:
            pending = json.load(f)

        if index >= len(pending):
            print(f"No trajectory at index {index}.")
            return False

        entry = pending[index]
        if not entry.get("success"):
            print("Skipping failed trajectory.")
            return False

        app = entry.get("apps", ["UnknownApp"])[0] if entry.get("apps") else "UnknownApp"
        ok = self.graph_store.commit_task_trajectory(
            task_description=entry["task"],
            task_id=entry["task"][:20],
            app=app,
            start_state_id=entry.get("start_state_id"),
            end_state_id=entry.get("end_state_id"),
            success=True,
        )
        if ok:
            # Remove committed entry
            pending.pop(index)
            with open(pending_file, "w", encoding="utf-8") as f:
                json.dump(pending, f, ensure_ascii=False, indent=2)
            print(f"✅ 轨迹已提交 Neo4j: {entry['task'][:50]}")
        return ok
    
    def _learn_successful_pattern(self):
        """Learn patterns from successfully completed tasks."""
        if len(self.session_history) < 2:
            return
        
        # Extract the sequence of apps used
        apps_sequence = []
        for step in self.session_history:
            app = step.get("app", "")
            if app and app not in ("Unknown", "unknown"):
                if not apps_sequence or apps_sequence[-1] != app:
                    apps_sequence.append(app)
        
        # If there's a consistent app flow, record it
        if len(apps_sequence) >= 2:
            flow_description = " → ".join(apps_sequence[:5])
            self.store.add(
                content=f"任务执行流程: {self.current_task[:30]} 使用了 {flow_description}",
                memory_type=MemoryType.TASK_PATTERN,
                metadata={
                    "task_type": self._classify_task(self.current_task),
                    "apps_flow": apps_sequence,
                    "task_summary": self.current_task[:100],
                },
                importance=0.4,
            )
        
        # 🔥 重要：记录联系人-应用绑定关系
        self._learn_contact_app_binding(apps_sequence)
    
    def _learn_contact_app_binding(self, apps_used: list[str]):
        """
        学习联系人与应用的绑定关系，基于使用频率。
        
        当用户通过某个应用联系某人时，记录这个关联。
        如果多次使用同一应用联系同一人，增加使用次数。
        """
        if not apps_used:
            return
        
        # 从当前任务中提取联系人
        import re
        contact_patterns = [
            r'给[「『""]?([\u4e00-\u9fa5a-zA-Z]{2,10})[」』""]?(?:发|说|打)',
            r'联系[「『""]?([\u4e00-\u9fa5a-zA-Z]{2,10})[」』""]?',
            r'(?:to|contact|message)\s+([a-zA-Z\u4e00-\u9fa5]{2,15})',
        ]
        
        contacts_found = set()
        for pattern in contact_patterns:
            matches = re.findall(pattern, self.current_task, re.IGNORECASE)
            for match in matches:
                if len(match) >= 2:
                    contacts_found.add(match)
        
        if not contacts_found:
            return
        
        # 找到主要使用的通讯应用
        comm_apps = ["qq", "微信", "wechat", "钉钉", "dingtalk", "飞书", "feishu", "短信", "sms"]
        main_app = None
        for app in apps_used:
            if any(ca in app.lower() for ca in comm_apps):
                main_app = app
                break
        
        if not main_app:
            return
        
        # 为每个联系人创建/更新应用绑定
        for contact in contacts_found:
            self._update_contact_app_binding(contact, main_app)
    
    def _update_contact_app_binding(self, contact: str, app: str):
        """
        更新联系人-应用绑定，支持使用频率统计。
        
        如果已存在绑定，增加使用次数；否则创建新绑定。
        """
        # 生成唯一的绑定 ID
        binding_key = f"{contact.lower()}_{app.lower()}"
        
        # 查找现有绑定
        existing_binding = None
        for memory in self.store.memories.values():
            if memory.memory_type == MemoryType.CONTACT_APP_BINDING:
                if memory.metadata.get("binding_key") == binding_key:
                    existing_binding = memory
                    break
        
        if existing_binding:
            # 更新现有绑定
            existing_binding.access_count += 1
            existing_binding.last_accessed = datetime.now().isoformat()
            # 重要性随使用次数增加
            existing_binding.importance = min(1.0, 0.5 + existing_binding.access_count * 0.05)
            existing_binding.metadata["use_count"] = existing_binding.access_count
            self.store._save_memories()
        else:
            # 创建新绑定
            self.store.add(
                content=f"联系人应用绑定: {contact} → {app}",
                memory_type=MemoryType.CONTACT_APP_BINDING,
                metadata={
                    "contact": contact,
                    "app": app,
                    "binding_key": binding_key,
                    "use_count": 1,
                },
                importance=0.5,
            )
    
    def _classify_task(self, task: str) -> str:
        """Classify task into categories."""
        task_lower = task.lower()
        
        if any(k in task_lower for k in ["消息", "发送", "聊天", "微信", "qq"]):
            return "communication"
        if any(k in task_lower for k in ["外卖", "点餐", "美团", "饿了么"]):
            return "food_delivery"
        if any(k in task_lower for k in ["购买", "下单", "淘宝", "京东", "购物"]):
            return "shopping"
        if any(k in task_lower for k in ["导航", "地图", "打车", "路线"]):
            return "navigation"
        if any(k in task_lower for k in ["视频", "抖音", "b站", "bilibili"]):
            return "entertainment"
        if any(k in task_lower for k in ["设置", "配置", "开关"]):
            return "settings"
        
        return "general"
    
    def add_step(self, thinking: str, action: dict, screenshot_app: str = ""):
        """
        Record a step in the current task and auto-learn from it.

        This method automatically extracts:
        - App usage patterns from the current app
        - Contact information from actions
        - User preferences from the thinking process
        - Shopping product info (names, prices, specs) for unified state
        """
        step = {
            "timestamp": datetime.now().isoformat(),
            "thinking": thinking,
            "action": action,
            "app": screenshot_app,
        }
        self.session_history.append(step)

        # Extract app usage patterns
        if screenshot_app:
            self._track_app_usage(screenshot_app)

        # Auto-learn from action
        if self.enable_auto_extract:
            self._learn_from_action(action)
        
        # Auto-learn from thinking (extract mentioned entities)
        if self.enable_thinking_analysis and thinking:
            self._learn_from_thinking(thinking)

        # Update unified session state
        if thinking and screenshot_app:
            self._update_session_state(thinking, action, screenshot_app)

    def _update_session_state(self, thinking: str, action: dict, screenshot_app: str) -> None:
        """Update unified session state with step data (single write, no dual-write)."""
        action_type = action.get("action_type", action.get("action", ""))
        action_target = action.get("element", action.get("text", ""))
        page_type = self._infer_page_type(screenshot_app, thinking)

        # Record step into unified state
        self.state.record_step(
            step=len(self.session_history),
            action_type=action_type,
            action_target=str(action_target)[:30] if action_target else "",
            thinking_full=thinking,
            page_type=page_type,
            app=screenshot_app,
        )
        self.state.set_current_focus(app=screenshot_app, page=page_type)

        # Extract product info from thinking
        product = self._extract_product_from_text(thinking)
        if product and product.name:
            self.state.record_product(
                name=product.name,
                price=product.price,
                specs=product.specs,
                page_type=page_type,
                step=len(self.session_history),
            )

        # Track cart action
        if any(kw in thinking for kw in ["加入购物车", "加购", "add to cart", "购物车"]):
            if self.state._current_product:
                self.state.add_to_cart(self.state._current_product)

        # Track constraints from thinking
        self._extract_constraints_from_text(thinking)

        # Update platform if detected from app
        if not self.state.platform and screenshot_app:
            platform = self._detect_platform(screenshot_app)
            if platform:
                self.state.platform = platform

    def _calculate_duration(self) -> float:
        """Calculate task duration in seconds."""
        if not self.task_start_time:
            return 0.0
        try:
            start = datetime.fromisoformat(self.task_start_time)
            return (datetime.now() - start).total_seconds()
        except Exception:
            return 0.0
    
    def _extract_from_task(self, task: str):
        """Extract memories from user task description."""
        # Extract general shopping preferences
        import re
        shopping_prefs = [
            r'(?:想要|买|要|选择|看)(?:个|一台|一部)?(.*?(?:品牌|牌子|便宜|贵|新款|折叠屏|全面屏|大杯|小杯|自营|旗舰店|百亿补贴))',
            r'(最新款|最便宜|性价比最高|销量最高|官方自营|百亿补贴)'
        ]
        for pattern in shopping_prefs:
            matches = re.findall(pattern, task, re.IGNORECASE)
            for match in matches:
                pref = match.strip() if isinstance(match, str) else match[0].strip()
                if pref and len(pref) >= 2:
                    self.add_user_preference(f"倾向于选择 {pref}", category="shopping", importance=0.7)

        # Extract contact mentions
        for pattern in PREFERENCE_PATTERNS["contact"]:
            matches = re.findall(pattern, task, re.IGNORECASE)
            for match in matches:
                if isinstance(match, tuple) and len(match) > 1:
                    contact_name = match[1].strip()
                    if len(contact_name) >= 2:
                        self._add_contact_memory(contact_name, task)
        
        # Extract app mentions
        for pattern in PREFERENCE_PATTERNS["app"]:
            matches = re.findall(pattern, task, re.IGNORECASE)
            for match in matches:
                if isinstance(match, tuple) and len(match) > 1:
                    app_name = match[1].strip()
                    if app_name.lower() in {a.lower() for a in KNOWN_APPS}:
                        self._add_app_preference(app_name, task)
    
    def _add_contact_memory(self, name: str, context: str):
        """Add or update contact memory."""
        self.store.add(
            content=f"联系人: {name}",
            memory_type=MemoryType.CONTACT,
            metadata={
                "name": name,
                "last_context": context,
                "interaction_count": 1,
            },
            importance=0.5,
        )
    
    def _add_app_preference(self, app_name: str, context: str):
        """Add or update app preference."""
        self.store.add(
            content=f"用户常用应用: {app_name}",
            memory_type=MemoryType.APP_USAGE,
            metadata={
                "app_name": app_name,
                "context": context,
            },
            importance=0.4,
        )
    
    def _track_app_usage(self, app_name: str):
        """Track app usage for preference learning."""
        if not app_name or app_name in ("Unknown", "unknown"):
            return
        
        # Normalize app name
        app_lower = app_name.lower().strip()
        
        # Avoid tracking in same session too frequently
        if app_lower in self._session_apps:
            return
        self._session_apps.add(app_lower)
        
        # Check if this is a known app worth tracking
        is_known = any(
            known.lower() in app_lower or app_lower in known.lower()
            for known in KNOWN_APPS
        )
        
        if is_known:
            self.store.add(
                content=f"用户使用了应用: {app_name}",
                memory_type=MemoryType.APP_USAGE,
                metadata={
                    "app_name": app_name,
                    "timestamp": datetime.now().isoformat(),
                },
                importance=0.3,
            )
    
    def _learn_from_action(self, action: dict):
        """Auto-learn from executed actions."""
        action_type = action.get("action", "")
        
        # Learn from Type_Name action (contact names)
        if action_type == "Type_Name":
            name = action.get("text", "")
            if name and len(name) >= 2:
                self._auto_add_contact(name, f"在任务中输入了联系人名: {name}")
        
        # Learn from Launch action (app preferences)
        if action_type == "Launch":
            app = action.get("app", "")
            if app:
                self._auto_add_app(app, f"用户主动启动了应用: {app}")
        
        # Learn from Type action (potential search patterns)
        if action_type == "Type":
            text = action.get("text", "")
            if text and len(text) > 5:
                self._learn_search_pattern(text)
    
    # 不应被识别为联系人的常见词汇
    _CONTACT_BLACKLIST = {
        "联系人", "聊天窗口", "窗口", "消息", "发送消息", "发送", "对话",
        "一个联系人", "某个联系人", "该联系人", "这个联系人",
        "输入框", "搜索框", "搜索栏", "按钮", "图标", "界面", "页面",
        "应用", "程序", "列表", "设置", "信息", "内容", "文本",
        "位置", "区域", "屏幕", "底部", "顶部", "左侧", "右侧",
        "相应", "相关", "当前", "目标", "指定", "对应",
        "来打开", "来打开聊天窗口", "的聊天窗口", "信息是可见的",
        "打开聊天", "发消息", "说", "打电话", "视频通话",
        "这个", "那个", "一个", "某个", "其他", "所有",
        "成功", "失败", "完成", "结束", "开始", "继续",
    }
    
    def _learn_from_thinking(self, thinking: str):
        """Auto-learn from agent's thinking process."""
        contact_patterns = [
            r'[「『""]([\u4e00-\u9fa5a-zA-Z]{2,8})[」』""](?:的聊天|的对话|的消息)',
            r'给[「『""]([\u4e00-\u9fa5a-zA-Z]{2,8})[」『""](?:发|说|打)',
            r'找到了?[「『""]([\u4e00-\u9fa5a-zA-Z]{2,8})[」『""](?:的聊天|这个联系)',
        ]
        
        for pattern in contact_patterns:
            matches = re.findall(pattern, thinking)
            for name in matches:
                name = name.strip()
                if (
                    name
                    and len(name) >= 2
                    and name not in self._session_contacts
                    and name not in self._CONTACT_BLACKLIST
                    and not any(bw in name for bw in ("窗口", "输入", "按钮", "页面", "列表", "可见"))
                ):
                    self._auto_add_contact(name, f"从任务执行中识别: {thinking[:50]}")
        
        # Extract preference hints from thinking
        preference_hints = [
            (r"用户(喜欢|偏好|习惯|经常|常用)([\u4e00-\u9fa5a-zA-Z0-9]+)", "habit"),
            (r"(深色|浅色|暗色|亮色)模式", "ui"),
            (r"(每天|每周|通常|一般)([\u4e00-\u9fa5]+)", "time"),
        ]
        
        for pattern, category in preference_hints:
            match = re.search(pattern, thinking)
            if match:
                preference_text = match.group(0)
                self.store.add(
                    content=f"从执行过程推断: {preference_text}",
                    memory_type=MemoryType.USER_PREFERENCE,
                    metadata={
                        "category": category,
                        "source": "auto_thinking",
                        "context": thinking[:100],
                    },
                    importance=0.4,
                )

    # ------------------------------------------------------------------
    # SessionMemory helpers — product extraction, summary, platform
    # ------------------------------------------------------------------

    def _extract_product_from_text(self, text: str) -> Product | None:
        """
        Extract product information from VLM thinking text.

        Uses the structured format guided by prompts (Phase 4):
          "商品名为【Name】" / "价格为 ¥Price" / "颜色为Color"
        Falls back to simple regex patterns when VLM doesn't follow format.
        """
        import re

        product_name: str | None = None
        price: float | None = None
        specs: dict[str, str] = {}

        # ── Primary: structured format from prompt guidance ──────────
        name_match = re.search(r'商品名为【(.+?)】', text)
        if not name_match:
            name_match = re.search(r'product name is【(.+?)】', text)
        if not name_match:
            name_match = re.search(r'已加购【(.+?)】', text)
        if not name_match:
            name_match = re.search(r'added【(.+?)】to cart', text)
        if name_match:
            product_name = name_match.group(1).strip()
            if not product_name or len(product_name) < 2:
                return None

        # ── Fallback: simple name extraction ──────────────────────────
        if not product_name:
            for pat in [
                r'(?:看到|点击了?|打开了?|进入了?)\s*[""「『]?(.{2,40})[""」『]?(?:，|。|售价|价格|¥|￥)',
            ]:
                m = re.search(pat, text)
                if m:
                    name = m.group(1).strip().rstrip("，。,.》）\"'「」『』")
                    # Strip trailing price info
                    name = re.sub(r'(?:售价|价格|¥|￥|元|块)\s*\d*\.?\d*\s*$', '', name).strip()
                    name = re.sub(r'(?:，|。|,)\s*$', '', name).strip()
                    if 2 <= len(name) <= 50:
                        product_name = name
                        break

        if not product_name:
            return None

        # ── Price: structured format first ────────────────────────────
        price_match = re.search(r'价格为\s*[¥￥]\s*(\d+(?:\.\d{1,2})?)', text)
        if not price_match:
            price_match = re.search(r'price is\s*\$?(\d+(?:\.\d{1,2})?)', text)
        if not price_match:
            # Fallback: "¥数字" or "数字元"
            price_match = re.search(r'[¥￥]\s*(\d+(?:\.\d{1,2})?)', text)
        if not price_match:
            price_match = re.search(r'(\d+(?:\.\d{1,2})?)\s*元', text)
        if price_match:
            try:
                price = float(price_match.group(1).replace(",", ""))
            except (ValueError, IndexError):
                pass

        # ── Specs: structured format "X为Y" ────────────────────────────
        for spec_key in self._shopping_config_spec_keys():
            spec_match = re.search(rf'{spec_key}为\s*([^，。、,\s]+)', text)
            if spec_match:
                specs[spec_key] = spec_match.group(1).strip()

        return Product(name=product_name, price=price, specs=specs)

    @staticmethod
    def _shopping_config_spec_keys() -> list[str]:
        """Get spec keywords from ShoppingConfig (lazy import to avoid circular)."""
        from phone_agent.config.shopping_config import ShoppingConfig
        config = ShoppingConfig.load()
        return sorted(config.spec_keywords, key=len, reverse=True)

    def _extract_constraints_from_text(self, text: str) -> None:
        """Extract user constraints (budget, brand preference) from thinking."""
        # Budget constraint
        budget_patterns = [
            r'(?:预算|不超过|以内|之内|范围内)\s*[¥￥]?\s*(\d+(?:\.\d{1,2})?)',
            r'(?:budget)\s*(?:is|:)?\s*\$?(\d+(?:\.\d{1,2})?)',
        ]
        for pattern in budget_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                self.state.constraints["budget"] = match.group(1)
                break

        # Brand constraint
        brand_patterns = [
            r'(?:只要|就要|偏好|想买|买)(?:这个)?\s*([\u4e00-\u9fa5a-zA-Z]{2,15})(?:的|牌|品牌)',
        ]
        for pattern in brand_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                brand = match.group(1).strip()
                if brand and len(brand) >= 2:
                    self.state.constraints["brand"] = brand
                    break


    def _infer_page_type(self, app: str, thinking: str) -> str:
        """Infer the current page type based on thinking context."""
        page_signals = {
            "home": ["首页", "主页", "home", "主界面"],
            "search": ["搜索", "search", "输入关键词", "搜索框"],
            "search_result": ["搜索结果", "搜索列表", "找到.*个商品"],
            "product_detail": ["详情", "detail", "规格", "参数", "商品介绍"],
            "cart": ["购物车", "cart", "结算"],
            "checkout": ["结算", "支付", "checkout", "提交订单"],
            "spec_selection": ["规格", "颜色", "尺码", "选择.*规格"],
            "order": ["订单", "order", "物流"],
        }
        for page_type, signals in page_signals.items():
            if any(s in thinking.lower() for s in signals):
                return page_type
        return ""

    def _detect_platform(self, text: str) -> str:
        """Detect shopping platform from text using externalized config."""
        from phone_agent.config.shopping_config import ShoppingConfig
        config = ShoppingConfig.load()
        text_lower = text.lower()
        for platform in config.platforms:
            if platform.lower() in text_lower:
                return platform
        return ""

    def compress_session_history(self) -> str | None:
        """
        Compress every 5 steps into one concise summary using a lightweight VLM call.

        Uses UnifiedSessionState steps (thinking_short) instead of old StepSummary.
        Returns the compressed summary string, or None if compression was skipped.
        """
        if not self.state.should_compress():
            return None

        recent = self.state.steps[-5:]
        summaries_text = "\n".join(
            f"Step {s.step}: {s.thinking_short or s.action_type}" for s in recent
        )

        try:
            from openai import OpenAI
            import os

            model_name = os.getenv("PHONE_AGENT_MODEL", "autoglm-phone-9b")
            base_url = os.getenv("PHONE_AGENT_BASE_URL", "http://localhost:8000/v1")
            api_key = os.getenv("PHONE_AGENT_API_KEY", "EMPTY")
            client = OpenAI(base_url=base_url, api_key=api_key)

            prompt = (
                "将以下手机购物操作的 5 个步骤压缩为一句简洁的进展描述"
                "（保留关键信息：App、商品名、价格、动作结果）：\n"
                + summaries_text
                + "\n\n压缩为一句："
            )

            response = client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=model_name,
                temperature=0.1,
                max_tokens=80,
            )
            compressed = (response.choices[0].message.content or "").strip()

            if compressed and len(compressed) >= 5:
                # Store compressed summary as overall progress
                self.state.overall_progress = f"[压缩] {compressed}"
                return compressed

        except Exception:
            pass

        return None

    def _auto_add_contact(self, name: str, context: str):
        """Auto-add contact with deduplication."""
        if name in self._session_contacts:
            return
        self._session_contacts.add(name)

        self.store.add(
            content=f"联系人: {name}",
            memory_type=MemoryType.CONTACT,
            metadata={
                "name": name,
                "source": "auto_extract",
                "context": context,
            },
            importance=0.5,
        )

    def _auto_add_app(self, app_name: str, context: str):
        """Auto-add frequently used app."""
        app_lower = app_name.lower()
        if app_lower in self._session_apps:
            return
        self._session_apps.add(app_lower)
        
        self.store.add(
            content=f"用户常用应用: {app_name}",
            memory_type=MemoryType.APP_USAGE,
            metadata={
                "app_name": app_name,
                "source": "auto_extract",
                "context": context,
            },
            importance=0.5,
        )
    
    def _learn_search_pattern(self, search_text: str):
        """Learn from user search patterns."""
        # Common search pattern categories
        food_keywords = ["外卖", "餐厅", "美食", "咖啡", "奶茶", "火锅", "烧烤"]
        shopping_keywords = ["购买", "下单", "商品", "店铺", "价格"]
        travel_keywords = ["酒店", "机票", "火车", "打车", "导航", "路线"]
        
        for keyword in food_keywords:
            if keyword in search_text:
                self.store.add(
                    content=f"用户搜索过: {search_text}",
                    memory_type=MemoryType.TASK_PATTERN,
                    metadata={
                        "category": "food",
                        "search_text": search_text,
                    },
                    importance=0.3,
                )
                return
        
        for keyword in shopping_keywords:
            if keyword in search_text:
                self.store.add(
                    content=f"用户搜索过: {search_text}",
                    memory_type=MemoryType.TASK_PATTERN,
                    metadata={
                        "category": "shopping",
                        "search_text": search_text,
                    },
                    importance=0.3,
                )
                return
        
        for keyword in travel_keywords:
            if keyword in search_text:
                self.store.add(
                    content=f"用户搜索过: {search_text}",
                    memory_type=MemoryType.TASK_PATTERN,
                    metadata={
                        "category": "travel",
                        "search_text": search_text,
                    },
                    importance=0.3,
                )
    
    def add_user_preference(
        self,
        preference: str,
        category: str = "general",
        importance: float = 0.6,
    ):
        """
        Manually add a user preference.
        
        Args:
            preference: The preference description
            category: Category of preference
            importance: Importance score (0-1)
        """
        self.store.add(
            content=f"用户偏好 ({category}): {preference}",
            memory_type=MemoryType.USER_PREFERENCE,
            metadata={
                "category": category,
                "raw_preference": preference,
            },
            importance=importance,
        )
    
    def add_user_correction(self, original_action: str, correction: str):
        """
        Record a user correction for learning.
        
        Args:
            original_action: What the agent did wrong
            correction: What the user wanted instead
        """
        self.store.add(
            content=f"用户纠正: 原操作 '{original_action}' 应改为 '{correction}'",
            memory_type=MemoryType.USER_CORRECTION,
            metadata={
                "original": original_action,
                "correction": correction,
                "task": self.current_task,
            },
            importance=0.8,  # Corrections are highly important
        )
    
    # ------------------------------------------------------------------
    # Memory Decoupling: lightweight context + on-demand retrieval
    # Replaces the old 4-layer push injection with UI-Copilot paradigm
    # ------------------------------------------------------------------

    def get_injection_context(
        self, thinking: str = "", current_app: str = "", step: int = 0,
    ) -> str:
        """
        Generate context for VLM injection using memory decoupling.

        ALWAYS injected (lightweight):
          - KnowledgeBase.progress_summary() — 1-2 lines of progress

        ON-DEMAND injected (triggered by retrieval signals):
          - RetrievalGateway.check_and_retrieve() — product details, recall, etc.

        This replaces the old Phase-6 multi-layer push injection with
        UI-Copilot's paradigm: minimal context by default, retrieval only
        when the agent shows signs of needing it.
        """
        parts: list[str] = []

        # Layer 1: Always-on progress summary (lightweight)
        progress = self.state.progress_summary()
        if progress:
            parts.append(f"[进度] {progress}")

        # Layer 2: Current focus (1 line)
        focus = self.state.current_focus()
        if focus:
            parts.append(f"[当前] {focus}")

        # Layer 3: On-demand retrieval (only when triggered)
        if thinking and step > 0:
            result = self.retrieval_gateway.check_and_retrieve(thinking, step)
            if result.triggered:
                parts.append(result.context_text)
                if hasattr(self, '_verbose') and getattr(self, '_verbose', True):
                    print(f"🔍 [On-Demand Retrieval] intent={result.intent} source={result.source}")

        # Layer 4: Constraints reminder (if any)
        if self.state.constraints:
            constraint_kv = ", ".join(
                f"{k}={v}" for k, v in self.state.constraints.items()
            )
            parts.append(f"[约束] {constraint_kv}")

        return "\n".join(parts) if parts else ""

    def record_product_to_kb(
        self, name: str, price: float | None = None,
        specs: dict | None = None, page_type: str = "",
        status: str = "viewed",
    ) -> None:
        """Record a product observation into unified state."""
        step_num = len(self.session_history)
        from .core import ProductStatus as PS
        try:
            ps = PS(status)
        except ValueError:
            ps = PS.VIEWED
        self.state.record_product(
            name=name, price=price, specs=specs,
            page_type=page_type, status=ps, step=step_num,
        )

    def get_relevant_context(self, task: str, max_memories: int = 8) -> str:
        """
        Get relevant memories for a task as context.

        Prioritizes contact-app bindings based on usage frequency.
        
        Args:
            task: The current task description
            max_memories: Maximum number of memories to include
        
        Returns:
            Formatted context string for the agent prompt
        """
        import re
        
        # 1. 首先提取任务中的联系人
        contact_patterns = [
            r'给[「『""]?([\u4e00-\u9fa5a-zA-Z]{2,10})[」『""]?(?:发|说|打)',
            r'联系[「『""]?([\u4e00-\u9fa5a-zA-Z]{2,10})[」『""]?',
        ]
        task_contacts = set()
        for pattern in contact_patterns:
            matches = re.findall(pattern, task, re.IGNORECASE)
            task_contacts.update(matches)
        
        # 2. 查找联系人-应用绑定（基于频率）
        contact_app_stats = {}  # {contact: {app: count}}
        
        for memory in self.store.memories.values():
            if memory.memory_type == MemoryType.CONTACT_APP_BINDING:
                contact = memory.metadata.get("contact", "")
                app = memory.metadata.get("app", "")
                use_count = memory.metadata.get("use_count", 1)
                
                if contact and app:
                    if contact not in contact_app_stats:
                        contact_app_stats[contact] = {}
                    contact_app_stats[contact][app] = use_count
        
        # 3. Search for relevant memories
        memories = self.store.search(
            query=task,
            top_k=max_memories,
            min_importance=0.2,
        )
        
        # Format memories as context
        context_parts = ["【用户个性化信息 - 请严格按照以下信息选择应用】"]
        
        # 4. 🔥 最重要：基于频率的联系人-应用推荐
        frequency_recommendations = []
        for contact in task_contacts:
            # 查找这个联系人的应用使用统计
            if contact in contact_app_stats:
                apps_stats = contact_app_stats[contact]
                # 按使用次数排序
                sorted_apps = sorted(apps_stats.items(), key=lambda x: x[1], reverse=True)
                if sorted_apps:
                    best_app, best_count = sorted_apps[0]
                    total_count = sum(apps_stats.values())
                    
                    # 生成推荐说明
                    if len(sorted_apps) > 1:
                        second_app, second_count = sorted_apps[1]
                        frequency_recommendations.append(
                            f"⚡ 联系「{contact}」：推荐使用 **{best_app}** (使用{best_count}次) "
                            f"而非 {second_app} (使用{second_count}次)"
                        )
                    else:
                        frequency_recommendations.append(
                            f"⚡ 联系「{contact}」：推荐使用 **{best_app}** (已使用{best_count}次)"
                        )
            else:
                # 从任务历史中查找
                for memory in self.store.memories.values():
                    if memory.memory_type == MemoryType.TASK_HISTORY:
                        past_task = memory.metadata.get("task", "")
                        if contact in past_task:
                            apps_used = memory.metadata.get("apps_used", [])
                            for app in apps_used:
                                if app.lower() not in ("system home", "unknown"):
                                    frequency_recommendations.append(
                                        f"⚡ 联系「{contact}」：历史记录显示使用 **{app}**"
                                    )
                                    break
                            break
        
        # 添加频率推荐（最高优先级）
        if frequency_recommendations:
            context_parts.append("\n**🎯 基于使用频率的应用推荐（必须遵循）:**")
            for rec in frequency_recommendations[:5]:
                context_parts.append(f"  {rec}")
        
        # 5. 任务历史中的应用关联（次要参考）
        task_app_hints = []
        for memory in memories:
            if memory.memory_type == MemoryType.TASK_HISTORY:
                past_task = memory.metadata.get("task", "")
                apps_used = memory.metadata.get("apps_used", [])
                success = memory.metadata.get("success", False)
                
                if success and past_task and apps_used:
                    for app in apps_used:
                        if app.lower() not in ("system home", "unknown"):
                            task_app_hints.append(f"历史: 「{past_task[:35]}」→ {app}")
                            break
            
            elif memory.memory_type == MemoryType.TASK_PATTERN:
                apps_flow = memory.metadata.get("apps_flow", [])
                task_summary = memory.metadata.get("task_summary", "")
                if apps_flow and task_summary:
                    main_app = [a for a in apps_flow if a.lower() not in ("system home", "unknown")]
                    if main_app:
                        task_app_hints.append(f"模式: 「{task_summary[:30]}」→ {main_app[0]}")
        
        if task_app_hints:
            context_parts.append("\n**📋 相关任务历史:**")
            for hint in task_app_hints[:3]:
                context_parts.append(f"  {hint}")
        
        # Add shopping preferences
        shopping_prefs = []
        for memory in memories:
            if memory.memory_type == MemoryType.USER_PREFERENCE and memory.metadata.get("category") == "shopping":
                shopping_prefs.append(memory.content)
        
        if shopping_prefs:
            context_parts.append("")
            context_parts.append("**🛒 购物偏好:**")
            for pref in shopping_prefs[:3]:
                context_parts.append(f"  {pref}")

        # 6. 其他记忆（低优先级）
        other_context = []
        for memory in memories:
            if memory.memory_type == MemoryType.USER_CORRECTION:
                other_context.append(f"⚠️ 注意: {memory.content}")
            elif memory.memory_type == MemoryType.USER_PREFERENCE:
                pref = memory.metadata.get("raw_preference", memory.content)
                other_context.append(f"偏好: {pref}")
        
        if other_context:
            context_parts.append("\n**其他信息:**")
            context_parts.extend(other_context[:3])
        
        if len(context_parts) == 1:
            return ""
        
        return "\n".join(context_parts)
    
    def locate_and_get_context(self, ui_hash: str, semantic_layout: str, task: str) -> dict:
        """
        基于 GraphRAG 的双层匹配策略：

        1. GraphRAG 任务语义匹配 (FAISS + Neo4j) → 语义向量召回历史轨迹
           - 高置信度 (>= 0.85): 直接提取第一个动作，进入 Navigate 模式执行
           - 中置信度 (0.60-0.85): 提取路径，凝练后注入上下文，进入 Explore 模式推理
        2. FAISS 向量语义 fallback → 仅补充 UI state 的探索上下文
        """
        context_data = {
            "max_similarity": 0.0,
            "mode": "explore",
            "semantic_context": self.get_relevant_context(task),
            "next_actions": [],
            "current_state_id": None,
            "task_trajectory": None,
        }

        # Layer 1: 基于 embedding-3 的任务语义匹配 (GraphRAG 入口)
        if task and len(task) > 3:
            similar_tasks = self.graph_store.find_similar_tasks(task, top_k=3)

            if similar_tasks:
                best = similar_tasks[0]
                similarity = best.get("similarity", 0.0)
                context_data["max_similarity"] = similarity
                task_id = best.get("task_id", "")
                task_desc = best.get("description", "")

                # 获取该相似任务的完整轨迹
                trajectory = self.graph_store.get_task_trajectory(task_id)

                # 高置信度匹配：尝试直接执行快捷动作
                if similarity >= 0.85:
                    first_action = self._get_first_action(trajectory)
                    if first_action:
                        context_data["mode"] = "navigate"
                        context_data["next_actions"] = [first_action]
                        print(f"🚀 语义命中: 「{task_desc}」(相似度={similarity:.2f})")
                        return context_data
                    else:
                        print(f"⚠️ 语义命中但无有效轨迹: 「{task_desc}」(相似度={similarity:.2f})，回退到 Explore 模式")
                        similar_tasks = [t for t in similar_tasks if self.graph_store.get_task_trajectory(t.get("task_id", "")).get("steps", [])]

                # 中/低置信度匹配：提取压缩轨迹注入上下文，交由大模型推理
                if similar_tasks and similar_tasks[0].get("similarity", 0.0) >= 0.60:
                    condensed_text = self._condense_trajectory_context(similar_tasks, current_task=task)
                    context_data["semantic_context"] = (
                        f"【⚠️ 历史参考 - 仅供参考，严禁直接复用】\n"
                        f"当前用户指令：「{task}」\n"
                        f"以下为相似历史任务的执行轨迹。当前用户的实际需求可能与以下内容不同。\n"
                        f"如果当前指令缺少具体细节（平台、店铺、商品、联系人等），必须使用 Interact 主动询问！\n"
                        f"---\n"
                        f"{condensed_text}\n"
                        f"（注意：当前界面可能与历史轨迹不同，请根据实际截图调整动作）\n"
                        f"{context_data.get('semantic_context', '')}"
                    )
                    context_data["task_trajectory"] = trajectory
                    print(f"🔍 语义参考: 「{task_desc}」(相似度={similarity:.2f})")

        # Layer 2: FAISS UI状态向量 fallback — 仅补充上下文
        if semantic_layout and len(semantic_layout) > 5:
            similar_states = self.store.search(
                query=semantic_layout,
                top_k=1,
                min_importance=0.0,
                memory_types=[MemoryType.UI_STATE]
            )
            if similar_states:
                matched_content = similar_states[0].content or ""
                if len(matched_content) > 10:
                    context_data["semantic_context"] = (
                        context_data.get("semantic_context", "")
                        + f"\n[参考历史页面] {matched_content[:200]}"
                    )
                    print(f"🔄 FAISS Semantic Hint: 参考历史页面特征")

        return context_data

    def _condense_trajectory_context(self, similar_tasks: list[dict], current_task: str = "") -> str:
        """
        将相似任务凝练成≤200字的行动参考。
        格式：app→动作1→动作2→动作3→动作4→动作5
        """
        lines = ["【行动参考】"]
        total_len = len("【行动参考】")

        for task in similar_tasks[:2]:
            desc = task.get("description", "")[:30]
            app = task.get("app", "")
            freq = task.get("frequency", 0)
            similarity = task.get("similarity", 0)

            trajectory = self.graph_store.get_task_trajectory(task["task_id"])
            steps = trajectory.get("steps", [])
            condensed = self._condense_steps(steps, max_steps=5)

            entry = f'"{desc}"({app}·{freq}次)：{condensed}'
            if total_len + len(entry) > 200:
                break
            lines.append(entry)
            total_len += len(entry) + 1

        return "\n".join(lines)

    def _condense_steps(self, steps: list[dict], max_steps: int = 5) -> str:
        """
        将步骤列表凝练为箭头分隔的行动指南。
        过滤辅助动作（wait），保留关键动作。
        """
        KEY_ACTIONS = {"click", "tap", "type", "input", "launch", "open", "swipe", "scroll", "long_press"}
        key_steps = [
            s for s in steps
            if s.get("action_type", "").lower() in KEY_ACTIONS
        ]
        key_steps = key_steps[:max_steps]

        parts = []
        for s in key_steps:
            action = s.get("action_type", "")
            target = s.get("action_target", "")[:8]
            if target:
                parts.append(f"{target}")
            else:
                parts.append(action)
        return "→".join(parts) if parts else "执行中"

    def _get_first_action(self, trajectory: dict) -> dict | None:
        """从轨迹提取第一个动作，用于Navigate模式直接执行"""
        steps = trajectory.get("steps", [])
        if not steps:
            return None
        first = steps[0]
        return {
            "type": first.get("action_type", "click"),
            "target": first.get("action_target", ""),
            "confidence": 0.9,
        }

    def get_user_summary(self) -> dict:
        """
        Get a summary of user information.
        
        Returns:
            Dictionary with user preferences, contacts, and habits
        """
        summary = {
            "contacts": [],
            "frequent_apps": [],
            "preferences": [],
            "recent_tasks": [],
        }
        
        # Get frequent contacts
        contacts = self.store.get_by_type(MemoryType.CONTACT, limit=10)
        for mem in contacts:
            name = mem.metadata.get("name", "")
            if name:
                summary["contacts"].append(name)
        
        # Get frequent apps
        apps = self.store.get_by_type(MemoryType.APP_USAGE, limit=10)
        app_counts: dict[str, int] = {}
        for mem in apps:
            app = mem.metadata.get("app_name", "")
            if app:
                app_counts[app] = app_counts.get(app, 0) + mem.access_count
        
        # Sort by usage frequency
        sorted_apps = sorted(app_counts.items(), key=lambda x: x[1], reverse=True)
        summary["frequent_apps"] = [app for app, _ in sorted_apps[:5]]
        
        # Get preferences
        prefs = self.store.get_by_type(MemoryType.USER_PREFERENCE, limit=10)
        for mem in prefs:
            pref = mem.metadata.get("raw_preference", "")
            if pref:
                summary["preferences"].append(pref)
        
        # Get recent tasks
        tasks = self.store.get_by_type(MemoryType.TASK_HISTORY, limit=5)
        for mem in tasks:
            task = mem.metadata.get("task", "")
            if task:
                summary["recent_tasks"].append(task)
        
        return summary
    
    def get_stats(self) -> dict:
        """Get memory statistics."""
        store_stats = self.store.get_stats()
        store_stats["user_id"] = self.user_id
        store_stats["session_steps"] = len(self.session_history)
        return store_stats
    
    def clear_all(self):
        """Clear all memories for this user."""
        self.store.clear()
        self.session_history.clear()
    
    def export_memories(self) -> list[dict]:
        """Export all memories for backup."""
        return self.store.export_memories()
    
    def import_memories(self, memories: list[dict]):
        """Import memories from backup."""
        self.store.import_memories(memories)


def build_personalized_prompt(
    base_prompt: str,
    memory_manager: MemoryManager,
    task: str,
) -> str:
    """
    Build a personalized system prompt with memory context.
    
    Args:
        base_prompt: The original system prompt
        memory_manager: Memory manager instance
        task: Current task description
    
    Returns:
        Enhanced prompt with personalization context
    """
    # Get relevant context
    context = memory_manager.get_relevant_context(task)
    
    if not context:
        return base_prompt
    
    # Insert personalization context before the rules
    # Find a good insertion point
    if "必须遵循的规则" in base_prompt:
        parts = base_prompt.split("必须遵循的规则")
        enhanced = parts[0] + f"\n\n{context}\n\n必须遵循的规则" + parts[1]
    else:
        enhanced = f"{base_prompt}\n\n{context}"
    
    return enhanced

