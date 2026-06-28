# 从稳定操作购物 App 到主动专业购物助手：Shopping-Agent 研究升级方案

> **版本**: 2026-06-28
> **用途**: 汇报展示文档。面向不熟悉本项目的人，先介绍现有系统如何稳定操作购物 App，再从第一性原则说明为什么这还不是专业购物助手，最后给出结合 MobileForge 与 MemGUI-Agent 启发后的购物垂直升级计划。
> **依据**: `phone_agent/docs/ARCHITECTURE_CN.md`、`phone_agent/docs/AMSG_DESIGN.md`、`references/MobileForge.pdf`、`references/MemGUI-Agent.pdf`。

---

## 1  现有工作：我们已经做成了一个稳定的购物 App 操作智能体

### 1.1  当前项目解决的问题

Shopping-Agent 当前解决的是真实手机购物 App 自动化中的**可执行性与可靠性**问题。它能在淘宝、京东等真实 App 中执行搜索、筛选、打开商品、选择规格、加入购物车、推进到结算前检查等长程任务。

这个问题本身不简单。手机购物 App 没有稳定 DOM，页面受广告、弹窗、登录态、活动页、App 更新、设备分辨率、滚动位置影响；同一个按钮也可能跳到规格页、登录页、促销弹窗、结算页或无响应。通用 GUI 模型即使能看懂截图，也经常在长程任务里焦点漂移、误判价格、误报完成。

因此，当前系统的核心命题是：

> 如何让一个可端侧部署的小型 GUI 模型，在真实购物 App 的长程操作中达到接近强 VLM 的可靠性，同时保持低延迟、低云调用和可审计安全边界？

当前答案是一个混合智能体：

```text
小模型高频执行
  + 强 VLM 低频里程碑监督
  + AMSG 空间图谱导航
  + 机械护栏反幻觉
  + 会话记忆文件抗遗忘
```

这使系统从“通用 GUI 模型偶尔会点购物 App”推进到了“可以稳定完成购物 App 操作链路”。但这还不是一个真正的专业购物助手。

### 1.2  非对称权威：现有系统的核心设计

`ARCHITECTURE_CN.md` 中最重要的原则是**非对称权威**：不同类型的判断交给最适合、失败代价最低的主体。

| 决策类型 | 当前权威 | 代码位置 | 设计理由 |
|---|---|---|---|
| 当前屏幕 grounding | 小型 GUI 模型 | `phone_agent/model/`, `phone_agent/actions/` | 每步都要做，必须低延迟；模型擅长看当前截图找元素 |
| 任务拆解与计划修订 | 强 VLM 里程碑监督 | `phone_agent/milestone_supervisor.py`, `phone_agent/task_plan.py` | 语义规划能力强，但只应低频调用 |
| 数字、约束、完成证据 | 机械护栏 | `phone_agent/core/spec_guard.py`, `phone_agent/verification_detector.py` | 价格、预算、完成判定不应交给模型幻觉 |
| 结构化导航复用 | AMSG 空间图谱 | `phone_agent/spatial/`, `phone_agent/memory/graph_store.py` | 首页到搜索、搜索到结果等机械转移可复用 |
| 单任务抗遗忘 | 会话记忆文件 | `phone_agent/session_memory_file.py` | 长程任务需要原始目标、已验证事实和子任务进度 |

这套机制解决的是 GUI 操作可靠性，而不是购物决策智能。它让系统能稳定到达页面、点击正确区域、避免明显的价格和完成幻觉。

### 1.3  AMSG：让购物 App 操作具备空间记忆

`AMSG_DESIGN.md` 介绍的 AMSG 是当前系统区别于普通 ReAct Agent 的关键。它不是静态页面图，而是一个会随真实执行持续更新的动作库。

AMSG 存储语义页面和语义动作，而不是截图哈希和绝对坐标：

```text
PageState = (app, page_type, landmarks, affordances, slots, risk_level)
Action = (type, intent, semantic_target, postcondition, lifecycle_stage)
```

图谱中的基本结构是：

```text
(UIState) -[NEXT_ACTION]-> (Action) -[PRODUCES]-> (UIState)
```

