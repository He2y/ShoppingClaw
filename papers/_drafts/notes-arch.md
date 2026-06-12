# 研究笔记：Shopping-Agent 系统架构（供论文写作 agent 使用）

> 来源：`phone_agent/docs/ARCHITECTURE_CN.md`（版本 2026-06-12，强 VLM 里程碑监督版 + 系统性缺陷审计与鲁棒性强化）；对照英文版 `ARCHITECTURE.md`。
> 文档性质：已逐项对照代码核验，经三维度并行审计与六项运行时缺陷修复；已在淘宝（核心购物链路端到端）与京东（含秒送外卖链路）真实 App 验证。
> 一句话定位：**以小型 GUI 模型为主执行器、强 VLM 低频监督、空间图谱引导的移动 GUI 智能体**。核心命题：如何让可量化端侧部署的小模型在长程任务上达到接近大模型的可靠性——答案是三层确定性结构（图谱加速机械导航、机械护栏接管确定性判断、强 VLM 里程碑低频介入），而非把决策权交给云端大模型（已验证每步延迟翻三倍且违背端侧理念）。

---

## ① 系统全部模块清单

系统 = 三个逻辑运行区域（决策 / 图谱 / 执行）+ 一个监督层 + 支撑子系统（记忆、分类器、模型/设备抽象、WebUI）。

### 监督层（独立于运行时三区域，低频介入）

| 模块 | 职责 | 关键类/文件 | 与其他模块的接口 |
|---|---|---|---|
| 里程碑监督者 | 任务拆解（initialize）、里程碑修订与核实（checkpoint）、完成核实（final_confirm） | `MilestoneSupervisor` / `phone_agent/milestone_supervisor.py` | 通过会话记忆文件与 TaskPlan 与决策区域解耦：监督层修订记忆与计划，决策区域读取执行；复用 `PageClassifier` 的 provider 链与截图压缩基建 |
| 会话记忆文件 | 抗遗忘持久锚；监督者与执行器的解耦接口。纯数据模块，无 VLM 依赖 | `SessionMemoryFile` / `phone_agent/session_memory_file.py` | `render_injection()` 每步注入小模型上下文；`brief_line()` 写入 `state.overall_progress`；`sync_subtasks_from_plan` 与 TaskPlan 双向同步；落盘 `memory_db/{user}/sessions/{session_id}.json`（原子写：`.json.tmp` + `os.replace`） |
| 任务计划 | 子任务序列追踪（稳定 step_id）、原子修订 | `TaskPlan.apply_revision` / `phone_agent/task_plan.py` | 接收监督者修订（done/skipped 不可改写、pending 尾部全量重建、≤12 步上限、非法修订整体拒绝）；镜像回会话记忆文件 |
| 里程碑触发器 | 四触发器去抖（纯逻辑，详见 ④） | `MilestoneTrigger`（在 agent 侧） | 判定点位于 `_execute_step_impl` 计划同步块之后，每步至多触发一个 |
| 每步规划器（消融上界） | 每步强 VLM 规划，与里程碑互斥 | `phone_agent/step_planner.py`（`PHONE_AGENT_STRONG_PLANNER=1`，默认关） | 启用时里程碑自动停用 |

### 决策区域（回答"做什么"）

| 模块 | 职责 | 关键类/文件 | 接口 |
|---|---|---|---|
| 主循环/编排 | 唯一编排入口；九阶段执行流；三档调度选择 | `PhoneAgent._execute_step_impl()` / `phone_agent/agent.py`；`_execute_step` 为遥测包装层（每步恰好发一个 step_observer 事件） | 调图谱区域取定位与建议；调监督层修订计划；应用机械护栏与 SpecGuard |
| 购买安全守卫 | 购买提交点硬拦截（价格越界 → 替换为 Interact 询问用户）；机械价格裁决（`_extract_price_bounds`） | `SpecGuard` / `phone_agent/core/spec_guard.py` | 守卫 `product_detail`/`spec_selection`/`checkout`/`payment` 页；Phase 7 在 canonical action 计算后介入 |
| 验证检测 | 登录/验证码/短信页检测，阶梯接管（1-3 次自动接管、4-5 次让模型试、>5 次失败） | `detect_verification` / `phone_agent/verification_detector.py` | Phase 3 调用 |
| 澄清 | 首步三层短路澄清：规则检查 → FAISS 偏好填充 → 强 VLM 判断是否追问 | `ClarificationAgent` | 仅第 1 步运行（Phase 4） |

### 图谱区域（回答"在哪、走哪"；只做结构性导航，不做语义判断）

