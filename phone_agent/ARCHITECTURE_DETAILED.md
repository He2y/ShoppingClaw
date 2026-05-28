# ClawGUI-Agent 架构文档

> 版本：2026-05 | 基于 AMSG v4 运行时

---

## 1. 项目定位

ClawGUI-Agent 是一个面向移动端 GUI 自动化的闭环框架，核心能力是让 Vision-Language Model（VLM）驱动的智能体在真实 Android / HarmonyOS / iOS 设备上执行购物等复杂任务。系统不训练新模型，而是通过**运行时结构化中间层**——空间图谱（AMSG）和多层记忆系统——为通用 VLM 赋予页面定位、路线规划和纠错能力。

### 解决的核心问题

| Mobile Agent 普遍痛点 | 本项目方案 |
|---|---|
| VLM 在多步骤购物任务中容易迷失、重复操作 | AMSG 空间图谱提供"我在哪→去哪→怎么走"的结构化认知 |
| 缺乏跨会话记忆，每次都从零开始 | 四层记忆系统（向量记忆 + 会话状态 + 图谱 + 按需检索） |
| 高风险操作（支付、下单）缺乏安全约束 | SpecGuard + PostconditionVerifier 双重安全门 |
| 不同模型输出格式不统一，难以复用 | ModelProtocolBridge + SpatialModelBridge 双层协议桥 |
| 离线构建的页面图无法在线高效使用 | GraphRuntimeController 将 canonical 图编译为轻量 RuntimeDAG |

---

## 2. 系统架构总览

### 2.1 架构图

系统架构分为 **6 层**，从上到下依次为：输入层 → Agent 核心循环 → 记忆系统 → AMSG 空间图谱 → 设备抽象层 → 外部依赖。

```
┌─────────────────────────────────────────────────────────────────────────┐
│ INPUT LAYER                                                             │
│  用户自然语言任务 ──→ ClarificationAgent (歧义检测 CLEAR / CLARIFY)      │
├─────────────────────────────────────────────────────────────────────────┤
│ AGENT CORE LOOP (PhoneAgent)                                            │
│                                                                         │
│  ┌──────────┐  ┌───────────────┐  ┌──────────────┐  ┌───────────────┐ │
│  │ Screenshot│→│ PageClassifier│→│MemoryManager  │→│ VLM Adapters  │ │
│  │  截图采集 │  │ 页面类型识别  │  │locate+inject │  │ 5种模型适配   │ │
│  └──────────┘  └───────────────┘  └──────────────┘  └───────┬───────┘ │
│        ↑                                                     ↓         │
│        │                    ┌─────────────┐                  │         │
│        │                    │ActionHandler│ ←────────────────┘         │
│        │                    │ 动作解析+    │                            │
│        │                    │ 坐标归一化   │                            │
│        │                    └──────┬──────┘                            │
│        │                           ↓                                   │
│        │              ┌────────────────────┐                           │
│        │              │ SpecGuard (安全门) │                           │
│        │              └────────┬───────────┘                           │
│        │                       ↓                                       │
│        │              ┌────────────────────┐    ┌──────────┐          │
│        └──────────────│  设备执行 (tap/    │ →  │GUITracer │          │
│                       │  swipe/type/back) │    │ 轨迹录制  │          │
│                       └────────────────────┘    └──────────┘          │
├─────────────────────────────────────────────────────────────────────────┤
│ MEMORY SYSTEM (4-Layer)                                                 │
│                                                                         │
│  ┌──────────────┐  ┌────────────────────┐  ┌──────────────┐           │
│  │MemoryManager │→│ UnifiedSessionState│  │MemoryStore   │           │
│  │ 记忆编排中枢 │  │ 会话级单源状态     │  │FAISS向量存储 │           │
│  └──────┬───────┘  │ (商品/步骤/约束)   │  │(偏好/联系人) │           │
│         │          └────────────────────┘  └──────────────┘           │
│         │                                                             │
│         ├──→ RetrievalGateway (按需检索: 不确定性/对比/计算信号触发)   │
│         │                                                             │
│         └──→ SpatialGraphMemory ──→ GraphStore (Neo4j图数据库)        │
│              (页面定位+路线规划+纠错)   (UIState/Action/TaskTarget)     │
├─────────────────────────────────────────────────────────────────────────┤
│ AMSG v4 — ACTIVE MOBILE SPATIAL GRAPH                                  │
│                                                                         │
│  ┌──────────┐  ┌────────────────┐  ┌────────────┐  ┌──────────────┐  │
│  │ PageNode │→│AffordanceEdge  │→│BeliefState │→│SpatialPlanner│  │
│  │语义签名ID│  │weighted_cost   │  │ 页面定位   │  │加权BFS最短路│  │
│  └──────────┘  └────────────────┘  └────────────┘  └──────┬───────┘  │
│                                                            ↓          │
│         ┌─────────────────────────────┐   ┌──────────────────────┐   │
│         │ GraphRuntimeController      │   │PostconditionVerifier │   │
│         │ RuntimeDAG生命周期管理      │──→│ 预期vs观测校验       │   │
│         │ navigate/verify/explore模式 │   │ retry/rollback/replan│   │
│         └────────────┬────────────────┘   └──────────────────────┘   │
│                      ↓                                                │
│  ┌──────────────────────┐  ┌────────────────┐  ┌─────────────────┐  │
│  │FunctionalityItem/    │  │SpatialModel    │  │SchemaRegistry   │  │
│  │Cluster (自发现功能层)│  │Bridge (语义→   │  │ 页面类型Schema  │  │
│  │                      │  │ 设备动作桥接)  │  │                 │  │
│  └──────────────────────┘  └────────────────┘  └─────────────────┘  │
├─────────────────────────────────────────────────────────────────────────┤
│ DEVICE ABSTRACTION LAYER                                                │
│  ┌───────────────┐                                                     │
│  │ DeviceFactory │──→ ADB (Android) / HDC (HarmonyOS) / XCTest (iOS)  │
│  └───────────────┘                                                     │
├─────────────────────────────────────────────────────────────────────────┤
│ EXTERNAL DEPENDENCIES                                                   │
│  VLM API (OpenAI-compatible) │ Neo4j │ FAISS │ ADB/HDC/WDA            │
└─────────────────────────────────────────────────────────────────────────┘
```

