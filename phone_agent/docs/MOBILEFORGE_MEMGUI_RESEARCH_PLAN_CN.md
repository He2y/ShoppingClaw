# 从 MobileForge 与 MemGUI-Agent 到 Shopping-Agent：图谱-记忆协同自适应升级方案

> **版本**: 2026-06-27
> **范围**: 基于 `ARCHITECTURE_CN.md` 与 `AMSG_DESIGN.md`，讨论如何吸收 MobileForge 与 MemGUI-Agent 的研究思想，升级当前 `phone_agent/` 架构。
> **定位**: 下一阶段研究设计文档。本文不是复刻两篇论文，而是把它们的核心机制转译为 Shopping-Agent 当前代码可落地、可消融、可写成学术贡献的系统方案。

---

## 0  研究设计立场

本项目当前已经不是一个纯 ReAct GUI Agent。它的关键路线是：**小模型主执行、强 VLM 低频监督、AMSG 空间图谱引导、机械护栏兜底、会话记忆抗遗忘**。因此，MobileForge 和 MemGUI-Agent 的价值不在于替换现有架构，而在于补齐两个仍然薄弱的闭环：

1. **经验如何变成可学习数据**。当前 AMSG 会把成功任务中的导航转移持久化为图谱知识，但失败任务中的局部正确步骤、纠错信息、任务变体和训练样本还没有被系统化沉淀。
2. **上下文如何被主动管理**。当前 `SessionMemoryFile` 已经解决了原始任务锚定、子任务进度和已验证事实，但上下文更新主要由监督者和规则写入，尚未形成可学习、可验证、可消融的"上下文动作"机制。

本文使用四个研究构思视角来收束方案：

- **问题优先**：从当前系统真实瓶颈出发，而不是因为论文有某个组件就照搬。
- **张力分析**：处理端侧低成本执行与云端强评估、图谱快速复用与过期污染、无标注学习与购物安全之间的矛盾。
- **组合/分解**：把 MobileForge 的自适应闭环拆成探索、课程、评估、训练样本四层，再映射到 AMSG；把 MemGUI-Agent 的 ConAct 拆成记忆写入、历史折叠、步骤自描述三类动作。
- **边界探测**：保留当前架构的安全边界，尤其是高风险购物动作、完成判定、价格约束和图谱入库质量门。

---

## 1  现有系统设计情况

### 1.1  非对称权威：当前系统的核心骨架

`ARCHITECTURE_CN.md` 中最重要的设计不是某个单点模块，而是"非对称权威"：

| 决策类型 | 当前权威 | 代码位置 | 设计理由 |
|---|---|---|---|
| 当前屏幕 grounding | 小型 GUI 模型 | `phone_agent/model/`, `phone_agent/actions/` | 需要高频、低延迟、贴近当前截图 |
| 任务拆解与计划修订 | 强 VLM 里程碑监督 | `phone_agent/milestone_supervisor.py`, `phone_agent/task_plan.py` | 语义强但低频，避免每步上云 |
| 数字/约束/完成证据 | 机械护栏 | `phone_agent/core/spec_guard.py`, `phone_agent/verification_detector.py` | 确定性判断不交给模型幻觉 |
| 结构化导航复用 | AMSG 图谱 | `phone_agent/spatial/`, `phone_agent/memory/graph_store.py` | 可复用、可降级、可追踪成功率 |
| 单任务抗遗忘 | 会话记忆文件 | `phone_agent/session_memory_file.py` | 原始任务不改写，已验证事实持久化 |

这套结构已经解决了纯模型方案的几个典型失败：长程焦点丢失、数字幻觉、伪完成、空响应和静态图谱过期。下一步升级不应破坏该分工，而应让每一类权威产生的反馈进入统一经验账本。

### 1.2  AMSG：现有系统最接近 MobileForge 的部分

AMSG 已经具备 MobileForge 所需要的若干底层能力：

- `GraphRuntimeController.locate_and_get_context()`：运行时定位页面、验证上一条 pending transition、规划下一步路径，并输出 `navigate` / `explore` / `verify_with_vlm` / `goal_reached`。
- `EdgeLifecycleManager`：记录边的成功/失败、优势比与生命周期阶段，避免把所有图谱边当成布尔真值。
- `TrajectoryReviewer`：在任务结束后用强 VLM 审核轨迹候选转移，作为第二质量门。
- 陌生 App 建图管线：onboarding、分轮焦点探索、staging 规范化、人工审核门、hypothesis 入图、在线学习。

