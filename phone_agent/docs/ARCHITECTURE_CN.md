# Shopping-Agent：系统架构文档

> **版本**: 2026-06-05（重写版）
> **范围**: 完整 `phone_agent/` 实现，重点覆盖 Agent 执行模型、记忆系统协同、AMSG 图谱设计与自动持久化管线。
> **用途**: 作为学术论文写作的架构参考文档。

Shopping-Agent 是一个**以 VLM 为主、图谱引导的移动 GUI 智能体**，通过自演进的**主动移动空间图谱（Active Mobile Spatial Graph, AMSG）**增强。系统在 Android、HarmonyOS 和 iOS 设备上自动化执行复杂购物任务，通过截图观测、页面状态定位、图谱路径规划、VLM 推理、动作执行和验证后图谱持久化的闭环循环实现。

核心设计原则是**非对称权威**：图谱负责加速和约束动作空间，VLM 保留对内容相关决策的语义权威。这一分离将 Shopping-Agent 与纯 VLM 智能体（每次重新探索导航）和纯图谱规划器（无法处理新视觉内容）区分开来。

---

## 1  设计哲学

移动 GUI 自动化在一个没有稳定 DOM 的视觉环境中运行，四类不确定性来源导致了大部分失败：

| 不确定性 | 失败模式 | 设计应对 |
|---|---|---|
| **感知不确定性** | 同一页面在不同应用版本、设备、广告、弹窗、滚动位置下外观不同 | `PageState` 将截图抽象为基于应用、页面类型、地标、可操作项、槽位、风险和语义签名的可复用状态 |
| **语义不确定性** | 历史记录中的"点击商品卡片"不能表明哪个商品符合当前任务 | 非锚定转移被显式标记；VLM 必须根据当前屏幕内容选择目标 |
| **转移不确定性** | 同一按钮可能打开 SKU 选择、登录、促销弹窗、结算或无反应 | `EdgeLifecycleManager` 追踪每个转移的经验结果分布、优势比和熵 |
| **上下文不确定性** | 过长的操作历史混淆 VLM 推理并遮蔽当前任务 | `UnifiedSessionState` + `RetrievalGateway` 每步注入进度，仅按需注入详细记忆 |

这四种应对产生了一个最准确的描述为**以 VLM 为主、图谱引导控制**的系统。AMSG 不是自主控制器；它是经过验证的动作库、状态定位器、路径规划器和持久化基底。每当决策依赖于当前屏幕内容或用户意图时，VLM 保持语义权威。这种非对称性是定义性的架构选择：它阻止图谱在内容敏感页面（商品选择、SKU 选择、结算）上重播过期坐标，同时允许图谱以近零延迟快捷执行机械式导航（首页到搜索、搜索到结果、筛选切换）。

---

## 2  系统架构

系统组织为六个功能层。各层解决不同的关注点，但并不孤立：记忆层向策略层上行供给上下文，战术层从记忆层读取定位和规划证据，反馈环路将验证后的观测写回图谱记忆和会话记忆。这种闭环耦合使系统能够自演进。

| 层 | 组件 | 职责 |
|---|---|---|
| **策略层**（VLM） | `PhoneAgent`、`TaskPlan`、`ClarificationAgent`、`SpecGuard`、`VerificationDetector` | 解释自然语言任务，规划子任务，检测歧义，保障购买安全，检测登录/验证码页面 |
| **战术层**（图谱） | `GraphRuntimeController`、`ActionAdvisor`、`EdgeLifecycleManager`、`MultiSignalLocalizer`、`EnhancedPlanner` | 在图谱中定位当前页面，推断图谱目标，规划路径，暴露已提升的动作，决定是否跳过或调用 VLM |
| **记忆层** | `MemoryManager`、`UnifiedSessionState`、`RetrievalGateway`、`SpatialGraphMemory`、`MemoryStore` | 存储用户偏好、会话状态、图谱记忆和轨迹文件；每步注入轻量上下文，按需触发详细检索 |
| **持久化层** | `GraphStore`、`GraphLifecycleStore`、`TrajectoryReviewer` | 将 UIState/Action/TransitionEdge 数据、生命周期统计和 VLM 审核的轨迹证据持久化到 Neo4j |
| **模型协议层** | `ModelClient`、5 族模型适配器、`ModelProtocolBridge`、`SpatialModelBridge` | 将 AutoGLM、UI-TARS、Qwen-VL、MAI-UI 和 GUI-Owl 的 VLM 输出归一化为统一的设备动作 IR |
| **设备执行层** | `ActionHandler`、平台特定处理器、`DeviceFactory`、`ADB/HDC/XCTest` 后端 | 在 Android、HarmonyOS 和 iOS 上执行 Tap、Type、Swipe、Back、Launch、Compound、Interact 和 Take_over |

