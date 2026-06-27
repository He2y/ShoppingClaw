# Shopping-Agent 的图谱-记忆协同自适应升级方案

> **版本**: 2026-06-27
> **用途**: 汇报展示文档。面向不熟悉本项目的人，先介绍现有系统设计，再说明 MobileForge 与 MemGUI-Agent 两篇最新工作的启发，最后落到本项目的实施计划。
> **依据**: `phone_agent/docs/ARCHITECTURE_CN.md`、`phone_agent/docs/AMSG_DESIGN.md`、`references/MobileForge.pdf`、`references/MemGUI-Agent.pdf`。

---

## 1  现有工作：Shopping-Agent 已经解决了什么

### 1.1  项目要解决的问题

Shopping-Agent 面向真实手机 App 自动化，当前重点场景是购物任务，例如在淘宝、京东中搜索商品、筛选规格、比较价格、加入购物车或推进到结算前检查。它面对的环境和网页自动化不同：移动端没有稳定 DOM，按钮和页面会受版本、广告、弹窗、登录态、设备分辨率、滚动位置影响，同一动作也可能产生不同后果。

项目的核心问题可以概括为：

> 如何让一个可端侧部署的小型 GUI 模型，在真实移动 App 的长程任务中达到接近强 VLM 的可靠性，同时保持低延迟、低云调用和可审计安全边界？

当前系统的回答不是把每一步都交给强 VLM，而是构建一个混合智能体：

```text
小模型高频执行
  + 强 VLM 低频里程碑监督
  + AMSG 空间图谱导航
  + 机械护栏反幻觉
  + 会话记忆文件抗遗忘
```

这套设计的关键是让不同类型的问题交给不同权威，而不是让一个模型包办所有判断。

### 1.2  非对称权威：系统的第一性原则

`ARCHITECTURE_CN.md` 中最重要的设计原则是**非对称权威**。它把移动 GUI 自动化中的决策拆成四类：

| 决策类型 | 当前权威 | 代码位置 | 为什么这样分工 |
|---|---|---|---|
| 当前屏幕 grounding | 小型 GUI 模型 | `phone_agent/model/`, `phone_agent/actions/` | 每步都要做，必须低延迟；模型擅长看当前截图找元素 |
| 任务拆解与计划修订 | 强 VLM 里程碑监督 | `phone_agent/milestone_supervisor.py`, `phone_agent/task_plan.py` | 语义规划能力强，但只应低频调用 |
| 数字、约束、完成证据 | 机械护栏 | `phone_agent/core/spec_guard.py`, `phone_agent/verification_detector.py` | 价格、预算、完成判定不应交给模型幻觉 |
| 结构化导航复用 | AMSG 空间图谱 | `phone_agent/spatial/`, `phone_agent/memory/graph_store.py` | 首页到搜索、搜索到结果等机械转移可复用 |
| 单任务抗遗忘 | 会话记忆文件 | `phone_agent/session_memory_file.py` | 长程任务需要原始目标、已验证事实和子任务进度 |

这个原则解决了小模型在真机中暴露的典型问题：

- **焦点丢失**：长任务中反复重述计划、原地打转。
- **数字幻觉**：把超预算商品判断为符合预算。
- **伪完成**：没有真正加购或到达目标页就宣布完成。
- **空响应**：关键步骤输出不可解析内容。
- **图谱污染**：把失败轨迹或过期坐标写入可执行导航。

因此，本项目的系统创新不是一个更复杂 prompt，而是一套决策权分配机制：语义问题低频交给强 VLM，确定性问题交给代码，当前屏幕 grounding 留给小模型，结构化导航交给图谱。

### 1.3  Agent 执行闭环

当前主循环入口是 `PhoneAgent.run(task)`，核心编排在 `PhoneAgent._execute_step_impl()`。每个任务大致经过以下闭环：

```text
用户任务
  -> 初始化任务计划与会话记忆文件
  -> 截图与页面分类
  -> AMSG 定位当前页面并给出图谱上下文
  -> 里程碑触发器决定是否调用强 VLM 修订计划
  -> 三档调度选择 Fast Path / Graph Co-pilot / 小模型推理
  -> ActionHandler 执行动作
  -> 下一步截图验证上一步后条件
  -> 更新图谱边生命周期、会话状态和轨迹
  -> 完成门与 final_confirm 判定任务是否真正结束
```

这条闭环有三个特点：

