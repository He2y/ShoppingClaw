# ClawGUI-Agent 系统架构

> **版本**: v4.1 (AMSG Runtime)  
> **更新**: 2026-05-28

---

## 1. 要解决什么问题

当前 Mobile GUI Agent 的核心瓶颈：

| 瓶颈 | 我们的方案 |
|------|-----------|
| **每步都依赖 VLM**，延迟高、成本大 | 空间图谱导航：已知路径跳过 VLM，毫秒级执行 |
| **跨会话记忆缺失**，每次从零开始 | 三层记忆架构：向量语义 + 图结构 + 会话状态 |
| **购物操作不可逆**，误下单/选错规格 | ClarificationAgent（任务前补全）+ SpecGuard（提交前校验）|

核心理念——**质量可控的自进化闭环**：

```
传统: 截图 → VLM → 动作 → 截图 → VLM → 动作 → ...（每步依赖 VLM）

我们: 截图 → 图谱定位 ─┬→ 已知路径 → 图谱导航 → 直接执行（跳过 VLM）
                       └→ 未知场景 → VLM → 执行 → 本地暂存（成功后审核入库）
```

---

## 2. 系统架构

![System Architecture](docs/architecture-system-overview.svg)

系统分 6 层，核心模块：

| 层 | 关键模块 | 一句话职责 |
|----|---------|----------|
| Agent 编排 | PhoneAgent, ClarificationAgent | 主循环编排 + 任务前意图补全 |
| Core 基础设施 | TaskSpecExtractor, SpecGuard, StatusReporter | 统一提取 + 购物安全 + 可观测性 |
| 模型 | 5× VLM Adapters, ProtocolBridge | 多模型适配 + 坐标归一化 |
| 记忆 | MemoryStore, SpatialGraphMemory, UnifiedState | 偏好/图谱/会话三层记忆 |
| 空间图谱 | RuntimeController, FunctionalityDiscovery | 路由决策 + 功能发现 |
| 设备 | DeviceFactory (ADB/HDC/XCTest) | Android + HarmonyOS + iOS 三平台 |

---

## 3. Agent 主流程

![Agent Execution Loop](docs/architecture-agent-loop.svg)

Agent 每步执行遵循 **感知 → 定位 → 路由 → 执行 → 记录** 的循环：

1. **ClarificationAgent**（首步）：三层短路检测任务歧义——规则提取 → Memory 偏好填充 → VLM 兜底。非购物任务 0ms 跳过。
2. **感知**：截图 → PageClassifier 分类页面类型
3. **定位**：SpatialGraphMemory.locate() 在图谱中定位当前页面
4. **路由**：RuntimeController 根据置信度和风险分级决定三种模式
   - **Navigate**：图谱有高置信度路径 → 跳过 VLM，直接执行
   - **Explore**：未知场景 → VLM 推理，同时本地暂存观测
   - **Verify**：高风险页面（支付/结算）→ 即使有路径也强制 VLM + SpecGuard 验证
5. **记录**：`record_observation(persist=False)` 仅写本地缓存；任务成功后 `flush_staged_graph()` 经去重和过滤才入库

---

## 4. 空间图谱 (AMSG)

### 4.1 图谱是什么

AMSG（Active Mobile Spatial Graph）是一个**语义级的应用导航图谱**。与 UI 元素树或像素特征不同，它工作在功能语义层——关注的是"这个页面能做什么、怎么到下一个页面"。

```
            AMSG 图谱示例 (淘宝购物流)

  [home] ──tap_search──→ [search_input] ──type+submit──→ [search_result]
                                                              │
                                                        tap_product
                                                              ▼
  [cart] ←──confirm──── [spec_selection] ←──tap_spec── [product_detail]
```

### 4.2 节点设计：PageNode

每个节点代表一个**语义页面状态**。核心标识是 `page_type`（如 home、search_result、product_detail），描述"这是什么页面"；辅以 landmarks 和 affordances 描述"页面上有什么、能做什么"。

**节点关键属性**：