```mermaid
block-beta
    columns 1
    block:strategic["策略层 — 以 VLM 为主的控制"]
        A["任务规划\n与预规划"] B["澄清\nAgent"] C["验证\n检测器"] D["SpecGuard\n安全守卫"]
    end
    block:tactical["战术层 — 图谱引导的动作库"]
        E["运行时图谱\n控制器"] F["动作\n顾问"] G["边\n生命周期"] H["信念\n定位器"]
    end
    block:memory["记忆层 — 四个记忆面"]
        I["FAISS\n向量存储"] J["统一\n会话状态"] K["检索\n网关"] L["轨迹\n审核器"]
    end
    block:execution["执行层 — 设备抽象"]
        M["ADB\nAndroid"] N["HDC\nHarmonyOS"] O["XCTest\niOS"] P["模型适配器 (5)"]
    end

    style strategic fill:#F3E8FD,stroke:#C4A8E0
    style tactical fill:#E8F5E9,stroke:#81C784
    style memory fill:#E0F2F1,stroke:#80CBC4
    style execution fill:#FFF3E0,stroke:#FFB74D
```

> 推荐图例：`figures/system-architecture.png`

### 2.1  跨层集成

六层通过三条主要数据流连接，每条服务于不同的角色：

1. **下行流（任务 → 动作）**：用户的自然语言任务依次通过 TaskPlan 提取、ClarificationAgent 短路、图谱定位、路径规划、VLM 推理、动作编译和设备执行。每个阶段都在收窄上下文：完整任务变为计划步骤，计划步骤变为图谱目标，目标变为路径，路径变为设备动作。

2. **上行流（观测 → 记忆）**：每个设备动作产生新的截图观测。该观测向上流经页面分类、后条件验证和结果记录。成功的任务轨迹触发图谱持久化；失败的任务产生轨迹文件但不污染图谱。

3. **横向流（图谱 ↔ VLM）**：图谱向 VLM 提示中提供导航提示、已提升动作和路径计划。VLM 的思考和动作输出反馈到图谱中作为转移证据。这种双向耦合使 VLM 受益于图谱结构，同时图谱从 VLM 决策中学习。

---

## 3  Agent 执行模型

### 3.1  闭环执行

主控制循环由 `PhoneAgent._execute_step()` 实现，这是一个 729 行的方法，在六阶段架构中集成了所有子系统。每步遵循相同的闭环，但通过循环的路径根据图谱置信度、页面风险和语义需求而变化。

```mermaid
flowchart LR
    A["1. 观测\n截图"] --> B["2. 分类\n页面语义"]
    B --> C{"3. 验证\n门控"}
    C -->|"登录/验证码"| T["Take_over\n(人工接管)"]
    C -->|"正常"| D["4. 图谱\n定位 + 规划"]
    D --> E{"5. 调度\n门控"}
    E -->|"锚定动作\n~0.5s"| F["快速路径"]
    E -->|"语义动作\n~5s"| G["完整 VLM 路径"]
    F --> H["6. 执行\n+ 验证"]
    G --> H
    H --> I["记忆 &\n图谱更新"]
    I --> A

    style F fill:#2E9E44,color:#fff,stroke:none
    style G fill:#0F4D92,color:#fff,stroke:none
    style T fill:#B64342,color:#fff,stroke:none
    style E fill:#E8873D,color:#fff,stroke:none
```

