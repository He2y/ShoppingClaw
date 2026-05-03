# ClawGUI-Agent 系统架构文档 v3.0

> 日期：2026-05-03
> 状态：已实现（含已知设计缺陷分析）
> 基于对 `phone_agent/` 全部源码的逐文件审计

---

## 一、目录结构

```
phone_agent/
├── __init__.py
├── agent.py                    # PhoneAgent 主循环 (879行)
├── agent_ios.py                # iOS 专用智能体 (无记忆系统)
├── clarify.py                  # ⚠️ 孤立文件：旧版 _clarify_task_if_needed
├── device_factory.py           # 跨平台设备抽象工厂 (ADB/HDC/XCTEST)
├── tracer.py                   # GUI 执行轨迹记录器
│
├── model/
│   ├── adapters.py             # 5个VLM适配器 + 模型类型检测
│   └── client.py               # OpenAI-compatible 流式推理客户端
│
├── actions/
│   ├── handler.py              # AutoGLM 动作解析器+执行器
│   ├── handler_uitars.py       # UI-TARS 专用处理器
│   ├── handler_qwenvl.py       # QwenVL 专用处理器
│   ├── handler_maiui.py        # MAI-UI 专用处理器
│   ├── handler_guiowl.py       # GUI-Owl 专用处理器
│   └── handler_ios.py          # iOS WDA 动作处理器
│
├── config/
│   ├── prompts.py / prompts_zh.py / prompts_en.py   # AutoGLM 提示词
│   ├── prompts_uitars.py / prompts_qwenvl.py        # 各模型专用提示词
│   ├── prompts_maiui.py / prompts_guiowl.py
│   ├── apps.py / apps_harmonyos.py / apps_ios.py    # 应用包名映射
│   ├── timing.py               # 统一操作延迟配置
│   └── i18n.py                 # 中/英 UI 字符串
│
├── adb/                        # Android ADB 后端
├── hdc/                        # HarmonyOS HDC 后端
├── xctest/                     # iOS XCTEST/WebDriverAgent 后端
│
└── memory/
    ├── __init__.py
    ├── memory_manager.py       # 记忆系统调度枢纽 (1093行)
    ├── memory_store.py         # FAISS + SimpleEmbedder 向量存储 (549行)
    ├── graph_store.py          # Neo4j 图存储 + TaskIndex (433行)
    ├── task_index.py           # FAISS + EmbeddingClient 语义任务索引 (109行)
    └── embedding_client.py     # 远程 embedding-3 API 客户端 (48行)
```

---

## 二、整体架构

```
┌──────────────────────────────────────────────────────────────────┐
│                         CLI / WebUI                              │
└─────────────────────────────┬────────────────────────────────────┘
                              │ task
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                          PhoneAgent                              │
│                                                                  │
│  run(task):                                                      │
│    _context=[], start_task(), _execute_step(is_first=True)       │
│    while step_count < max_steps:                                 │
│      _execute_step(is_first=False)                               │
│                                                                  │
│  _execute_step():  7个阶段的单步执行                              │
│    Phase 1: 截图采集 (DeviceFactory)                              │
│    Phase 2: 状态定位 (MemoryManager.locate_and_get_context)      │
│    Phase 3: 模式决策 (navigate vs explore)                       │
│    Phase 4: 消息构建 (Adapter.build_messages)                    │
│    Phase 5: 上下文注入 + VLM推理 (ModelClient)                   │
│    Phase 6: Action解析与执行 (Handler)                           │
│    Phase 7: 记忆更新 + 图构建 (MemoryManager + GraphStore)       │
└──┬──────────────┬──────────────┬──────────────┬──────────────────┘
   │              │              │              │
   ▼              ▼              ▼              ▼
┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────┐
│  Model   │ │  Action  │ │  Device  │ │  Memory System   │
│ Adapters │ │ Handlers │ │  Factory │ │  (双核记忆引擎)   │
│ (5个)    │ │ (6个)    │ │          │ │                  │
└──────────┘ └──────────┘ └─────┬────┘ │ ┌──────────────┐ │
                                │       │ │ Semantic Core│ │
                    ┌───────────┼───┐   │ │ (FAISS 128d) │ │
                    ▼           ▼   ▼   │ │ SimpleEmbedder│ │
                  ADB         HDC  XCTEST│ └──────────────┘ │
                  (Android) (Harmony) (iOS)│ ┌──────────────┐ │
                                          │ │ Spatial Core │ │
                                          │ │ (Neo4j Graph)│ │
                                          │ │ + TaskIndex  │ │
                                          │ │ (FAISS 2048d)│ │
                                          │ └──────────────┘ │
                                          └──────────────────┘
```

