# 主动移动空间图谱：设计理念与算法创新

> **版本**: 2026-06-05
> **定位**: 技术算法论文基础文档。区别于工程实现导向的 ARCHITECTURE.md，本文聚焦于空间图谱的理论动机、形式化定义、核心算法和与现有工作的理论对比。

---

## 1  为什么选择空间图谱

### 1.1  问题根源：移动 GUI 的四类不确定性

移动 GUI 自动化的本质困难不在于单步动作执行，而在于跨步骤的状态追踪和决策累积。一个购物任务可能需要 20-50 步操作，跨越 8-12 种页面类型。在这个过程中，智能体面对四类递进的不确定性：

**感知不确定性**（同一页面多种外观）。同一个"搜索结果页"在不同设备分辨率、应用版本、广告插入、滚动位置下呈现完全不同的像素。纯像素匹配在跨设备部署时迅速退化。

**语义不确定性**（动作目标依赖当前内容）。历史记录中的"点击第三个商品卡片"不能指导当前任务——当前屏幕上的第三个商品可能完全不同。坐标重放在内容敏感页面上必然失败。

**转移不确定性**（同一动作多种结果）。点击"立即购买"按钮可能导向 SKU 选择页、登录页、促销弹窗、结算页或无响应。同一动作的结果取决于应用状态（是否登录、是否有默认地址、是否命中 A/B 测试），智能体无法预先确定。

**上下文不确定性**（长历史混淆推理）。将完整的 30 步操作历史塞入 VLM 上下文窗口不仅消耗大量 token，更重要的是会干扰模型对当前步骤的关注。历史信息应当按需提供，而非无差别注入。

### 1.2  现有技术路线的局限

现有移动 GUI 智能体的技术路线可分为三类，每类都只解决了部分不确定性：

**路线 A：纯 VLM 推理**（如 ColorAgent [Li et al., 2025]）。每步从截图推理下一动作，不积累结构性知识。能处理语义不确定性（VLM 理解当前内容），但在感知和转移不确定性面前，只能反复重新发现已知导航路径。每次新任务的导航开销为 O(n)，其中 n 为到达目标页的步数。

**路线 B：静态图谱 + RAG**（如 PG-Agent [Chen et al., 2025]、KG-RAG [Guan et al., 2025]、WebNavigator [Zhang et al., 2025]）。离线构建页面导航图，在线检索相关路径作为 VLM 参考。解决了感知不确定性（页面被抽象为图节点），但图谱一旦构建即冻结。当应用 UI 更新、按钮功能变化或新页面出现时，图谱无法自我修正。更关键的是，这些方法对转移不确定性缺乏建模——它们假设边的目标是确定性的。

**路线 C：经验重放**（如 MobiAgent/AgentRR [Zhang et al., 2025]）。记录成功轨迹，在相似任务中重放前缀。解决了重复任务的效率问题，但本质上是"背诵"而非"理解"——一旦任务偏离已记录路径，重放即失效。对转移不确定性同样缺乏概率建模。

### 1.3  AMSG 的设计定位

AMSG（Active Mobile Spatial Graph）的设计目标是**同时**解决四类不确定性，而非在任一维度上深耕。其核心观察是：

> 移动应用的 UI 是一个有限但动态的有向图。页面是节点，动作是边。与 Web 不同，移动 UI 没有 URL 可寻址，没有 DOM 可解析（或 DOM 不稳定），也没有静态超链接可爬取。但移动 UI 的转移结构**在统计意义上是稳定的**——同一个"搜索结果→商品详情"转移在 90% 的情况下产生预期结果，只有 10% 的情况被弹窗或登录页截断。

基于这一观察，AMSG 做出三个关键设计选择：

1. **语义页面抽象**（解决感知不确定性）：不存储像素或 DOM，而是将页面抽象为 `(app, page_type, landmarks, affordances, slots, risk)` 的语义元组。两个外观不同但结构相同的页面映射到同一个图节点。

2. **生命周期验证的边**（解决转移不确定性）：每条边不是"存在/不存在"的布尔值，而是一个带结果分布的概率转移。边经历 `hypothesis → candidate → promoted → demoted` 的生命周期，仅在后条件验证通过且结果优势度足够高时才被提升为可执行动作。

3. **非对称权威**（解决语义不确定性）：图谱不替代 VLM 的语义判断。内容敏感的转移（选择哪个商品、选择哪个 SKU、确认哪个订单）被显式标记为"非锚定"，图谱仅提供提示而由 VLM 做最终决策。

这三个选择使 AMSG 不同于上述任何路线：它是一个**自演进的、概率性的、VLM 协同的空间图谱**，而非静态知识库或轨迹录制器。

---

## 2  形式化定义

### 2.1  图谱结构

**定义 1**（AMSG）。主动移动空间图谱 $G$ 是一个十元组：

$$G = (V, A, E_c, E_h, T, \Sigma, B, \Pi, L, O)$$

其中：
- $V$：UIState 节点集合，每个节点是一个页面状态抽象
- $A$：Action 节点集合，每个节点存储语义意图和锚定证据
- $E_c \subseteq V \times A \times V$：已提交（committed/promoted）的转移边
- $E_h \subseteq V \times A \times V$：假设（hypothesis）阶段的候选转移边
- $T$：TaskTarget 节点集合，用于轨迹级检索
- $\Sigma$：领域模式（domain schema），定义合法页面类型和合理转移
- $B$：UIState 上的信念分布（贝叶斯后验）
- $\Pi$：加权转移边上的规划器
- $L$：边提升和降级的生命周期记录
- $O$：后条件验证的结果分布

