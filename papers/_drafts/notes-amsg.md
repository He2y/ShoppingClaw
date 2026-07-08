# AMSG 研究笔记（供论文写作 agent 使用）

> 来源：深度阅读 `phone_agent/docs/AMSG_DESIGN.md`（版本 2026-06-10，多 App 泛化版）。
> 所有源码位置均已对照仓库实际文件核验（行号为当前 `refactor/architecture-optimization` 分支）。
> **重要警示**：项目内部审计（见记忆条目"架构文档审计结论"）曾在 ARCHITECTURE 文档中发现多处论文级失实。本文档中所有数字在写入论文前必须按第 ③ 节的性质标注处理——绝大多数不可直接作为实验结果引用。

---

## ① 核心机制清单

### A. 图谱表示层

**A1. 语义页面状态与确定性签名去重**
- 解决问题：截图哈希级去重导致图谱膨胀且无法复用（搜"iPhone"和搜"耳机"截图不同但页面结构相同）。
- 怎么做：UIState 不存截图，存语义元组 `PageState = (app, page_type, landmarks, affordances, slots, risk_level)`；去重靠确定性签名 `app|domain|page_type|landmarks|affordances|slots`，签名相同的观测合并到同一节点。
- 源码：`phone_agent/spatial/core.py:35` `semantic_signature()`。

**A2. 语义动作节点（坐标只作锚定证据，不参与去重）**
- 解决问题：坐标级动作跨设备不可迁移；同一意图在不同分辨率下会裂成多个节点。
- 怎么做：`Action = (type, intent, semantic_target, postcondition, lifecycle_stage)`；坐标作为 `target_locator` 附加属性。去重键 `source_page_type|intent|semantic_target|region|target_page_type`。
- 源码：`phone_agent/memory/graph_store.py:892` `_semantic_action_key()`。

**A3. 关系级执行统计**
- 解决问题：现有方法的边是布尔存在性，无可靠性度量（见 §1.2 批评）。
- 怎么做：每条关系记录 `frequency`（成功次数）、`fail_count`、`confidence = frequency / total`，作为生命周期系统的数据基础。
- 源码：`phone_agent/memory/graph_store.py`、`phone_agent/spatial/edge_lifecycle.py`。

**A4. 多 App 单库逻辑分区 + AppRegistry**
- 解决问题：多 App 图谱组织；跨 App 聚合查询（泛化实验核心）在多库下需要 Neo4j Enterprise Fabric。
- 怎么做：单 Neo4j 库内按 `app` 属性逻辑分区；每节点写 `app`（规范 id，程序检索作用域键）、`app_raw`（人工浏览显示名）、`domain`（跨 App 聚合分区键）三字段；复合索引 `(app, page_type)`、`(domain, page_type)`。AppRegistry 统一管理包名/中英文名/历史编码变体到规范 id 的映射。
- 实战教训（可写入 discussion/lessons）：规范合并后边的两端可能一端 `jd` 一端 `京东`，精确相等比较会静默丢边——一切 App 比较必须走别名感知 `_same_app()`。
- 源码：`phone_agent/spatial/app_registry.py`、`phone_agent/spatial/schemas/app_registry.yaml`、`GraphStore.ensure_indexes()`（`phone_agent/memory/graph_store.py`）。

**A5. 领域模式（domain schema）作为结构先验 + 开放词表进化**
- 解决问题：无约束生长的图谱噪声大；封闭词表是泛化瓶颈（真机实例：外卖商家页被硬塞成 product_detail）。
- 怎么做：`shopping.yaml` 定义页面类型、风险等级、典型转移、覆盖目标、危险动作词表（common→域→App 三级累加）、干扰页集合、陷阱词表、默认探索任务模板（`{app}` 占位）；分类器系统提示词完全由模式生成。开放词表：分类器遇词表外页面输出 `new:<类型名>` + 一句话定义进提案池，出现 ≥2 次才浮出、永不直接建边，人工审核后合入域模式或 App profile。
- 验证案例：给 shopping.yaml 加即时零售类型（channel_home/shop_list/store 扩展）后，京东秒送外卖链路零代码改动即被正确分类。
- 源码：`phone_agent/spatial/schemas/shopping.yaml`、`phone_agent/spatial/schema_registry.py`、`phone_agent/memory/exploration/classifier_prompts.py`（`build_fast_prompt`）。

