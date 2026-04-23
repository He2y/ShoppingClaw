# ClawGUI-Agent 系统架构详解

> 版本：2.0
> 日期：2026-04-23
> 状态：**已实现**（对比 CLAWGUI_UPGRADE_PLAN.md 目标）

---

## 一、整体架构

ClawGUI-Agent 是一个基于视觉-语言模型（VLM）的手机自动化框架，采用**双核记忆引擎**架构，在实时推理能力与历史知识复用之间取得平衡。

```
┌─────────────────────────────────────────────────────────┐
│                      CLI / WebUI                        │
└──────────────────┬────────────────────────────────────┘
                   │ task
                   ▼
┌─────────────────────────────────────────────────────────┐
│                     PhoneAgent                          │
│  _execute_step(): 截图 → 状态定位 → 上下文获取 → VLM推理 │
│                    → Action执行 → 记忆更新 → Trace记录   │
└────────┬──────────────────────────┬────────────────────┘
         │                          │
         ▼                          ▼
┌─────────────────┐      ┌─────────────────────────┐
│  Model Adapters │      │    Action Handlers      │
│  (5个VLM适配器) │      │  (平台+模型专用)        │
└─────────────────┘      └─────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────┐
│              双核记忆引擎 (Dual-Core Memory)             │
│  ┌──────────────────┐  ┌─────────────────────────────┐  │
│  │ 语义记忆核        │  │ 空间记忆核                  │  │
│  │ Semantic Memory  │  │ Spatial Memory             │  │
│  │ ─────────────── │  │ ─────────────────────────  │  │
│  │ FAISS Vector DB │  │ Neo4j Graph DB             │  │
│  │ • 用户偏好       │  │ • UI状态图谱               │  │
│  │ • 联系人绑定     │  │ • 任务轨迹                 │  │
│  │ • 商品偏好       │  │ • 状态转换                 │  │
│  │ • 购物习惯       │  │ • 快捷动作                 │  │
│  └──────────────────┘  └─────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────┐
│                  Device Backends                        │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐             │
│  │   ADB    │  │   HDC    │  │  XCTEST  │             │
│  │ (Android)│  │(HarmonyOS)│  │  (iOS)   │             │
│  └──────────┘  └──────────┘  └──────────┘             │
└─────────────────────────────────────────────────────────┘
```

---

## 二、Agent 执行流程详解

### 2.1 主循环入口

```python
def run(self, task: str) -> str:
    self._context = []          # 清空对话历史
    self._step_count = 0
    self._current_task = task
    self._last_state_hash = None

    if self.tracer:
        self.tracer.start_task(task, model=...)
    if self.memory_manager:
        self.memory_manager.start_task(task)   # ← 记忆系统感知新任务

    # 第一步（带任务描述）
    result = self._execute_step(task, is_first=True)
    if result.finished: return ...

    # 循环直到完成或超限
    while self._step_count < self.agent_config.max_steps:
        result = self._execute_step(is_first=False)
        if result.finished: return ...
    return "Max steps reached"
```

### 2.2 单步执行 `_execute_step()`

`_execute_step()` 是整个系统的核心，包含 **7个阶段**：

```
Phase 1: 屏幕采集 ──► Phase 2: 状态定位 ──► Phase 3: 模式决策
                              │                    │
                              ▼                    ▼
Phase 7: 追踪记录  ◄── Phase 6: 记忆更新  ◄── Phase 4: 消息构建
                              ▲                    │
                              │                    ▼
                    Phase 5: VLM推理 ◄── Action执行
```

#### Phase 1: 屏幕采集（第292-295行）

```python
device_factory = get_device_factory()
screenshot = device_factory.get_screenshot(self.agent_config.device_id)
current_app = device_factory.get_current_app(self.agent_config.device_id)
```

获取当前设备截图（Base64编码）和前台应用名称。

#### Phase 2: 状态定位（第297-314行）

```python
hasher = hashlib.md5()
hasher.update(screenshot.base64_data.encode('utf-8'))
ui_hash = hasher.hexdigest()
self._last_state_hash = f"state_{ui_hash}"

semantic_layout = current_app if current_app else "home_screen"

context_data = self.memory_manager.locate_and_get_context(
    ui_hash, semantic_layout, user_prompt or self._current_task
)
mode = context_data.get("mode", "explore")
current_state_id = context_data.get("current_state_id")
```

