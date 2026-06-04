# AMSG 图谱采集构建管线 — 工作进度

## 1. 项目目标

为论文 "Active Mobile Spatial Graph (AMSG)" 构建完整的端到端图谱采集管线：从真机自动探索，到数据合并去重，到 Neo4j 持久化，每条边携带生命周期元数据，支持跨 session 累积验证。

论文核心论点：**移动 GUI 空间图必须自维护——边通过后置条件验证获取信任，其可靠性以结果分布形式追踪，规划器感知跳转的不确定性。**

---

## 2. 形式化框架（8 个定义）

| # | 定义 | 对应代码 | 状态 |
|---|---|---|---|
| 1 | AMSG 图结构 $\mathcal{G}=(V, E^c, E^h, \Sigma, \mathcal{B}, \Pi)$ | `SpatialGraphMemory` | ✅ 实现 |
| 2 | 页面状态（7 元组节点） | `UIState` Neo4j 节点 | ✅ 11 种页面 |
| 3 | 多信号观测模型（4 通道融合） | `MultiSignalLocalizer` | ✅ 代码就绪 |
| 4 | 频率估计跳转模型 | `OutcomeDistribution` | ✅ 持久化 |
| 5 | 不确定性感知规划（3 后端） | `EnhancedPlanner` | ✅ 代码就绪 |
| 6 | 贝叶斯置信更新 | `BeliefDistribution` | ✅ 代码就绪 |
| 7 | 边生命周期 hypothesis→candidate→promoted→demoted | `EdgeLifecycleManager` | ✅ 持久化到 Neo4j |
| 8 | 结果分布与熵 | `OutcomeDistribution.entropy` | ✅ 持久化到 Neo4j |

---

## 3. 图谱采集构建管线

### 3.1 管线总览

```
真机自动探索（Supervisor-Executor）
        │
        ▼
  5 个 JSON 文件（pages / transitions / trajectory / report / lifecycle）
        │
        ▼
  合并去重（merge_and_import.py）
        │  按 page_type 去重页面，按 (src, action_type, tgt) 去重跳转
        │  加载 lifecycle JSON 到 EdgeLifecycleManager
        │  transition key 对齐到 canonical summary
        ▼
  AMSG 导入管线（SpatialGraphMemory）
        │  import_exploration_staging() → 页面规范化 + 跳转过滤
        │  promote_staging_to_canonical() → 写入 Neo4j + lifecycle 元数据
        ▼
  Neo4j shopping-spatial-v4
```

### 3.2 自主探索（run_autonomous_explorer.py）

**双 VLM 架构：**
- **Supervisor**（qwen3-vl-plus）：看截图 + 图谱摘要，输出 JSON 规划（page_type、elements、plan）
- **Executor**（autoglm-phone）：接收单条指令，输出一个 do() 动作

**Supervisor 提示词设计：**
- 图谱摘要标记 ✓已探索 / ★未探索 的元素，按 page_type 聚合
- 已知跳转显示为 ✓{target}，避免重复走
- 规则：绝不重复已走路径，优先底部导航 Tab，弹窗停留不超 1 步

**安全策略（分层）：**

| 层级 | 阻止的页面/操作 | 说明 |
|---|---|---|
| 全局阻止 | payment, address, login, permission | 任何操作均拒绝 |
| 全局阻止 | 立即支付、确认支付、确认付款 | 无论在哪个页面 |
| 结算页阻止 | 提交订单、确认订单、立即下单、去支付 | 仅在 checkout/cart/spec_selection 页面 |
| 允许 | checkout 页面的浏览和返回 | 允许进入以覆盖购物流 |

**收敛检测：** 连续 N 轮没有新页面类型发现 → 停止

**输出：** 5 个 JSON 文件
1. `{app}_autonomous_pages_{ts}.json` — 发现的页面
2. `{app}_autonomous_transitions_{ts}.json` — 跳转记录
3. `{app}_autonomous_trajectory_{ts}.json` — 完整步骤轨迹
4. `{app}_autonomous_report_{ts}.json` — 功能簇、收敛数据、覆盖报告
5. `{app}_autonomous_lifecycle_{ts}.json` — 边生命周期记录和结果分布

### 3.3 合并去重（merge_and_import.py）

```bash
python -m phone_agent.memory.merge_and_import \
  --input-dirs dir1 dir2 dir3 \
  --output-dir merged/ \
  --app 淘宝 \
  --import-graph   # 加此参数才写入 Neo4j
```

**去重逻辑：**
- 页面：按 page_type 分组，保留元素最丰富的变体，合并所有变体的 elements
- 跳转：按 (src_type, action_type, tgt_type) 去重，transition key 对齐到 canonical summary
- 自环和 unknown 页面跳转被丢弃