---

## 三、Agent 执行流程详解

### 3.1 run(task) — 任务入口

```
run(task)
  ├─ _context = []
  ├─ _step_count = 0
  ├─ _last_state_hash = None
  ├─ _last_user_reply = None
  ├─ clear_history()  ← 清空 QwenVL/GUI-Owl adapter 历史
  ├─ tracer.start_task()
  ├─ memory_manager.start_task(task)  ← 从任务文本中提取联系人/App
  │
  ├─ _execute_step(task, is_first=True)   ← 第一步（带任务描述）
  │   └─ if finished → end_task() → return
  │
  └─ while step_count < max_steps:
       _execute_step(is_first=False)       ← 后续步骤
       └─ if finished → end_task() → return

end_task():  ← 三处调用点（第一步完成、循环中完成、超时）
  memory_manager.end_task(success, result, end_state_id)
  tracer.end_task(result, total_steps)
```

### 3.2 _execute_step() — 单步执行的7个阶段

#### Phase 1: 屏幕采集 (agent.py:357-359)

```python
screenshot = device_factory.get_screenshot(device_id)  # → base64, width, height
current_app = device_factory.get_current_app(device_id) # → "微信" / "home_screen"
```

#### Phase 2: 状态定位 (agent.py:361-378)

```python
ui_hash = MD5(screenshot.base64_data)  # ⚠️ 问题：同一界面不同截图永远不同哈希
self._last_state_hash = f"state_{ui_hash}"
semantic_layout = current_app  # 仅用APP名称，粒度过粗

context_data = memory_manager.locate_and_get_context(ui_hash, semantic_layout, task)
# → {mode, max_similarity, semantic_context, next_actions, current_state_id}
```

locate_and_get_context 的三层匹配策略：

```
Layer 1: TaskIndex FAISS (embedding-3, 2048d) 语义向量搜索
  ├─ similarity >= 0.85 → mode="navigate" (直接执行快捷动作)
  ├─ similarity >= 0.60 → mode="explore" + 注入压缩轨迹上下文
  └─ similarity < 0.60  → 触发主动澄清 (仅第一步)

Layer 2: Neo4j N-gram 关键词回退 (FAISS结果为空时)
  └─ 中文N-gram分词 + 令牌重叠计分 + 关键词奖励

Layer 3: MemoryStore FAISS fallback (SimpleEmbedder, 128d)
  └─ 搜索 MemoryType.UI_STATE，补充页面特征参考
```

#### Phase 3: 模式决策 (agent.py:380-436)

**Navigate 模式** — 跳过VLM直接执行：

```python
if mode == "navigate" and context_data.get("next_actions"):
    best_action = context_data["next_actions"][0]
    action = {"_metadata": "do", "action_type": best_action["type"], ...}
    # ⚠️ 问题：无置信度阈值检查，任意匹配都触发
    # ⚠️ 问题：无VLM验证，图数据可能过时
    result = action_handler.execute(action, width, height)
```

**Explore 模式** — 正常VLM推理路径。

#### Phase 4: 消息构建 (agent.py:438-509)

按模型类型分支：

| 模型类型 | 构建策略 | 上下文图片 |
|---------|---------|-----------|
| AutoGLM | 追加式：`system + user(task+image)` → 后续 `user(image)` | 无限制 |
| UI-TARS | 追加式 + limit_context(5) | 最多5张 |
| QwenVL | 重构建式：每轮 `system + user(task+history+image)` | 始终1张 |
| MAI-UI | 多消息式：`system → user(text) → user(image)` | 最多3张 |
| GUI-Owl | 重构建式：同 QwenVL | 始终1张 |

AutoGLM 模式下的个性化 Prompt 构建：