它与 MobileForge 的关键差别在于：AMSG 目前主要把经验转化为**图谱导航知识**，还没有系统转化为**模型训练样本、纠错 hint、上下文管理样本**。换句话说，AMSG 是活图谱，但还不是完整的 annotation-free adaptation substrate。

### 1.3  会话记忆：现有系统最接近 MemGUI-Agent 的部分

当前记忆系统有四层：

- `SessionMemoryFile`：保存 `original_task`、`subtasks`、`verified_facts`、`revisions`，是单任务抗遗忘锚。
- `UnifiedSessionState`：记录 step、商品、购物车、约束、当前焦点等运行时状态。
- `StepRecord`：保存每步动作、目标、thinking 摘要、页面类型与 app。
- `RetrievalGateway`：按需检索商品列表、购物车计算、历史步骤等上下文。

这已经比 ReAct 被动追加历史更稳，但它的上下文管理仍然偏"外部控制"：由监督者、规则和状态管理器更新，而不是由执行策略显式学习"什么时候记、什么时候折叠、什么时候删除"。MemGUI-Agent 的启发是：**上下文管理不只是工程压缩，它可以成为 GUI Agent 的可学习动作空间**。

### 1.4  当前设计的可升级缺口

基于现有代码和两份设计文档，下一阶段最值得补的不是再加一个大模型 planner，而是四个缺口：

1. **失败经验未充分利用**：失败任务不会写入图谱，这是安全的，但也丢掉了失败轨迹里的局部正确导航、正确记忆和失败原因。
2. **反馈粒度不统一**：图谱后条件、机械护栏、里程碑监督、final confirm、VLM 轨迹审核都在反馈，但缺少统一 step-level feedback schema。
3. **上下文更新不可训练**：`SessionMemoryFile` 很有效，但其更新还不是模型输出协议的一部分，难以导出上下文管理训练样本。
4. **自适应目标不够主动**：陌生 App 探索已有覆盖目标，但日常任务中的失败簇、低置信边、高熵页面、用户高频需求尚未反过来驱动课程生成。

---

## 2  MobileForge 带来的启发：把 AMSG 升级为适配底座

### 2.1  可迁移思想

MobileForge 的真正贡献不是"自动生成任务"四个字，而是把目标 App 适配拆成了闭环：

```
目标 App 探索 -> 任务课程生成 -> 多次 rollout -> 层级评估 -> step 选择 -> 策略更新
```

对 Shopping-Agent 来说，这个闭环不需要重新从 MobileGym 起步，因为 AMSG 已经承担了大量交互与图谱积累工作。更合理的迁移方式是：

- 用 AMSG 的页面图谱、边生命周期、staging 批次和轨迹文件作为探索证据。
- 用现有后条件验证、机械护栏、里程碑监督和轨迹审核组成层级评估器。
- 用失败簇、低置信边和覆盖缺口生成主动课程。
- 用 step-level 反馈导出 SFT / GRPO 数据，而不是在生产运行时直接在线强化学习。

### 2.2  不应照搬的部分

MobileForge 直接把多次尝试和 GRPO 作为训练主线，但 Shopping-Agent 当前面向真实购物 App，存在更强安全约束：

- 高风险动作如 checkout、submit order、支付、账号登录不能在真实环境中为训练反复尝试。
- 当前系统的核心优势是端侧低成本执行，不应让训练闭环污染在线执行稳定性。
- 自动评估器质量不足时，错误 step 被强化比丢弃数据更危险。

因此，本项目应该采用**离线导出、隔离训练、影子验证、再上线**的方式吸收 MobileForge，而不是让生产 Agent 在线自我强化。

### 2.3  项目化创新：AMSG-Forge

建议将升级后的自适应底座命名为 **AMSG-Forge**：

> AMSG-Forge 是一个以空间图谱为中心的无人工标注适配底座。它不只把成功任务写成图谱边，还把每步执行转化为带来源、置信、风险和纠错 hint 的反馈事件；再从这些事件中挖掘课程、局部正确步骤和离线训练样本。

AMSG-Forge 相比 MobileForge 的差异化创新：

