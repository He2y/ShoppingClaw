# 主动移动空间图谱：设计理念与核心机制

> **版本**: 2026-06-05（修订版）
> **定位**: 技术方法论文的基础文档。只包含已实现、可复现、默认启用的机制。实验性扩展在末尾单独标注。

---

## 1  问题与动机

### 1.1  移动 GUI 自动化的核心困难

移动 GUI 智能体的困难不在单步执行，而在跨步骤的知识累积。一个购物任务 20-50 步，跨越 8-12 种页面类型。两个问题反复出现：

**问题 A：重复探索**。每次新任务都从零开始导航。"淘宝首页 → 搜索框 → 输入 → 搜索结果"这条路径，第一次需要 VLM 推理每步该做什么（~5s/步），但这条路径在所有购物任务中完全相同。纯 VLM 方法每次都付出相同的推理成本。

**问题 B：导航噪声**。应用中充斥弹窗、广告、登录页、促销中间页。如果将每次观测到的转移都当作可靠知识存储，图谱会迅速被噪声污染。下次规划时，规划器可能走一条经过广告弹窗的"捷径"，导致任务失败。

### 1.2  现有方法为什么不够

**纯 VLM（如 ColorAgent）**：每次重新发现导航路径。强在语义理解，弱在重复劳动。

**静态图谱 + RAG（如 PG-Agent、KG-RAG、WebNavigator）**：离线建图，在线检索。解决了重复探索，但图谱冻结后无法适应 UI 更新。更关键的是，它们不区分"这个按钮总是到搜索页"和"这个按钮可能到登录页也可能到 SKU 选择页"——所有边都被同等对待。

**经验重放（如 MobiAgent/AgentRR）**：记录成功轨迹，相似任务重放。本质是"背诵"而非"理解"——轨迹偏离就失效。

### 1.3  AMSG 解决什么

AMSG 做三件事，每件针对一个真实问题：

1. **把已验证的导航路径存下来**（解决重复探索）。home → search_input 这条路径验证过 3 次都成功，就直接走，不再问 VLM。
2. **过滤噪声，只存可靠的转移**（解决导航噪声）。失败任务不写图谱；成功任务也要经过规范化和去重；新发现的转移要 VLM 审核后才能入图。
3. **区分"图谱能决定"和"必须 VLM 决定"的边界**（保持语义正确性）。点击搜索按钮，图谱能决定。点击哪个商品，只有 VLM 看当前屏幕才能决定。

---

## 2  图谱存什么

### 2.1  页面状态节点（UIState）

一个页面状态不是一张截图，是一个语义描述：

```
PageState = (app, page_type, landmarks, affordances, slots, risk_level)
```

| 字段 | 例子 | 作用 |
|---|---|---|
| `app` | "taobao" | 同一应用内的转移才有意义 |
| `page_type` | "search_result" | 页面的功能类别，不是外观 |
| `landmarks` | ("search_bar", "product_cards", "filter_buttons") | 稳定的视觉锚点 |
| `affordances` | ("open_product", "open_filter", "sort", "scroll") | 页面上可执行的操作 |
| `slots` | {query: "iPhone 16"} | 与当前任务相关的运行时变量 |
| `risk_level` | "normal" / "medium" / "high" | payment 和 login 始终为 high |

两个外观不同但结构相同的截图（比如不同商品的搜索结果页）映射到同一个 PageState。这就是跨任务复用的基础。

**去重机制**：语义签名 `app|domain|page_type|landmarks|affordances|slots` 用于确定性去重。签名相同的观测合并到同一节点。

**实现**：`spatial/core.py` 的 `PageNode` + `semantic_signature()`，`spatial_graph_memory.py` 的 `build_page_state()`。

### 2.2  动作节点（Action）

动作的身份不包含坐标。"点击搜索按钮"在不同设备上坐标不同，但语义相同。

```
Action = (type, intent, semantic_target, expected_postcondition, risk_level)
```

坐标存在 `target_locator` 字段中，作为锚定证据，不参与去重。去重键为 `source_page_type|intent|semantic_target|region|target_page_type`。

**实现**：`graph_store.py` 的 `_semantic_action_key()`。

### 2.3  持久化模式

图谱存储在 Neo4j 中，基本模式是三节点两关系：

```
(UIState) -[NEXT_ACTION]-> (Action) -[PRODUCES]-> (UIState)
```

关系上记录 `frequency`（成功次数）、`fail_count`（失败次数）和 `confidence`（= frequency / total）。这些计数是生命周期系统的数据基础。

