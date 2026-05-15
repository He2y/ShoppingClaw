# 购物 Agent 图谱记忆优化方案

## 1. 目标

本方案把购物 Agent 的图谱记忆从“相似任务轨迹召回”升级为“训练无关的 App 空间认知系统”。核心约束是不引入额外模型训练成本，不依赖人工标注轨迹，而是复用现有 GUI/VLM 模型完成自探索、页面抽象、路径规划和运行时纠错。

当前系统已经具备四个基础：

- `OfflineExplorer` 可以探索购物 App 并产出页面与转移 JSON。
- `GraphStore` 可以用 Neo4j 存储 UI 状态和动作转移。
- `MemoryManager.locate_and_get_context()` 已接入 Agent 执行循环。
- `SpecGuard` 已能在购物规格/确认场景中阻止高风险误触。

主要问题是图谱尚未成为 Agent 的空间导航核心。它现在更多是相似任务召回和首步复用，缺少页面定位、目标状态推断、路径规划、失败惩罚和回退重规划。

## 2. 核心概念

新增深模块 `SpatialGraphMemory`，位于 `MemoryManager` 和 `GraphStore` 之间。调用方只关心四个行为：

- `locate(screen, task, previous_action) -> PageBelief`
- `plan(belief, goal_spec) -> RoutePlan`
- `record_observation(before, action, after, outcome) -> None`
- `repair(observed, expected, route_plan) -> RepairDecision`

### PageState

`PageState` 是页面节点抽象，不再等同于截图 hash。

字段包括：

- `app`：当前 App。
- `page_type`：`home`、`search_input`、`search_result`、`product_detail`、`spec_selection`、`cart`、`checkout`、`login` 等。
- `landmarks`：稳定地标，如搜索框、商品卡片、购物车按钮、规格选项。
- `affordances`：可执行动作，如搜索、打开商品、选择规格、返回。
- `slots`：动态槽位，如商品名、价格、规格、地址。
- `risk_level`：`normal`、`medium`、`high`。
- `semantic_signature`：`app + page_type + landmarks + affordances` 形成的页面级语义签名。

### TransitionEdge

`TransitionEdge` 是带条件的页面转移边。

字段包括：

- `source_id`、`target_id`
- `action_type`、`action_target`、`action_params`
- `precondition`、`postcondition`
- `success_count`、`fail_count`
- `rollback_action`
- `cost`、`risk`、`confidence`

边成本由动作开销、失败率、风险等级和置信度共同决定。购物确认页、支付页、地址页、登录页等高风险页面会提高路径成本。

## 3. 执行闭环

### 3.1 页面定位

执行时不再只返回相似任务，而是先生成页面 belief：

```text
b_t(v) = P(current_page = v | screenshot, history, last_action, task)
```

第一版使用确定性启发式：

- 当前截图 hash 和页面语义签名生成当前 `PageState`。
- Neo4j 中存在语义匹配节点时加入候选。
- 返回 top-k candidates、`current_state_id`、`confidence` 和 `is_novel`。

后续可以接入 OCR、布局解析和视觉 embedding，但不改变接口。

### 3.2 目标状态推断

用户任务先转为 `GoalSpec`，而不是直接匹配历史任务。

例如“把手机加入购物车”对应：

```text
domain = shopping
target_page_types = [cart]
forbidden_actions = [pay, submit_order, payment]
missing_info_policy = ask_user
```

目标是页面类型和槽位条件，不是固定截图。

### 3.3 路径规划

`SpatialGraphMemory.plan()` 在图谱上搜索从当前页面到目标页面的最小成本路径。第一版使用确定性最短路径，边权为：

```text
weighted_cost =
  base_cost
  + failure_rate * 3
  + risk_penalty
  - confidence_bonus
```

当找到可执行路线时，`MemoryManager.locate_and_get_context()` 返回：

- `mode = navigate`
- `next_actions = [route_plan.next_action]`
- `route_plan`
- `belief`
- `goal_spec`

找不到路线时继续进入 VLM explore 模式，并把空间图谱摘要注入上下文。

### 3.4 转移记录

当前执行循环的截图在动作前采集，动作后页面只能在下一轮看到。因此图谱转移不能在同一轮立刻写死。新的逻辑是：

1. 动作执行后缓存 `source_page + action`。
2. 下一轮 `locate_and_get_context()` 定位到新页面。
3. 写入真实的 `source_page -> action -> observed_page`。

这样能避免把动作前截图误当成动作后页面。

### 3.5 反思纠错

每次规划都有预期后置条件。下一轮观测后，系统比较：

```text
expected postcondition vs observed PageBelief
```

若不匹配，`repair()` 返回：

- `retry`：已经到达预期页面或状态。
- `rollback`：页面偏离路线，优先执行边上的回退动作。
- `replan`：没有可靠回退边，重新规划。
- `ask_user`：进入支付、地址、登录等用户拥有信息或高风险页面。
- `fallback_to_vlm`：没有足够图谱证据，交回 VLM。

## 4. 当前工程落点

第一版实现重点是闭环，而不是追求完整研究系统：

- 新增 `phone_agent/memory/spatial_graph_memory.py`。
- `MemoryManager` 初始化并委托 `SpatialGraphMemory`。
- `locate_and_get_context()` 保持旧字段兼容，同时新增 `belief`、`goal_spec`、`route_plan`、`repair_hint`。
- `GraphStore.add_state_transition()` 改为 `MERGE` 缺失的 `UIState`，并记录成功/失败计数和置信度。
- `GraphStore.get_outgoing_transitions()` 为路径规划提供边。
- `PhoneAgent` 的 navigate 模式修正为使用 `action` 字段，并且只在置信度足够时直接执行。

## 5. 评估指标

建议论文和工程评估都围绕空间认知是否有效：

- `Task Success Rate`
- `Average Steps`
- `Loop Rate`
- `Wrong Page Entry Rate`
- `Recovery Success Rate`
- `High-risk Action Avoidance Rate`
- `Graph Reuse Rate`
- `Replanning Count`
- `Human Annotation Cost`

关键消融：

- 去掉页面 belief。
- 去掉失败边惩罚。
- 去掉风险边权。
- 去掉 route repair。
- 只用相似任务轨迹召回。

## 6. 后续路线

短期优先级：

1. 用已有淘宝探索 JSON 构建图谱导入器。
2. 增强 `PageState`：接入 OCR、布局区域和可点击元素摘要。
3. 给 `RoutePlan` 增加多候选路径和路线解释。
4. 把 `SpecGuard` 产生的拦截结果写回边权，形成高风险动作负反馈。
5. 建立购物任务回放集，固定比较优化前后的成功率、步数和恢复能力。

研究表述上，本文的核心主张是：

> 现有移动 GUI Agent 主要记住轨迹；Spatial Page Graph 让 Agent 形成可导航的 App 空间认知，在不训练新模型、不依赖人工轨迹标注的前提下，自己探索、认路、规划和纠错。
