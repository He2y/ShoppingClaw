# AMSG 模拟环境方案分析（三篇论文精读 + 对抗核验综合）

> **定位**：回答"用提取 App 关键结构造模拟器 + 结合 sim/真机数据"能否弥补 AMSG 实验环节。服务 `amsg-experiment-plan.md`，结论已并入其 §8。
> **方法**：精读 Beyond the GUI Paradigm / PhoneBuddy / MobileForge 三篇 + 5 视角头脑风暴 + 3 路对抗核验（codebase 可行性 / 论文忠实度 / 审稿人有效性）。**所有代码/数据事实已逐条核对活体仓库**（见文末核验台账）。
> **版本**：2026-06-29

---

## 0  一句话结论

**不要先造模拟器，先榨数据。** 真瓶颈不是环境成本/弹窗干扰，而是**数据贫瘠 + 幸存者偏差**：实测唯一事实地基是 **≈4 条可用淘宝轨迹（6 条里 2 条是 2 步残桩）、全成功零失败、18 张分散 JD 截图、JD 0 条真正购买边、OutcomeDistribution 全单峰**。在此密度下，任何"造完整 sim 跑机制消融"都会被审稿人一枪打死——**不是因为循环论证**（团队代码 `runtime_controller.py:277` 已有 `unverified` 防线），**而是因为 RQ2 的核心命题"布尔边会错、生命周期能挡"在现有语料里没有一个"会错"的样本**。模拟器造不出不存在的失败信号；硬造出来的就是人为假设，就是新的循环。

**正确顺序**：① 纯数据回放（榨干现有轨迹做 RQ2 机制方向）+ Sim2Real Gap 审计 → ② Oracle 协议补 JD 购买边 + **补采含失败/干扰的真机轨迹** → ③ 才谈概率转移核与干扰注入。把"图谱即模拟器"当 abstract 级 novelty 在补采前写出去 = overclaim。

---

## 1  三篇论文核心（他们怎么解决"造模拟环境"）

**① Beyond the GUI Paradigm（Mila，CLI-vs-GUI 对比，47 页）** — 不自建 sim，复用 **AndroidWorld/MobileWorld 可重置容器基准**：`random.Random(seed)` 播种确定性 fixture，**二值规则验证器**（State-check 读设备 DB / Cache-match 查答案子串，**绝不用 LLM-judge、绝不联网**）判成败，把"活体不可复现"靠"活体 App→可重置容器"工程绕过。独特贡献：**5 阶段 human-LLM oracle 协议**（Setup→Exploration→Proposal→Oracle-acting→Human-in-loop，双准则 Solvability+Integrity + 3 专家复核）造金标准最短轨迹；**Oracle 天花板法**把"范式可达上界"与"当前 agent 差距"分离。**关键警示：电商购物车/总额是 GUI-only、无后端 API**（CartManagementTask 被判 GUI-only）。全仿真零真机、纯 CLI、无 sim-to-real 对照。$8K frontier API 成本佐证全真机不可持续。

**② PhoneBuddy / PhoneWorld（腾讯混元，arXiv 2606.23049）** — 从真机 GUI 轨迹**离线重建可运行 mock app**：恢复四类结构（哪些页关键 / 页怎么连=页面转移图 / 支持哪些动作 / 哪些状态要存），产出 `read-only content + writable state` 状态机 + rule-based verifier，并派生 tasks + verifiers。方法论：**"real 锚定真实性、mock 扩规模与可验证性"三段式**（共享 SFT 950K steps → Real / Real+Mock 分支 RL）。结果：Real+Mock 比 SFT +12.2，AndroidWorld 60.3→83.2。**红线：mock 可迁移当且仅当 grounded in realistic GUI structure**；**Cross-App 全程 18–22% 无提升**——明确警示跨 App 泛化 mock 救不了。（重建管线细节在未公开的姊妹论文 PhoneWorld，引用须降级为"高层启发"。）

**③ MobileForge / HiFPO（浙大快手，arXiv 2606.19930）** — 不造虚拟 App，把"真实但可复现的目标 App"包成自动化交互+评测基底 **MobileGym**：APK activity 列表当结构锚点做 function-aware 探索；**MobileGym-Critic 无学习的 agentic 分层 prompt 链**（步描述→final-decision JSON→失败反思生成 corrective hint）做 step 级裁决；corrective-hint 跨尝试累积让 rollout 52%→77%。可复现性来自 AndroidWorld 确定性环境，**明确回避电商活体干扰**（弹窗/captcha/AB/价格漂移）——这恰是 AMSG 的难点与卖点。