### B. 运行时集成层（"空间感知"）

**B1. 锚定性（grounded）分类——图谱与 VLM 的边界划分**
- 解决问题：PG-Agent（图谱只做 RAG，VLM 始终推理，不省调用）与 WebNavigator（图谱直接 Teleport，无回退）两个极端都不区分"图谱可独立完成的转移"与"必须看屏幕才能决定的转移"。
- 怎么做：每条边标注锚定性。锚定边（如 home→search_input，按钮位置固定）走快速路径（~0.5s，跳过 VLM）；非锚定边（如 search_result→product_detail，选哪个商品取决于任务）走 VLM 路径（~5s，图谱提供方向提示）。快速路径判据：`grounded=True` AND `confidence >= 0.9` AND 有坐标或复合步骤。
- 源码：`phone_agent/spatial/action_advisor.py:57` `ActionAdvisor.is_fast_executable()`。

**B2. 统一定位入口 locate_and_get_context() 与 mode 调度**
- 解决问题：图谱知识如何嵌入 Agent 每步循环。
- 怎么做：Agent 循环 Phase 3 调用，一次完成：页面匹配（(app,page_type) 查 Neo4j）→ 验证上步 pending transition → 目标推断（TaskSlots→GoalSpec）→ Dijkstra 加权最短路 → 缓存 RuntimeDAG → 返回 `mode ∈ {navigate, explore, verify_with_vlm, goal_reached}` 直接驱动 Phase 5 调度。
- 源码：`phone_agent/spatial/runtime_controller.py:137`（`GraphRuntimeController.locate_and_get_context`）；`phone_agent/memory/memory_manager.py:1700`（委托入口）。

**B3. RuntimeDAG 路径缓存**
- 解决问题：完整管线（Neo4j 查询 + 规划 + 上下文组装）每步 50-200ms，重复规划浪费。
- 怎么做：把 Dijkstra 结果缓存为内存边数组 + 游标；DAG 命中时 ~10ms 取 `route[current_index]`。失效条件：后条件不匹配 / 应用切换 / 连续失败超阈值 / 下一步高风险，失效后下次 Phase 3 自动重建。副作用：DAG 可用时 `should_use_page_classifier()` 返回 False，跳过 Phase 1 的 PageClassifier VLM 调用（代价：弹窗出现时带错误 page_type 走一步，由延迟后条件验证在下步捕获）。
- 源码：`phone_agent/spatial/runtime_controller.py`（含 `should_use_page_classifier`:82）。

**B4. 延迟后条件验证（t+1 验证）——生命周期系统的事实来源**
- 解决问题：没有后条件验证，成功率统计的数据来源是"Agent 以为会到哪"而非"实际到了哪"。PG-Agent/KG-RAG/WebNavigator 均无此机制（论文可作核心论点）。
- 怎么做：Phase 6 暂存 `(当前页面, 动作, 预期目标)` 为 pending transition；下一步 Phase 3 开头对比实际 page_type 与预期，匹配→`record_observation(outcome="success")`，不匹配→failure + 修复策略。
- 源码：`phone_agent/memory/spatial_graph_memory.py:1618` `record_observation()`；暂存逻辑在 `phone_agent/agent.py` Phase 6 / `memory_manager.py`。

**B5. 上下文注入（图谱知识进 VLM 提示）**
- 怎么做：两通道——导航上下文（mode=navigate 时注入路径概要 + 下一步动作描述，VLM 可采纳可忽略）；动作提示（`ActionAdvisor.query()` 返回当前页所有 promoted 边，非锚定边只给"转移存在"不给坐标）。
- 源码：`phone_agent/spatial/action_advisor.py`；优先级表见 ARCHITECTURE.md §3.3。

