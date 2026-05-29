# AMSG 学术级优化：改动文档

> 日期：2026-05-29  
> 分支：`refactor/architecture-optimization`  
> Commit：`db1b60e`  
> 改动量：新增 2,247 行 / 修改 41 行 / 15 个文件

## 1. 背景与目标

AMSG (Active Mobile Spatial Graph) 原有架构（22 模块、~3,400 行）具备清晰的工程能力，但存在 6 个阻碍学术发表的问题：

| # | 问题 | 位置 |
|---|------|------|
| 1 | 功能发现依赖 70 行 if-else + 硬编码关键词 | `functionality.py` |
| 2 | 信念定位用固定分数（0.92/1.0/0.82）+ MD5 签名 | `spatial_graph_memory.py:locate()` |
| 3 | 路径规划是教科书级 Dijkstra | `spatial_graph_memory.py:_shortest_path()` |
| 4 | 缺乏形式化定义 | 全局 |
| 5 | 边质量门控依赖 page_type 关键词，无法处理同一动作的多种结果 | `spatial_graph_memory.py:_is_promotable_edge()` |
| 6 | 硬编码注入 `product_detail→spec_selection` 边，伪造 `success_count=10` | `spatial_graph_memory.py:_load_edges()` line 1942-1983 |

**核心论题**：在动态移动 GUI 中，如何从少量安全探索中构建一个可复用、风险感知、可自维护的空间认知图谱，并用它减少 VLM 试错和高风险误操作。

**三个差异化点**（vs PG-Agent / MobiAgent / AgentRR / WebNavigator）：
1. 真实移动端动态 App（同一按钮可能触发规格弹窗/登录页/优惠弹窗/无效）
2. 风险感知后置条件验证（边必须通过 lifecycle 才能进入图谱）
3. 功能簇自发现 + 质量门控写入

---

## 2. 新增模块

### 2.1 `phone_agent/spatial/amsg_config.py` (117 行)

消融配置中心。所有优化通过 `AMSGOptimConfig` 的布尔开关独立控制。

```python
AMSGOptimConfig.legacy()     # 精确复现修改前行为
AMSGOptimConfig.full()       # 启用全部优化
AMSGOptimConfig.edge_only()  # 仅边生命周期
AMSGOptimConfig.belief_only() # 仅多信号信念
AMSGOptimConfig.planner_only() # 仅增强规划
```

**消融矩阵**（7 种配置）：

| Config | Belief | Planner | Edge Policy | Func | Heuristic |
|--------|--------|---------|-------------|------|-----------|
| legacy | fixed | dijkstra | legacy | keyword | ON |
| edge_only | fixed | dijkstra | verified | keyword | OFF |
| belief_only | bayes | dijkstra | legacy | keyword | ON |
| planner_only | fixed | belief_a\* | legacy | keyword | ON |
| func_only | fixed | dijkstra | legacy | embedding | ON |
| edge+belief | bayes | dijkstra | verified | keyword | OFF |
| full | bayes | belief_a\* | verified | embedding | OFF |

### 2.2 `phone_agent/spatial/edge_lifecycle.py` (307 行)

**论文核心差异化**。实现 Definition 8 (Edge Lifecycle) 和 Definition 9 (Outcome Distribution)。

**边生命周期状态机**：
```
hypothesis → candidate → promoted → (optional) demoted
```

- **`OutcomeDistribution`**：追踪同一 (page, action) 对的所有真实结果。例如在 product_detail 点击"加入购物车"可能产生 `{"spec_selection": 8, "login": 2, "promotion_popup": 1}`。
- **`EdgeLifecycleRecord`**：记录每条边的验证次数、dominant outcome ratio、风险等级。
- **`EdgeLifecycleManager`**：
  - `record_outcome()` — 每次真机执行后记录实际结果
  - `requires_vlm_verification()` — 当 outcome entropy 超过阈值时自动触发 VLM 验证（数据驱动，替代硬编码列表）
  - `get_promotable_edges()` — 仅返回满足 min_verification_count + dominance_ratio ≥ 0.6 的边

**解决的问题**：
- 问题 5：边质量门控从关键词匹配升级为 postcondition 验证
- 问题 6：启发式注入受 `enable_heuristic_injection` 控制，paper 模式关闭

### 2.3 `phone_agent/spatial/belief_localizer.py` (274 行)

实现 Definition 3 (Observation Model) 和 Definition 7 (Belief Update)。