> 推荐图例：`figures/agent-execution-flow.png`

### 3.2  任务初始化与预规划

在执行循环开始之前，`PhoneAgent.run(task)` 执行初始化：

1. **状态重置**：清除对话上下文、步数计数器、图谱失败计数器、验证计数器、步骤摘要和模型适配器动作历史。
2. **VLM 预规划**：调用 `_vlm_pre_plan(task)` 使用更强的 VLM（通过 `AMSG_STRONG_VLM_*`）。预规划提取 `search_query`、`product`、`specs`、`target_action`、`target_page` 和带目标页面类型的有序步骤。
3. **TaskPlan 创建**：`TaskPlan.from_vlm_output()` 将预规划转换为带目标槽位的步骤列表。此计划是脚手架而非事实——当前截图和安全门控仍然决定实际可执行的内容。
4. **记忆会话启动**：记忆管理器初始化会话状态并加载相关用户偏好。

预规划有两个超越任务分解的作用：它为图谱路由填充 `GoalSpec`（让规划器知道目标页面类型），并为 SpecGuard 填充 `TaskSlots`（让安全系统在结算时验证规格选择）。

### 3.3  双速调度

调度门控是双速执行的架构核心。Agent 维护三条实际执行路径：

| 路径 | 触发条件 | VLM 开销 | 延迟 |
|---|---|---:|---|
| **快速路径** | `ActionAdvisor` 返回置信度 >= 0.9 的锚定已提升动作，且与当前计划步骤对齐 | 无 | ~0.5s |
| **图谱协助** | `GraphRuntimeController` 返回需要 VLM 验证的高置信度结构性动作 | 1 次调用 | ~3s |
| **完整 VLM 路径** | 无安全图谱动作、需要语义目标选择、低置信度、修复失败或安全守卫激活 | 1 次调用 | ~5s |

快速路径通过 `ActionAdvisor.get_fast_action()` 立即执行，并进行槽位替换（用目标槽位值替换 `<query>`、`<color>` 占位符）。执行后，后条件检查会截取新截图并验证页面类型。如果后条件不匹配，该步回退到完整 VLM 路径——图谱快捷方式不会被盲目信任。

完整 VLM 路径是刻意宽泛的。在购物任务中，图谱不应选择具体的商品、店铺、SKU 或结算决策，除非该转移已知为锚定且安全。这是根本性的"图谱作为顾问而非控制器"原则。

### 3.4  三层澄清机制

`ClarificationAgent` 在购物和外卖任务的第一步运行，使用三层短路设计最小化不必要的用户交互：

- **第一层（规则，0ms）**：`TaskSpecExtractor.extract()` 检查任务规格完整性。非购物域或规格已完整时直接跳过。
- **第二层（记忆偏好）**：查询 `MemoryStore` 中的用户偏好（如"喜欢银色"），填补缺失的规格。
- **第三层（强 VLM 歧义判断）**：仅当仍有空缺且查询模糊时运行。VLM 输出"CLEAR"或"CLARIFY: [问题]"。

这种分层设计直接连接到记忆系统：第二层查询用户偏好可填补规格缺口而无需提问。结果反馈到 `TaskSlots`，SpecGuard 后续用于验证购买安全。

### 3.5  SpecGuard 与购买安全

`SpecGuard` 位于 VLM 输出和动作执行之间，保护规格选择、结算和支付相邻页面：

- **`get_context_hints()`**：在规格/结算/支付页面注入约束提醒（预防性）。
- **`check()`**：在执行前拦截购买提交动作，可替换为 `Interact`（反应性）。