**三篇共同教训**：都用**外部独立来源**做 verifier 切断"用被测系统证明自己"；都把电商活体干扰当噪声规避而非建模。AMSG 的差异机会：图谱是**在线生长的活体状态机**（别人离线重建静态 mock），可被真机持续校准。

---

## 2  五个存活方案（去重 + 核验后排序）

排序依据：sim-to-real 有效性 × 数据可支撑度 × 解锁 RQ 价值。

### 方案 A（首选，FEASIBLE）：TrajReplay — 轨迹观测流离线回放器
合并 `TrajReplaySim` / `TrajectoryOracleReplay` / `Held-out轨迹回放`，三轮核验一致判 valid、sim-to-real 风险最低。
- **机制**：零渲染零设备。轨迹 `step_details` 的 `(source_page_type, action, observed_target)` 流逐步喂 `EdgeLifecycleManager.record_outcome(observed_target=轨迹实测下一页)` + t+1 后条件验证。**反循环关键：`observed_target` 是真机当时实测的页，独立于图谱预测**——被测的是"如何记账"，输入观测外生。同一流在 `sava vs legacy`、开/关后条件验证、N∈{1,3,5} 下重放，差异唯一归因于配置。
- **复用（已核验）**：`edge_lifecycle.py:204 record_outcome`（纯内存、零 Neo4j 依赖、`observed_target` 外部参数）、`amsg_config.py legacy()/sava()/from_env`、`check_demotion`/`_compute_stage`、`spatial_graph_memory.py record_observation`。造 `scripts/replay_lifecycle.py` ~200 行 + F2 去验证开关。
- **借鉴**：Beyond-the-GUI 二值 State-check/Cache-match；PhoneBuddy rule verifier 直读状态。
- **成本** S。**解锁** RQ2 全部 + RQ1a 增长机制。**唯一能让 RQ2 摆脱"用图测图"质疑的路径。**
- **能 claim**：`sava>legacy` 相对方向、记账逻辑正确性。**不能 claim**：N∈{3,5} 定量敏感性（语料零失败、`_expected_postcondition` 仅 1 点）、延迟。**必做**：披露"语料全成功、错误快路径率被低估"；20% 空/unknown page_type 步用 staging 标签清洗。

### 方案 B（必做元方案，FEASIBLE）：Sim2Real Gap 审计
合并 `Sim2RealGapAudit` / `双轨锚定`，核验一致判 valid 且 necessary。
- **机制**：不新建环境。同一批配对任务、同一 policy/接口，在 sim 与真机各跑一遍，报：① 机制排序 gap（`sava>legacy` 两侧是否一致，Kendall-τ）② 分布 gap（dispatch 分档、错误快路径率差）③ 绝对成功率 gap。再报孪生覆盖率、转移一致率、seed 方差。最后给**"可 claim 矩阵"**（哪些 sim≈real 可替代、哪些 Δ 显著必须真机）。
- **复用（已核验）**：`agent.py:195 step_observer` + `:1471 _tele`（被 `test_step_telemetry.py` pin 住）、`runtime_controller.py:106 get_runtime_metrics`。**注意 H1 ExperimentRunner / H4 聚合器是待建非现存。**
- **借鉴**：Beyond-the-GUI seed std + additivity 余弦 + realism κ；PhoneBuddy held-out 验证迁移。
- **成本** S（依赖 A + 真机表就位）。**解锁**：让所有 sim 结论可被审稿人采信。
- **边界**：若机制排序 sim≠real，是**诚实发现**（"sim 适用边界"），论文反而更可信。配对样本 thesis-scale 仅 10–20，CI 宽要诚实报。**"可 claim 矩阵"前置方法节，不进附录。**