```python
# 通过 build_personalized_prompt() 在 "必须遵循的规则" 前插入记忆上下文
system_prompt = build_personalized_prompt(base_prompt, memory_manager, task)
```

#### Phase 5: 上下文注入与VLM推理 (agent.py:511-586)

上下文注入方式：**文本追加到用户消息末尾**

```python
# 注入到最后一条 Vision 消息的 text 字段
item["text"] = item["text"].rstrip() + f"\n\n[记忆上下文]\n{extra_context}"
```

注入内容来自 `locate_and_get_context()` 的 `semantic_context` 字段：
- 基于频率的联系人-应用推荐（⚡ 标记）
- 相关任务历史（📋 标记）
- 压缩的相似任务轨迹（最多200字）
- 购物偏好（🛒 标记）
- 用户纠正（⚠️ 标记）

VLM推理：流式输出，thinking 实时打印，action 缓冲。

#### Phase 6: Action解析与执行 (agent.py:588-677)

```
专用Handler路径 (UI-TARS/QwenVL/MAI-UI/GUI-Owl):
  parsed = specialized_handler.parse_response(raw_content)
  result = specialized_handler.execute(parsed, width, height)

通用Handler路径 (AutoGLM):
  action = parse_action(response.action)  # AST/JSON/XML 多级解析
  remove_images_from_message(context[-1]) # 节省空间
  result = action_handler.execute(action, width, height)
```

#### Phase 7: 记忆更新 + 在线图构建 (agent.py:739-770)

```python
# 记忆更新
memory_manager.add_step(thinking, action, current_app)
  ├─ _track_app_usage(app)        → APP_USAGE 记忆
  ├─ _learn_from_action(action)   → 联系人/搜索模式提取
  └─ _learn_from_thinking(thinking) → 实体提取+偏好推断

# ⚠️ 在线图构建：agent.py 直接访问 graph_store
memory_manager.graph_store.add_state_transition(
    self._prev_state_id, current_state_id, action, task
)
self._prev_state_id = current_state_id
```

---

## 四、双核记忆引擎详解

### 4.1 语义记忆核 — MemoryStore (FAISS + SimpleEmbedder)

```
存储层:
  memory_db/default/
    ├── memories_meta.json   ← 所有记忆的元数据 (JSON)
    └── embeddings.npy       ← 128维向量 (NumPy)

嵌入器: SimpleEmbedder(dim=128)
  - 字符频率特征（位置加权）
  - 二元组 (bigram) 特征
  - L2 归一化 → 余弦相似度
  - ⚠️ 仅基于字符分布，无法捕捉语义相似性
  - ⚠️ embedding_client.py 的 embedding-3 API 未使用

搜索: O(n) 暴力遍历
  for memory in self.memories.values():
      similarity = cosine(query_emb, memory.embedding)
      score = similarity * 0.7 + importance * 0.3
  - ⚠️ search() 不使用 FAISS 索引 (_add_to_index 构建的索引是死代码)
  - ⚠️ FAISS IndexFlatIP 只在 add/delete/rebuild 中维护，search 从未查询
```

**MemoryType 枚举 (13种):**

| 类型 | 值 | 默认重要性 | 说明 |
|------|-----|----------|------|
| USER_PREFERENCE | user_preference | 0.6 | 用户设置/使用偏好 |
| CONTACT | contact | 0.7 | 联系人信息 |
| CONTACT_APP_BINDNG | contact_app_binding | 0.8 | ⚠️ 枚举名少字母D：应为 BINDING |
| APP_USAGE | app_usage | 0.5 | 应用使用记录 |
| TASK_HISTORY | task_history | 0.4 | 任务执行历史 |
| TASK_PATTERN | task_pattern | 0.6 | 任务模式/流程 |
| USER_CORRECTION | user_correction | 1.0 | 用户纠正（最高优先级） |
| PRODUCT_PREFERENCE | product_preference | 0.5 | 商品品类偏好 |
| PRICE_SENSITIVITY | price_sensitivity | 0.5 | 价格敏感度 |
| BRAND_AFFINITY | brand_affinity | 0.5 | 品牌忠诚度 |
| SCENE_RECOMMENDATION | scene_recommendation | 0.5 | 场景推荐 |
| UI_STATE | ui_state | 0.4 | UI状态特征 |
| UI_TRANSITION | ui_transition | 0.4 | UI状态转换 |