1. **每步都有反馈**：动作执行后不会立即假设成功，而是在下一步用实际页面状态验证后条件。
2. **完成有单一权威**：完成声明必须通过机械证据或强 VLM 截图核实，避免小模型污染结束判定。
3. **失败可恢复**：空响应、完成被否决、后条件不匹配时，会重置污染上下文并回退到更稳妥路径。

### 1.4  AMSG：主动移动空间图谱

`AMSG_DESIGN.md` 介绍的 AMSG 是当前系统区别于普通 ReAct Agent 的关键模块。它不是静态页面图，而是一个会随真实执行持续更新的动作库。

AMSG 存储的是语义状态与语义动作，而不是截图哈希和绝对坐标：

```text
PageState = (app, page_type, landmarks, affordances, slots, risk_level)
Action = (type, intent, semantic_target, postcondition, lifecycle_stage)
```

图谱中的基本结构是：

```text
(UIState) -[NEXT_ACTION]-> (Action) -[PRODUCES]-> (UIState)
```

AMSG 有四个核心机制：

| 机制 | 作用 | 对用户体验的影响 |
|---|---|---|
| 语义去重 | 把同类页面合并，例如不同关键词的搜索结果页都归为 `search_result` | 图谱可复用，不因内容变化爆炸 |
| 边生命周期 | 跟踪每条转移的成功率、失败率、优势比和阶段 | 只有可靠边才可被自动复用 |
| 锚定性分类 | 区分图谱可独立执行的边和必须让 VLM/模型看屏幕的边 | 减少不必要模型调用，同时避免过期坐标误点 |
| 后条件验证 | 在 t+1 步验证 t 步动作是否达到预期页面 | 图谱学习基于真实结果，不靠假设 |

AMSG 的运行时入口是 `GraphRuntimeController.locate_and_get_context()`，它返回 `navigate`、`explore`、`verify_with_vlm`、`goal_reached` 等模式，直接影响后续调度。

### 1.5  三档调度：速度和可靠性的折中

当前系统不是“始终模型推理”，也不是“始终相信图谱”，而是按图谱边的可靠性和锚定性做三档调度：

| 档位 | 使用条件 | 行为 |
|---|---|---|
| Fast Path | promoted、锚定、低风险、有坐标或复合动作 | 跳过模型，直接执行机械导航 |
| Graph Co-pilot | 图谱知道方向但目标需看屏幕 | 图谱提供 `graph_hint`，小模型负责 grounding |
| Model Path | 图谱未知、非锚定、高风险或验证失败 | 完整模型推理 |

这套调度让系统在稳定路径上变快，在内容敏感路径上保持谨慎。例如首页到搜索框可以复用图谱，商品选择、SKU 选择、结算前检查仍必须看当前屏幕。

### 1.6  会话记忆与机械护栏

长程购物任务中，“记住什么”和“不让模型判断什么”同样重要。

当前会话记忆文件 `SessionMemoryFile` 保存：

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

这部分设计的用户价值很直接：不让模型用自然语言“猜”价格、猜完成、猜安全状态，而是把高风险判断变成可审计规则。

### 1.7  当前系统的不足

现有工作已经建立了可靠的执行和图谱自进化基础，但如果面向下一阶段研究和论文贡献，仍有四个缺口：

1. **陌生 App 探索还不够反馈驱动**。当前已有 onboarding、焦点探索、staging、人工审核和在线学习管线，但探索目标主要来自 schema 覆盖与人工设定；低置信边、高熵转移、失败簇还没有系统反向驱动下一轮探索。
2. **经验主要进入图谱边，还没有形成统一反馈账本**。成功任务会更新 AMSG，但失败任务中的局部正确步骤、失败原因、干扰态和纠错 hint 还没有以统一 schema 沉淀。
3. **上下文管理仍偏工程规则**。会话记忆有效，但模型还没有学习“什么时候记、什么时候更新、什么时候折叠历史”。
4. **训练不是当前最高收益方向**。购物 App 更新频繁、页面动态大、风险动作多；对单个 App 做强化学习容易过拟合当前版本，回报率不稳定，也会引入安全成本。

这意味着 MobileForge 对本项目最有价值的不是 GRPO 训练结论，而是 MobileGym 式的目标 App 探索、可执行任务挖掘和分层反馈；MemGUI-Agent 的价值则在于把长程上下文管理变成可验证、可学习的动作。

---

## 2  最新研究启发：把论文创新落到本系统模块中