区分*选择*（选择可见选项）和*提交*（加购、立即购买、结算、支付）。选择被允许；提交被守护：如果 VLM 不能在其思考中证明请求的规格已被选择，结算或支付将被阻止。

这连接回 `TaskSlots`：用户的原始规格需求（由 `TaskSpecExtractor` 提取一次并由 ClarificationAgent 丰富）作为一等状态贯穿整个执行过程，而非偶然的提示文本。

---

## 4  记忆架构

记忆系统分离了四个服务于不同时间和语义角色的面。关键设计决策是**记忆解耦**：并非所有记忆都注入每次 VLM 调用。系统始终注入轻量上下文，仅当 VLM 推理信号表明需要时才触发详细检索。

### 4.1  四个记忆面

| 记忆面 | 存储 | 时间范围 | 注入策略 |
|---|---|---|---|
| **用户记忆** | `MemoryStore`（FAISS 向量存储） | 跨会话 | 偏好和联系人在澄清和槽位填充时注入 |
| **会话状态** | `UnifiedSessionState` | 单任务 | 进度摘要和当前焦点每步注入 |
| **图谱记忆** | `SpatialGraphMemory` + Neo4j | 跨会话（持久化图谱）+ 会话内（暂存观测） | 路径存在时注入图谱上下文；匹配页面时注入动作提示 |
| **轨迹文件** | 每任务 JSON | 单任务，保留供审核 | 不注入。由 `TrajectoryReviewer` 用于离线图谱演进 |

### 4.2  轻量上下文注入

`MemoryManager.get_injection_context()` 实现解耦策略：

1. **始终注入**：简短的进度摘要（任务计划状态、步数、最近动作）。
2. **始终注入**：当前焦点（Agent 当前正在做什么）。
3. **触发式注入**：`RetrievalGateway` 仅当上一次 VLM 思考表明回忆、比较、计算、商品查询或停滞时才激活。
4. **条件注入**：任务约束（价格范围、颜色、存储）仅在存在且当前页面与决策相关时注入。

在典型的 30 步购物任务中，完整检索可能在 3-5 步触发。其余 25+ 步仅接收 2-3 行进度摘要。这对 VLM 性能至关重要：过长的历史混淆推理并增加延迟。

### 4.3  四面协同

四个记忆面不是独立数据库——它们形成协调的记忆架构：

- **用户记忆 → 澄清**：用户偏好在第二层填补规格缺口，减少不必要的提问。
- **会话状态 → 图谱路由**：会话状态中的任务槽位填充 `GoalSpec`，驱动路径规划。
- **图谱记忆 → VLM 上下文**：图谱记忆中的已提升动作和路径计划作为动作提示出现在 VLM 提示中。
- **轨迹文件 → 图谱演进**：保存的轨迹喂入 `TrajectoryReviewer`，验证新转移并作为图谱假设导入。
- **会话状态 → 轨迹文件**：任务结束时，会话状态序列化为轨迹 JSON 文件供审计和审核。

这种协调意味着从过去任务中学到的用户偏好（存储在 FAISS 中）可以影响当前任务的图谱路由（通过 GoalSpec）和安全检查（通过 SpecGuard）——用户无需重复表述。

---

## 5  主动移动空间图谱（AMSG）

AMSG 是核心研究贡献。它是一个有类型的有向图，将移动 UI 表示为由动作转移连接的页面状态网络，标注了生命周期元数据、结果分布和定位证据。

### 5.1  形式化定义

```
G = (V, A, E_c, E_h, T, Σ, B, Π, L, O)

V      UIState 节点，由 PageState 实现
A      Action 节点，存储语义意图和锚定证据
E_c    已提交/已提升的 UIState-Action-UIState 转移
E_h    提升前暂存的假设/候选转移
T      TaskTarget 节点，用于轨迹级检索
Σ      页面类型和合理转移的领域模式
B      UIState 上的信念分布（贝叶斯后验）
Π      加权转移边上的规划器（Dijkstra 或 A*）
L      边提升和降级的生命周期记录
O      后条件统计的结果分布
```

