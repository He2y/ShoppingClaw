# ClawGUI-Agent 系统架构文档

> **版本**: v4 (AMSG Runtime)  
> **最后更新**: 2026-05-28  
> **适用读者**: 不熟悉本项目的研究者、开发者

---

## 目录

1. [项目定位与研究贡献](#1-项目定位与研究贡献)
2. [系统架构总览](#2-系统架构总览)
3. [核心模块设计](#3-核心模块设计)
4. [Agent 主流程](#4-agent-主流程)
5. [空间图谱 (AMSG) 建图管线](#5-空间图谱-amsg-建图管线)
6. [全局记忆系统](#6-全局记忆系统)
7. [运行时协作：Agent × 空间图谱 × 记忆](#7-运行时协作agent--空间图谱--记忆)
8. [创新点与研究价值](#8-创新点与研究价值)

---

## 1. 项目定位与研究贡献

### 1.1 解决的问题

当前 Mobile GUI Agent 研究面临三个核心瓶颈：

| 瓶颈 | 具体表现 | ClawGUI-Agent 的应对 |
|------|---------|---------------------|
| **VLM 推理成本高** | 每一步都需要截图→VLM→动作，单任务 10-50 步，延迟和费用不可忽视 | 空间图谱导航模式：已知路径直接跳过 VLM，0 推理成本执行 |
| **跨会话记忆缺失** | 每次任务从零开始，不学习用户偏好，不复用已探索路径 | 三层记忆架构：向量语义记忆 + Neo4j 图记忆 + 会话状态 |
| **购物安全风险** | Agent 可能误操作付款、下单错误规格 | SpecGuard 机制 + 风险分级 + VLM 验证门 |

### 1.2 核心理念

```
传统 Agent:   截图 → VLM推理 → 动作 → 截图 → VLM推理 → 动作 → ...（每步都依赖VLM）

ClawGUI-Agent: 截图 → 图谱定位 ─┬→ [已知路径] → 图谱导航 → 直接执行（跳过VLM）
                               └→ [未知场景] → VLM推理 → 执行 → 同时建图（扩展图谱）
```

这构成了一个**自进化闭环**：Agent 每一次执行都在扩展图谱，使未来的同类任务越来越快。

---

## 2. 系统架构总览

### 2.1 架构图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           ClawGUI-Agent System                              │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                        Entry Layer                                   │   │
│  │   main.py (CLI)          webui.py (Gradio)         nanobot (Chat)   │   │
│  └──────────────────────────────┬───────────────────────────────────────┘   │
│                                 │                                           │
│  ┌──────────────────────────────▼───────────────────────────────────────┐   │
│  │                      Agent Orchestrator                              │   │
│  │                                                                      │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐   │   │
│  │  │  PhoneAgent   │  │IOSPhoneAgent │  │  ClarificationAgent     │   │   │
│  │  │  (Android/    │  │(iOS XCTest)  │  │  (购物场景主动澄清)       │   │   │
│  │  │   HarmonyOS)  │  │              │  │                          │   │   │
│  │  └──────┬───────┘  └──────────────┘  └──────────────────────────┘   │   │
│  └─────────┼────────────────────────────────────────────────────────────┘   │
│            │                                                                │
│  ┌─────────▼────────────────────────────────────────────────────────────┐   │
│  │                     Core Subsystems                                  │   │
│  │                                                                      │   │
│  │  ┌─────────────────┐  ┌────────────────┐  ┌──────────────────────┐  │   │
│  │  │  Model Layer     │  │ Action Layer   │  │  Memory Layer        │  │   │
│  │  │                  │  │                │  │                      │  │   │
│  │  │  ModelClient     │  │ ActionHandler  │  │  MemoryManager       │  │   │
│  │  │  5× Adapters     │  │ 5× Handlers   │  │  ├─ MemoryStore      │  │   │
│  │  │  ProtocolBridge  │  │ SpecGuard     │  │  ├─ GraphStore        │  │   │
│  │  │                  │  │                │  │  ├─ SpatialGraphMem  │  │   │
│  │  └────────┬─────────┘  └───────┬────────┘  │  ├─ UnifiedState    │  │   │
│  │           │                    │            │  └─ RetrievalGW     │  │   │
│  │           │                    │            └──────────┬───────────┘  │   │
│  │           │                    │                       │              │   │
│  │  ┌────────▼────────────────────▼───────────────────────▼───────────┐  │   │
│  │  │                    Spatial Layer (AMSG v4)                      │  │   │
│  │  │                                                                │  │   │
│  │  │  RuntimeController  │ PageClassifier  │ SchemaRegistry         │  │   │
│  │  │  SpatialPlanner     │ SemanticExtract │ FunctionalityCluster   │  │   │
│  │  │  SpatialModelBridge │ ActiveBuilder   │ PostconditionVerifier  │  │   │
│  │  └────────────────────────────────────────────────────────────────┘  │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                     Device Layer                                     │   │
│  │   DeviceFactory → ADB (Android) │ HDC (HarmonyOS) │ XCTest (iOS)   │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                     Storage Layer                                    │   │
│  │   Neo4j (图谱)  │  FAISS (向量索引)  │  JSON/NumPy (本地持久化)      │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 模块文件映射

| 层级 | 路径 | 核心文件 |
|------|------|---------|
| Agent 编排 | `phone_agent/` | `agent.py` (66K), `agent_ios.py`, `clarify.py`, `device_factory.py` |
| 模型层 | `phone_agent/model/` | `adapters.py`, `client.py`, `protocol_bridge.py` |
| 动作层 | `phone_agent/actions/` | `handler.py` + 4 个特化 handler |
| 记忆层 | `phone_agent/memory/` | `memory_manager.py` (93K), `graph_store.py` (66K), `spatial_graph_memory.py` (102K) |
| 空间层 | `phone_agent/spatial/` | 19 个模块，含 `runtime_controller.py`, `core.py`, `functionality.py` |
| 设备层 | `phone_agent/{adb,hdc,xctest}/` | 各含 `connection.py`, `device.py`, `input.py`, `screenshot.py` |
| 配置层 | `phone_agent/config/` | 提示词模板 × 5 模型 × 2 语言，购物配置，应用映射 |

---

## 3. 核心模块设计

### 3.1 Model Layer — 多模型适配

系统支持 5 种 VLM，通过 **Adapter Pattern** 统一接口：

```
           ┌─── AutoGLMAdapter ─── [0, 1000] 归一化坐标
           │
           ├─── UITarsAdapter ──── smart_resize 像素空间
           │
ModelType ─┤─── QwenVLAdapter ──── [0, 999] 归一化 + tool_call JSON
           │
           ├─── MAIUIAdapter ───── [0, 999] + <thinking> 嵌套
           │
           └─── GUIOwlAdapter ──── [0, 999] + 文本操作历史
```

**每个 Adapter 负责**：
1. `get_system_prompt()` — 模型专属提示词（含坐标系声明、动作格式定义）
2. `build_messages()` — 构建对话消息（处理图片数量限制、历史压缩）
3. `parse_response()` — 解析模型输出为 `(thinking, action)` 二元组

**ProtocolBridge** 将所有模型的原生坐标统一为 `DeviceActionIR`：

```python
@dataclass
class DeviceActionIR:
    action_type: str              # "click", "type", "scroll", "system_button", ...
    coordinate: tuple[float, float] | None
    coordinate_space: str          # "normalized_999", "normalized_1000", "absolute", ...
    screen_size: tuple[int, int]
    source_model: str
    text: str | None = None
    metadata: dict = field(default_factory=dict)
```

坐标转换路径：**模型原生空间 → DeviceActionIR → absolute 像素 → 设备执行**。

**消息构建策略差异**：

| 策略 | 模型 | 特征 |
|------|------|------|
| 累积式 | AutoGLM | 保留完整对话历史，含多张截图 |
| 窗口式 | Qwen-VL, GUI-Owl | 每次重建，只含当前截图 + 文本操作历史 |
| 混合式 | MAI-UI, UI-TARS | 保留历史但限制图片数 (3/5 张) |

### 3.2 Action Layer — 动作解析与执行

```
VLM 输出 (各格式不同)
    │
    ▼
┌──────────────────┐
│ parse_response() │  ← 模型专属 handler 解析
│ 容错：markdown噪声、│
│ XML标签残缺、嵌套  │
└────────┬─────────┘
         │
         ▼
┌────────────────────┐
│ DeviceActionIR     │  ← ProtocolBridge 归一化
│ (模型无关中间表示)   │
└────────┬───────────┘
         │
         ▼
┌────────────────────┐
│ ActionHandler      │  ← 坐标转换 + 设备执行
│ execute(action)    │
│ ├─ Tap / Swipe     │
│ ├─ Type / Launch   │
│ ├─ Back / Home     │
│ └─ Interact (询问)  │
└────────────────────┘
```

**SpecGuard**（购物安全守卫）：
- 在 `spec_selection`、`checkout`、`payment` 页面激活
- 从用户任务中提取期望规格（颜色、容量、尺码）
- 执行前校验当前页面规格是否匹配
- 不匹配时阻断操作，触发 Interact 向用户确认

### 3.3 Device Layer — 三平台抽象

`DeviceFactory` 通过工厂模式统一三个平台的设备控制接口：

| 平台 | 后端 | 连接方式 | 截图 | 输入 |
|------|------|---------|------|------|
| Android | ADB | USB/TCP | `screencap` | `input tap/swipe/text` |
| HarmonyOS | HDC | USB | `snapshot` | `uitest click/swipe` |
| iOS | XCTest | WebDriverAgent HTTP | WDA `/screenshot` | WDA `/tap`, `/swipe` |

---

## 4. Agent 主流程

### 4.1 完整执行循环

```
用户任务 "去淘宝买 iPhone 17 Pro Max，银色 512G，加入购物车"
    │
    ▼
┌──────────────────────────────────────────────────────────────────┐
│ Phase 0: 初始化                                                  │
│                                                                  │
│  ① 解析模型类型 → 加载 Adapter + Handler                          │
│  ② MemoryManager.start_task(task)                                │
│     → 检索相似历史任务                                              │
│     → 提取用户偏好记忆                                              │
│  ③ ClarificationAgent.check_and_clarify(task, screenshot)        │
│     → 检测任务是否模糊（缺规格/缺平台）                               │
│     → 模糊则主动询问用户，重组任务文本                                 │
└──────────────────────────┬───────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│ Phase 1: 截图 & 感知                                              │
│                                                                  │
│  ① DeviceFactory.get_screenshot() → base64 图片                  │
│  ② DeviceFactory.get_current_app() → 当前应用名                   │
│  ③ 计算 state_id = MD5(screenshot)                               │
└──────────────────────────┬───────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│ Phase 2: 图谱定位 & 路由决策                                       │
│                                                                  │
│  ① PageClassifier(screenshot) → page_type + summary + elements  │
│  ② ScreenSemanticsExtractor → PageNode (landmarks, affordances, │
│     slots, risk_level)                                           │
│  ③ SpatialGraphMemory.locate() → PageBelief                     │
│     → 在 Neo4j 中匹配候选节点                                      │
│     → 返回 confidence + candidates                               │
│  ④ SpatialGraphMemory.plan() → RoutePlan                        │
│     → Dijkstra 最短路径规划                                        │
│     → 返回 mode: navigate / explore / goal_reached              │
│                                                                  │
│  路由判断:                                                        │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │ IF mode == "navigate" AND confidence ≥ 0.7                  │ │
│  │    AND risk_level != "high"                                 │ │
│  │ THEN → Phase 2a (图谱导航快速路径)                            │ │
│  │ ELSE → Phase 3 (VLM 推理路径)                                │ │
│  └─────────────────────────────────────────────────────────────┘ │
└──────────────┬──────────────────────────────┬────────────────────┘
               │                              │
               ▼                              ▼
┌──────────────────────────┐  ┌──────────────────────────────────┐
│ Phase 2a: 图谱导航        │  │ Phase 3: VLM 推理                 │
│ (跳过 VLM)                │  │                                  │
│                          │  │ ① Adapter.build_messages()       │
│ ① SpatialModelBridge     │  │    构建对话消息（含截图、历史）       │
│   .compile_direct_action │  │ ② 注入语义上下文                   │
│   → DeviceActionIR       │  │    (图谱建议 + 记忆 + 进度)        │
│ ② 转换为设备坐标          │  │ ③ ModelClient.request(messages)  │
│ ③ 直接执行                │  │    → 流式推理 (thinking + action)  │
│                          │  │ ④ 解析 + SpecGuard 校验           │
│                          │  │ ⑤ 执行动作                        │
└──────────────┬───────────┘  └───────────────┬──────────────────┘
               │                              │
               └──────────────┬───────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│ Phase 4: 状态更新 & 记忆记录                                       │
│                                                                  │
│  ① UnifiedSessionState.record_step(thinking, action, app)       │
│  ② MemoryManager.update_state_and_transition()                  │
│     → 记录页面转换 (source → target)                               │
│     → 更新边的 success/fail 计数                                   │
│     → 在线扩展空间图谱                                              │
│  ③ RetrievalGateway 检查是否需要触发按需检索                        │
│  ④ 检查终止条件 (finished / max_steps)                            │
│     → 未完成: 回到 Phase 1                                        │
│     → 完成: Phase 5                                              │
└──────────────────────────────┬───────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│ Phase 5: 任务结束                                                 │
│                                                                  │
│  ① MemoryManager.end_task(success, result)                      │
│  ② 提交轨迹到 Neo4j (commit_task_trajectory)                     │
│  ③ 更新 FAISS 任务索引                                            │
│  ④ 保存 pending_trajectories.json                                │
│  ⑤ 返回结果给用户                                                 │
└──────────────────────────────────────────────────────────────────┘
```

### 4.2 关键数据流

```
截图 (bytes)
  │
  ├─→ base64 编码 → VLM 输入
  │
  ├─→ MD5 哈希 → state_id (去重键)
  │
  └─→ PageClassifier → page_type + summary
                          │
                          ├─→ ScreenSemanticsExtractor → PageNode
                          │     (landmarks, affordances, slots, risk)
                          │
                          ├─→ SpatialGraphMemory.locate() → PageBelief
                          │     (当前在图谱中的位置)
                          │
                          └─→ SpatialGraphMemory.plan() → RoutePlan
                                (到目标的最短路径)
```

---

## 5. 空间图谱 (AMSG) 建图管线

### 5.1 AMSG (Active Mobile Spatial Graph) 概述

AMSG 是一个**语义级的应用导航图谱**，节点是页面状态，边是页面间的转换动作。与 UI 元素树不同，AMSG 工作在**功能语义层**，关注的是"这个页面能做什么"而非"页面上有哪些像素"。

```
                    AMSG 图谱示例 (淘宝)
                    
    ┌──────────┐  tap_search  ┌──────────────┐
    │   home   │────────────→│ search_input  │
    │ 首页推荐流 │             │ 搜索输入页     │
    └──────────┘             └──────┬───────┘
                                   │ type <query> + submit
                                   ▼
    ┌──────────────┐  tap_product ┌──────────────┐
    │search_result │────────────→│product_detail│
    │ 搜索结果列表  │             │ 商品详情页     │
    └──────────────┘             └──────┬───────┘
                                       │ tap_spec / add_to_cart
                                       ▼
    ┌────────────────┐  confirm  ┌──────────┐
    │spec_selection  │─────────→│   cart    │
    │ 规格选择弹窗    │           │ 购物车     │
    └────────────────┘           └──────────┘
    
    每个节点: PageNode(app, page_type, landmarks, affordances, slots, risk)
    每条边:   AffordanceEdge(intent, semantic_target, confidence, cost)
```

### 5.2 图谱数据结构

**PageNode (节点)**:
```python
@dataclass
class PageNode:
    node_id: str                    # 稳定标识符 (hash)
    app: str                        # 应用名 (taobao, jd, ...)
    domain: str                     # 领域 (shopping, utility, ...)
    page_type: str                  # 语义页面类型 (17 种)
    summary: str                    # 人类可读描述
    landmarks: tuple[str, ...]      # 关键 UI 要素 (搜索框, 商品卡片, ...)
    affordances: tuple[str, ...]    # 可用操作 (tap_search, add_to_cart, ...)
    slots: dict[str, str]           # 动态值 (query, product, price, color, ...)
    risk_level: str                 # normal / medium / high
    semantic_signature: str         # 语义指纹 (用于去重)
```

**AffordanceEdge (边)**:
```python
@dataclass
class AffordanceEdge:
    source_node: str                # 源页面 ID
    target_node: str                # 目标页面 ID
    intent: str                     # 语义意图 (click, type_text, scroll, ...)
    semantic_target: str            # 语义目标 (搜索框, 加入购物车按钮, ...)
    target_locator: dict            # 坐标/bbox 定位信息
    expected_postcondition: str     # 预期目标页面类型
    success_count: int              # 成功次数
    fail_count: int                 # 失败次数
    confidence: float               # 可靠性 (基于成功率)
    weighted_cost: float            # Dijkstra 路径权重
```

**风险分级**:

| Risk Level | 页面类型 | 策略 |
|-----------|---------|------|
| `high` | checkout, payment, address, login | 即使图谱有路径也必须 VLM 验证 |
| `medium` | spec_selection, cart, order_list | 图谱导航 + 降低置信度阈值 |
| `normal` | home, search_result, product_detail | 图谱导航可直接执行 |

### 5.3 建图管线 (5 阶段)

```
┌──────────────────────────────────────────────────────────────────┐
│                    AMSG 建图管线                                   │
│                                                                  │
│  Stage 1: 页面分类 (PageClassifier)                               │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  输入: base64 截图 + 任务描述                                  │ │
│  │  预处理: 裁掉状态栏(顶部4%) + 导航栏(底部12%)                   │ │
│  │  VLM 分类: 17 种页面类型 (home, search_input, product_detail..│ │
│  │  输出: ShoppingPageType + summary + elements dict            │ │
│  └─────────────────────────────┬───────────────────────────────┘ │
│                                │                                 │
│  Stage 2: 语义提取 (ScreenSemanticsExtractor)                     │
│  ┌─────────────────────────────▼───────────────────────────────┐ │
│  │  应用名归一化 (淘宝→taobao, 京东→jd)                           │ │
│  │  地标推断: elements keys → landmarks                          │ │
│  │  能力推断: element labels → affordances                       │ │
│  │  槽位提取: regex → query, product, price, color, storage      │ │
│  │  风险评估: page_type → risk_level                             │ │
│  │  输出: PageNode (完整语义节点)                                  │ │
│  └─────────────────────────────┬───────────────────────────────┘ │
│                                │                                 │
│  Stage 3: 节点去重 & 存储                                         │
│  ┌─────────────────────────────▼───────────────────────────────┐ │
│  │  规范键: (app, page_type, summary_prefix)                    │ │
│  │  合并策略: landmarks 取并集(上限12), 保留较短 summary           │ │
│  │  过滤: 丢弃 unknown/dialog/permission 临时页面                 │ │
│  │  持久化: Neo4j :UIState 节点                                  │ │
│  └─────────────────────────────┬───────────────────────────────┘ │
│                                │                                 │
│  Stage 4: 边构建 & 转换记录                                       │
│  ┌─────────────────────────────▼───────────────────────────────┐ │
│  │  TransitionEdge.from_action(source, target, action)          │ │
│  │  边富化: action_type → intent, 提取 region, semantic_target   │ │
│  │  语义边键去重: source_type|intent|target|region|target_type   │ │
│  │  复合动作合成: Type <query> + Submit → 带槽位模板边             │ │
│  │  边过滤: 移除自环, 过滤危险转换 (pay, logout)                   │ │
│  │  持久化: Neo4j :Action 节点 + NEXT_ACTION/PRODUCES 关系       │ │
│  └─────────────────────────────┬───────────────────────────────┘ │
│                                │                                 │
│  Stage 5: 功能发现 & 聚类 (v4 新增)                                │
│  ┌─────────────────────────────▼───────────────────────────────┐ │
│  │  FunctionalityExtractor: 从页面元素提取功能项                   │ │
│  │  分类: "functionality" (可操作) vs "data" (数据展示)            │ │
│  │  FunctionalityClusterer: 相似功能聚类 (threshold=0.58)        │ │
│  │  持久化: Neo4j :FunctionalityItem + :FunctionalityCluster    │ │
│  │  覆盖度量: FunctionalityCoverageMetrics                       │ │
│  └─────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

### 5.4 Schema 注册表

`spatial/schemas/` 下的 YAML 文件定义了领域知识先验：

```yaml
# shopping.yaml 示例
page_types:
  product_detail:
    risk: normal
    landmarks: [商品名, 价格, 图片, 评价数]
    affordances: [open_product, add_to_cart, open_spec]
    aliases: [detail, item, product]

transitions:
  - source: product_detail
    target: spec_selection
    intent: open_spec
    affordance: 规格选择
    risk: normal

app_aliases:
  taobao: [淘宝, 手机淘宝, com.taobao.taobao]
  jd: [京东, com.jd.commerce]
```

SchemaRegistry 在建图和路由时提供归一化和验证。

### 5.5 Neo4j 存储模型

```
(:UIState) -[:NEXT_ACTION {confidence, frequency, fail_count}]-> (:Action)
(:Action)  -[:PRODUCES {success_rate}]-> (:UIState)

(:TaskTarget) -[:STARTS_AT]-> (:UIState)
(:TaskTarget) -[:ENDS_AT]->   (:UIState)

(:UIState) -[:EXPOSES_FUNCTION]-> (:FunctionalityItem)
(:Action)  -[:IMPLEMENTS_FUNCTION]-> (:FunctionalityCluster)
(:FunctionalityCluster) -[:LEADS_TO]-> (:UIState)
```

---

## 6. 全局记忆系统

### 6.1 三层记忆架构

```
┌──────────────────────────────────────────────────────────────────┐
│                        Memory Architecture                       │
│                                                                  │
│  Layer 1: 向量语义记忆 (MemoryStore)                               │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  存储: FAISS IndexFlatIP + Embedding-3 API (2048维)          │ │
│  │  内容: 用户偏好、联系人、任务模式、应用习惯、品牌亲和度           │ │
│  │  检索: 0.7×余弦相似度 + 0.3×重要性评分                         │ │
│  │  去重: 相似度 ≥ 0.85 时合并而非新增                             │ │
│  │  持久化: memories_meta.json + embeddings.npy                  │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  Layer 2: 图结构记忆 (GraphStore + SpatialGraphMemory)             │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  存储: Neo4j 图数据库                                         │ │
│  │  内容: 页面状态、转换路径、任务轨迹、功能发现                     │ │
│  │  检索: 图遍历 + Dijkstra 最短路径                               │ │
│  │  路径权重: cost = 1.0 + failure_rate×3 + risk_penalty         │ │
│  │            - confidence×0.3                                   │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  Layer 3: 会话状态 (UnifiedSessionState)                           │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  生命周期: 单次任务                                            │ │
│  │  内容: 商品列表(含状态)、步骤历史、约束条件、进度追踪             │ │
│  │  停滞检测: 连续3步相同动作 → 触发按需检索                        │ │
│  │  进度摘要: 每轮注入 VLM (轻量级)                                │ │
│  └─────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

### 6.2 记忆类型

| 类型 | 枚举值 | 示例 | 来源 |
|------|--------|------|------|
| 用户偏好 | `USER_PREFERENCE` | "用户偏好自提而非快递" | 自动提取/用户纠正 |
| 联系人 | `CONTACT` | "张三: 微信好友" | 任务文本提取 |
| 任务模式 | `TASK_PATTERN` | "买手机常搜索淘宝再比价京东" | 历史轨迹学习 |
| 应用习惯 | `APP_USAGE` | "常用应用: 淘宝" | 操作统计 |
| 任务历史 | `TASK_HISTORY` | "在淘宝买了 iPhone, 10步完成" | 任务结束记录 |
| 用户纠正 | `USER_CORRECTION` | "用户说不要选默认地址" | Interact 反馈 |
| 品牌亲和 | `BRAND_AFFINITY` | "用户偏好 Apple 产品" | 长期行为分析 |

### 6.3 RetrievalGateway — 按需检索

不是每一步都注入记忆。RetrievalGateway 监听 VLM 的 thinking 输出，只在检测到以下信号时触发：

| 信号类型 | 触发词 | 检索动作 |
|---------|--------|---------|
| 不确定 | "不确定", "忘记了", "记不清" | 查找相关记忆上下文 |
| 对比 | "对比", "比较", "哪个更便宜" | 格式化已浏览商品价格表 |
| 计算 | "总共", "合计", "多少钱" | 汇总购物车金额 |
| 回溯 | "前面那个", "之前的", "价格是多少" | 检索最近步骤 + 相关商品 |
| 停滞 | 同一页面连续3步相同动作 | 注入近期步骤 + 替代方案 |

### 6.4 记忆生命周期

```
任务开始
  │
  ├→ 检索相似历史任务 (FAISS + Neo4j)
  ├→ 加载用户偏好记忆
  ├→ 初始化 UnifiedSessionState
  │
  ▼
每一步执行
  │
  ├→ 自动提取记忆 (从 thinking 和 action)
  │   → 联系人提取: "给[Name]发..." 
  │   → 应用提取: "打开[App]"
  │   → 偏好提取: "用户选择了..."
  │
  ├→ record_step() → 更新会话状态
  │
  ├→ RetrievalGateway 检查 → 按需注入
  │
  └→ 记录图谱转换 (source → target edge)
  
任务结束
  │
  ├→ 提交轨迹到 Neo4j
  ├→ 更新 FAISS 任务索引
  ├→ 提取任务级记忆
  └→ 持久化到磁盘
```

---

## 7. 运行时协作：Agent × 空间图谱 × 记忆

### 7.1 协作全景图

```
                          ┌────────────────────┐
                          │    PhoneAgent       │
                          │   _execute_step()   │
                          └─────────┬──────────┘
                                    │
          ┌─────────────────────────┼─────────────────────────┐
          │                         │                         │
          ▼                         ▼                         ▼
┌──────────────────┐  ┌──────────────────────┐  ┌──────────────────┐
│ Spatial Graph    │  │    VLM Inference      │  │ Memory System    │
│                  │  │                       │  │                  │
│ ● locate()       │  │ ● build_messages()   │  │ ● get_injection  │
│   → 定位当前页面  │  │ ● request()          │  │   _context()     │
│                  │  │ ● parse_response()   │  │   → 进度摘要      │
│ ● plan()         │  │                       │  │   → 商品上下文    │
│   → 规划路径     │  │                       │  │                  │
│                  │  │                       │  │ ● add_step()     │
│ ● compile_       │  │                       │  │   → 记录步骤      │
│   direct_action  │  │                       │  │                  │
│   → 图谱直接执行  │  │                       │  │ ● update_state_  │
│                  │  │                       │  │   and_transition  │
└────────┬─────────┘  └───────────┬───────────┘  └────────┬─────────┘
         │                        │                       │
         │    ┌───────────────────┘                       │
         │    │  注入图谱语义上下文                          │
         │    │  + 记忆上下文                               │
         │    │                                           │
         └────┼───────────────────────────────────────────┘
              │   执行结果反馈 → 同时更新图谱和记忆
              ▼
       ┌──────────────┐
       │ Device Layer  │
       │ (ADB/HDC/iOS) │
       └──────────────┘
```

### 7.2 三种运行模式

| 模式 | 触发条件 | VLM 调用 | 图谱角色 | 记忆角色 |
|------|---------|---------|---------|---------|
| **Navigate** | 图谱有高置信度路径 (≥0.7) + 非高风险 | **跳过** | 提供路径 + 动作 | 提供进度上下文 |
| **Explore** | 图谱无匹配 / 低置信度 / 新场景 | **执行** | 接收观测，扩展图谱 | 提供语义上下文 + 按需检索 |
| **Verify** | 高风险页面 (payment/checkout) | **执行** | 提供预期后置条件 | 提供 SpecGuard 校验 |

### 7.3 Navigate 模式 (图谱导航快速路径)

```
当前截图 → PageClassifier → page_type="search_result"
                                    │
    SpatialGraphMemory.locate() ────┘
    → 匹配到 Neo4j 节点: taobao_search_result_xxxx
    → confidence: 0.92
    
    SpatialGraphMemory.plan(goal="spec_selection")
    → Dijkstra 路径: search_result → product_detail → spec_selection
    → 下一步边: click on "商品卡片", region="center"
    → cost: 2.3, confidence: 0.85
    
    RuntimeController 判断:
    → mode = "navigate" ✓
    → confidence ≥ 0.7 ✓
    → risk_level = "normal" ✓
    
    SpatialModelBridge.compile_direct_action()
    → DeviceActionIR(type="click", coordinate=(500, 600), space="normalized_1000")
    
    直接执行 → 跳过 VLM 推理 → 节省 ~2-5 秒延迟 + API 费用
```

### 7.4 Explore 模式 (VLM 推理 + 在线建图)

```
当前截图 → PageClassifier → page_type="unknown" 或 无图谱匹配
                                    │
    SpatialGraphMemory.locate() ────┘
    → 无候选节点 / confidence < 0.5
    → is_novel = True
    
    回退到 VLM 推理路径:
    ① 构建消息 (Adapter.build_messages)
    ② 注入语义上下文:
       - "你当前在淘宝首页" (图谱描述)
       - "已浏览 3 个商品, 最低价 ¥4999" (记忆进度)
    ③ VLM 推理 → thinking + action
    ④ 执行动作
    
    同时建图:
    ⑤ 为当前页面创建新 PageNode
    ⑥ 记录 TransitionEdge (从上一页到当前页)
    ⑦ 更新 Neo4j
    
    → 下次遇到相同场景时可走 Navigate 模式
```

### 7.5 Verify 模式 (高风险页面)

```
图谱路径到达: checkout (结算页面)
                    │
    RuntimeController 判断:
    → risk_level = "high" → 必须 VLM 验证
    → 即使图谱有路径也不能直接执行
    
    VLM 验证流程:
    ① 截图发给 VLM
    ② 注入 SpecGuard 上下文:
       - "用户要求: 银色 512G"
       - "当前页面选择的规格: [待VLM确认]"
    ③ VLM 判断是否匹配
    ④ 匹配 → 执行
       不匹配 → Interact (询问用户)
```

### 7.6 信息注入机制

VLM 接收的上下文注入发生在消息文本的**开头**（不是结尾），结构如下：

```
[注入位置: 最后一条 user 消息的 text 开头]

── 图谱语义上下文 (来自 RuntimeController) ──
"你当前在淘宝的商品详情页。
 下一步建议: 点击"加入购物车"按钮 (confidence: 0.85)"

── 记忆进度上下文 (来自 MemoryManager.get_injection_context) ──
"[进度] 已完成: 打开淘宝 → 搜索 iPhone 17 → 浏览商品
 [商品] 已浏览: iPhone 17 Pro Max ¥8999 (淘宝旗舰店)
 [约束] 用户要求: 银色, 512G"

── SpecGuard 提示 (购物关键页面) ──
"⚠️ 当前为规格选择页面，请确保选择正确的颜色和容量再操作"
```

---

## 8. 创新点与研究价值

### 8.1 核心创新

#### 创新 1: 混合 VLM + 图谱导航架构

**问题**: 现有 Mobile Agent 每步都依赖 VLM 推理，延迟高、成本大。

**方案**: AMSG 空间图谱实现"已知路径零 VLM 推理"。Navigate 模式在置信度足够时直接执行图谱路径，将每步延迟从 2-5 秒降至毫秒级。

**深挖方向**:
- 图谱覆盖度 vs VLM 调用率的量化关系
- 图谱置信度阈值的自适应调节
- 跨应用图谱迁移（淘宝的购物流程知识迁移到京东）

#### 创新 2: 在线自进化图谱

**问题**: 静态知识图谱无法适应 UI 变化（App 改版、新功能上线）。

**方案**: Agent 每次执行都在**同时建图** — Explore 模式产生的观测实时写入图谱。失败的边会增加 fail_count，降低 confidence，自然地将过时路径权重降低。

**深挖方向**:
- 图谱更新策略：何时删除过时节点/边
- 多用户共享图谱时的冲突解决
- 图谱质量的自动化评估指标

#### 创新 3: 语义级页面表示

**问题**: 传统方法用 UI 元素树或像素特征表示页面，脆弱且泛化差。

**方案**: PageNode 使用 (landmarks, affordances, slots) 三元组表示页面语义。"搜索框 + 商品卡片 → 搜索结果页" 这个语义在 UI 改版后依然成立。

**深挖方向**:
- 语义相似度计算的改进（当前用 hash 匹配，可引入 embedding 相似度）
- 跨语言/跨地区 App 的语义对齐
- 从语义表示自动生成测试用例

#### 创新 4: 功能发现 (Functionality Discovery)

**问题**: Agent 只知道"怎么走"，不知道"页面还能做什么"。

**方案**: v4 新增 FunctionalityExtractor + FunctionalityClusterer，自动发现页面上的可操作功能项，聚类为功能簇，持久化到图谱。

**深挖方向**:
- 功能覆盖度驱动的主动探索策略
- 功能簇作为任务规划的 building blocks
- 功能发现的 benchmark 评估

#### 创新 5: 三层记忆架构 + 按需检索

**问题**: 全量注入记忆会污染 VLM 上下文窗口，降低推理质量。

**方案**: 
- Layer 1 (向量记忆): 跨会话持久化，语义检索
- Layer 2 (图结构记忆): 导航知识，路径规划
- Layer 3 (会话状态): 轻量进度追踪，每步注入
- RetrievalGateway: 只在 VLM 表现出不确定时才触发深度检索

**深挖方向**:
- 记忆重要性衰减曲线的优化
- 记忆注入对 VLM 推理质量的因果分析
- 隐私保护的联邦记忆共享

#### 创新 6: SpecGuard 购物安全机制

**问题**: Agent 误操作购物（错误规格、意外下单）的后果严重且不可逆。

**方案**: 
- 风险分级：页面级 (high/medium/normal) 控制是否必须 VLM 验证
- 规格守卫：从任务文本提取用户期望规格，在结算前自动校验
- 人机交互兜底：不确定时用 Interact 动作主动询问用户

**深挖方向**:
- 风险评估的自动化学习（从历史错误中学习新的风险模式）
- 多模态规格验证（OCR + VLM 联合确认屏幕上的规格文字）
- 可逆操作检测（哪些操作可以撤销，哪些不能）

### 8.2 与现有工作的差异化定位

| 维度 | AppAgent / CogAgent 类 | GUI-TARS 类 | ClawGUI-Agent |
|------|----------------------|-------------|---------------|
| 每步推理 | 必须 VLM | 必须 VLM | 可选 VLM (图谱导航跳过) |
| 跨会话学习 | 无 | 无 | 三层记忆持久化 |
| 页面理解 | 像素/元素级 | 像素级 | 语义级 (PageNode) |
| 安全机制 | 无 | 无 | SpecGuard + 风险分级 |
| 导航知识 | 无 | 无 | AMSG 空间图谱 |
| 多模型支持 | 单模型 | 单模型 | 5 种 VLM 统一适配 |
| 平台支持 | 单平台 | Android | Android + HarmonyOS + iOS |

### 8.3 值得进一步研究的方向

1. **图谱预训练**: 能否用大规模 App 截图数据集预训练一个通用 AMSG，让 Agent 在新 App 上也能"一步到位"？

2. **自适应置信度**: 当前 Navigate 模式的置信度阈值是固定的 0.7，能否根据用户的风险容忍度和任务类型动态调整？

3. **图谱压缩与蒸馏**: 随着使用时间增长，Neo4j 图谱会越来越大。如何在保持导航质量的前提下压缩图谱？

4. **多 Agent 协作**: 多个 Agent 实例共享同一个 AMSG，是否可以通过分工（一个探索、一个执行）来加速覆盖？

5. **VLM 微调反馈环**: 图谱中的成功/失败统计能否作为 RLHF 信号，反过来提升 VLM 在 GUI 任务上的表现？

6. **离线-在线图谱融合**: 当前有 OfflineExplorer 进行离线预探索，如何优雅地将离线图谱与在线增量更新融合？

---

## 附录 A: 核心文件索引

| 文件 | 行数 | 职责 |
|------|------|------|
| `agent.py` | ~1400 | Agent 主循环编排，VLM 推理，图谱/记忆集成 |
| `memory/memory_manager.py` | ~2000 | 记忆系统总协调，自动提取，上下文构建 |
| `memory/spatial_graph_memory.py` | ~2700 | 图谱建图、定位、路径规划、去重、在线更新 |
| `memory/graph_store.py` | ~1400 | Neo4j 驱动，Cypher 查询，图谱 CRUD |
| `memory/offline_explorer.py` | ~1600 | PageClassifier VLM 分类，离线探索循环 |
| `spatial/runtime_controller.py` | ~520 | v4 运行时控制器，路由决策，DAG 管理 |
| `spatial/functionality.py` | ~600 | 功能发现与分类 |
| `spatial/core.py` | ~330 | PageNode, AffordanceEdge, DeviceActionIR 定义 |
| `model/adapters.py` | ~800 | 5 种 VLM 适配器 |
| `model/protocol_bridge.py` | ~600 | 坐标系归一化，动作 IR 转换 |
| `actions/handler.py` | ~650 | 通用动作解析与执行 |
| `spatial/model_bridge.py` | ~150 | AMSG 语义动作 → 设备动作编译 |
| `clarify.py` | ~285 | 购物场景任务澄清子代理 |
| `memory/core/unified_state.py` | ~600 | 会话状态单一数据源 |
| `memory/retrieval_gateway.py` | ~200 | 按需检索触发器 |

## 附录 B: 环境变量速查

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PHONE_AGENT_BASE_URL` | `http://localhost:8000/v1` | VLM API 端点 |
| `PHONE_AGENT_MODEL` | `autoglm-phone-9b` | 模型名称 |
| `PHONE_AGENT_API_KEY` | `EMPTY` | API 密钥 |
| `PHONE_AGENT_MAX_STEPS` | `100` | 单任务最大步数 |
| `PHONE_AGENT_DEVICE_TYPE` | `adb` | 设备类型 (adb/hdc/ios) |
| `EMBEDDING_API_KEY` | — | BigModel Embedding-3 API 密钥 |
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j 连接地址 |
| `NEO4J_DATABASE` | `shopping` | Neo4j 数据库名 |