> 注：截至 2026-06-27 核对，MobileForge 与 MemGUI-Agent 在 arXiv 上均为 2026-06-18 提交的 v1 版本。MobileForge 聚焦无标注目标 App 适配，MemGUI-Agent 聚焦长程任务上下文管理。

### 2.1  MobileForge 的核心创新

MobileForge 解决的问题是：移动 App 数量多、更新快，人工写任务、专家轨迹和人工 reward 标签无法覆盖真实目标 App。论文提出了一个 annotation-free adaptation 系统：

```text
真实 App 探索
  -> 任务课程生成
  -> 多次 rollout
  -> 层级评估
  -> 从反馈中筛选 step
  -> hint-contextualized GRPO 更新策略
```

但对 Shopping-Agent 来说，必须拆开看 MobileForge 的贡献：

- **MobileGym 更值得迁移**：它把真实目标 App 中的探索、可执行任务挖掘、rollout 评估和分层反馈组织成一个闭环。
- **HiFPO/GRPO 不是当前主线**：购物 App 更新频繁、风险动作多、回报稀疏，对 App-specific policy 做强化学习容易过拟合当前版本，维护成本高。

因此，这篇论文对本项目的核心启发应改写为：

> 真实 App 执行产生的经验，不应优先压进模型权重，而应先压进可审计、可降级、可迁移的 AMSG 图谱与探索反馈系统。

### 2.2  MobileForge 对 Shopping-Agent 的具体映射：AMSG-Gym

Shopping-Agent 已经有 AMSG，因此不需要重新造一个 MobileGym。更合理的映射是把 MobileGym 的思想改造成 **AMSG-Gym**：

> AMSG-Gym 是面向陌生 App 的图谱原生探索与分层反馈系统。它的目标不是优先训练 policy，而是冷启动和修复 AMSG：发现页面、验证转移、挖掘可执行任务、生成纠错 hint，并推动 schema 与边生命周期演化。

| MobileForge / MobileGym 概念 | 本系统已有基础 | 应落到的模块 | 升级方式 |
|---|---|---|---|
| Target-app exploration | 陌生 App 建图管线 | `phone_agent/memory/exploration/` | 由 schema 覆盖扩展为图谱缺口、低置信边、高熵转移、失败簇共同驱动 |
| Curriculum mining | `task_builder.py`, schema coverage | `phone_agent/memory/exploration/task_builder.py` | 生成“补图谱”的可执行任务，而不是默认生成训练任务 |
| Rollout execution | `PhoneAgent.run()` | `phone_agent/agent.py` | 在安全策略下执行探索任务并产出 feedback trace |
| Hierarchical rollout evaluation | 后条件验证、SpecGuard、final_confirm、TrajectoryReviewer | `phone_agent/feedback/`, `phone_agent/spatial/trajectory_reviewer.py` | 汇总为任务级、转移级、step 级反馈 |
| Corrective hints | 里程碑 checkpoint 修订 | `phone_agent/milestone_supervisor.py`, `phone_agent/spatial/action_advisor.py` | 指导下一轮探索和图谱提示，而不是直接强化模型 |
| Multi-attempt rollout | 边生命周期重复验证 | `phone_agent/spatial/edge_lifecycle.py` | 对低置信/高熵边做安全重复验证，提升、降级或分裂边 |
| Training export | 当前暂无统一出口 | `phone_agent/adaptation/dataset_exporter.py` | 作为图谱验证后的副产品导出，不作为第一阶段目标 |

AMSG-Gym 的主闭环应是：

```text
陌生 App 探索
  -> 挖掘可执行任务
  -> 安全 rollout
  -> 分层反馈
  -> 更新 AMSG 图谱 / schema / 边生命周期 / 探索策略
```

这与 MobileForge 的差别很关键：MobileForge 的主线是“探索真实 App -> 生成数据 -> 训练模型”；本项目更适合的主线是“探索真实 App -> 生成反馈 -> 更新 AMSG 图谱 -> 改善执行与下一轮探索”。

### 2.3  本项目不应照搬 MobileForge 的地方

MobileForge 的多尝试和 GRPO 更适合 benchmark 或安全隔离环境。Shopping-Agent 面向真实购物 App，不能直接把生产环境变成在线强化学习场：