> **系统架构图** (SVG): `phone_agent/clawgui-architecture.svg` / PNG: `phone_agent/clawgui-architecture.png`

### 2.2 模块清单

| 模块 | 位置 | 职责 |
|---|---|---|
| `PhoneAgent` | `phone_agent/agent.py` | 主循环：截图→定位→VLM推理→动作执行→写回 |
| `PageClassifier` | `phone_agent/memory/offline_explorer.py` | 从截图提取 page_type / summary / elements |
| `MemoryManager` | `phone_agent/memory/memory_manager.py` | 连接会话记忆与空间图谱的高层编排器 |
| `SpatialGraphMemory` | `phone_agent/memory/spatial_graph_memory.py` | 页面抽象、定位、目标推断、路径规划、修复 |
| `GraphStore` | `phone_agent/memory/graph_store.py` | Neo4j 适配器：UIState / Action / TaskTarget 读写 |
| `GraphRuntimeController` | `phone_agent/spatial/runtime_controller.py` | v4 运行时协调器：RuntimeDAG 生命周期管理 |
| `ModelAdapter` | `phone_agent/model/adapters.py` | 5 种 VLM 模型的消息与输出格式适配 |
| `ActionHandler` | `phone_agent/actions/handler*.py` | 动作解析 → ADB / HDC / XCTest 操作 |
| `ModelProtocolBridge` | `phone_agent/model/protocol_bridge.py` | 5 种模型原生格式 → DeviceActionIR 归一化 |
| `SpatialModelBridge` | `phone_agent/spatial/model_bridge.py` | SemanticActionIR → DeviceActionIR → 可执行动作 |
| `ClarificationAgent` | `phone_agent/clarify.py` | 任务歧义检测，CLEAR / CLARIFY 判断 |
| `SpecGuard` | `phone_agent/agent.py` | 关键购物场景的高风险动作约束 |
| `GUITracer` | `phone_agent/tracer.py` | 操作轨迹录制与回放 |
| `OfflineExplorer` | `phone_agent/memory/offline_explorer.py` | VLM 驱动的离线自主探索，构建页面图 |
| `RetrievalGateway` | `phone_agent/memory/retrieval_gateway.py` | UI-Copilot 范式按需检索引擎 |

---

## 3. Agent 核心执行流程

### 3.1 主循环总览

```
PhoneAgent.run(task)
  │
  ├── memory_manager.start_task(task)          # 重置会话状态，提取联系人/偏好
  ├── _vlm_pre_plan(task)                       # VLM 预规划任务分解
  ├── memory_manager.set_vlm_plan(vlm_plan)     # 存储计划供图谱路由使用
  │
  └── for step in range(max_steps):
        └── _execute_step()                     # 每一步的核心逻辑
```

### 3.2 单步执行流程 `_execute_step()`

```
┌─────────────────────────────────────────────────────────────┐
│ Step 1: 截图 & 页面分类                                      │
│                                                             │
│  DeviceFactory.get_screenshot()                             │
│  → 计算 ui_hash                                             │
│  → PageClassifier 提取 page_type / summary / elements       │
│  → 构造 screen_dict                                         │
├─────────────────────────────────────────────────────────────┤
│ Step 2: 空间定位 & 记忆检索                                  │
│                                                             │
│  MemoryManager.locate_and_get_context(screen_dict, task)    │
│  → 委托 GraphRuntimeController:                             │
│    a. SpatialGraphMemory.locate() → PageBelief              │
│    b. GoalSpec.from_task() → 目标页面类型                   │
│    c. SpatialPlanner.plan() → RoutePlan (加权BFS)           │
│    d. 判断模式: navigate / verify_with_vlm / explore        │
├─────────────────────────────────────────────────────────────┤
│ Step 3: 模式分发                                             │
│                                                             │
│  ┌─ mode == "navigate" 且 confidence >= 0.7:               │
│  │  → 直接执行图谱 shortcut (跳过 VLM 推理)                 │
│  │  → _compile_spatial_shortcut_action()                   │
│  │  → ActionHandler.execute()                              │
│  │                                                          │
│  ├─ mode == "verify_with_vlm":                              │
│  │  → 图谱提供空间建议，VLM 验证具体目标                    │
│  │  → VLM 推理 + 空间上下文注入                            │
│  │                                                          │
│  └─ mode == "explore":                                      │
│     → 回退 VLM 截图推理                                     │
│     → 注入 get_injection_context() + 空间摘要              │
│     → ModelClient.request() → parse_action → execute       │
├─────────────────────────────────────────────────────────────┤
│ Step 4: 记忆更新 & 图谱写回                                  │
│                                                             │
│  memory_manager.add_step(thinking, action, app)             │
│  → 自动学习: 联系人、偏好、商品、约束                       │
│  memory_manager.update_state_and_transition(before→action)  │
│  → 缓存 pending transition                                  │
│                                                             │
│  [下一轮截图后]                                              │
│  → record_observation(before, action, after)                │
│  → PostconditionVerifier: expected vs observed              │
│  → repair(): retry / rollback / replan / ask_user / VLM    │
└─────────────────────────────────────────────────────────────┘
```

