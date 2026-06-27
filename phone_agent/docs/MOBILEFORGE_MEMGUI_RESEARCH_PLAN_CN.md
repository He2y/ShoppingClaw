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

1. **经验主要进入图谱，还没有充分进入模型训练数据**。成功任务会更新 AMSG，但失败任务中的局部正确步骤、失败原因和纠错 hint 还没有系统沉淀。
2. **反馈信号分散**。后条件验证、里程碑监督、SpecGuard、final confirm、轨迹审核都在产生反馈，但缺少统一的 step-level feedback schema。
3. **上下文管理仍偏工程规则**。会话记忆有效，但模型还没有学习“什么时候记、什么时候更新、什么时候折叠历史”。
4. **课程生成还不够主动**。陌生 App 探索已有管线，但日常任务中的低置信边、高熵页面、失败簇还没有反过来生成适配任务。

这四个缺口正好对应 MobileForge 与 MemGUI-Agent 两篇新工作的启发。

---

## 2  最新研究启发：把论文创新落到本系统模块中

> 注：截至 2026-06-27 核对，MobileForge 与 MemGUI-Agent 在 arXiv 上均为 2026-06-18 提交的 v1 版本。MobileForge 聚焦无标注目标 App 适配，MemGUI-Agent 聚焦长程任务上下文管理。

### 2.1  MobileForge 的核心创新

MobileForge 解决的问题是：移动 App 数量多、更新快，人工写任务、专家轨迹和人工 reward 标签无法覆盖真实目标 App。它提出一个 annotation-free adaptation 系统：

```text
真实 App 探索
  -> 任务课程生成
  -> 多次 rollout
  -> 层级评估
  -> 从反馈中筛选 step
  -> hint-contextualized GRPO 更新策略
```

它的两个关键组件是：

- **MobileGym**：作为交互和评估底座，负责目标 App 探索、课程生成、rollout 评估。
- **HiFPO**：把多次尝试的结果、step-level process feedback 和 corrective hints 转成可训练的策略优化信号。

这篇论文对本项目的核心启发不是“照搬 GRPO”，而是：

> 真实 App 执行产生的经验，不应该只作为日志或图谱边保存，还应该被组织成任务课程、层级反馈和可训练样本。

### 2.2  MobileForge 对 Shopping-Agent 的具体映射

Shopping-Agent 已经有 AMSG，因此不需要重新造一个 MobileGym。更合理的映射是把 AMSG 升级成 **AMSG-Forge**：

| MobileForge 概念 | 本系统已有基础 | 应落到的模块 | 升级方式 |
|---|---|---|---|
| Target-app exploration | 陌生 App 建图管线 | `phone_agent/memory/exploration/` | 从覆盖目标扩展到失败簇、低置信边、高熵页面驱动 |
| Curriculum mining | `task_builder.py`, schema coverage | `phone_agent/memory/exploration/task_builder.py` | 生成带来源解释的课程任务 |
| Rollout execution | `PhoneAgent.run()` | `phone_agent/agent.py` | 任务执行产出统一 feedback trace |
| Hierarchical critic | 后条件验证、SpecGuard、final_confirm、TrajectoryReviewer | `phone_agent/feedback/`, `phone_agent/spatial/trajectory_reviewer.py` | 汇总为 outcome/process/hint 三层反馈 |
| Corrective hints | 里程碑 checkpoint 修订 | `phone_agent/milestone_supervisor.py` | 失败任务生成可复用纠错 hint |
| Step selection | 边生命周期与后条件验证 | `phone_agent/spatial/edge_lifecycle.py` | 从失败轨迹中回收低风险局部正确 step |
| Training export | 当前暂无统一出口 | `phone_agent/adaptation/dataset_exporter.py` | 导出 SFT / GRPO 样本，离线训练 |

### 2.3  本项目不应照搬 MobileForge 的地方

MobileForge 的多尝试和 GRPO 更适合 benchmark 或安全隔离环境。Shopping-Agent 面向真实购物 App，不能直接把生产环境变成在线强化学习场：

- 不能为了训练反复尝试结算、登录、提交订单等高风险动作。
- 不能把失败轨迹直接写入图谱，否则会污染后续任务。
- 不能把自动评估器的结论无条件当 reward，否则错误反馈会被放大。

因此本项目应采用更稳的路线：

```text
生产运行只产反馈和样本
  -> 失败经验进入 quarantine
  -> 离线筛选和训练
  -> 影子评估通过后再切换模型或提示协议
```

这也是本项目可以形成差异化创新的地方：**风险敏感的无标注适配**，而不是通用 GUI benchmark 上的无标注适配。

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
| 经验学习 | 从 App 交互中生成课程、反馈和训练信号 | 较少涉及图谱和任务课程 | AMSG-Forge：图谱原生适配底座 |
| 上下文管理 | 使用 hint 指导多次 rollout | 把上下文管理作为动作 | VCA：可验证上下文动作 |
| 失败利用 | 从失败中筛局部合理 step | 关注长程记忆失败 | 风险敏感 local-positive recovery |
| 安全边界 | benchmark 适配为主 | 长程基准为主 | 购物场景下的高风险动作隔离 |