- **App 更新导致过拟合风险高**：强化学习可能学到某个版本、某些弹窗、某些布局下的动作偏好，App 改版后收益快速衰减。
- **回报稀疏且归因困难**：购物链路长，失败可能来自登录态、弹窗、库存、价格、SKU、网络或页面分类，单一 reward 很难稳定归因。
- **高风险动作不适合探索式优化**：登录、地址、下单、支付、提交订单等动作必须由规则和人工边界控制。
- **模型权重不如图谱容易降级**：AMSG 的边可以 promoted/demoted；模型一旦学到过期偏好，回滚和解释都更难。

因此本项目应采用更稳的路线：

```text
生产运行和安全探索产出反馈
  -> 失败经验进入 quarantine
  -> 低风险局部经验用于修复图谱和 schema
  -> 训练数据作为副产品离线导出
  -> 仅在影子评估收益明确时才更新模型或提示协议
```

这也是本项目可以形成差异化创新的地方：**图谱优先的无标注适配**，而不是 App-specific policy 的强化学习适配。

### 2.4  MemGUI-Agent 的核心创新

MemGUI-Agent 解决的问题是：长程 GUI 任务中，ReAct 式逐步追加历史会让 prompt 越来越长，同时关键事实被稀释、截断或遗忘。它提出 Context-as-Action，即把上下文管理作为模型动作的一部分。

它维护三类结构化上下文：

- Folded Action History：压缩后的历史行动。
- Folded UI State：持久 UI 事实。
- Recent Step Record：最近一步的观察、意图、动作、结果。

每步模型不仅输出 UI 动作，还输出 history folding、memory update、UI observation、action intent。它的关键启发是：

> 长程 GUI Agent 的记忆管理不应只是外部日志压缩，而应成为可学习、可评估、可消融的策略行为。

### 2.5  MemGUI-Agent 对 Shopping-Agent 的具体映射

本项目已经有 `SessionMemoryFile`，所以不需要照搬完整五段式 ConAct。更适合的升级是 **Verified Context Actions (VCA)**：让模型、监督者或机械护栏都可以提出上下文动作，但提交权仍由系统校验。

| MemGUI-Agent 概念 | 本系统已有基础 | 应落到的模块 | 升级方式 |
|---|---|---|---|
| Folded Action History | 历史压缩 currently no-op、StepRecord | `phone_agent/session_memory_file.py`, `phone_agent/memory/core/step_record.py` | 增加 `folded_trace`，按图谱 span 折叠 |
| Folded UI State | `verified_facts` | `phone_agent/session_memory_file.py` | 增加带证据来源的事实提交机制 |
| Recent Step Record | `StepRecord` | `phone_agent/memory/core/step_record.py` | 增加 `ui_observation`, `action_intent`, `postcondition_result` |
| Memory actions | 当前由规则/监督者写入 | `phone_agent/context/actions.py`, `phone_agent/context/committer.py` | 引入 `remember/update/delete/fold/set_focus` |
| Learnable context management | 当前暂无训练出口 | `phone_agent/adaptation/dataset_exporter.py` | 导出上下文动作 SFT 样本 |

### 2.6  本项目不应照搬 MemGUI-Agent 的地方

MemGUI-Agent 让模型直接输出上下文管理字段，但本项目的小模型主执行器已经暴露过空响应、伪完成、数字幻觉等问题。直接让小模型写记忆会带来新风险：

- 错误价格、错误商品、错误规格进入长期上下文。
- 临时弹窗文本被当成任务事实。
- 模型为了输出复杂格式牺牲当前屏幕 grounding。

因此，本项目的设计应是：

```text
模型可以提出 ContextActionIR
  -> ContextCommitter 校验证据
  -> 高风险事实必须由机械护栏或强 VLM 确认
  -> 通过后才写入 SessionMemoryFile
```

这保持了当前系统的非对称权威：模型可以建议记忆，但不能独占事实写入权。

### 2.7  两篇论文合起来给本项目的研究方向

MobileForge 和 MemGUI-Agent 分别补齐两个维度：

| 维度 | MobileForge | MemGUI-Agent | 本项目升级 |
|---|---|---|---|
| 陌生 App 适配 | MobileGym 在真实 App 中探索、挖掘任务、产生反馈 | 较少涉及图谱和探索课程 | AMSG-Gym：图谱原生探索与分层反馈底座 |
| 上下文管理 | 使用 hint 指导多次 rollout | 把上下文管理作为动作 | VCA：可验证上下文动作 |
| 失败利用 | 从失败中筛局部合理 step | 关注长程记忆失败 | 风险敏感 local-positive recovery，用于图谱修复优先 |
| 安全边界 | benchmark 适配为主 | 长程基准为主 | 购物场景下的高风险动作隔离 |
| 模型训练 | HiFPO/GRPO 是主线 | SFT 学习 ConAct | 本项目中训练是副产品，主线是图谱自进化 |