---

## 3  边的生命周期：成功率追踪与提升/降级

图谱的核心质量机制不是复杂的概率模型，而是一个直白的成功率追踪器。

### 3.1  四阶段状态机

每条转移边经历四个阶段：

```
hypothesis  →  candidate  →  promoted  →  demoted
  (新观测)      (验证过)      (可信赖)     (失效)
```

| 阶段 | 进入条件 | 对 ActionAdvisor 的可见性 |
|---|---|---|
| `hypothesis` | 首次观测到该转移 | 不可见（图谱不会推荐未验证的动作） |
| `candidate` | 后条件验证次数 >= N | 不可见 |
| `promoted` | 成功率 >= θ 且不是高风险 | **可见**（可进入快速路径或提供 VLM 提示） |
| `demoted` | 成功率跌破阈值 | 不可见（VLM 重新探索） |

**默认阈值**：N=1, θ=0.6。严格模式（`sava` 配置）：N=3, θ=0.8。

**实现**：`spatial/edge_lifecycle.py` 的 `EdgeLifecycleRecord.is_promotable()`。

### 3.2  结果追踪

对于每个 `(source_page_type, action_key)` 对，系统记录该动作实际到达了哪些页面及各自次数。

```
例：在 product_detail 页面点击"立即购买"
  → spec_selection: 8 次 (73%)
  → login:          2 次 (18%)
  → promotion_popup: 1 次 (9%)
```

成功率 = 8/11 = 73%。如果阈值是 60%，这条边被提升。如果阈值是 80%（严格模式），这条边停留在 candidate 阶段。

**自动降级**：如果一条 promoted 边的成功率跌破 40%（比如 app 更新后按钮功能变了），边被降级。VLM 重新探索该转移，新的观测进入生命周期系统。这是一个被动的自修复机制——不需要主动检测 UI 变更，只需观测结果的统计偏移。

**实现**：`spatial/edge_lifecycle.py` 的 `OutcomeDistribution` 和 `record_outcome()`。

### 3.3  这个机制的真实价值

边生命周期解决的是一个真实问题：在线观测不可避免地包含噪声（弹窗、登录拦截、网络错误）。如果把所有观测等权对待，图谱会包含大量"点击购买 → 登录页"这样的噪声边，规划器会被误导。

成功率追踪 + 提升阈值的组合确保：只有**统计上可靠**的转移才进入可执行动作库。这不是一个复杂算法，但它解决了一个没有它就无法解决的问题。

---

## 4  锚定与非锚定：图谱和 VLM 的控制边界

这是 AMSG 最重要的设计决策，也是与所有现有图谱方法的根本区别。

### 4.1  区分标准

一条转移是**锚定的**（grounded），如果图谱可以独立执行它——不需要看当前屏幕内容就知道点哪里。

| 转移 | 锚定？ | 原因 |
|---|---|---|
| `home → search_input` | 是 | 搜索按钮位置固定，点击后总是打开搜索 |
| `search_input → search_result` | 是 | 输入查询 + 点提交，结果确定 |
| `search_result → product_detail` | **否** | 必须由 VLM 选择哪个商品匹配当前任务 |
| `product_detail → spec_selection` | **否** | 必须由 VLM 判断当前页面和目标动作 |
| `search_result → filter_panel` | 是 | 筛选按钮位置固定 |

### 4.2  双速调度

```
锚定 + promoted + confidence >= 0.9 → 快速路径 (~0.5s，不调用 VLM)
promoted 但非锚定              → 图谱提示 + VLM 验证 (~3s)
其他                           → 完整 VLM 推理 (~5s)
```

快速路径执行后有后条件保护：截取新截图，检查 page_type 是否匹配预期。不匹配则自动回退到 VLM 路径。最坏情况是多花一次 VLM 调用，不会执行错误动作。

### 4.3  为什么现有方法没有这个区分

- **PG-Agent / KG-RAG**：图谱提供 RAG 上下文，VLM 始终做决策。图谱不减少 VLM 调用。
- **WebNavigator**：图谱直接控制导航（Teleport），VLM 仅处理页面内操作。图谱对所有转移一视同仁，不区分锚定性。当图谱错误时没有回退机制。
- **MobiAgent/AgentRR**：二元决策——重放或不重放。没有"部分信任图谱"的中间状态。

