# 主动移动空间图谱（AMSG）：从页面图谱到自进化动作库

> **版本**: 2026-06-05（重写版）
> **定位**: 技术方法论文基础文档。从源码 `phone_agent/spatial/` 出发，分析现有页面图谱方法的问题，介绍 AMSG 的设计、与 Agent 执行的集成、以及图谱的构建与自进化机制。

---

## 1  页面图谱的想法是对的，但实现方式有根本缺陷

PG-Agent [Chen et al., ACM MM 2025] 提出了一个正确的直觉：移动应用的页面天然形成一个由操作连接的图结构，一次线性的任务轨迹本质上是这个图上的一条路径采样。将多条轨迹重建为页面图，比孤立地存储单条轨迹能提供更丰富的导航知识。

但 PG-Agent 的实现方式存在三个根本问题，这些问题在其他引入图谱的工作（KG-RAG、WebNavigator、MobiAgent/AgentRR）中同样存在。

### 1.1  静态图谱：建完就冻结

PG-Agent 从预收集的 episode 批量构建页面图，然后在线阶段仅读取图谱，不修改。KG-RAG 的 UTG 向量库、WebNavigator 的交互图同理——图谱是离线产物，构建后冻结。

这在两个场景下失效：

**应用更新**。移动应用平均每 2-4 周更新一次，每次更新可能改变页面布局、按钮位置、导航流程。PG-Agent 2 月构建的页面图，到 4 月时可能有 30% 的边已经失效（按钮移位、流程变更）。但系统不知道哪些边失效了，因为它不追踪边的实际执行结果。

**新页面发现**。Agent 在执行中可能遇到图谱未覆盖的新页面（如新增的促销活动页、改版后的结算流程）。PG-Agent 无法将这些新发现写回图谱——下次遇到同样的页面，还是要从零开始探索。

### 1.2  边的可靠性不区分

PG-Agent 的图谱中，所有边被同等对待——只记录"从页面 A 经过操作 X 可以到达页面 B"，不记录"这个转移有多可靠"。

但在真实环境中，同一个操作可能产生多种结果。点击"立即购买"按钮：
- 73% 的情况到达规格选择页（正常流程）
- 18% 到达登录页（未登录时被拦截）
- 9% 到达促销弹窗（命中 A/B 测试）

PG-Agent 会把这三种结果都当作有效转移存入图谱。下次规划时，规划器可能选择一条经过登录页的"路径"，而 Agent 并不知道这条路径只在未登录时才成立。

WebNavigator 用 DOM 哈希去重，但也不追踪单条边的成功/失败统计。MobiAgent 的 AgentRR 记录执行轨迹，但判断"是否可复用"的是一个二元的匹配模型，没有边级别的可靠性度量。

### 1.3  图谱和 VLM 的边界模糊

PG-Agent 将图谱用作 RAG 源：从图谱中检索与当前屏幕相关的导航指南（"从当前页面做某些操作可以完成某些任务"），注入 VLM 提示。VLM 仍然每步都推理，图谱不减少 VLM 调用——它只是提供了更好的提示内容。

WebNavigator 走向另一个极端：图谱直接控制导航（Retrieve-Reason-Teleport），VLM 只处理页面内操作。但 Teleport 假设图谱总是正确的——如果图谱边失效（app 更新后路径不通），没有回退机制。

两种方式都不区分一个关键事实：**有些转移图谱可以独立完成（搜索按钮在哪里是固定的），有些转移只有看了当前屏幕才能决定（选哪个商品取决于任务）**。把这两种转移混在一起处理，要么浪费 VLM 调用（始终推理），要么冒图谱过时的风险（始终信任图谱）。

---

## 2  AMSG：我们的图谱设计

AMSG 的设计直接针对上述三个问题：用**自动持久化**替代静态构建，用**成功率追踪**替代布尔存在性，用**锚定性分类**划定图谱和 VLM 的边界。

### 2.1  图谱存什么

