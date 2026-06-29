# AMSG 小论文实验执行计划

> **定位**：横跨代码（harness/修复）与论文（填补第 4 章）的科研自动化执行计划，服务 `amsg-paper-cn.md` 的实验章节落地。非论文稿件。
> **版本**：2026-06-29（初版，基于 ARCHITECTURE_CN.md / AMSG_DESIGN.md 2026-06 审计版 + 代码实测核验）
> **事实纪律**：本计划中所有"现状"判断均经代码/数据实测核验；所有写入论文的数字须由本计划产出的实验测量，不得沿用设计文档的估算值（详见 `_drafts/notes-amsg.md` ③）。

---

## 0  决策基线与关键技术澄清

### 0.1  三项已锁定决策

| 维度 | 决策 | 含义 |
|---|---|---|
| 评测策略 | **混合** | 真机跑头条成功率表 + 京东冷启动 + RQ3 延迟；离线轨迹回放 + DB 注入跑 RQ1/RQ2 机制消融 |
| 缺口处理 | **先修实现再测** | 实现"离线审核边直入 promoted"冷启动加速，使 RQ3 快路径可触发、RQ4 京东有图谱可比 |
| 规模 | **毕业论文够用** | 30–50 任务、关键基线、核心消融；够支撑硕士创新点 + 工程落地性，可作小论文起步 |

### 0.2  承重技术澄清：Fast Path 调度档 ≠ RuntimeDAG 缓存（已代码核验）

设计文档 §3.2 的"快路径几乎不触发"是整个 RQ3 实验设计的关键歧义点。代码核验确认这是**两层独立机制**，遥测分开记账：

| 机制 | 是什么 | 触发条件 | 遥测字段 | 现状 |
|---|---|---|---|---|
| **Fast Path 调度档** | 锚定 promoted 边 → 跳过小模型调用（~0.5s） | `ActionAdvisor` 返回 `is_fast_executable` 的 hint（`grounded ∧ conf≥0.9 ∧ 有坐标`） | `dispatch="fast_path"` / `"fast_path_fallback"`（`agent.py:595/613/640`） | **淘宝 35 条 promoted 今天就能触发**；京东 0 条不触发 |
| **RuntimeDAG 缓存** | 跳过定位管线 + 跳过分类器（~10ms） | DAG 命中且无 pending 后条件 | `runtime_dag_hits` / `page_classifier_skips`（`runtime_controller.py:92/104`） | **结构性失效**（冷 App 不建 + 验证互斥 + 每步重建） |

**裁决**：
- **RQ3 的真卖点是 Fast Path 调度档**（省小模型调用），它**本就可测**——只需京东补上 promoted 边。不去啃 RuntimeDAG 深坑。
- **RuntimeDAG 作为 limitation 如实写入论文**（"路径缓存的管线/分类器跳过在当前实现下基本不兑现，原因为 ..."），不作头条 claim。
- 关键代码锚点：`agent.py:512 _needs_vlm` / `579 _execute_fast_path` / `1747 调度入口`；`action_advisor.py:57 is_fast_executable` / `91 query` / `21 _UNGROUNDED_TRANSITIONS`；`edge_lifecycle.py:204 record_outcome` / `388 _stage_for` / `276 get_promoted_edges_for_page`；`runtime_controller.py:104/106/633`。

### 0.3  诚实性红线（贯穿全程）

1. **bootstrap 配置必须透明**：F1 注入的 promoted 边以 `source_type=offline_bootstrap` 标记；RQ1（纯在线自进化）用**冷起步无注入**配置，RQ3/RQ4/头条用 **warm-start（offline-bootstrapped）**配置，两者在论文中分别命名、不混淆。
2. **不得用预期偏置验证**：分类器独立性是后条件验证 ground truth 的前提（ARCHITECTURE §8 红线），消融时也不破坏。
3. **基线为同框架协议复刻**（§4.3），差异须完全归因于控制协议，复用同一感知/执行/存储基建。
4. **失败任务照常记录**（写 trajectories）但不入图，便于故障模式分析。

---

## 1  实验设计

### 1.1  RQ 可测性裁决表（修复后）