AMSG 的锚定/非锚定分类在**每条边**上独立判断，提供了三档控制强度。这不是一个算法创新，而是一个架构决策——但它决定了系统在实际部署中的可靠性。

**实现**：`spatial/action_advisor.py` 的 `ActionHint.is_fast_executable()` 和 `query()`，`agent.py` 的 `_try_graph_shortcut()`。

---

## 5  延迟后条件验证

### 5.1  为什么不能立即记录

传统做法是执行动作后立即记录"我做了 X"。但此时还不知道 X 的结果是什么——下一张截图还没有被捕获。如果立即记录 `(product_detail, tap_buy, spec_selection)`，而实际到达的是 login 页，图谱就记录了一条错误的成功转移。

### 5.2  延迟验证机制

AMSG 将转移记录延迟到下一步的观测阶段：

**步骤 t**：执行动作后，将 `(当前页面, 执行的动作, 预期目标页面)` 暂存为 pending transition。不写入图谱。

**步骤 t+1**：截取新截图，分类页面。将观测到的 page_type 与 t 步暂存的预期目标对比：
- 匹配 → `record_observation(outcome="success")`
- 不匹配 → `record_observation(outcome="failure")`，触发修复（重试或重规划）

这确保边生命周期系统收到的是**真实的后条件观测**，而非一厢情愿的预期。没有这个机制，成功率追踪的数据基础就不可靠。

**实现**：`memory_manager.py` 的 `update_state_and_transition()` 暂存 pending；`runtime_controller.py` 的 `_verify_pending_transition()` 在下一步验证。

---

## 6  暂存优先持久化

### 6.1  核心规则

**没有原始动作直接写入 Neo4j。** 每条路径都经过至少一道质量门。

### 6.2  三条持久化路径

**路径 1：在线运行时**
- 任务执行中，每步的观测暂存在内存（`_local_states`, `_local_edges`）
- 任务成功 → `flush_staged_graph()` 执行规范化（语义签名去重、瞬态页面过滤）后写入 Neo4j
- 任务失败 → 仅保存轨迹 JSON，不写图谱

**路径 2：轨迹审核**
- 成功任务的轨迹文件由 `TrajectoryReviewer` 处理
- 提取页面转移链，过滤同页/未知/等待伪转移
- 对比 Neo4j 去重
- 新候选提交给强 VLM 验证："这是真实的页面导航还是弹窗/分类器错误？"
- 仅 VLM 批准的转移以 hypothesis 身份导入

**路径 3：离线探索**
- 预采集的探索数据经过规范化、过滤、搜索宏合成后才能进入图谱

### 6.3  为什么需要这么多门

因为在线观测**必然包含噪声**。在真机测试中，以下情况频繁出现：
- 广告弹窗触发了一次 `product_detail → dialog` 的观测
- 页面分类器把促销横幅误判为新页面类型
- 网络延迟导致页面加载不完整，分类器返回 `unknown`
- 用户在 take_over 时做了非任务操作

如果这些观测直接进入图谱，规划器下次可能走一条"经过广告弹窗"的路径。三道质量门确保只有经过验证的、稳定的转移才成为图谱的一部分。

**实现**：`spatial_graph_memory.py` 的 `flush_staged_graph()` 和 `canonicalize_state_graph()`；`spatial/trajectory_reviewer.py` 的 `review_and_import()`。

---

## 7  运行时管线

### 7.1  单步流程

每步的图谱交互由 `GraphRuntimeController.locate_and_get_context()` 编排：

1. **RuntimeDAG 快速路径**：如果有缓存的路径计划且上步后条件通过，直接取下一步动作。~10ms。
2. **页面匹配**：用 `(app, page_type)` 在 Neo4j 中查找匹配的 UIState 节点。
3. **验证上步 pending transition**：对比观测 page_type 与预期，记录 success/failure。
4. **路径规划**：Dijkstra 在加权图上找从当前节点到目标页面类型的最短路径。
5. **缓存 RuntimeDAG**：将规划结果缓存，后续步骤复用。
6. **判断是否需要 VLM**：锚定转移直接执行，非锚定转移由 VLM 决策。

返回 `mode` 字段告诉 Agent 走哪条路径：
- `navigate`：图谱有路径，可尝试快速路径
- `explore`：图谱无路径，VLM 自主探索
- `goal_reached`：已到达目标页面
- `verify_with_vlm`：图谱有方向但需要 VLM 验证具体元素

### 7.2  RuntimeDAG 缓存

