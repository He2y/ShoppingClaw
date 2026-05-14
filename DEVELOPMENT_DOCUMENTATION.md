# ClawGUI-Agent 系统开发文档 v1.0

> 日期：2026-05-11
> 用途：指导后续学术论文写作，全面记录系统架构、功能模块、图谱检索与记忆管理机制
> 代码基线：commit 4fbb1ae

---

## 目录

1. [系统概览](#1-系统概览)
2. [Agent 执行引擎](#2-agent-执行引擎)
3. [双核记忆引擎](#3-双核记忆引擎)
4. [会话产品记忆系统](#4-会话产品记忆系统)
5. [图谱检索机制](#5-图谱检索机制)
6. [模型适配器体系](#6-模型适配器体系)
7. [安全防护机制](#7-安全防护机制)
8. [关键数据流](#8-关键数据流)
9. [文件清单](#9-文件清单)
10. [学术贡献点](#10-学术贡献点)

---

## 1. 系统概览

### 1.1 系统定位

ClawGUI-Agent 是一个 VLM 驱动的 GUI 手机自动化框架，专注于**长任务购物场景**。它实现了"截图 → 推理 → 动作"的闭环控制循环，并通过双核记忆引擎（语义核 + 空间核）提供跨会话的个性化能力。

### 1.2 核心创新

| 创新点 | 描述 | 对应组件 |
|--------|------|---------|
| **双核记忆引擎** | FAISS 语义向量存储 + Neo4j 空间图谱存储，统一状态管理 | MemoryStore + GraphStore + StateManager |
| **会话结构化记忆** | 任务内产品追踪（商品名/价格/规格），进展摘要，停滞检测 | SessionMemory |
| **多层上下文注入** | 4 层注入策略：进度摘要 → 详细记忆 → 图谱语义 → 安全提示 | agent.py Phase ⑥ |
| **按需记忆供给** | 基于不确定性关键词和停滞检测的自动记忆触发，免模型训练 | SessionMemory.should_trigger_detailed_injection |
| **图谱路径规划** | GraphRAG 三层匹配（TaskIndex FAISS → Neo4j N-gram → MemoryStore FAISS） | MemoryManager.locate_and_get_context |
| **SpecGuard 安全网** | Prompt+代码双重防护，确定性地阻止购物场景下的错误购买操作 | agent.py _spec_guard_check |

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
│  run(task): 14个阶段的单步执行循环                                 │
│    ① 截图采集  ② 记忆查找  ②.5 SessionMemory压缩                 │
│    ③ HITL澄清  ④ 导航检查  ⑤ 消息构建  ⑥ 多层上下文注入          │
│    ⑦ VLM推理  ⑧ 动作解析+SpecGuard  ⑨ 动作执行                   │
│    ⑩ 记忆更新  ⑪ Interact捕获  ⑫ 完成检测                       │
│    ⑬ 轨迹记录  ⑭ 返回结果                                        │
└──┬──────────────┬──────────────┬──────────────┬──────────────────┘
   │              │              │              │
   ▼              ▼              ▼              ▼
┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────┐
│  Model   │ │  Action  │ │  Device  │ │  Memory System   │
│ Adapters │ │ Handlers │ │  Factory │ │  (双核记忆引擎)   │
│ (5个)    │ │ (6个)    │ │          │ │                  │
└──────────┘ └──────────┘ └─────┬────┘ │ ┌──────────────┐ │
                                │       │ │ Semantic Core│ │
                    ┌───────────┼───┐   │ │ (FAISS 2048d)│ │
                    ▼           ▼   ▼   │ │ embedding-3  │ │
                  ADB         HDC  XCTEST│ └──────────────┘ │
                  (Android) (Harmony) (iOS)│ ┌──────────────┐ │
                                          │ │ Spatial Core │ │
                                          │ │ (Neo4j Graph)│ │
                                          │ │ + TaskIndex  │ │
                                          │ │ (FAISS 2048d)│ │
                                          │ │              │ │
                                          │ │ StateManager │ │
                                          │ │ +SessionMemory│ │
                                          │ └──────────────┘ │
                                          └──────────────────┘
```

---

## 2. Agent 执行引擎

### 2.1 PhoneAgent 类结构

**文件**: `phone_agent/agent.py` (998行)

```python
class PhoneAgent:
    def __init__(self, model_config, agent_config,
                 confirmation_callback, takeover_callback,
                 clarification_callback):
        # 1. 模型解析：检测模型类型 → 选择适配器 + 专用Handler
        # 2. 默认ActionHandler（AutoGLM）
        # 3. 初始化_context, _step_count, _current_task
        # 4. 可选组件：GUITracer, MemoryManager, ClarificationAgent
```

**关键属性**:

| 属性 | 类型 | 用途 |
|------|------|------|
| `_model_type` | `ModelType` | 5种之一：AUTOGLM/UITARS/QWENVL/MAIUI/GUIOWL |
| `_adapter` | `ModelAdapter` | 负责消息构建、上下文管理、响应解析 |
| `_specialized_handler` | `ActionHandler*` | 非AutoGLM模型的专用动作处理器 |
| `action_handler` | `ActionHandler` | AutoGLM默认动作处理器 |
| `memory_manager` | `MemoryManager` | 记忆系统统一入口 |
| `clarification_agent` | `ClarificationAgent` | HITL任务澄清子代理 |
| `tracer` | `GUITracer` | 执行轨迹记录器 |

### 2.2 任务执行循环

```
run(task):
  ├─ 重置状态 (_context, _step_count, _current_task)
  ├─ 清空适配器历史 (QwenVL/GUI-Owl)
  ├─ tracer.start_task(task, model)
  ├─ memory_manager.start_task(task)
  │   ├─ 重置 SessionMemory + 检测平台
  │   ├─ 提取初始偏好 (_extract_from_task)
  │   └─ StateManager.start_task()
  │
  ├─ _execute_step(is_first=True)  ← 第一步（携带任务描述）
  │   └─ if finished → end_task() → return
  │
  └─ while step_count < max_steps:
       _execute_step(is_first=False)  ← 后续步骤
       └─ if finished → end_task() → return

end_task():
  ├─ memory_manager.end_task(success, result, end_state)
  │   ├─ MemoryStore.add(TASK_HISTORY, session_memory_data)
  │   ├─ _learn_successful_pattern() → TASK_PATTERN
  │   ├─ _learn_contact_app_binding() → CONTACT_APP_BINDING
  │   └─ _save_pending_trajectory() → pending_trajectories.json
  └─ tracer.end_task(result, total_steps) → episode.json
```

### 2.3 _execute_step() 详细阶段

#### Phase ①: 截图采集 (行 399-404)

```python
screenshot = device_factory.get_screenshot(device_id)  # → {base64, width, height}
current_app = device_factory.get_current_app(device_id) # → "京东" / "home_screen"
```

#### Phase ②: 记忆查找 — GraphRAG 三层匹配 (行 407-423)

```python
ui_hash = MD5(screenshot.base64_data)
semantic_layout = current_app or "home_screen"

context_data = memory_manager.locate_and_get_context(ui_hash, semantic_layout, task)
# → {mode: "explore"|"navigate", max_similarity, semantic_context, next_actions, current_state_id}
```

三层匹配策略（详见 §5）：

| 层 | 方法 | 阈值 | 行为 |
|----|------|------|------|
| Layer 1 | TaskIndex FAISS (embedding-3, 2048d) | ≥0.85 → Navigate; 0.60-0.85 → Explore+注入轨迹 | 语义向量搜索历史任务 |
| Layer 2 | Neo4j N-gram 关键词回退 | 中文N-gram + 令牌重叠计分 | FAISS零结果时的回退 |
| Layer 3 | MemoryStore FAISS UI状态回退 | 搜索 UI_STATE 类型 | 补充页面特征参考 |

#### Phase ②.5: SessionMemory 压缩 (行 426-427)

```python
if current_app:
    memory_manager.compress_session_history()
    # 每5步触发：VLM压缩5条模板摘要 → 1条压缩摘要
```

#### Phase ③: HITL 任务澄清 (行 429-445)

仅在第一步执行：

```python
if is_first and self.clarification_agent:
    clarify_result = clarification_agent.check_and_clarify(
        task, image_base64, current_app,
        memory_context=context_data.get("semantic_context", "")
    )
    if clarify_result.needs_clarification:
        task = clarify_result.clarified_task  # 用澄清后的任务替换
        # 重新评估记忆上下文
        context_data = memory_manager.locate_and_get_context(...)
```

**ClarificationAgent 工作流** (clarify.py: 281行):
1. `_detect_ambiguity()` → VLM 判断任务是否有足够信息（temperature=0.1）
2. 若模糊 → `_ask_user()` → 通过 callback 或 stdin 获取用户补充
3. `_reconstruct_task()` → VLM 将原始任务+用户回答重写为 ≤50字符的明确指令

#### Phase ④: 导航模式检查 (行 447-496)

```python
if mode == "navigate" and context_data.get("next_actions"):
    best_action = context_data["next_actions"][0]
    if best_action.get("confidence", 0) >= 0.8:  # 置信度阈值
        # 直接执行图谱快捷动作，完全绕过 VLM
        result = action_handler.execute(action, width, height)
        return StepResult(...)  # thinking="[Graph Shortcut Navigated]"
    # 低于阈值 → 回退到 explore 模式，继续 VLM 推理
```

#### Phase ⑤: 消息构建 (行 501-572)

按模型类型分为两条路径：

| 路径 | 模型 | 消息策略 | 图片限制 |
|------|------|---------|---------|
| 专用Handler | UI-TARS, QwenVL, MAIUI, GUI-Owl | 通过 adapter.build_messages() | 1-5张 |
| AutoGLM | AutoGLM/GLM-4V | MessageBuilder 手动构建 | 无限制（每步移除上一张图片） |

AutoGLM 第一步特殊处理：
- 通过 `build_personalized_prompt()` 注入个性化系统提示
- 包含联系人-应用绑定、购物偏好、任务历史

#### Phase ⑥: 4层上下文注入 (行 574-624)

**这是本系统的核心创新之一**。4层注入按优先级排序：

```
Layer 1 [📋 进度摘要] ← SessionMemory.get_context_for_injection("summary")
  ├─ 永远注入（每步）
  └─ 内容：任务、平台、当前商品（名称+价格+规格）、购物车摘要、最近3步

Layer 2 [🔍 详细记忆] ← SessionMemory.get_context_for_injection("detailed")
  ├─ 触发注入（不确定性信号 OR 停滞检测）
  └─ 内容：全部已浏览商品（名称/价格/规格/状态/首次出现步骤）、购物车、
      用户约束、完整步骤历史

Layer 3 [🛒 记忆参考] ← context_data["semantic_context"]
  ├─ 来自 locate_and_get_context() 的结果
  └─ 内容：联系人-应用绑定推荐、相似任务轨迹、FAISS UI状态参考

Layer 4 [⚠️ 安全提示] ← _detect_critical_scenario()
  ├─ 购物App + 规格页面时注入
  └─ 内容：强制性 Interact 操作提示
```

**触发检测算法** (SessionMemory.should_trigger_detailed_injection)：

```python
def should_trigger_detailed_injection(self, thinking: str) -> bool:
    # 信号1: 不确定性关键词
    uncertainty_keywords = [
        "不确定", "忘记了", "之前看到", "那个商品", "价格是多少",
        "哪个", "记得", "我不确定", "记不清", "刚刚看", "前面看到",
        "not sure", "forgot", "remember", "which one",
    ]
    if any(kw.lower() in thinking.lower() for kw in uncertainty_keywords):
        return True
    # 信号2: 停滞检测
    if self._consecutive_same_action >= 2:
        return True
    return False
```

#### Phase ⑦: VLM 推理 (行 626-687)

```python
response = self.model_client.request(self._context)
# → ModelResponse(thinking, action, raw_content,
#                 time_to_first_token, time_to_thinking_end, total_time)
```

流式输出：thinking 实时打印，action 缓冲。使用动作标记缓冲区技术（`finish(message=`, `do(action=`, `<tool_call>`, `<answer>`）精确分离 thinking 和 action。

#### Phase ⑧: 动作解析 + SpecGuard (行 689-783)

**专用Handler路径** (UI-TARS/QwenVL/MAI-UI/GUI-Owl):
```
专用Handler.parse_response(raw_content) → typed action object
专用Handler.execute(parsed_action, width, height) → ActionResult
```

**AutoGLM 通用路径**:
```
parse_action(response.action)
  ├─ _extract_function_call() → 括号深度追踪，截断尾部自然语言
  ├─ <tool_call> JSON → 解析 {"name": "mobile_use", "arguments": {...}}
  ├─ 直接JSON → json.loads
  ├─ Type/Type_Name特殊处理 → 正则提取含特殊字符的text参数
  └─ AST安全解析 → ast.parse(mode="eval") + ast.literal_eval

_spec_guard_check(action, thinking, current_app)  ← 代码级安全网
  ├─ 非购物App → 放行
  ├─ 已是Interact → 放行
  ├─ thinking包含规格关键词 + 购买意图 → 确定性覆写为Interact
  └─ 返回替换action或None
```

**支持的操作类型 (13种)**：
Launch, Tap, Type, Type_Name, Swipe, Back, Home, Double Tap, Long Press, Wait, Take_over, Interact, finish

**坐标转换**：`pixel_x = int(rel_x / 1000 * screen_width)`

#### Phase ⑨: 动作执行 (行 774-783)

```python
result = action_handler.execute(action, width, height) → ActionResult
# 内部按action_type调度到具体handler方法
```

#### Phase ⑩-⑭: 后处理

```
⑩ 记忆更新: memory_manager.add_step(thinking, action, current_app)
     ├─ _track_app_usage() → APP_USAGE 记忆 (会话级去重)
     ├─ _learn_from_action() → 联系人/App/搜索模式提取
     ├─ _learn_from_thinking() → 联系人/偏好提取 + 产品提取
     └─ _update_session_memory() → 产品追踪 + 步骤摘要 + 约束提取
   + update_state_and_transition() → 图状态转换记录
   + SessionMemory verbose日志

⑪ Interact回复捕获: _last_user_reply = result.message
⑫ 完成检测: finished = action._metadata == "finish" or result.should_finish
⑬ 轨迹记录: tracer.record_step(..., session_memory_snapshot)
⑭ 返回结果: StepResult(success, finished, action, thinking, message)
```

---

## 3. 双核记忆引擎

### 3.1 架构总览

```
agent.py
   │
   ▼
MemoryManager (统一调度枢纽 — 1464行)
   │           │           │
   ▼           ▼           ▼
MemoryStore  GraphStore  StateManager
(语义核)     (空间核)     (状态核)
FAISS 2048d Neo4j        统一状态追踪
```

### 3.2 语义记忆核 — MemoryStore (FAISS 2048d)

**文件**: `phone_agent/memory/memory_store.py` (666行)

**存储层**:
```
memory_db/{user_id}/
  ├── memories_meta.json   ← 所有记忆的元数据 (JSON)
  └── embeddings.npy       ← 2048维向量 (NumPy)
```

**嵌入器 (双级回退)**:
1. `EmbeddingClient` (embedding-3 API, 2048d) ← 主嵌入器
2. `SimpleEmbedder` (字符哈希, 128d → 补零到2048d) ← 回退嵌入器

**搜索**: FAISS IndexFlatIP + L2归一化 = 余弦相似度
- 得分 = `similarity * 0.7 + importance * 0.3`
- 暴力搜索作为 FAISS 的后备方案

**MemoryType 枚举 (14种)**:

| 类型 | 重要性 | 说明 |
|------|--------|------|
| USER_PREFERENCE | 0.6 | 用户设置/使用偏好 |
| CONTACT | 0.7 | 联系人信息 |
| CONTACT_APP_BINDING | 0.8 | 联系人→常用App绑定（频率加权） |
| APP_USAGE | 0.5 | 应用使用记录 |
| TASK_HISTORY | 0.4 | 任务执行历史 |
| TASK_PATTERN | 0.6 | 任务模式/流程 |
| USER_CORRECTION | 1.0 | 用户纠正（最高优先级） |
| PRODUCT_PREFERENCE | 0.5 | 商品品类偏好 |
| PRICE_SENSITIVITY | 0.5 | 价格敏感度 |
| BRAND_AFFINITY | 0.5 | 品牌忠诚度 |
| SCENE_RECOMMENDATION | 0.5 | 场景推荐 |
| UI_STATE | 0.4 | UI状态特征 |
| UI_TRANSITION | 0.4 | UI状态转换 |
| GENERAL | — | 通用知识 |

**记忆生命周期**:
1. **创建**: 自动提取（从任务文本/thinking/action）或手动添加
2. **去重**: FAISS 余弦相似度 ≥0.85 时更新现有记忆，不新建
3. **检索**: FAISS 向量搜索 → 得分 = similarity*0.7 + importance*0.3
4. **访问更新**: 被检索后 access_count+1, importance+0.05 (上限1.0)
5. **过期**: 数量超过 max_memories(10000) 时，删除最低 (importance, last_accessed) 排序的条目
6. **持久化**: `memories_meta.json` + `embeddings.npy`，每次变更时保存

### 3.3 空间记忆核 — GraphStore (Neo4j + TaskIndex)

**文件**: `phone_agent/memory/graph_store.py` (433行)

**图数据模型**:

```
  ┌──────────────┐     NEXT_ACTION      ┌──────────┐     PRODUCES      ┌──────────────┐
  │   UIState    │ ──────────────────→  │  Action  │ ────────────────→ │   UIState    │
  │ state_id     │   {confidence,       │ action_id│   {success_rate}  │ state_id     │
  │ app          │    frequency}        │ type     │                    │ app          │
  │ semantic_layout│                    │ target   │                    │ semantic_layout│
  └──────────────┘                      └──────────┘                    └──────────────┘
        ↑                                                                    ↑
        │ STARTS_AT                                                          │ ENDS_AT
        │                              ┌──────────────┐                      │
        └──────────────────────────────│  TaskTarget  │──────────────────────┘
                                       │ target_id    │  {success: bool}
                                       │ description  │
                                       │ app          │
                                       │ success      │
                                       │ committed_at │
                                       └──────────────┘
```

**Node 类型**: UIState, Action, TaskTarget
**Edge 类型**: NEXT_ACTION, PRODUCES, STARTS_AT, ENDS_AT

**TaskIndex (FAISS + EmbeddingClient)**:

```
嵌入维度: 2048d (embedding-3 API)
索引类型: FAISS IndexFlatIP + L2归一化 = 余弦相似度
存储:
  memory_db/{user_id}/
    ├── task_index.npy   ← FAISS 向量
    └── task_ids.json    ← 任务ID列表
```

**轨迹提交流程**:
```
任务完成 → _save_pending_trajectory() → pending_trajectories.json (本地暂存, 最多20条)
                                              ↓
                              memory_manager.commit_pending(index=0) (手动调用)
                                              ↓
                              graph_store.commit_task_trajectory() → Neo4j + TaskIndex
```

### 3.4 状态管理核 — StateManager

**文件**: `phone_agent/memory/state_manager.py` (101行)

```python
class StateManager:
    """统一状态追踪器 — 解决 agent.py/MemoryManager 状态分裂"""

    def compute_state_id(screenshot_hash, semantic_layout) -> str:
        # "state_{semantic_layout}_{screenshot_hash[:8]}"
        # 结合语义上下文 + 部分哈希 → 允许相似页面匹配

    def update_state(new_state_id) -> tuple[str | None, str]:
        # 返回 (prev_state, current_state)，原子性更新

    def start_task(initial_state_id=None)
    def end_task(final_state_id=None)
    def get_current_state() -> str | None
    def get_prev_state() -> str | None
    def get_state_history() -> list[str]
    def reset()
```

**状态 ID 设计**: `state_{semantic_layout}_{screenshot_hash[:8]}`

- `semantic_layout`：当前 App 名称（如 "京东"）
- `screenshot_hash[:8]`：MD5 前 8 位（允许同 App 内不同页面区分）
- 组合后可在会话内稳定识别同一页面

### 3.5 MemoryManager 统一 API

**文件**: `phone_agent/memory/memory_manager.py` (1464行)

**任务生命周期**:
```
start_task(task, start_state_id)       → 提取实体，初始化 StateManager + SessionMemory
end_task(success, result, end_state)   → 记录历史，学习模式，保存轨迹
```

**上下文检索**:
```
locate_and_get_context(hash, layout, task) → {mode, context, actions, state_id}
get_relevant_context(task)              → 个性化上下文字符串
compress_session_history()              → VLM压缩每5步摘要
```

**自动学习**:
```
add_step(thinking, action, app)         → 自动提取管线:
    ├─ _track_app_usage(app)            → APP_USAGE 记忆
    ├─ _learn_from_action(action)       → 联系人/App/搜索模式
    ├─ _learn_from_thinking(thinking)   → 联系人/偏好/产品信息
    └─ _update_session_memory(...)      → 产品追踪 + 步骤摘要
```

**状态管理**:
```
update_state_and_transition(hash, layout, action, task) → current_state_id
get_current_state_id()                  → 委托给 StateManager
```

---

## 4. 会话产品记忆系统

### 4.1 设计动机

传统 GUI Agent 在长任务中面临三种失败模式（UI-Copilot 论文验证）：

| 失败模式 | 占比 | 根因 | 本系统解决方案 |
|---------|------|------|------------|
| Progress Confusion | 43.8-66.7% | Agent 忘记已完成步骤 | StepSummary 进展链 + 每步注入 |
| Memory Degradation | 13.3-21.8% | 长上下文导致信息丢失 | 结构化 SessionMemory + 按需详细注入 |
| Math Hallucination | 6.7-10.9% | VLM 数值计算不可靠 | 产品价格结构化提取 + 约束追踪 |

### 4.2 数据模型

**文件**: `phone_agent/memory/session_memory.py` (343行)

```python
@dataclass
class ProductInfo:
    name: str                              # 商品名称
    price: float | None = None             # 价格
    specs: dict[str, str] = {}             # {"颜色": "黑色", "尺码": "42"}
    source_page: str = ""                  # "product_detail" | "search_result" | "cart"
    status: str = "viewed"                 # "viewed" | "added_to_cart" | "compared"
    first_seen_step: int = 0               # 首次出现的步骤编号

@dataclass
class StepSummary:
    step: int                              # 步骤编号
    summary: str                           # 一句话摘要
    action_type: str                       # 动作类型
    page_type: str = ""                    # 页面类型
    timestamp: str = ""                    # ISO时间戳

@dataclass
class SessionMemory:
    task: str = ""                         # 当前任务描述
    platform: str = ""                     # 购物平台（京东/淘宝/...）
    current_product: ProductInfo | None    # 当前正在查看的商品
    viewed_products: list[ProductInfo]     # 全部已浏览商品
    cart_items: list[ProductInfo]          # 购物车
    completed_steps: list[StepSummary]     # 完成步骤摘要
    constraints: dict[str, str] = {}       # 用户约束（预算/品牌）
```

### 4.3 生命周期

```
MemoryManager.start_task()  → SessionMemory.reset() + 填充 task/platform
MemoryManager.add_step()    → _update_session_memory():
    ├─ _extract_product_from_text(thinking) → 商品名/价格/规格
    ├─ _extract_constraints_from_text(thinking) → 预算/品牌约束
    ├─ _generate_step_summary(action, app, thinking) → 模板化摘要
    └─ SessionMemory.add_step_summary(summary)
agent.py Phase ⑥            → SessionMemory.get_context_for_injection()
MemoryManager.end_task()    → SessionMemory.to_dict() 序列化存入 MemoryStore
```

### 4.4 产品信息提取

基于正则模式的轻量提取（零延迟，跨模型兼容）：

```python
# 产品名称提取模式（示例）
PRODUCT_PATTERNS["product_name"] = [
    r'(?:商品|产品|看到|找到|点击|打开)(?:了|的是)?\s*[""「『]?(.{2,50})[""」『]?(?:，|。|售价|价格|¥)',
    r'(?:Nike|Adidas|Apple|Samsung|华为|小米|OPPO|vivo|李宁)[\w\s\-\u4e00-\u9fa5]{2,40}',
]

# 价格提取模式（示例）
PRODUCT_PATTERNS["price"] = [
    r'[¥￥]\s*(\d+(?:,\d{3})*(?:\.\d{1,2})?)',
    r'(\d+(?:\.\d{1,2})?)\s*(?:元|块|块钱)',
]

# 规格提取模式（示例）
PRODUCT_PATTERNS["spec"] = [
    r'(?:颜色|尺码|规格|容量|版本|型号)(?:是|为|：|:)\s*(.{1,20})',
    r'(黑|白|红|蓝|绿|灰|粉|紫|黄|金|银)(?:色|的)',
]
```

### 4.5 摘要生成

模板驱动的步骤摘要生成，基于动作类型拼装：

```python
def _generate_step_summary(action, current_app, thinking) -> str:
    templates = {
        "Launch":  f"已启动{current_app}",
        "Tap":     f"在{current_app}点击了「{target}」",
        "Type":    f"在{current_app}输入了「{text}」",
        "Swipe":   f"在{current_app}浏览页面",
        "Interact": f"向用户确认了选择",
        # ...
    }
    return f"[Step {n}] {summary}"
```

### 4.6 VLM 历史压缩

每5步触发一次，将模板摘要压缩为更简洁的进展描述：

```python
def compress_session_history() -> str | None:
    if not session_memory.should_compress():  # len(completed_steps) % 5 == 0
        return None

    # 用最近5条步骤摘要构建提示
    prompt = "将以下手机购物操作的 5 个步骤压缩为一句简洁的进展描述..."
    # 调用轻量 VLM (temperature=0.1, max_tokens=80)
    compressed = vlm_client.request(prompt)
    # 用1条压缩摘要替换5条模板摘要
    session_memory.completed_steps[-5:] = [StepSummary(summary=f"[压缩] {compressed}")]
```

### 4.7 学术指标设计

SessionMemory 支持以下量化分析指标：

| 指标 | 测量方法 | 预期效果 |
|------|---------|---------|
| 重复操作率 | SessionMemory.completed_steps 中连续相同 action_type 的比例 | 启用后下降 |
| 信息保留率 | viewed_products 中在后续步骤被 VLM thinking 引用的比例 | 启用后上升 |
| 上下文膨胀度 | 注入前后的 VLM 输入 token 数变化 | 摘要模式 vs 详细模式对比 |
| 触发精度 | should_trigger_detailed_injection 的 precision/recall | 消融实验 |

---

## 5. 图谱检索机制

### 5.1 三层匹配策略 (GraphRAG)

```
用户任务 → "在京东搜索Nike跑鞋，对比价格后加入购物车"

┌─────────────────────────────────────────────────────────────┐
│ Layer 1: TaskIndex FAISS 语义向量搜索                        │
│                                                             │
│   任务文本 → embedding-3 API → 2048d 向量                     │
│   → FAISS IndexFlatIP 余弦相似度搜索                          │
│   → 返回 top-3 相似历史任务 + 相似度分数                        │
│                                                             │
│   similarity >= 0.85 → mode = "navigate"                    │
│       ├─ 从 Neo4j 提取第一个动作                              │
│       └─ 直接执行（完全跳过 VLM）                              │
│                                                             │
│   similarity >= 0.60 → mode = "explore"                     │
│       ├─ 从 Neo4j 提取完整轨迹 (get_task_trajectory)           │
│       ├─ 压缩轨迹 (_condense_trajectory_context)              │
│       └─ 注入 VLM 上下文（附"严禁直接复用"警告）                 │
│                                                             │
│   similarity < 0.60 → 不注入图谱上下文                         │
└─────────────────────────────────────────────────────────────┘
                              ↓ (FAISS无结果时)
┌─────────────────────────────────────────────────────────────┐
│ Layer 2: Neo4j N-gram 关键词回退                              │
│                                                             │
│   中文N-gram分词 (1-3字符) + 令牌重叠计分                       │
│   关键词奖励: "外卖"→外卖类, "KFC"→具体店铺                     │
│   → Cypher CONTAINS 查询 → 返回候选任务列表                     │
└─────────────────────────────────────────────────────────────┘
                              ↓ (补充策略)
┌─────────────────────────────────────────────────────────────┐
│ Layer 3: MemoryStore FAISS UI状态回退                         │
│                                                             │
│   以 semantic_layout (当前App名) 为查询                         │
│   → 搜索 MemoryType.UI_STATE 类型记忆                          │
│   → 附加到 semantic_context 作为补充页面特征                     │
└─────────────────────────────────────────────────────────────┘
```

### 5.2 Navigate 模式触发条件

```
mode == "navigate" AND context_data["next_actions"] 非空
    AND best_action["confidence"] >= 0.8
    → 直接执行历史动作，零 VLM 调用
```

**设计原理**: 只有当历史轨迹与当前任务高度语义相似（FAISS ≥0.85）且动作置信度足够时，才跳过 VLM 推理。这平衡了效率与安全性。

### 5.3 轨迹压缩算法

```python
def _condense_steps(steps, max_steps=5) -> str:
    """将步骤列表凝练为箭头分隔的行动指南"""
    KEY_ACTIONS = {"click", "tap", "type", "input", "launch",
                   "open", "swipe", "scroll", "long_press"}
    key_steps = [s for s in steps
                 if s["action_type"].lower() in KEY_ACTIONS]
    parts = [s["action_target"][:8] or s["action_type"]
             for s in key_steps[:max_steps]]
    return " → ".join(parts)

def _condense_trajectory_context(similar_tasks, current_task) -> str:
    """最多2个相似任务，压缩为~200字符的参考文本"""
    return f"""[⚠️ 历史参考 - 仅供参考，严禁直接复用]
当前用户指令：「{current_task}」

以下历史任务与当前任务语义相似，但具体参数（商品名、规格等）可能不同：
"任务描述"(App·执行次数)：步骤1→步骤2→步骤3
"任务描述"(App·执行次数)：步骤1→步骤2→步骤3

请严格按照当前用户指令执行，历史轨迹仅作流程参考。"""
```

### 5.4 图谱边置信度机制

```python
# Edge: (UIState)-[NEXT_ACTION {confidence, frequency}]->(Action)

# 首次创建 (add_state_transition):
confidence = 1.0
frequency = 1

# 重复使用:
frequency += 1    # 边被遍历的次数
confidence = 1.0  # 当前版本（TODO: 失败惩罚学习 #7.8）
```

---

## 6. 模型适配器体系

### 6.1 适配器对比

| 适配器 | 目标模型 | 消息策略 | 图片限制 | 坐标空间 | 推理内容字段 |
|--------|---------|---------|---------|---------|-----------|
| AutoGLMAdapter | AutoGLM/GLM-4V | 追加式+移除图片 | 无限制 | [0, 1000] | thinking |
| UITarsAdapter | UI-TARS (Doubao) | 追加式 | 5张 | 绝对像素 (smart_resize) | thinking |
| QwenVLAdapter | Qwen2.5/3-VL | 重构建式 | 1张+历史文本 | [0, 999] | thinking |
| MAIUIAdapter | MAI-UI | 多消息追加 | 3张 | [0, 999] | reasoning_content |
| GUIOwlAdapter | GUI-Owl | 重构建式 | 1张 | [0, 999] | N/A |

### 6.2 消息策略差异

**追加式 (AutoGLM, UI-TARS, MAI-UI)**:
```
Step 1: [System] + [User: task + image1]
Step 2: [System] + [User: task + image1] + [Assistant: response1] + [User: image2]
Step 3: ... ← 累积，使用 limit_context() 控制长度
```
- 优势：保留完整对话历史
- 风险：长任务token累积

**重构建式 (QwenVL, GUI-Owl)**:
```
Step 1: [System] + [User: task + history_text + image1]
Step 2: [System] + [User: task + history_text + image2]
Step 3: ... ← 每轮重建，历史以文本形式嵌入
```
- 优势：始终只有1张图片，token可控
- 风险：历史文本可能丢失视觉细节

### 6.3 模型检测优先级

```
detect_model_type(model_name):
  1. gui[-_]?owl | guiowl           → GUIOWL
  2. ui[-_]?tars | tars | doubao.*ui | seed → UITARS
  3. qwen.*vl | qwen2.?5.*vl | qwen3.*vl   → QWENVL
  4. mai[-_]?ui | mai[-_]?mobile           → MAIUI
  5. autoglm | glm[-_]?4.?d*v              → AUTOGLM
  6. 默认                                 → AUTOGLM
```

---

## 7. 安全防护机制

### 7.1 SpecGuard 三层防护体系

**设计动机**: 在购物场景中，VLM 可能在规格选择页面跳过 `Interact` 操作，替用户做出错误的规格选择，导致购买错误商品。

**三层防护**:

```
Layer 1: System Prompt (始终生效)
  ├─ 位置: prompts_zh.py 核心原则 + SKU页面规则
  └─ 内容: "如果任何参数未指定 → 唯一合法操作：Interact"
              "严禁：点击购买按钮、加入购物车、接受默认选项"

Layer 2: Pre-inference 动态注入 (Phase ⑥ Layer 4)
  ├─ 触发: _detect_critical_scenario(current_app, screenshot)
  │   current_app ∈ _SHOPPING_APPS = {"淘宝","京东","天猫","拼多多","美团","饿了么",...}
  └─ 注入: "当前处于购物应用的规格选择页面。唯一合法的操作是 Interact..."

Layer 3: Post-inference 代码安全网 (Phase ⑧)
  ├─ 触发: _spec_guard_check(action, thinking, current_app)
  │   thinking 包含 _SPEC_KEYWORDS (规格/颜色/容量/尺码/口味/温度/糖度)
  │   AND thinking 包含 _PURCHASE_KEYWORDS (领券购买/立即购买/加入购物车/...)
  │   AND action ≠ Interact
  └─ 行为: 确定性地将 action 覆写为 Interact，附带上下文问题
           "有多种颜色可选，请问您喜欢哪个颜色？"
```

### 7.2 关键场景关键词

```python
_SPEC_KEYWORDS = [
    "规格", "颜色", "容量", "尺码", "口味", "温度", "糖度",
    "浓度", "选配", "可选规格", "机身颜色", "存储容量",
    "套餐类型", "配送方式"
]

_PURCHASE_KEYWORDS = [
    "领券购买", "立即购买", "加入购物车", "提交订单",
    "确认下单", "结算", "选好了", "确定", "立即抢购"
]
```

---

## 8. 关键数据流

### 8.1 完整任务数据流

```
User Task: "在京东买一双黑色Nike跑鞋，预算500以内"

PhoneAgent.run(task)
│
├─ memory_manager.start_task(task)
│   ├─ SessionMemory.reset() + platform="京东"
│   ├─ _extract_from_task() → "倾向于选择百亿补贴" (USER_PREFERENCE)
│   └─ StateManager.start_task()
│
└─ [Loop] _execute_step()
    │
    ├─ ① device_factory.get_screenshot() → base64 + w/h
    ├─ ① device_factory.get_current_app() → "京东"
    │
    ├─ ② memory_manager.locate_and_get_context(ui_hash, "京东", task)
    │   ├─ TaskIndex.search("在京东买...") → embedding-3 FAISS 2048d
    │   │   └─ 无匹配 (首次执行此任务)
    │   ├─ _find_by_ngram() → "京东"+"Nike"+"跑鞋"+"黑色"
    │   │   └─ 找到1条: "在京东搜索运动鞋"(相似度0.42)
    │   └─ MemoryStore.search("京东", UI_STATE)
    │       └─ 补充页面特征
    │   → {mode: "explore", semantic_context: "...", max_similarity: 0.42}
    │
    ├─ ②.5 compress_session_history() → 跳过 (步骤数<5)
    │
    ├─ ③ [is_first] clarification_agent.check_and_clarify()
    │   └─ VLM判断: CLEAR (平台明确=京东, 商品明确=Nike跑鞋, 规格明确=黑色)
    │   → needs_clarification=False
    │
    ├─ ⑤ AutoGLM消息构建
    │   ├─ build_personalized_prompt() → "🛒 购物偏好: 倾向于选择百亿补贴"
    │   └─ [System] + [User: task + 截图]
    │
    ├─ ⑥ 上下文注入:
    │   ├─ [📋] 任务: 在京东买...; 平台: 京东
    │   ├─ [🛒] 相似任务: "在京东搜索运动鞋"(京东·1次)
    │   └─ (购物App → Layer 4安全提示注入)
    │
    ├─ ⑦ VLM推理 → thinking: "当前在京东首页，需要搜索Nike跑鞋..."
    │            → action: do(action="Type", text="Nike跑鞋")
    │
    ├─ ⑧ parse_action("do(action=\"Type\", text=\"Nike跑鞋\")")
    │   └─ _extract_function_call → AST安全解析
    │   → action = {"_metadata": "do", "action": "Type", "text": "Nike跑鞋"}
    │
    ├─ ⑨ _handle_type → detect_adb_keyboard → clear → type → restore
    │   → result = ActionResult(success=True)
    │
    ├─ ⑩ memory_manager.add_step(thinking, action, "京东")
    │   ├─ _track_app_usage("京东") → 已在本会话记录，跳过
    │   ├─ _learn_from_action({"action": "Type", "text": "Nike跑鞋"})
    │   │   └─ _learn_search_pattern("Nike跑鞋") → TASK_PATTERN(shopping)
    │   ├─ _learn_from_thinking("当前在京东首页...")
    │   │   └─ 无联系人/偏好匹配
    │   └─ _update_session_memory(thinking, action, "京东")
    │       ├─ _extract_product_from_text → 无产品信息（thinking中无产品名）
    │       ├─ _generate_step_summary → "在京东输入了「Nike跑鞋」"
    │       └─ session_memory.add_step_summary("在京东输入了「Nike跑鞋」", "Type", "search")
    │   + update_state_and_transition(hash, "京东", action, task)
    │       ├─ StateManager.compute_state_id → "state_京东_a1b2c3d4"
    │       └─ GraphStore.add_state_transition(None, "state_京东_a1b2c3d4", ...)
    │
    ├─ ⑬ tracer.record_step(..., session_memory_snapshot)
    │   └─ {"viewed_products": 0, "cart_items": 0, "completed_steps": 1, ...}
    │
    └─ ⑭ return StepResult(success=True, finished=False)

[后续步骤类似，逐步积累产品信息和进展摘要]
```

### 8.2 记忆检索数据流

```
locate_and_get_context(ui_hash, semantic_layout, task)
│
├─ 0. 预填充: semantic_context = get_relevant_context(task)
│    ├─ MemoryStore 扫描 CONTACT_APP_BINDING → 频率排序推荐
│    ├─ MemoryStore.search(task) → 联系人/任务历史/偏好
│    └─ 格式化为: "⚡ 联系「X」推荐 **App** (N次)\n🛒 购物偏好..."
│
├─ Layer 1: TaskIndex FAISS 语义搜索
│    ├─ embedding_client.encode(task) → 2048d向量
│    ├─ task_index.search(query, top_k=3) → [(task_id, similarity), ...]
│    │   └─ FAISS IndexFlatIP + L2归一化 = 余弦相似度
│    └─ 对于每个匹配: Neo4j获取TaskTarget + 起始状态 + 第一个动作
│
├─ Layer 2: Neo4j N-gram回退 (Layer 1无结果时)
│    ├─ _tokenize_chinese(task) → n-grams (1-3字符集)
│    ├─ Cypher: MATCH (t:TaskTarget) WHERE t.description CONTAINS $ngram
│    ├─ 令牌重叠得分 + 关键词奖励
│    └─ 排序返回 top-k 候选
│
└─ Layer 3: MemoryStore UI状态回退
     ├─ MemoryStore.search(semantic_layout, top_k=3, min_importance=0.2)
     ├─ 过滤 MemoryType.UI_STATE
     └─ 附加到 semantic_context
```

---

## 9. 文件清单

| 文件 | 行数 | 复杂度 | 核心职责 |
|------|------|--------|---------|
| `agent.py` | 998 | 中 | Agent主循环 + 5种模型分支 + 4层上下文注入 + SpecGuard |
| `memory/memory_manager.py` | 1464 | 高 | 记忆调度+上下文构建+自动学习+状态协调+产品提取 |
| `memory/memory_store.py` | 666 | 中 | FAISS向量存储 2048d + 双级嵌入器 |
| `memory/session_memory.py` | 343 | 低 | 会话结构化产品追踪 + 摘要生成 + 上下文构建 |
| `memory/graph_store.py` | 433 | 中 | Neo4j图+Cypher查询+TaskIndex |
| `memory/state_manager.py` | 101 | 低 | 统一状态追踪 |
| `memory/task_index.py` | 108 | 低 | FAISS+EmbeddingClient任务索引 |
| `memory/embedding_client.py` | 48 | 低 | embedding-3 HTTP客户端 |
| `model/adapters.py` | ~880 | 中 | 5个适配器的消息构建+解析+坐标归一化 |
| `model/client.py` | 329 | 中 | OpenAI流式客户端+消息构建器 |
| `actions/handler.py` | 574 | 中 | AutoGLM动作解析+13种动作执行 |
| `actions/handler_uitars.py` | ~300 | 中 | UI-TARS专用处理器 |
| `actions/handler_qwenvl.py` | ~300 | 中 | QwenVL专用处理器 |
| `actions/handler_maiui.py` | ~250 | 中 | MAI-UI专用处理器 |
| `actions/handler_guiowl.py` | ~250 | 中 | GUI-Owl专用处理器 |
| `clarify.py` | 281 | 低 | HITL任务澄清子代理 |
| `tracer.py` | 122 | 低 | GUI执行轨迹录制 |
| `config/prompts_zh.py` | ~110 | 低 | 中文系统提示词(含SpecGuard+SessionMemory提示) |
| `config/prompts_en.py` | ~85 | 低 | 英文系统提示词 |
| `config/timing.py` | 167 | 低 | 统一操作延迟配置(均环境变量可覆盖) |
| `device_factory.py` | 167 | 低 | 跨平台设备抽象工厂(ADB/HDC/XCTEST) |
| **核心总计** | **~6900** | | |

---

## 10. 学术贡献点

### 10.1 核心claim

> 我们提出了一种面向长任务购物场景的 GUI Agent 记忆增强架构。通过双核记忆引擎（FAISS语义核 + Neo4j空间核）与会话结构化产品记忆（SessionMemory）的协同，Agent 在不增加模型训练成本的前提下，显著降低了长任务中的进度迷失、记忆衰退和数学幻觉问题。

### 10.2 可量化指标

1. **任务成功率** (Task Success Rate): 启用/禁用 SessionMemory 的长购物任务对比
2. **重复操作率** (Redundancy Rate): 连续相同动作在相同页面的比例
3. **信息保留率** (Information Retention): 会话早期产品在后续步骤中被引用的比率
4. **上下文效率** (Context Efficiency): token消耗量 / 任务步骤数
5. **图谱命中率** (Graph Hit Rate): Navigate模式直接执行的比例 vs VLM推理比例
6. **计算幻觉率** (Math Hallucination Rate): 购物场景中价格/折扣计算错误率

### 10.3 消融实验设计

| 实验组 | 配置 | 预期效果 |
|--------|------|---------|
| Full System | 全部组件启用 | 基准性能 |
| - SessionMemory | 移除Layer 1+2上下文注入 | 成功率下降，迷失率上升 |
| - Detailed Trigger | 仅保留摘要注入，关闭触发式详细注入 | 长任务中的信息丢失增加 |
| - GraphRAG | 仅使用MemoryStore FAISS，禁用Neo4j图 | Navigate快捷路径消失，步数增加 |
| - Compress | 禁用VLM历史压缩 | Token消耗上升 |
| - SpecGuard | 禁用安全防护 | 规格页面错误率上升 |

*本文档基于 2026-05-11 对 `phone_agent/` 全部源文件的探索分析 + commit 4fbb1ae 的变更追踪。*