**B6. 多 App 分层检索 / 同域结构先验**
- 解决问题：新 App 冷启动无可用边。
- 怎么做：三层检索，只有第一层产生可执行动作：① App 专属子图（promoted 快速路径唯一来源）；② 同域结构先验——App 子图过冷且 mode=explore 时，注入同域其他 App 在该页面类型的常见转移（schema 转移 + 跨 App promoted 边聚合，按支持度排序），**仅作文本提示、绝不携带坐标**（杜绝跨 App 坐标污染）；③ common_mobile 兜底（dialog/permission/login/captcha 处置知识）。效果叙事："京东冷启动借淘宝的购物结构方向感"。
- 源码：`phone_agent/spatial/domain_priors.py`；开关 `AMSG_DOMAIN_PRIORS=0`。

### C. 学习与进化层

**C1. 自动持久化管线（核心规则：没有原始动作直接写 Neo4j）**
- 怎么做：任务执行期观测全在内存暂存（`_local_states`/`_local_edges`）；`end_task(success=True)` 才触发 `flush_staged_graph()`，三步：① `canonicalize_state_graph()` 语义签名去重 + 过滤 unknown 瞬态页 + 应用一致性检查；② 生命周期提升检查；③ MERGE 写入 Neo4j（幂等）。
- 源码：`phone_agent/memory/memory_manager.py:276` `end_task()`；`phone_agent/memory/spatial_graph_memory.py:1685` `flush_staged_graph()`。

**C2. 质量门 1：任务成功才写入**
- 解决问题：失败任务的观测可能整条是错误导航，写入会污染规划。
- 怎么做：`end_task(success=False)` 不调用 flush，整条丢弃（"一个 20 步失败任务可能 15 步是错误操作，丢弃比逐步审核更高效"——可作设计论点）。

**C3. 质量门 2：VLM 轨迹审核**
- 解决问题：分类器误判产生的虚假转移（如 dialog→home 实为关闭广告弹窗）逃过规范化过滤。
- 怎么做：flush 后 `TrajectoryReviewer.review_and_import()` 从轨迹 JSON 提取转移链 → 过滤伪转移 → 对 Neo4j 去重 → 新候选交强 VLM（`AMSG_STRONG_VLM_MODEL`）判真伪 → 仅批准者以 hypothesis 导入。两个失败安全细节（可写成 lessons）：VLM 解析失败标"未审核"而非全通过（早期实现是危险的反向回退）；导入用语义键 MERGE 而非 CREATE（避免重跑重复节点）。共享 `VlmTransitionJudge` 同时服务在线与离线审核。
- 源码：`phone_agent/spatial/trajectory_reviewer.py:89`；`phone_agent/spatial/review/vlm_review.py`。

**C4. 四阶段边生命周期（hypothesis → candidate → promoted → demoted）**
- 解决问题：静态图谱无法应对应用更新（§1.1 批评），边可靠性不区分（§1.2 批评）。
- 怎么做：hypothesis/candidate/demoted 对 Agent 不可见，只有 promoted 可见（锚定→快速路径，非锚定→VLM 提示）。提升条件：`verification_count >= N` AND `dominance_ratio >= θ` AND `risk != high`；降级条件：promoted 边成功率跌破 40%。人工批准的边也不豁免——hypothesis 仍需在线后条件验证才能提升。高风险目标边（cart→checkout）可为结构完整性存在但永不成为快速路径。
- 源码：`phone_agent/spatial/edge_lifecycle.py:204` `EdgeLifecycleManager.record_outcome()`。

**C5. 自修复（无人工干预的 UI 漂移恢复）**
- 怎么做：promoted 边失效 → 后条件验证记 failure → dominance_ratio 下降 → 跌破阈值降级 → Agent 回退 VLM 重新探索 → 新观测建 hypothesis → 验证后重新提升。统计偏移即 UI 变更信号，无需人工检测。文档称通常 3-5 次任务内完成（估算，见 ③）。

**C6. 跨任务正反馈循环（自进化）**
- 叙事链：成功任务 → 图谱增长 → 更多 promoted 边 → 更多快速路径命中 → 更少 VLM 调用 → 任务更快更稳 → 进一步强化。收敛性论证：页面类型有限（典型购物 app 8-12 种核心页面、15-25 种常用转移），10-20 次成功任务后核心流程覆盖，此后以强化为主。

### D. 陌生 App 建图管线（五阶段闭环，`phone_agent/memory/exploration/`）