因此，下一阶段系统可以定义为：

> **Feedback-Grounded Memory-Spatial Agent**：把真实移动 GUI 交互中的空间转移、上下文事实、失败反馈和纠错经验统一沉淀为可执行图谱、可验证记忆和可审计探索策略；训练数据是该闭环的副产品，而不是第一目标。

这不是另起炉灶，而是在现有 Shopping-Agent 上增加三个可落地层：

1. **Feedback Trace**：统一记录每步行为的 outcome、process feedback、guardrail verdict、context action 和 corrective hint。
2. **Verified Context Actions**：把记忆写入、更新、删除、历史折叠变成带证据的事务。
3. **AMSG-Gym**：从图谱覆盖缺口、低置信边、高熵转移、失败簇和反馈账本中生成探索任务，并更新 AMSG 图谱、schema 与边生命周期。

---

## 3  实施计划：从可观测反馈到账本化自适应

### 3.1  总体路线

实施顺序必须遵守一个原则：**先让经验可观测，再让经验修复图谱，最后才考虑训练**。如果没有统一反馈账本和图谱质量门，直接做 GRPO 或上下文动作训练只会放大日志噪声，也容易把 App 当前版本的偶然路径写进模型权重。

推荐三阶段路线：

```text
阶段一：Feedback Trace + VCA pilot
  -> 让每步反馈和记忆提交可审计

阶段二：AMSG-Gym + Hierarchical Critic
  -> 让图谱缺口、失败簇、低置信边、高熵转移驱动陌生 App 探索和图谱修复

阶段三：Dataset Export + Shadow Evaluation
  -> 仅把图谱验证后的低风险经验导出为训练候选，离线评估收益后再决定是否训练
```

换句话说，当前版本的主目标不是“训练一个更懂某个 App 的模型”，而是“建设一个能随 App 变化自修复的图谱-反馈系统”。

### 3.2  阶段一：统一反馈账本

新增模块：

```text
phone_agent/feedback/
  __init__.py
  schema.py
  trace.py
  exporters.py
```

每步写入 `FeedbackEvent`：

```python
FeedbackEvent = {
    "session_id": str,
    "step": int,
    "app": str,
    "page_type": str,
    "task": str,
    "ui_action": dict,
    "graph_mode": "navigate|explore|verify_with_vlm|goal_reached",
    "postcondition": {"expected": str, "actual": str, "passed": bool},
    "guardrail_verdicts": list[dict],
    "milestone_feedback": dict,
    "context_actions": list[dict],
    "outcome_label": "unknown|success|failure|partial",
    "process_label": "reasonable|unreasonable|risky|unknown",
    "corrective_hint": str,
    "risk_level": "low|normal|high",
    "evidence_refs": list[str],
}
```

模块改动：

| 文件 | 改动 |
|---|---|
| `phone_agent/agent.py` | 在 `_execute_step_impl()` 每步结束时写入 `FeedbackEvent` |
| `phone_agent/tracer.py` | 关联截图、模型输出、动作 IR、后条件结果 |
| `phone_agent/spatial/runtime_controller.py` | 输出图谱 mode、pending transition 验证结果 |
| `phone_agent/core/spec_guard.py` | 输出结构化 guardrail verdict |
| `phone_agent/milestone_supervisor.py` | checkpoint/final_confirm 进入同一反馈账本 |

验收标准：

- 每个任务结束后生成 `feedback_trace.jsonl`。
- 成功和失败任务都保留 trace。
- 默认只有成功任务进入图谱持久化；失败任务只进入 quarantine 候选池。

### 3.3  阶段一并行：Verified Context Actions

新增模块：

```text
phone_agent/context/
  __init__.py
  actions.py
  committer.py
  renderer.py
```

上下文动作 IR：

```python
ContextActionIR = {
    "type": "remember|update_fact|delete_fact|fold_history|set_focus",
    "target": str,
    "content": str,
    "evidence": list[str],
    "risk": "low|normal|high",
    "proposed_by": "model|milestone|mechanical|graph",
}
```