当规划器找到路径时，路径被物化为 RuntimeDAG 缓存在内存中。连续步骤中，只要后条件持续匹配，直接从 DAG 取下一步——跳过 Neo4j 查询和 Dijkstra 规划。

DAG 失效条件：后条件不匹配、应用切换、连续失败超阈值、下一步是高风险转移。

在一个 5 步连续导航（home → search_input → search_result → product_detail → spec_selection）中，仅第一步需要完整规划，后续 4 步从 DAG 读取。

### 7.3  路径规划

实际使用的规划器是 **Dijkstra**，边权为：

$$\text{cost}(e) = 1.0 + \frac{\text{fail\_count}}{\text{total}} \times 3.0 + R(\text{risk}) - \text{confidence} \times 0.3$$

风险惩罚：normal=0, medium=0.8, high=2.0。

这个公式的设计意图：高失败率的边代价高（避开不可靠路径），高风险页面代价高（尽量不经过支付/登录页），高置信度的边代价低（优先走验证过的路径）。

**实现**：`spatial/core.py:147-150` 的 `AffordanceEdge.weighted_cost`，`spatial_graph_memory.py` 的 `_shortest_path()`。

### 7.4  任务结束：持久化触发

```
PhoneAgent.run() 循环结束
    ↓
MemoryManager.end_task(success, result)
    ↓
    ├── 保存轨迹 JSON（无论成功与否）
    ├── [仅成功] flush_staged_graph() → Neo4j
    └── [仅成功] TrajectoryReviewer.review_and_import() → VLM 审核新转移
```

### 7.5  跨任务演进

任务 N 成功后，图谱中新增了若干经过验证的转移。任务 N+1 的 `locate_and_get_context()` 查询 Neo4j 时，能找到更多已验证边 → 更多快速路径命中 → 更少 VLM 调用 → 更快完成。

收敛性来自应用页面的有限性：一个典型购物应用有 8-12 种核心页面类型和 15-25 种常用转移。在 10-20 次成功任务后，核心购物流程基本被覆盖。

---

## 8  领域模式（Domain Schema）

图谱的结构不是无约束的——它受领域模式指导。模式定义了合法的页面类型、风险等级和典型转移：

```json
// shopping.yaml（继承自 common_mobile.yaml）
{
  "page_types": {
    "search_result": {"risk": "normal", "landmarks": ["search_bar", "product_cards"]},
    "checkout":      {"risk": "high",   "landmarks": ["address", "submit_order"]}
  },
  "transitions": [
    {"source": "search_result", "target": "product_detail", "intent": "open_product"},
    {"source": "cart",          "target": "checkout",        "intent": "checkout", "risk": "high"}
  ]
}
```

模式的作用：
- 规范化 page_type（把 "detail" 映射到 "product_detail"）
- 指导风险评估（checkout 始终高风险）
- 为规划器提供启发式（模式中的转移距离）

模式不是学习到的——它是预定义的领域知识。但图谱的边是在线学习的，模式只是提供了一个骨架。

**实现**：`spatial/schema_registry.py`，`spatial/schemas/shopping.yaml`。

---

## 9  与现有工作的对比

以下对比只包含可验证的差异，不做主观优劣评价。

| 维度 | PG-Agent | KG-RAG | WebNavigator | MobiAgent | **AMSG** |
|---|---|---|---|---|---|
| 图谱来源 | 从 episode 批量构建 | xTester 爬取 UTG | 自适应 BFS 爬取 | 任务执行中记录 | 在线执行 + 离线探索 + VLM 审核 |
| 图谱更新 | 不更新 | 不更新 | 不更新 | 手动纠正轨迹 | 自动：成功任务触发提升，失败率下降触发降级 |
| 边的可靠性 | 不追踪 | 不追踪 | 哈希去重 | 不追踪 | 成功率 + 提升阈值 |
| VLM/图谱边界 | 图谱提供 RAG，VLM 始终决策 | 同左 | 图谱直接传送，无回退 | 二元：重放/不重放 | 三档：快速/提示+验证/完整 VLM |
| 图谱噪声控制 | 双层相似度检查 | BFS 路径评分 | DOM 差分过滤 | 无 | 三道质量门（任务成功/VLM 审核/规范化） |
| 后条件验证 | 无 | 无 | 无 | 无 | 延迟验证：t+1 步对比预期与实际 |

### 9.1  AMSG 的真实差异点