因此，下一阶段系统可以定义为：

> **Feedback-Grounded Memory-Spatial Agent**：把真实移动 GUI 交互中的空间转移、上下文事实、失败反馈和纠错经验统一沉淀为可执行图谱、可验证记忆和可训练样本。

这不是另起炉灶，而是在现有 Shopping-Agent 上增加三个可落地层：

1. **Feedback Trace**：统一记录每步行为的 outcome、process feedback、guardrail verdict、context action 和 corrective hint。
2. **Verified Context Actions**：把记忆写入、更新、删除、历史折叠变成带证据的事务。
3. **AMSG-Forge**：从图谱覆盖缺口、失败簇和反馈账本中生成课程与离线训练样本。

---

## 3  实施计划：从可观测反馈到账本化自适应

### 3.1  总体路线

实施顺序必须遵守一个原则：**先让经验可观测，再让经验可筛选，最后才让经验进入训练**。如果没有统一反馈账本，直接做 GRPO 或上下文动作训练只会放大日志噪声。

推荐三阶段路线：

```text
阶段一：Feedback Trace + VCA pilot
  -> 让每步反馈和记忆提交可审计

阶段二：AMSG-Forge + Hierarchical Critic
  -> 让图谱缺口、失败簇、低置信边生成课程和 step feedback

阶段三：Dataset Export + Shadow Training
  -> 导出 SFT/GRPO 样本，离线训练并影子评估
```

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

### 3.4  阶段二：AMSG-Forge 课程生成

新增模块：

```text
phone_agent/adaptation/
  __init__.py
  curriculum.py
  critic.py
  rollout.py
  dataset_exporter.py
```

课程不应只由 LLM 扩写，而应从系统证据中产生：

| 课程来源 | 生成任务示例 | 对应模块 |
|---|---|---|
| 图谱覆盖缺口 | 覆盖 `search_result -> filter_panel -> search_result` | `schema_registry.py`, `task_builder.py` |
| 低置信边 | 反复验证某 App 的 `cart -> checkout` 前置流程 | `edge_lifecycle.py` |
| 高熵页面 | 同一动作产生登录/弹窗/目标页多结果 | `edge_lifecycle.py`, `runtime_controller.py` |
| 失败簇 | 多次卡在 SKU 选择或筛选面板 | `feedback/trace.py`, `adaptation/curriculum.py` |
| 用户高频槽位 | 耳机、手机、外卖等高频意图 | `memory/task_index.py` |
| 新词表提案 | `new:<type>` 页面 | `schema_registry.py` |

课程生成结果必须包含来源解释：

```json
{
  "task": "在京东中搜索一个低价蓝牙耳机并进入筛选面板",
  "source": "low_confidence_edge",
  "evidence": ["edge:search_result->filter_panel", "confidence:0.42"],
  "risk_policy": "no_checkout",
  "target_modules": ["PageClassifier", "ActionAdvisor", "EdgeLifecycle"]
}
```

### 3.5  阶段二：层级评估器

当前系统已有多个反馈源，但需要统一成 `HierarchicalCritic`：

| 层级 | 信号来源 | 输出 |
|---|---|---|
| Trajectory outcome | final_confirm、任务成功状态、完成门 | success/failure/partial |
| Step process | 后条件验证、边生命周期、VLM step review | reasonable/unreasonable/risky |
| Constraint verdict | SpecGuard、价格裁决、提交拦截 | safe/unsafe/violated |
| Context verdict | VCA 提交/拒绝原因 | useful/noisy/wrong |
| Corrective hint | 里程碑监督、失败聚类、VLM reviewer | 下次尝试应避免和应尝试的策略 |

实现策略：

- 普通 step 优先使用机械信号，减少强 VLM 调用。
- 只有失败关键点、高价值课程和训练候选样本调用强 VLM 生成 hint。
- 高风险动作永远不因自动 critic 结论直接进入训练正样本。

### 3.6  阶段三：数据导出与影子训练

导出两类样本。

SFT 样本：

```json
{
  "prompt": "task + screenshot + VCA context + graph_hint",
  "response": {
    "ui_action": "...",
    "context_actions": [],
    "action_intent": "...",
    "ui_observation": "..."
  },
  "source": "success_trace|local_positive_from_failed_trace",
  "evidence": []
}
```

GRPO/RFT 样本：

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

1. 离线格式校验。
2. 回放环境或 dry-run 验证。
3. 真实 App 影子评估。
4. 与当前模型做任务成功率、强 VLM 调用次数、错误事实写入率对比。

### 3.7  实验设计

主要假设：