1. **图谱原生**：MobileForge 的 MobileGym 是交互底座；AMSG-Forge 的底座是带生命周期的空间图谱，天然能表达页面状态、边可靠性、跨 App 同域结构迁移。
2. **安全分层**：不是所有失败 step 都可训练。购物场景下需要按风险等级、后条件证据和护栏 verdict 分层进入 `quarantine`、`candidate`、`trainable`。
3. **运行时与训练时同源反馈**：同一份反馈既能驱动运行时恢复，也能导出训练数据，减少"线上系统一套、训练数据一套"的割裂。
4. **从边成功率到任务课程**：课程不是由 LLM 凭空生成，而是由图谱覆盖缺口、低置信边、高熵页面、失败聚类和用户任务分布共同驱动。

---

## 3  MemGUI-Agent 带来的启发：把记忆升级为可验证上下文动作

### 3.1  可迁移思想

MemGUI-Agent 的核心洞察是：长程 GUI 任务失败往往不是看不懂当前屏幕，而是**重要事实没有以正确形式留在工作上下文里**。它用 ConAct 让模型每步同时输出 UI action、history folding、UI memory、step observation 和 action intent。

Shopping-Agent 已经有 `SessionMemoryFile` 和 `StepRecord`，因此不需要照搬完整 ConAct 协议。更适合本项目的方式是引入**可验证上下文动作**：

```
UI action: tap/type/swipe/wait/finish
Context action: remember/update_fact/delete_fact/fold_history/set_focus
```

模型可以提出上下文动作，但不能无条件提交。提交必须经过 `ContextCommitter` 的规则校验和必要的强 VLM/机械证据核验。

### 3.2  不应照搬的部分

MemGUI-Agent 的 ConAct 假设模型能在同一 forward pass 中稳定处理五段结构化输出。当前项目的小模型是主执行器，并且已观测到空响应、伪完成、数字幻觉等问题。如果直接把记忆写入权交给小模型，风险是：

- 写入错误价格、错误商品、错误完成状态，污染 `SessionMemoryFile`。
- 把临时 UI 文本当成持久事实，后续任务被误导。
- 为了格式而牺牲当前屏幕 grounding，导致动作质量下降。

所以本项目的原则应是：**上下文动作可以由模型提议，但提交权仍属于系统**。这延续当前非对称权威，不把确定性事实交给模型自由改写。

### 3.3  项目化创新：Verified Context Actions

建议将本项目的记忆升级机制命名为 **Verified Context Actions (VCA)**：

> VCA 把上下文管理建模为一组可审计动作。执行模型、里程碑监督者或机械护栏都可以产生上下文动作提案；系统根据证据来源、风险等级和写入类型决定是否提交到会话记忆。

VCA 的五类状态字段：

| 字段 | 当前对应 | 升级含义 |
|---|---|---|
| `task_anchor` | `SessionMemoryFile.original_task` | 永不改写的用户目标和硬约束 |
| `working_focus` | `UnifiedSessionState.current_focus` | 当前子任务、当前页面、下一步局部目标 |
| `verified_facts` | `SessionMemoryFile.verified_facts` | 只保存有截图/机械/监督者证据的事实 |
| `folded_trace` | 历史压缩 currently no-op | 把已完成 span 折叠成可复用摘要 |
| `recent_evidence` | `StepRecord` | 最近 1-3 步的屏幕事实、动作意图、后条件结果 |

VCA 相比 MemGUI-Agent 的差异化创新：

1. **提交权分离**：模型不是直接写内存，而是发起 `ContextActionIR`；系统根据证据提交。
2. **风险敏感记忆**：价格、商品、规格、完成状态等高风险事实必须有机械或强 VLM 证据。
3. **图谱感知折叠**：历史折叠不只按步数，还按 AMSG 页面转移 span 折叠，例如 `home -> search_input -> search_result` 可折叠为"已完成搜索进入结果页"。
4. **可导出训练样本**：每条上下文动作都有提案、证据、提交结果，可作为小模型学习记忆管理的监督数据。

---

## 4  总体升级方向：Feedback-Grounded Memory-Spatial Agent

建议把下一阶段系统研究问题表述为：

> 移动 GUI Agent 如何在不依赖人工标注的情况下，把真实 App 交互中的空间转移、上下文事实、失败反馈和纠错经验统一沉淀为可执行图谱、可验证记忆和可训练样本？

对应系统名称可暂定为 **Feedback-Grounded Memory-Spatial Agent (FGMS-Agent)**。它不是新建一个 Agent，而是在当前 Shopping-Agent 上增加三层能力：