### 方案 C（解硬阻塞，PARTIAL，M）：OracleGoldenPath — 人机协作金标准轨迹
合并 `OracleGoldenPath` / `VLM-as-Environment`。
- **机制**：照搬 Beyond-the-GUI 5 阶段 oracle 协议——强 VLM 当 oracle-agent，喂富上下文提**最短动作轨迹**，受控回放过 rule verifier，**人审 solvability+integrity**，通过后入库。三用途：① 喂图谱 promoted 边（**补 JD 缺失的 product_detail→spec_selection→cart→checkout 三条核心购买边**，`quality.missing_edges` 实测确认）；② 当方案 A 干净真值；③ 标 Oracle 天花板（= Fast Path 理论上界）。配 `NonGroundedResolver`（VLM 对非锚定页裁决"选哪个/是否满足约束"，temperature=0+缓存）补 `_UNGROUNDED_TRANSITIONS`（9 条）图谱无法确定性渲染的半壁。
- **复用（已核验）**：`exploration/planner.py` 强 VLM 规划器、`review/apply.py promote_staging_to_canonical`（F1 入口）、`runtime_controller.py:556` 防泄漏提示范例。⚠️ **核验纠正**：`VlmTransitionJudge`（`vlm_review.py`）是 approve/reject 二值，**不是 rubric 逐步打分，"扩 rubric"是新功能**；双盲只有单 VLM，无多标注者/κ 基建。
- **成本** M（人审环 + JD 高墙 captcha 人工接力）。**解锁** RQ4 京东冷启动 + RQ3 上界。
- **防循环**：oracle 拿含图谱富上下文，**必须人审"无验证器/答案泄漏"**；RQ1 纯在线绝不用 oracle 边（标 `source_type=offline_bootstrap`）。oracle=强 VLM、主执行器=端侧小模型，**天花板高≠小模型够得着，分开报**。verifier 限**结构性判定**（到达目标页/购物车含目标商品），不判"买到 X 元具体商品"。oracle 轨迹**必过真机 verifier 一次**。

### 方案 D（可做但限定定性，PARTIAL，M）：AMSG-Gym — 图谱固化离线确定性沙盒
合并 `GraphReplaySim` / `AMSG-Gym` / `GraphEnv` / `AMSG-Sim 概率核`。
- **机制**：Neo4j 图编译成 Gym 式环境：state=page_type 节点，promoted 边=确定性转移，非确定边按**真机校准的** OutcomeDistribution 种子采样；`reset/step/is_successful`，verifier 读终态。无设备无网络消除弹窗/captcha/AB/价格漂移。
- **复用（已核验）**：`edge_lifecycle.py:30 OutcomeDistribution`、`schemas/shopping.yaml`、`device_factory.py:157 get_device_factory`（单例注入点）、`_UNGROUNDED_TRANSITIONS`(9) 与 `VLM_VERIFY_TRANSITIONS`(5)（⚠️**两份不同常量，需先统一**）。造 `scripts/graph_env.py` ~300-600 行。
- **成本** **M（非 S）**。核验纠正：① **Fast-path（`agent.py:579`）硬编码 `classify()`+截图，"零像素旁路"必须改 agent.py 核心步进**（对接计划 F6）；② **staging 实测只有 1 个 JD 批次、7 状态 6 边、链路止于 cart，无 spec_selection/checkout——端到端购物链在任何 sim 里都跑不通**。
- **解锁** RQ3 dispatch 分档计数 + RQ1a 可控复跑（**延迟仍真机**）。
- **致命防循环**：**转移核成功/失败概率必须从真机轨迹原始频次估计，禁用图谱自身 promoted 结论**（这正是 `runtime_controller.py:277` 在防的，该 sim 反向打开了防护）。**只能 claim 机制消融，绝不能 claim 端到端成功率**（零像素=grounding 未测）。**"图谱即概率模拟器"abstract novelty 当前空心**：实测全单峰，`{spec:8,login:2}` 是 `edge_lifecycle.py:35` docstring 示例非数据。**补采前不可写进 abstract。**

### 方案 E（降级定性 demo，PARTIAL）：合成 UI 漂移注入 → RQ1b
- 在方案 D 环境里确定性损坏 N 条 promoted 边 target_locator/page_type，触发降级→重探索→重提升，记恢复任务数。复用 `check_demotion`（dominance<0.4）。计划 F4 `inject_ui_drift.py` **待建**。
- **边界**：防循环设计正确（漂移=环境外部篡改，调度器不知情）。但合成漂移是"干净"的一次性失效，真机漂移伴随弹窗/加载噪声，恢复偏乐观——标**"理想化下界"**，用真机一次真实 UI 更新做单点佐证。

### ❌ 判死（剔除）
- **GraphTwinSim**（真实截图回放 + 数据隔离留出）：**"数据隔离留出"在 3 个 JD batch + 6 条轨迹体量下不可行**（没有第二来源 promoted 边做正交切分）。降级为干扰触发逻辑的定性 demo，不进头条。
- **"干扰即变量"**（从 OutcomeDistribution 经验频率注入弹窗/captcha/AB）：**前提与数据直接矛盾**——实测干扰转移样本=0（captcha 被分类成 anomaly 人工接管、page_type 为空、没进分布）。作"纯合成扰动"可并入 E，作"数据驱动经验频率"是凭空假设。