Neo4j 中的基本模式：

```
(UIState) -[NEXT_ACTION]-> (Action) -[PRODUCES]-> (UIState)
```

**UIState（页面节点）**不存截图，存语义描述：

```
PageState = (app, page_type, landmarks, affordances, slots, risk_level)
```

为什么不存截图哈希？搜索"iPhone"和搜索"耳机"的截图完全不同，但页面结构相同（搜索栏 + 商品列表 + 筛选按钮）。存截图哈希意味着每次搜索不同关键词都产生新节点，图谱膨胀但不复用。存 `(app, page_type)` 级别的语义描述，使同一个 `search_result` 节点在所有购物任务中复用。

去重靠确定性语义签名 `app|domain|page_type|landmarks|affordances|slots`（`spatial/core.py:semantic_signature()`）。签名相同的观测合并到同一节点。

**Action（动作节点）**不存坐标，存语义意图：

```
Action = (type, intent, semantic_target, postcondition, lifecycle_stage)
```

为什么不存坐标？"点击搜索按钮"在不同设备上坐标不同，但语义相同。坐标作为 `target_locator` 附加在动作上，是锚定证据，不参与去重。去重键是 `source_page_type|intent|semantic_target|region|target_page_type`（`graph_store.py:_semantic_action_key()`），确保不同坐标但相同意图的动作合并。

**关系**上记录执行统计：`frequency`（成功次数）、`fail_count`（失败次数）、`confidence`（= frequency / total）。这是边生命周期系统的数据基础。

### 2.2  领域模式作为结构先验

图谱不是无约束生长的——`spatial/schemas/shopping.yaml` 定义了购物场景的页面类型、风险等级和典型转移：

```json
{
  "page_types": {
    "search_result": {"risk": "normal", "landmarks": ["search_bar", "product_cards"]},
    "checkout":      {"risk": "high",   "landmarks": ["address", "submit_order"]}
  },
  "transitions": [
    {"source": "search_result", "target": "product_detail", "intent": "open_product"},
    {"source": "cart", "target": "checkout", "intent": "checkout", "risk": "high"}
  ]
}
```

模式的作用是规范化（"detail" → "product_detail"）和风险标注（checkout 始终高风险），不是限制图谱的生长。图谱可以学习到模式中未预定义的转移——只要它通过了质量门。

### 2.3  锚定与非锚定：图谱和 VLM 各管什么

AMSG 在每条边上标注**锚定性**：图谱能独立执行（不需要看当前屏幕内容），还是需要 VLM 选择具体目标。

| 转移 | 锚定？ | 原因 |
|---|---|---|
| `home → search_input` | 是 | 搜索按钮位置固定 |
| `search_input → search_result` | 是 | 输入 + 提交，机械操作 |
| `search_result → product_detail` | **否** | 选哪个商品取决于当前任务 |
| `product_detail → spec_selection` | **否** | 选什么规格取决于用户需求 |

锚定边走快速路径（~0.5s，跳过 VLM），非锚定边走 VLM 路径（~5s，图谱提供方向提示，VLM 选择具体元素）。判断标准在 `ActionAdvisor.is_fast_executable()`：`grounded=True` AND `confidence >= 0.9` AND 有坐标或复合步骤。

与 PG-Agent 的区别：PG-Agent 的图谱对所有边提供 RAG 文本指南，VLM 始终推理。与 WebNavigator 的区别：WebNavigator 对所有边执行 Teleport，没有回退。AMSG 在**每条边**上独立决定控制强度。

---

## 3  赋予 Agent 空间感知能力

AMSG 不是一个独立系统——它通过 `GraphRuntimeController` 嵌入到 Agent 的每步执行循环中（参见 ARCHITECTURE.md 第 3.2 节 Phase 3）。以下是图谱如何在运行时赋予 Agent "空间感知"的具体机制。

### 3.1  集成入口：locate_and_get_context()