设计原则两条（可直接写入方法节）：① 真实 App 环境高度干扰，**逃逸机制不得依赖 VLM 分类正确**（用感知哈希、覆盖进度、前台包名等机械信号兜底）；② **离线探索产物一律不直写正式图谱**（人工审核是离线数据的质量门）。

**D1. 阶段一：App 引导（onboarding）**
- 安全采样（启动→Back→下滑各一图，绝不点击）→ 强 VLM 推断领域并产出 draft profile（domain 映射、覆盖建议、App 界面语言下的危险词/陷阱词、登录墙标记、建议任务）→ 校验层把 VLM 输出对齐模式（别名规范化、剔除干扰类型、越模式转移丢弃、与购物 CTA 冲突的"危险词"降级）→ **人工确认门**：draft 可探索但拒绝入图，人工核对后改 confirmed。
- 源码：`phone_agent/memory/exploration/onboarding.py`；产物 `spatial/schemas/apps/<id>.yaml`。

**D2. 阶段二：分轮焦点探索（规划-执行分离）**
- 关键实证发现（论文可作 motivation）：小 GUI 模型（autoglm-phone-9b）能精确 grounding 但**没有规划能力**——不知道覆盖 search_result→filter_panel 需要点筛选按钮，永远在主链路打转。
- 怎么做：强 VLM 规划器每步看截图 + 剩余焦点转移 + 最近执行结果，输出一条具体指令；GUI 模型只收这一条指令做 grounding，看不到宏大任务；执行结果回灌规划器，失败换打法，覆盖完毕主动 finish。分轮焦点：每轮（24 步）从历届覆盖缺口取一个小目标（骨架优先），完成即收尾；`--focus` 支持结构化与自然语言混用。轮末强 VLM 生成 round_summary。
- 防御栈（表格可直接复用，威胁→机械信号防御）：感知哈希滞留检测 + 逃逸升级（Back→Back→重启）、前台包名漂移拉回、弹窗消解循环、captcha 一等页面类型 + 默认蜂鸣暂停人工接力（人工操作不记边）、登录墙 `--pause-on-login`、落页稳定性检测（连续截图相似才分类）、三信号一致性（分类器/推理证据/landmark 核验）、中断安全保存。
- 源码：`exploration/planner.py`、`interference.py`、`watchdog.py`、`stability.py`、`confidence.py`、`explorer.py`。

**D3. 阶段三：staging 规范化**
- 每轮产物自动打包为 staging 批次（`memory_db/staging/<batch>/`）：语义签名去重、瞬态页过滤、Type+Submit 搜索宏合成、同页动作压缩、高风险边界过滤。**只落磁盘不碰 Neo4j**。
- 源码：`phone_agent/memory/spatial_graph_memory.py:618` `import_exploration_staging()`。

**D4. 阶段四：人工审核门（Gradio）**
- 逐条审核：前后截图对 + 动作描述 + 观测次数/置信度 + VLM 预判 + **双盲交叉验证**（强 VLM 独立重分类两端截图、不告知探索时标签，不一致标红置顶）。批次概览显示完整漏斗（探索 N → 探索期拒 K → 候选 M → approve/reject），无静默丢失。决策预填规则：VLM 批准 / 观测 ≥2 且非低置信 / 人工辅助采集 → approve。
- 源码：`phone_agent/spatial/review/webui_review.py`、`batch.py`、`decisions.py`。

**D5. 阶段五：批准入图（hypothesis）**
- `apply_review()` → `promote_staging_to_canonical()` 写入 Neo4j：`lifecycle_stage="hypothesis"` + 溯源属性（`source_type=exploration_reviewed`、`review_batch_id`）。人工批准不豁免在线验证。重复 apply 由 applied.json 回执拦截。
- 源码：`phone_agent/spatial/review/apply.py:18`；`phone_agent/memory/spatial_graph_memory.py:1141`。

**D6. 人工辅助采集**
- 操作者（或更强 Agent）直接驱动设备采集，同格式落盘走同一 staging→审核→入图管线，`source_kind=human_assisted`。定位：给图谱注入"转移→操作"执行知识的捷径。