调用记忆系统的三层匹配策略，返回决策模式（navigate/explore）和上下文数据。

#### Phase 3: 模式决策（第316-356行）

**Navigate 模式（空间引导）**：
- 条件：Graph 精确匹配到高置信度快捷动作
- 行为：跳过 VLM 推理，直接构造并执行 Action
- 优势：零延迟、确定性高

```python
if mode == "navigate" and context_data.get("next_actions"):
    best_action = context_data["next_actions"][0]
    action = {"_metadata": "do", "action_type": best_action["type"], ...}
    result = self.action_handler.execute(action, ...)
    return StepResult(success=result.success, finished=..., action=action, ...)
```

**Explore 模式（语义推理）**：
- 条件：Graph 无匹配或置信度不足
- 行为：调用 VLM 进行完整视觉推理
- 输出：`🧭 知识图谱查询: 未匹配到当前界面特征，使用视觉大模型进行推理 (Explore Mode)`

#### Phase 4: 消息构建（第364-423行）

根据模型类型选择不同的消息构建策略：

**非AutoGLM模型（UI-TARS/QwenVL/MAI-UI/GUI-Owl）**：
```python
self._context = self._adapter.build_messages(
    task=user_prompt or self._current_task,
    image_base64=screenshot.base64_data,
    current_app=current_app,
    context=self._context,
    lang=self.agent_config.lang,
    screen_width=screenshot.width,
    screen_height=screenshot.height,
)
```

**AutoGLM模型**：
```python
system_prompt = build_personalized_prompt(system_prompt, self.memory_manager, user_prompt)
self._context.append(MessageBuilder.create_system_message(system_prompt))
self._context.append(MessageBuilder.create_user_message(
    text=f"{user_prompt}\n\n{build_screen_info(current_app)}",
    image_base64=screenshot.base64_data
))
```

#### Phase 5: 上下文注入与VLM推理（第425-490行）

**图谱语义上下文注入**（第425-437行）：

```python
extra_context = context_data.get("semantic_context", "") if self.memory_manager else ""
if extra_context and self._context:
    last_msg = self._context[-1]
    if isinstance(last_msg.get("content"), list):  # Vision message
        for item in last_msg["content"]:
            if item.get("type") == "text":
                item["text"] = item["text"].rstrip() + f"\n\n[记忆上下文]\n{extra_context}"
    elif isinstance(last_msg.get("content"), str):
        last_msg["content"] = last_msg["content"].rstrip() + f"\n\n[记忆上下文]\n{extra_context}"
```

注入的内容来自 `locate_and_get_context()` 的 `semantic_context` 字段，包含：
- 基于频率的联系人-应用推荐
- 相关任务历史
- 相似任务的完整动作轨迹

**VLM推理**：
```python
response = self.model_client.request(self._context)
# 流式输出 thinking，缓冲 action 部分
```

#### Phase 6: Action解析与执行（第502-591行）

**专用Handler解析**（UI-TARS/QwenVL等）：
```python
parsed_action = self._specialized_handler.parse_response(response.raw_content)
result = self._specialized_handler.execute(parsed_action, ...)
```

**通用Handler解析**（AutoGLM）：
```python
action = parse_action(response.action)
result = self.action_handler.execute(action, ...)
```

#### Phase 7: 记忆更新（第653-668行）

```python
if self.memory_manager:
    self.memory_manager.add_step(thinking, action, current_app)

    # 在线动态图构建（Phase 4）
    if hasattr(self, '_prev_state_id') and current_state_id:
        self.memory_manager.graph_store.add_state_transition(
            self._prev_state_id, current_state_id, action, self._current_task
        )
    self._prev_state_id = current_state_id
```

---

## 三、双核记忆引擎详解

### 3.1 语义记忆核（Semantic Memory — FAISS）

#### 数据结构

```python
class Memory:
    id: str                    # SHA256(content:type:timestamp)[:16]
    content: str               # 记忆文本
    memory_type: MemoryType    # 记忆类型
    importance: float           # 重要性 [0-1]
    embedding: list[float]      # 128维向量（字符级特征）
    metadata: dict             # 附加元数据
    access_count: int          # 访问次数
    created_at: str
    last_accessed: str
```

#### 记忆类型（MemoryType）