Agent 循环的 Phase 3 调用 `MemoryManager.locate_and_get_context()`，后者委托给 `GraphRuntimeController`。这个方法在一次调用中完成三件事，返回一个 `mode` 字段直接驱动 Phase 5 的调度决策：

```
locate_and_get_context(ui_hash, semantic_layout, task, screen_dict)
    │
    ├─ 如果 RuntimeDAG 存在且可用
    │   → 直接取 route[current_index]，返回 mode="navigate" (~10ms)
    │
    └─ 否则完整管线：
        ① 页面匹配：(app, page_type) 查询 Neo4j
        ② 验证上步 pending transition（success/failure）
        ③ 目标推断：TaskSlots → GoalSpec（目标 page_type + 槽位）
        ④ Dijkstra 路径规划：加权最短路径到目标
        ⑤ 缓存为 RuntimeDAG
        ⑥ 返回 mode + next_actions + semantic_context
```

返回的 `mode` 决定 Agent 后续行为：

| mode | 含义 | Agent 行为（Phase 5） |
|---|---|---|
| `navigate` | 图谱有路径，下一步可执行 | 锚定边 → 快速路径；非锚定 → VLM + 提示 |
| `explore` | 图谱无路径（冷启动或覆盖缺口） | 完整 VLM 推理，图谱记录新观测 |
| `verify_with_vlm` | 图谱有方向但需要 VLM 选择具体元素 | VLM 推理 + 图谱提供的语义目标提示 |
| `goal_reached` | 当前页面已是目标 page_type | 不需要导航动作 |

### 3.2  RuntimeDAG：路径缓存避免重复规划

Dijkstra 规划在微秒级完成，但完整管线（Neo4j 查询 + 规划 + 上下文组装）需要 50-200ms。RuntimeDAG 将规划结果缓存为内存中的边数组 + 游标：

```
步骤 1: 完整管线 → Dijkstra 找到 [edge_A, edge_B, edge_C, edge_D]
        缓存为 RuntimeDAG(route=[A,B,C,D], current_index=0)
步骤 2: DAG 可用 → route[1] → 跳过整个管线 (~10ms)
步骤 3: DAG 可用 → route[2] (~10ms)
步骤 4: route[3] 是非锚定边 → mode=verify_with_vlm → VLM 接管
```

DAG 失效条件：后条件不匹配、应用切换、连续失败超阈值、下一步高风险。失效后下次 Phase 3 自动重建。

副作用：DAG 可用时 `should_use_page_classifier()` 返回 `False`，Phase 1 跳过 PageClassifier 的 VLM 调用——又省一步。代价是弹窗出现时 Agent 带着错误 page_type 执行一步，但延迟后条件验证在下步捕获。

### 3.3  延迟后条件验证：图谱学习的数据来源

Agent 循环的 Phase 6 暂存 `(当前页面, 动作, 预期目标)` 为 pending transition。下一步的 Phase 3 开头对比实际 page_type 与预期：

- 匹配 → `record_observation(outcome="success")`
- 不匹配 → `record_observation(outcome="failure")` + 修复策略

这个延迟验证是整个生命周期系统正确工作的前提——没有它，成功率追踪的数据来源就是"Agent 认为会到达哪个页面"而非"Agent 实际到达了哪个页面"。PG-Agent、KG-RAG、WebNavigator 都没有后条件验证机制。

### 3.4  上下文注入：图谱知识如何进入 VLM 提示

Phase 4 组装 VLM 上下文时，图谱提供两种信息（参见 ARCHITECTURE.md 第 3.3 节的优先级表）：

**导航上下文**（优先级 3）：当 `mode=navigate` 时，注入"当前位置 → 目标的路径概要 + 下一步动作描述"。这告诉 VLM "图谱建议你点击搜索按钮进入搜索页"，VLM 可以采纳也可以忽略。

**动作提示**（优先级 7）：`ActionAdvisor.query()` 返回当前页面上所有 promoted 边作为可选动作列表。非锚定边只提供"存在这个转移"的信息，不提供坐标——VLM 必须自己看截图决定点哪里。