---

### 4.2 空间记忆核 — GraphStore (Neo4j + TaskIndex)

```
图数据模型:
  ┌──────────────┐     NEXT_ACTION      ┌──────────┐     PRODUCES      ┌──────────────┐
  │   UIState    │ ──────────────────→  │  Action  │ ────────────────→ │   UIState    │
  │ state_id     │                      │ action_id│                    │ state_id     │
  │ app          │                      │ type     │                    │ app          │
  │ semantic_layout│                    │ target   │                    │ semantic_layout│
  └──────────────┘                      └──────────┘                    └──────────────┘
        ↑                                                                    ↑
        │ STARTS_AT                                                          │ ENDS_AT
        │                              ┌──────────────┐                      │
        └──────────────────────────────│  TaskTarget  │──────────────────────┘
                                       │ target_id    │
                                       │ description  │
                                       │ app          │
                                       │ success      │
                                       │ committed_at │
                                       └──────────────┘
```

**TaskIndex (FAISS + EmbeddingClient):**

```
嵌入维度: 2048d (embedding-3 API)
索引类型: FAISS IndexFlatIP + L2归一化 = 余弦相似度
存储:
  memory_db/default/
    ├── task_index.npy   ← FAISS 向量
    └── task_ids.json    ← 任务ID列表

⚠️ 与 MemoryStore 的 128d SimpleEmbedder 是完全不相交的嵌入空间
⚠️ 如果 EMBEDDING_API_KEY 未配置，返回全零向量（静默降级）
```

**轨迹提交流程:**

```
任务完成 → _save_pending_trajectory() → pending_trajectories.json (本地暂存, 最多20条)
                                              ↓
                              memory_manager.commit_pending(index=0) (手动调用)
                                              ↓
                              graph_store.commit_task_trajectory() → Neo4j + TaskIndex
```

---

## 五、上下文注入机制

### 5.1 个性化上下文 (AutoGLM 专用)

`get_relevant_context(task)` → 格式化为以下结构：

```
【用户个性化信息 - 请严格按照以下信息选择应用】

**🎯 基于使用频率的应用推荐（必须遵循）:**
  ⚡ 联系「张三」：推荐使用 **微信** (使用5次) 而非 QQ (使用1次)

**📋 相关任务历史:**
  模式: 「在京东点外卖」→ 京东

**🛒 购物偏好:**
  倾向于选择百亿补贴

**其他信息:**
  ⚠️ 注意: 用户纠正 - 应选择名字完全匹配的联系人
```

### 5.2 轨迹上下文 (Explore 模式)

当 Layer 1/2 匹配到相似任务时，通过 `locate_and_get_context()` 注入：

```
【行动参考】
"在京东点KFC外卖"(京东·3次)：搜索框→KFC→官方店→套餐→购物车
（注意：当前界面可能与历史轨迹不同，请根据实际截图调整动作）

【用户个性化信息 - 请严格按照以下信息选择应用】
...
```

---

## 六、模型适配器体系

| 适配器 | 模型 | 消息策略 | 图片限制 | 坐标空间 |
|--------|------|---------|---------|---------|
| AutoGLMAdapter | AutoGLM/GLM-4V | 追加式 | 无限制 | [0,1000] |
| UITarsAdapter | UI-TARS (Doubao) | 追加式 | 5张 | 绝对像素 |
| QwenVLAdapter | Qwen2.5/3-VL | 重构建式 | 1张 | [0,999] |
| MAIUIAdapter | MAI-UI | 多消息追加 | 3张 | [0,999] |
| GUIOwlAdapter | GUI-Owl | 重构建式 | 1张 | [0,999] |

模型检测优先级：GUI-Owl > UI-TARS > Qwen-VL > MAI-UI > AutoGLM（默认）

---

## 七、设计缺陷与协调问题分析

### 🔴 严重缺陷

#### 7.1 agent.py 与 MemoryManager 的状态管理分裂