核心机制：

| 机制 | 作用 | 对用户体验的影响 |
|---|---|---|
| 语义去重 | 把同类页面合并，例如不同关键词的搜索结果页都归为 `search_result` | 图谱可复用，不因内容变化爆炸 |
| 边生命周期 | 跟踪每条转移的成功率、失败率、优势比和阶段 | 只有可靠边才可被自动复用 |
| 锚定性分类 | 区分图谱可独立执行的边和必须看屏幕的边 | 减少模型调用，同时避免过期坐标误点 |
| 后条件验证 | 在下一步验证上一步动作是否达到预期页面 | 图谱学习基于真实结果，不靠假设 |

AMSG 的价值是让 Agent 不必每次都从零学习“购物 App 怎么走”。它解决的是**操作路径复用**问题。

### 1.4  三档调度：让操作更快、更稳

当前系统按图谱边的可靠性和锚定性做三档调度：

| 档位 | 使用条件 | 行为 |
|---|---|---|
| Fast Path | promoted、锚定、低风险、有坐标或复合动作 | 跳过模型，直接执行机械导航 |
| Graph Co-pilot | 图谱知道方向但目标需看屏幕 | 图谱提供 `graph_hint`，小模型负责 grounding |
| Model Path | 图谱未知、非锚定、高风险或验证失败 | 完整模型推理 |

这让首页到搜索、搜索到结果等稳定步骤加速，同时在商品选择、SKU 选择、结算前检查等内容敏感步骤保持谨慎。

### 1.5  会话记忆与机械护栏：解决长程操作中的幻觉

当前 `SessionMemoryFile` 保存：

- `original_task`：原始用户任务，永不改写，是抗漂移锚。
- `subtasks`：任务拆解后的子任务状态。
- `verified_facts`：已验证事实，例如已筛选条件、已确认价格、已加入购物车。
- `revisions`：强 VLM 里程碑修订记录。

机械护栏负责模型不擅长但代码擅长的判断：

- 价格解析和预算比较。
- 商品约束校验。
- 购买提交点拦截。
- 完成证据判断。
- 空响应和不可解析输出恢复。

这些机制让系统更可靠，但它们主要是在保护“操作任务”不出错，而不是在优化“购物决策”。

### 1.6  从第一性原则看：当前真正缺的不是更会点 App，而是更会购物

如果题目是“手机 GUI 自动化”，当前工作已经很接近核心问题。但本项目题目和应用场景是**手机购物场景的主动购物助手**，那么研究目标必须往前走一步。

购物不是简单的页面导航。用户真正想要的不是“帮我打开某个页面并点击加购”，而是：

```text
理解我的购买意图
  -> 明确硬约束和软偏好
  -> 主动收集候选商品证据
  -> 比较价格、规格、评价、服务和风险
  -> 给出可解释推荐
  -> 在我确认后安全执行购买相关操作
```

因此，当前系统的真实不足不是“没有吸收 MobileForge/MemGUI”，而是：

1. **缺少购物意图建模**。用户说“买一个蓝牙耳机”时，系统现在主要把它拆成 App 操作步骤；但专业购物助手应追问或推断使用场景、预算、品牌偏好、降噪/续航/佩戴方式/发货时效等决策变量。
2. **缺少商品候选集管理**。当前系统能打开商品页、读取价格、加购，但还没有稳定维护一个候选商品池，记录每个商品的证据、优缺点、约束匹配和淘汰原因。
3. **缺少多目标购物决策**。购物不是只看价格，还涉及规格、质量、评价、店铺可靠性、优惠券、售后、配送、风险。当前机械护栏能判断预算越界，但没有形成 utility scoring 或 Pareto 比较。
4. **缺少主动策略**。当前系统更像执行器：按用户目标完成链路。专业购物助手应主动提出“是否放宽品牌”“这个低价商品评价风险高”“要不要等优惠/换平台比价”等策略建议。
5. **缺少购物风险与信任模型**。促销文案、券后价、预售价、店铺信誉、评价异常、规格不一致、默认勾选服务等都影响购买安全。当前护栏偏价格和完成判定，购物风险维度还不够。
6. **缺少跨会话偏好学习**。用户对品牌、价位、颜色、物流、售后、平台、是否接受二手/预售等偏好应逐步沉淀；当前记忆更多服务单任务执行，还不是购物画像。
7. **缺少“建议权”和“执行权”的边界**。专业购物助手可以推荐和解释，但购买、提交订单、支付必须由用户确认。当前系统有提交点护栏，但还没有完整的购物决策授权模型。