---

## 4  图谱的构建管线

图谱有三种构建途径，每种服务不同场景。它们共享同一套规范化和质量过滤逻辑。

### 4.1  途径 A：离线探索（冷启动）

在 Agent 首次使用一个应用之前，可以通过 `OfflineExplorer` 预采集页面和转移数据。`import_exploration_staging()` 处理这些原始数据：

1. 原始页面 JSON → 转换为 PageState（`build_page_state()`）
2. 按 `(app, page_type, risk)` 规范化
3. 过滤瞬态 `unknown` 页面和应用不匹配的转移
4. 搜索宏合成：从轨迹中恢复 Type + Submit 复合动作（`_synthesize_search_compound_steps()`）
5. 同页动作压缩和边质量过滤
6. 通过所有过滤后才由 `promote_staging_to_canonical()` 合并入 Neo4j

离线探索产生的边初始 `lifecycle_stage="hypothesis"`，需要在线验证后才能提升为 `promoted`。

### 4.2  途径 B：领域模式（结构骨架）

`spatial/schemas/shopping.yaml`（继承 `common_mobile.yaml`）预定义了购物场景的 8 种核心页面类型和 10 条典型转移。这不是图谱本身，而是图谱生长的骨架——`SchemaRegistry` 提供页面类型规范化、风险标注和转移距离启发式。

模式 + 离线探索 = 冷启动时的初始图谱。但真正有价值的边来自途径 C——Agent 实际执行时的在线学习。

### 4.3  途径 C：Agent 运行中的在线学习

这是图谱最主要的数据来源。每次 Agent 成功完成任务，都会向图谱贡献新的观测（详见下节）。与 PG-Agent 的离线批量构建不同，AMSG 的在线学习是持续的——每次新任务都可能发现新的转移或验证已有转移的可靠性。

---

## 5  Agent 运行结束的自动持久化

当 `PhoneAgent.run()` 的执行循环结束时，`MemoryManager.end_task()` 触发图谱持久化（参见 ARCHITECTURE.md 第 3.4 节）。整个管线遵循一个核心规则：**没有原始动作直接写入 Neo4j**。

### 5.1  在线暂存：任务执行期间

每步的 Phase 6 调用 `update_state_and_transition()` 后，`record_observation()` 将转移暂存到内存中的 `_local_states` 和 `_local_edges`。同时，`EdgeLifecycleManager.record_outcome()` 更新该边的成功/失败计数和优势度。

这些操作都在内存中完成，不碰 Neo4j。20 步的任务产生约 20 个暂存观测。

### 5.2  质量门 1：任务成功才写入

`end_task(success=True)` 调用 `flush_staged_graph()`。`end_task(success=False)` **不调用**——失败任务的观测可能整条都是错误导航（走错路、陷入循环、被弹窗干扰），写入图谱会污染后续任务的规划。

这是最简单也最有效的质量门：一个 20 步的失败任务可能有 15 步是错误操作，直接丢弃比逐步审核更高效。

### 5.3  规范化与过滤：flush_staged_graph()

`flush_staged_graph()` 对暂存的 20 条观测执行三步处理：

**① canonicalize_state_graph()**：按语义签名去重页面状态（搜索"iPhone"的 search_result 和搜索"耳机"的 search_result 合并为一个节点）。过滤 `unknown` 瞬态页面。检查应用一致性（不同应用的页面不应有直接转移边）。

**② 生命周期提升检查**：对每条边检查是否满足提升条件（验证次数 >= N AND 成功率 >= θ AND 非高风险）。满足则从 `hypothesis` 提升为 `promoted`。

**③ Neo4j 写入**：对每个 PageState 执行 `MERGE UIState`，对每个转移执行 `MERGE Action` + `MERGE NEXT_ACTION/PRODUCES` 关系。`MERGE` 语义确保幂等——重复写入不会产生重复节点。生命周期元数据（`lifecycle_stage`、`verification_count`、`dominance_ratio`）同步写入 Action 节点属性。