| 枚举值 | 用途 | 重要性默认值 |
|--------|------|-------------|
| `USER_PREFERENCE` | 用户设置偏好 | 0.6 |
| `CONTACT` | 联系人信息 | 0.7 |
| `CONTACT_APP_BINDNG` | 联系人-应用频率绑定 | 0.8 |
| `APP_USAGE` | 应用使用记录 | 0.5 |
| `TASK_HISTORY` | 任务执行历史 | 0.4 |
| `TASK_PATTERN` | 任务模式/流程 | 0.6 |
| `USER_CORRECTION` | 用户纠正反馈 | **1.0** |
| `PRODUCT_PREFERENCE` | 商品品类偏好 | 0.5 |
| `PRICE_SENSITIVITY` | 价格敏感度 | 0.5 |
| `BRAND_AFFINITY` | 品牌忠诚度 | 0.5 |
| `SCENE_RECOMMENDATION` | 场景推荐 | 0.5 |
| `UI_STATE` | UI状态特征 | 0.4 |
| `UI_TRANSITION` | UI状态转换 | 0.4 |

#### 向量嵌入（SimpleEmbedder）

```python
class SimpleEmbedder:
    dim: int = 128

    def encode(self, texts: list[str]) -> list[list[float]]:
        # 字符频率特征（位置加权）
        for char in text.lower():
            emb[ord(char) % dim] += 1.0 / (position + 1)

        # N-gram 特征 (2-gram)
        for bigram in text[i:i+2]:
            emb[hash(bigram) % dim] += 0.5

        # L2 归一化 → 余弦相似度
        emb = emb / norm(emb)
```

**局限性**：字符级哈希嵌入无法捕捉语义相似性。"京东点外卖"和"淘宝点外卖"可能有完全不同的字符分布，但语义相近。当前系统通过 `find_similar_tasks()` 的中文N-gram分词（单字+2gram+3gram）来补偿这一点。

#### 检索算法

```python
def search(self, query, memory_types, top_k, min_importance):
    query_emb = self.encode(query)
    results = []

    for memory in self.memories.values():
        # 类型过滤 + 重要性过滤
        if memory.memory_type not in memory_types: continue
        if memory.importance < min_importance: continue

        # 综合评分：相似度×0.7 + 重要性×0.3
        similarity = cosine_similarity(query_emb, memory.embedding)
        score = similarity * 0.7 + memory.importance * 0.3
        results.append((memory, score))

    results.sort(key=lambda x: x[1], reverse=True)
    return [mem for mem, score in results[:top_k]]
```

#### 持久化

```
memory_db/default/
├── memories_meta.json    # 记忆元数据（JSON）
└── embeddings.npy        # 向量数据（NumPy格式）
```

---

### 3.2 空间记忆核（Spatial Memory — Neo4j）

#### 图数据模型

```
                    ┌─────────────────────────────┐
                    │          UIState           │
                    │  state_id: "state_abc123"  │
                    │  app: "京东"               │
                    │  semantic_layout: "首页"   │
                    └──────────┬──────────────────┘
                               │
              ┌────────────────┼────────────────┐
              │ NEXT_ACTION    │ PRODUCES       │ NEXT_ACTION
              ▼                ▼                ▼
     ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
     │   Action     │  │   UIState    │  │   Action     │
     │  type: Tap   │──│ state_xyz    │──│ type: Swipe  │
     │  target: 搜索│  │              │  │  target: 上滑│
     └──────────────┘  └──────────────┘  └──────────────┘

     ┌────────────────────────────────────────────────────┐
     │               TaskTarget                          │
     │  target_id: "JD外卖_abc123"                     │
     │  description: "在京东点KFC外卖"                  │
     │  app: "京东"                                     │
     │  committed_at: timestamp()                       │
     └──────────────┬───────────────────────────────────┘
                    │ STARTS_AT / ENDS_AT
                    ▼
              [UIState ...]
```

#### 节点定义

| Label | 关键属性 | 说明 |
|-------|---------|------|
| `:UIState` | `state_id`, `app`, `semantic_layout` | 界面状态节点 |
| `:Action` | `action_id`, `type`, `target_desc`, `reasoning` | 动作节点（从边独立） |
| `:TaskTarget` | `target_id`, `description`, `app`, `success` | 任务目标节点 |

#### 边定义