| RQ | 主张 | 修复后能否做出效果 | 测法概要 | 真机/离线 |
|---|---|---|---|---|
| 头条 | AMSG 胜过基线 | ✅ | 4 系统 × 任务集 | 真机 |
| **RQ1a** 活性增长 | 快路径命中率随任务数升 | ✅（Fast Path 档随 promoted 边增长） | 冷起步重复跑，画 `dispatch=fast_path` 占比 vs 成功任务数 | 真机 + 轨迹回放佐证 |
| **RQ1b** 自修复 | UI 漂移后 3–5 任务恢复 | ✅（可控） | DB 注入漂移 → 测恢复任务数 | 真机（恢复需新观测） |
| **RQ2** 可靠性 | 生命周期 > 布尔边 | ✅ | sava(N=3,θ=0.8) vs legacy；去后条件验证消融；N 敏感性 | 离线轨迹回放为主 |
| **RQ3** 控制边界 | 锚定三档调度最优 | ✅（Fast Path 档；RuntimeDAG 作 limitation） | 真机三维权衡（成功率/延迟/调用），dispatch 分档计数 | 真机 |
| **RQ4** 泛化冷启动 | 五阶段建图管线有效 | ✅（注入后京东有图谱） | 京东建图漏斗 + 域先验消融 | 真机建图 |

### 1.2  头条实验（核心结果表，真机）

4 系统 × 任务集（淘宝 + 京东），每系统 30–50 任务：

| 系统 | 协议 | 实现方式 |
|---|---|---|
| **S1 纯模型无图谱** | 小模型每步执行，无图谱辅助 | `ActionAdvisor` 返回空 + 禁快路径 + 禁 graph_hint（下界） |
| **S2 静态 RAG**（PG-Agent 式） | 图谱冻结只读、仅作提示注入、每步推理 | 冻结图谱（禁 flush）+ 强制 `_needs_vlm=True` + 保留 graph_hint |
| **S3 静态 Teleport**（WebNavigator 式） | promoted 边一律直接重放、无生命周期无降级 | promoted 边全部 fast-path（含非锚定）+ 禁降级 |
| **S4 AMSG 全量** | 锚定逐边调度 + 生命周期 + 后条件验证 | 默认 `sava` + warm-start bootstrap |

> S1–S3 是 harness 内的**配置变体**（§2 F5 需新建对应开关），非外部系统运行。

### 1.3  RQ1 活性

- **(a) 增长曲线**：从冷图谱起步，重复跑同一任务子集，记录每次成功任务后的：promoted 边数、覆盖率（覆盖页面类型/转移占域模式目标比）、`dispatch=fast_path` 步占比。画 vs 成功任务数曲线，验证"10–20 次后核心流程覆盖"。
- **(b) 自修复**：在稳定图谱上用 §2 F4 注入漂移（损坏 N 条 promoted 锚定边的 `target_locator`/`target_page_type`）→ 跑任务 → 记录到 dominance 跌破阈值降级、VLM 重探索、重新提升的任务数。验证"3–5 任务恢复"。

### 1.4  RQ2 可靠性（离线轨迹回放为主）

**关键可行性洞察**：6+ 条真机轨迹含每步 `action_type/page_type`，**本身就是可回放的观测流**。把观测流喂进 `EdgeLifecycleManager.record_outcome` / 后条件验证管线，离线即可对比不同配置下的生命周期行为，无需真机。

- **生命周期 vs 布尔边**：`AMSG_CONFIG=sava`（N=3,θ=0.8）vs `legacy`（布尔），对比错误快路径执行率（= `fast_path_fallback / (fast_path+fast_path_fallback)`）、promoted 边精确率。
- **去后条件验证消融**：需 §2 F2 新建开关（关闭 t+1 验证，所有转移记 success）→ 验证"统计真实性根基"主张。
- **N 敏感性**：N ∈ {1,3,5} 对错误快路径率的影响（论文 §3.4.3 已点名此分析）。

### 1.5  RQ3 控制边界（真机）

AMSG 在 (成功率 / 端到端延迟 / 每任务模型调用) 三维空间 vs S2 全 RAG vs S3 全 Teleport。
- 头条三维散点 + dispatch 分档堆叠图。
- **延迟必须真机测**（回放无法测壁钟延迟）；dispatch 分档计数可额外用轨迹离线统计作佐证。
- RuntimeDAG 命中率（`runtime_dag_hits` 占比）**如实报告**（预期 ≈0），作为"路径缓存优化未兑现"的 limitation 数据。

### 1.6  RQ4 泛化冷启动（真机建图）

- 京东从零到核心覆盖：探索轮次、每轮新增覆盖、人工审核漏斗通过率（探索候选→审核批准→在线提升各环节转化率）。
- 域先验消融：`AMSG_DOMAIN_PRIORS=0` vs 默认，看冷启动方向感贡献。
- 开放词表消融：需 §2 F2 评估 `open_vocab` 开关是否值得建（thesis-scale 可降级为定性案例：秒送外卖页分类）。

### 1.7  基线（同框架协议复刻，§4.3）

S1/S2/S3 见 1.2。复刻原则：复用同一感知/动作执行/Neo4j 存储，仅替换更新策略与调度策略 → 差异归因于控制协议。

