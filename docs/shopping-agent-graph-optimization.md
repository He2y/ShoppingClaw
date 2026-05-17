# 购物 Agent 图谱记忆优化方案

## 目标

把购物 Agent 的图谱记忆从“相似任务轨迹召回”升级为“训练无关的 App 空间认知系统”。核心约束是不训练新模型、不新增人工标注轨迹，只复用已有 GUI/VLM 模型和离线探索产物，让 Agent 能定位当前页面、规划路径、识别高风险页面，并在偏航后回退或重规划。

对用户的直接影响是：面对淘宝这类弹窗多、入口多、促销干扰强的 App，Agent 不再只记“上次怎么点”，而是知道“我在哪个页面、下一步应去哪个页面、哪些页面需要用户确认”。

## 当前已实现

### Spatial Page Graph

`phone_agent/memory/spatial_graph_memory.py` 已实现训练无关的空间图谱核心：

- `PageState`：页面节点，包含 `app`、`page_type`、`summary`、`landmarks`、`affordances`、`slots`、`risk_level`、`semantic_signature`。
- `TransitionEdge`：页面转移边，包含动作类型、目标、参数、后置页面、成功/失败计数、风险等级和加权成本。
- `PageBelief`：当前页面 belief，保留 top candidates 和置信度。
- `GoalSpec`：把任务解析成目标页面类型和槽位条件。
- `RoutePlan`：基于图谱的下一步路径计划。
- `RepairDecision`：动作后页面不符合预期时，输出 `retry`、`rollback`、`replan`、`ask_user` 或 `fallback_to_vlm`。

### 页面抽象增强

页面签名已经从 `semantic_layout = current_app` 扩展为：

```text
app + page_type + landmarks + affordances + slots
```

其中：

- `page_type` 来自页面类型关键词、离线探索的 `page_type` 或执行时传入的页面摘要。
- `landmarks` 从页面类型默认结构和 `OfflineExplorer` 的 `elements` key 抽取，例如 `search_bar`、`product_cards`、`buy_buttons`。
- `affordances` 从页面类型和元素语义抽取，例如 `tap_search`、`open_product`、`add_to_cart`、`checkout`。
- `slots` 从任务和页面摘要中抽取商品、搜索词、价格等轻量条件。
- `risk_level` 对 `cart`、`spec_selection`、`checkout`、`payment`、`login`、`address` 做风险分层。

这相当于把 VLM 看到的一张截图压缩成可规划的空间路标，而不是只保存截图 hash。

### 离线探索导入

新增 `phone_agent/memory/import_exploration.py`，可以把 `memory_db/exploration/*_explore_*.json` 和对应的 `*_explore_transitions_*.json` 导入空间图谱。

```bash
python -m phone_agent.memory.import_exploration --storage memory_db/exploration
```

仅验证解析、不写 Neo4j：

```bash
python -m phone_agent.memory.import_exploration --storage memory_db/exploration --dry-run
```

导入逻辑：

1. 读取 `pages`，把每个探索页转成 `PageState`。
2. 用 `page_type:summary` 建立探索文件中的页面 key 到状态节点的映射。
3. 读取 `transitions`，把 `from -> action -> to` 转成 `TransitionEdge`。
4. 本地内存始终可用；Neo4j 可用时同步写入 `UIState`、`Action`、`NEXT_ACTION`、`PRODUCES`。

`OfflineExplorer` 已经接入自动导入：探索结束保存 JSON 后，可以直接调用 `SpatialGraphMemory.import_exploration_files()` 把本次探索写入图谱。`run_explorer.py` 的 CLI 入口也已经修复为调用 `explore()`，默认探索后导入图谱；需要只保存 JSON 时使用：

```bash
python -m phone_agent.memory.run_explorer --app 淘宝 --queries "耳机,iPhone" --no-import-graph
```

### Neo4j 图存储增强

`GraphStore` 现在支持：

- `upsert_page_state()`：单独写入页面节点，即使页面暂无出边也不会丢失。
- `add_state_transition()`：在记录边时 `MERGE` 缺失的 `UIState`，并同步页面摘要、地标、可操作性、槽位和风险等级。
- `get_state_by_semantic()`：支持按 `semantic_signature` 或旧的 `semantic_layout` 查询。
- `get_outgoing_transitions()`：返回可用于 Dijkstra 规划的 `TransitionEdge`。

### 执行期接入

`MemoryManager.locate_and_get_context()` 已委托给 `SpatialGraphMemory`，并保持旧字段兼容，同时新增：

- `current_state_id`
- `belief`
- `goal_spec`
- `route_plan`
- `repair_hint`
- `semantic_context`

当图谱能规划出路线时，`PhoneAgent` 的 `navigate` 模式可以拿到风险感知的下一步动作；当置信度不足时，仍回退给 VLM 自主决策。

执行期后置条件校验也已经接入：图谱导航动作会携带 `_expected_postcondition`，动作执行后的下一次页面定位会比较预期页面与实际页面。如果不匹配，系统会：

1. 把该边记录为失败边，增加 `fail_count`。
2. 返回 `repair_hint`，提示 `replan`、`rollback`、`ask_user` 等修复策略。
3. 基于当前真实页面重新规划，而不是继续沿着错误路线执行。

同时修复了一个页面抽象问题：任务文本不再直接参与当前页面类型推断，只在页面观测无法判断时作为兜底。这样“加入购物车”任务不会把商品详情页误判成购物车页。

## 后续计划

### 1. 增强真实探索闭环

当前已经形成：

```text
explore App -> save pages/transitions JSON -> import SpatialGraph -> execute with graph route
```

下一步应把探索策略从“VLM 自由探索”升级成“图谱覆盖率驱动探索”：优先选择未知页面类型、未知 affordance、低风险未访问边，避免重复点同一类入口。

### 2. 增强页面 belief

当前 belief 主要来自页面语义签名和图查询。下一步需要把定位分数拆成多项：

```text
belief_score =
  visual_or_hash_similarity
  + text_similarity
  + layout_landmark_similarity
  + previous_action_transition_prior
  + task_relevance
```

目标是从单点页面匹配升级为 top-k belief，让 Agent 在页面变体、弹窗遮挡、搜索结果刷新时仍能保持位置感。

### 3. 修复动作落地

当前已经能产生 `repair_hint` 并惩罚失败边。下一步要把 `repair_hint.action` 真正落到动作层：例如 `rollback` 自动执行 Back，`ask_user` 触发 Interact，`replan` 禁止继续复用刚失败的边。

### 4. 购物高干扰页面特化

优先补强这些节点：

- SKU/规格弹窗：允许选择规格，但缺失用户偏好时必须 `ask_user`。
- 优惠/广告弹窗：优先识别为干扰节点，提供关闭或回退边。
- 登录页：不自动输入隐私信息，进入 `ask_user`。
- 支付/提交订单页：高风险，路径成本显著提高，禁止自动提交。
- 地址页：高风险用户数据页，缺失明确指令时停止询问。

### 5. 实验与论文指标

建议围绕以下指标做优化前后对比：

- 任务成功率
- 平均步数
- 重复动作率
- 错误页面进入率
- 回退成功率
- 高风险误触率
- 图谱复用率
- 重规划次数
- 人工轨迹标注成本

消融实验：

- 去掉页面 belief
- 去掉离线探索导入
- 去掉失败边惩罚
- 去掉风险边权
- 去掉反思修复

论文表达上，核心创新点应聚焦为：无需训练新模型的 Spatial Page Graph，把 GUI Agent 的记忆单位从人工轨迹迁移到可定位、可规划、可修复的 App 空间认知结构。