| 模块 | 职责 | 关键类/文件 | 接口 |
|---|---|---|---|
| 运行时图谱控制器 | 一次调用完成：定位 `(app,page_type)` 匹配 Neo4j → 验证上步 pending transition → Dijkstra 规划 → 缓存 RuntimeDAG | `GraphRuntimeController.locate_and_get_context()` / `phone_agent/spatial/runtime_controller.py` | 返回 `mode`（navigate/explore/verify_with_vlm/goal_reached）+ `next_actions` + `graph_hint`（独立键，不污染个性化记忆）；mode 直接驱动决策区域调度 |
| 动作顾问 | 提升边（promoted）的快执行提示；搜索 Compound 边合成（`_append_search_compound`） | `ActionAdvisor` / `phone_agent/spatial/action_advisor.py` | `ActionHint.is_fast_executable()` 供 Phase 6 Fast Path 门控 |
| 边生命周期 | 状态机 `hypothesis → candidate → promoted → demoted`；成功率/优势比/熵统计；单通道记账 | `EdgeLifecycleManager` / `phone_agent/spatial/edge_lifecycle.py` | 后条件验证（t+1 验证 t）喂计数；仅 promoted 边对 ActionAdvisor 可见；Neo4j 写入仅在任务成功结束 flush |
| RuntimeDAG | Dijkstra 结果缓存为内存边数组+游标；`should_use_page_classifier` 可省分类调用 | 在 `runtime_controller.py` 内 | 失效被动（后条件失配/应用切换/高风险/路径耗尽），自动重建 |
| 存储 | Neo4j（跨会话图谱）/ FAISS（偏好）/ JSON（轨迹、会话） | — | 详细设计见 `phone_agent/docs/AMSG_DESIGN.md` |
| 图谱清理工具 | 离线清理无效条目 | `scripts/purge_invalid_graph_entries.py` | — |

### 执行区域（回答"怎么操作设备"）

| 模块 | 职责 | 关键类/文件 | 接口 |
|---|---|---|---|
| 模型客户端 | 驱动小模型推理；流式 token 回调 | `ModelClient`（`stream_callback`） | Phase 6 模型路径 |
| 模型协议桥 | 五模型家族原生输出归一化为 `DeviceActionIR`；坐标空间互转枢纽 `_scale_coord()`（经 (0,1) 归一化中间表示） | `phone_agent/model/protocol_bridge.py` | 使图谱模型无关：同一 Neo4j 图谱服务不同模型 |
| 模型适配器 | AutoGLM / UI-TARS / Qwen-VL / MAI-UI / GUI-Owl 五家族 | `phone_agent/model/adapters.py` | 坐标系与上下文策略见 ⑥ 附表 |
| 动作处理器 | 规范动作字典 → 设备调用；键盘管理（切 ADB 键盘、清空、输入、恢复 IME）、时序延迟、同页复合动作 | `ActionHandler` | Phase 7 |
| 设备抽象 | ADB（Android）/ HDC（HarmonyOS）统一；iOS 独立走 `IOSPhoneAgent` + `IOSActionHandler`（XCTest/WebDriverAgent HTTP，不经 DeviceFactory，无图谱栈与遥测） | `DeviceFactory` / `phone_agent/device_factory.py`、`phone_agent/agent_ios.py` | 截图损坏返回 `is_sensitive=True` 兜底图 |

### 支撑子系统

| 模块 | 职责 | 关键类/文件 | 接口 |
|---|---|---|---|
| 页面分类器 | 截图 → page_type；后条件验证的 ground truth；浮层优先 + 自洽性 | `PageClassifier` / `phone_agent/memory/exploration/classifier.py` + `classifier_prompts.py`；域模式 `phone_agent/spatial/schemas/shopping.yaml` | 三级 provider 链与监督者/规划器同源；开放词表（`new:<type>` 提案供离线探索）；`vlm_verify_transitions` 按 schema 配置 |
| 记忆管理 | 五数据面统一管理（见 ① 末表）；`start_task`/`end_task` 生命周期 | `MemoryManager` / `phone_agent/memory/memory_manager.py` | `end_task` 四步：轨迹保存（无论成败）→ 学习偏好（仅成功）→ 图谱刷新（仅成功，规范化→质量过滤→Neo4j）→ VLM 轨迹审核（仅成功，新转移以 hypothesis 导入） |
| 会话状态 | 进度追踪、商品提取、停滞检测（`is_stagnating`） | `UnifiedSessionState` / `phone_agent/memory/core/unified_state.py` | Phase 9 `add_step`；SpecGuard 读会话态商品价 |
| 按需检索 | 监听模型思考文本，回忆/比较/计算/停滞信号触发检索，冷却 3 步；典型 30 步任务仅 3-5 步触发 | `RetrievalGateway` / `phone_agent/memory/retrieval_gateway.py` | `get_injection_context` 分层注入；`compress_session_history` 现为 no-op（泄漏 bug 由会话记忆文件取代） |
| WebUI | `StreamingAgent` 包装真实 `PhoneAgent.run()`（worker 线程+事件队列）；图谱协同面板（`format_graph_panel` 纯函数） | `webui.py` | 三流式钩子：`stream_callback` / `step_observer` / `takeover_callback`；与 CLI 共享同一编排入口 |

### 记忆五数据面（第 9 节原表）