### 1.8  指标 → 遥测字段映射（已有埋点，缺落盘+聚合）

| 指标 | 来源字段 | 位置 |
|---|---|---|
| 任务成功率 | `end_task(success)` / harness 判定 | `memory_manager.py:276` |
| 平均步数 | step 计数 | harness |
| 端到端延迟 | 壁钟（harness 计时） | harness |
| 小模型调用数 | `dispatch="model"` 步数 | step telemetry |
| 强 VLM 调用数 | `vlm_call_count` | `session_memory_file.py` |
| 快速路径命中率 | `dispatch="fast_path"` / 总步 | step telemetry |
| 错误快路径率 | `fast_path_fallback` / (fast_path+fallback) | step telemetry |
| RuntimeDAG 命中率 | `runtime_dag_hits/(hits+misses)` | `runtime_controller.py:106 get_runtime_metrics` |
| 分类器跳过数 | `page_classifier_skips` | `runtime_controller.py:104` |
| 覆盖率 | promoted 边数/域模式目标 | 查 Neo4j |

### 1.9  消融矩阵（修正版：区分真实开关 / 待建 / 弃用）

| 开关 | 状态 | RQ | 备注 |
|---|---|---|---|
| `AMSG_CONFIG ∈ {legacy,sava,...}` | ✅ 已实现 | RQ1/RQ2 | `amsg_config.py:139` |
| `PHONE_AGENT_MILESTONE=0/1` | ✅ 已实现 | 系统级 | `milestone_supervisor.py:26` |
| `PHONE_AGENT_STRONG_PLANNER=0/1` | ✅ 已实现 | RQ4 | `step_planner.py:172`（env var，**无 `--no-strong-planner` CLI**） |
| `AMSG_DOMAIN_PRIORS=0/1` | ✅ 已实现 | RQ4 | `domain_priors.py:20` |
| 去后条件验证 | ❌ **待建**（F2） | RQ2 核心 | 关闭 t+1 验证 |
| S1/S2/S3 协议开关 | ❌ **待建**（F5） | 头条/RQ3 | 禁图谱/冻结/全 teleport |
| 漂移注入 | ❌ **待建**（F4） | RQ1b | DB 工具 |
| `open_vocab=False` | ⚠️ 待评估 | RQ4 | 代码未确认存在，thesis-scale 可降级定性 |
| `--transition-policy` | ⛔ **弃用** | — | 代码不存在，非核心，本轮不建 |

---

## 2  实现修复清单（"先修后测"的修）

| ID | 修复 | 位置 | 工作量 | 验收 |
|---|---|---|---|---|
| **F1** | 冷启动 promoted 注入（核心使能） | `spatial_graph_memory.py:1141 promote_staging_to_canonical` + `review/apply.py:18` 加 `--bootstrap-promoted` | 中 | 京东建图后有 ≥K 条 promoted 锚定边，跑 1 个京东任务见 `dispatch=fast_path` |
| **F2** | 补缺失消融开关：去后条件验证；评估 open_vocab | `agent.py` Phase 6 验证暂存处加 env 开关 | 小 | 开关置位后 t+1 验证不记账，生命周期不提升 |
| **F3** | 遥测落盘观测器 | harness 侧挂 `step_observer`，写 per-step JSONL + per-task TSV，task 末拉 `get_runtime_metrics` | 小 | 跑 1 任务产出含全部 §1.8 字段的 TSV 行 |
| **F4** | DB 漂移注入工具 | 新 `scripts/inject_ui_drift.py`，连 Neo4j 损坏 N 条 promoted 边 | 小 | 注入后该边 dominance 随失败下降、被降级 |
| **F5** | 基线协议变体开关 | harness 配置层 + `agent.py`/`action_advisor.py` 旁路 | 中 | S1/S2/S3 各跑通 1 任务，行为符合协议定义 |

> 每个 F 项用 `tdd` skill 先写测试（复用 `tests/`，注意 `conftest.py` 设 `AMSG_CONFIG=legacy`、`--basetemp`），`autoresearch:fix` 稳定。

---

## 3  自动化基础设施（补 ④⑤ 缺口）

| ID | 组件 | 设计 |
|---|---|---|
| **H1** | `ExperimentRunner` | 包 `PhoneAgent.run()`；输入任务集 + 配置矩阵（system × ablation × 重复）；逐项设 env/config、挂 F3 观测器、捕获结果、写 results TSV。真机模式复用探索栈的 captcha/login 蜂鸣暂停人工接力 |
| **H2** | 任务集 schema + 生成 | YAML：`{id, app, task_text, constraints, target_page, difficulty}`；从现有 6 条 + `autoresearch:scenario` 按 app×意图×约束组合矩阵扩到 30–50，含失败/边缘场景 |
| **H3** | 轨迹观测回放器 | 读 `trajectories/*.json` 的 (action,page_type) 流喂入生命周期/验证管线，离线产 RQ2/RQ1a 机制数据 |
| **H4** | 聚合器 | 读 results TSV → pandas → 成功率/步数/延迟/调用数汇总 + 置信区间 + 分档堆叠，输出论文表 4-1~4-4 与 `nature-figure` 入参 |