1. **Feedback Trace**：统一记录每步行为的 outcome、process feedback、guardrail verdict、context action 和 corrective hint。
2. **Verified Context Actions**：把上下文写入、更新、删除、折叠变成可验证动作。
3. **AMSG-Forge**：从图谱覆盖缺口、失败簇和反馈账本中生成课程与离线训练样本。

### 4.1  新闭环

```
用户/探索任务
  -> PhoneAgent 执行
  -> 每步产生 Feedback Trace
  -> VCA 提交可靠上下文事实
  -> AMSG 更新可靠空间转移
  -> 失败/低置信经验进入 quarantine
  -> Curriculum Miner 生成主动课程
  -> 多尝试安全 rollout 或离线回放
  -> Hierarchical Critic 标注 outcome/process/hint
  -> Dataset Exporter 生成 SFT/GRPO 样本
  -> 训练后的模型影子评估
  -> 通过质量门后进入生产配置
```

### 4.2  与当前系统的关系

| 当前能力 | 升级后角色 |
|---|---|
| 里程碑监督 | 从低频计划修订者升级为层级反馈来源之一 |
| AMSG 图谱 | 从导航复用库升级为课程生成和训练数据索引 |
| 机械护栏 | 从运行时拦截器升级为训练 reward/verdict 来源 |
| 会话记忆文件 | 从抗遗忘文件升级为上下文动作提交账本 |
| 轨迹文件 | 从任务后产物升级为可回放、可筛选、可导出的经验样本 |
| WebUI | 从可视化执行升级为反馈审计、样本审计与消融面板 |

---

## 5  现有模块改进升级计划

### 5.1  第一阶段：统一反馈账本

新增模块建议：

```
phone_agent/feedback/
  __init__.py
  trace.py
  schema.py
  exporters.py
```

核心数据结构：

```python
FeedbackEvent = {
    "session_id": str,
    "step": int,
    "app": str,
    "page_type": str,
    "task": str,
    "ui_action": DeviceActionIR,
    "graph_mode": "navigate|explore|verify_with_vlm|goal_reached",
    "postcondition": {"expected": str, "actual": str, "passed": bool},
    "guardrail_verdicts": list[dict],
    "context_actions": list[dict],
    "outcome_label": "unknown|success|failure|partial",
    "process_label": "reasonable|unreasonable|risky|unknown",
    "corrective_hint": str,
    "risk_level": "low|normal|high",
    "evidence_refs": list[str],
}
```

改动映射：

| 文件 | 改动 |
|---|---|
| `phone_agent/agent.py` | 在 `_execute_step_impl()` 每步结束时写入 `FeedbackEvent` |
| `phone_agent/tracer.py` | 关联截图、模型输出、动作 IR、后条件结果 |
| `phone_agent/spatial/runtime_controller.py` | 输出图谱 mode、pending transition 验证结果和边生命周期状态 |
| `phone_agent/core/spec_guard.py` | 输出结构化 guardrail verdict，而不是只作为拦截逻辑 |
| `phone_agent/milestone_supervisor.py` | checkpoint/final_confirm 结果进入同一反馈账本 |

验收标准：

- 每个任务结束后生成一份 `feedback_trace.jsonl`。
- 成功和失败任务都保存 trace，但默认只有成功任务可进入图谱持久化。
- 失败任务中的 step 只有在 `postcondition.passed=True` 且 `risk_level != high` 时可进入后续候选池。

### 5.2  第二阶段：VCA 上下文动作

新增模块建议：

```
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
| `remember(price)` | 机械价格解析成功，禁止仅凭模型文本写入 |
| `update_fact(cart_added)` | 后条件或 final_confirm 证据通过 |
| `fold_history` | span 内每步已有 `process_label != risky` |
| `delete_fact` | 只允许删除低风险临时事实，高风险事实需监督者确认 |
| `set_focus` | 与 `TaskPlan` 当前子任务或图谱目标一致 |

改动映射：

| 文件 | 改动 |
|---|---|
| `phone_agent/session_memory_file.py` | 增加 `context_actions`、`folded_trace`、`recent_evidence` 字段 |
| `phone_agent/memory/core/step_record.py` | 增加 `ui_observation`、`action_intent`、`postcondition_result` |
| `phone_agent/model/protocol_bridge.py` | 解析模型可选的上下文动作块；不支持的模型保持兼容 |
| `phone_agent/config/prompts*.py` | 增加轻量上下文动作协议提示 |
| `phone_agent/memory/retrieval_gateway.py` | 从 VCA 状态渲染上下文，而不是拼接过长历史 |

验收标准：

- 不训练模型也能先由里程碑监督和机械护栏产生 VCA。
- 小模型输出的 VCA 默认进入 proposal，必须经 `ContextCommitter` 提交。
- 对比当前 `SessionMemoryFile`，长程任务上下文 token 增长更慢，且关键事实保留率更高。

### 5.3  第三阶段：AMSG-Forge 课程生成

新增模块建议：

```
phone_agent/adaptation/
  __init__.py
  curriculum.py
  critic.py
  rollout.py
  dataset_exporter.py
