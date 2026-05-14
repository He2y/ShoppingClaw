# ClawGUI-Agent 系统开发文档 v3.0

> 日期：2026-05-15
> 用途：记录架构重构后的系统设计，涵盖统一状态管理、智能重试、配置外部化、VLM 引导提取等全部优化
> 代码基线：UnifiedSessionState 架构 (Phase 1-4 重构完成)

---

## 目录

1. [系统概览](#1-系统概览)
2. [架构重构总览](#2-架构重构总览)
3. [统一状态管理 — UnifiedSessionState](#3-统一状态管理--unifiedsessionstate)
4. [Agent 执行引擎](#4-agent-执行引擎)
5. [双核记忆引擎](#5-双核记忆引擎)
6. [记忆解耦与按需检索](#6-记忆解耦与按需检索)
7. [智能重试机制](#7-智能重试机制)
8. [SpecGuard 智能规格防护](#8-specguard-智能规格防护)
9. [配置外部化](#9-配置外部化)
10. [模型适配器体系](#10-模型适配器体系)
11. [关键数据流](#11-关键数据流)
12. [文件清单](#12-文件清单)
13. [环境变量参考](#13-环境变量参考)
14. [学术贡献点](#14-学术贡献点)

---

## 1. 系统概览

### 1.1 系统定位

ClawGUI-Agent 是一个 VLM 驱动的 GUI 手机自动化框架，专注于**长任务购物场景**。实现"截图 → 推理 → 动作"闭环控制循环，通过双核记忆引擎（语义核 + 空间核）提供跨会话的个性化能力。

### 1.2 核心创新

| 创新点 | 描述 | 对应组件 |
|--------|------|---------|
| **统一状态管理** | 合并 SessionMemory + KnowledgeBase + StateManager 为单一 UnifiedSessionState，消除双写和数据冗余 | `memory/core/unified_state.py` |
| **双核记忆引擎** | FAISS 语义向量存储 (2048d) + Neo4j 空间图谱存储，协同工作 | MemoryStore + GraphStore |
| **记忆解耦** | 详细观察外置于 UnifiedSessionState，VLM 上下文仅保留 1-2 行进度摘要 | RetrievalGateway |
| **按需检索** | 5 种混淆信号触发记忆查询，Agent 仅在迷失时获取针对性帮助 | RetrievalGateway |
| **智能重试** | 网络错误指数退避（1s/2s/4s），限流等待 60s，认证失败直接终止 | `agent.py` _execute_step |
| **SpecGuard 自反思** | 代码级安全网：提取用户原始任务中的 SKU，交叉验证 thinking 中是否已选中 | `agent.py` _spec_guard_check |
| **配置外部化** | JSON 配置文件 + 代码默认值的混合方案，零代码修改即可添加新平台 | `config/shopping.json` |
| **VLM 引导提取** | Prompt 工程让 VLM 输出结构化商品信息，替代复杂正则 | `config/prompts_*.py` |

### 1.3 架构分层

```
┌──────────────────────────────────────────────────────────────────┐
│                      Entry Layer (入口层)                         │
│                   CLI (main.py)  │  WebUI (webui.py)              │
└─────────────────────────────┬────────────────────────────────────┘
                              │ task
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                      Agent Core (代理核心)                        │
│                         PhoneAgent                               │
│                                                                  │
│  Execution Loop:                                                  │
│    ① Screenshot  ② GraphRAG Lookup  ③ HITL Clarify               │
│    ④ Navigate Check  ⑤ Message Build  ⑥ Memory Decoupling        │
│    ⑦ VLM Inference (with retry)  ⑧ Action Parse + SpecGuard     │
│    ⑨ Execute  ⑩ Memory Update  ⑪ Finish  ⑫ Trace                │
└──┬──────────────┬──────────────┬──────────────┬──────────────────┘
   │              │              │              │
   ▼              ▼              ▼              ▼
┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────────────────────┐
│  Model   │ │  Action  │ │  Device  │ │  Memory System             │
│ Adapters │ │ Handlers │ │  Factory │ │  ┌───────────────────────┐ │
│  (5)     │ │  (6)     │ │  (3)     │ │  │ UnifiedSessionState   │ │
└──────────┘ └──────────┘ └─────┬────┘ │  │ (单一数据源)           │ │
                                │       │  │ - Products            │ │
                    ┌───────────┼───┐   │  │ - StepRecords         │ │
                    ▼           ▼   ▼   │  │ - ReasoningArchive    │ │
                  ADB         HDC  XCTEST│  └───────┬───────────────┘ │
                  (Android) (Harmony) (iOS)│        │                  │
                                          │ ┌──────▼───────────────┐ │
                                          │ │ RetrievalGateway     │ │
                                          │ │ (5 signals, cooldown)│ │
                                          │ └──────┬───────────────┘ │
                                          │        │                  │
                                          │ ┌──────▼───────────────┐ │
                                          │ │ Dual-Core Backend    │ │
                                          │ │ FAISS 2048d + Neo4j  │ │
                                          │ └──────────────────────┘ │
                                          └──────────────────────────┘
```

---

## 2. 架构重构总览

### 2.1 重构动机

v2.0 架构存在四个核心问题：

| 问题 | 影响 | 解决方案 |
|------|------|---------|
| 状态管理碎片化 | SessionMemory (343行) + KnowledgeBase (492行) + StateManager (101行) 职责重叠，数据重复存储 | 合并为 UnifiedSessionState (559行) |
| 双写模式 | 每次 add_step 同时写入 SessionMemory 和 KnowledgeBase，数据冗余 | 单写：仅 UnifiedSessionState |
| 错误处理缺失 | 无重试机制，网络波动直接终止任务 | 智能重试 + 指数退避 |
| 配置硬编码 | SHOPPING_APPS, SPEC_KEYWORDS 散落各处 | JSON 配置 + 代码默认值 |

### 2.2 代码变化

| 文件 | 旧行数 | 新行数 | 变化 |
|------|--------|--------|------|
| `memory_manager.py` | 1,554 | 1,451 | -7% |
| `session_memory.py` | 343 | 0 (删除) | -100% |
| `state_manager.py` | 101 | 0 (删除) | -100% |
| `knowledge_base.py` | 492 | 0 (删除) | -100% |
| `memory/core/unified_state.py` | 0 | 559 | +559 |
| `memory/core/product.py` | 0 | 75 | +75 |
| `memory/core/step_record.py` | 0 | 30 | +30 |
| `core/errors.py` | 0 | 27 | +27 |
| `config/shopping_config.py` | 0 | 72 | +72 |
| `config/shopping.json` | 0 | 27 | +27 |
| `agent.py` | 986 | 1,084 | +10% |
| **总计** | **3,476** | **3,325** | **-4%** |

### 2.3 数据流简化

```
旧架构 (v2.0):
agent.py → MemoryManager.add_step()
           ├─→ SessionMemory: set_current_product(), add_to_cart()
           ├─→ KnowledgeBase: record_product(), record_step()
           └─→ StateManager: update_state()
           三写，数据重复

新架构 (v3.0):
agent.py → MemoryManager.add_step()
           └─→ UnifiedSessionState: record_step(), record_product()
           单写，单一数据源
```

---

## 3. 统一状态管理 — UnifiedSessionState

### 3.1 设计原则

**单一数据源 (Single Source of Truth)**：每次观察仅写入一次，不再双写。

### 3.2 数据模型

**文件**: `phone_agent/memory/core/`

```
memory/core/
├── __init__.py         # 公开导出
├── product.py          # Product 数据类 + ProductStatus 枚举
├── step_record.py      # StepRecord 数据类
└── unified_state.py    # UnifiedSessionState 核心类 (559行)
```

#### Product (product.py)

```python
class ProductStatus(Enum):
    VIEWED = "viewed"           # 已浏览
    COMPARED = "compared"       # 已对比
    ADDED_TO_CART = "added_to_cart"  # 已加入购物车
    PURCHASED = "purchased"     # 已购买

@dataclass
class Product:
    name: str                    # 商品名称
    price: float | None = None   # 价格
    currency: str = "¥"          # 货币符号
    specs: dict[str, str] = {}   # {"颜色": "蓝色", "容量": "512G"}
    source_page: str = ""        # 来源页面
    status: ProductStatus = ProductStatus.VIEWED
    first_seen_step: int = 0     # 首次出现步骤
    screenshot_desc: str = ""    # 截图描述
```

#### StepRecord (step_record.py)

```python
@dataclass
class StepRecord:
    step: int                    # 步骤编号
    action_type: str             # 动作类型
    action_target: str = ""      # 动作目标
    thinking_short: str = ""     # 简短推理 (注入用)
    thinking_full: str = ""      # 完整推理 (归档用)
    page_type: str = ""          # 页面类型
    app: str = ""                # 当前 App
    timestamp: str = ""          # ISO 时间戳
```

### 3.3 UnifiedSessionState 核心 API

| 方法 | 功能 | 调用时机 |
|------|------|---------|
| `reset(task, platform)` | 清空所有会话数据，设置任务 | start_task |
| `record_step(step, action_type, thinking_full, page_type, app)` | 记录步骤 + 停滞检测 | 每步 |
| `record_product(product)` | 记录/合并商品（按名称去重） | 产品提取时 |
| `progress_summary()` | 生成 1-2 行进度摘要，注入 VLM 上下文 | 每步注入 |
| `current_focus()` | 返回当前焦点描述 | 进度摘要 |
| `retrieve_by_keywords(keywords)` | 关键词搜索推理归档 + 步骤 | 信号触发 |
| `retrieve_all_products_text()` | 返回所有商品文本 | 回退检索 |
| `is_stagnating()` | 检测同页面同动作 ≥ 2 次 | 停滞信号 |
| `should_compress()` | 每 5 步触发历史压缩 | 上下文管理 |
| `detect_retrieval_intent(thinking)` | 解析 VLM thinking 中的隐式检索意图 | 按需检索 |
| `to_dict()` | 完整序列化 | end_task 持久化 |
| `compute_state_id(hash, layout)` | 计算状态 ID | 状态追踪 |
| `start_task_state(id)` / `end_task_state(id)` | 状态生命周期 | 图谱记录 |

### 3.4 替换对照

| 旧组件 (已删除) | 新组件 | 关键差异 |
|----------------|--------|---------|
| `SessionMemory.current_product` | `UnifiedSessionState._current_product` | 去重逻辑内联 |
| `SessionMemory.viewed_products` | `UnifiedSessionState.products` | 使用 ProductStatus 枚举 |
| `KnowledgeBase._product_observations` | `UnifiedSessionState.products` | 同一列表 |
| `KnowledgeBase._step_records` | `UnifiedSessionState.steps` | 使用 StepRecord |
| `KnowledgeBase._reasoning_archive` | `UnifiedSessionState._reasoning_archive` | 直接迁移 |
| `StateManager._current_state` | `UnifiedSessionState._current_state_id` | 内联到统一状态 |
| `SessionMemory.constraints` | `UnifiedSessionState.constraints` | 保持不变 |
| `SessionMemory.get_context_for_injection()` | 已删除 (死代码) | 从未被调用 |

---

## 4. Agent 执行引擎

### 4.1 PhoneAgent 类结构

**文件**: `phone_agent/agent.py` (1,084行)

```python
class PhoneAgent:
    def __init__(self, model_config, agent_config,
                 confirmation_callback, takeover_callback,
                 clarification_callback):
        # 1. 模型解析：检测模型类型 → 选择适配器 + 专用 Handler
        # 2. 加载 ShoppingConfig（外部 JSON + 默认值）
        # 3. 默认 ActionHandler（AutoGLM 回退）
        # 4. 初始化 _context, _step_count, _current_task
        # 5. 可选组件：GUITracer, MemoryManager, ClarificationAgent
```

**关键属性**:

| 属性 | 类型 | 用途 |
|------|------|------|
| `_model_type` | `ModelType` | 5 种之一：AUTOGLM / UITARS / QWENVL / MAIUI / GUIOWL |
| `_adapter` | `ModelAdapter` | 消息构建、上下文管理、响应解析 |
| `_shopping_config` | `ShoppingConfig` | 外部化购物配置（Apps, 规格关键词, 购买关键词） |
| `_specialized_handler` | `ActionHandler*` | 非 AutoGLM 模型的专用动作处理器 |
| `action_handler` | `ActionHandler` | AutoGLM 默认动作处理器 |
| `memory_manager` | `MemoryManager` | 记忆系统统一入口 |
| `clarification_agent` | `ClarificationAgent` | HITL 任务澄清子代理 |
| `tracer` | `GUITracer` | 执行轨迹记录器 |

### 4.2 任务执行循环

```
run(task):
  ├─ 重置状态 (_context, _step_count, _current_task)
  ├─ 清空适配器历史 (QwenVL/GUI-Owl)
  ├─ tracer.start_task(task, model)
  ├─ memory_manager.start_task(task)
  │   ├─ UnifiedSessionState.reset(task, platform)
  │   ├─ 提取初始偏好 (_extract_from_task)
  │   └─ StateManager.start_task() → 内联到 UnifiedSessionState
  │
  ├─ _execute_step(is_first=True)  ← 第一步（携带任务描述）
  │   └─ if finished → end_task() → return
  │
  └─ while step_count < max_steps:
       _execute_step(is_first=False)  ← 后续步骤
       └─ if finished → end_task() → return

end_task():
  ├─ memory_manager.end_task(success, result, end_state)
  │   ├─ MemoryStore.add(TASK_HISTORY, state.to_dict())
  │   ├─ _learn_successful_pattern() → TASK_PATTERN
  │   ├─ _learn_contact_app_binding() → CONTACT_APP_BINDING
  │   └─ _save_pending_trajectory() → pending_trajectories.json
  └─ tracer.end_task(result, total_steps) → episode.json
```

### 4.3 _execute_step() 详细阶段

#### ① 截图采集 → ② 记忆查找 → ③ HITL 澄清

```
Screenshot → GraphRAG 三层匹配 → (第一步) ClarificationAgent 判断 → Navigate/Explore 模式选择
```

#### ⑥ Memory Decoupling — 最小化上下文注入

```
始终注入: UnifiedSessionState.progress_summary() → "Step 5 | 当前在京东商品详情页 | 已看3件 | 购物车(1件)"
按需注入: RetrievalGateway.check_and_retrieve(thinking, step)
  ├─ 无信号 → 跳过（上下文保持最小 ~200 tokens）
  └─ 有信号 → 注入检索结果 (~300-500 tokens)
```

#### ⑦ VLM 推理（带智能重试）

```python
max_retries = 3
for attempt in range(max_retries + 1):
    try:
        response = self.model_client.request(self._context)
        break  # 成功
    except (APIConnectionError, APITimeoutError) as e:
        # 网络错误 → 指数退避: 1s, 2s, 4s
        time.sleep(2 ** attempt)
    except RateLimitError:
        # 限流 → 等待 60s
        time.sleep(60)
    except AuthenticationError:
        # 认证失败 → 直接终止，不重试
        return StepResult(success=False, finished=True, ...)
```

#### ⑧ 动作解析 + SpecGuard

```
parse_action() → SpecGuard 自反思检查
  ├─ 终端动作 (finish/terminate/answer) → 直接放行
  ├─ 用户指定了 SKU → 检查 thinking 中是否已选中
  │   ├─ 全部选中 → ✅ 放行
  │   └─ 缺失 → 🛑 强制 Interact
  └─ 用户未指定 SKU → 🛑 强制 Interact 询问
```

---

## 5. 双核记忆引擎

### 5.1 架构总览

```
agent.py
   │
   ▼
MemoryManager (统一调度枢纽 — 1,451行)
   │           │
   ▼           ▼
MemoryStore  GraphStore
(语义核)     (空间核)
FAISS 2048d Neo4j
   │           │
   └─────┬─────┘
         ▼
  UnifiedSessionState
  (状态核 — 559行)
```

### 5.2 语义记忆核 — MemoryStore (FAISS 2048d)

**文件**: `phone_agent/memory/memory_store.py`

**存储层**:
```
memory_db/{user_id}/
  ├── memories_meta.json   ← 记忆元数据 (JSON)
  └── embeddings.npy       ← 2048 维向量 (NumPy)
```

**嵌入器 (双级回退)**:
1. `EmbeddingClient` (embedding-3 API, 2048d) ← 主嵌入器
2. `SimpleEmbedder` (字符哈希, 128d → 补零到 2048d) ← 回退

**搜索**: FAISS IndexFlatIP + L2 归一化 = 余弦相似度
- 得分 = `similarity * 0.7 + importance * 0.3`

### 5.3 空间记忆核 — GraphStore (Neo4j + TaskIndex)

**文件**: `phone_agent/memory/graph_store.py`

**优雅降级**: Neo4j 不可用时 `self.driver = None`，所有方法返回空值，不阻塞系统启动。

**图数据模型**:
```
  ┌──────────────┐     NEXT_ACTION      ┌──────────┐     PRODUCES      ┌──────────────┐
  │   UIState    │ ──────────────────→  │  Action  │ ────────────────→ │   UIState    │
  │ state_id     │   {confidence,       │ action_id│   {success_rate}  │ state_id     │
  │ app          │    frequency}        │ type     │                    │ app          │
  └──────────────┘                      └──────────┘                    └──────────────┘
        ↑                                                                    ↑
        │ STARTS_AT                                         ENDS_AT          │
        │                              ┌──────────────┐                      │
        └──────────────────────────────│  TaskTarget  │──────────────────────┘
                                       │ target_id    │  {success: bool}
                                       │ description  │
                                       │ app          │
                                       └──────────────┘
```

### 5.4 三层图谱匹配策略

| 层 | 方法 | 阈值 | 行为 |
|----|------|------|------|
| Layer 1 | TaskIndex FAISS (embedding-3, 2048d) | ≥0.85 → Navigate; 0.60-0.85 → Explore+轨迹注入 | 语义向量搜索历史任务 |
| Layer 2 | Neo4j N-gram 关键词回退 | 中文 N-gram + 令牌重叠计分 | FAISS 零结果时的回退 |
| Layer 3 | MemoryStore FAISS UI 状态回退 | 搜索 UI_STATE 类型 | 补充页面特征参考 |

---

## 6. 记忆解耦与按需检索

### 6.1 设计理念

传统架构将完整推理链、截图、观察全部推入 VLM 上下文，导致上下文膨胀和长任务迷失。v3.0 架构：

```
BEFORE (传统 Push 模式):                     AFTER (Memory Decoupling v3.0):
┌────────────────────────────┐              ┌────────────────────────────┐
│ [System Prompt]            │              │ [System Prompt]            │
│ [Step1: full thinking+img] │              │ [Progress: Step 5 | 当前:  │
│ [Step2: full thinking+img] │              │  Nike Air Max | 已看3件]   │
│ [Step3: full thinking+img] │              │ [Screenshot]               │
│ → Context grows unboundedly│              │                            │
└────────────────────────────┘              │ (仅当触发时注入)             │
                                            │ [记忆检索] 已浏览商品+步骤   │
                                            │ → Context ~80% smaller     │
                                            └────────────────────────────┘
```

### 6.2 RetrievalGateway — 5 种检索信号

| 信号类型 | 触发关键词 | 检索动作 | 冷却 |
|---------|-----------|---------|------|
| **Uncertainty** | "不确定", "忘记了", "之前看到", "not sure" | 搜索推理归档 + 商品列表 | 3步 |
| **Comparison** | "对比", "哪个更便宜", "compare" | 生成价格对比表 | 3步 |
| **Calculation** | "总共", "合计", "total", "sum" | 返回购物车商品+价格 | 3步 |
| **Product Lookup** | "价格是多少", "什么颜色", "那个商品" | 模糊商品名匹配 | 3步 |
| **Stagnation** | UnifiedSessionState.is_stagnating() | 返回最近步骤历史 | 3步 |

---

## 7. 智能重试机制

### 7.1 错误分类

**文件**: `phone_agent/core/errors.py`

```python
class ErrorCategory(Enum):
    RECOVERABLE = "recoverable"    # 网络错误、超时 — 指数退避重试
    RATE_LIMITED = "rate_limited"  # API 限流 — 等待后重试
    FATAL = "fatal"               # 认证失败 — 不重试
    VALIDATION = "validation"      # 响应解析失败
```

### 7.2 重试策略

**位置**: `agent.py` _execute_step() 方法

| 错误类型 | 策略 | 参数 |
|---------|------|------|
| `APIConnectionError` / `APITimeoutError` | 指数退避 | 1s → 2s → 4s，最多 3 次 |
| `RateLimitError` | 等待后重试 | 固定 60s，最多 3 次 |
| `AuthenticationError` | 立即终止 | 不重试 |
| 其他 `Exception` | 记录并终止 | 不重试 |

### 7.3 max_tokens 优化

为防止长任务中模型输出截断导致重复输出：

| 配置项 | 旧值 | 新值 | 覆盖方式 |
|--------|------|------|---------|
| `ModelConfig.max_tokens` | 3000 | 4096 | `PHONE_AGENT_MAX_TOKENS` 环境变量 |

---

## 8. SpecGuard 智能规格防护

### 8.1 设计动机

购物场景中，VLM 可能在规格选择页面跳过 `Interact` 操作，替用户做出错误的规格选择。v3.0 的 SpecGuard 增加了**自反思**能力。

### 8.2 三层防护体系

```
Layer 1: System Prompt (始终生效)
  ├─ 位置: prompts_zh.py / prompts_en.py
  └─ 内容: "如果任何参数未指定 → 唯一合法操作：Interact"
            "遇到商品信息时，使用格式：商品名为【Name】, 价格为 ¥Price, 颜色为Color"

Layer 2: Pre-inference 动态注入
  ├─ 触发: 当前 App ∈ ShoppingConfig.apps AND 检测到关键场景
  └─ 注入: SpecGuard 安全提示

Layer 3: Post-inference SpecGuard 自反思 (v3.0 核心增强)
  ├─ 提取用户原始任务中的 SKU 规格
  │   _extract_specs_from_task(task)
  │   → {"颜色": "银色", "容量": "512G"}
  │
  ├─ 检查 thinking 中是否已选中:
  │   _is_spec_selected(thinking, "颜色", "银色")
  │   → 检测 "银色.*已选中" 或 "机身颜色.*银色" + "已选中"
  │
  ├─ 判断逻辑:
  │   ├─ 用户指定了 SKU AND 全部已选中 → ✅ 放行
  │   ├─ 用户指定了 SKU AND 缺失 → 🛑 强制 Interact + 具体提示
  │   └─ 用户未指定 SKU → 🛑 强制 Interact + 询问
  │
  └─ 特殊放行:
      ├─ 终端动作 (finish/terminate/answer) → 直接放行
      └─ 已发出 Interact → 直接放行
```

### 8.3 SKU 提取规则

**文件**: `agent.py` _extract_specs_from_task()

| SKU 类型 | 提取来源 | 示例值 |
|---------|---------|--------|
| 颜色 | 内置 30+ 颜色词表 | 银色, 星光色, 深空黑色 |
| 容量 | 正则 `\d+\s*(TB?\|GB?)` | 512G, 256GB, 1TB |
| 尺码 | 内置尺码词表 | S, M, L, XL, 均码 |

---

## 9. 配置外部化

### 9.1 设计

采用 **JSON 配置文件 + 代码默认值** 的混合方案：

- 配置文件存在 → 加载 JSON 覆盖
- 配置文件不存在/损坏 → 使用代码内置默认值，打印 warning
- 零代码修改即可添加新购物平台

### 9.2 ShoppingConfig

**文件**: `phone_agent/config/shopping_config.py` (72行)

```python
@dataclass
class ShoppingConfig:
    apps: set[str]              # 购物 App 列表
    platforms: set[str]         # 平台识别列表
    spec_keywords: set[str]     # 规格关键词
    purchase_keywords: set[str] # 购买意图关键词

    @classmethod
    def default(cls) -> "ShoppingConfig": ...
    @classmethod
    def load(cls, config_path=None) -> "ShoppingConfig": ...
```

### 9.3 配置文件

**文件**: `config/shopping.json` (27行)

```json
{
  "apps": ["淘宝", "京东", "天猫", "拼多多", "美团", "饿了么", ...],
  "platforms": ["京东", "淘宝", "天猫", ...],
  "spec_keywords": ["规格", "颜色", "容量", "尺码", "口味", ...],
  "purchase_keywords": ["领券购买", "立即购买", "加入购物车", ...]
}
```

### 9.4 使用方式

```python
# agent.py
from phone_agent.config.shopping_config import ShoppingConfig

self._shopping_config = ShoppingConfig.load()
# 替换旧的硬编码常量:
# self._SHOPPING_APPS → self._shopping_config.apps
# self._SPEC_KEYWORDS → self._shopping_config.spec_keywords
# self._PURCHASE_KEYWORDS → self._shopping_config.purchase_keywords
```

---

## 10. 模型适配器体系

### 10.1 适配器对比

| 适配器 | 目标模型 | 消息策略 | 图片限制 | 坐标空间 | 推理内容字段 |
|--------|---------|---------|---------|---------|-----------|
| AutoGLMAdapter | AutoGLM/GLM-4V | 追加式+移除图片 | 无限制 | [0, 1000] | thinking |
| UITarsAdapter | UI-TARS (Doubao) | 追加式 | 5张 | 绝对像素 | thinking |
| QwenVLAdapter | Qwen2.5/3-VL | 重构建式 | 1张+历史文本 | [0, 999] | thinking |
| MAIUIAdapter | MAI-UI | 多消息追加 | 3张 | [0, 999] | reasoning_content |
| GUIOwlAdapter | GUI-Owl | 重构建式 | 1张 | [0, 999] | N/A |

### 10.2 Prompt 模板体系

| 文件 | 行数 | 用途 |
|------|------|------|
| `prompts.py` | 82 | 核心模板 + 控制逻辑 |
| `prompts_zh.py` | 111 | 中文系统提示（AutoGLM 默认） |
| `prompts_en.py` | 88 | 英文系统提示 |
| `prompts_uitars.py` | 90 | UI-TARS 模型专用 |
| `prompts_qwenvl.py` | 199 | QwenVL 模型专用（含工具 Schema） |
| `prompts_guiowl.py` | 285 | GUI-Owl 模型专用（含完整工具定义） |
| `prompts_maiui.py` | 92 | MAI-UI 模型专用 |

**v3.0 Prompt 增强**: 所有模板添加了 VLM 引导商品信息提取：

```
[购物] 遇到商品信息时，在思考中使用格式：
商品名为【Name】, 价格为 ¥Price, 颜色为Color
```

---

## 11. 关键数据流

### 11.1 完整购物任务数据流

```
User Task: "去淘宝苹果官方旗舰店买一个iPhone 17 Pro Max，银色，512G"

PhoneAgent.run(task)
│
├─ memory_manager.start_task(task)
│   ├─ UnifiedSessionState.reset(task="...", platform="淘宝")
│   └─ _extract_from_task() → 用户偏好
│
└─ [Loop] _execute_step()
    │
    ├─ ① Screenshot + ② GraphRAG Lookup
    │   └─ TaskIndex FAISS 搜索 → 相似历史轨迹注入参考
    │
    ├─ ③ [is_first] ClarificationAgent → "任务信息完整，直接执行"
    │
    ├─ ⑤ Message Build (AutoGLM 路径)
    │   └─ [System Prompt] + [个性化记忆] + [User: task + 截图]
    │
    ├─ ⑥ Memory Decoupling
    │   ├─ [Progress] "Step 1 | 当前在淘宝首页" (1-2行)
    │   └─ RetrievalGateway → 无信号 → 跳过检索
    │
    ├─ ⑦ VLM Inference (with retry)
    │   └─ response → thinking + action
    │
    ├─ ⑧ Action Parse + SpecGuard 自反思
    │   ├─ parse_action("do(action=\"Tap\", element=[...])")
    │   ├─ _extract_specs_from_task(task) → {"颜色": "银色", "容量": "512G"}
    │   ├─ _is_spec_selected(thinking, "颜色", "银色") → True/False
    │   └─ 全部选中 → ✅ 放行 / 缺失 → 🛑 Interact
    │
    ├─ ⑨ Execute → ActionResult
    │
    ├─ ⑩ Memory Update
    │   ├─ memory_manager.add_step(thinking, action, "淘宝")
    │   │   ├─ UnifiedSessionState.record_step(...)  ← 单写
    │   │   └─ _extract_product_simple(thinking) → Product(name="iPhone 17 Pro Max", price=11894.8, specs={"颜色": "银色", "容量": "512GB"})
    │   │       └─ UnifiedSessionState.record_product(product)
    │   └─ update_state_and_transition(...)
    │
    ├─ ⑪-⑫ Finish detection → finish(message="任务完成...")
    │   └─ SpecGuard: _metadata=="finish" → 直接放行
    │
    └─ ⑬ Tracer Record + ⑭ Return
```

---

## 12. 文件清单

| 文件 | 行数 | 核心职责 |
|------|------|---------|
| `agent.py` | 1,084 | Agent 主循环 + 5种模型分支 + 智能重试 + SpecGuard 自反思 + Memory Decoupling |
| `memory/memory_manager.py` | 1,451 | 记忆调度 + 上下文构建 + 自动学习 + 产品提取 |
| `memory/core/unified_state.py` | 559 | **NEW** 统一状态管理 (合并 SessionMemory + KnowledgeBase + StateManager) |
| `memory/core/product.py` | 75 | **NEW** Product 数据类 + ProductStatus 枚举 |
| `memory/core/step_record.py` | 30 | **NEW** StepRecord 数据类 |
| `memory/memory_store.py` | 666 | FAISS 向量存储 2048d + 双级嵌入器 |
| `memory/graph_store.py` | 436 | Neo4j 图 + Cypher 查询 + TaskIndex (优雅降级) |
| `memory/retrieval_gateway.py` | ~200 | 按需检索引擎：5种信号检测 + 冷却机制 |
| `memory/embedding_client.py` | 48 | embedding-3 HTTP 客户端 |
| `memory/task_index.py` | 108 | FAISS + EmbeddingClient 任务索引 |
| `memory/offline_explorer.py` | ~250 | 离线页面探索器：页面分类 + 轨迹收集 |
| `core/errors.py` | 27 | **NEW** ErrorCategory 枚举 + AgentError 异常类 |
| `config/shopping_config.py` | 72 | **NEW** 外部化购物配置 (JSON + 默认值) |
| `config/prompts_zh.py` | 111 | 中文系统提示 (含 VLM 引导提取 + SpecGuard 规则) |
| `config/prompts_en.py` | 88 | 英文系统提示 |
| `config/prompts_uitars.py` | 90 | UI-TARS 专用提示 |
| `config/prompts_qwenvl.py` | 199 | QwenVL 专用提示 (含工具 Schema) |
| `config/prompts_guiowl.py` | 285 | GUI-Owl 专用提示 (含完整工具定义) |
| `config/prompts_maiui.py` | 92 | MAI-UI 专用提示 |
| `model/adapters.py` | ~880 | 5个模型适配器：消息构建 + 解析 + 坐标归一化 |
| `model/client.py` | 330 | OpenAI 流式客户端 + MessageBuilder (max_tokens=4096) |
| `actions/handler.py` | 574 | AutoGLM 动作解析 + 13种动作执行 |
| `actions/handler_uitars.py` | ~300 | UI-TARS 专用处理器 |
| `actions/handler_qwenvl.py` | ~300 | QwenVL 专用处理器 |
| `actions/handler_maiui.py` | ~250 | MAI-UI 专用处理器 |
| `actions/handler_guiowl.py` | ~250 | GUI-Owl 专用处理器 |
| `clarify.py` | 281 | HITL 任务澄清子代理 |
| `tracer.py` | 122 | GUI 执行轨迹录制 |
| `device_factory.py` | 167 | 跨平台设备抽象工厂 (ADB/HDC/XCTEST) |
| **核心总计** | **~8,200** | |

### 已删除文件

| 文件 | 原因 |
|------|------|
| `memory/session_memory.py` (343行) | 合并到 UnifiedSessionState |
| `memory/state_manager.py` (101行) | 合并到 UnifiedSessionState |
| `memory/knowledge_base.py` (492行) | 合并到 UnifiedSessionState |

---

## 13. 环境变量参考

**文件**: `.env.example`

```bash
# ============================================================
# Neo4j 图数据库配置 (空间记忆核)
# ============================================================
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=password
NEO4J_DATABASE=shopping

# ============================================================
# Phone Agent 模型配置
# ============================================================
PHONE_AGENT_BASE_URL=http://localhost:8000/v1
PHONE_AGENT_MODEL=autoglm-phone-9b
PHONE_AGENT_API_KEY=EMPTY
PHONE_AGENT_MAX_STEPS=100
PHONE_AGENT_MAX_TOKENS=4096
PHONE_AGENT_DEVICE_TYPE=adb
PHONE_AGENT_LANG=cn

# ============================================================
# 记忆系统开关
# ============================================================
# ENABLE_MEMORY=true

# ============================================================
# 离线 VLM 配置 (import_manual_data.py 布局提取)
# ============================================================
OFFLINE_VLM_BASE_URL=https://api.openai.com/v1
OFFLINE_VLM_MODEL=gpt-4o-mini
OFFLINE_VLM_API_KEY=your_offline_vlm_api_key

# ============================================================
# Embedding 配置 (FAISS 向量化)
# ============================================================
EMBEDDING_API_KEY=your_embedding_api_key
EMBEDDING_BASE_URL=https://api.openai.com/v1
EMBEDDING_MODEL=text-embedding-3-large
```

**所有环境变量说明**:

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PHONE_AGENT_BASE_URL` | `http://localhost:8000/v1` | VLM API 端点 |
| `PHONE_AGENT_MODEL` | `autoglm-phone-9b` | 模型名称 |
| `PHONE_AGENT_API_KEY` | `EMPTY` | API 密钥 |
| `PHONE_AGENT_MAX_STEPS` | `100` | 单任务最大步骤数 |
| `PHONE_AGENT_MAX_TOKENS` | `4096` | 单次推理最大输出 token 数 |
| `PHONE_AGENT_DEVICE_TYPE` | `adb` | 设备类型: `adb` / `hdc` / `ios` |
| `PHONE_AGENT_LANG` | `cn` | 提示语言: `cn` / `en` |
| `ENABLE_MEMORY` | `true` | 启用记忆系统 |
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j 连接地址 |
| `NEO4J_USER` | `neo4j` | Neo4j 用户名 |
| `NEO4J_PASSWORD` | `password` | Neo4j 密码 |
| `NEO4J_DATABASE` | `shopping` | Neo4j 数据库名 |
| `OFFLINE_VLM_BASE_URL` | — | 离线 VLM 端点 |
| `OFFLINE_VLM_MODEL` | — | 离线 VLM 模型名 |
| `OFFLINE_VLM_API_KEY` | — | 离线 VLM API 密钥 |
| `EMBEDDING_API_KEY` | — | Embedding API 密钥 |
| `EMBEDDING_BASE_URL` | — | Embedding API 端点 |
| `EMBEDDING_MODEL` | — | Embedding 模型名 |

---

## 14. 学术贡献点

### 14.1 核心 Claim

> 我们提出了一种面向长任务购物场景的 GUI Agent 架构，通过四个维度的优化提升系统鲁棒性：(1) 统一状态管理消除数据冗余与双写；(2) 记忆解耦将详细观察外置，VLM 上下文仅保留最小化进度；(3) SpecGuard 自反思在推理时交叉验证用户原始需求与当前状态；(4) 智能重试与配置外部化提升工程可靠性。与双核记忆引擎协同，系统在上下文效率、容错能力和可维护性三个维度均有显著提升。

### 14.2 可量化指标

1. **任务成功率** (Task Success Rate): 启用/禁用各组件的长购物任务对比
2. **上下文效率** (Context Efficiency): 传统 Push vs Memory Decoupling 的 VLM 输入 token 对比（预期 ~80% 降低）
3. **重复操作率** (Redundancy Rate): 连续相同动作在相同页面的比例
4. **信息保留率** (Information Retention): 会话早期产品在后续步骤中被 VLM thinking 引用的比例
5. **检索触发精度** (Retrieval Precision): RetrievalGateway 5 种信号的 precision/recall
6. **图谱命中率** (Graph Hit Rate): Navigate 模式直接执行的比例 vs VLM 推理比例
7. **SpecGuard 准确率**: 规格页面的正确拦截率 vs 误拦率
8. **重试恢复率** (Retry Recovery Rate): 网络错误后成功重试的比例

### 14.3 消融实验设计

| 实验组 | 配置 | 预期效果 |
|--------|------|---------|
| Full System | 全部组件启用 | 基准性能 |
| - UnifiedState | 回退到 SessionMemory + KnowledgeBase 双写 | 数据一致性降低 |
| - RetrievalGateway | 始终推入所有记忆（关闭按需检索） | 上下文增大 |
| - SpecGuard Self-Reflection | 仅保留关键词匹配拦截 | 误拦率上升 |
| - Retry | 移除智能重试 | 网络抖动时成功率下降 |
| - GraphRAG | 仅使用 MemoryStore FAISS | Navigate 快捷路径消失 |
| - External Config | 回退到代码硬编码 | 维护灵活性降低 |

---

*本文档基于 2026-05-15 对 `phone_agent/` 全部源文件的架构重构分析。*
*重构分支: `refactor/architecture-optimization`*
