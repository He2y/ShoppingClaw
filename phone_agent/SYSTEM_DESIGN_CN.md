# Mobile-ShoppingAgent 系统设计

> 主动移动空间图（AMSG）框架的技术规范。
> 涵盖系统架构、形式化定义、模块设计和核心创新。
> 用作学术论文写作的工程参考文档。

---

## 1. 问题陈述

移动购物应用（淘宝、京东、拼多多）与网页相比，呈现出本质不同的自动化挑战：

1. **非确定性跳转** --- 产品页面上同一个"加入购物车"按钮可能打开规格选择弹窗（60%）、登录页面（20%）、促销弹窗（15%），或无可见变化（5%）。网页相比之下几乎是确定性的。
2. **无 DOM、无 URL** --- 智能体只能通过截图观察，必须依靠视觉和语义信号推断页面身份，而非稳定的选择器。
3. **高风险操作不可逆** --- 提交订单、确认支付或修改地址等操作无法通过"返回"键撤销。
4. **UI 布局随版本和 A/B 测试变化** --- 昨天学到的图边今天可能已经失效。

现有 GUI 智能体框架（PG-Agent、MobiAgent、AgentRR、WebNavigator）将跳转视为确定性观测：观察一次跳转就写入图中并永久复用。这一假设在真实移动应用上不成立。

**核心论点**：在动态移动 GUI 中，空间图必须具备*自维护*能力 --- 边必须通过后置条件验证来获取信任，其可靠性必须以结果分布的形式被追踪，规划器必须感知跳转的不确定性。

---

## 2. 形式化定义

系统建立在 8 个形式化定义之上，每个定义都映射到具体的代码模块。

### 定义 1：主动移动空间图

$$\mathcal{G} = (V,\; E^c,\; E^h,\; \Sigma,\; \mathcal{B},\; \Pi)$$

| 符号 | 含义 | 代码 |
|------|------|------|
| $V$ | 页面状态（节点）的有限集合 | `PageState`, `PageNode` |
| $E^c$ | 已验证（已提升）边 | `EdgeLifecycleRecord.stage == "promoted"` |
| $E^h$ | 假设边（未验证） | `EdgeLifecycleRecord.stage in {"hypothesis", "candidate"}` |
| $\Sigma$ | 领域模式（页面类型分类体系 + 规范跳转） | `MobileSchema`, `SchemaRegistry` |
| $\mathcal{B}$ | $V$ 上的置信分布 | `BeliefDistribution` |
| $\Pi$ | 生成动作序列的规划器 | `EnhancedPlanner` |

图具有*自维护*性：边在后置条件验证通过后从 $E^h$ 提升为 $E^c$，在观测到失败时降级回去。

### 定义 2：页面状态（节点）

$$v = (\text{app},\; \tau,\; \mathbf{L},\; \mathbf{A},\; \mathbf{S},\; \rho,\; \sigma)$$

| 字段 | 类型 | 描述 |
|------|------|------|
| app | string | 标准化的应用标识符 |
| $\tau$ | enum | 页面类型（首页、搜索输入、搜索结果、商品详情、规格选择、购物车、结算……） |
| $\mathbf{L}$ | set | 地标 --- 用于识别页面的稳定视觉锚点 |
| $\mathbf{A}$ | set | 可操作元素 --- 智能体可交互的界面元素 |
| $\mathbf{S}$ | dict | 槽位 --- 任务相关的键值对（搜索词、商品名、规格选择等） |
| $\rho$ | enum | 风险等级 $\in$ {normal, medium, high} |
| $\sigma$ | string | 语义签名 --- 用于去重的确定性哈希 |

当两张截图的语义签名匹配时，它们映射到同一个节点。

### 定义 3：多信号观测模型

$$P(o \mid v) = \sum_{c=1}^{C} \hat{w}_c \cdot \phi_c(o, v)$$

四个观测通道，当某通道不可用时权重自动重分配：