提交规则：

| 动作 | 提交条件 |
|---|---|
| `remember(product)` | 当前截图或商品抽取器能定位同一商品 |
| `remember(price)` | 机械价格解析成功，禁止只凭模型文本写入 |
| `update_fact(cart_added)` | 后条件或 final_confirm 证据通过 |
| `fold_history` | span 内没有 risky step，且子任务已完成或页面转移已验证 |
| `delete_fact` | 只允许删除低风险临时事实，高风险事实需监督者确认 |
| `set_focus` | 与 `TaskPlan` 当前子任务或图谱目标一致 |

模块改动：

| 文件 | 改动 |
|---|---|
| `phone_agent/session_memory_file.py` | 增加 `context_actions`、`folded_trace`、`recent_evidence` |
| `phone_agent/memory/core/step_record.py` | 增加 `ui_observation`、`action_intent`、`postcondition_result` |
| `phone_agent/model/protocol_bridge.py` | 兼容解析模型可选上下文动作块 |
| `phone_agent/config/prompts*.py` | 增加轻量上下文动作提示，不强制所有模型输出 |
| `phone_agent/memory/retrieval_gateway.py` | 从 VCA 状态渲染上下文，减少长历史拼接 |

第一版不要求小模型输出 VCA，可先由机械护栏和里程碑监督产生上下文动作。这样风险低，也能先验证数据结构是否有用。

### 3.4  阶段二：AMSG-Gym 陌生 App 探索

新增模块：

```text
phone_agent/adaptation/
  __init__.py
  amsg_gym.py
  curriculum.py
  critic.py
  quarantine.py
  dataset_exporter.py
```

AMSG-Gym 的课程不应只由 LLM 扩写，而应从图谱和执行反馈中产生：

| 探索来源 | 生成任务示例 | 对应模块 | 图谱目标 |
|---|---|---|---|
| 图谱覆盖缺口 | 覆盖 `search_result -> filter_panel -> search_result` | `schema_registry.py`, `task_builder.py` | 新增缺失转移 |
| 低置信边 | 重复验证某 App 的 `cart -> checkout` 前置流程 | `edge_lifecycle.py` | 提升、降级或标记需人工 |
| 高熵转移 | 同一动作产生登录/弹窗/目标页多结果 | `edge_lifecycle.py`, `runtime_controller.py` | 分裂条件边，补充前置条件 |
| 失败簇 | 多次卡在 SKU 选择或筛选面板 | `feedback/trace.py`, `adaptation/curriculum.py` | 生成纠错 hint 和探索焦点 |
| 用户高频槽位 | 耳机、手机、外卖等高频意图 | `memory/task_index.py` | 优先覆盖高价值链路 |
| 新词表提案 | `new:<type>` 页面 | `schema_registry.py` | 推动 schema proposal |

课程生成结果必须包含来源解释，并默认带安全策略：

```json
{
  "task": "在京东中搜索一个低价蓝牙耳机并进入筛选面板",
  "source": "low_confidence_edge",
  "evidence": ["edge:search_result->filter_panel", "confidence:0.42"],
  "graph_goal": "verify_or_demote_edge",
  "risk_policy": "no_checkout",
  "target_modules": ["PageClassifier", "ActionAdvisor", "EdgeLifecycle"]
}
```

AMSG-Gym 的产物优先级如下：

1. 更新页面类型、干扰态、schema proposal。
2. 更新边的成功率、失败率、高熵分支和生命周期阶段。
3. 生成下一轮探索 hint。
4. 在低风险、证据完整时导出训练候选样本。

### 3.5  阶段二：层级评估器

当前系统已有多个反馈源，但需要统一成 `HierarchicalCritic`，用于服务图谱修复和探索调度：

| 层级 | 信号来源 | 输出 | 默认用途 |
|---|---|---|---|
| Trajectory outcome | final_confirm、任务成功状态、完成门 | success/failure/partial | 判断整条探索是否可信 |
| Transition outcome | 后条件验证、边生命周期 | passed/failed/branched/high_entropy | 更新 AMSG 边生命周期 |
| Step process | 后条件验证、VLM step review | reasonable/unreasonable/risky | 从失败轨迹回收低风险局部步骤 |
| Constraint verdict | SpecGuard、价格裁决、提交拦截 | safe/unsafe/violated | 拦截高风险样本 |
| Context verdict | VCA 提交/拒绝原因 | useful/noisy/wrong | 修复会话记忆策略 |
| Corrective hint | 里程碑监督、失败聚类、VLM reviewer | 下次探索应避免和应尝试的策略 | 注入下一轮 AMSG-Gym 任务 |