### 5.4  质量门 2：VLM 轨迹审核

`flush_staged_graph()` 完成后，`TrajectoryReviewer.review_and_import()` 从保存的轨迹 JSON 中提取额外的转移候选：

1. 提取步骤间的页面转移链
2. 过滤同页/unknown/finish/wait 伪转移
3. 对比 Neo4j 去重（已有的转移不重复处理）
4. 对新候选调用强 VLM（`AMSG_STRONG_VLM_MODEL`）验证：

```
你是一个手机 App 页面导航图谱的质量审核员。
以下是从执行轨迹中提取的新页面转换。请判断每条转换是否是真实有效的页面导航。

有效：从一个明确的页面类型跳转到另一个明确的页面类型。
无效：dialog → home（关闭广告弹窗），分类器错误导致的虚假转换。
```

5. 仅 VLM 批准的转移以 `lifecycle_stage="hypothesis"` 导入

这道门的价值是过滤 `flush_staged_graph()` 可能遗漏的噪声——特别是分类器误判产生的虚假转移。

### 5.5  边生命周期：成功率驱动的提升与降级

每条边追踪一个四阶段生命周期，由执行结果统计驱动：

```
hypothesis → candidate → promoted → demoted
```

| 阶段 | 对 Agent 的影响 |
|---|---|
| `hypothesis` | 不可见。Agent 无法使用这条边走快速路径 |
| `candidate` | 不可见。验证次数达标但成功率不够 |
| `promoted` | **可见**。锚定边 → 快速路径可用；非锚定边 → VLM 提示 |
| `demoted` | 不可见。VLM 重新探索该转移 |

提升条件：`verification_count >= N` AND `dominance_ratio >= θ` AND `risk != high`。降级条件：promoted 边的成功率跌破 40%。

降级是自动的自修复机制：应用更新后，某个按钮的功能改变 → 该边的成功率开始下降 → 优势度跌破阈值 → 边被降级 → Agent 不再走快速路径而是用 VLM 重新探索 → 新的观测建立新的 hypothesis → 验证后重新提升。整个过程不需要人工干预。

---

## 6  图谱自进化：跨任务的累积学习

以上所有机制组合在一起，形成一个跨任务的正反馈循环：

```
任务 N 执行
  ↓
成功 → flush_staged_graph() + VLM 轨迹审核
  ↓
Neo4j 新增/强化若干 UIState-Action-UIState 转移
  ↓
任务 N+1 执行
  ↓
locate_and_get_context() 查询 Neo4j
  ↓
更多 promoted 边 → 更多快速路径命中 → 更少 VLM 调用
  ↓
任务完成更快 → 成功 → 进一步强化图谱
```

### 6.1  收敛性

这个循环不会无限膨胀。移动应用的页面类型是有限的——一个典型购物 app 有 8-12 种核心页面和 15-25 种常用转移。在 10-20 次成功任务后，核心购物流程（home → search → results → detail → spec → cart）的转移基本被覆盖。此后，新任务主要是**强化**已有边的成功率，偶尔发现边缘场景的新转移。

### 6.2  冷启动 → 稳定期的延迟变化

| 阶段 | 图谱状态 | 典型 20 步任务中快速路径步数 | 总延迟（估算） |
|---|---|---|---|
| 冷启动（0 次成功任务） | 无边或仅离线探索 | 0 步（全 VLM） | ~100s (20×5s) |
| 早期（3-5 次成功任务） | 部分核心转移已 promoted | 5-8 步快速 + 12-15 步 VLM | ~65-80s |
| 稳定期（10+ 次成功任务） | 核心流程全覆盖 | 12-15 步快速 + 5-8 步 VLM | ~30-45s |

快速路径步骤越多，总延迟越低。稳定期的延迟主要由非锚定步骤（选商品、选规格等必须 VLM 推理的步骤）决定——这些步骤无论图谱多完善都需要 VLM，因为它们依赖当前屏幕内容。