| 关系 | 方向 | 属性 | 说明 |
|------|------|------|------|
| `[:NEXT_ACTION]` | UIState → Action | `confidence`, `frequency` | 状态→动作的置信度和频次 |
| `[:PRODUCES]` | Action → UIState | `success_rate` | 动作执行后的新状态 |
| `[:STARTS_AT]` | TaskTarget → UIState | — | 任务起始状态 |
| `[:ENDS_AT]` | TaskTarget → UIState | `success` | 任务终止状态 |

**注意**：`[:SOLVES_POPUP]` 边在升级计划中定义但**尚未实现**。当前系统没有弹窗检测和自动处理逻辑。

#### 核心查询方法

**1. MD5精确状态匹配** `get_current_state(state_hash)`:
```cypher
MATCH (s:UIState {state_id: $state_id}) RETURN s
```

**2. 快捷动作查询** `get_next_actions(state_hash, min_confidence)`:
```cypher
MATCH (s:UIState {state_id: $state_id})-[r:NEXT_ACTION]->(a:Action)
WHERE r.confidence >= $min_confidence
RETURN a.action_id, a.type, a.target_desc, r.confidence, r.frequency
ORDER BY r.confidence DESC, r.frequency DESC
```

**3. 相似任务检索** `find_similar_tasks(task_description, top_k)`:
```cypher
MATCH (t:TaskTarget)
WHERE toLower(t.description) CONTAINS $q0
   OR toLower(t.description) CONTAINS $q1
   ...
RETURN t.target_id, t.description, t.app, ...
ORDER BY r.frequency DESC
LIMIT $top_k
```

中文N-gram分词策略：
- 单字符：捕获单字关键词
- 2-gram：捕获常见词（如"外卖"、"京东"）
- 3-gram：捕获短语（如"KFC"、"闪购"）

**4. 完整轨迹获取** `get_task_trajectory(task_id)`:
```cypher
MATCH (t:TaskTarget {target_id: $task_id})
OPTIONAL MATCH (t)-[:STARTS_AT]->(s0)
OPTIONAL MATCH (s0)-[:NEXT_ACTION]->(a1:Action)-[:PRODUCES]->(s1)
...（最多15步）
RETURN description, app, state0, a1_type, a1_target, ..., state15, end_state
```

**5. 状态转换记录** `add_state_transition()`:
```cypher
MATCH (s1:UIState {state_id: $s1_id}), (s2:UIState {state_id: $s2_id})
MERGE (a:Action {action_id: $a_id})
MERGE (s1)-[r1:NEXT_ACTION]->(a)
  ON CREATE SET r1.confidence = 1.0, r1.frequency = 1
  ON MATCH   SET r1.frequency = r1.frequency + 1
MERGE (a)-[r2:PRODUCES]->(s2)
  ON CREATE SET r2.success_rate = 1.0
```

#### 轨迹提交机制

```
任务成功 → _save_pending_trajectory() → pending_trajectories.json
                                              ↓
                              scripts/review_trajectories.py
                                              ↓
                              commit_pending() → Neo4j
```

三层保障：
1. 轨迹先暂存到本地文件（pending）
2. 人工审核确认后再提交到图谱
3. 防止错误轨迹污染知识图谱

---

## 四、上下文注入机制

### 4.1 三层匹配策略

`memory_manager.locate_and_get_context()` 实现优先级递减的三层匹配：

```
用户任务描述
     │
     ▼
┌─────────────────────────────────────────────────────────┐
│ Layer 1: Graph MD5 精确匹配                              │
│ · 使用截图 Base64 的 MD5 哈希作为 state_id               │
│ · 匹配成功 → Navigate 模式（快捷动作直出）               │
│ · ⚠️ 问题：相同界面但不同截图 → 永远不命中               │
└────────────────────────┬────────────────────────────────┘
                         │ 未命中
                         ▼
┌─────────────────────────────────────────────────────────┐
│ Layer 2: Graph 任务语义匹配                             │
│ · 从任务描述提取中文 N-gram 关键词                       │
│ · 在 Neo4j 中搜索相似 TaskTarget 描述                    │
│ · 获取该任务的完整动作轨迹                              │
│ · 注入 semantic_context（相似轨迹供参考）               │
│ · 命中 → Explore 模式（VLM推理 + 轨迹上下文）           │
└────────────────────────┬────────────────────────────────┘
                         │ 未命中
                         ▼
┌─────────────────────────────────────────────────────────┐
│ Layer 3: FAISS 向量 fallback                            │
│ · 对 semantic_layout（当前APP名称）做向量相似度搜索      │
│ · 仅补充探索上下文                                      │
│ · 永不触发 Navigate                                    │
└─────────────────────────────────────────────────────────┘
```