| 记忆面 | 存储 | 服务对象 | 解决的问题 |
|---|---|---|---|
| 用户记忆 | FAISS（跨会话） | Phase 4 澄清/槽位填充 | "买个手机"缺规格——从历史偏好填充，不追问 |
| 会话状态 | UnifiedSessionState（单任务） | Phase 6 进度注入/Phase 9 记步 | 进度追踪、商品提取、停滞检测 |
| 会话记忆文件 | JSON（单任务，原子落盘） | 抗遗忘锚 + 监督接口 | 长任务焦点丢失 |
| 图谱记忆 | Neo4j（跨会话） | Phase 4 定位/Phase 6 Fast Path | 机械导航重复劳动 |
| 轨迹文件 | JSON（单任务产物） | 任务后图谱演进 | 发现新转移供 VLM 审核 |

---

## ② 设计哲学与原则（逐条展开）

### 2.1 两类不确定性（问题刻画，论文动机节素材）

**环境不确定性**：移动 GUI 无稳定 DOM。同一页面随应用版本/设备/广告/弹窗/滚动位置外观不同；同一按钮可能打开 SKU 选择、登录、促销弹窗、结算或无反应；过长操作历史混淆推理并遮蔽当前任务。

**模型不确定性**（小模型特有，**真机实测**的失败模式，可作实证证据引用）：
- 长任务焦点丢失：反复重述全部计划、原地打转；
- 数字判断幻觉：连续三次把 ¥1424/¥172/¥201 称为"在 500-1000 元内"；
- 伪完成：把"压缩 5 步历史"当成任务宣布完成；未真正加购却输出"已成功加入购物车"；
- 空响应：关键步输出无内容回答。

**不确定性 → 失败模式 → 设计应对映射表**（文档 1.2 原表）：

| 不确定性 | 失败模式 | 设计应对 |
|---|---|---|
| 感知 | 页面外观随版本/广告/弹窗变化 | `PageState` 将截图抽象为基于 app、页面类型、地标、可操作项、槽位、风险的语义状态；分类器"浮层优先 + 自洽性"区分叠加态页面 |
| 转移 | 同一动作结果不确定 | `EdgeLifecycleManager` 追踪每条转移的经验结果分布、优势比与熵；只有统计可靠的转移进入可执行动作库 |
| 上下文 | 长历史混淆推理 | 会话记忆文件作为抗遗忘锚（原始任务永不改写）；按需检索；历史压缩 |
| 焦点丢失 | 小模型偏离任务 | 强 VLM 里程碑监督每隔数步修订计划，单一【当前目标】喂给小模型 |
| 数字幻觉 | 价格判断错误 | 机械价格裁决——代码计算预算对比注入 ⛔/✅；SpecGuard 购买提交点硬拦截 |
| 伪完成 | 虚报任务成功 | 机械完成门（到访证据）+ 强 VLM final_confirm（截图核实），构成单一完成权威 |
| 空响应 | 无有效输出 | 空/不可解析输出不再转 finish；触发上下文重置与回退重新分析 |

### 2.2 非对称权威（asymmetric authority）

原则：**每一类决策交给最适合它、且失败代价最小的主体**。

- **图谱**：加速与约束机械导航（首页→搜索、搜索→结果、筛选切换）。两条禁令：绝不在内容敏感页（商品选择、SKU、结算）重放过期坐标；绝不自动重放撤销类动作（Back/Home）——图谱无法区分"偏离路线"与"模型有意绕路"。
- **小模型**：保留对当前屏幕内容的 grounding 权威（元素在哪、点哪里），每步执行一条动作。
- **机械护栏（纯代码）**：接管确定性判断——数字比较、约束核实、完成证据。
- **强 VLM**：仅在任务开始（拆解）与里程碑（修订/核实）低频介入；是任务结束权威与计划修订权威。

关键性质：**所有护栏都是规则代码，不依赖云**——小模型量化部署到手机本地后整套约束结构原样成立。

决策路由四象限（文档建议绘图：纵轴"确定性 vs 语义性"，横轴"本地 vs 云端"）：
- 确定性 × 本地：图谱（机械导航复用）+ 机械护栏（数字比较/约束核实/完成证据）；
- 确定性 × 云端：空——确定性判断绝不上云；
- 语义性 × 本地：小模型（grounding、每步一条动作，高频）；
- 语义性 × 云端：强 VLM（initialize / checkpoint / final_confirm，低频 ~1+步数/5 次/任务）。

### 2.3 单一完成权威（single completion authority）

完成判定两条路径**互斥而非并行**：监督者在场（`supervisor_can_confirm` = 在场 ∧ 未因连续失败禁用 ∧ `call_count < max_calls`）时由 final_confirm 截图核实**独裁**，机械到访门让位；监督者缺位/预算耗尽时机械门兜底。

为什么不能双权威并行：机械到访门依赖页面分类，分类有噪声（规格弹窗常被判成 product_detail，导致 spec_selection"未到访"）；监督者看截图直接核实"加购成功"，严格强于到访证据。两者同时运行会让先跑的机械门**误拦监督者本可放行的真完成**。