**导入前必须审核：** `--import-graph` 不加时仅输出合并结果 JSON，人工检查后再加参数导入。

### 3.4 AMSG 导入管线（SpatialGraphMemory）

`import_exploration_staging()` → `promote_staging_to_canonical(persist=True)`

每条边写入时附带 lifecycle 元数据：
- `lifecycle_stage`：promoted / hypothesis / candidate / demoted
- `verification_count`：后置条件验证次数
- `dominance_ratio`：主导结果占比
- `outcome_distribution_json`：结果分布 JSON
- `outcome_entropy`：香农熵
- `created_at` / `last_traversed`：时间戳

### 3.5 跨 Session 累积

`SpatialGraphMemory.__init__()` 启动时自动从 Neo4j 加载 lifecycle 数据到内存 `EdgeLifecycleManager`，实现跨 session 验证累积。

---

## 4. 图谱设计（shopping-spatial-v4）

### 4.1 Neo4j Schema

```
(UIState) ─[NEXT_ACTION]→ (Action) ─[PRODUCES]→ (UIState)
```

**UIState 节点属性：**

| 属性 | 类型 | 说明 |
|---|---|---|
| state_id | string | 唯一标识 |
| app | string | 应用名（淘宝） |
| page_type | string | 页面类型 |
| summary | string | 语义描述 |
| landmarks | list | 视觉锚点 |
| affordances | list | 可操作元素 |
| slots | JSON | 任务槽位 |
| risk_level | enum | normal / medium / high |
| semantic_signature | string | 去重哈希 |
| updated_at | timestamp | 更新时间 |

**Action 节点属性：**

| 属性 | 类型 | 说明 |
|---|---|---|
| action_id | string | 唯一标识 |
| type | string | Tap / Swipe / Back / Type / Compound |
| intent | string | 语义意图 |
| semantic_target | string | 操作目标描述 |
| source_page_type | string | 来源页面 |
| target_page_type | string | 目标页面 |
| region | string | 操作区域（top_left / center / bottom_right ...） |
| risk_level | string | 跳转风险 |
| **lifecycle_stage** | string | hypothesis / candidate / promoted / demoted |
| **verification_count** | int | 后置条件验证次数 |
| **dominance_ratio** | float | 主导结果比例 |
| **outcome_distribution_json** | JSON | 结果分布 |
| **outcome_entropy** | float | 香农熵 |
| **created_at** | timestamp | 首次观测时间 |
| **last_traversed** | timestamp | 最近遍历时间 |

**NEXT_ACTION 关系属性：** confidence, frequency, fail_count
**PRODUCES 关系属性：** success_count, fail_count, success_rate

### 4.2 当前图谱状态

| 指标 | 值 |
|---|---|
| UIState 节点 | 11 |
| Action 节点 | 45 |
| 页面类型 | 11 种 |
| 唯一跳转类型 | 30 对 |
| AMSG 字段覆盖率 | 7/7 = 100% |
| 自环 | 0 |
| 孤立节点 | 0 |

**页面类型：**

| 页面 | 风险 | 从 home 可达 |
|---|---|---|
| home | normal | — |
| search_input | normal | 1 跳 |
| search_result | normal | 1 跳 |
| product_detail | normal | 1 跳 |
| spec_selection | medium | 2 跳 |
| cart | medium | 1 跳 |
| checkout | medium | 2 跳 |
| filter_panel | normal | 2 跳 |
| my_account | normal | 1 跳 |
| store | normal | 1 跳 |
| dialog | normal | 1 跳 |

**核心购物流（6/6 完整）：**

```
home →(2)→ search_input →(1)→ search_result →(2)→ product_detail →(2)→ spec_selection →(1)→ cart →(1)→ checkout
```

**跳转拓扑（30 对）：**

```
cart → checkout(1), home(1), my_account(1)
dialog → home(2), product_detail(1)
filter_panel → search_result(2)
home → cart(1), dialog(1), my_account(2), product_detail(2), search_input(2), search_result(2), store(1)
my_account → home(2), product_detail(1)
product_detail → cart(1), dialog(1), home(2), search_result(2), spec_selection(2), store(2)
search_input → filter_panel(1), product_detail(1), search_result(1)
search_result → filter_panel(2), product_detail(2)
spec_selection → cart(1), product_detail(2)
store → home(1), product_detail(2)
```

### 4.3 配置切换

通过环境变量 `AMSG_CONFIG` 切换预设（默认 legacy）：

| 预设 | 置信模型 | 规划器 | 边策略 | 启发式注入 |
|---|---|---|---|---|
| legacy | 固定 | dijkstra | 全部直接 promote | 开启 |
| **full** | 贝叶斯 | belief_a* | 需验证才 promote | 关闭 |
| edge_only | 固定 | dijkstra | 需验证 | 关闭 |
| belief_only | 贝叶斯 | dijkstra | 全部直接 | 开启 |
| planner_only | 固定 | belief_a* | 全部直接 | 开启 |