### 3.3 三种空间模式详解

| 模式 | 触发条件 | 执行行为 |
|---|---|---|
| **navigate** | 图谱定位当前页面 + 找到到目标的可信路径 + confidence ≥ 0.7 | 直接执行路径第一条边，**跳过 VLM 推理** |
| **verify_with_vlm** | 图谱提供空间建议，但需要 VLM 确认具体目标（如"从搜索结果选正确商品"） | 注入图谱上下文后调用 VLM 验证 |
| **explore** | 页面未知 / 无可用边 / 路线不可信 / 被过滤器拒绝 | 完全回退到 VLM 截图推理 |

**关键设计**: navigate 模式只执行路径**第一条边**，不一次性重放整条轨迹。下一步执行后必须重新截图、重新定位、重新规划。这避免了移动 App 中弹窗、推荐流、活动页等扰动导致的轨迹失效。

### 3.4 VLM 推理路径 (explore 模式)

当图谱无法提供可信路线时，Agent 进入完整 VLM 推理路径：

```
1. 适配器构造消息 (Adapter.build_messages)
   └── 注入上下文:
       ├── [SpatialGraph] 摘要 (当前位置、路线、修复提示)
       ├── progress_summary() (1-2行进度)
       ├── RetrievalGateway 按需检索结果 (3步冷却)
       └── SpecGuard 关键场景提示
2. ModelClient.request(context) → VLM 响应
3. 解析动作 (parse_action / specialized_handler.parse_response)
4. SpecGuard 校验高风险操作
5. ActionHandler.execute() → 设备操作
```

---

## 4. 空间图谱 (AMSG v4)

### 4.1 设计理念

AMSG (Active Mobile Spatial Graph) 的核心思想：**不训练新模型，通过运行时结构化中间层赋予 Agent 空间认知能力**。

```
当前截图
  → 页面抽象 PageState
  → 页面定位 PageBelief
  → 任务目标 GoalSpec
  → 路线 RoutePlan (加权BFS)
  → 下一步动作或探索回退
```

VLM 负责视觉理解和无路线时的探索。图谱回答三个空间问题：
1. **我现在在哪个页面？** (PageBelief)
2. **这个任务应该去哪个页面类型？** (GoalSpec)
3. **图中是否有可信的下一步边？** (RoutePlan.next_action)

### 4.2 核心数据结构

#### PageState — 页面节点

```python
@dataclass(frozen=True)
class PageState:
    state_id: str           # e.g. "state_taobao_home_abc123"
    app: str                # "淘宝", "jd"
    page_type: str          # "home", "search_input", "search_result", "product_detail"
    summary: str            # 页面摘要
    landmarks: tuple[str]   # ("search_bar", "bottom_tabs", "product_cards")
    affordances: tuple[str] # ("tap_search", "open_product", "filter")
    slots: dict[str, str]   # {"query": "iPhone 17"}
    risk_level: str         # "normal", "medium", "high"
    semantic_signature: str # "app|page_type|landmarks|affordances|slots"
```

**语义签名** (`semantic_signature`) 是页面身份的唯一标识符，由 `app + page_type + landmarks + affordances + slots` 管道连接而成。这使得不同截图只要语义一致就映射到同一节点，避免图谱膨胀。

#### TransitionEdge — 转移边

```python
@dataclass(frozen=True)
class TransitionEdge:
    source_id, target_id: str
    action_type: str           # "Tap", "Swipe", "Back", "Compound"
    action_target: str         # "search bar", "product card"
    action_params: dict        # 可回放的动作参数
    postcondition: str         # 预期目标页面类型
    success_count, fail_count: int
    confidence: float
    weighted_cost: float       # = cost + fail_rate*3.0 + risk_penalty - confidence_bonus
```

`weighted_cost` 的设计：购物场景中，失败多、高风险、低置信的边在规划中变贵，自然被 BFS 排序到后面。

#### PageBelief — 定位结果

```python
@dataclass(frozen=True)
class PageBelief:
    current_state_id: str
    candidates: tuple[PageBeliefCandidate]
    confidence: float
    is_novel: bool    # True = 无图谱匹配，全新页面
```

#### GoalSpec — 目标推断