```

课程来源不应只靠 LLM 扩写，而应从当前系统证据中挖掘：

| 课程来源 | 生成任务示例 | 研究价值 |
|---|---|---|
| 图谱覆盖缺口 | 覆盖 `search_result -> filter_panel -> search_result` | 补齐未探索转移 |
| 低置信边 | 重复验证某 App 的 `cart -> checkout` 前置流程 | 修复不稳定路径 |
| 高熵页面 | 同一动作产生登录/弹窗/目标页多结果 | 学习分支处理 |
| 失败簇 | 多次卡在 SKU 选择 | 生成针对性规格选择任务 |
| 用户高频槽位 | 耳机、手机、外卖等高频购物意图 | 面向真实需求优化 |
| 新词表提案 | `new:<type>` 页面 | 推动 schema 演化 |

改动映射：

| 文件 | 改动 |
|---|---|
| `phone_agent/memory/exploration/task_builder.py` | 从 coverage-only 扩展为 feedback-aware curriculum |
| `phone_agent/spatial/edge_lifecycle.py` | 暴露低置信、高熵、降级边查询 |
| `phone_agent/spatial/schema_registry.py` | 提供 schema coverage 与 open-vocab 提案接口 |
| `phone_agent/memory/exploration/explorer.py` | 支持课程任务批量执行与安全边界 |

验收标准：

- 每轮生成课程都能解释来源：coverage gap、failure cluster、edge entropy 或 user demand。
- 高风险课程只在安全模式、模拟环境或人工接管下执行。
- 课程执行产物进入 feedback trace，而不是直接污染图谱。

### 5.4  第四阶段：层级评估器

当前系统已经有多个评估信号，但分散在不同模块。需要把它们组合成 `HierarchicalCritic`：

| 层级 | 信号来源 | 输出 |
|---|---|---|
| Trajectory outcome | final_confirm、任务成功状态、完成门 | 成功/失败/部分成功 |
| Step process | 后条件验证、边生命周期、VLM step review | reasonable/unreasonable/risky |
| Constraint verdict | SpecGuard、价格裁决、提交拦截 | safe/unsafe/violated |
| Context verdict | VCA 提交/拒绝原因 | useful/noisy/wrong |
| Corrective hint | 里程碑监督、失败聚类、VLM reviewer | 下次尝试应避免和应尝试的策略 |

实现方式：

- 第一版不用训练 reward model，直接组合已有机械证据和低频强 VLM。
- 对高价值失败样本调用强 VLM 生成 corrective hint。
- 对普通成功样本只用机械和图谱后条件，控制云调用成本。

验收标准：

- 每个失败任务至少有 trajectory-level reason。
- 每个可训练 step 必须有 process label 和证据来源。
- Corrective hint 可被下一次同族任务注入，但默认只影响 prompt，不直接改图谱。

### 5.5  第五阶段：数据导出与训练接口

离线训练数据建议导出两类：

**SFT 样本**：

```json
{
  "prompt": "task + screenshot + VCA rendered context + graph hint",
  "response": {
    "ui_action": "...",
    "context_actions": [...],
    "action_intent": "...",
    "ui_observation": "..."
  },
  "source": "success_trace|local_positive_from_failed_trace",
  "evidence": [...]
}
```

**GRPO/RFT 样本**：

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

关键原则：

- 生产运行只产数据，不在线改模型权重。
- 训练后的 checkpoint 必须先跑 shadow evaluation，再进入默认配置。
- 数据样本要保留 `risk_level`、`evidence_refs`、`app`、`domain`、`source_quality`，方便消融和过滤。

---

## 6  学术创新点组织

### 6.1  创新点一：空间图谱作为无标注适配底座

现有 MobileForge 用真实 App 交互构建适配底座，但没有把页面图谱生命周期作为核心组织结构。AMSG-Forge 的创新是：**以可降级、可自修复的空间图谱组织无标注适配数据**。

可写成论文贡献：

> We propose a graph-native annotation-free adaptation substrate that turns online GUI interaction into spatial transitions, reliability-aware curriculum, hierarchical feedback, and trainable step samples.

### 6.2  创新点二：可验证上下文动作

MemGUI-Agent 把上下文管理作为模型动作，但本项目进一步引入提交权分离和风险敏感校验。创新点是：**上下文动作不是自由文本记忆，而是带证据、风险和提交结果的事务**。

可写成论文贡献：

> We introduce verified context actions, a transaction-style context management mechanism that lets GUI agents propose memory and folding operations while deterministic and VLM-based validators decide commitment.

### 6.3  创新点三：失败轨迹的安全局部回收

当前系统安全地丢弃失败任务图谱写入，但这会浪费局部正确步骤。升级后可以把失败轨迹拆成：

- 可回收的低风险局部正确 step。
- 只进入 quarantine 的可疑 step。
- 必须丢弃的高风险或错误 step。

这比 MobileForge 更强调真实购物场景的安全边界。

可写成论文贡献：

> We recover useful local decisions from failed mobile GUI trajectories through postcondition-verified, risk-aware step filtering, without admitting failed trajectories into the executable graph.

### 6.4  创新点四：运行时恢复与离线训练同源

同一套 feedback trace 同时服务三件事：

- 运行时：纠错 hint、上下文修复、图谱降级。
- 数据：课程生成、样本筛选、训练导出。
- 审计：WebUI 中解释为什么某个事实被记住、某条边被提升、某个 step 被训练。

这能形成系统论文里很有价值的主张：**不是单独做一个训练 pipeline，而是把产品运行闭环变成研究数据闭环**。

---

## 7  实验设计

### 7.1  主要假设

| 假设 | 验证方式 |
|---|---|
| H1: VCA 能降低长程任务上下文漂移 | 对比当前 `SessionMemoryFile` 与 VCA，测 Pass@k、关键事实保留率、上下文 token 增长 |
| H2: 失败轨迹局部回收能提升冷启动适配 | 对比 success-only 图谱学习与 local-positive step mining |
| H3: 图谱驱动课程优于纯 LLM 扩写课程 | 对比 coverage/failure/entropy curriculum 与 prompt-only task expansion |
| H4: 反馈账本能减少强 VLM 调用 | 对比每步强规划、当前里程碑监督、FGMS-Agent 三档 |
| H5: 风险分层能避免图谱污染 | 统计 promoted 边误用率、demotion 次数、高风险误执行次数 |

### 7.2  消融矩阵

| 配置 | VCA | AMSG-Forge 课程 | 失败局部回收 | 层级 Critic | 目标 |
|---|---:|---:|---:|---:|---|
| Current | 否 | 否 | 否 | 部分 | 当前基线 |
| +VCA | 是 | 否 | 否 | 部分 | 验证上下文动作 |
| +Forge-Curriculum | 否 | 是 | 否 | 是 | 验证课程来源 |
| +Local Recovery | 否 | 是 | 是 | 是 | 验证失败轨迹回收 |
| Full FGMS | 是 | 是 | 是 | 是 | 完整系统 |
| Strong Planner Upper | 可选 | 可选 | 可选 | 强 | 每步强 VLM 上界 |

### 7.3  指标

任务层：

- 成功率 / Pass@k。
- 任务平均步数。
- 平均强 VLM 调用次数。
- 平均延迟。
- final false-positive rate。

图谱层：

- 新增 hypothesis/candidate/promoted 边数量。
- promoted 边后续成功率。
- demotion 次数与原因。
- 图谱命中率、Fast Path 命中率、Co-pilot 命中率。

记忆层：

- 关键事实保留率。
- 错误事实写入率。
- 上下文 token 增长曲线。
- fold span 平均长度。
- VCA proposal/commit/reject 分布。

训练数据层：

- 可训练 step 数量。
- 来自失败轨迹的 local-positive 占比。
- 样本证据完备率。
- 高风险样本过滤率。

---

## 8  分阶段落地路线

### 8.1  两周 pilot

目标：不训练模型，只验证 feedback trace 与 VCA 是否能提升可观测性和长程稳定性。

任务：

1. 增加 `FeedbackEvent` JSONL 输出。
2. 在 `SessionMemoryFile` 中增加 `context_actions` 和 `folded_trace`。
3. 由机械护栏和里程碑监督先产生 VCA，不要求小模型输出新格式。
4. 在淘宝/京东各跑 10 个长程购物任务。
5. 统计关键事实保留率、错误事实写入率、强 VLM 调用次数和任务成功率。

成功标准：

- 每步反馈可追溯到截图、动作、后条件和记忆提交结果。
- 不增加明显延迟。
- 至少发现 3 类当前日志无法解释的失败原因。

### 8.2  一个月版本

目标：让课程生成和失败局部回收跑通。

任务：

1. 实现 `CurriculumMiner`，从图谱缺口、低置信边、失败簇生成任务。
2. 实现 `HierarchicalCritic` 第一版，组合后条件、护栏和强 VLM 轨迹审核。
3. 实现失败轨迹 local-positive step quarantine。
4. 导出 SFT JSONL，不开始训练或只做小规模格式验证。

成功标准：

- 课程任务都有可解释来源。
- 失败轨迹中可回收 step 不进入 promoted 图谱，只进入训练候选池。
- 导出样本能复现当时 prompt、截图、动作和证据。

### 8.3  三个月论文型版本

目标：形成可投稿的系统贡献和实验矩阵。

任务：

1. 小模型支持可选 VCA 输出块。
2. 训练一个轻量 SFT checkpoint，学习 UI action + context action。
3. 在淘宝、京东、至少一个陌生购物/服务 App 上做跨 App 泛化实验。
4. 完成 Current / +VCA / +Curriculum / +Local Recovery / Full FGMS / Strong Planner Upper 消融。
5. WebUI 增加反馈审计面板。

成功标准：

- Full FGMS 在长程任务成功率、强 VLM 调用次数或上下文事实保留上至少有一个显著优势。
- VCA 错误事实写入率可控。
- AMSG-Forge 课程相比纯 LLM 扩写在冷启动覆盖或成功率上更强。

---

## 9  风险与边界

| 风险 | 后果 | 控制策略 |
|---|---|---|
| 自动 critic 错误 | 错误 step 被训练或错误 hint 被复用 | 高风险 step 不自动进入训练；保留 evidence refs；抽样人工审计 |
| 失败轨迹污染图谱 | 后续任务被错误边误导 | 失败轨迹只进 quarantine，不进 promoted；必须通过后条件和生命周期门 |
| 小模型乱写记忆 | 错误事实长期影响任务 | VCA 提案与提交分离；价格、商品、完成状态需机械/强 VLM 证据 |
| 课程触发危险动作 | 误下单、误登录、误支付 | 高风险动作默认 dry-run 或人工接管；SpecGuard 作为硬门 |
| 云调用成本上升 | 违背端侧低成本目标 | Critic 分层调用；普通 step 用机械信号，高价值失败才用强 VLM |
| 训练数据过拟合单 App | 泛化弱 | 按 `domain` 组织课程，跨 App 留出验证；保留 app/domain/source 字段 |

---

## 10  推荐的下一步

优先做 **Feedback Trace + VCA pilot**，原因很直接：

1. 它不改变主执行策略，风险最低。
2. 它能立刻提升失败可解释性。
3. 它是后续 AMSG-Forge 课程生成、层级 Critic、训练样本导出的共同基础。

不建议第一步就做 GRPO 或完整多尝试 rollout。当前系统更需要先把每步反馈和上下文动作规范化；没有这层账本，训练只会把现有日志的噪声放大。

最小工程切入点：

```text
phone_agent/feedback/trace.py
phone_agent/context/actions.py
phone_agent/context/committer.py
phone_agent/session_memory_file.py
phone_agent/tracer.py
phone_agent/agent.py
webui.py
```

最小论文切入点：

> Shopping-Agent 当前已经证明了"小模型主执行 + 强 VLM 低频监督 + AMSG 图谱"的可行性。下一篇工作的研究问题应从"如何执行"推进到"如何从执行中自适应学习"：通过反馈账本、可验证上下文动作和图谱原生课程生成，把真实移动 GUI 交互转化为可审计、可训练、可安全复用的经验。