### 4.2 上下文内容格式化

**Navigate模式的上下文**（直接执行，不注入VLM）：
- 无额外上下文，直接执行快捷动作

**Explore模式的上下文**（注入用户消息）：

```
【用户个性化信息 - 请严格按照以下信息选择应用】

**🎯 基于使用频率的应用推荐（必须遵循）:**
  ⚡ 联系「张三」：推荐使用 **微信** (使用5次) 而非 QQ (使用1次)
  ⚡ 联系「李四」：推荐使用 **钉钉** (已使用3次)

**📋 相关任务历史:**
  模式: 「在京东点外卖」→ 京东
  历史: 「给王五发消息」→ 微信

**其他信息:**
  ⚠️ 注意: 不要点击弹窗广告
```

**相似轨迹上下文**（当Layer 2命中时）：

```
[相似历史任务: 在京东点KFC外卖]
该任务曾在 京东 中成功完成，以下是完整动作轨迹供参考：
  步骤1: Tap → 搜索框
  步骤2: Type → KFC
  步骤3: Tap → KFC官方旗舰店
  步骤4: Tap → 套餐选择
  步骤5: Tap → 加入购物车
（注意：当前界面可能与历史轨迹不同，请根据实际截图调整动作）

【用户个性化信息...】（上述内容）
```

### 4.3 个性化Prompt构建（AutoGLM）

```python
def build_personalized_prompt(base_prompt, memory_manager, task):
    context = memory_manager.get_relevant_context(task)
    if not context:
        return base_prompt

    # 在"必须遵循的规则"前插入个性化上下文
    if "必须遵循的规则" in base_prompt:
        parts = base_prompt.split("必须遵循的规则")
        enhanced = parts[0] + f"\n\n{context}\n\n必须遵循的规则" + parts[1]
    else:
        enhanced = f"{base_prompt}\n\n{context}"
    return enhanced
```

---

## 五、与升级计划（CLAWGUI_UPGRADE_PLAN.md）的对比

### 5.1 实现情况总览

| 升级计划项 | 状态 | 说明 |
|-----------|------|------|
| 语义记忆类型扩展 | ✅ 完成 | 8种购物相关类型已定义 |
| 空间记忆核（Neo4j） | ✅ 完成 | graph_store.py 已实现 |
| 离线图谱构建脚本 | ✅ 完成 | scripts/import_manual_data.py |
| Phase 1-4 执行流程 | ✅ 完成 | _execute_step() 已覆盖 |
| 双轨决策模式 | ✅ 完成 | Navigate + Explore 模式 |
| 在线动态建图 | ⚠️ 部分 | add_state_transition() 已实现，但无RL反馈 |
| SHOPPING_CONTEXT短期记忆 | ❌ 未实现 | 无跨Prompt短期意图维持 |
| [:SOLVES_POPUP] 边 | ❌ 未实现 | 无弹窗检测逻辑 |
| 状态特征向量库（FAISS初始状态）| ❌ 未实现 | 无离线建图 |
| 失败路径惩罚 | ❌ 未实现 | end_task() 不更新已有边置信度 |
| 强化学习权重更新 | ❌ 未实现 | success_rate 未随任务结果更新 |

### 5.2 核心设计对比

| 维度 | 升级计划设计 | 当前实现 | 差距 |
|------|------------|---------|------|
| UI状态标识 | View Hierarchy Hash（稳定） | MD5(截图Base64)（不稳定） | **严重**：同界面不同截图永远不命中 |
| 状态语义描述 | VLM生成 semantic_layout | 仅使用APP名称 | **中等**：语义粒度过粗 |
| 导航触发条件 | confidence >= 0.8 | 任意匹配 | **中等**：低置信度也可能触发 |
| 图谱更新时机 | 任务结束时（RL反馈） | 仅记录新转换 | **轻微**：无置信度调整 |
| 弹窗处理 | [:SOLVES_POPUP] 边+检测 | 未实现 | **缺失**：复杂场景会卡住 |

---

## 六、第一性原则分析：当前系统的问题与改进方向

### 6.1 核心问题：MD5精确匹配失效

**问题**：当前使用 `MD5(截图Base64)` 作为 UI 状态标识。这在两个层面失败：