```python
@dataclass(frozen=True)
class GoalSpec:
    domain: str                    # "shopping" / "general"
    target_page_types: tuple[str]  # ("search_result", "product_detail")
    slots: dict[str, str]          # {"query": "机械键盘"}
    forbidden_actions: tuple[str]  # ("pay", "submit_order", "payment")
```

`GoalSpec.from_task()` 从自然语言任务中提取目标，**自动过滤否定约束**（如"不要加购""不要支付"不会变成规划目标）。

#### RuntimeDAG — 运行时编译图

```python
@dataclass
class RuntimeDAG:
    plan_id: str
    app: str
    goal_spec: GoalSpec
    nodes: dict[str, PageState]
    edges: dict[str, list[TransitionEdge]]
    route: list[TransitionEdge]    # 编译好的执行路线
    current_index: int             # 当前执行位置
    coverage_gaps: list[str]       # 未覆盖的页面类型
```

RuntimeDAG 将 Neo4j 中的 canonical 图编译为轻量的内存 DAG，支持 step-through 执行，避免每一步都查询 Neo4j。

### 4.3 图谱构建过程

#### 4.3.1 从截图到 PageState

`SpatialGraphMemory.build_page_state()` 将 screen_dict 抽象为 PageState：

```
screen_dict = {
    "ui_hash": "a3f2b1...",
    "app": "淘宝",
    "page_type": "search_result",
    "summary": "搜索结果页，显示iPhone 17相关商品",
    "elements": {"search_bar": True, "product_cards": [...]}
}
    ↓
build_page_state()
    ↓  关键词匹配推断 page_type
    ↓  从 page_type 模板提取 landmarks / affordances
    ↓  正则提取 slots (query, product, price, color, storage)
    ↓  计算 semantic_signature
PageState(
    state_id="state_taobao_search_result_a3f2b1",
    app="淘宝", page_type="search_result",
    landmarks=("search_bar", "product_cards", "filter_bar"),
    affordances=("tap_product", "filter", "sort", "swipe_up"),
    slots={"query": "iPhone 17"},
    semantic_signature="淘宝|search_result|search_bar,product_cards,filter_bar|..."
)
```

#### 4.3.2 页面定位 (locate)

`SpatialGraphMemory.locate(screen, task)` 的执行流程：

```
1. 从 screen_dict 构造 runtime PageState
2. 精确匹配: semantic_signature → GraphStore.get_state_by_semantic()
3. 模糊匹配: (app, page_type) → find_v4_page_candidates()
4. 相似度重排:
   - app 一致性: +0.35
   - page_type 一致性: +0.35
   - landmark 重叠: +0.20
   - affordance 重叠: +0.10
5. 阈值判断: score >= 0.65 → 匹配; < 0.65 → is_novel=True
6. 返回 PageBelief
```

#### 4.3.3 路线规划 (plan)

`SpatialGraphMemory.plan(belief, goal_spec)` 执行加权最短路径搜索：

```
1. 若当前 page_type 已满足目标 → goal_reached
2. 加载当前节点的出边 (仅同 App 转移)
3. 对每条候选边执行 plausibility 过滤:
   - 拒绝 Type/Input 边 (文本是任务槽位，不可泛化)
   - 拒绝可疑跳跃 (search_result → cart/checkout 直接跳过)
   - 拒绝语义不匹配的边
4. 加权 BFS: weighted_cost = base_cost + fail_rate*3.0 + risk_penalty - confidence_bonus
5. 只返回第一条边作为 next_action (不重放整条轨迹)
```

### 4.4 Neo4j 图 Schema

```
TaskTarget ──STARTS_AT──→ UIState ──NEXT_ACTION──→ Action ──PRODUCES──→ UIState
     │                        ↑                         │
     └──ENDS_AT──→ UIState    │                         │
                              │                         ├──→ FunctionalityItem
                              │                         │     (功能项)
                              └──EXPOSES_FUNCTION──→ FunctionalityItem ──MEMBER_OF──→ FunctionalityCluster
                                                                              │
                                                                              └──LEADS_TO──→ UIState
```

**节点类型**:
- `UIState`: 页面抽象 (state_id, app, page_type, semantic_signature, landmarks, affordances)
- `Action`: 动作描述 (type, intent, semantic_target, region, expected_postcondition)
- `TaskTarget`: 任务目标 (task, platform, success_rate)
- `FunctionalityItem`: 自发现功能项 (name, canonical_role, type)
- `FunctionalityCluster`: 功能聚类 (cluster_name)

**关系属性**:
- `NEXT_ACTION`: confidence, frequency (成功次数), fail_count
- `PRODUCES`: success_count, fail_count, success_rate

### 4.5 三类数据来源

| 来源 | 入口 | 价值 | 风险 |
|---|---|---|---|
| **人工轨迹** | `manual_trajectory_importer.py` | 真实任务路线 | 可能混入任务槽位 |
| **离线自动探索** | `OfflineExplorer` + `import_exploration.py` | 页面和转移覆盖 | 弹窗/噪声页面扩张 |
| **在线执行** | `record_observation()` 运行时 | 真实成功/失败更新 | 页面合并和写入膨胀 |

**数据流水线**:
```
离线探索 → pages.json + transitions.json
  → import_exploration_staging() → 暂存图
  → canonicalize_pages() → 合并页面变体
  → promote_staging_to_canonical() → 校验 + 持久化到 Neo4j
```