**D7. 在线学习作为持续来源**
- 离线管线解决冷启动；持续数据来源是在线学习（C1-C4）。两套质量门相互独立。

---

## ② 可引用的对比论点（vs 现有工作）

### 对 PG-Agent [Chen et al., ACM MM 2025]
- 共识起点（论文写作可先承认）：页面天然成图、线性轨迹是图上路径采样、多轨迹重建图优于孤立轨迹——这个直觉是对的。
- 批评 1（静态）：从预收集 episode 批量建图，在线只读不写。应用更新（文档称平均 2-4 周一次，**需找外部文献支撑**）使边失效但系统不自知（不追踪执行结果）；新页面无法写回，每次重新探索。
- 批评 2（边无可靠性）：所有边同等对待，只记"A 经 X 可达 B"。同一操作多结果（登录拦截、A/B 弹窗）全部当有效转移存入，规划器可能选只在特定条件下成立的路径。
- 批评 3（边界模糊）：图谱只是 RAG 源，VLM 仍每步推理——图谱不减少 VLM 调用，只改善提示内容。
- 批评 4：无后条件验证。

### 对 KG-RAG
- UTG 向量库由 xTester 爬取、离线构建后冻结（同静态批评）；不追踪边可靠性；同 PG-Agent 属"RAG 文本→VLM 始终推理"模式；无后条件验证；单 App。

### 对 WebNavigator
- 走另一极端：Retrieve-Reason-Teleport，图谱直接控制导航，VLM 只管页面内操作。Teleport 假设图谱总是正确——边失效（app 更新）时无回退机制。
- DOM 哈希去重但不追踪单边成功/失败统计；噪声控制靠 DOM 差分；单站点；无后条件验证。

### 对 MobiAgent / AgentRR
- 记录执行轨迹，但"是否可复用"由二元匹配模型判断，无边级可靠性度量；图谱更新靠手动纠正轨迹；无后条件验证、无自修复；单 App。

### 逐维度对比表（§7 原表，可直接改写为论文表格）

| 维度 | PG-Agent | KG-RAG | WebNavigator | MobiAgent | AMSG |
|---|---|---|---|---|---|
| 图谱来源 | episode 批量构建 | xTester 爬取 UTG | 自适应 BFS | 任务执行记录 | 引导式探索 + 人工审核 + 在线学习 |
| 图谱更新 | 不更新 | 不更新 | 不更新 | 手动纠正轨迹 | 每次成功任务自动更新 |
| 边可靠性 | 不追踪 | 不追踪 | 哈希去重 | 不追踪 | 成功率追踪 + 四阶段生命周期 |
| VLM/图谱边界 | RAG→VLM 始终推理 | 同左 | Teleport 无回退 | 二元重放 | 每条边独立判断（锚定→图谱/非锚定→VLM） |
| 噪声控制 | 双层相似度 | BFS 评分 | DOM 差分 | 无 | 四道门（任务成功 + VLM 审核 + 规范化 + 离线人工审核） |
| 后条件验证 | 无 | 无 | 无 | 无 | t+1 延迟验证，成功率基于真实观测 |
| 自修复 | 无 | 无 | 无 | 无 | 成功率下降→自动降级→VLM 重新探索 |
| 跨 App 泛化 | 单 App | 单 App | 单站点 | 单 App | 域模式复用 + 同域结构先验 + 开放词表进化 |
| 探索规划 | — | 爬虫 | BFS | — | 强 VLM 规划/GUI 模型执行分离，分轮焦点 |

### 五条核心差异论点（§7.1，可作 contribution 框架）
1. **活的图谱 vs 冻结的图谱**：每次成功任务自动增长，可靠性下降自动收缩。"Active"是名字中最关键的词。
2. **边的成功率 vs 边的存在性**：只有统计上可靠的边进入可执行动作库。
3. **每条边独立判断控制强度 vs 一刀切**：避免 PG-Agent（永远 RAG，不省调用）和 WebNavigator（永远 Teleport，无回退）两个极端。
4. **真实后条件验证 vs 无验证**：没有它，成功率追踪的数据来源是假设而非事实——这是生命周期系统正确性的前提。
5. **可复制的陌生 App 建图流程 vs 一次性数据集工程**：五阶段管线对任意同域 App 可复制（京东含秒送链路按此流程从零完成入图，并反哺域模式）。