这才是下一阶段的核心研究问题：

> 如何从“会稳定操作购物 App 的 GUI Agent”，升级为“能主动理解需求、收集证据、比较商品、解释推荐并安全执行的专业购物助手”？

---

## 2  最新研究启发：两篇论文是支撑机制，不是研究目标

### 2.1  MobileForge 的启发：陌生 App 探索可以服务购物能力建设

MobileForge 的核心贡献是 annotation-free adaptation：在真实目标 App 中探索、挖掘可执行任务、执行 rollout、产生分层反馈，再把经验转成策略改进信号。

但对本项目来说，不能把它简单理解成“用强化学习训练购物 App policy”。购物 App 更新频繁、页面动态强、风险动作多，对单个 App 做 RL 容易过拟合当前版本，回报稀疏且安全成本高。

更合理的迁移是把 MobileGym 思想落到 AMSG，形成 **AMSG-Gym**：

```text
陌生购物 App 探索
  -> 挖掘可执行购物任务
  -> 安全 rollout
  -> 分层反馈
  -> 更新 AMSG 图谱 / schema / 边生命周期 / 探索策略
```

AMSG-Gym 的目标不是训练模型，而是让系统更快适应新购物 App、新页面、新活动入口、新干扰态。它服务的是“专业购物助手”的基础设施：如果助手要跨平台比较和购买，必须先稳定理解不同 App 的购物流程。

| MobileForge / MobileGym 概念 | 本系统落点 | 面向购物助手的意义 |
|---|---|---|
| Target-app exploration | `phone_agent/memory/exploration/` | 冷启动陌生购物 App 的首页、搜索、结果、商品详情、SKU、购物车等链路 |
| Curriculum mining | `task_builder.py` | 生成补齐购物流程覆盖的探索任务，不是默认生成训练任务 |
| Rollout evaluation | `trajectory_reviewer.py`, 后条件验证 | 判断某条购物流程转移是否真实有效 |
| Corrective hints | `milestone_supervisor.py`, `ActionAdvisor` | 指导下一轮探索和图谱修复 |
| Training export | `dataset_exporter.py` | 作为副产品，不作为主目标 |

### 2.2  MemGUI-Agent 的启发：长程购物需要可验证上下文动作

MemGUI-Agent 的核心贡献是 Context-as-Action：把上下文管理作为模型动作的一部分，让模型学会何时记忆、何时折叠历史、何时保留 UI 事实。

购物任务天然长程，且事实密集：价格、券后价、规格、库存、店铺、评价、物流、售后、用户偏好都会在不同页面出现。如果这些事实只靠历史文本堆叠，很容易丢失或混淆。

但本项目不能直接让小模型自由写记忆，因为价格、商品、规格、完成状态都是高风险事实。更合适的是 **Verified Context Actions (VCA)**：

```text
模型/监督者/机械护栏提出上下文动作
  -> ContextCommitter 校验证据
  -> 低风险事实自动提交
  -> 高风险事实需机械或强 VLM 证据
  -> 写入购物记忆和商品证据账本
```

VCA 对购物助手的意义是：上下文管理不只是压缩历史，而是构建可审计的购物证据。

### 2.3  本项目自己的垂直创新：Shopping Intelligence Layer

MobileForge 和 MemGUI-Agent 给的是基础能力启发，但专业购物助手需要自己的垂直智能层。建议把下一阶段核心命名为 **Shopping Intelligence Layer (SIL)**，它运行在现有 GUI 操作底座之上。

SIL 由五个模块组成。

#### 2.3.1  Shopping Intent Graph：购物意图图谱

把用户任务从一句自然语言改写为结构化购物意图：