实现策略：

- 普通 step 优先使用机械信号，减少强 VLM 调用。
- 只有失败关键点、高价值探索任务和候选样本调用强 VLM 生成 hint。
- 高风险动作永远不因自动 critic 结论直接进入训练正样本或 promoted 图谱。
- 失败轨迹默认不入图；只有通过后条件验证的低风险局部转移可以进入 quarantine，等待复验。

### 3.6  阶段三：数据导出与影子评估

训练数据导出应被定位为 AMSG-Gym 的副产品，而不是阶段三的唯一目标。优先导出三类数据：

**图谱修复样本**：用于复验低置信边、解释边降级、辅助人工审核。

```json
{
  "source": "low_confidence_edge",
  "edge": "search_result->filter_panel",
  "evidence": ["postcondition_failed", "actual:ad_dialog"],
  "suggested_action": "split_branch_or_demote"
}
```

**SFT 候选样本**：只来自成功轨迹或失败轨迹中后条件通过的低风险局部 step。

```json
{
  "prompt": "task + screenshot + VCA context + graph_hint",
  "response": {
    "ui_action": "...",
    "context_actions": [],
    "action_intent": "...",
    "ui_observation": "..."
  },
  "source": "success_trace|verified_local_positive",
  "evidence": []
}
```

**RFT/GRPO 候选样本**：长期可选，仅在离线回放和影子评估证明有收益时启用。

```json
{
  "state": "hint-contextualized step prompt",
  "target_action": "...",
  "reward_components": {
    "action_type": 0.0,
    "arguments": 0.0,
    "postcondition": 0.0,
    "context_action": 0.0,
    "safety": 0.0
  }
}
```

上线前必须经过：

1. 图谱修复收益验证：promoted 边误用率下降，demotion 更及时。
2. 离线格式校验。
3. 回放环境或 dry-run 验证。
4. 真实 App 影子评估。
5. 与当前模型做任务成功率、强 VLM 调用次数、错误事实写入率对比。

### 3.7  实验设计

主要假设：

| 假设 | 验证方式 |
|---|---|
| H1: AMSG-Gym 能提升陌生 App 冷启动覆盖 | 对比 schema-only 探索与 feedback-driven 探索的页面/转移覆盖率 |
| H2: 高熵边重复验证能提升图谱自修复 | 统计 promoted 边误用率、demotion 延迟、条件分支识别率 |
| H3: 图谱驱动课程优于纯 LLM 扩写课程 | 对比 coverage/failure/entropy curriculum 与 prompt-only expansion |
| H4: VCA 能降低长程任务上下文漂移 | 对比当前会话记忆与 VCA，测关键事实保留率和 token 增长 |
| H5: 训练候选作为副产品仍可产生价值 | 只在图谱验证样本上做小规模 SFT/影子评估，比较收益是否超过维护成本 |

消融矩阵：

| 配置 | VCA | AMSG-Gym 探索 | 高熵/低置信边复验 | 训练导出 | 目标 |
|---|---:|---:|---:|---:|---|
| Current | 否 | 否 | 否 | 否 | 当前基线 |
| +VCA | 是 | 否 | 否 | 否 | 验证上下文动作 |
| +AMSG-Gym | 否 | 是 | 是 | 否 | 验证陌生 App 图谱构建 |
| +Local Recovery | 否 | 是 | 是 | 候选 | 验证失败轨迹局部回收 |
| Full Graph-First | 是 | 是 | 是 | 候选 | 完整图谱优先系统 |
| RL/SFT Optional | 是 | 是 | 是 | 是 | 训练收益上界，不作为默认路线 |

### 3.8  两周 pilot

目标：不训练模型，只验证反馈账本、VCA 和 AMSG-Gym 任务来源是否能改善可观测性。

任务：

1. 实现 `FeedbackEvent` JSONL。
2. 扩展 `SessionMemoryFile`，增加 `context_actions` 和 `folded_trace`。
3. 由机械护栏和里程碑监督先产生 VCA。
4. 从低置信边、高熵转移和失败簇中生成 10-20 个 AMSG-Gym 探索任务。
5. 在淘宝/京东各跑 10 个长程购物任务，另选一个陌生 App 做小规模冷启动探索。
6. 统计关键事实保留率、错误事实写入率、图谱覆盖率、边降级次数和强 VLM 调用次数。