⚠️ 写作注意：对四个 baseline 的描述均来自本设计文档作者的转述，论文写作前必须回到各原始论文核实（尤其 PG-Agent 的引用格式 "Chen et al., ACM MM 2025"、KG-RAG/WebNavigator/MobiAgent 的确切机制与发表处）。

---

## ③ 全部可引用数字及性质标注

**结论先行：本文档中没有任何数字可直接当实验结果引用。** 分类如下：

### 纯虚构示例（绝对不可引用为数据，仅可作"假想例"修辞）
- "点击立即购买：73% 到规格选择页 / 18% 到登录页 / 9% 到促销弹窗" —— 说明性编造数字。
- "2 月构建的页面图到 4 月可能有 30% 的边已失效" —— 假设性推演，无测量。

### 估算/经验值（文档自己标注或语境表明是估算；可作设计动机叙述，不可作结果）
- 移动应用平均每 2-4 周更新一次 —— 行业常识级断言，**需外部文献支撑**。
- 快速路径 ~0.5s vs VLM 路径 ~5s —— 量级估计。
- 完整定位管线 50-200ms；RuntimeDAG 命中 ~10ms；Dijkstra 微秒级 —— 工程量级，未给测量方法。
- §6.2 延迟表（冷启动 ~100s=20×5s / 早期 ~65-80s / 稳定期 ~30-45s；快速路径步数 0 / 5-8 / 12-15）—— **表头自标"估算"**，不可作实验结果。
- 典型购物 app 8-12 种核心页面、15-25 种常用转移 —— 经验估计。
- 10-20 次成功任务后核心流程基本覆盖 —— 经验估计。
- 自修复通常 3-5 次任务内完成 —— 经验估计。
- "20 步任务产生约 20 个暂存观测" —— 定义性陈述，非数据。

### 实现常量（可如实引用为系统配置，引用时注明是设计参数而非调优结果）
- 快速路径门槛：`grounded=True` AND `confidence >= 0.9`（`action_advisor.py`）。
- 降级阈值：promoted 边成功率跌破 40%。
- 提升条件：`verification_count >= N` AND `dominance_ratio >= θ` AND `risk != high`（N、θ 文档未给值，**需查源码 `edge_lifecycle.py` 确认具体数值**）。
- 探索每轮 24 步。
- 开放词表提案 ≥2 次出现才浮出。
- 审核预填规则中"观测 ≥2 次且非低置信"。
- 当前图谱规模 < 50 节点；Belief-A* 增强项贡献 < 0.1 —— 内部观察，作为"默认关闭"的理由可述，不可作消融结果。

### 定性实测事实（可引用为系统状态陈述，但无量化指标）
- 已在淘宝（核心流程）与京东（核心流程 + 秒送外卖链路）两个真实 App 完成端到端验证 —— 定性事实，论文需补充量化（成功率、步数、延迟、覆盖率等需重新实测）。
- 小 GUI 模型（autoglm-phone-9b）能 grounding 但无规划能力 —— 真机观察，作为设计动机可引，作为 finding 需正式实验。
- 京东秒送链路在模式扩展后零代码改动被正确分类 —— 定性案例。

---

## ④ 消融开关清单（§8）

### 已实现、可直接做消融的开关
| 开关 | 关闭后的基线含义 |
|---|---|
| `--no-strong-planner` / `AMSG_STRONG_PLANNER=0` | 单模型自主探索（"小模型规划失败"基线） |
| `build_fast_prompt(open_vocab=False)` | 封闭词表分类（"泛化失败"基线） |
| `AMSG_DOMAIN_PRIORS=0` | 无同域结构先验（冷启动对照） |
| `--no-staging` | 关闭 staging 自动打包 |
| `--no-human` | 关闭人工接力 |
| `--transition-policy strict / schema_guided / permissive` | 转移校验强度三档 |