**问题：** `_prev_state_id` 在 agent.py (line 205) 中追踪，但 `locate_and_get_context()` 在 MemoryManager 中返回 `current_state_id`。在线图构建时 agent.py 直接调用 `memory_manager.graph_store`（绕过 MemoryManager 抽象层），而 MemoryManager 自身也维护 `_current_state_id` / `_task_start_state_id` / `_task_end_state_id`（line 97-99），两者**各自管理状态、互不同步**。

```python
# agent.py:747 — 直接穿透 MemoryManager 访问 GraphStore
self.memory_manager.graph_store.add_state_transition(
    self._prev_state_id,        # ← agent.py 自己的状态
    current_state_id,           # ← 来自 locate_and_get_context() 返回值
    action, self._current_task
)
self._prev_state_id = current_state_id  # ← agent.py 管理

# memory_manager.py:99 — MemoryManager 自己也有
self._current_state_id: str | None = None  # ← 从未与 agent.py 同步
```

**影响：** 两层状态各自独立演化，MemoryManager 记录的 `start_state/end_state` 与 agent.py 实际传给 graph_store 的状态不一致。

#### 7.2 嵌入维度分裂（128d vs 2048d）

**问题：** 系统存在两套完全不相交的向量嵌入空间：

| 组件 | 嵌入器 | 维度 | 底层技术 |
|------|--------|------|---------|
| MemoryStore (语义记忆) | SimpleEmbedder | 128 | 字符频率哈希 |
| TaskIndex (任务索引) | EmbeddingClient | 2048 | embedding-3 API |

- MemoryStore 的 128d 向量**无法参与** TaskIndex 的 2048d 语义搜索
- 两个空间中的相似度值**不可比较**
- embedding_client.py 存在但 MemoryStore 完全不使用它

#### 7.3 MemoryStore 的 FAISS 索引是死代码

**问题：** `MemoryStore.search()` (line 346-397) 使用 O(n) 暴力遍历所有记忆：

```python
for memory in self.memories.values():  # ← 暴力遍历
    similarity = self._compute_similarity(...)
```

但 `_add_to_index()` (line 334-344) 正确地将每个新记忆添加到 FAISS IndexFlatIP，`_rebuild_index()` 也在增删后重建索引。这些 FAISS 索引**从未被 search() 查询**。FAISS 的 `index.search()` 调用只存在于 TaskIndex 类中，而 MemoryStore 类虽有 `self.index` 属性，search 方法完全无视它。

#### 7.4 clarify.py 是孤立/重复文件

**问题：** `phone_agent/clarify.py` (55行) 包含一个 `_clarify_task_if_needed` 方法，与 `agent.py:290-348` 中的版本功能相同但实现不同：
- clarify.py 使用 `self.model_client.request(messages)` (非流式)
- agent.py 使用 `self.model_client.client.chat.completions.create(...)` (直接HTTP + temperature=0.1)
- clarify.py 缺少任务重组/重写步骤
- 这个文件未被任何模块导入，是残余代码

---

### 🟠 中等缺陷

#### 7.5 Navigate 模式缺少置信度阈值

**问题：** agent.py:393 — 只要 `next_actions` 非空就触发 Navigate 模式，直接执行图快捷动作：

```python
if mode == "navigate" and context_data.get("next_actions"):
    best_action = context_data["next_actions"][0]
    # 无 confidence 检查，任意匹配都执行
```

但 `locate_and_get_context()` 中高置信度阈值是 0.85。理论上进入 navigate 时 similarity >= 0.85，但动作并没有独立的置信度检查。graph_store 中边的 `confidence` 初始值 1.0 且永不衰减。

#### 7.6 Neo4j 不可用时静默降级

**问题：** GraphStore.__init__() (graph_store.py:37-39) 捕获所有异常只打印警告，设 `self.driver = None`。之后所有方法检查 `if not self.driver: return None/[]/{}`。整个空间记忆核静默失效，用户和开发者无感知。

#### 7.7 MD5(截图Base64) 作为状态标识根本性不稳定

**问题：** agent.py:367-370 — 使用 `MD5(screenshot.base64_data)` 作为 UI state_id。这在两个层面失败：
1. **会话内不稳定：** JPEG/PNG 编码器每次输出可能略有差异（元数据、时间戳）
2. **会话间完全失效：** 同一界面在不同时刻的截图 base64 完全不同