### 6.3  自修复

当应用 UI 更新导致某条 promoted 边失效时：

```
步骤 t: Agent 走快速路径，执行 edge_X
步骤 t+1: 后条件验证 → 实际页面 ≠ 预期 → record(failure)
  → edge_X 的 dominance_ratio 开始下降
  → Agent 回退到 VLM 路径完成本步
  → 后续步骤中 edge_X 被降级 → Agent 不再使用
  → VLM 重新探索该转移 → 新观测产生新 hypothesis
  → 新边经过验证后提升为 promoted
```

这个过程通常在 3-5 次任务内完成。不需要人工检测 UI 变更——成功率的统计偏移就是信号。

---

## 7  与现有工作的对比

| 维度 | PG-Agent | KG-RAG | WebNavigator | MobiAgent | **AMSG** |
|---|---|---|---|---|---|
| 图谱来源 | episode 批量构建 | xTester 爬取 UTG | 自适应 BFS | 任务执行记录 | 离线探索 + 在线学习 + VLM 审核 |
| 图谱更新 | 不更新 | 不更新 | 不更新 | 手动纠正轨迹 | 每次成功任务自动更新 |
| 边可靠性 | 不追踪 | 不追踪 | 哈希去重 | 不追踪 | 成功率追踪 + 四阶段生命周期 |
| VLM/图谱边界 | RAG 文本→VLM 始终推理 | 同左 | Teleport 无回退 | 二元重放 | 每条边独立判断（锚定→图谱 / 非锚定→VLM） |
| 噪声控制 | 双层相似度 | BFS 评分 | DOM 差分 | 无 | 三道门（任务成功 + VLM 审核 + 规范化） |
| 后条件验证 | 无 | 无 | 无 | 无 | t+1 步延迟验证，成功率基于真实观测 |
| 自修复 | 无 | 无 | 无 | 无 | 成功率下降 → 自动降级 → VLM 重新探索 |

### 7.1  AMSG 的核心差异

1. **活的图谱 vs 冻结的图谱**。PG-Agent/KG-RAG/WebNavigator 的图谱建完即冻。AMSG 在每次成功任务后自动增长，在边可靠性下降时自动收缩。"Active" 是名字中最关键的词。

2. **边的成功率 vs 边的存在性**。所有现有方法把边当布尔值。AMSG 追踪每条边的成功率，只有统计上可靠的边才进入 Agent 的可执行动作库。

3. **每条边独立判断控制强度 vs 一刀切**。PG-Agent 对所有边都提供 RAG（图谱不减少 VLM 调用）。WebNavigator 对所有边都 Teleport（图谱错误无回退）。AMSG 在每条边上独立判断——锚定且可靠的走快速路径，非锚定的让 VLM 决定。

4. **真实后条件验证 vs 无验证**。没有后条件验证，成功率追踪的数据来源就是假设而非事实。这是 AMSG 生命周期系统正确工作的前提。

---

## 8  实验性扩展（未默认启用）

以下机制已实现但默认关闭，需要消融实验验证后才能作为论文贡献：

- **多通道信念定位**（`use_multi_signal_belief=False`）：在 `(app, page_type)` 匹配上增加视觉/语义/时序通道的贝叶斯更新。当前 `(app, page_type)` 匹配已覆盖绝大多数场景。
- **Belief-A* 规划**（`planner_backend="dijkstra"`）：在 Dijkstra 上叠加过期衰减、探索奖励等项。当前图谱规模（< 50 节点）下增强项贡献 < 0.1。
- **熵驱动 VLM 验证边界**：基于结果分布的 Shannon 熵决定是否需要 VLM。当前硬编码转移集合是主要机制。
- **消融预设矩阵**：通过 `AMSG_CONFIG` 切换 6 种配置（legacy/edge_only/belief_only/planner_only/full/sava），用于未来实验。