---

## 5. 记忆模块设计

### 5.1 四层记忆架构

```
┌──────────────────────────────────────────────────────────┐
│ Layer 1: UnifiedSessionState (会话级，内存)              │
│  商品列表、步骤记录、用户约束、进度追踪                  │
│  → 每步注入 progress_summary() (1-2行)                  │
├──────────────────────────────────────────────────────────┤
│ Layer 2: MemoryStore (跨会话，FAISS 向量存储)            │
│  用户偏好、联系人、任务模式、App使用、商品偏好           │
│  → 余弦相似度检索 + 去重 (threshold=0.85)               │
├──────────────────────────────────────────────────────────┤
│ Layer 3: SpatialGraphMemory + GraphStore (Neo4j)         │
│  页面图、转移边、任务轨迹                                │
│  → locate() + plan() 每步调用                           │
├──────────────────────────────────────────────────────────┤
│ Layer 4: RetrievalGateway (按需检索)                     │
│  不确定性/对比/计算/查找信号触发                         │
│  → 3步冷却期，避免过度注入                              │
└──────────────────────────────────────────────────────────┘
```

### 5.2 MemoryStore (FAISS 向量存储)

**存储结构**: `memory_db/{user_id}/`
- `memories_meta.json` — 记忆元数据 (content, type, importance, timestamps)
- `embeddings.npy` — NumPy 向量数组

**嵌入模型**: BigModel Embedding-3 API (2048d)，失败时回退 SimpleEmbedder (128d hash-based)

**记忆类型**:
| 类型 | 内容 |
|---|---|
| `USER_PREFERENCE` | 用户偏好 (颜色、品牌、预算) |
| `CONTACT` | 联系人信息 |
| `TASK_PATTERN` | 任务模式 (常用 App 流程) |
| `APP_USAGE` | App 使用习惯 |
| `TASK_HISTORY` | 完成的历史任务 |
| `PRODUCT_PREFERENCE` | 商品偏好 |
| `PRICE_SENSITIVITY` | 价格敏感度 |
| `BRAND_AFFINITY` | 品牌偏好 |
| `UI_STATE` / `UI_TRANSITION` | 页面状态和转移 |

**去重机制**: 添加新记忆前，对同类型记忆计算余弦相似度。≥ 0.85 则更新已有记忆，不创建副本。

**检索排序**: `score = similarity × 0.7 + importance × 0.3`

**容量限制**: 最大 10,000 条。超限时驱逐低重要度 / 最旧的记忆。

### 5.3 UnifiedSessionState (会话状态)

合并了原来 3 个独立组件 (KnowledgeBase + SessionMemory + StateManager) 为**单一信息源**：

```python
class UnifiedSessionState:
    task: str               # 当前任务
    platform: str           # 购物平台
    products: list[Product] # 观察到的商品 (模糊名称匹配)
    steps: list[StepRecord] # 步骤记录
    constraints: dict       # 用户约束 (预算、品牌)
    # 进度追踪
    subtasks_completed: list[str]
    subtasks_remaining: list[str]
    # 停滞检测
    _consecutive_same_actions: int  # 同一动作连续次数
    # 推理存档
    _reasoning_archive: list[str]   # VLM thinking 存档
```

**关键能力**:
- `progress_summary()` — 每步注入的 1-2 行轻量进度摘要
- `retrieve_by_keywords()` — 按需关键词检索 (推理存档 + 商品)
- `is_stagnating()` — 连续 2+ 次相同动作 → 停滞信号
- `should_compress()` — 每 5 步触发会话压缩

### 5.4 RetrievalGateway (按需检索)

**UI-Copilot 范式**: 不总是注入全部记忆，而是监控 VLM thinking 中的不确定性信号，按需触发检索。

**触发信号**:
| 信号类型 | 关键词 | 检索意图 |
|---|---|---|
| 不确定性 | "不确定", "忘记了", "not sure" | 回忆最近步骤 |
| 对比 | "对比", "比较", "性价比" | 商品比较表 |
| 计算 | "总共", "合计", "total" | 购物车汇总 |
| 查找 | "价格是多少", "什么颜色" | 商品详情查找 |
| 停滞 | 同一页面重复动作 | 步骤回顾 + 建议 |

**冷却期**: 3 步。避免连续注入大量记忆干扰 VLM 决策。

### 5.5 自动学习机制

MemoryManager 在执行过程中自动从 Agent 行为中学习：

| 学习类型 | 来源 | 存储 |
|---|---|---|
| 联系人提取 | 任务文本 + VLM thinking | MemoryStore (CONTACT) |
| App 使用追踪 | current_app + task 分类 | MemoryStore (APP_USAGE) |
| 联系人-App 绑定 | 频率统计 | MemoryStore (CONTACT_APP_BINDING) |
| 商品提取 | VLM thinking 中的结构化格式 | UnifiedSessionState (Product) |
| 约束提取 | "不超过 ¥5000" 等 | UnifiedSessionState (constraints) |
| 搜索模式 | 搜索关键词分类 | MemoryStore (TASK_PATTERN) |
| 用户纠正 | `add_correction()` 调用 | MemoryStore (USER_CORRECTION) |