**4 通道观测模型**：
$$P(o \mid v) = \sum_{c=1}^{4} \hat{w}_c \cdot \phi_c(o, v)$$

| 通道 | $\phi_c$ | 是否必须 |
|------|----------|---------|
| Visual | VLM 截屏 embedding cosine similarity | 可选 |
| Semantic | 文本 embedding cosine similarity | 可选 |
| Structural | 0.35·app + 0.35·page_type + 0.20·landmark Jaccard + 0.10·affordance Jaccard | **必须** |
| Temporal | 频率估计的转移概率 P(v|v',a) | 可选 |

**优雅降级**：当 visual/semantic embedding 不可用时，权重自动重分配到 structural + temporal。

**Bayesian 更新**：
$$\mathcal{B}_t(v) = \eta \cdot P(o_t \mid v) \cdot \sum_{v'} P(v \mid v', a_{t-1}) \cdot \mathcal{B}_{t-1}(v')$$

**解决的问题**：问题 2（固定分数 → 概率分布），产出 `BeliefDistribution.entropy` 供 planner 使用。

### 2.4 `phone_agent/spatial/enhanced_planner.py` (330 行)

实现 Definition 5 (Planning Objective)。

**三个可消融后端**：
1. **`dijkstra`** — 精确复制 legacy `_shortest_path()`
2. **`astar`** — Dijkstra + admissible schema-distance heuristic $h(v) = \min_\tau d_\Sigma(\tau(v), \tau)$
3. **`belief_astar`** — A\* + temporal decay + exploration bonus + outcome entropy penalty

**增强代价函数**：
$$C'(e) = C_{\text{base}}(e) + \alpha \cdot \text{staleness}(e) - \frac{\beta}{\sqrt{1 + n_{\text{visit}}}} + \gamma \cdot H_{\mathcal{O}}(e)$$

- **Temporal decay**：长时间未遍历的边成本增加
- **Exploration bonus**：UCB 风格，偏好低访问次数的节点
- **Outcome entropy penalty**：不确定转移（高熵）的代价更高

**解决的问题**：问题 3（vanilla Dijkstra → belief-aware A\*）

### 2.5 `phone_agent/spatial/role_classifier.py` (132 行)

实现 Definition 6 (Functionality Discovery)。

- **`RoleClassifier`** Protocol — 定义接口
- **`KeywordRoleClassifier`** — 包装现有 if-else 链，行为完全不变
- **`EmbeddingRoleClassifier`** — 基于原型的分类，使用 sentence embedding 的 cosine similarity。原型通过 `register_verified_role()` 在线增长（postcondition 验证确认 role 后自动添加）

**解决的问题**：问题 1（硬编码关键词 → 可插拔 Protocol）

### 2.6 `phone_agent/spatial/FORMALIZATION.md` (200 行)

9 个形式化定义，每个对应具体代码模块：

| # | 定义 | 数学符号 |
|---|------|---------|
| 1 | AMSG 图谱 | $\mathcal{G} = (V, E^c, E^h, \Sigma, \mathcal{B}, \Pi)$ |
| 2 | Page State | $v = (\text{app}, \tau, \mathbf{L}, \mathbf{A}, \mathbf{S}, \rho, \sigma)$ |
| 3 | 多信号观测模型 | $P(o \mid v) = \sum_c w_c \cdot \phi_c(o, v)$ |
| 4 | 频率转移模型 | $\hat{P}(v' \mid v, a)$ |
| 5 | 规划目标 | $\arg\min_\pi \sum_t C'(e_t) - \lambda H(\mathcal{B}_t) \cdot u(e_t)$ |
| 6 | 功能发现 | $\mathcal{C}: (\text{element}, \tau, \text{ctx}) \to (\text{role}, \text{type})$ |
| 7 | Bayesian 信念更新 | $\mathcal{B}_t(v) = \eta \cdot P(o_t \mid v) \cdot \sum P(v \mid v', a) \cdot \mathcal{B}_{t-1}(v')$ |
| 8 | 边生命周期 | hypothesis $\to$ candidate $\to$ promoted $\to$ demoted |
| 9 | Outcome Distribution | $\mathcal{O}(v, a) = \{(v'_i, p_i)\}$ 及 Shannon 熵 |

---

## 3. 修改的现有文件

### 3.1 `phone_agent/memory/spatial_graph_memory.py` (+212/-41)

| 方法 | 改动 |
|------|------|
| `__init__()` | 接受 `config: AMSGOptimConfig`，初始化 `EdgeLifecycleManager` / `MultiSignalLocalizer` / `EnhancedPlanner` |
| `locate()` | 新增 Bayesian 分支：当 `use_multi_signal_belief=True` 时构造 `ObservationSignals` 委托给 `MultiSignalLocalizer.update()` |
| `record_observation()` | 新增 `EdgeLifecycleManager.record_outcome()` 调用，追踪每次真机结果 |
| `_is_promotable_edge()` | 当 `edge_promotion_policy="verified"` 时委托给 `EdgeLifecycleManager`，检查 lifecycle stage |
| `_load_edges()` | 启发式注入受 `enable_heuristic_injection` 开关控制；同时修复了 operator precedence bug |
| `_shortest_path()` | 当 `planner_backend != "dijkstra"` 时委托给 `EnhancedPlanner`，传入 belief entropy 和 outcome entropy |

### 3.2 `phone_agent/spatial/runtime_controller.py` (+15)

- `_requires_vlm_verification()` 新增数据驱动分支：查询 `EdgeLifecycleManager.get_outcome_entropy()`，高熵转移自动触发 VLM 验证，替代硬编码的 `VLM_VERIFY_TRANSITIONS` 集合

### 3.3 `phone_agent/spatial/functionality.py` (+18/-1)

- `FunctionalityExtractor` 新增 `__init__(role_classifier)` 和 `_classify()` 方法，通过可注入的 `RoleClassifier` 替代内联关键词匹配

### 3.4 `phone_agent/spatial/functionality_cluster.py` (+82)

- 新增 `EmbeddingFunctionalityClusterer`：cosine similarity + structural bonuses，无 embedding 时降级为 Jaccard

### 3.5 `phone_agent/spatial/__init__.py` (+17)

- 导出新增模块：`AMSGOptimConfig`, `EdgeLifecycleManager`, `OutcomeDistribution`, `MultiSignalLocalizer`, `BeliefDistribution`, `EnhancedPlanner`, `RoleClassifier`, `KeywordRoleClassifier`, `EmbeddingRoleClassifier`

---

## 4. 测试

新增 4 个测试文件，共 40 个测试用例：

| 文件 | 测试数 | 覆盖 |
|------|--------|------|
| `test_edge_lifecycle.py` | 10 | 边生命周期状态机、outcome entropy、VLM 阈值、高风险降级 |
| `test_belief_localizer.py` | 12 | 4 通道独立性、Bayesian 更新正确性、temporal 历史、优雅降级 |
| `test_enhanced_planner.py` | 11 | 3 后端路径一致性、schema heuristic admissibility、temporal decay、exploration bonus、outcome entropy penalty |
| `test_role_classifier.py` | 7 | keyword/embedding 一致性、原型去重、FunctionalityExtractor 注入 |

**回归结果**：80/80 通过（原有测试 40 + 新增 40），零回归。

---

## 5. 向后兼容性

- `AMSGOptimConfig.legacy()` 精确复现修改前行为
- `SpatialGraphMemory()` 无参数构造默认使用 legacy 配置
- `FunctionalityExtractor()` 无参数构造保持原有内联逻辑
- 所有新增字段使用默认值，不影响现有序列化/反序列化
- 启发式边注入在 legacy 模式下保持不变

---

## 6. 文件清单

```
新建 (6 个模块 + 4 个测试):
  phone_agent/spatial/amsg_config.py        117 行  消融配置
  phone_agent/spatial/edge_lifecycle.py     307 行  边生命周期 + Outcome Distribution
  phone_agent/spatial/belief_localizer.py   274 行  4 通道 Bayesian 信念
  phone_agent/spatial/enhanced_planner.py   330 行  A* + belief-aware 规划
  phone_agent/spatial/role_classifier.py    132 行  可插拔功能分类
  phone_agent/spatial/FORMALIZATION.md      200 行  9 个形式化定义
  tests/test_edge_lifecycle.py              168 行
  tests/test_belief_localizer.py            159 行
  tests/test_enhanced_planner.py            174 行
  tests/test_role_classifier.py              83 行

修改 (5 个文件):
  phone_agent/memory/spatial_graph_memory.py   +212/-41
  phone_agent/spatial/__init__.py              +17
  phone_agent/spatial/functionality.py         +18/-1
  phone_agent/spatial/functionality_cluster.py +82
  phone_agent/spatial/runtime_controller.py    +15

总计: +2,247 / -41
```