成功标准：

- 每步能追溯到截图、动作、后条件和记忆提交结果。
- 每个 AMSG-Gym 任务都有图谱来源解释和安全策略。
- 至少发现 3 类当前日志无法解释的图谱或上下文失败原因。

### 3.9  一个月版本

目标：让 AMSG-Gym 的陌生 App 探索、低置信边复验和失败局部回收跑通。

任务：

1. 实现 `CurriculumMiner`，从图谱缺口、低置信边、高熵转移、失败簇生成探索任务。
2. 实现 `HierarchicalCritic` 第一版。
3. 实现失败轨迹 local-positive step quarantine，但默认不进入 promoted 图谱。
4. 实现 schema proposal：把 `new:<type>` 页面和新干扰态送入人工审核。
5. 导出图谱修复样本和 SFT 候选 JSONL，先做格式验证，不急于训练。

成功标准：

- 每个探索任务都有可解释来源和风险策略。
- 低置信边能被复验、降级、分裂或提升。
- 失败轨迹中的可回收 step 不进入 promoted 图谱，只进入 quarantine 或训练候选池。
- 导出样本能复现当时 prompt、截图、动作和证据。

### 3.10  三个月论文型版本

目标：形成可汇报、可投稿的图谱优先自适应系统贡献。

任务：

1. 小模型支持可选 VCA 输出块。
2. AMSG-Gym 支持陌生 App 的自动探索、分层反馈、schema proposal 和边生命周期修复。
3. 在淘宝、京东、至少一个陌生购物或服务 App 上做跨 App 泛化实验。
4. 完成 Current / +VCA / +AMSG-Gym / +Local Recovery / Full Graph-First / RL-SFT Optional 消融。
5. WebUI 增加反馈审计和图谱修复面板。
6. 可选训练一个轻量 SFT checkpoint，作为训练收益上界而不是系统主贡献。

成功标准：

- Full Graph-First 在陌生 App 冷启动覆盖、promoted 边可靠性、强 VLM 调用次数或关键事实保留率上显著优于当前系统。
- VCA 错误事实写入率可控。
- AMSG-Gym 相比纯 LLM 扩写在冷启动覆盖或图谱修复效率上更强。
- 可选训练若无明显收益，也可作为负结果支持“图谱优先比 App-specific RL 更经济”的论文论点。

---

## 附录  汇报时的核心表述

对不熟悉项目的人，推荐用这条主线讲：

1. **我们已有的基础**：Shopping-Agent 不是普通 ReAct Agent，而是小模型执行、强 VLM 低频监督、AMSG 图谱导航、机械护栏和会话记忆组成的混合系统。
2. **我们对 MobileForge 的重新定位**：它最值得借鉴的是 MobileGym 的陌生 App 探索、任务挖掘和分层反馈，而不是把 GRPO 当成当前系统主线。
3. **我们看到的新机会**：MemGUI-Agent 说明长程任务的上下文管理应该成为可学习动作；AMSG-Gym 则让真实 App 交互先修复图谱，再把训练数据作为副产品。
4. **我们的落地创新**：形成 `Feedback Trace + Verified Context Actions + AMSG-Gym`，把适应性放在可审计、可降级的图谱和记忆系统中。
5. **我们的安全边界**：失败轨迹不直接入图，高风险事实不由模型直接写入，App-specific 训练只作为离线可选项。

一句话版本：

> Shopping-Agent 已经解决了真实手机购物任务中的可靠执行问题；下一阶段不是优先对频繁变化的购物 App 做强化学习，而是把 MobileGym 式探索落到 AMSG 中。我们将用反馈账本统一每步经验，用可验证上下文动作管理长程记忆，用 AMSG-Gym 让陌生 App 探索、任务挖掘、分层反馈和图谱自修复形成闭环；训练数据只是这个闭环的副产品。

---

## 参考来源

- MobileForge: Annotation-Free Adaptation for Mobile GUI Agents with Hierarchical Feedback-Guided Policy Optimization, arXiv:2606.19930.
- MemGUI-Agent: An End-to-End Long-Horizon Mobile GUI Agent with Proactive Context Management, arXiv:2606.19926.
- `phone_agent/docs/ARCHITECTURE_CN.md`
- `phone_agent/docs/AMSG_DESIGN.md`