```text
ShoppingIntent = {
  product_category,
  hard_constraints,
  soft_preferences,
  decision_criteria,
  budget_policy,
  risk_tolerance,
  clarification_needed,
  approval_boundary
}
```

例子：

```text
“买一个 500-1000 元蓝牙耳机”
  -> hard: price <= 1000, category=bluetooth_headphone
  -> soft: noise_cancellation?, battery?, brand?, in-ear/headphone?
  -> decision: price/value, rating, shipping, after-sales
  -> clarification: 是否必须降噪？是否接受非官方旗舰店？
  -> approval: 只允许加购，不允许提交订单
```

这解决的是“用户到底想买什么”的问题，而不是“下一步点哪里”。

#### 2.3.2  Product Evidence Ledger：商品证据账本

建立候选商品池，每个候选商品不是一段文本摘要，而是一组带来源的证据：

```text
ProductCandidate = {
  title,
  price_raw,
  price_final,
  coupon_info,
  specs,
  seller,
  rating,
  review_signals,
  shipping,
  after_sales,
  risk_flags,
  evidence_refs,
  match_status,
  reject_reason
}
```

证据来自截图、OCR/VLM 抽取、机械价格解析、页面状态和用户偏好。高风险字段必须可追溯到截图或机械解析，不能只来自模型自由生成。

这解决的是“系统看过哪些商品、为什么保留/淘汰”的问题。

#### 2.3.3  Shopping Decision Engine：多目标购物决策引擎

购物决策应分两层：

1. **硬约束过滤**：预算、品类、规格、是否官方店、是否现货、是否满足用户明确要求。
2. **软偏好排序**：性价比、评价质量、店铺可靠性、物流时效、售后、优惠确定性、风险分。

输出不是“买这个”，而是可解释推荐：

```text
推荐 A：最符合预算且评价稳定，但降噪一般。
备选 B：价格略高但续航更好。
淘汰 C：价格符合，但店铺和评价风险较高。
```

这解决的是“为什么这个商品更值得买”的问题。

#### 2.3.4  Proactive Shopping Strategy：主动购物策略

专业购物助手不应只被动执行。它需要在以下节点主动介入：

- 需求不清时主动澄清：预算、使用场景、品牌、规格、配送时效。
- 搜索结果质量差时主动换关键词或平台。
- 候选商品都不满足时主动建议放宽约束。
- 发现优惠券、满减、预售、默认服务勾选时主动提醒。
- 发现低价但高风险商品时主动拒绝推荐。
- 购买前生成简短决策摘要并请求确认。

这解决的是“助手是否真的在帮用户购物，而不只是执行点击”的问题。

#### 2.3.5  Trust and Approval Boundary：信任与授权边界

购物助手必须明确区分建议权和执行权：

| 动作 | 系统权限 |
|---|---|
| 搜索、筛选、打开商品 | 可自动执行 |
| 收集商品证据、比较候选 | 可自动执行 |
| 加入购物车 | 需按用户设置，可默认确认 |
| 提交订单、支付、修改地址 | 必须用户显式确认 |
| 记忆用户偏好 | 低风险偏好可记录，高风险隐私需提示 |

这解决的是“主动性不能越权”的问题。

### 2.4  两篇论文在新主线中的位置

| 能力层 | 本项目主线 | MobileForge 的作用 | MemGUI-Agent 的作用 |
|---|---|---|---|
| GUI 操作底座 | 稳定使用购物 App | 通过 AMSG-Gym 提升陌生 App 适配 | 通过 VCA 降低长程上下文漂移 |
| 购物证据层 | 商品候选与证据账本 | 探索不同 App 的商品信息页面 | 记住价格、规格、评价等关键事实 |
| 决策层 | 多目标商品比较与推荐 | 不直接解决 | 不直接解决 |
| 主动策略层 | 澄清、换策略、风险提醒 | 可提供探索反馈 | 可保持长期任务上下文 |
| 安全授权层 | 建议权/执行权边界 | 不直接解决 | 不直接解决 |

因此，下一阶段的研究问题应是：