1. **会话内不稳定**：PNG/JPEG 编码器的每次输出可能略有差异（时间戳、元数据）
2. **会话间完全失效**：同一界面在不同设备、不同时间截图的 Base64 完全不同

**后果**：Layer 1（MD5精确匹配）在实际运行中几乎永远不会命中，导致：
- Navigate 模式几乎不可能触发
- `current_state_id` 永远是新的 state_id
- 在线图谱记录的是"瞬时状态"而非"逻辑状态"

**第一性分析**：我们需要的是**逻辑状态的等价类**，而非物理截图的哈希。

**改进方向**：
```
方案A: 移除 MD5 精确匹配，直接用 Layer 2（任务语义）
       优点：简单，Layer 2 实际更实用
       缺点：失去了"同界面精准导航"能力

方案B: 替换为 View Hierarchy Hash（升级计划原定方案）
       优点：稳定，可跨截图/跨设备匹配
       缺点：需要额外获取 UI 树数据（Android: uiautomator dump）

方案C: 替换为 VLM semantic layout 匹配
       优点：语义级匹配，更鲁棒
       缺点：每步需要额外 VLM 调用（延迟和成本增加）

推荐：方案B + 方案C 结合
  - 优先用 View Hierarchy Hash 做精确匹配
  - fallback 用 N-gram 语义匹配（当前 Layer 2）
  - 长期：引入 VLM semantic layout 作为第三层
```

### 6.2 上下文注入方式的低效

**问题**：当前通过文本拼接方式注入上下文：

```python
# 将上下文追加到用户消息文本
item["text"] = item["text"].rstrip() + f"\n\n[记忆上下文]\n{extra_context}"
```

这对于 **Vision Model** 有两个问题：
1. Vision Model 需要同时处理图像和长文本上下文，上下文过长会干扰视觉注意力
2. 文本格式的记忆上下文（如"⚡ 联系「张三」：推荐使用微信"）对模型来说不易理解

**第一性分析**：上下文注入应该区分**结构化指令**和**参考信息**：
- **结构化指令**（如"必须用微信联系张三"）→ 注入 system prompt 或单独字段
- **参考信息**（如历史轨迹）→ 作为简短注释，不干扰主推理

**改进方向**：
```python
# 更结构化的注入方式
{
    "role": "user",
    "content": [
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        {"type": "text", "text": f"{task}\n\nScreen: {current_app}"},
        # 分离的结构化上下文
        {"type": "text", "text": "[Memory: contact→app binding for 张三 is 微信 (freq=5)]"}
    ],
    "memory": {   # 扩展字段，供支持扩展字段的模型使用
        "contact_app": {"张三": "微信"},
        "similar_trajectory": {...}
    }
}
```

### 6.3 Navigate模式缺少置信度阈值

**问题**：当前只要 `next_actions` 非空就触发 Navigate 模式：

```python
if mode == "navigate" and context_data.get("next_actions"):
    # 任意匹配都执行快捷动作
    best_action = context_data["next_actions"][0]
```

**第一性分析**：Shortcut 的置信度来自历史频率，但频率高不代表**当前情境正确**。即使"在京东首页点击搜索"执行了100次，下次执行时可能用户已经不在京东。

**改进方向**：
```python
CONFIDENCE_THRESHOLD = 0.8  # 可配置

high_conf_actions = [
    a for a in context_data.get("next_actions", [])
    if a["confidence"] >= CONFIDENCE_THRESHOLD
]
if high_conf_actions:
    mode = "navigate"
    best_action = high_conf_actions[0]
```

### 6.4 缺少失败路径惩罚

**问题**：`end_task()` 只保存轨迹，不更新已有边的置信度：

```python
# 当前：只写入新关系，不更新已有关系
def add_state_transition(self, source, target, action_data, task_id):
    # ON CREATE SET confidence = 1.0
    # ON MATCH   SET frequency = frequency + 1
    # 但没有根据 success/failure 调整 confidence
```

**第一性分析**：图谱需要成为**可学习的系统**。每次任务结果都应该反馈到图中。