| 属性 | 设计意义 |
|------|---------|
| **page_type** | 节点的主标识。17 种语义类型（home, search_input, search_result, product_detail, spec_selection, cart, checkout, payment...）|
| **landmarks** | **看到什么**——页面上可观测的关键 UI 要素（搜索框、商品卡片、价格标签、筛选栏）。来自两个来源的合并：Schema YAML 先验知识 + 运行时 PageClassifier 从截图提取的 elements |
| **affordances** | **能做什么**——该页面提供的操作能力（open_search、open_product、add_to_cart、filter）。同样由 Schema 先验 + 运行时推断合并而来 |
| **slots** | 当前观测到的动态值（query="iPhone 17"、price="¥8999"）。注意：这是**观测值**，不是模板参数 |
| **risk_level** | 安全分级，由 Schema 中的 page_type 映射决定：`normal` → 可直接导航，`medium` → 降低置信度阈值，`high`（checkout/payment/login）→ 强制 VLM 验证 |
| **semantic_signature** | 去重键，由 `app|domain|page_type|landmarks|affordances|slots` 拼接后哈希。同一语义页面在不同截图间产生相同签名 → 合并为一个节点，避免变体膨胀 |

**landmarks vs affordances 的区别**：landmarks 回答"页面长什么样"（视觉锚点），affordances 回答"用户下一步能做什么"（动作空间）。图谱路由时，affordances 决定从当前页面可以走哪些边；landmarks 用于定位匹配（当前截图是否对应这个节点）。

**对 UI 改版鲁棒的原因**：节点的身份不是像素或坐标，而是 `page_type` + 语义签名。淘宝改了首页布局，只要"搜索框 + 商品推荐"的语义结构不变，节点仍然匹配。

### 4.3 边设计：AffordanceEdge

每条边代表一次**页面间的语义转换**，携带：

| 属性 | 作用 |
|------|------|
| **intent** | 语义意图（click, type_text, scroll, go_back）|
| **semantic_target** | 操作对象（"搜索框", "加入购物车按钮"）|
| **expected_postcondition** | 执行后预期到达的页面类型 |
| **confidence** | 可靠性，基于历史成功/失败统计自动更新 |
| **weighted_cost** | 路径规划权重：`1.0 + failure_rate×3 + risk_penalty − confidence×0.3` |

边的 `semantic_edge_key`（由 `source_type|intent|target|region|target_type` 组成）用于去重——同一语义转换不会因为坐标微小偏移产生重复边。

特殊的**复合边（Compound）**：将"输入关键词 → 提交搜索"合成为一条带 `<query>` 槽位的模板边，运行时填入实际搜索词即可复用。

### 4.4 Agent 如何检索图谱获得路线

当 Agent 收到一个任务（如"去淘宝买 iPhone 17 Pro Max 银色 512G，加入购物车"），图谱检索分三步：

**Step 1: 目标推断（GoalSpec）**
从任务文本提取目标页面类型和槽位：
- "加入购物车" → 目标页面 = `spec_selection`（加购按钮在规格选择弹窗上）
- "iPhone 17 Pro Max 银色 512G" → slots = {query, color, storage}

**Step 2: 当前定位（locate）**
将当前截图的 PageClassifier 分类结果与图谱中的节点匹配：
- 输入：当前 app=淘宝, page_type=home, semantic_signature
- 输出：`PageBelief`（最佳匹配节点 + 置信度 0.92）

**Step 3: 路径规划（plan）**
在图谱上执行 Dijkstra 最短路径搜索：
- 起点：当前节点（home）
- 终点：目标页面类型（spec_selection）
- 权重：edge.weighted_cost（综合成功率、风险、置信度）
- 输出：`RoutePlan`（mode=navigate, 路径=[home → search_input → search_result → product_detail → spec_selection], 下一步动作）

如果置信度 ≥ 0.7 且 risk ≠ high，Agent 直接执行图谱给出的动作（**Navigate 模式**），跳过 VLM 推理。否则回退到 VLM（**Explore 模式**），同时将新观测暂存到本地缓存供未来使用。

### 4.5 建图管线

![AMSG Pipeline](docs/architecture-amsg-pipeline.svg)

5 阶段管线：截图 → PageClassifier 分类 → 语义提取 → 节点去重 → 边构建 → 功能发现

### 4.6 图谱质量门控（v4.1）