**影响：** Layer 1（图精确状态匹配）实际上永远不会命中，`add_state_transition` 创建的 UIState 节点无法被后续步骤的 `get_current_state()` 匹配到。

#### 7.8 失败路径无惩罚学习

**问题：** `add_state_transition()` 中的频率只增不减（`ON MATCH SET r.frequency = r.frequency + 1`），`end_task()` 不更新已有边的置信度。任务失败时不会降低对应路径的 confidence 或 success_rate。

---

### 🟡 轻微缺陷

#### 7.9 Interact 回复捕获重复三次

agent.py:757-770 — 完全相同的 Interact 消息捕获逻辑重复了三次：

```python
# 第757-760行
if action.get("action_type") == "Interact" or ...:
    if hasattr(result, "message") and result.message:
        self._last_user_reply = result.message

# 第762-765行 — 完全相同
# 第767-770行 — 完全相同
```

#### 7.10 CONTACT_APP_BINDNG 拼写错误

memory_store.py:75 — 枚举名少字母 D：`CONTACT_APP_BINDNG = "contact_app_binding"`。虽然字符串值是正确的，但枚举名本身有误。代码中 6 处引用此枚举名（均为 `MemoryType.CONTACT_APP_BINDNG`）。

#### 7.11 硬编码 15 步轨迹限制

graph_store.py:252-304 — `get_task_trajectory()` 硬编码 `s0 → a1 → s1 → ... → a15 → s15` 的 Cypher 查询。超过 15 步的轨迹会被截断，且无法通过参数配置。

#### 7.12 semantic_layout 粒度过粗

agent.py:373 — 使用 `current_app` 名称作为 semantic_layout 的唯一输入，丢失了具体的页面/控件结构信息。MemoryManager 从未调用 `get_state_by_semantic()` 方法（发送 semantic_layout 给 Neo4j 做精确匹配的接口存在但未被使用）。

#### 7.13 MemoryManager 的 start_state_id 参数从未传入

MemoryManager.start_task() 接受 `start_state_id` 参数 (memory_manager.py:101)，但 agent.py:220 调用时从未传入：`self.memory_manager.start_task(task)`。导致 `_task_start_state_id` 始终为 None。

---

## 八、协调缺陷根因分析

### 问题根因：三层架构的中间层被架空

理想的分层架构应该是：

```
agent.py  →  MemoryManager (统一接口)  →  MemoryStore (语义) + GraphStore (空间)
```

但实际运行时：

```
agent.py ──直接访问──→ GraphStore.add_state_transition()
agent.py ──自己管理──→ _prev_state_id
agent.py → MemoryManager.locate_and_get_context() → MemoryStore + GraphStore (只读)
agent.py → MemoryManager.add_step() → MemoryStore (只写语义)
agent.py → MemoryManager.start_task() / end_task() (生命周期，但状态不传)
```

MemoryManager 被架空为"只读查询 + 语义记录"的角色，图构建和状态追踪的实际权力在 agent.py 手中。这导致：
1. **状态不一致：** agent.py 的 `_prev_state_id` 和 MemoryManager 的 `_current_state_id` 各自演化
2. **抽象泄漏：** agent.py 需要知道 GraphStore 的存在和接口
3. **改动困难：** 记忆系统的任何改动需要同时修改 agent.py 和 memory_manager.py

### 修复方向

**短期（低成本）：**
- 删除 clarify.py，删除 agent.py 中重复的 Interact 捕获
- 修复 CONTACT_APP_BINDNG 拼写
- MemoryStore.search() 改用 FAISS index.search()
- Navigate 模式添加 `CONFIDENCE_THRESHOLD >= 0.8` 检查

**中期（结构修复）：**
- 将 `_prev_state_id` 管理移入 MemoryManager，agent.py 通过 `memory_manager.set_current_state()` 更新
- 将 `add_state_transition()` 封装为 MemoryManager 方法，agent.py 不再直接访问 graph_store
- MemoryManager.start_task() 接受 start_state_id，end_task() 接受 end_state_id
- 为 agent.py 添加统一的 `_update_memory(thinking, action, app, state_id)` 入口点