> 如何在稳定 GUI 操作底座上，构建一个证据驱动、偏好感知、风险可控、能主动提出购物策略的专业购物助手？

而 MobileForge 和 MemGUI-Agent 是支撑这个目标的底层机制，不是目标本身。

---

## 3  实施计划：以购物智能层为主线，图谱和记忆作为底座

### 3.1  总体路线

实施顺序应从购物任务本质出发：

```text
阶段一：购物意图与商品证据账本
  -> 让系统知道用户想买什么、看过哪些商品、证据是什么

阶段二：购物决策与主动策略
  -> 让系统能比较商品、解释推荐、主动澄清和提醒风险

阶段三：AMSG-Gym 与 VCA 强化底座
  -> 让系统能适应陌生购物 App，并稳定保持长程购物上下文

阶段四：训练数据导出与影子评估
  -> 仅把图谱和证据验证后的低风险经验作为候选数据
```

核心原则：**先做购物专业性，再做模型训练**。如果系统没有商品证据账本和购物决策引擎，训练只会让模型更会点 App，不会让它更会购物。

### 3.2  阶段一：Shopping Intent Graph 与 Product Evidence Ledger

新增模块建议：

```text
phone_agent/shopping/
  __init__.py
  intent.py
  evidence.py
  candidate.py
  criteria.py
  risk.py
```

核心数据结构：

```python
ShoppingIntent = {
    "category": str,
    "hard_constraints": dict,
    "soft_preferences": dict,
    "decision_criteria": list[str],
    "budget_policy": dict,
    "risk_tolerance": str,
    "clarification_questions": list[str],
    "approval_boundary": dict,
}

ProductCandidate = {
    "id": str,
    "title": str,
    "price_raw": str,
    "price_final": float | None,
    "specs": dict,
    "seller": dict,
    "rating": dict,
    "review_signals": dict,
    "shipping": dict,
    "after_sales": dict,
    "risk_flags": list[str],
    "evidence_refs": list[str],
    "match_status": "candidate|rejected|recommended",
    "reject_reason": str,
}
```

模块映射：

| 当前模块 | 升级方式 |
|---|---|
| `phone_agent/core/task_spec.py` | 扩展为购物意图 schema，不只保存预算和规格 |
| `phone_agent/session_memory_file.py` | 增加 `shopping_intent`、`product_candidates`、`decision_notes` |
| `phone_agent/memory/core/product.py` | 从单商品状态升级为候选商品池 |
| `phone_agent/core/spec_guard.py` | 从硬拦截升级为硬约束过滤器 |
| `phone_agent/tracer.py` | 保存商品证据截图引用 |

验收标准：

- 系统能在一个任务中维护至少 3 个候选商品。
- 每个候选商品都有价格、规格、店铺、证据来源和淘汰/保留理由。
- 预算和规格等硬约束由机械逻辑判断，不由模型自由判断。

### 3.3  阶段二：Shopping Decision Engine 与主动策略

新增模块建议：

```text
phone_agent/shopping/
  decision_engine.py
  strategy.py
  recommendation.py
  approval.py
```

决策流程：

```text
候选商品池
  -> 硬约束过滤
  -> 软偏好评分
  -> 风险扣分
  -> 生成推荐/备选/淘汰列表
  -> 生成用户可读的决策摘要
  -> 请求确认后才执行加购或后续高风险动作
```

评分不需要第一版就复杂。可以先做透明的规则加权：

| 维度 | 示例 |
|---|---|
| 价格 | 是否低于预算，是否有券后价不确定 |
| 规格匹配 | 是否满足降噪、续航、颜色、尺码等要求 |
| 店铺可信 | 官方店、旗舰店、自营、评分异常 |
| 评价质量 | 好评率、差评关键词、销量异常 |
| 物流售后 | 发货时间、退换政策、运费 |
| 风险 | 预售、默认服务、低价异常、规格不一致 |

主动策略触发器：

| 触发条件 | 系统行为 |
|---|---|
| 需求缺关键变量 | 主动澄清 1-2 个最高价值问题 |
| 搜索结果普遍不符合预算 | 建议放宽预算、换关键词或换平台 |
| 候选商品风险高 | 标记风险并拒绝直接推荐 |
| 两个候选各有优劣 | 给出 trade-off，而不是强行选一个 |
| 进入高风险动作前 | 输出决策摘要并请求确认 |