**改进方向**：
```python
def end_task(self, success: bool, result: str, end_state_id: str):
    if success:
        # 强化：提升轨迹中每条边的置信度
        for edge_id in trajectory_edges:
            cypher = """
            MATCH ()-[r]->() WHERE r.edge_id = $edge_id
            SET r.confidence = r.confidence * 1.1,
                r.success_rate = (r.success_rate * r.frequency + 1.0) / (r.frequency + 1)
            """
    else:
        # 惩罚：降低失败路径的置信度
        for edge_id in trajectory_edges:
            cypher = """
            MATCH ()-[r]->() WHERE r.edge_id = $edge_id
            SET r.confidence = r.confidence * 0.5
            """
```

### 6.5 缺少短期购物意图维持

**升级计划中提到**：`SHOPPING_CONTEXT` — 跨Prompt维持意图（如"正在看Nike的鞋"，后续"加入购物车"应关联）。

**当前实现**：无此机制。每次任务都是独立的，没有会话级短期记忆。

**改进方向**：
```python
class ShortTermMemory:
    """会话级短期记忆，维持跨步意图"""

    def __init__(self):
        self.current_intent: str = ""      # 当前购物意图
        self.viewed_items: list[dict] = []  # 浏览过的商品
        self.last_action: str = ""          # 上一步动作
        self.pending_cart: list[dict] = []   # 待确认加入购物车的商品

    def update(self, action: dict, screenshot_app: str, thinking: str):
        # 从 thinking 和 action 中提取短期信息
        if "加入购物车" in thinking or action.get("type") == "Tap":
            # 检查是否点击了商品
            item = extract_item_from_screenshot(...)
            if item:
                self.pending_cart.append(item)

        if "买了" in thinking or "下单" in thinking:
            # 意图完成
            self.current_intent = ""

    def get_context(self) -> str:
        if self.pending_cart:
            return f"[短期记忆] 当前购物车待确认: {self.pending_cart}"
        return ""
```

### 6.6 CONTACT_APP_BINDNG 拼写错误

```python
# 当前（memory_store.py:75）
CONTACT_APP_BINDNG = "contact_app_binding"  # "BIN" 少了 "D"

# 使用处（memory_manager.py:332）
if memory.memory_type == MemoryType.CONTACT_APP_BINDNG:  # 能匹配，因为枚举值正确
    ...
```

**问题**：枚举名 `CONTACT_APP_BINDNG` 拼写错误（缺少字母 D）。虽然值 `"contact_app_binding"` 是正确的，但枚举名本身有错。

### 6.7 Neo4j不可用时的静默降级

**当前行为**：
```python
# graph_store.py
if not self.driver:
    print(f"Warning: Could not connect to Neo4j...")  # 仅打印警告
    self.driver = None  # 静默设为 None

# 调用时
def add_state_transition(self, ...):
    if not self.driver:
        return  # 静默返回，不记录
```

**问题**：Neo4j 不可用时，整个 Spatial Memory 静默失效。系统在 Explore 模式下运行，用户无感知。

**改进方向**：
```python
# 分层降级策略
if not self.graph_store.driver:
    print("⚠️  Spatial Memory (Neo4j) 不可用，切换到 Semantic-only 模式")
    self._spatial_available = False
    # 但 Semantic Memory (FAISS) 仍然工作
    # Layer 1 和 Layer 2 降级到 Layer 3（FAISS fallback）
```

### 6.8 待审核轨迹的批量操作缺失

**当前**：每次只能提交一个轨迹（`commit_pending(index=0)`）

**改进方向**：支持批量审核和提交
```python
def commit_pending_batch(self, indices: list[int]) -> list[bool]
def review_pending(self, limit: int = 20) -> list[dict]  # 查看待审核列表
```

---

## 七、文件统计

| 文件 | 行数 | 职责 |
|------|------|------|
| `phone_agent/agent.py` | 776 | Agent主循环执行引擎 |
| `phone_agent/memory/memory_manager.py` | 1043 | 记忆系统调度枢纽 |
| `phone_agent/memory/memory_store.py` | 549 | FAISS向量存储 |
| `phone_agent/memory/graph_store.py` | 365 | Neo4j图存储 |
| `phone_agent/tracer.py` | 118 | 执行轨迹记录 |
| `phone_agent/model/adapters.py` | ~800 | 5个VLM模型适配器 |
| `phone_agent/actions/handler*.py` | ~1500 | 6个Action处理器 |
| **核心代码** | **~5151** | |

---

*本文档由 Claude Code 基于代码分析生成，对比基准：`CLAWGUI_UPGRADE_PLAN.md`*