两条路径都有确定性失败逃逸（各自连续 3 次），杜绝"既不结束也不推进"的死循环：
- 监督者否决用连续计数 `_finish_rejected_count`（距上次否决 >5 步则重置为 1，避免相隔几十步的三次否决被误判为"连续"）；连续 3 次否决 → 确定性失败终止。
- 机械门逃逸计数 `_finish_gate_blocked_count`：连续 3 次拦截 → 失败终止（target_page 可能因分类噪声永远判不到访，否则陷入 finish→拦截→finish 慢速死循环直到 max_steps）。
- 细节：`final_confirm` **不消耗里程碑调用预算**——否则反复 finish 尝试会耗尽 `max_calls`，使判定权悄悄从监督者切到更弱的机械门。

### 2.4 确定性恢复（deterministic recovery）

对每一类真机实测失败模式都有**确定性**（非概率、非 prompt 依赖）的检测与恢复路径，且每条路径都有**有界终止保证**（见 ⑤ 故障映射表）。论文级论点：**可靠性来自系统设计的确定性兜底，而非寄望小模型自我修正**——这是与"靠模型自我修正"的端侧智能体的关键区别。

恢复可行性的架构前提：**持久状态全在会话记忆文件里，对话上下文是可丢弃的**——所以"否决→上下文重置→回退重新分析"的恢复闭环才成立。

另一条有意识的架构红线（第 8 节）：**绝不把运行时预期后条件喂给分类器做偏置**。分类器输出是后条件验证的 ground truth，预期偏置会让验证退化为自我证实，摧毁边生命周期统计真实性。这是"有意识不做的改进"，论文方法节值得说明。

---

## ③ 九阶段执行循环（`_execute_step_impl`，按实际代码顺序）

入口 `PhoneAgent.run(task)`，主循环 `_execute_step`（遥测包装）→ `_execute_step_impl`，出口 `MemoryManager.end_task()` + `SessionMemoryFile.finalize()`。

任务接收四步（循环前）：① 状态全量重置（对话上下文/步数/各计数器/`_visited_pages`/TaskPlan/适配器历史——任务间完全隔离）；② 强 VLM 任务拆解（`MilestoneSupervisor.initialize`，输出经 `_sanitize_search_query` 净化——确定性剥离价格区间与特征词，搜索框只输入裸商品名；监督者不可用回退 `_vlm_pre_plan`）；③ 构建会话记忆文件 + TaskPlan + MilestoneTrigger；④ `MemoryManager.start_task()`（重置 UnifiedSessionState/RetrievalGateway、清 RuntimeDAG、从任务文本提取偏好）。

- **Phase 1 感知**：`DeviceFactory.get_screenshot()`（损坏 → `is_sensitive=True` 兜底图）、当前 app、`ui_hash`；`PageClassifier.classify()` 输出 page_type（浮层优先+自洽性）；RuntimeDAG hint 可用时跳过分类器省一次调用；到访页面记入 `_visited_pages`。
- **Phase 2 异常看门狗**：`is_sensitive` 截图或连续 unknown/None 页面累计，连续 2 次 → `Take_over` 人工接管。机械信号兜底——不依赖模型认出验证码，黑屏本身就是证据。
- **Phase 3 验证检测 L1**：`detect_verification` 检测登录/验证码/短信页。阶梯接管：连续命中 1-3 次自动接管、4-5 次让模型试一次、>5 次任务失败。
- **Phase 4 图谱协同 + 澄清**：`locate_and_get_context()` 委托 GraphRuntimeController——RuntimeDAG 可用直接推进游标返回下一动作（不查 Neo4j）；否则 `(app,page_type)` 匹配 Neo4j → 验证上步 pending transition → Dijkstra → 缓存 RuntimeDAG。返回 mode + next_actions + graph_hint。仅第 1 步运行 ClarificationAgent 三层短路澄清。
- **Phase 5 计划同步 + 里程碑触发**：当前页面匹配 TaskPlan 未来步（Fast Path 超前）则批量推进中间步并标记 `milestone_subtask_done`；随后触发器评估，命中则 `_run_milestone_checkpoint`（强 VLM 看截图修订记忆与计划），checkpoint 调用防御包装，崩溃不杀任务。
- **Phase 6 三档调度与上下文组装**：
  - Fast Path：`_needs_vlm` 检查 ActionAdvisor 提示是否含匹配 effective plan target 的可快执行动作（grounded、conf≥0.9、有坐标或 Compound）→ `_execute_fast_path`：执行后立即截新图分类做**同步后条件检查**，匹配成功、失配回退模型路径并记失败观测。搜索场景有合成 `Type <query> + Tap` Compound 边。
  - 图谱捷径（兜底）：ActionAdvisor 无提示时 legacy `_try_graph_shortcut`；`verify_with_vlm` 转移自动转交模型。
  - 模型路径：组装消息（双模型路径同构）——`【会话记忆】`摘要 → `【当前目标】`（单焦点）→ 关键约束 → 执行历史 → `【图谱导航】`（graph_hint）→ 可用操作 → SpecGuard 提示 → **机械价格裁决**（⛔/✅ 注入）→ 价格红线 → 按需记忆检索。