### 已实现但默认关闭（需先消融验证才能写为贡献）
- 多通道信念定位（`use_multi_signal_belief=False`）：(app,page_type) 匹配上叠加视觉/语义/时序贝叶斯更新。
- Belief-A* 规划（`planner_backend="dijkstra"` 默认）：Dijkstra 上叠加过期衰减、探索奖励（当前 <50 节点规模下贡献 <0.1）。
- 熵驱动 VLM 验证边界：按结果分布 Shannon 熵决定是否需要 VLM（当前由域模式 `vlm_verify_transitions` 静态配置）。
- 消融预设矩阵：`AMSG_CONFIG` 切换 6 配置（legacy / edge_only / belief_only / planner_only / full / sava）。**坑位注意**：离线建图管线固定 plausibility 策略——sava 的 verified 边策略要求运行时后条件记录，对离线数据会过滤掉全部候选。

### 建议论文消融矩阵（笔记作者建议，非文档原文）
核心四消融对应四个核心差异：① 去掉生命周期（所有边即 promoted）→ 验证可靠性追踪价值；② 去掉锚定分类（全 VLM 或全快速）→ 验证逐边边界价值；③ 去掉后条件验证 → 验证事实数据来源价值；④ 去掉域先验/开放词表 → 验证跨 App 泛化价值。前两者可用 AMSG_CONFIG 的 edge_only/legacy 近似。

---

## ⑤ 方法叙事主线建议（问题 → 洞察 → 设计 → 闭环）

**第一幕（问题）**：承认 PG-Agent 的直觉正确——页面成图、轨迹是路径采样。但指出现有图谱方法共享三个根本缺陷：(1) 静态——建完即冻，应用每 2-4 周更新使图谱腐烂且系统不自知；(2) 边是布尔的——无可靠性区分，多结果转移全部存入污染规划；(3) 图谱/VLM 边界一刀切——要么永远 RAG（不省调用），要么永远 Teleport（无回退）。

**第二幕（洞察，三条一一对应）**：
1. 图谱必须是"活的"——执行本身就是免费的验证数据流，关键是把"Agent 实际到达哪"（而非"以为会到哪"）作为事实来源 → 引出 t+1 延迟后条件验证。
2. 边的价值在统计可靠性而非存在性 → 引出成功率追踪 + 四阶段生命周期 + 自动降级自修复。
3. 转移天然分两类：位置固定的机械转移（锚定）与依赖屏幕内容的决策转移（非锚定）→ 引出逐边控制强度划分，锚定且可靠走快速路径省 VLM，非锚定永远留给 VLM。

**第三幕（设计）**：按"表示（语义签名/动作键/域模式/多 App 分区）→ 运行时（locate_and_get_context 四模式调度 + RuntimeDAG + 上下文注入 + 分层检索）→ 学习（双质量门自动持久化 + 生命周期）→ 冷启动（五阶段建图管线，规划-执行分离 + 人工审核门）"展开。强调统一不变量："没有原始动作直接写入 Neo4j"——一切入图须过质量门；"人工批准不豁免在线验证"——hypothesis 对 Agent 不可见直至统计达标。

**第四幕（闭环）**：自进化正反馈——成功任务→图谱增长→更多快速路径→更少 VLM 调用→更快更稳→进一步强化；配收敛性论证（页面类型有限）与自修复叙事（UI 漂移→统计偏移→降级→重探索→重提升，无人工干预）。落点：图谱从"导航知识库"升格为"自进化动作库"（标题立意），离线管线与在线学习两条供给线、同一套生命周期消费线。

**写作提醒**：
- 实验章节所有量化数字必须重新实测，§6.2 延迟表只能作为预期趋势示意。
- 故事中的"实战教训"素材（别名丢边、VLM 解析失败反向回退、CREATE 重复节点、外卖页硬塞 product_detail 催生开放词表、captcha 人工接力不记边）适合放 Design Decisions / Lessons 小节，能显著增强系统论文可信度。
- 配图素材：`phone_agent/docs/图谱设计.png`、`phone_agent/docs/图谱构建管线.png`（文档内嵌引用，注意 git 状态显示 docs 下多个 svg/png 已删除，写作前确认这两张图仍存在）。
- 关联文档：ARCHITECTURE.md §3.2/§3.3/§3.4（Agent 循环集成细节）；注意内部审计曾对 ARCHITECTURE 提出失实修正，引用前核对最新版。