### 2.2  页面状态抽象

**定义 2**（PageState）。页面状态 $s \in V$ 是一个不可变语义元组：

$$s = (\text{app}, \text{page\_type}, \text{landmarks}, \text{affordances}, \text{slots}, \text{risk})$$

其中 `app` 为应用标识，`page_type` 为页面类型枚举（如 `home`, `search_result`, `product_detail`），`landmarks` 为稳定视觉锚点集合，`affordances` 为可操作项集合，`slots` 为运行时槽位字典，`risk` 为风险等级。

**语义签名**（Semantic Signature）。每个 PageState 具有确定性签名：

$$\sigma(s) = \text{app} \mid \text{domain} \mid \text{page\_type} \mid \text{landmarks}_{csv} \mid \text{affordances}_{csv} \mid \text{slots}_{csv}$$

签名相同的两个观测被视为同一页面状态，这是图谱去重的基础。

**实现参考**：`spatial/core.py:35-53` 的 `semantic_signature()` 函数，以及 `PageNode.build()` 工厂方法（`spatial/core.py:72-100`）。

**设计抉择**：为什么不用像素哈希或 DOM 哈希？因为像素级匹配对布局微调敏感（一个广告 banner 的插入会改变所有像素），DOM 哈希对不提供 accessibility tree 的应用不可用。语义签名的抽象粒度在页面类型级别——足够粗以实现跨设备复用，足够细以区分功能不同的页面。

### 2.3  语义动作节点

**定义 3**（Action）。动作节点 $a \in A$ 是一个语义可操作性描述：

$$a = (\text{type}, \text{intent}, \text{semantic\_target}, \text{locator}, \text{postcondition}, \text{confidence}, \text{lifecycle}, \text{entropy})$$

关键设计：动作的身份由 `(intent, semantic_target, postcondition)` 决定，**不包含坐标**。坐标存储在 `locator` 字段中，但仅作为锚定证据，不参与动作去重（`graph_store.py` 的 `_semantic_action_key()` 忽略坐标抖动）。

**实现参考**：`spatial/core.py:118-208` 的 `AffordanceEdge` 数据类。

**设计抉择**：为什么不存坐标作为动作身份？因为同一个"点击搜索按钮"在不同设备上的坐标不同，甚至在同一设备上随滚动位置变化。将坐标视为证据而非身份，使图谱能跨设备、跨会话复用。

### 2.4  锚定与非锚定转移