---

## 3  推荐落地路径（四阶段，对接混合策略）

**模拟环境承担"离线机制消融的可复现替身"，真机锚定"端到端成功率 + 延迟 + grounding + 京东冷启动 + gap 度量"。**

1. **阶段 1（立即，1–2 周，纯软件零真机）**：方案 A（RQ2 机制方向，标 N≈4）+ 方案 B 报表骨架 + 落盘遥测（F3）+ **从零建 ExperimentRunner**（缺口④本身）。
2. **阶段 2（补数据，2–4 周，人机 + 少量真机）**：方案 C 补 JD 三条购买边；**⭐补采含失败/弹窗/登录墙/选错商品的真机轨迹（≥10 条）**——让 OutcomeDistribution 出现真实多峰、让 RQ2 有"会错"样本。**没有这步，RQ2 定量、概率核、干扰注入全部无地基。**
3. **阶段 3（造 sim，补采后，3–4 周）**：方案 D（转移核用阶段 2 真机频次校准）+ 方案 E。
4. **阶段 4（真机锚定收口）**：头条表 + RQ3 延迟 + 京东建图漏斗 + 方案 B 配对 gap 度量，"可 claim 矩阵"前置方法节。

**保真度校准闭环**：转移核概率参数**永远从真机频次估计**；`bulk_load` 的"verification_count 高者胜"合并语义天然支持"真机增量修正模拟"；每轮真机报一次 sim↔real 转移一致率，gap 扩大触发重校准。

---

## 4  诚实边界（绝不能宣称 / 审稿人攻击点 / 真机兜底）

**绝不能宣称**：① 离线 sim 端到端成功率≈真机（grounding 未测）；② N∈{3,5} 定量敏感性（补采前零失败、后条件真值仅 1 点）；③ "图谱即概率模拟器"为已验证 novelty（多峰为空）；④ "显式建模电商干扰频率"（样本=0）；⑤ 解决了 captcha（只能识别+暂停）；⑥ 任何 sim 延迟数字。

**审稿人三大攻击点**：①"用图测图吗？"→ 方案 A 外生真值 + 方案 D 真机频次校准接线讲到无歧义；②"sim 凭什么迁移？"→ 方案 B gap 度量 + 可 claim 矩阵；③"6 条全成功凭什么证 RQ2？"→ **唯一解是阶段 2 补采失败轨迹**，在此之前 RQ2 只报方向不报量级。

**必须真机兜底**：① RQ2 边可靠真值（held-out 真机 page_type + 补采失败样本）；② 所有延迟（RQ3）；③ 头条成功率 + grounding；④ 京东冷启动建图；⑤ sim-to-real gap 度量本身。

---

## 附：核验台账（已对活体仓库逐条核对）

| 声称 | 核验 |
|---|---|
| 6 条轨迹，2 条 2 步残桩，≈4 可用 | ✅ `20260605_111128/111216` 各 2 步 |
| 全成功零失败 | ✅ 全 `success=True` |
| spec_selection 全库 1 次 | ✅ `134014` |
| login/captcha/promo 转移=0 | ✅ |
| `_expected_postcondition` 全库 1 个 | ✅ 仅 `144630` |
| 18 张 PNG 分 3 JD batch | ✅ 7+6+5 |
| JD 缺 3 条购买边 | ✅ `quality.missing_edges`=[product_detail→spec_selection, spec_selection→cart, cart→checkout] |
| JD 边全单峰 sc:1/fc:0 | ✅ |
| `{spec:8,login:2}` 是 docstring 非数据 | ✅ `edge_lifecycle.py:35` |
| `unverified` 反循环防线真实 | ✅ `runtime_controller.py:277-283` |
| `record_outcome` 纯内存接外生真值 | ✅ `edge_lifecycle.py:204` |
| `ExperimentRunner` 不存在 | ✅ 缺口④ |
| Fast-path 硬编码 classify+截图 | ✅ `agent.py:579,623,630` |
| `_UNGROUNDED`(9)≠`VLM_VERIFY`(5) | ✅ `action_advisor.py:21` / `runtime_controller.py:22` |
| `device_factory` 全局单例注入点 | ✅ `device_factory.py:143-167` |
| `inject_ui_drift.py`(F4) 不存在 | ✅ 待建 |