---

## 6. Agent 主循环与空间图谱和全局记忆的协作

### 6.1 协作时序

```
每步执行的协作链:

PhoneAgent                    MemoryManager              GraphRuntimeController         SpatialGraphMemory
    │                              │                            │                              │
    │─── get_screenshot ──────────>│                            │                              │
    │    (DeviceFactory)           │                            │                              │
    │                              │                            │                              │
    │─── PageClassifier ──────────>│                            │                              │
    │    → screen_dict             │                            │                              │
    │                              │                            │                              │
    │─── locate_and_get_context ──>│── delegate ──────────────>│                              │
    │                              │                            │── should_use_classifier? ───>│
    │                              │                            │<── True/False ───────────────│
    │                              │                            │                              │
    │                              │                            │── locate(screen) ───────────>│
    │                              │                            │<── PageBelief ───────────────│
    │                              │                            │                              │
    │                              │                            │── infer_goal(task) ─────────>│
    │                              │                            │<── GoalSpec ─────────────────│
    │                              │                            │                              │
    │                              │                            │── plan(belief, goal) ───────>│
    │                              │                            │<── RoutePlan ────────────────│
    │                              │                            │                              │
    │                              │                            │── 判断模式 ─────────────────>│
    │                              │                            │  navigate / verify / explore │
    │<── context_data ─────────────│<── context_data ──────────│                              │
    │                              │                            │                              │
    │─── 模式分发 ────────────────>│                            │                              │
    │  navigate: 直接执行图谱动作  │                            │                              │
    │  verify: VLM验证图谱建议     │                            │                              │
    │  explore: VLM截图推理        │                            │                              │
    │                              │                            │                              │
    │─── add_step(thinking,action)>│                            │                              │
    │    → 自动学习                │                            │                              │
    │    → progress_summary注入    │                            │                              │
    │    → RetrievalGateway检测    │                            │                              │
    │                              │                            │                              │
    │─── update_state_and ────────>│                            │                              │
    │    transition()              │                            │                              │
    │    → 缓存 pending            │                            │                              │
    │                              │                            │                              │
    │ [下一轮]                     │                            │                              │
    │─── record_observation ──────>│── record ────────────────>│                              │
    │    (before→action→after)     │                            │                              │
    │                              │                            │── repair(expected,observed)─>│
    │                              │                            │<── RepairDecision ───────────│
```

### 6.2 上下文注入策略

**Memory Decoupling (UI-Copilot 范式)**: 取代旧的 4 层推送注入，采用"最小化常驻 + 按需检索"。

每步注入到 VLM prompt 的内容（按优先级）：

```
1. [Always-on] progress_summary()          # 1-2 行：当前步骤/已完成子任务/商品数
2. [Spatial]   [SpatialGraph] 摘要         # 当前位置 + 路线 + 修复提示
3. [On-demand] RetrievalGateway 结果       # 仅当检测到不确定性/对比/计算信号
4. [Guard]     SpecGuard 关键场景提示      # 仅当检测到 SKU/结算/支付等高风险页面
5. [Session]   用户原始任务                # 防止跨会话图谱数据覆盖真实意图
```

### 6.3 图谱写回与纠错

图谱写回采用**延迟一拍**策略：

```
当前步骤: 执行动作 action_t
  → update_state_and_transition() 缓存:
    - before_page (当前页面)
    - action_t (执行的动作)
    - expected_postcondition (预期下一页面)

下一轮: 截图定位到 after_page
  → record_observation(before, action_t, after)
  → PostconditionVerifier 比较:
    - expected: search_input
    - observed: search_result  ← 不匹配!
  → repair() 返回修复决策:
    - retry:       重新执行上一步
    - rollback:    回退到上一页面
    - replan:      从当前页面重新规划
    - ask_user:    请求用户介入 (高风险页面优先)
    - fallback_to_vlm: 放弃图谱，完全交给 VLM
  → 写入成功/失败边到 Neo4j
```

### 6.4 VLM-Graph 协同的三种模式

| 模式 | 图谱角色 | VLM 角色 | 典型场景 |
|---|---|---|---|
| **navigate** | 主控：提供动作 | 不参与 | 首页→搜索栏、返回、启动App |
| **verify_with_vlm** | 顾问：提供空间建议 | 验证者：确认具体目标 | 搜索结果→商品详情（需选对商品） |
| **explore** | 上下文：注入位置信息 | 主控：视觉推理决策 | 未知页面、无图谱边、路线不可信 |

`verify_with_vlm` 模式的设计动机：图谱知道"应该点击搜索结果中的某个商品"，但**哪个商品**需要 VLM 根据用户任务语义判断。这是空间认知和视觉语义的最佳协同点。

---

## 7. 设备与模型抽象

### 7.1 设备工厂模式

```python
DeviceFactory (抽象工厂)
  ├── ADBFactory     → adb shell input / screencap
  ├── HDCFactory     → hdc shell uitest / screenshot
  └── IOSFactory     → WDA HTTP API / W3C pointer actions
```

Agent 上层只关心 `tap(x, y)` / `swipe(x1, y1, x2, y2)` / `type(text)` / `back()` / `launch_app(pkg)` 等抽象操作。