### 5.2  UIState / PageState

`PageState` 将截图抽象为可复用的页面身份。这不是像素级身份——它是*语义*身份，使系统能够识别"这是淘宝的商品详情页"而不管显示的是哪个商品。

| 字段 | 系统中的角色 |
|---|---|
| `state_id` | 来自 `(app, page_type, landmarks, affordances, risk)` 的稳定哈希 |
| `app` | 当前应用名，通过模式别名规范化 |
| `page_type` | 分类：`home`、`search_input`、`search_result`、`product_detail`、`spec_selection`、`cart`、`checkout`、`filter_panel` 等 |
| `landmarks` | 稳定的视觉锚点（如"搜索栏"、"价格标签"、"加购按钮"） |
| `affordances` | 可能的交互（如"点击商品"、"下滑"、"输入查询"） |
| `slots` | 从任务和页面文本检测的运行时槽位（如 `{query: "iPhone 16"}`） |
| `risk_level` | `normal`、`medium` 或 `high`。支付和登录页面始终为 `high` |
| `semantic_signature` | 确定性字符串：`app|domain|page_type|landmarks|affordances|slots` |

页面状态抽象使图谱可跨任务复用。从一次购物会话学到的商品详情页可以指导完全不同会话中的导航，因为图谱存储的是页面*类型*和*结构*，而非具体内容。

### 5.3  锚定与非锚定转移

这一区分是"图谱作为顾问而非控制器"的操作核心：

| 转移 | 锚定？ | 图谱行为 | VLM 角色 |
|---|---|---|---|
| `home → search_input` | 是 | 快速路径，带坐标 | 无 |
| `search_input → search_result` | 是 | 复合动作：输入 + 提交 | 无（槽位替换） |
| `search_result → product_detail` | **否** | 仅提示（无坐标） | 必须选择匹配当前任务的商品 |
| `product_detail → spec_selection` | **否** | 仅提示 | 必须判断商品页面和目标动作 |
| `spec_selection → cart/checkout` | **否** | 仅提示 | 必须安全选择用户的 SKU |

锚定转移具有稳定的坐标和确定性结果。非锚定转移需要关于*与哪个*元素交互的语义判断——图谱知道转移存在，但只有 VLM 能在当前屏幕上决定具体目标。

---

## 6  图谱自演进

图谱不是通过记录每个观测到的动作来增长的。它通过一个暂存优先的管线和三条独立路径演进，全部收敛到同一个质量门控的持久化层。

### 6.1  边生命周期状态机

AMSG 中的每个转移都经历一个决定其运行时权限的生命周期：

```
hypothesis（假设）→ candidate（候选）→ promoted（已提升）→ demoted（已降级）
```

- **提升条件**：`verification_count >= min_verification_count`（默认 1，严格 3）AND `dominance_ratio >= threshold`（默认 0.6，严格 0.8）AND `risk_level != "high"`
- **降级条件**：已提升边的优势比降至 40% 以下时自动降级。这处理了 UI 变更：如果应用更新改变了按钮的目标，结果分布偏移，优势度下降，边自动降级。

**结果熵**（Shannon 熵）：$H = -\sum_i p_i \ln(p_i)$

当 $H >$ 结果熵 VLM 阈值（默认 0.8）时，转移被标记为需要 VLM 验证而非直接执行。这用数据驱动的决策边界替代了硬编码的"风险转移"列表。

### 6.2  三条持久化路径

**路径 1（在线运行时）**：任务执行期间，每个动作产生后条件观测。`SpatialGraphMemory.record_observation()` 在本地暂存转移。任务结束时，`flush_staged_graph()` 规范化状态和边，提升有效转移，写入 Neo4j。**失败的任务不会刷新**——这是第一道质量门。