**长期（架构重构）：**
- 替换 MD5 截图哈希为 View Hierarchy Hash (Android: uiautomator dump) 或 VLM semantic layout
- MemoryStore 升级到 embedding-3 client (统一 2048d 嵌入空间)
- 添加 ShortTermMemory 组件维持跨步购物意图
- 实现失败路径的 confidence 惩罚机制
- Neo4j 不可用时在 UI 层给用户可见提示

---

## 九、数据流图

```
User Task: "给张三发微信说晚上见面"

PhoneAgent.run(task)
│
├─ memory_manager.start_task(task)
│   └─ _extract_from_task(): regex → 联系人"张三", App"微信"
│       └─ MemoryStore.add(CONTACT: "张三")
│       └─ MemoryStore.add(APP_USAGE: "微信")
│
└─ [Loop] _execute_step()
    │
    ├─ device_factory.get_screenshot() → base64 + w/h
    ├─ device_factory.get_current_app() → "微信"
    │
    ├─ memory_manager.locate_and_get_context(ui_hash, "微信", task)
    │   │
    │   ├─ graph_store.find_similar_tasks("给张三发微信...")
    │   │   ├─ TaskIndex.search() → embedding-3 FAISS 2048d → [(task_id, 0.72)]
    │   │   └─ fallback: Neo4j N-gram match → [{description, similarity}]
    │   │
    │   ├─ graph_store.get_task_trajectory(task_id) → {steps: [...], app}
    │   │
    │   ├─ _condense_trajectory_context() → "【行动参考】发送消息(微信·5次)：搜索→张三→输入→发送"
    │   │
    │   ├─ get_relevant_context(task) → "⚡ 联系张三：推荐微信(5次)"
    │   │
    │   └─ MemoryStore.search(UI_STATE) → fallback context
    │
    │   return {mode: "explore", semantic_context: "...", max_similarity: 0.72}
    │
    ├─ [if is_first and max_sim < 0.60] _clarify_task_if_needed()
    │
    ├─ adapter.build_messages() → messages
    ├─ inject semantic_context into last user message text
    ├─ model_client.request(messages) → ModelResponse(thinking, action)
    │
    ├─ parse_action() → {"action": "Tap", "element": [500, 300]}
    ├─ action_handler.execute() → ActionResult(success=True)
    │
    ├─ memory_manager.add_step(thinking, action, "微信")
    │   ├─ _track_app_usage("微信")
    │   ├─ _learn_from_action({"action": "Type_Name", "text": "张三"})
    │   └─ _learn_from_thinking("在微信中找到张三的聊天窗口...")
    │
    ├─ ⚠️ graph_store.add_state_transition(prev, current, action, task)
    │
    └─ context.append(assistant_message)

Task完成:
  memory_manager.end_task(success=True, result, end_state)
  ├─ MemoryStore.add(TASK_HISTORY)
  ├─ _learn_successful_pattern() → TASK_PATTERN + CONTACT_APP_BINDNG
  └─ _save_pending_trajectory() → pending_trajectories.json
```

---

## 十、文件规模与复杂度

| 文件 | 行数 | 圈复杂度风险 | 核心职责 |
|------|------|-------------|---------|
| agent.py | 879 | 中 | Agent主循环 + 5种模型分支 + 记忆协调 |
| memory/memory_manager.py | 1093 | 高 | 记忆调度+上下文构建+自动学习+图桥接 |
| memory/memory_store.py | 549 | 低 | FAISS向量存储+SimpleEmbedder |
| memory/graph_store.py | 433 | 中 | Neo4j图+Cypher查询+TaskIndex |
| model/adapters.py | ~800 | 中 | 5个适配器的消息构建+解析 |
| actions/handler*.py | ~1500 | 中 | 6个平台/模型专用处理器 |
| memory/task_index.py | 109 | 低 | FAISS+EmbeddingClient任务索引 |
| memory/embedding_client.py | 48 | 低 | embedding-3 HTTP客户端 |
| clarify.py | 55 | 低 | ⚠️ 孤立/重复代码 |
| **核心总计** | **~5450** | | |

---

*本文档基于 2026-05-03 对 `phone_agent/` 全部源码的逐文件审计，覆盖所有 30+ 源文件的完整分析。*