---

## 5. 关键文件

| 文件 | 职责 |
|---|---|
| `phone_agent/memory/autonomous_explorer.py` | Supervisor-Executor 自主探索 |
| `phone_agent/memory/run_autonomous_explorer.py` | CLI 入口 |
| `phone_agent/memory/merge_and_import.py` | 合并去重 + Neo4j 导入 |
| `phone_agent/memory/graph_store.py` | Neo4j CRUD（含 lifecycle 参数） |
| `phone_agent/memory/graph_lifecycle_store.py` | lifecycle 专用 Neo4j 操作 |
| `phone_agent/memory/spatial_graph_memory.py` | AMSG 模块串联、导入管线 |
| `phone_agent/spatial/edge_lifecycle.py` | 边生命周期状态机 + 序列化 |
| `phone_agent/spatial/amsg_config.py` | 消融实验配置预设 |
| `phone_agent/SYSTEM_DESIGN_CN.md` | 论文形式化定义（8 个） |
| `tests/test_autonomous_explorer.py` | 40 个单元测试 |
| `tests/test_edge_lifecycle.py` | 22 个单元测试 |

---

## 6. 探索数据

| 目录 | 内容 | 结果 |
|---|---|---|
| `autonomous_v4/` | 首次成功探索 | 6 页面类型 |
| `autonomous_v7/` | 最丰富探索（26 页面） | 合并入图 |
| `autonomous_v8/` | 补充探索 | 合并入图 |
| `autonomous/` | ✓/★ 改进后首轮 | 合并入图 |
| `autonomous_e2e_1/` | 端到端第 2 轮 | search/product 流 |
| `autonomous_e2e_2/` | 端到端第 3 轮 | 发现 store 页面 |
| `autonomous_checkout/` | checkout 补全轮 | cart→checkout |
| `merged_final/` | 早期合并结果 | — |
| `merged_e2e/` | 端到端合并结果 | 导入 Neo4j |
| `merged_checkout/` | checkout 合并结果 | 导入 Neo4j |

---

## 7. 已完成的改进

### Phase 1：边生命周期持久化
- `EdgeLifecycleRecord` / `OutcomeDistribution` 增加 `from_dict()` 反序列化
- `EdgeLifecycleManager` 增加 `bulk_load()` / `bulk_export()` / `check_demotion()`
- `GraphLifecycleStore` 新模块：lifecycle 批量 persist / load / demote
- `graph_store.add_state_transition()` 增加 `lifecycle` 参数
- `SpatialGraphMemory.__init__()` 启动时从 Neo4j 恢复 lifecycle

### Phase 2：导入管线闭环
- `promote_staging_to_canonical()` 传递 lifecycle dict 到 Neo4j
- `flush_staged_graph()` 批量持久化 + demotion 检查
- `autonomous_explorer._save_results()` 输出第 5 个 lifecycle JSON
- `merge_and_import.py` 导入时加载 lifecycle 文件

### Phase 3：安全策略放宽
- checkout 从 HIGH_RISK 降为 MEDIUM_RISK
- 安全检查同时匹配 action dict + instruction + reasoning 文本
- 分层阻止：全局阻止支付动作，结算页阻止提交订单

### Phase 4：Supervisor 改进
- graph_summary 标记 ✓/★ 元素，按 page_type 聚合
- 提示词强调不重复已走路径
- 已知跳转显示为 ✓ 以引导探索新路径

### Phase 5：图谱清理
- 删除 7 条语义错误的边（方向反的 go_back、不存在的跳转）
- 移除孤立节点
- 修正风险等级
- 回填 outcome_distribution_json 至 100% 覆盖

### Phase 6：文档更新
- SYSTEM_DESIGN.md / SYSTEM_DESIGN_CN.md 移除 Functionality Discovery（原 Definition 6）
- 重新编号为 8 个定义
- 删除 merge_and_import.py 中的废弃代码

---

## 8. 待优化项

| 优先级 | 项目 | 说明 |
|---|---|---|
| P1 | Supervisor 探索效率 | 仍然容易进入直播/弹窗区域浪费步数，需更强的 page_type 快速识别 |
| P1 | PageClassifier 准确率 | 结算页被分类为 product_detail，需针对 checkout 特征加强 |
| P2 | 多轮验证积累 | 当前所有边 verification_count=1，需多次探索积累真实 outcome 分布 |
| P2 | 消融实验 | full vs legacy 预设的对比实验尚未运行 |
| P3 | 更多 App 覆盖 | 当前仅淘宝，需扩展到京东/拼多多验证泛化性 |