- **Phase 7 执行与安全拦截**：专用 handler 路径（UI-TARS/Qwen-VL/MAI-UI/GUI-Owl）先算 canonical action **再过 SpecGuard**；AutoGLM 路径 parse → `try_ground` 图谱坐标增强 → SpecGuard → 执行。SpecGuard 守卫四类页（含 product_detail，因规格弹窗常被分类为它），价格越界 → Interact。执行异常构造失败 StepResult，**绝不借道 finish 洗白**。
- **Phase 8 完成判定与恢复**（仅 finish）：见 ② 2.3 单一完成权威与 ④。
- **Phase 9 状态更新**：Interact 回答回填任务文本（SpecGuard 下步可见）；缓存 pending transition 供下步验证；`add_step`（会话态、商品提取、停滞检测）；历史压缩保留最近 2 条完整对话。

空输出恢复（3.3）：解析失败**不再转 finish**（曾是最后一条 finish 洗白通道）；`_unparseable_count` 连续 2 次 → `_reset_dialogue_context`（下步由 system prompt + 会话记忆摘要 + 修订目标 + 新截图重建）；连续 4 次 → 显式失败终止。

任务结束 `end_task`：① 轨迹保存（无论成败，`trajectories/`）→ ② 学习偏好（仅成功，FAISS）→ ③ 图谱刷新（仅成功：规范化→质量过滤→Neo4j）→ ④ VLM 轨迹审核（仅成功，新转移以 hypothesis 导入）。

闭环数据流五个关键特性（2.3 节）：每步都有反馈（后条件实时更新边成功率，非任务末才更新）；持久化延迟（实时更新内存计数器，Neo4j 仅成功结束时写）；快速路径有保护（失配回退模型，最坏多一次模型调用）;完成有双权威（监督者独裁/机械门兜底）；失败可恢复（丢弃污染上下文、基于会话记忆回退）。

---

## ④ 里程碑监督机制（全细节）

设计动机：纯小模型长任务能力不足（遗忘/幻觉/伪完成）；每步强 VLM 已验证太贵（延迟翻三倍）且违背端侧理念。中间路径：**强 VLM 任务开始拆解一次 + 里程碑低频介入**，预算约 `1 + 步数/5` 次调用/任务。

### 监督者三职责（`milestone_supervisor.py`）

- **initialize(task)**：一次性拆解，输出与 `_vlm_pre_plan` 同 schema（兼容 `set_vlm_plan`/`TaskPlan.from_vlm_output`）；拆解子任务 + 提取 `search_query/product/specs/target_action/target_page`。
- **checkpoint(...)**：输入 = 截图（裁剪压缩）+ 会话记忆渲染 + 近 8 步摘要 + 当前页面类型 + 触发原因 + 机械事实快照（会话追踪的商品数与价格）。系统提示强调"**执行模型自述不可信，以截图与已验证事实为准；原始任务永不改变**"。输出 `CheckpointResult`：已完成子任务 id、新验证事实、重新规划的剩余子任务（全量替换未完成部分）、完成/阻塞判定。
- **final_confirm**：finish 时截图核实，三态 `task_complete`（放行）/`task_blocked`（显式失败）/否决（上下文重置回退重析）。

### 触发器表（`MilestoneTrigger`，纯逻辑去抖；判定点在计划同步块后，每步至多触发一个）

| 触发器 | 条件 | 去抖 |
|---|---|---|
| T1 步数间隔 | `step - last ≥ INTERVAL`（默认 5，最小 2） | 天然去抖 |
| T2 子任务达成 | 计划同步标记 done 且 `step - last ≥ MIN_GAP`（默认 2） | 不满足则推迟到 T1 |
| T3 停滞 | `state.is_stagnating()`（连续 2 次同动作同页）且 ≥ MIN_GAP | 同一停滞段不重复触发 |
| T4 完成门拦截 | 机械完成门拦截 finish 后下一判定点立即触发 | 每任务 ≤ 2 次 |

优先级 T4 > T3 > T2 > T1。checkpoint 后 `last_checkpoint_step` 前移，所有触发器共享该基准。

### 预算

总预算 `MAX_CALLS`（默认 `2 + max_steps/INTERVAL`）耗尽后全部静默；**final_confirm 不计入此预算**（属完成判定，有自己的连续 3 次否决上限）——否则反复 finish 尝试会与常规里程碑共享预算并快速耗尽。

### 原子修订（`TaskPlan.apply_revision`）

`apply_revision(completed_step_ids, revised_subtasks)`：done/skipped 步保留原对象不可改写；pending 尾部用修订全量重建（空修订=保留原尾部）；current_index 重定位到首个未完成步；校验（空描述丢弃、≤12 步上限、非法修订整体拒绝）；原子赋值后返回变更摘要；同步镜像回会话记忆文件（`sync_subtasks_from_plan`）。

### 降级语义（失败 = 维持现状）

- checkpoint 失败（网络/解析/超时）→ 保留旧计划、记 `RevisionEntry("checkpoint_failed")`、基准前移（防重试风暴）；
- 连续 2 次失败 → 本任务内禁用监督者；
- 强 VLM 完全不可用 → 不挂载监督者，整体退化为"机械护栏 + 纯小模型"；
- 所有 checkpoint 调用点 try/except 防御包装，崩溃既不杀任务也不堵 finish。