| 通道 $c$ | 信号 $\phi_c$ | 默认权重 | 可用性 |
|----------|--------------|---------|--------|
| 1. 视觉 | VLM 截图嵌入的余弦相似度 | 0.30 | 可选 |
| 2. 语义 | 文本嵌入的余弦相似度 | 0.25 | 可选 |
| 3. 结构 | $0.35[\text{app}=] + 0.35[\tau=] + 0.20 J(\mathbf{L}) + 0.10 J(\mathbf{A})$ | 0.25 | 始终可用 |
| 4. 时序 | 来自频率模型的 $\hat{P}(v \mid v_{\text{prev}}, a_{\text{prev}})$ | 0.20 | 首次跳转后 |

$J(\cdot)$ 表示 Jaccard 相似度。当某通道返回 $\text{None}$（不可用）时，其权重按比例重分配给可用通道。

### 定义 4：频率估计跳转模型

$$\hat{P}(v' \mid v, a) = \frac{n(v, a, v')}{\sum_{v''} n(v, a, v'')}$$

纯观测驱动。为时序通道（定义 3）和结果分布（定义 8）提供输入。

### 定义 5：规划目标

$$\pi^* = \arg\min_\pi \sum_{t=0}^{T} C'(e_t) - \lambda \, H(\mathcal{B}_t) \cdot u(e_t)$$

增强边代价：

$$C'(e) = \underbrace{1 + 3\,r_{\text{fail}} + p_{\text{risk}} - 0.3\,c_{\text{conf}}}_{\text{基础代价}} + \underbrace{\alpha \cdot s(e)}_{\text{时效衰减}} - \underbrace{\frac{\beta}{\sqrt{1 + n_{\text{visit}}(t)}}}_{\text{探索奖励}} + \underbrace{\gamma \cdot H_{\mathcal{O}}(e)}_{\text{熵惩罚}}$$

| 项 | 公式 | 作用 |
|----|------|------|
| 基础代价 | $1 + 3\,r_{\text{fail}} + p_{\text{risk}} - 0.3\,c_{\text{conf}}$ | 惩罚易失败和高风险的边 |
| 时效衰减 $s(e)$ | $1 - \exp(-\Delta t / \text{halflife})$ | 陈旧的边吸引力降低 |
| 探索奖励 | $\beta / \sqrt{1 + n_{\text{visit}}}$ | 访问不足的状态吸引力增加 |
| 熵惩罚 | $\gamma \cdot H_{\mathcal{O}}(v, a)$ | 不可预测的跳转受到惩罚 |

三个可插拔后端：Dijkstra（传统）、A\*（模式启发式）、Belief-A\*（完整优化）。

### 定义 6：贝叶斯置信更新