**路径 2（轨迹审核）**：`TrajectoryReviewer` 处理保存的轨迹文件。提取页面转移，对比现有图谱边去重，并请求强 VLM 验证新候选。仅 VLM 批准的转移以 `hypothesis` 边导入。这是第二道质量门——防止对话框、广告、分类器错误和瞬态屏幕污染图谱。

**路径 3（离线探索）**：`import_exploration_staging()` 通过规范化、瞬态页面过滤、应用不匹配过滤、搜索宏合成、同页压缩和边质量过滤处理预采集的探索数据。

关键洞察是**没有原始动作直接写入 Neo4j**。每条持久化路径都通过至少一道质量门（任务成功、VLM 验证或规范化过滤）。这使 AMSG 成为"经过验证的动作库"而非嘈杂的轨迹堆积。

> 推荐图例：`figures/graph-persistence-pipeline.png`

---

## 7  定位与规划

### 7.1  贝叶斯信念定位

`MultiSignalLocalizer` 维护图谱节点上的概率分布，每次新观测时更新：

$$B_t(v) = \eta \cdot P(o_t | v) \cdot \sum_{v'} P(v | v', a_{t-1}) \cdot B_{t-1}(v')$$

观测似然 $P(o_t | v)$ 是四个独立通道的加权和：

| 通道 | 权重 | 信号 | 可用性 |
|---|---|---|---|
| 视觉 | 0.30 | VLM 截图嵌入的余弦相似度 | 可选 |
| 语义 | 0.25 | 文本语义嵌入的余弦相似度 | 可选 |
| 结构 | 0.25 | 加权相似度：app(0.35) + page_type(0.35) + landmark Jaccard(0.20) + affordance Jaccard(0.10) | 始终 |
| 时序 | 0.20 | 来自历史的转移频率 $P(v | v', a_{t-1})$ | 首次动作后 |

通道不可用时（如未配置嵌入模型），权重重新分配到可用通道。这种优雅退化意味着系统无需嵌入模型也能工作（仅使用结构+时序），但有了它们会更好。

信念分布连接到规划器：高熵信念（对当前位置的不确定性）通过增强成本函数中的信息增益项增加动作成本。

### 7.2  路径规划

`EnhancedPlanner` 支持三种规划后端（通过 `AMSGOptimConfig` 选择）：

**Dijkstra**（遗留）：基础成本 + 失败率惩罚 + 风险惩罚 - 置信度奖励

**A\***（模式感知）：Dijkstra 成本 + 基于 BFS 预计算的模式距离的可采纳启发式

**Belief-A\***（完整）：
$$C'(e) = \text{base} + 0.5 \cdot \text{staleness} - \text{exploration\_bonus} - \text{information\_gain} + \text{entropy\_penalty}$$

其中：
- **过期衰减**：$1 - \exp(-\Delta t / \text{halflife})$ — 未近期遍历的边向更高成本衰减
- **探索奖励**：$-w / \sqrt{1 + \text{visits}}$ — UCB 风格的低访问状态奖励
- **信息增益**：$+w \cdot H_{\text{belief}} \cdot \text{staleness}$ — 高熵信念状态成本更高
- **熵惩罚**：$+0.5 \cdot H_{\text{outcome}}$ — 不可预测的转移被惩罚

规划器连接回信念定位器：信念熵直接影响路径成本，在定位置信度和规划决策之间创建反馈环路。

### 7.3  动作编译

图谱原生动作通过两阶段 IR 管线编译：

```
SemanticActionIR（意图、语义目标、定位器、后条件）
    → DeviceActionIR（动作类型、坐标、坐标空间、屏幕尺寸）
    → ActionHandler 的标准动作字典
```

`SpatialModelBridge` 处理第一阶段（语义到设备 IR），`ModelProtocolBridge` 处理跨模型家族的坐标归一化。这保持 AMSG 模型无关：图谱存储语义意图和定位器证据，而适配器处理模型原生语法和坐标系统。

---

## 8  模型与设备抽象

### 8.1  多模型支持

Shopping-Agent 通过统一的适配器架构支持五个 VLM 家族：

| 模型家族 | 坐标空间 | 上下文策略 | 响应格式 |
|---|---|---|---|
| AutoGLM | [0, 1000] 归一化 | 累积（每轮追加） | `<answer>` XML |
| UI-TARS | 绝对像素（smart_resize） | 最多 5 张图，裁剪最旧的 | "Thought: ... Action: ..." |
| Qwen-VL | [0, 999] 归一化 | 每轮重新构建 | `<tool_call>` JSON |
| MAI-UI | [0, 999] 归一化 | 最多 3 张图 | `<thinking>` + `<tool_call>` |
| GUI-Owl | [0, 1.0] 小数 | 最多 1 张图（仅当前） | Action + `<tool_call>` |

`ModelProtocolBridge._scale_coord()` 是坐标转换枢纽，通过归一化的 (0, 1) 中间表示在所有坐标空间之间转换。

---

## 9  对比分析

### 9.1  在文献中的定位

Shopping-Agent 解决了当前 GUI 智能体领域的三个具体缺口：

**缺口 1：无状态的重复探索。** 大多数 GUI 智能体（包括 ColorAgent [Li et al., 2025]、MobiAgent [Zhang et al., 2025] 等强系统）将每个任务会话视为独立。它们可能通过反思或自纠正在会话内学习，但关于应用导航的结构性知识不会跨会话持久化。Shopping-Agent 的 AMSG 在 Neo4j 中持久化经过验证的转移，使 Agent 能够构建关于应用导航模式的累积知识。

**缺口 2：无生命周期的图谱。** 构建导航图谱的系统——PG-Agent [Chen et al., 2025] 从 episode 构建页面图，KG-RAG [Guan et al., 2025] 将 UTG 转换为向量数据库，WebNavigator [Zhang et al., 2025] 通过启发式探索构建交互图——将图谱视为静态产物。一旦构建，边不会演进。Shopping-Agent 引入完整的生命周期：hypothesis → candidate → promoted → demoted，具有数据驱动的提升标准和 UI 变更时的自动降级。

**缺口 3：二元的图谱/VLM 控制。** 现有图谱增强的 Agent 将图谱用作静态 RAG 源（PG-Agent、KG-RAG）或完全绕过 VLM 的确定性控制器（WebNavigator 的 Teleport）。Shopping-Agent 引入连续谱：快速路径（仅图谱，~0.5s）、图谱协助（图谱提示 + VLM 验证，~3s）和完整 VLM 路径（仅 VLM，~5s）。调度决策是数据驱动的，基于边生命周期阶段、结果熵和锚定状态。

### 9.2  详细对比

| 维度 | ColorAgent | PG-Agent | KG-RAG | WebNavigator | MobiAgent (AgentRR) | **Shopping-Agent** |
|---|---|---|---|---|---|---|
| **图谱结构** | 无 | 页面图 | UTG → 向量库 | 交互图（BFS） | ActTree（前缀复用） | AMSG（带生命周期的类型化有向图） |
| **图谱演进** | N/A | 构建后静态 | 提取后静态 | 离线 BFS 后静态 | 记录-重放（静态） | 自演进：在线暂存 → 后条件验证 → 生命周期提升 → 降级 |
| **持久化** | 无 | 会话内内存 | 向量库（静态） | 向量库（静态） | 潜在记忆模型 | Neo4j + 生命周期元数据 + 结果分布 |
| **VLM/图谱边界** | 仅 VLM | RAG → VLM | RAG → VLM | 确定性传送（无 VLM） | 经验 → 跳过 VLM（二元） | 三速调度 + 熵驱动边界 |
| **定位** | VLM 感知 | BFS 相似搜索 | 嵌入检索 | 多模态检索 | 页面匹配 | 四通道贝叶斯信念 |
| **规划** | 多 Agent 分解 | 页面图 BFS | UTG BFS | 交互图最短路径 | 前缀可复用性 | Belief-A* + 模式启发式 |
| **安全机制** | 未报告 | 未报告 | 未报告 | 未报告 | 未报告 | SpecGuard：任务槽位感知的购买拦截 |
| **转移验证** | 自演进训练 | 无 | 无 | 无 | 无 | 后条件验证 + 结果分布 + 熵阈值 |
| **质量门控** | 轨迹过滤 | 无 | 无 | 无 | 手动纠正 | 三道门：任务成功、VLM 轨迹审核、规范化 |
| **多模型支持** | 专有模型 | GPT-4o | MobileAgent-v2 | GPT-4o, Gemini, Claude | MobiMind（自定义） | 5 族：AutoGLM, UI-TARS, Qwen-VL, MAI-UI, GUI-Owl |
| **跨平台** | 仅 Android | Android | Android + HarmonyOS | 仅 Web | 仅 Android | Android + HarmonyOS + iOS |

### 9.3  创新点总结

1. **以 VLM 为主的图谱引导**：图谱收窄和锚定动作空间但不替代视觉语义推理。
2. **移动 GUI 的页面状态抽象**：截图被折叠为基于页面类型、地标、可操作项和语义签名的可复用页面状态。
3. **基于熵的双速调度**：调度边界由边生命周期阶段和结果熵决定，而非硬编码规则。
4. **生命周期验证的图谱自演进**：转移仅在后条件验证和结果优势度测量后提升，在可靠性衰减时自动降级。
5. **结果熵决策边界**：高熵转移自动要求 VLM 验证，用数据驱动信号替代脆弱的硬编码边界。
6. **暂存优先的三道质量门持久化**：在线执行、离线探索和轨迹审核全部通过规范化和验证后才持久化到 Neo4j。
7. **结构化任务约束作为一等运行时状态**：任务规格提取一次后被澄清、图谱路由、提示构建、SpecGuard 和运行时槽位填充复用。
8. **记忆解耦的提示注入**：进度和焦点始终注入；代价高昂的检索仅在推理信号表明需要时发生。
9. **多通道贝叶斯信念定位**：四个独立观测通道，通道不可用时自动权重重新分配。
10. **模型无关的动作 IR**：图谱存储语义动作和定位器证据，模型特定的适配器归一化坐标和语法。

---

## 10  推荐论文图例

1. **系统架构**：`figures/system-architecture.png` — 六层架构与跨层数据流
2. **Agent 执行流程**：`figures/agent-execution-flow.png` — 五阶段闭环执行与双速调度
3. **AMSG 图谱模式**：`figures/amsg-graph-schema-nature-image2.png` — UIState-Action-UIState 模体、生命周期证据、结果分布
4. **图谱持久化管线**：`figures/graph-persistence-pipeline.png` — 三条持久化路径收敛到质量门控的 Neo4j 写入

---

## 11  论文方法摘要

> Shopping-Agent 是一个以 VLM 为主的移动 GUI 智能体，通过自演进的主动移动空间图谱（AMSG）增强。每个执行步骤观测当前屏幕，分类页面语义，使用四通道贝叶斯推理定位页面状态信念，验证前一转移的后条件，并通过三速门控调度：锚定动作通过快速路径执行（~0.5s），不确定转移接受 VLM 协助，语义决策使用完整 VLM 推理路径（~5s）。调度边界是数据驱动的，由边生命周期阶段和结果熵决定而非硬编码规则。AMSG 在 Neo4j 中存储页面抽象、语义动作节点、带生命周期元数据的验证转移边、经验结果分布和轨迹级 TaskTarget 锚点。在线观测和离线探索产物经过暂存、规范化、过滤后，仅在后条件验证或 VLM 轨迹审核后才持久化——没有原始动作直接写入图谱。用户约束作为一等任务槽位提取，并由 SpecGuard 在购买提交点强制执行，而会话记忆通过保持 VLM 上下文精简的轻量按需机制注入。