### Provider 链鉴别（关键正确性保障，论文实验有效性前提）

监督者、分类器、规划器共用同一条 `PageClassifier` 三级 provider 链：`AMSG_STRONG_VLM_*` → `OFFLINE_VLM_*` → `PHONE_AGENT_*`（autoglm 小模型）。链尾默认值齐全（永远存在），故**未配置强 VLM 时链会静默退化到小模型**——"监督者"变成执行器自己的模型，无法纠正同源幻觉，里程碑机制名存实亡。系统通过 `MilestoneSupervisor.is_strong_vlm()` / `provider_label` 鉴别实际 provider：挂载时打印真实监督模型（如 `监督模型: amsg_strong_vlm:qwen3-vl-plus`），退化到小模型时大声告警。`_vlm_pre_plan` 降级路径对齐同一三级链。**这道鉴别确保论文实验中"强 VLM 监督"配置不会被静默掏空**。

### 与每步规划器互斥（三档消融）

`step_planner.py`（`PHONE_AGENT_STRONG_PLANNER=1`，默认关）为每步强 VLM 规划，与里程碑互斥（启用时里程碑自动停用）。三档消融矩阵：纯小模型（`MILESTONE=0`）/ 里程碑（默认）/ 每步规划（上界）。

---

## ⑤ 机械护栏表与故障模式映射表（原样保留，可直接转写）

设计原则：**能用代码确定性判断的，绝不交给小模型推理**。护栏每步零额外云调用，量化部署后原样成立；与强 VLM 监督互补——监督是语义层（理解进度、修订计划），护栏是符号层（数字、证据、状态）。

### 机械护栏表（文档第 6 节原表）

| 护栏 | 接管的判断 | 实现 |
|---|---|---|
| **机械价格裁决** | "这个价格符合预算吗" | `SpecGuard._extract_price_bounds` 解析预算区间，对照会话态商品价，决策页注入三态结论：⛔ 越界禁购 / ✅ 在区间 / ⚠️ **价格未读取到时强制模型先从截图核实价格再加购**（防越界商品在价格未提取时滑过）。绝不让 9B 模型比数字 |
| **SpecGuard 价格拦截** | 越界商品加购 | commit 动作时硬拦截：越界 → 替换为 Interact 询问用户 |
| **机械完成门** | "任务真完成了吗"（兜底） | success finish 须到访过预规划 target_page；否则拦截+写历史引导；**连续 3 次拦截 → 确定性失败终止**（逃逸，防分类噪声导致死循环） |
| **异常看门狗** | "屏幕是否失效" | 连续 2 步敏感/unknown 截图 → 人工接管。机械信号不依赖模型识别；**接管后复位异常/验证两计数器**（人工已处理，避免下帧重复抢先接管） |
| **空输出恢复** | "模型卡住了吗" | 不可解析输出不转 finish；2 次重置上下文、4 次失败终止 |
| **搜索词净化** | "查询词是否被污染" | `_sanitize_search_query` 确定性剥离价格/特征词 |
| **finish 防洗白** | "异常是否伪装成功" | 执行异常一律构造失败 StepResult，绝不借道 finish |

### 故障模式与确定性恢复映射表（文档 6.1 原表）

| 失败模式（真机实测） | 检测信号 | 确定性响应 | 终止保证 |
|---|---|---|---|
| 长任务焦点丢失 | （持续）会话记忆文件每步注入 + 里程碑修订 | 单一【当前目标】替代自由规划 | — |
| 数字判断幻觉（价格越界判合规） | 代码解析预算 vs 会话态价格 | ⛔/⚠️ 注入 + SpecGuard 硬拦截 | — |
| 伪完成（虚报成功） | 监督者截图核实 or 机械到访门 | 否决 → 回退重析 | 连续 3 次否决/拦截 → 失败终止 |
| 空响应（无有效输出） | 解析失败 | 不转 finish + 上下文重置 | 连续 4 次 → 失败终止 |
| 验证码/安全弹窗 | 截图损坏(is_sensitive) + 连续 unknown | 异常看门狗 → 人工接管 | 验证检测 >5 次 → 失败终止 |
| 人工接管后计数残留 | — | 接管后复位异常/验证计数器 | — |
| 图谱对抗模型绕路 | Back/Home 划为非锚定 | 撤销类动作永不自动重放 | — |
| 强 VLM 配置缺失 | provider 链鉴别(is_strong_vlm) | 退化告警 + 鉴别日志 | — |
| 监督者 checkpoint 崩溃 | try/except 包装 | 保留旧计划，既不杀任务也不堵 finish | 连续 2 次失败 → 禁用监督者 |
| Fast Path 后条件失配 | 同步截图分类对比 | 回退模型路径 + 记失败观测 | — |

附：非锚定转移表（`_UNGROUNDED_TRANSITIONS`，9 对，必须模型决策具体元素；7.2 节）：