$$\mathcal{B}_t(v) = \eta \cdot P(o_t \mid v) \cdot \sum_{v'} P(v \mid v', a_{t-1}) \cdot \mathcal{B}_{t-1}(v')$$

- 香农熵 $H(\mathcal{B}) = -\sum_v \mathcal{B}(v) \log \mathcal{B}(v)$ 输入规划器的信息增益项。
- MAP 估计 $v^* = \arg\max_v \mathcal{B}(v)$ 用于动作锚定。

### 定义 7：边生命周期

$$e: \text{hypothesis} \xrightarrow{n \geq k,\; \text{dom} \geq \theta} \text{candidate} \xrightarrow{\text{gate}} \text{promoted} \xrightarrow{\text{fail\_rate} > \delta} \text{demoted}$$

| 阶段 | 条件 | 图归属 | 规划角色 |
|------|------|--------|---------|
| hypothesis（假设） | $n_{\text{verify}} < k$ | 仅 $E^h$ | 不使用 |
| candidate（候选） | $n \geq k$，支配度 $< \theta$ 或高风险 | $E^h$ | 可用但带惩罚 |
| promoted（已提升） | 支配度 $\geq \theta$，非高风险 | $E^c$ | 持久化到 Neo4j |
| demoted（已降级） | 提升后失败率 $> \delta$ | 从 $E^c$ 移除 | 回退到 $E^h$ |

默认参数：$k = 1$，$\theta = 0.6$，$\delta$ = 可配置。

### 定义 8：结果分布

$$\mathcal{O}(v, a) = \{(v'_1, p_1), \ldots, (v'_m, p_m)\}$$

$$H_{\mathcal{O}}(v, a) = -\sum_{i=1}^{m} p_i \log p_i$$

| 熵值 | 解释 | 智能体行为 |
|------|------|-----------|
| 低 $H_{\mathcal{O}}$ | 确定性跳转 | 可安全使用图快捷路径，跳过 VLM |
| 高 $H_{\mathcal{O}}$ | 不可预测的跳转 | 需要 VLM 协同验证 |

用**数据驱动的决策边界**替代硬编码的 VLM 验证列表：当 $H_{\mathcal{O}} > \theta_{\text{vlm}}$（默认 0.8）时，智能体自动触发该跳转的 VLM 验证。

---

## 3. 系统架构

### 3.1 模块映射

```
phone_agent/
  spatial/                           # AMSG 核心模块
    amsg_config.py                   # AMSGOptimConfig（13 个字段，5 个预设）
    edge_lifecycle.py                # 定义 7-8：EdgeLifecycleManager, OutcomeDistribution
    belief_localizer.py              # 定义 3,6：MultiSignalLocalizer, BeliefDistribution
    enhanced_planner.py              # 定义 5：EnhancedPlanner（3 个后端）
    role_classifier.py               # RoleClassifier 协议（辅助页面元素分类）
    core.py                          # PageNode, BeliefState, AffordanceEdge
    semantics.py                     # ScreenSemanticsExtractor
    hypothesis.py                    # EdgeHypothesisGenerator
    active_builder.py                # ActiveGraphBuilder（前沿评分）
    schema_registry.py               # MobileSchema, SchemaRegistry
    runtime_controller.py            # GraphRuntimeController（定义 8 VLM 验证）
    verifier.py                      # PostconditionVerifier
    reporting.py                     # 报告构建器与格式化器
    FORMALIZATION.md                 # 8 个形式化定义

  memory/                            # 持久化与集成
    spatial_graph_memory.py          # SpatialGraphMemory（串联所有 AMSG 模块）
    graph_store.py                   # Neo4j CRUD, TaskIndex
    memory_manager.py                # MemoryManager（面向智能体的 API）
    memory_store.py                  # FAISS 向量存储
    task_index.py                    # FAISS 任务语义搜索
    offline_explorer.py              # VLM 驱动的应用探索
    state_manager.py                 # UI 状态追踪

  agent.py                           # PhoneAgent（主循环）
  model/adapters.py                  # VLM 适配器（5 个模型）
  actions/handler*.py                # 平台相关的动作执行
```

### 3.2 执行循环

```
截图 ──> 置信定位 ──> 目标推理 ──> 路径规划 ──> 动作选择
  ^        (定义 3,6)                 (定义 5,7,8)     |
  |                                                   v
  └───────── 后置条件验证 <── 动作执行 <── 边生命周期更新
                (定义 7,8)                    (定义 4)
```

每个智能体步骤：

1. **截图** --- 从设备（ADB/HDC/XCTest）捕获当前屏幕。
2. **置信定位** --- `MultiSignalLocalizer.update()` 融合 4 个观测通道，生成已知页面状态上的概率分布（定义 3、6）。
3. **目标推理** --- `GoalSpec.from_task()` 从自然语言指令中提取目标页面类型和任务槽位。
4. **路径规划** --- `EnhancedPlanner.plan()` 搜索图中代价最小的路径。代价综合了边的时效衰减、探索奖励和结果熵（定义 5）。仅信任来自 `EdgeLifecycleManager` 的已提升边（$E^c$）；假设边带有惩罚。
5. **动作选择** --- `next_planned_action()` 发出路径的第一步。高熵跳转触发 VLM 协同验证（定义 8）。
6. **动作执行** --- `ActionHandler.execute()` 将抽象动作转换为设备特定命令。
7. **边生命周期更新** --- `EdgeLifecycleManager.record_outcome()` 记录观测到的后置条件。边在生命周期状态机中推进（定义 7）。`OutcomeDistribution` 更新熵值供未来规划使用（定义 8）。

### 3.3 配置与消融实验

`AMSGOptimConfig` 是一个冻结数据类，包含 13 个字段控制所有优化目标。五个预设构造器支持消融实验：

| 预设 | 置信模型 | 规划器 | 边策略 | 启发式注入 |
|------|---------|--------|--------|-----------|
| `legacy()` | 固定 | dijkstra | 传统 | 开启 |
| `full()` | 贝叶斯 | belief\_a\* | 已验证 | 关闭 |
| `edge_only()` | 固定 | dijkstra | 已验证 | 关闭 |
| `belief_only()` | 贝叶斯 | dijkstra | 传统 | 开启 |
| `planner_only()` | 固定 | belief\_a\* | 传统 | 开启 |

`legacy()` 预设精确复现优化前的行为。`full()` 预设启用所有学术贡献。每个中间预设隔离单一优化目标用于消融实验。

---

## 4. 核心模块

### 4.1 边生命周期管理器（`edge_lifecycle.py`，定义 7-8）

**这是与先前工作的主要差异点。**

先前方法（PG-Agent、AgentRR）将每次观测到的跳转立即写入图中。在移动应用上，这会产生不可靠的图，原因如下：
- 同一操作会因应用状态、登录状态、A/B 测试和网络条件产生不同的结果。
- 一条误报边（例如"点击加入购物车必定打开规格选择"）会导致规划器跳过 VLM 推理，引发级联失败。

AMSG 引入了生命周期状态机：

```python
@dataclass(frozen=True)
class EdgeLifecycleRecord:
    edge_key: str                    # "source_type|intent|target|region|target_type"
    stage: str                       # "hypothesis" | "candidate" | "promoted" | "demoted"
    outcome_counts: dict[str, int]   # {"spec_selection": 5, "login": 2, "popup": 1}
    dominance_ratio: float           # dominant_count / total
    verification_count: int
    risk_level: str

@dataclass(frozen=True)
class OutcomeDistribution:
    outcomes: dict[str, int]         # page_type -> 观测次数

    @property
    def entropy(self) -> float:      # 香农熵（定义 8）
        ...
```

**核心 API**：
- `record_outcome(source_page_type, intent, action_target, observed_target, risk)` --- 记录操作后实际发生的结果。同时更新边生命周期记录和结果分布。
- `requires_vlm_verification(source_page_type, action_key) -> bool` --- 当结果熵超过 VLM 阈值时返回 `True`。替代硬编码的验证集合。
- `get_promotable_edges()` --- 返回满足提升条件（$n \geq k$，支配度 $\geq \theta$，非高风险）的边。

**集成方式**：`SpatialGraphMemory.record_observation()` 在每次真机执行时调用 `_edge_lifecycle.record_outcome()`。`RuntimeController._requires_vlm_verification()` 在信任图边之前查询生命周期管理器。

### 4.2 多信号置信定位器（`belief_localizer.py`，定义 3、7）

移动应用没有 URL 或 DOM 选择器。智能体必须仅凭截图在图中定位自身。先前方法使用固定相似度分数或简单哈希匹配。AMSG 使用贝叶斯置信融合：

```python
class MultiSignalLocalizer:
    def update(self, signals: ObservationSignals, candidates: list[PageState]) -> BeliefDistribution:
        # 对每个候选：后验 = 似然 * 先验
        # 似然 = 4 个通道相似度的加权和
        # 归一化得到概率分布
```

**四个通道**：
1. **视觉**：VLM 截图嵌入的余弦相似度（可选，低端设备不可用）。
2. **语义**：页面内容文本嵌入的余弦相似度（可选）。
3. **结构**：应用名、页面类型、地标、可操作元素的加权匹配（始终可用，封装了传统逻辑）。
4. **时序**：来自跳转历史的频率估计 $P(v \mid v_{\text{prev}}, a_{\text{prev}})$（首次跳转后可用）。

**优雅降级**：当某通道不可用时，其权重按比例重分配给可用通道。在没有嵌入模型的设备上，仅使用结构和时序通道。

**关键输出**：`BeliefDistribution.entropy` 输入规划器的信息增益项。高置信熵意味着智能体对自身位置不确定，此时探索性动作更有价值。

### 4.3 增强规划器（`enhanced_planner.py`，定义 5）

通过统一的 `plan()` 分发的三个可插拔后端：

**后端 1：Dijkstra** --- 精确复现优化前的最短路径算法。仅使用 `edge.weighted_cost`。

**后端 2：A\*** --- 添加模式感知的可容许启发式：
$$h(v) = \min_{\tau \in \text{goals}} d_\Sigma(\tau(v), \tau)$$
其中 $d_\Sigma$ 是领域模式中的 BFS 距离。这加速了稀疏图上的搜索。

**后端 3：Belief-A\*** --- 完整优化。边代价包含：
- **时效衰减**：$s(e) = 1 - \exp(-\Delta t / \text{halflife})$。长时间未遍历的边吸引力降低，迫使规划器优先选择近期验证过的路径。
- **探索奖励**：$\beta / \sqrt{1 + n_{\text{visit}}}$。访问不足的状态获得奖励，鼓励覆盖。
- **信息增益**：$\lambda \cdot H(\mathcal{B}) \cdot s(e)$。当置信熵较高（智能体迷失）时，陈旧的边变得更不吸引，因为它们无法提供定位价值。
- **结果熵惩罚**：$\gamma \cdot H_{\mathcal{O}}(v, a)$。结果不可预测的边受到惩罚，引导规划器走向确定性路径。

### 4.4 运行时控制器（`runtime_controller.py`）

编排完整的定位-规划-执行循环。关键集成点：

- **`_requires_vlm_verification(source_page_type, target_page_type)`**：查询 `EdgeLifecycleManager` 的结果熵。如果熵超过 `outcome_entropy_vlm_threshold`，返回 `True` --- 智能体必须使用 VLM 协同验证跳转后才能信任图边。仅在无生命周期数据时回退到硬编码集合 `VLM_VERIFY_TRANSITIONS`。
- **`locate_and_get_context()`**：完整的编排流水线。先尝试 RuntimeDAG 提示（快速路径），然后执行定位 + 规划 + 充实。
- **`compile_task_dag()`**：编译任务局部的内存 DAG，用于多步执行而无需重复定位。

### 4.5 离线探索器（`offline_explorer.py`）

VLM 驱动的应用探索，支持覆盖引导的任务生成：

- 接受自然语言任务描述（"覆盖核心购物流程：首页 -> 搜索 -> 商品 -> 规格 -> 购物车"）。
- 运行闭环：截图 -> VLM 决定动作 -> 页面分类器验证 -> 记录跳转 -> 执行 -> 重复。
- **双 VLM 架构**：动作模型（如 AutoGLM、UI-TARS）决定做什么；分类器模型（如 Qwen-VL）独立验证结果页面类型。这种交叉验证防止误分类污染图。
- **安全护栏**：阻止针对支付、结算、登录、地址页面的操作。自动从高风险页面退出。
- **覆盖目标**：对照目标集合追踪已发现的页面类型和跳转。覆盖完成时停止探索。
- **主动探索模式**：使用 `EdgeHypothesisGenerator` 和 `ActiveGraphBuilder` 评分前沿假设，建议覆盖价值最高的下一个动作。

### 4.6 SpatialGraphMemory（`spatial_graph_memory.py`）

串联所有 AMSG 模块的中心集成层：

```python
class SpatialGraphMemory:
    def __init__(self, graph_store, config=None):
        self._amsg_config = config or AMSGOptimConfig.legacy()
        self._edge_lifecycle = EdgeLifecycleManager(self._amsg_config)
        self._localizer = MultiSignalLocalizer(self._amsg_config)
        self._enhanced_planner = EnhancedPlanner(self._amsg_config)
```

核心职责：
- **`locate(screen, task)`**：当 `use_multi_signal_belief=True` 时委派给 `MultiSignalLocalizer`。
- **`plan(belief, goal_spec)`**：当 `planner_backend != "dijkstra"` 时委派给 `EnhancedPlanner`。
- **`record_observation(before, action, after)`**：调用 `EdgeLifecycleManager.record_outcome()`。
- **`_load_edges(state_id)`**：遵循 `enable_heuristic_injection` 标志。当为 `False`（论文模式）时，不注入硬编码边。
- **`import_exploration_staging()` + `promote_staging_to_canonical()`**：离线探索导入流水线，包含页面规范化、跳转过滤、搜索复合动作合成和质量报告。

---

## 5. 核心创新（对比先前工作）

### 5.1 带后置条件验证的边生命周期（定义 7）

| 方面 | PG-Agent / AgentRR | AMSG |
|------|-------------------|------|
| 边创建 | 观测一次，立即写入 | 假设阶段；不用于规划 |
| 边提升 | 无 | 需要 $k$ 次后置条件验证 + 支配度 $\geq \theta$ |
| 边降级 | 从不 | 提升后失败率超过 $\delta$ |
| 同一操作多种结果 | 未建模 | `OutcomeDistribution` 追踪每个操作的结果熵 |

### 5.2 数据驱动的 VLM 验证（定义 8）

| 方面 | 先前工作 | AMSG |
|------|---------|------|
| 何时使用 VLM | 始终使用，或硬编码跳转集合 | 结果熵 $H_{\mathcal{O}} > \theta_{\text{vlm}}$ |
| 适应性 | 固定 | 从观测中学习；新跳转自动分类 |
| 开销 | 高（每步都用 VLM）或脆弱（遗漏未列出的跳转） | 最小化：仅在不确定性真实存在时使用 VLM |

### 5.3 多信号置信定位（定义 3、7）

| 方面 | 先前工作 | AMSG |
|------|---------|------|
| 页面识别 | 哈希匹配或固定相似度 | 4 通道贝叶斯融合 |
| 嵌入依赖 | 必需 | 优雅降级；仅结构+时序通道也可工作 |
| 不确定性感知 | 二值（找到/未找到） | 完整的熵度量输入规划器 |

### 5.4 不确定性感知规划（定义 5）

| 方面 | Dijkstra（先前） | Belief-A\*（AMSG） |
|------|-----------------|-------------------|
| 边代价 | 静态权重 | 动态：时效衰减 + 探索奖励 + 熵惩罚 |
| 启发式 | 无 | 模式 BFS 可容许启发式 |
| 信息搜寻 | 无 | $\lambda \cdot H(\mathcal{B}) \cdot u(e)$ 项奖励不确定性降低 |
| 结果不确定性 | 忽略 | $\gamma \cdot H_{\mathcal{O}}$ 惩罚引导远离混沌跳转 |

### 5.5 启发式注入控制

传统系统注入硬编码边（例如"product\_detail -> spec\_selection，通过加入购物车，置信度=0.70"）来弥补稀疏图。AMSG 将此设为开关：
- `enable_heuristic_injection=True`（传统模式）：注入边填补图的空白。
- `enable_heuristic_injection=False`（论文模式）：所有边必须通过真机验证获取。这对"自维护图"的主张至关重要。

---

## 6. 数据流

### 6.1 在线执行

```
用户："搜索无线耳机，把最便宜的加入购物车"
  |
  v
GoalSpec.from_task() --> target_page_types=("spec_selection",), slots={"query": "无线耳机"}
  |
  v
MultiSignalLocalizer.update(screenshot_signals, graph_candidates)
  --> BeliefDistribution(home: 0.85, search_input: 0.10, ...)
  |
  v
EnhancedPlanner.plan(start="state_home", goals={"spec_selection"})
  --> 路径：home -> search_input -> search_result -> product_detail -> spec_selection
  |  （代价考虑了时效衰减、探索奖励、结果熵）
  |
  v
RuntimeDAG 编译完成；next_planned_action() = Tap(search_bar)
  |
  v
ActionHandler.execute(Tap, [500, 100])
  --> 截取屏幕截图
  |
  v
EdgeLifecycleManager.record_outcome(
    source="home", intent="Tap", target="search_bar",
    observed_target="search_input"
)
  --> EdgeLifecycleRecord：hypothesis -> promoted（确定性跳转）
  --> OutcomeDistribution：{"search_input": 1}，entropy=0.0
  |
  v
（循环继续直到达成目标或达到 max_steps）
```

### 6.2 离线探索

```
CLI: python -m phone_agent.memory.offline_explorer --app Taobao --task "覆盖 home->search->product->spec->cart"
  |
  v
OfflineExplorer.explore()
  |
  +--> 启动应用
  +--> 循环（max_steps）：
  |      截图 -> 动作 VLM 决定动作 -> PageClassifier 验证页面类型
  |      -> 记录跳转（带拒绝过滤器）-> 执行动作
  |      -> 检查覆盖目标
  |
  +--> 保存结果：pages.json + transitions.json + trajectory.json
  |
  +--> （如果 --auto-import-graph）：
         SpatialGraphMemory.import_exploration_staging()
           --> 页面规范化（语义去重）
           --> 跳转过滤（安全性、合理性）
           --> 搜索复合动作合成
         SpatialGraphMemory.promote_staging_to_canonical()
           --> 与现有图合并
           --> 持久化到 Neo4j
```

---

## 7. 测试覆盖

| 模块 | 测试数 | 测试文件 |
|------|--------|---------|
| 边生命周期（定义 7、8） | 10 | `tests/test_edge_lifecycle.py` |
| 置信定位器（定义 3、7） | 13 | `tests/test_belief_localizer.py` |
| 增强规划器（定义 5） | 11 | `tests/test_enhanced_planner.py` |
| 角色分类器 | 8 | `tests/test_role_classifier.py` |
| SpatialGraphMemory | 32+ | `tests/test_spatial_graph_memory.py` |
| AMSG 集成 | 20+ | `tests/test_amsg_spatial.py` |
| V4 功能 | 10+ | `tests/test_amsg_v4_functionality.py` |
| GraphStore V4 运行时 | 1 | `tests/test_graph_store_v4_runtime.py` |

所有测试在 `AMSGOptimConfig.legacy()`（回归）和 `AMSGOptimConfig.full()`（优化）下均通过。

---

## 8. 环境与依赖

| 组件 | 技术 |
|------|------|
| 图数据库 | Neo4j（bolt 协议，可选） |
| 向量索引 | FAISS（embedding-3，内存 2048 维，任务索引 1536 维） |
| VLM 适配器 | AutoGLM、UI-TARS、Qwen-VL、MAI-UI、GUI-Owl |
| 设备后端 | ADB（Android）、HDC（HarmonyOS）、XCTest（iOS） |
| 聊天网关 | nanobot（12+ 通道集成） |
| 语言 | Python 3.11+，冻结数据类，Protocol 类型 |