### 7.2 双层协议桥

```
VLM 输出 (模型原生格式)
  │
  ├── AutoGLM: do(Tap, 500, 300) / finish(...)
  ├── UI-TARS: Thought:/Action: <point>x y</point>
  ├── Qwen-VL: tool_call JSON
  ├── MAI-UI: <thinking>...</thinking>
  └── GUI-Owl: action + action_history
  │
  ↓ ModelProtocolBridge
  │
  DeviceActionIR {
    action_type: "Tap",
    coordinate: (500, 300),    # 已归一化到设备像素
    params: {...}
  }
  │
  ↓ SpatialModelBridge (图谱方向)
  │
  SemanticActionIR {
    action_type: "Tap",
    semantic_target: "search bar",
    region: "top",
    expected_postcondition: "search_input"
  }
  │
  → 编译为 DeviceActionIR → ModelProtocolBridge → 可执行动作
```

### 7.3 坐标系统归一化

| 模型 | 坐标空间 | 归一化方式 |
|---|---|---|
| AutoGLM | `[0, 1000]` | `x_device = x_norm / 1000 * screen_width` |
| UI-TARS | 绝对像素 (smart_resize 空间) | `smart_resize()` 反算 |
| Qwen-VL | 绝对像素 | 直接使用 |
| MAI-UI | `[0, 1000]` (SCALE_FACTOR=999) | 同 AutoGLM |
| GUI-Owl | 绝对像素 + coordinate2 | 特殊处理 swipe 双坐标 |

---

## 8. 离线图谱构建

### 8.1 OfflineExplorer

VLM 驱动的自主探索系统，在真实购物 App 中自主导航以构建页面图：

```
1. 启动目标 App (淘宝/京东)
2. 截图 → VLM 决定下一步探索动作 → 执行
3. PageClassifier 分类每个新页面
4. 记录发现的 pages、elements、transitions
5. 覆盖率引导: 追踪缺失的 page_type 和转移
6. 安全约束: 阻止支付/结账/地址/登录操作
```

### 8.2 ActiveGraphBuilder

前沿评分探索，使用 AMSG frontier scoring 提示 VLM 探索未覆盖的页面区域：

```
frontier_score = novelty_bonus * transition_coverage_gap * functionality_gap
```

优先探索:
- 从未见过的页面类型
- 转移覆盖率低的页面
- 功能项缺失的页面

### 8.3 数据流水线

```
OfflineExplorer
  ↓ pages.json + transitions.json
  ↓
import_exploration_staging()
  ↓ 暂存图 (staging graph)
  ↓
canonicalize_pages()
  ↓ 合并页面变体 (相同 semantic_signature → 同一节点)
  ↓
quality_gate checks:
  - Schema 覆盖率
  - 缺失核心边检测
  - 跨 App 边检测
  ↓
promote_staging_to_canonical()
  ↓ 持久化到 Neo4j
```

---

## 9. 创新点分析

### 9.1 解决 Mobile Agent 核心痛点

| 问题 | 传统方案 | 本项目方案 |
|---|---|---|
| **页面迷失** | 纯视觉截图推理，不知道"我在哪" | AMSG PageBelief 提供结构化页面定位 |
| **重复操作** | VLM 容易在推荐流/弹窗中循环 | 停滞检测 + 图谱边统计 + replan 机制 |
| **长任务遗忘** | 上下文窗口有限，多步后丢失早期信息 | UnifiedSessionState 持久化 + 按需检索 |
| **高风险操作** | VLM 可能直接下单 | SpecGuard (运行时) + PostconditionVerifier (执行后) 双重门 |
| **模型锁定** | 单一模型适配 | 5 种模型 + 双层协议桥，模型可热插拔 |

### 9.2 技术创新点

**1. Training-free 空间图谱 (AMSG)**

不训练新 VLM/GUI 模型，而是通过运行时中间层实现空间认知。这是一个重要的工程哲学：**空间能力来自结构化中间层，而非模型权重**。这使得系统可以兼容任何 OpenAI-compatible VLM，且随着 VLM 升级自动受益。

**2. Semantic Signature 页面身份**

用 `app|page_type|landmarks|affordances|slots` 管道签名作为页面唯一标识，而非截图 hash。这意味着：
- 不同截图只要语义一致就映射到同一节点
- 图谱天然抵抗 App 的 A/B 测试、广告插入、推荐流变化
- 图谱规模可控，不会因为每个弹窗都创建新节点

**3. Navigate / Verify / Explore 三模式协同**

不是"图谱全接管"或"VLM 全接管"，而是根据置信度动态选择：
- 高置信空间路径 → 直接执行 (快、省 token)
- 需要语义判断的转移 → 图谱建议 + VLM 验证
- 无图谱支持 → 完全 VLM 推理

**4. UI-Copilot 记忆解耦**

取代传统的"全量记忆注入 VLM prompt"，采用"最小常驻 + 按需检索"：
- 每步只注入 1-2 行进度摘要
- 只有检测到不确定性/对比/计算信号时才触发检索
- 3 步冷却期避免过度注入
- 减少 VLM 上下文噪声，提高决策质量

**5. 延迟一拍写回 + PostconditionVerifier**