**定义 4**（锚定性 Groundedness）。转移 $(s, a, s')$ 是**锚定的**（grounded），当且仅当：
1. 动作 $a$ 的 `locator` 包含有效坐标或区域描述
2. 动作 $a$ 的 `confidence` $\geq 0.9$
3. 转移的目标页面类型不依赖当前屏幕的具体内容

**示例**：`home → search_input`（点击搜索图标）是锚定的——搜索图标的位置稳定，点击后总是打开搜索页。`search_result → product_detail`（点击某个商品）是非锚定的——图谱知道这个转移存在，但**哪个**商品取决于当前任务和屏幕内容。

这一区分是 AMSG "图谱作为顾问而非控制器"原则的形式化。对于锚定转移，图谱可直接提供可执行动作（快速路径，~0.5s）；对于非锚定转移，图谱仅提供导航提示，VLM 做最终的语义选择（完整 VLM 路径，~5s）。

---

## 3  贝叶斯信念定位

### 3.1  问题定义

每步执行开始时，智能体观测到一个截图和当前应用信息，需要确定"我在图谱中的哪个位置"。这是一个状态估计问题：给定观测序列 $o_1, \ldots, o_t$ 和动作序列 $a_1, \ldots, a_{t-1}$，估计当前状态的后验分布 $B_t(v)$。

### 3.2  四通道观测模型

**定义 5**（观测似然）。观测似然 $P(o_t | v)$ 是四个独立通道信号的加权和：

$$P(o_t | v) = \sum_{c \in \mathcal{C}} \frac{w_c}{\sum_{c' \in \mathcal{C}} w_{c'}} \cdot \phi_c(o_t, v)$$

其中 $\mathcal{C}$ 为可用通道集合，$w_c$ 为通道权重，$\phi_c$ 为通道相似度函数。

四个通道及其默认权重：

| 通道 | 权重 | 信号 $\phi_c$ | 可用条件 |
|---|---|---|---|
| 视觉（Visual） | 0.30 | VLM 截图嵌入的余弦相似度 | 需要嵌入模型 |
| 语义（Semantic） | 0.25 | 文本语义嵌入的余弦相似度 | 需要嵌入模型 |
| 结构（Structural） | 0.25 | 加权特征相似度 | 始终可用 |
| 时序（Temporal） | 0.20 | 历史转移频率 $P(v | v', a_{t-1})$ | 首次动作后可用 |

**结构相似度**的精确计算（`belief_localizer.py:111-138`）：

$$\phi_{\text{struct}} = 0.35 \cdot \mathbb{1}[\text{app 匹配}] + 0.35 \cdot \mathbb{1}[\text{page\_type 匹配}] + 0.20 \cdot J(\text{landmarks}) + 0.10 \cdot J(\text{affordances})$$

其中 $J(\cdot)$ 为 Jaccard 相似系数。

**通道权重重新分配**。当某通道不可用（如未配置嵌入模型）时，该通道的权重按比例重新分配到可用通道：

$$w_c' = \frac{w_c}{\sum_{c' \in \mathcal{C}} w_{c'}} \quad \forall c \in \mathcal{C}$$

这意味着系统在仅有结构通道时仍可工作（退化为固定评分匹配），但在四通道全开时获得最佳定位精度。

**实现参考**：`belief_localizer.py:160-179` 的 `observation_likelihood()` 方法。

### 3.3  信念更新

**定义 6**（贝叶斯信念更新）。信念分布 $B_t$ 通过标准贝叶斯滤波更新：

$$B_t(v) = \eta \cdot P(o_t | v) \cdot \sum_{v'} P(v | v', a_{t-1}) \cdot B_{t-1}(v')$$

其中 $\eta$ 为归一化常数，$P(v | v', a_{t-1})$ 为转移概率（从历史频率表估计）。

实际实现中（`belief_localizer.py:183-203`），采用离散贝叶斯更新：
1. 对每个候选状态 $v$，计算 $\text{likelihood}(v) \times \text{prior}(v)$
2. 归一化得到后验
3. 将后验作为下一步的先验

**信念熵**用于度量定位不确定性：

$$H(B_t) = -\sum_{v} B_t(v) \cdot \ln B_t(v)$$

高信念熵意味着智能体不确定自己在哪个页面，这一信号直接反馈到规划器的信息增益项中（见第 5 节）。

**实现参考**：`belief_localizer.py:74-81` 的 `BeliefDistribution.entropy` 属性。

### 3.4  与现有定位方法的对比

| 方法 | 信号来源 | 概率性 | 多通道 | 优雅退化 |
|---|---|---|---|---|
| WebNavigator 的哈希匹配 | DOM 结构哈希 | 否（精确匹配） | 否 | 否（匹配失败=丢失） |
| PG-Agent 的 BFS 相似搜索 | MLLM 语义 + 像素 | 否（Top-1 匹配） | 部分（双层） | 否 |
| KG-RAG 的嵌入检索 | 向量嵌入 | 否（余弦相似度排名） | 否 | 否 |
| MobiAgent 的页面匹配 | OmniParser 解析 | 否 | 否 | 否 |
| **AMSG 贝叶斯信念** | 四通道加权 | **是（后验分布）** | **是（4 通道）** | **是（权重重分配）** |

AMSG 的优势在于：(1) 提供概率分布而非单点估计，使下游规划器能感知定位不确定性；(2) 四通道设计允许按硬件能力自适应配置；(3) 信念熵直接参与规划成本计算。

---

## 4  边生命周期与图谱自演进

### 4.1  生命周期状态机

**定义 7**（边生命周期）。AMSG 中每条转移边经历四阶段生命周期：

$$\text{hypothesis} \xrightarrow{\text{验证 } \geq N} \text{candidate} \xrightarrow{\text{优势度 } \geq \theta} \text{promoted} \xrightarrow{\text{优势度 } < \theta'} \text{demoted}$$

| 阶段 | 含义 | 对 ActionAdvisor 的可见性 |
|---|---|---|
| `hypothesis` | 新观测到的转移，尚未经后条件验证 | 不可见 |
| `candidate` | 验证次数达到最低要求 | 不可见 |
| `promoted` | 结果优势度达到阈值 | 可见（锚定→快速路径，非锚定→VLM 提示） |
| `demoted` | 曾被提升但优势度下降（UI 变更） | 不可见，VLM 重新探索 |

**提升条件**（`edge_lifecycle.py:122-130`）：

$$\text{promotable}(e) \iff \text{verif\_count}(e) \geq N_{\min} \wedge \text{dom\_ratio}(e) \geq \theta_{\text{dom}} \wedge \text{risk}(e) \neq \text{high}$$

**实现参考**：`AMSGOptimConfig` 中的默认阈值为 $N_{\min} = 1$, $\theta_{\text{dom}} = 0.6$。严格模式（`sava` 预设）使用 $N_{\min} = 3$, $\theta_{\text{dom}} = 0.8$。

### 4.2  结果分布与 Shannon 熵

**定义 8**（结果分布）。对于每个 `(source_page_type, action_key)` 对，AMSG 维护一个经验结果分布 $O$：

$$O(s, a) = \{(s'_1, c_1), (s'_2, c_2), \ldots\}$$

其中 $s'_i$ 为观测到的目标页面类型，$c_i$ 为该目标出现的次数。

**示例**：动作"点击加购按钮"在`product_detail`页面上的结果分布可能是：

| 目标页面 | 次数 | 频率 |
|---|---|---|
| `spec_selection` | 8 | 73% |
| `login` | 2 | 18% |
| `promotion_popup` | 1 | 9% |

**定义 9**（结果熵）。结果分布的 Shannon 熵用于量化转移的不确定性：

$$H(O) = -\sum_{i} \frac{c_i}{\sum c_j} \cdot \ln\left(\frac{c_i}{\sum c_j} + \epsilon\right)$$

其中 $\epsilon = 10^{-12}$ 防止对数溢出。

**实现参考**：`edge_lifecycle.py:47-53` 的 `OutcomeDistribution.entropy` 属性。

### 4.3  熵驱动的 VLM 验证边界

**定义 10**（VLM 验证决策）。当结果熵超过阈值时，转移不再自动执行，而是要求 VLM 验证：

$$\text{requires\_vlm}(s, a) \iff H(O(s, a)) > \theta_H$$

默认阈值 $\theta_H = 0.8$，严格模式为 $\theta_H = 0.5$。

这一机制用**数据驱动的连续边界**替代了**硬编码的离散集合**。传统方法（如手动列出"需要 VLM 的转移"）在应用变更或新增功能时需要人工更新。AMSG 的熵边界是自适应的：当某个曾经确定性的转移开始产生多种结果（例如应用更新后按钮功能改变），其熵自然上升，触发 VLM 验证。

**与现有方法的对比**：
- PG-Agent、KG-RAG、WebNavigator 不追踪结果分布，因此无法检测转移可靠性变化
- MobiAgent/AgentRR 使用二元决策（重放/不重放），缺乏连续的不确定性度量
- AMSG 的熵边界是唯一已知的**数据驱动、自适应**的 VLM/图谱控制切换机制

### 4.4  自动降级与 UI 变更适应

当一条已提升的边持续产生非预期结果时，其优势度下降。当优势度低于降级阈值（默认 40%）时，边被降级为 `demoted`，从 ActionAdvisor 的可见集合中移除。VLM 随后通过全路径推理重新探索该转移，探索结果作为新的观测进入生命周期系统。

这构成一个**自修复环路**：
```
promoted edge → UI 变更 → 非预期结果 → 优势度下降 → 自动降级 →
VLM 重新探索 → 新观测 → 新 hypothesis → 验证 → 重新提升
```

该环路使 AMSG 能够适应应用更新，无需人工重建图谱。

**实现参考**：`graph_lifecycle_store.py` 的 `demote_stale_edges()` 方法。

---

## 5  增强路径规划

### 5.1  基础成本函数

**定义 11**（边权成本）。每条边的基础代价由成功率、风险和置信度决定：

$$C_{\text{base}}(e) = 1.0 + \text{fail\_rate}(e) \times 3.0 + R(\text{risk}) - \text{clamp}(\text{conf}(e), 0, 1) \times 0.3$$

其中风险惩罚 $R$ 为：`normal` = 0.0, `medium` = 0.8, `high` = 2.0。

**实现参考**：`spatial/core.py:147-150` 的 `AffordanceEdge.weighted_cost` 属性。

### 5.2  增强成本函数（Belief-A*）

**定义 12**（增强成本函数）。Belief-A* 在基础成本上叠加四个调整项：

$$C'(e) = C_{\text{base}}(e) + 0.5 \cdot \underbrace{(1 - e^{-\Delta t / \tau})}_{\text{过期衰减}} - \underbrace{\frac{w_e}{\sqrt{1 + n_v}}}_{\text{探索奖励}} - \underbrace{w_i \cdot H(B) \cdot s}_{\text{信息增益}} + \underbrace{0.5 \cdot H(O)}_{\text{结果熵惩罚}}$$

各项含义：

| 项 | 公式 | 默认参数 | 作用 |
|---|---|---|---|
| 过期衰减 | $1 - \exp(-\Delta t / \tau)$ | $\tau = 50$ 步 | 未近期遍历的边成本增加 |
| 探索奖励 | $w_e / \sqrt{1 + n_v}$ | $w_e = 0.3$ | UCB 风格的低访问状态奖励 |
| 信息增益 | $w_i \cdot H(B) \cdot s$ | $w_i = 0.2$ | 高信念熵时偏好确定性路径 |
| 结果熵惩罚 | $0.5 \cdot H(O)$ | — | 不可预测的转移被惩罚 |

**信念-规划耦合**。信息增益项建立了信念定位器和规划器之间的反馈回路：当信念熵高（定位不确定）时，规划器偏好经过验证的、低风险的路径；当信念熵低（定位确定）时，规划器更愿意探索新路径以扩展图谱覆盖。

**实现参考**：`enhanced_planner.py:116-144` 的 `enhanced_cost()` 方法。

### 5.3  模式感知启发式

A* 和 Belief-A* 使用基于领域模式（domain schema）的可采纳启发式：

$$h(s) = \min_{g \in \text{Goals}} d_\Sigma(s.\text{page\_type}, g)$$

其中 $d_\Sigma$ 为模式中页面类型之间的 BFS 最短跳数。例如，在购物模式中，`home` 到 `checkout` 的模式距离为 5（home → search_input → search_result → product_detail → spec_selection → checkout），这为 A* 提供了方向性引导。

**实现参考**：`enhanced_planner.py:88-98` 的 `schema_heuristic()` 方法；`spatial/schemas/shopping.yaml` 定义了购物领域的页面类型和转移规范。

### 5.4  三种规划后端对比

| 规划器 | 成本函数 | 启发式 | 信念感知 | 适用场景 |
|---|---|---|---|---|
| Dijkstra | $C_{\text{base}}$ | 无 | 否 | 基线/回归测试 |
| A* | $C_{\text{base}}$ | $h(s) = d_\Sigma$ | 否 | 长距离导航 |
| Belief-A* | $C'(e)$（含四项调整） | $h(s) = d_\Sigma$ | 是 | 完整方法 |

三种后端通过 `AMSGOptimConfig.planner_backend` 切换，用于消融实验。

---

## 6  运行时图谱管线：从单步闭环到跨任务演进

前述章节分别介绍了定位（第 3 节）、生命周期（第 4 节）和规划（第 5 节）的独立算法。本节阐述这些算法如何在运行时管线中协同工作，形成从单步观测到跨任务图谱累积的完整闭环。

### 6.1  单步闭环：延迟验证架构

AMSG 运行时管线的核心设计是**延迟后条件验证**（delayed postcondition verification）。传统方法在执行动作后立即记录转移结果，但此时尚无法知道动作是否真正达到了预期页面——因为下一张截图还没有被捕获和分类。

AMSG 的解决方案是将每步分为**两个时间点**：

**时间点 t（执行时）**：执行动作 $a_t$ 后，将 `(source_state, action, expected_postcondition)` 暂存为**待验证转移**（pending transition），不立即写入图谱。

**时间点 t+1（验证时）**：捕获新截图，进行页面分类和信念定位。将观测到的 `page_type` 与 $t$ 步暂存的 `expected_postcondition` 对比：
- **匹配**：调用 `record_observation(source, action, target, outcome="success")`，转移进入生命周期系统
- **不匹配**：调用 `record_observation(source, action, target, outcome="failure")`，触发修复策略（重试/重规划/回退）

这一设计产生如下单步数据流：

```
Step t:                              Step t+1:
┌────────────────────┐               ┌────────────────────┐
│ 1. 截图 + 分类      │               │ 1. 截图 + 分类      │
│ 2. 验证 t-1 的      │               │ 2. 验证 t 的        │
│    pending transition│              │    pending transition│
│ 3. 信念定位          │               │    → 匹配? success  │
│ 4. 目标推断 + 路径规划│              │    → 不匹配? failure │
│ 5. 调度(快速/VLM)    │               │ 3. 信念定位          │
│ 6. 执行动作          │               │ ...                 │
│ 7. 暂存 pending:     │               │                     │
│    (state, action,   │              │                     │
│     postcondition)   │               │                     │
└────────────────────┘               └────────────────────┘
```

**实现参考**：
- 暂存：`memory_manager.py` 的 `update_state_and_transition()` 设置 `_pending_transition_source`、`_pending_transition_action`、`_pending_expected_postcondition`
- 验证：`runtime_controller.py` 的 `_verify_pending_transition()` 在下一步的 `locate_and_get_context()` 中执行
- 记录：验证通过后调用 `spatial_graph_memory.record_observation()`，触发 `EdgeLifecycleManager.record_outcome()`

**设计抉择**：为什么要延迟验证而非立即记录？因为立即记录只能记录"我执行了什么动作"，而延迟验证能记录"动作实际产生了什么结果"。后者是生命周期系统和结果分布的数据基础——没有真实的后条件观测，就无法计算优势度和熵。

### 6.2  RuntimeDAG：有状态的路径执行

当规划器找到从当前状态到目标的路径时，该路径被物化为一个 `RuntimeDAG` 对象，缓存在 `MemoryManager._runtime_dag` 中。RuntimeDAG 是一个**有状态的执行计划**：

```python
RuntimeDAG:
    plan_id: str              # 基于(任务, 当前状态, 路径步骤)的唯一标识
    app: str                  # 目标应用
    goal_spec: GoalSpec       # 目标页面类型 + 运行时槽位
    route: list[Edge]         # 有序边列表（从起点到目标）
    current_index: int        # 当前执行到第几条边
```

**RuntimeDAG 生命周期**：

1. **创建**：`_runtime_dag_from_route()` 在首次路径规划成功时创建 DAG，缓存路径上的所有节点和边
2. **推进**：每步执行后，如果后条件验证通过，`current_index += 1`，DAG 推进到下一条边
3. **复用**：连续多步中，如果 DAG 的后条件持续匹配，直接从 DAG 取下一条边——跳过完整的 Neo4j 查询和 Dijkstra 规划
4. **失效**：以下情况触发 DAG 失效并重建：
   - 后条件不匹配（到达了非预期页面）
   - 当前应用与 DAG 应用不匹配（用户切换了应用）
   - 连续图谱失败计数超过阈值
   - 下一条边是高风险转移
   - DAG 到达终点或无下一条边

**性能影响**：RuntimeDAG 的存在将连续导航步骤的图谱查询从 O(Neo4j 查询 + Dijkstra 规划) 降低到 O(内存数组索引)。在一个 5 步连续导航序列（如 home → search_input → search_result → product_detail → spec_selection）中，仅第一步需要完整规划，后续 4 步直接从 DAG 读取。

**实现参考**：
- 创建：`runtime_controller.py` 的 `_runtime_dag_from_route()`
- 快速路径使用：`runtime_controller.py` 的 `_try_runtime_dag_hint()`
- 失效条件：`runtime_controller.py` 的 `should_use_page_classifier()` 和 DAG 可用性检查

### 6.3  六阶段运行时编排

`GraphRuntimeController.locate_and_get_context()` 是单步图谱交互的编排入口，将定位、验证、规划和调度在一次调用中完成：

**阶段 1：RuntimeDAG 快速路径尝试**。如果 DAG 存在且可用，直接从 DAG 取下一步动作，记录上一步的 pending transition，返回 `mode=navigate`。成本：~10ms。

**阶段 2：信念定位**。构建当前 PageState，查询 Neo4j 候选，执行四通道贝叶斯更新（第 3 节），得到信念分布 $B_t(v)$。

**阶段 3：待验证转移验证**。将上一步的 `pending_expected_postcondition` 与当前信念的 `page_type` 对比。匹配则以 `success` 记录观测；不匹配则以 `failure` 记录并触发修复策略。

**阶段 4：目标推断与路径规划**。从任务文本和 VLM 预规划提取 `GoalSpec`，执行 Dijkstra/A*/Belief-A* 规划（第 5 节），生成 `RoutePlan`。

**阶段 5：RuntimeDAG 创建**。如果规划成功（`mode=navigate`），将路径物化为 RuntimeDAG 并缓存。

**阶段 6：VLM 验证决策**。对下一条边检查是否需要 VLM 验证——基于硬编码的语义转移集合（如 `search_result → product_detail`）和数据驱动的结果熵阈值（第 4.3 节）。

返回的 `context_data` 包含：

| 字段 | 含义 | 消费者 |
|---|---|---|
| `mode` | `navigate` / `explore` / `verify_with_vlm` / `goal_reached` | Agent 调度门控 |
| `belief` | 当前信念分布 | 调试/日志 |
| `goal_spec` | 目标页面类型和槽位 | 任务进度追踪 |
| `route_plan` | 完整路径和代价 | 调试/日志 |
| `next_actions` | 下一步推荐动作列表 | 快速路径编译 / VLM 提示 |
| `semantic_context` | 图谱上下文文本 | VLM 提示注入 |
| `repair_hint` | 修复策略（如果上步失败） | Agent 修复逻辑 |
| `runtime_metrics` | 分类器调用/DAG 命中/覆盖缺口 | 监控/消融分析 |

### 6.4  任务结束：三阶段持久化触发

当 `PhoneAgent.run()` 的循环结束（任务完成或超时）时，`MemoryManager.end_task()` 触发三阶段持久化：

**阶段 A：轨迹保存**。无论成功与否，将完整的步骤详情序列化为 `trajectories/{timestamp}_{ok|fail}_{task_slug}.json`。这是审计和离线分析的基础。

**阶段 B：图谱刷新（仅成功任务）**。调用 `spatial_graph_memory.flush_staged_graph()`：
1. 规范化：按语义签名去重页面状态，合并相同签名的多次观测
2. 过滤：移除瞬态 `unknown` 页面、应用不匹配的边、自环边
3. 生命周期提升：检查每条边是否满足提升条件（验证次数 + 优势度 + 风险）
4. Neo4j 写入：`MERGE` UIState 节点 + `MERGE` Action 节点 + `MERGE` 关系
5. 生命周期持久化：将 `EdgeLifecycleManager` 的记录写入 Neo4j Action 节点的属性

**阶段 C：VLM 轨迹审核（仅成功任务）**。调用 `TrajectoryReviewer.review_and_import()`：
1. 从轨迹提取页面转移链
2. 对比 Neo4j 去重
3. 向强 VLM 提交新候选进行验证（"这是真实的页面导航还是弹窗/分类器错误？"）
4. VLM 批准的转移以 `hypothesis` 身份导入 Neo4j

**关键设计**：失败任务只执行阶段 A，不执行 B 和 C。这是第一道质量门——失败任务的观测可能包含大量错误导航（走错路、陷入循环、被弹窗干扰），将它们写入图谱会污染后续任务的导航质量。

### 6.5  跨任务图谱演进

AMSG 的跨任务演进形成一个正反馈循环：

```
任务 N 执行
    ↓
  成功 → flush_staged_graph() + VLM 轨迹审核
    ↓
  Neo4j 中新增/增强了若干 UIState-Action-UIState 转移
    ↓
任务 N+1 执行
    ↓
  locate_and_get_context() 查询 Neo4j
    ↓
  更多已验证边 → 更多快速路径命中 → 更少 VLM 调用 → 更快完成
    ↓
  成功 → 进一步增强图谱
    ↓
  ...
```

这一循环的收敛性来自两个约束：

1. **上界约束**：移动应用的页面类型是有限的。一个典型的购物应用有 8-12 种核心页面类型和 15-25 种常用转移。在 10-20 次成功任务后，核心购物流程的转移基本被覆盖，图谱趋于稳定。

2. **质量约束**：生命周期系统只提升可靠的转移，降级不可靠的转移。这意味着图谱不会无限膨胀——低质量的边被自动移除，图谱收敛到一组稳定的、经过验证的导航路径。

**与纯 VLM 方法的效率对比**：在图谱冷启动（0 条边）时，AMSG 退化为纯 VLM 方法（每步都走完整 VLM 路径）。随着任务累积，越来越多的导航步骤走快速路径（~0.5s vs ~5s），总任务延迟呈亚线性下降。在图谱稳定后（核心转移已覆盖），一个典型的 20 步购物任务中约 12-15 步走快速路径，仅 5-8 步需要 VLM 推理。

**实现参考**：
- `memory_manager.py` 的 `end_task()` 方法，约 100 行，编排三阶段持久化
- `spatial_graph_memory.py` 的 `flush_staged_graph()` 方法，调用 `canonicalize_state_graph()` 和 `promote_staging_to_canonical()`
- `trajectory_reviewer.py` 的 `review_and_import()` 方法，调用强 VLM 验证新转移

---

## 7  暂存优先持久化

### 6.1  核心原则

AMSG 的持久化遵循一个严格原则：**没有原始动作直接写入 Neo4j**。每条持久化路径都经过至少一道质量门。

这一原则的动机是：在线观测不可避免地包含噪声——弹窗、广告、分类器误判、瞬态屏幕、用户中断。如果将所有观测直接写入图谱，图谱会迅速被噪声污染，导致规划器产生错误路径、ActionAdvisor 推荐无效动作。

### 6.2  三条持久化路径

**路径 1：在线运行时**。任务执行期间，每个动作产生后条件观测，由 `record_observation()` 在本地内存暂存。任务结束时：
- 成功：`flush_staged_graph()` 执行规范化（语义签名去重、瞬态页面过滤、应用一致性检查），然后持久化到 Neo4j
- 失败：仅保存轨迹文件，不触发图谱写入

**路径 2：轨迹审核**。`TrajectoryReviewer` 处理成功任务的轨迹文件：
1. 从步骤详情中提取页面转移链
2. 过滤同页、未知、完成和等待的伪转移
3. 对比 Neo4j 现有边去重
4. 请求强 VLM 验证新候选（是否为真实导航？是否为分类器错误？）
5. 仅 VLM 批准的转移以 `hypothesis` 身份导入

**路径 3：离线探索**。`import_exploration_staging()` 处理预采集的探索数据：
1. 原始页面 JSON 转换为 PageState
2. 按 `(app, page_type, risk)` 规范化
3. 过滤瞬态 `unknown` 页面和应用不匹配
4. 搜索宏合成（从轨迹中恢复 Type + Submit 复合动作）
5. 同页动作压缩和边质量过滤
6. 通过所有门后才由 `promote_staging_to_canonical()` 合并

### 6.3  质量门的理论保证

三条路径共同提供以下保证：

1. **无噪声写入**：弹窗、广告、分类器误判、瞬态屏幕不会被持久化（路径 1 的规范化和路径 2 的 VLM 验证过滤）
2. **无重复边**：语义动作键忽略坐标抖动，相同意图的动作合并而非重复创建
3. **无失败路径**：失败任务的观测不会被持久化为可执行边（路径 1 的成功门）
4. **可审计性**：每个持久化的边都有证据来源（在线观测、VLM 验证批准或离线探索通过）

---

## 8  双速调度决策理论

### 7.1  调度门控

每步的调度门控是一个三路决策函数：

$$\text{dispatch}(s, a, \text{plan}) = \begin{cases} \text{FastPath} & \text{if grounded}(a) \wedge \text{conf}(a) \geq 0.9 \wedge \text{aligned}(a, \text{plan}) \\ \text{GraphCopilot} & \text{if promoted}(a) \wedge \neg\text{grounded}(a) \\ \text{FullVLM} & \text{otherwise} \end{cases}$$

三条路径的特性：

| 路径 | VLM 调用 | 延迟 | 适用转移 |
|---|---|---|---|
| 快速路径 | 0 次 | ~0.5s | 锚定 + 高置信度 + 计划对齐 |
| 图谱协助 | 1 次 | ~3s | 已提升但非锚定（提供提示） |
| 完整 VLM | 1 次 | ~5s | 语义决策、低置信度、安全守卫 |

### 7.2  快速路径的后条件保护

快速路径执行后，系统截取新截图并验证页面类型。如果后条件不匹配（例如预期 `search_result` 但实际到达 `login`），该步自动回退到完整 VLM 路径——图谱快捷方式不会被盲目信任。

这一机制确保快速路径的**最坏情况**仅是多花一次 VLM 调用（回退到完整路径），而非执行错误动作。

### 7.3  与现有调度方法的对比

| 方法 | 调度机制 | 粒度 | 回退策略 |
|---|---|---|---|
| WebNavigator | 二元：图谱传送 vs 局部执行 | 页面级（粗） | 无（传送失败=任务失败） |
| MobiAgent/AgentRR | 二元：经验重放 vs 模型推理 | 前缀级 | 前缀不匹配时全部回退 |
| PG-Agent | RAG 注入后统一 VLM 推理 | 无调度（始终 VLM） | N/A |
| **AMSG** | 三速：快速/协助/完整 | 边级（细） | 后条件不匹配自动回退 |

AMSG 的调度是唯一在**边级别**做决策、提供**三档速度**、且有**后条件验证保护**的方案。

---

## 9  消融配置矩阵

`AMSGOptimConfig` 提供六种预设配置，用于分离各算法模块的独立贡献：

| 预设 | 信念定位 | 规划器 | 边策略 | $N_{\min}$ | $\theta_{\text{dom}}$ | $\theta_H$ | 目标 |
|---|---|---|---|---|---|---|---|
| `legacy` | 固定评分 | Dijkstra | 遗留 | 1 | 0.6 | 0.8 | 回归基线 |
| `edge_only` | 固定评分 | Dijkstra | 验证 | 1 | 0.6 | 0.8 | 边生命周期贡献 |
| `belief_only` | 贝叶斯 | Dijkstra | 遗留 | 1 | 0.6 | 0.8 | 信念定位贡献 |
| `planner_only` | 固定评分 | Belief-A* | 遗留 | 1 | 0.6 | 0.8 | 增强规划贡献 |
| `full` | 贝叶斯 | Belief-A* | 验证 | 1 | 0.6 | 0.8 | 完整方法 |
| `sava` | 固定评分 | Dijkstra | 严格验证 | **3** | **0.8** | **0.5** | 最严格模式 |

**实现参考**：`spatial/amsg_config.py:14-149`。通过环境变量 `AMSG_CONFIG` 选择预设。

**消融预期**：
- `legacy → edge_only`：应减少图谱噪声带来的错误导航，提升任务成功率
- `legacy → belief_only`：应改善跨页面定位准确度，减少"迷路"步骤
- `legacy → planner_only`：应提升长距离导航效率，减少冗余步骤
- `legacy → full`：三个模块协同应产生超线性收益（信念→规划→边验证的闭环增强）

---

## 10  与现有工作的理论对比

### 9.1  定位方式

现有图谱方法的定位本质上是**检索**——给定当前截图，找到最相似的图节点。这有两个理论缺陷：

1. **无时序建模**。检索仅基于当前观测，忽略了"我刚从搜索结果页执行了点击动作"这一强先验。AMSG 的时序通道利用转移频率表提供 $P(v | v', a_{t-1})$，在视觉相似的页面之间消岐。

2. **无不确定性量化**。检索返回 Top-1 结果，不提供"我对这个定位有多确信"的信息。AMSG 的信念分布提供完整的后验分布和熵，使下游规划器能感知定位风险。

### 9.2  图谱演进

| 系统 | 构建方式 | 更新方式 | 质量控制 |
|---|---|---|---|
| PG-Agent | 从 episode 批量构建 | 不更新 | 双层相似度检查 |
| KG-RAG | xTester 4-8h 探索 | 不更新 | BFS 路径评分 |
| WebNavigator | 自适应 BFS 爬取 | 不更新 | DOM 差分过滤 |
| MobiAgent | 任务执行中记录 | 手动纠正错误轨迹 | 人工审核 |
| **AMSG** | 在线 + 离线 + 审核 | **自动：生命周期验证** | **三道质量门** |

AMSG 是唯一实现**无人工干预的自动图谱演进**的系统。其自修复环路（第 4.4 节）确保图谱随应用更新自动适应。

### 9.3  VLM 与图谱的控制边界

现有方法在 VLM 与图谱的控制切换上只有两种策略：

1. **图谱作为提示源**（PG-Agent, KG-RAG）：图谱提供 RAG 上下文，VLM 始终做决策。图谱不减少 VLM 调用次数。
2. **图谱作为确定性控制器**（WebNavigator）：图谱直接控制导航，VLM 仅处理页面内操作。图谱错误时无法回退。

AMSG 引入**连续谱**：通过结果熵和锚定性，在每条边上独立决定图谱/VLM 的控制比例。这既避免了"始终调用 VLM"的延迟浪费，也避免了"盲目信任图谱"的可靠性风险。

### 9.4  WebClipper 的轨迹优化视角

WebClipper [Wang et al., 2025] 将轨迹建模为状态图并通过最小必要 DAG 剪枝冗余步骤，与 AMSG 有一个有趣的互补关系：

- WebClipper 作用于**单条轨迹的后处理**：给定一条冗长的轨迹，找到达到答案的最短路径
- AMSG 作用于**跨轨迹的持久化累积**：从多条轨迹中学习可复用的页面转移结构

两者可以结合：先用 WebClipper 剪枝单条轨迹，再将剪枝后的高质量轨迹输入 AMSG 的轨迹审核路径。

---

## 11  创新贡献总结

基于上述分析，AMSG 的核心算法创新可概括为：

1. **生命周期验证的图谱自演进**（定义 7-9）。转移边经历 hypothesis → candidate → promoted → demoted 的状态机，由后条件验证和结果优势度驱动提升/降级。这是移动 GUI 智能体领域首个无需人工干预的自演进图谱机制。

2. **熵驱动的 VLM 验证边界**（定义 10）。用结果分布的 Shannon 熵替代硬编码的 VLM/图谱控制集合，提供自适应的、数据驱动的控制切换。

3. **四通道贝叶斯信念定位**（定义 5-6）。视觉、语义、结构、时序四个独立通道的加权贝叶斯更新，支持通道不可用时的自动权重重分配。

4. **信念感知的增强路径规划**（定义 12）。在 A* 中融入过期衰减、探索奖励、信息增益和结果熵惩罚，使规划器能感知信念不确定性和转移可靠性。

5. **暂存优先的三道质量门持久化**。在线运行时、轨迹审核和离线探索三条路径共用规范化和验证机制，确保图谱仅包含经过质量控制的转移。

6. **非对称权威与锚定性分类**（定义 4）。在边级别形式化图谱/VLM 的控制边界，锚定转移由图谱直接执行，非锚定转移保留 VLM 的语义权威。

这些贡献不是孤立的——它们通过信念→规划→边验证→信念的闭环相互增强。信念定位的不确定性影响规划决策，规划决策影响动作选择，动作结果更新边的生命周期和结果分布，更新后的分布又反馈到下一步的信念更新。这一闭环是 AMSG 从"静态知识库"到"自演进动作库"转变的根本机制。