1. **图谱自更新**。PG-Agent/KG-RAG/WebNavigator 的图谱是静态产物。AMSG 的图谱在每次成功任务后自动增长，在边可靠性下降时自动收缩。这是"active"一词的含义。

2. **边的可靠性建模**。所有现有方法把边当作布尔值（存在/不存在）。AMSG 追踪每条边的成功率，只有统计上可靠的边才进入可执行动作库。

3. **三档控制强度**。现有方法要么完全信任图谱（WebNavigator 的 Teleport），要么完全依赖 VLM（PG-Agent 的 RAG）。AMSG 在每条边上独立判断控制强度——锚定且可靠的边直接走，非锚定的边让 VLM 决定。

4. **延迟后条件验证**。现有方法不验证动作结果。AMSG 在 t+1 步验证 t 步的动作是否到达了预期页面，使成功率追踪基于真实观测而非假设。

5. **噪声过滤**。现有方法的质量控制在图谱构建阶段（离线），AMSG 的质量控制贯穿运行时（暂存→成功门→VLM 审核→规范化）。

---

## 10  实验性扩展（未默认启用）

以下机制已实现但不在默认配置中启用，它们的贡献需要消融实验验证后才能作为论文贡献：

### 10.1  多通道信念定位

`AMSGOptimConfig.use_multi_signal_belief = False`（默认关闭）

四通道（视觉嵌入、语义嵌入、结构匹配、时序频率）的贝叶斯信念更新。理论上比单通道 `(app, page_type)` 匹配更鲁棒，但实际上 `(app, page_type)` 匹配已经解决了绝大多数定位场景。视觉和语义通道需要额外的嵌入模型，增加延迟和成本。

**潜在价值场景**：页面分类器不可用、多个相似页面类型需要消岐、跨设备 UI 差异显著。这些场景需要专门构造的实验来验证。

### 10.2  增强路径规划（Belief-A*）

`AMSGOptimConfig.planner_backend = "dijkstra"`（默认 Dijkstra）

在 Dijkstra 基础上叠加过期衰减、探索奖励、信息增益和结果熵惩罚。理论上使规划器感知定位不确定性和边的时效性，但在当前图谱规模（通常 < 50 节点）下，Dijkstra 已经能找到最优路径，增强项的成本调整量级 < 0.1，不影响路径选择。

**潜在价值场景**：图谱规模显著增大（> 200 节点）、多应用交叉导航、需要平衡探索和利用。

### 10.3  熵驱动的 VLM 验证边界

当前实现中，需要 VLM 验证的转移主要由硬编码集合决定（`search_result → product_detail` 等）。`EdgeLifecycleManager.requires_vlm_verification()` 基于结果分布的 Shannon 熵提供了数据驱动的替代方案，但硬编码集合仍是主要机制。

**潜在价值**：当硬编码集合无法覆盖新应用或新转移模式时，熵边界可以自适应地发现不可靠转移。

### 10.4  消融配置矩阵

通过 `AMSG_CONFIG` 环境变量可切换六种预设（legacy, edge_only, belief_only, planner_only, full, sava），用于分离各模块的独立贡献。这是实验基础设施，不是研究结果——需要实际运行消融实验并报告数据。

---

## 11  核心贡献总结

只列可复现、默认启用、经得起推敲的贡献：

1. **自更新的页面状态图谱**。移动 GUI 智能体领域首个跨会话持久化的导航知识图谱，图谱随成功任务自动增长，随边可靠性下降自动收缩。

2. **基于成功率的边生命周期**。转移边经历 hypothesis → candidate → promoted → demoted 四阶段状态机，由后条件验证通过率和提升阈值驱动。解决了在线观测噪声污染图谱的问题。

3. **锚定性分类与三档调度**。在每条边上独立判断图谱/VLM 的控制强度。锚定转移直接执行（~0.5s），非锚定转移由 VLM 决策（~5s），带后条件保护。唯一已知的在边级别做控制切换的方案。

4. **延迟后条件验证**。在 t+1 步验证 t 步的动作结果，使成功率追踪基于真实观测。这是边生命周期正确工作的前提。

5. **暂存优先持久化与三道质量门**。失败任务不写图谱、新转移需 VLM 审核、所有路径经过规范化。确保图谱只包含经过验证的导航知识。

这五个贡献形成一个闭环：延迟验证提供可靠数据 → 成功率追踪决定边的可信度 → 三档调度根据可信度选择执行路径 → 执行结果反馈到成功率追踪 → 成功任务触发持久化 → 持久化的图谱加速下一次任务。