| 写入路径 | 策略 | 门控 |
|---------|------|------|
| 在线执行 | 每步 `persist=False` → 仅本地缓存 | 不直接写 Neo4j |
| 任务成功 | `flush_staged_graph()` → canonicalize → promote | 去重 + 质量过滤 |
| 任务失败 | 丢弃 | 坏转换不入库 |

**功能发现过滤**：只有可操作功能（`open_search`, `add_to_cart`）入库，瞬态数据（具体价格、商品标题）不入库。

---

## 5. 全局记忆系统

![Memory Architecture](docs/architecture-memory-system.svg)

三层架构，各解决不同问题：

| 层 | 存储 | 内容 | 生命周期 |
|----|------|------|---------|
| **向量语义** | FAISS 2048d | 用户偏好、联系人、任务模式 | 跨会话持久化 |
| **图结构** | Neo4j (AMSG) | 页面状态、转换路径、功能簇 | 持久化（门控写入）|
| **会话状态** | 内存 | 商品列表、步骤历史、约束 | 单次任务 |

关键设计决策：
- **按需检索**（RetrievalGateway）：不是每步都注入记忆，只在 VLM 表现出不确定（"忘记了"、"对比"）或停滞时触发
- **ClarificationAgent 接入 Memory**：规格缺失时先查偏好（"用户上次买 512G"），有命中就不打断用户
- **可观测性**（StatusReporter）：CLI 可见记忆系统状态——偏好数量、Neo4j 连接、相似任务命中

---

## 6. ClarificationAgent + SpecGuard 双层购物安全

两者共享 `TaskSpecExtractor` 提取基础设施，但职责互补：

| | ClarificationAgent | SpecGuard |
|--|---|---|
| **时机** | 任务开始前 | 购买提交前 |
| **目的** | 补全缺失信息 → 提高规划精度 | 校验 SKU 匹配 → 防止误操作 |
| **手段** | 规则 → Memory → VLM（三层短路）| 从 task + VLM plan 提取规格 → 与页面选择比对 |
| **结果** | 重组任务文本 | 放行 / Interact 拦截 |

**TaskSpecExtractor** 统一了此前分散在 4 处的规格提取逻辑（color/storage/size/query/contact/app），被 ClarificationAgent、SpecGuard、GoalSpec、MemoryManager 共享。

---

## 7. 创新点总结

| # | 创新 | 解决的问题 |
|---|------|----------|
| 1 | **VLM + 图谱混合导航** | 已知路径零 VLM 推理，延迟从秒级降至毫秒级 |
| 2 | **质量门控的在线图谱进化** | 解决无门控写入导致的节点污染（70 个 unknown 节点 → 去重后 7 个）|
| 3 | **语义级页面表示** | (landmarks, affordances, slots) 三元组，UI 改版后仍有效 |
| 4 | **功能发现 + Promotability** | 自动发现页面功能，瞬态数据不入库（53 → 9 个有效功能节点）|
| 5 | **三层记忆 + 按需检索** | 不全量注入，只在 VLM 不确定时触发，降低上下文污染 |
| 6 | **双层购物安全** | ClarificationAgent（前）+ SpecGuard（后），共享提取基础设施 |

### 与现有工作对比

| 维度 | AppAgent / CogAgent | GUI-TARS | **ClawGUI-Agent** |
|------|-------|---------|---|
| 每步推理 | 必须 VLM | 必须 VLM | **可选**（图谱跳过）|
| 跨会话学习 | 无 | 无 | **三层记忆** |
| 安全机制 | 无 | 无 | **双层防护** |
| 图谱质量 | N/A | N/A | **门控写入 + 过滤** |
| 多模型 | 单模型 | 单模型 | **5 种 VLM** |
| 多平台 | 单平台 | Android | **Android + HarmonyOS + iOS** |

### 值得深挖的方向

1. **图谱预训练**：大规模 App 截图预训练通用 AMSG
2. **自适应置信度**：根据用户风险容忍度动态调节 Navigate 阈值
3. **图谱压缩**：保持导航质量前提下的图谱蒸馏
4. **VLM 微调反馈环**：图谱成功/失败统计作为 RLHF 信号
5. **离线-在线融合**：OfflineExplorer 预探索与在线增量更新的优雅融合