验收标准：

- 系统不是只返回“已加入购物车”，而能解释为什么选择这个商品。
- 至少能输出推荐、备选、淘汰三类结论。
- 高风险动作前必须有用户确认边界。

### 3.4  阶段三：AMSG-Gym 服务购物 App 泛化

AMSG-Gym 仍然重要，但它现在服务的是购物智能层，而不是替代购物智能层。

新增/升级模块：

```text
phone_agent/adaptation/
  amsg_gym.py
  curriculum.py
  critic.py
  quarantine.py
```

AMSG-Gym 的探索任务应围绕购物能力生成：

| 探索来源 | 任务示例 | 目标 |
|---|---|---|
| 商品信息缺口 | 找到商品详情页中的评价、店铺、售后入口 | 支持商品证据账本 |
| 价格结构不清 | 探索券后价、满减、预售价页面 | 支持价格证据校验 |
| SKU 流程不稳定 | 重复验证规格选择路径 | 支持规格约束判断 |
| 购物车/结算风险 | 探索但不提交订单 | 支持 approval boundary |
| 陌生 App 页面类型 | `new:<type>` 页面提案 | 支持 schema 演化 |

这时 MobileForge 的启发落在这里：真实 App 探索、任务挖掘、分层反馈都为购物证据和图谱修复服务。

### 3.5  阶段四：VCA 支撑长程购物上下文

Verified Context Actions 应和购物证据账本结合：

```text
remember_candidate
update_price
update_spec
reject_candidate
fold_search_phase
set_decision_focus
record_user_preference
```

提交规则：

| 动作 | 提交条件 |
|---|---|
| `update_price` | 必须来自机械价格解析或截图证据 |
| `update_spec` | 必须来自当前商品页或规格页证据 |
| `reject_candidate` | 必须给出违反的硬约束或风险证据 |
| `record_user_preference` | 低风险偏好可自动记，高风险隐私需确认 |
| `fold_search_phase` | 候选商品已写入账本后才能折叠搜索历史 |

MemGUI-Agent 的启发在这里：不是为了压缩历史而压缩，而是为了让购物证据持续可用。

### 3.6  实验设计

主要假设：

| 假设 | 验证方式 |
|---|---|
| H1: 商品证据账本能提升购物决策质量 | 对比无候选池 vs 有候选池的约束命中率、推荐解释完整性 |
| H2: 多目标决策引擎能降低错误推荐 | 统计预算越界、规格不符、风险商品推荐率 |
| H3: 主动澄清能提升任务成功与满意度 | 对比直接执行 vs 澄清关键变量后的任务完成率 |
| H4: AMSG-Gym 能提升陌生购物 App 冷启动覆盖 | 对比 schema-only 探索与 feedback-driven 探索 |
| H5: VCA 能降低长程购物事实丢失 | 测价格、规格、候选商品事实保留率 |

消融矩阵：

| 配置 | 商品证据账本 | 决策引擎 | 主动策略 | AMSG-Gym | VCA | 目标 |
|---|---:|---:|---:|---:|---:|---|
| Current | 否 | 否 | 否 | 部分 | 部分 | 当前操作型基线 |
| +Evidence | 是 | 否 | 否 | 部分 | 部分 | 验证候选商品池 |
| +Decision | 是 | 是 | 否 | 部分 | 部分 | 验证购物推荐质量 |
| +Proactive | 是 | 是 | 是 | 部分 | 部分 | 验证主动助手能力 |
| +AMSG-Gym | 是 | 是 | 是 | 是 | 部分 | 验证陌生 App 泛化 |
| Full Assistant | 是 | 是 | 是 | 是 | 是 | 完整专业购物助手 |

关键指标：