```
search_result→product_detail   选哪个商品
search_result→store            选哪家店
product_detail→spec_selection  选什么规格
spec_selection→cart/checkout   购买提交点
cart→checkout                  购买提交点
search_input→search_result     裸 Tap 会提交残留文本（仅合成 Compound 锚定）
filter_panel→search_result     离开筛选需内容决策
spec_selection→product_detail  弹窗关闭/取消决策
```

快执行条件 `ActionHint.is_fast_executable()`：grounded ∧ confidence ≥ 0.9 ∧（有坐标 ∨ Compound ∨ action_type ∈ {Launch, Wait}）；Back/Home 永不快执行。

---

## ⑥ 环境变量与消融配置（文档第 12 节）

| 环境变量 | 默认 | 作用 |
|---|---|---|
| `PHONE_AGENT_MILESTONE` | `1`（开） | 里程碑监督开关 |
| `PHONE_AGENT_MILESTONE_INTERVAL` | `5` | 步数间隔触发 N |
| `PHONE_AGENT_MILESTONE_MIN_GAP` | `2` | 子任务/停滞触发最小间隔 |
| `PHONE_AGENT_MILESTONE_MAX_CALLS` | `0`（auto = 2+步数/N） | 每任务 checkpoint 上限 |
| `PHONE_AGENT_STRONG_PLANNER` | `0`（关） | 每步强 VLM 规划（消融上界，与里程碑互斥） |
| `AMSG_STRONG_VLM_*`（`_API_KEY`/`_BASE_URL`/`_MODEL`） | — | 强 VLM（监督/分类/规划共用，三级链首选）。未配置则静默退化到 `PHONE_AGENT_*` 小模型，挂载时告警 |
| `OFFLINE_VLM_*` | — | 三级链中间级（强 VLM 未配时次选） |
| `PHONE_AGENT_*`（`_MODEL` 等） | autoglm-phone-9b | 小模型主执行器；也是三级链兜底末级 |
| `AMSG_CONFIG` | `sava`（推荐） | 图谱生命周期策略预设（详见 AMSG_DESIGN.md） |
| `AMSG_DOMAIN_PRIORS` | `1`（开） | 同域结构先验注入（冷启动借同域方向感，仅文本不含坐标） |

其他运行时环境变量（项目 CLAUDE.md）：`PHONE_AGENT_BASE_URL`（默认 `http://localhost:8000/v1`）、`PHONE_AGENT_API_KEY`、`PHONE_AGENT_MAX_STEPS`（100）、`PHONE_AGENT_DEVICE_ID`、`PHONE_AGENT_DEVICE_TYPE`（adb/hdc/ios）、`PHONE_AGENT_WDA_URL`、`PHONE_AGENT_LANG`（cn/en）。

**三档消融矩阵（论文实验核心）**：
1. 纯小模型基线（`MILESTONE=0`）：仅机械护栏 + 图谱，无强 VLM 运行期介入；
2. 里程碑监督（默认）：~1+步数/5 次强 VLM 调用；
3. 每步规划上界（`STRONG_PLANNER=1`）：每步强 VLM，延迟翻三倍。

三档构成"准确率—延迟—云调用次数"曲线，支撑核心论点：**低频监督达到接近每步监督的可靠性，且端侧部署友好**（"系统设计而非模型规模提升端侧可靠性"）。

附：五模型家族适配表（10.1 节，论文实现节可用）：

| 模型家族 | 坐标空间 | 上下文策略 | 响应格式 |
|---|---|---|---|
| AutoGLM | `[0,1000]` 归一化 | 累积（文本累加，图片仅当前） | `<answer>` / `do()`+`finish()` |
| UI-TARS | 绝对像素（smart_resize 空间） | 最多 5 张图，裁剪最旧 | `Thought: … Action: …` |
| Qwen-VL | `[0,999]` 归一化 | 每轮重建 | `<tool_call>` JSON |
| MAI-UI | `[0,999]` 归一化 | 最多 3 张图 | `<thinking>` + `<tool_call>` |
| GUI-Owl | `[0,1.0]` 小数（原生 0-999，解析后转 0-1） | 最多 1 张图 | Action + `<tool_call>` |

---

## ⑦ 图表建议清单（供论文"图X"占位引用）

文档自带 mermaid 图（可直接转绘）与显式"绘图建议"标注，共 8 项：