目录约定：实验产物落 `experiments/{YYYYMMDD}-{slug}/`（results.tsv / per-step jsonl / config.yaml / summary.md），与 `.pytest_tmp/` 隔离。

---

## 4  执行阶段与里程碑

| Phase | 目标 | 交付物 | 验收 | 主用 skill |
|---|---|---|---|---|
| **P0 对齐地基** | 核对真实 Neo4j 状态（各 app 各 lifecycle 边数），核对 N/θ 实际值，确认 §1.9 开关存废 | `experiments/baseline-graph-audit.md` | 真实边数表 vs 设计文档数字差异清单 | — |
| **P1 搭 harness** | H1/H3/H4 + F3 | ExperimentRunner 可跑 + TSV + 聚合 | 6 条现有任务端到端跑通、产出指标表 | `tdd` `autoresearch:fix` |
| **P2 任务集** | H2 扩到 30–50 | `experiments/tasks.yaml` | 覆盖 ≥3 商品类×≥3 约束组合 + 失败场景 | `autoresearch:scenario` |
| **P3 修实现** | F1/F2/F4/F5 | 各 F 项 + 测试 | §2 各验收项通过 | `tdd` `autoresearch:fix` |
| **P4 京东建图** | 跑五阶段管线 + F1 注入 | 京东 promoted 子图 | 京东任务见 fast_path；RQ4 漏斗数据 | — |
| **P5 跑实验** | 头条 + RQ1-4 全矩阵 | 全 results TSV | 各 RQ 数据齐、占位可填 | `ExperimentRunner` |
| **P6 出图成文** | 填 §4 占位 | 表 4-1~4-4 + 图 + 改稿 | `nature-reviewer` 模拟过审 | `nature-figure` `nature-writing` `nature-polishing` `nature-reviewer` |

---

## 5  Skill 挂载表

| 环节 | 本地 skill | 缺口 |
|---|---|---|
| 实验设计/选题 | `brainstorming-research-ideas` `creative-thinking-for-research` | — |
| 文献/baseline 调研 | `academic-researcher` `arxiv-mcp` | 四 baseline 须回原文核实机制 |
| harness 搭建 | `tdd` `autoresearch:fix` | 无 GUI-agent 评测 harness 模板，自建 H1-H4 |
| 批量执行编排 | `autoresearch:scenario`（任务生成） | **无现成编排 skill**，自建 ExperimentRunner |
| 结果记录统计 | — | **完全缺失**，自建 H4 |
| 图表生成 | `nature-figure` | — |
| 论文写作/润色 | `nature-writing` `nature-polishing` | — |
| 同行评审模拟 | `nature-reviewer` `nature-response` | — |

---

## 6  数据与规模目标（thesis-sufficient）

| 项 | 现状 | 目标 |
|---|---|---|
| 真机任务 | 6（淘宝，全成功，2 商品类） | 30–50（淘宝+京东，多约束，含失败） |
| 京东图谱 | 0 promoted（仅 staging） | 核心链路 promoted 覆盖 |
| 基线 | 0 跑 | S1/S2/S3 复刻 |
| 量化指标 | 无 | §1.8 全字段汇总表 |
| 故障模式 | 6/6 成功无失败样本 | 含失败任务的故障分析 |

---

## 7  风险登记

| 风险 | 影响 | 缓解 |
|---|---|---|
| 真机陪跑耗时（captcha/login） | P5 进度 | 复用探索栈蜂鸣暂停人工接力；任务集去高墙场景 |
| 活体 App 不可复现（价格/库存/AB） | 头条可比性 | 同期连续跑同批任务；记录环境快照；趋势而非绝对值 |
| F1 放松"入图必经在线验证"不变量 | 论文严谨性 | 透明标 `offline_bootstrap`；RQ1 用纯在线配置；可消融 bootstrap vs 纯在线 |
| 设计文档数字失实（记忆"6 处论文级失实"） | 论文可信度 | P0 核对真实 Neo4j；所有数字重测；不沿用估算 |
| RuntimeDAG 作为卖点崩塌 | RQ3 | 已澄清：Fast Path 档才是卖点，RuntimeDAG 降为 limitation |
| 单人时间线 | 整体 | 严格 thesis-scale；P1 先跑通最小闭环再扩规模 |