- 任务完成率仍然保留，但不再是唯一指标。
- 约束满足率：预算、品类、规格、店铺等硬约束是否满足。
- 推荐解释完整性：推荐是否有证据、备选、淘汰理由。
- 错误推荐率：是否推荐了越界、风险高或规格不符商品。
- 主动澄清收益：澄清后任务成功率和推荐质量是否提升。
- 用户确认前高风险动作次数：必须为 0。

### 3.7  两周 pilot

目标：不训练模型，先验证购物专业状态层是否能跑通。

任务：

1. 实现 `ShoppingIntent` schema。
2. 实现 `ProductCandidate` 与商品证据账本。
3. 扩展 `SessionMemoryFile`，保存购物意图、候选商品和决策摘要。
4. 在淘宝/京东各跑 5-10 个“比较后推荐”的购物任务。
5. 记录推荐、备选、淘汰理由。

成功标准：

- 每个任务至少能维护 2-3 个候选商品。
- 每个推荐都有价格、规格、店铺或风险证据。
- 系统能明确说出为什么淘汰某个商品。

### 3.8  一个月版本

目标：形成第一版主动购物助手。

任务：

1. 实现硬约束过滤和软偏好评分。
2. 实现主动澄清策略。
3. 实现购买前推荐摘要和确认边界。
4. 将 AMSG-Gym 的探索任务改为服务商品证据采集，例如评价页、优惠券页、店铺页、售后页。
5. 将 VCA 动作扩展为购物证据动作。

成功标准：

- 系统能在购物任务中主动提出澄清问题或风险提醒。
- 系统能输出推荐/备选/淘汰列表。
- 高风险动作前必须有明确用户确认。

### 3.9  三个月论文型版本

目标：形成可汇报、可投稿的垂直购物助手系统贡献。

任务：

1. 完成 Shopping Intelligence Layer：意图图谱、证据账本、决策引擎、主动策略、授权边界。
2. AMSG-Gym 支持陌生购物 App 的商品证据路径探索。
3. VCA 支持长程购物证据维护。
4. 在淘宝、京东、至少一个陌生购物或服务 App 上做跨 App 泛化实验。
5. 完成 Current / +Evidence / +Decision / +Proactive / +AMSG-Gym / Full Assistant 消融。

成功标准：

- Full Assistant 不仅任务完成率更高，还在约束满足率、推荐解释完整性、错误推荐率上优于当前系统。
- 陌生 App 中能更快找到商品信息、评价、优惠和售后入口。
- 用户确认前不触发提交订单、支付、地址修改等高风险动作。

---

## 附录  汇报时的核心表述

对不熟悉项目的人，推荐用这条主线讲：

1. **我们已有的基础**：Shopping-Agent 已经不是普通 GUI Agent，而是能稳定操作真实购物 App 的混合智能体。
2. **真实问题**：稳定操作 App 只是底座；购物助手的核心是理解需求、收集证据、比较商品、解释推荐和安全执行。
3. **两篇论文的位置**：MobileForge 启发 AMSG-Gym 做陌生 App 探索和分层反馈；MemGUI-Agent 启发 VCA 做长程购物证据管理。它们是支撑机制，不是研究目标。
4. **我们的垂直创新**：Shopping Intelligence Layer，包括购物意图图谱、商品证据账本、多目标决策引擎、主动购物策略和信任授权边界。
5. **最终目标**：从“会用购物 App 的 GUI 模型”升级为“主动、专业、可信的购物助手”。

一句话版本：

> 当前系统已经解决了如何稳定操作购物 App；下一阶段要解决的是如何专业地购物。我们将构建 Shopping Intelligence Layer，让 Agent 能理解购买意图、维护商品证据、做多目标比较、主动澄清和解释推荐；MobileForge/MemGUI 的思想分别作为陌生 App 探索和长程证据管理的底层支撑。

---

## 参考来源

- MobileForge: Annotation-Free Adaptation for Mobile GUI Agents with Hierarchical Feedback-Guided Policy Optimization, arXiv:2606.19930.
- MemGUI-Agent: An End-to-End Long-Horizon Mobile GUI Agent with Proactive Context Management, arXiv:2606.19926.
- `phone_agent/docs/ARCHITECTURE_CN.md`
- `phone_agent/docs/AMSG_DESIGN.md`