| 占位 | 类型 | 来源（CN 文档位置） | 内容与绘图建议 |
|---|---|---|---|
| 图 A：决策路由四象限 | ASCII 框图 + 绘图建议 | §1.3 | 四象限矩阵：纵轴"确定性 vs 语义性"、横轴"本地 vs 云端"；底部标注频率（高频每步 vs 低频 ~1+步数/5 次/任务）。适合方法概览/动机图 |
| 图 B：三区域 + 监督层总体架构 | ASCII 框图 | §2.1 | 监督层（MilestoneSupervisor 三职责 + SessionMemoryFile）→ 决策区域（主控/TaskPlan/护栏/SpecGuard/三档调度）→ 图谱区域（定位/Dijkstra/RuntimeDAG/ActionAdvisor/EdgeLifecycle，存储 Neo4j/FAISS/JSON）→ 执行区域（ModelClient/Bridge/ActionHandler/DeviceFactory），含反馈回路。适合系统架构图（论文图 1 候选） |
| 图 C：闭环数据流 | **mermaid flowchart**（已成图） | §2.3 | 截图分类 → 看门狗 → 图谱定位 → 里程碑 checkpoint → 三档调度门控 → 执行 → 后条件验证 → 反馈/完成判定/上下文重置。配色已标：Fast Path 绿 #2E9E44、模型路径蓝 #0F4D92、里程碑橙 #C77D00、完成判定紫 #7B61A0 |
| 图 D：九阶段执行流 | **mermaid flowchart** + 绘图建议 | §3.2 | 建议改纵向泳道流程图：左侧标 Phase 序号，菱形分支，红色块为提前返回（Take_over 红 #B23B3B） |
| 图 E：完成判定树 | **mermaid flowchart** + 绘图建议 | §3.4 | 判定树，强调"机械门让位"的互斥关系；三色：放行绿/失败红/重置橙。单一完成权威贡献点的核心配图 |
| 图 F：强 VLM 与小模型交替时序 | **mermaid sequenceDiagram** + 绘图建议 | §4 开头 | 泳道时序图（用户/强 VLM/会话记忆文件/小模型执行器），突出强 VLM 介入点稀疏、小模型高频执行。里程碑监督贡献点的核心配图 |
| 图 G：会话记忆注入样例 | 代码框 + 绘图建议 | §5.2 | 直接作为代码框/标注框呈现 `render_injection()` 实际注入文本（原始任务/目标商品+约束/已验证事实≤5条/子任务进度清单），体现"每步喂给小模型的抗遗忘上下文" |
| 图 H：边生命周期状态机 | 文字描述（需自绘） | §7.4 | `hypothesis → candidate → promoted → demoted` 状态机 + 后条件验证喂计数 + 仅 promoted 可见；可与三档调度（Fast Path/Co-pilot/模型）合并为一图 |

另可从表格转图的候选：⑤ 故障模式映射表（可转"失败模式→检测→响应→终止保证"四列图式表）；⑥ 三档消融（可绘"准确率—延迟—云调用"三轴权衡示意，论文实验节）。

---

## 附：论文方法摘要（文档 §15 原文，可直接改写为 abstract/方法导言）

> Shopping-Agent 是一个以小型 GUI 模型为主执行器、强 VLM 低频监督、空间图谱引导的移动 GUI 智能体，旨在让可量化端侧部署的小模型在长程任务上达到接近大模型的可靠性。任务开始时，强 VLM 将任务拆解为有序子任务并提取商品信息，生成原始任务永不改写的会话记忆文件交给小模型执行；此后强 VLM 仅在里程碑处（每数步、子任务达成、停滞或完成声明被拦）低频介入，看截图修订记忆文件、重新规划剩余子任务、核实任务是否真正完成，预算约 1+步数/5 次调用。运行期，纯代码的机械护栏接管小模型不擅长的确定性判断——价格预算对比、约束核实、完成证据——零额外云调用；空间图谱缓存经验证的机械导航转移，按边的锚定性进行三档调度（Fast Path 跳过模型 / Co-pilot 图谱给方向模型 grounding / 完整模型推理），延迟后条件验证驱动边生命周期。完成判定采用单一权威：监督者在场时以截图核实独裁，缺位时由机械到访门兜底，杜绝伪完成污染图谱质量门。任务无法推进或模型空响应时，丢弃被污染的对话上下文，基于会话记忆回退重新分析。系统支持五个 GUI 模型家族与三个设备平台，并通过纯小模型/里程碑/每步规划三档消融提供端侧—云协同的准确率—延迟—成本权衡分析。

## 附：核心贡献清单（§13.2，论文 contributions 节直接素材）

1. 强 VLM 里程碑监督机制（~1+步数/5 次调用接近每步监督可靠性；拆解/修订/核实归强 VLM，grounding 归小模型）
2. 会话记忆文件作为抗遗忘锚（原始任务永不改写；可丢弃对话上下文使恢复闭环成立）
3. 机械反幻觉护栏体系（符号层，零额外云调用，量化部署原样成立）
4. 单一完成权威（互斥判定避免双权威误拦；系统性堵死伪 finish）
5. 自更新页面状态图谱与三档调度（跨会话导航知识、成功率驱动生命周期、Compound 边合成）
6. 结构化任务约束作为一等状态（SpecGuard 机械强制，购物安全是系统属性而非提示工程，对五模型家族成立）
7. 浮层优先页面分类（叠加态页面按最上层分类 + 自洽性；明确拒绝预期偏置以保护统计真实性）
8. 可复现的端侧—云协同消融（三档配置的准确率—延迟—成本曲线）

文献定位四缺口（§13.1）：纯模型方案能力—成本两难 / 无状态重复探索 / 无生命周期的图谱 / 二元的图谱-模型控制。

实验性扩展（未默认启用，§13.3）：每步强 VLM 规划（消融上界）；多通道贝叶斯信念定位、Belief-A* 增强规划器（见 AMSG_DESIGN.md）。