| 假设 | 验证方式 |
|---|---|
| H1: VCA 能降低长程任务上下文漂移 | 对比当前会话记忆与 VCA，测关键事实保留率和 token 增长 |
| H2: 失败轨迹局部回收能提升冷启动适配 | 对比 success-only 图谱学习与 local-positive step mining |
| H3: 图谱驱动课程优于纯 LLM 扩写课程 | 对比 coverage/failure/entropy curriculum 与 prompt-only expansion |
| H4: 反馈账本能减少强 VLM 调用 | 对比当前里程碑监督、每步强规划、FGMS 三档 |
| H5: 风险分层能避免图谱污染 | 统计 promoted 边误用率、demotion 次数、高风险误执行次数 |

消融矩阵：

| 配置 | VCA | AMSG-Forge 课程 | 失败局部回收 | 层级 Critic | 目标 |
|---|---:|---:|---:|---:|---|
| Current | 否 | 否 | 否 | 部分 | 当前基线 |
| +VCA | 是 | 否 | 否 | 部分 | 验证上下文动作 |
| +Forge-Curriculum | 否 | 是 | 否 | 是 | 验证课程来源 |
| +Local Recovery | 否 | 是 | 是 | 是 | 验证失败轨迹回收 |
| Full FGMS | 是 | 是 | 是 | 是 | 完整系统 |
| Strong Planner Upper | 可选 | 可选 | 可选 | 强 | 每步强 VLM 上界 |

### 3.8  两周 pilot

目标：不训练模型，只验证反馈账本和 VCA 是否改善可观测性。

任务：

1. 实现 `FeedbackEvent` JSONL。
2. 扩展 `SessionMemoryFile`，增加 `context_actions` 和 `folded_trace`。
3. 由机械护栏和里程碑监督先产生 VCA。
4. 在淘宝/京东各跑 10 个长程购物任务。
5. 统计关键事实保留率、错误事实写入率、强 VLM 调用次数和任务成功率。

成功标准：

- 每步能追溯到截图、动作、后条件和记忆提交结果。
- 不显著增加延迟。
- 至少发现 3 类当前日志无法解释的失败原因。

### 3.9  一个月版本

目标：让课程生成和失败局部回收跑通。

任务：

1. 实现 `CurriculumMiner`，从图谱缺口、低置信边、失败簇生成任务。
2. 实现 `HierarchicalCritic` 第一版。
3. 实现失败轨迹 local-positive step quarantine。
4. 导出 SFT JSONL，先做格式验证，不急于训练。

成功标准：

- 每个课程任务都有可解释来源。
- 失败轨迹中的可回收 step 不进入 promoted 图谱，只进入训练候选池。
- 导出样本能复现当时 prompt、截图、动作和证据。

### 3.10  三个月论文型版本

目标：形成可汇报、可投稿的系统贡献。

任务：

1. 小模型支持可选 VCA 输出块。
2. 训练一个轻量 SFT checkpoint，学习 UI action + context action。
3. 在淘宝、京东、至少一个陌生购物或服务 App 上做跨 App 泛化实验。
4. 完成 Current / +VCA / +Curriculum / +Local Recovery / Full FGMS / Strong Planner Upper 消融。
5. WebUI 增加反馈审计面板。

成功标准：

- Full FGMS 在长程成功率、强 VLM 调用次数或关键事实保留率上显著优于当前系统。
- VCA 错误事实写入率可控。
- AMSG-Forge 课程相比纯 LLM 扩写在冷启动覆盖或成功率上更强。

---

## 附录  汇报时的核心表述

对不熟悉项目的人，推荐用这条主线讲：

1. **我们已有的基础**：Shopping-Agent 不是普通 ReAct Agent，而是小模型执行、强 VLM 低频监督、AMSG 图谱导航、机械护栏和会话记忆组成的混合系统。
2. **我们看到的新机会**：MobileForge 说明真实 App 交互可以变成无标注适配数据；MemGUI-Agent 说明长程任务的上下文管理应该成为可学习动作。
3. **我们的落地创新**：不是照搬两篇论文，而是把它们落到现有系统中，形成 `Feedback Trace + Verified Context Actions + AMSG-Forge`。
4. **我们的安全边界**：失败轨迹不直接入图，高风险事实不由模型直接写入，训练在离线和影子评估后再进入生产。

一句话版本：

> Shopping-Agent 已经解决了真实手机购物任务中的可靠执行问题；下一阶段要解决的是如何从执行中学习。我们将用反馈账本统一每步经验，用可验证上下文动作管理长程记忆，用 AMSG-Forge 把空间图谱升级为无标注适配底座，从而把真实 App 交互转化为可审计、可训练、可安全复用的经验。

---

## 参考来源

- MobileForge: Annotation-Free Adaptation for Mobile GUI Agents with Hierarchical Feedback-Guided Policy Optimization, arXiv:2606.19930.
- MemGUI-Agent: An End-to-End Long-Horizon Mobile GUI Agent with Proactive Context Management, arXiv:2606.19926.
- `phone_agent/docs/ARCHITECTURE_CN.md`
- `phone_agent/docs/AMSG_DESIGN.md`