图谱写回不在当前步骤完成，而是等下一轮截图确认真实结果后写回。PostconditionVerifier 比较预期 vs 观测，提供 5 种修复策略 (retry/rollback/replan/ask_user/VLM fallback)。这比"执行后假设成功"更鲁棒。

**6. 自发现功能层 (FunctionalityItem/Cluster)**

不预定义功能集合，而是从执行轨迹中自动提取功能项（如"搜索商品"、"筛选价格"、"加入购物车"），聚类为 FunctionalityCluster，与页面图关联。这使得图谱具有自我进化的功能描述能力。

**7. 多平台统一抽象**

DeviceFactory + ModelProtocolBridge + SpatialModelBridge 三层抽象，使得同一套空间图谱和记忆系统可以驱动 Android (ADB)、HarmonyOS (HDC)、iOS (XCTest) 三种平台，且支持 5 种不同 VLM 模型的热插拔。

### 9.3 与学术界方法的对比

| 维度 | AppAgent / CogAgent | DigiRL / Agent-E | 本项目 (ClawGUI-Agent) |
|---|---|---|---|
| 页面表示 | 截图 hash / XML | 截图 + accessibility tree | Semantic Signature (语义签名) |
| 导航方式 | 纯 VLM 推理 | 训练专用策略模型 | Training-free 空间图谱 BFS |
| 记忆系统 | 无 / 简单 history | 无 | 四层记忆 (FAISS + Session + Neo4j + Retrieval) |
| 安全约束 | 无 | 有限 | SpecGuard + PostconditionVerifier |
| 多模型支持 | 单一模型 | 单一模型 | 5 种模型 + 双层协议桥 |
| 图谱构建 | 人工标注 | 训练数据 | 离线探索 + 在线执行 + 人工轨迹 |

---

## 10. 附录

### 10.1 环境变量

| 变量 | 默认值 | 用途 |
|---|---|---|
| `PHONE_AGENT_BASE_URL` | `http://localhost:8000/v1` | VLM API 端点 |
| `PHONE_AGENT_MODEL` | `autoglm-phone-9b` | 模型名称 |
| `PHONE_AGENT_API_KEY` | `EMPTY` | API 密钥 |
| `PHONE_AGENT_MAX_STEPS` | `100` | 每任务最大步数 |
| `PHONE_AGENT_DEVICE_ID` | auto-detect | 设备 ID |
| `PHONE_AGENT_DEVICE_TYPE` | `adb` | `adb` / `hdc` / `ios` |
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j 连接 |
| `NEO4J_DATABASE` | `shopping` | Neo4j 数据库名 |

### 10.2 目录结构

```
phone_agent/
├── agent.py                    # 主 Agent (Android/HarmonyOS)
├── agent_ios.py                # iOS Agent
├── clarify.py                  # 歧义检测子 Agent
├── tracer.py                   # 轨迹录制
├── device_factory.py           # 设备工厂
│
├── actions/                    # 动作处理
│   ├── handler.py              # AutoGLM (默认)
│   ├── handler_uitars.py       # UI-TARS
│   ├── handler_qwenvl.py       # Qwen-VL
│   ├── handler_maiui.py        # MAI-UI
│   ├── handler_guiowl.py       # GUI-Owl
│   └── handler_ios.py          # iOS
│
├── model/                      # VLM 模型层
│   ├── adapters.py             # 5 种模型适配器
│   ├── client.py               # OpenAI-compatible 客户端
│   └── protocol_bridge.py      # 模型协议桥
│
├── memory/                     # 记忆子系统
│   ├── memory_manager.py       # 记忆编排中枢
│   ├── memory_store.py         # FAISS 向量存储
│   ├── graph_store.py          # Neo4j 图存储
│   ├── spatial_graph_memory.py # 空间图谱记忆
│   ├── retrieval_gateway.py    # 按需检索
│   ├── offline_explorer.py     # 离线探索
│   ├── embedding_client.py     # 嵌入客户端
│   ├── task_index.py           # FAISS 任务索引
│   └── core/                   # 核心数据模型
│       ├── unified_state.py    # 统一会话状态
│       ├── product.py          # 商品模型
│       └── step_record.py      # 步骤记录
│
├── spatial/                    # AMSG v4 空间图谱
│   ├── core.py                 # 核心类型
│   ├── runtime_controller.py   # 运行时控制器
│   ├── planner.py              # BFS 路径规划
│   ├── semantics.py            # 页面语义提取
│   ├── model_bridge.py         # 空间模型桥
│   ├── verifier.py             # 后置条件验证
│   ├── active_builder.py       # 活跃图构建
│   ├── functionality.py        # 功能项提取
│   ├── functionality_cluster.py# 功能聚类
│   ├── schema_registry.py      # Schema 注册
│   └── schemas/                # 领域 Schema
│       └── shopping.yaml       # 购物领域定义
│
├── config/                     # 配置
│   ├── apps.py                 # 188 个 App 包名映射
│   ├── timing.py               # 延迟配置
│   ├── shopping_config.py      # 购物配置
│   ├── prompts*.py             # 模型专用 Prompt
│   └── i18n.py                 # 国际化
│
├── adb/                        # Android ADB
├── hdc/                        # HarmonyOS HDC
└── xctest/                     # iOS XCTest/WDA
```
