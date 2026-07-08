# 摘要

随着移动互联网购物成为大众日常消费的主要形态，利用视觉语言模型（Vision-Language Model, VLM）驱动图形用户界面（Graphical User Interface, GUI）智能体自动完成手机购物任务，成为人机交互与智能系统领域的研究热点。现有方案面临端云两难：云端大模型每步规划虽然准确，但实测估算会使单步延迟翻三倍，且成本高、隐私差、无法端侧部署；可量化端侧部署的小型 GUI 模型虽然延迟与隐私友好，但在长程购物任务上存在真机实测的系统性失败模式——长任务焦点丢失、数字判断幻觉（如连续多次将明显越界的价格判定为符合预算区间）、伪完成与空响应，导致任务可靠性远低于实用要求。针对上述问题，本文设计并实现了面向移动端购物场景的小模型驱动 GUI 智能体系统 Shopping-Agent，其核心思路是以三层确定性结构约束小模型，而非把决策权交给云端大模型。本文的主要工作包括：(1) 针对纯小模型在长程任务上的焦点丢失、幻觉与伪完成问题，提出强 VLM 里程碑监督机制与会话记忆文件设计，强 VLM 仅在任务开始与里程碑处低频介入（约 1+步数/5 次调用，实测估算），通过四触发器去抖、原子计划修订与降级语义实现接近每步监督的可靠性；(2) 针对小模型的数字判断幻觉与伪完成，设计机械反幻觉护栏体系与确定性故障恢复机制，价格裁决、SpecGuard、单一完成权威、异常看门狗与空输出恢复均为纯代码确定性判断，零额外云调用，每类故障模式均有有界终止保证；(3) 针对无状态重复探索与静态图谱失效问题，设计 AMSG 自更新空间图谱与锚定性三档调度，以语义页面状态抽象、四阶段边生命周期、延迟后条件验证与 Fast Path/Co-pilot/模型路径三档调度使机械导航跳过模型推理。系统支持五个 GUI 模型家族与 Android、HarmonyOS、iOS 三类设备平台，并已在淘宝、京东两个真实电商应用上完成端到端验证；纯小模型、里程碑监督、每步规划三档消融实验给出端云协同的准确率—延迟—成本权衡分析，量化结果【实验数据待补充】。本文工作表明，端侧小模型的长程任务可靠性可以来自系统级的确定性结构设计而非模型规模扩张，为低成本、隐私友好的移动 GUI 自动化提供了可行的工程路径。

**关键词：** GUI 智能体；端侧小模型；里程碑监督；空间知识图谱

# Abstract

As mobile shopping becomes a primary form of everyday consumption, automating smartphone shopping tasks with GUI agents driven by Vision-Language Models (VLMs) has emerged as an active research topic in human-computer interaction and intelligent systems. Existing solutions face an on-device/cloud dilemma: cloud-based large models plan accurately at every step, but empirical estimates show that per-step strong-VLM planning triples the step latency, and such models are costly, privacy-unfriendly, and cannot be deployed on device; quantizable on-device small GUI models, in contrast, suffer from systematic failure modes observed on real devices in long-horizon shopping tasks, including task-focus loss, numerical-judgment hallucination (e.g., repeatedly declaring clearly out-of-range prices to be within budget), false completion, and empty responses. To address these problems, this thesis designs and implements Shopping-Agent, a small-model-driven GUI agent system for mobile shopping scenarios, whose core idea is to constrain the small model with a three-layer deterministic structure rather than delegating decisions to a cloud-based large model. The main contributions of this thesis are threefold. First, to mitigate focus loss, hallucination, and false completion of pure small models in long-horizon tasks, this thesis proposes a strong-VLM milestone supervision mechanism together with a session memory file design, in which the strong VLM intervenes only at task initialization and at milestones (approximately 1 + steps/5 calls per task, an empirical estimate), achieving reliability close to per-step supervision through four debounced triggers, atomic plan revision, and graceful degradation semantics. Second, to counter numerical hallucination and false completion, this thesis designs a mechanical anti-hallucination guardrail system with deterministic failure recovery: price adjudication, SpecGuard, a single completion authority, an anomaly watchdog, and empty-output recovery are all implemented as pure-code deterministic checks with zero additional cloud calls, and every failure mode is covered by a bounded-termination guarantee. Third, to overcome stateless repeated exploration and the decay of static graphs, this thesis designs AMSG, a self-updating spatial graph with groundedness-based three-tier scheduling, which combines semantic page-state abstraction, a four-stage edge lifecycle, delayed postcondition verification, and Fast Path/Co-pilot/model-path scheduling so that mechanical navigation can bypass model inference. The system supports five GUI model families and three device platforms (Android, HarmonyOS, and iOS), and has been verified end-to-end on two real e-commerce applications, Taobao and JD; a three-tier ablation study—pure small model, milestone supervision, and per-step planning—provides an accuracy-latency-cost trade-off analysis for on-device/cloud collaboration, with quantitative results to be supplemented. This work demonstrates that the long-horizon reliability of on-device small models can be obtained from system-level deterministic structural design rather than model scaling, offering a practical engineering path toward low-cost, privacy-friendly mobile GUI automation.

**Key words:** GUI agent; on-device small model; milestone supervision; spatial knowledge graph

---

# 第1章 绪论

## 1.1 研究背景与意义

### 1.1.1 研究背景

移动购物已成为大众日常消费的主要渠道，但在手机上完成一次有约束的购物任务——例如"在预算 500 至 1000 元内购买一副具有降噪功能的蓝牙耳机"——仍然需要用户在搜索、筛选、比价、规格选择、加购、结算等多个页面间进行长达数十步的手工交互。将这一过程交给软件智能体自动完成，长期以来受制于移动 GUI 环境的特殊性：与 Web 页面不同，移动应用界面没有稳定可解析的 DOM 结构，同一页面在不同应用版本、不同设备、广告与弹窗干扰、不同滚动位置下外观各异；同一个按钮的点击结果可能是规格选择浮层、登录页、促销弹窗、结算页，甚至没有任何反应。传统的基于控件树或脚本录制的自动化方法在这种高度动态的视觉环境中难以维持。

近年来，视觉语言模型（VLM）的快速发展使"截图输入、动作输出"的纯视觉 GUI 智能体成为可能：智能体以屏幕截图为观测，由模型推理出下一步的点击、滑动或输入动作，形成"截图—推理—执行"的闭环循环。这一范式催生了大量移动 GUI 智能体研究 [AppAgent, CHI 2025][Mobile-Agent, arXiv 2024][CogAgent, CVPR 2024]，也推动了 AutoGLM [AutoGLM, arXiv 2024]、UI-TARS [UI-TARS, arXiv 2025] 等 GUI 专用基座模型的出现。然而，当这一范式落到真实的移动购物场景时，立即面临一个端云两难的部署困境。

一方面，以云端大模型（强 VLM）为每步决策者的方案虽然单步决策质量高，但代价沉重：每一步都需要将截图上传云端并等待大模型推理，本文在系统构建过程中实测估算，每步引入强 VLM 规划会使单步延迟翻三倍；按步计费的云端调用使长程任务成本随步数线性膨胀；用户的购物意图、浏览记录与支付页截图持续上传云端带来隐私暴露；而数百亿参数的大模型本身无法部署到手机端侧。另一方面，可量化部署到手机本地的小型 GUI 模型（如 AutoGLM-Phone-9B 这一量级的模型，本文称小模型）在延迟、成本与隐私上具有天然优势，其单步元素定位（grounding）能力经过专门训练也已可用，但其在长程任务上的能力短板在真机环境中暴露得非常彻底。本文在淘宝、京东真实应用上的实测观察到四类系统性失败模式：

第一，**长任务焦点丢失**。任务超过十余步后，小模型开始反复重述全部计划、在相邻页面间原地打转，遗忘当前应当推进的子目标。第二，**数字判断幻觉**。小模型不可靠地执行数值比较——真机实测中曾出现连续三次将 ¥1424、¥172、¥201 的商品价格判定为"在 500—1000 元区间内"的实例，若不加约束将直接导致越界购买。第三，**伪完成**。小模型会虚报任务成功：曾把"压缩了 5 步历史"这一内部操作当成任务完成宣布结束，也曾在未真正执行加购动作时输出"已成功加入购物车"。第四，**空响应**。在关键步骤输出无任何有效内容的回答，使闭环循环失去驱动。这些失败模式并非偶发噪声，而是小模型能力上限与长程任务需求之间的结构性矛盾，单靠提示工程无法根除。

由此，本文的核心研究命题浮现：**如何让一个能力有限、可量化部署到手机本地的小型 GUI 模型，在长程购物任务上达到接近大模型的可靠性？**本文给出的答案不是把决策权交还云端大模型（这会重新陷入延迟、成本与隐私的困境），而是用三层确定性结构约束小模型：其一，空间图谱加速机械导航——把"首页→搜索框→搜索结果"这类与屏幕内容语义无关的机械转移沉淀为跨会话的图谱知识，复用时直接跳过模型推理；其二，机械护栏接管确定性判断——数字比较、约束核实、完成证据这些可以用纯代码确定判定的事项，绝不交给小模型推理；其三，强 VLM 仅在里程碑处低频介入——任务开始时拆解一次计划，此后每隔数步或在子任务达成、停滞、完成声明被拦截时核实进度并修订计划，调用预算约为 1+步数/5 次每任务（实测估算）。三层结构各司其职，使高频执行留在端侧、确定性判断零云调用、语义监督低频上云。

### 1.1.2 研究意义

本文工作的意义可以从理论与应用两个层面阐述。

在理论层面，本文提出的"里程碑级监督"为大小模型协同提供了一个新的粒度层级。现有的大小模型协同研究主要发生在三个粒度：token 级（如投机解码中大模型逐 token 验证小模型草稿 [Speculative Decoding, ICML 2023]）、查询级（如按单次请求在大小模型间级联或路由 [FrugalGPT, arXiv 2023][RouteLLM, arXiv 2024]）与步级（如验证器对每步动作候选打分 [V-Droid, arXiv 2025]）。token 级与步级协同过于细粒度，无法摆脱对大模型的高频依赖；查询级路由又过于粗粒度，无法在一个长程任务内部持续纠偏。本文的里程碑级监督填补了这一空档：强 VLM 不参与逐步执行，而是以任务内的状态化检查点形式低频介入，依靠会话记忆文件这一持久锚维持跨检查点的监督状态，从而在低频调用下获得接近每步监督的纠偏能力。同时，本文系统性地论证了"可靠性来自系统设计的确定性兜底，而非寄望模型自我修正"这一工程命题——每一类真机实测失败模式都对应一条非概率的、不依赖提示词的确定性检测与恢复路径，且均有有界终止保证，这为端侧智能体的可靠性工程提供了可借鉴的设计范式。

在应用层面，本文系统直接面向真实电商应用的购物自动化。系统在淘宝、京东两个国内主流电商 App 上完成了端到端验证，覆盖搜索、筛选、规格选择、加购等核心购物链路；机械价格裁决与 SpecGuard 购买安全守卫把"不买越界商品、不在未经用户确认时提交支付"做成了系统属性而非提示约定，对购物这类涉及真实资金的敏感场景提供了确定性的安全保障。由于全部护栏均为规则代码、不依赖云端，小模型量化部署到手机本地后整套约束结构原样成立，这使本文方案对低成本、隐私敏感的端侧部署场景具有直接的工程价值。

## 1.2 国内外研究现状

本节按四个主题组织相关研究：移动 GUI 智能体、图谱与结构化记忆驱动的导航、小模型端侧部署与大小模型协同、GUI 智能体基准评测。每个主题以"现状—不足"收尾，末尾汇总现有工作留下的研究缺口。

### 1.2.1 移动 GUI 智能体

移动 GUI 智能体研究大体沿两条路线展开。一条是**框架路线**，即以通用大模型为推理核心、围绕其构造感知与执行框架。AppAgent [AppAgent, CHI 2025] 是最早的 VLM 驱动手机操作智能体之一，通过自主探索与人类演示构建应用知识文档供部署期查阅；Mobile-Agent [Mobile-Agent, arXiv 2024] 采用纯视觉感知（OCR 与检测定位）摆脱对系统 XML 元数据的依赖；Mobile-Agent-v2 [Mobile-Agent-v2, NeurIPS 2024] 引入规划、决策、反思三智能体协作与任务内记忆单元缓解长程导航中的历史漂移；AutoDroid [AutoDroid, MobiCom 2024] 通过随机探索构建 UI 转移图（UI Transition Graph, UTG），以探索记忆注入增强大模型的应用领域知识。另一条是**基座模型路线**，即训练 GUI 专用模型。CogAgent [CogAgent, CVPR 2024] 以双分辨率视觉编码器支持高分辨率截图中微小 UI 元素的识别；UI-TARS [UI-TARS, arXiv 2025] 训练端到端原生 GUI 智能体模型，将反思与里程碑识别等 System-2 推理能力内化于模型；AutoGLM [AutoGLM, arXiv 2024] 提出分离规划与定位的"中间接口"并以自进化在线课程强化学习训练 GUI 基座智能体；Mobile-Agent-v3 [Mobile-Agent-v3, arXiv 2025] 发布开源基座模型 GUI-Owl 并配套多智能体框架，在 AndroidWorld 上取得领先成绩。

上述工作的不足在于：基座模型路线依靠大规模训练与云端虚拟环境自进化提升能力，训练成本高昂，且能力提升与模型规模强绑定，难以惠及端侧可部署的小模型；框架路线中的反思、自我纠错等机制（以及 UI-TARS 把里程碑识别内化于模型的做法）均以模型内隐能力形式存在，外部不可审计、不可干预——当模型自身产生幻觉时，依赖同一模型的反思无法发现同源错误。

### 1.2.2 图谱与结构化记忆驱动的导航

为减少重复探索，一批工作将环境知识组织为结构化记忆。PG-Agent [PG-Agent, ACM MM 2025] 用自动管线将顺序轨迹转化为页面图，部署期以检索增强生成（RAG）方式检索图中的感知指南辅助多智能体执行；KG-RAG [KG-RAG, EMNLP 2025] 将不完整 UTG 经大模型离线图搜索转为结构化向量库，在线以嵌入检索导航路径；WebNavigator [WebNavigator, arXiv 2026] 离线构建网站交互图并提出"拓扑盲区"问题，在线直接以图驱动导航跳转；MobileGPT [MobileGPT, MobiCom 2024] 以"屏幕—子任务—任务"层级树组织应用记忆，显著降低重复任务成本；AgentRR [AgentRR, arXiv 2025] 与 MobiAgent [MobiAgent, arXiv 2025] 将经典的记录—重放机制引入智能体，把交互轨迹抽象为多级经验或 ActTree 结构以加速重复执行。更一般的经验复用机制还包括从轨迹归纳可复用工作流的 AWM [AWM, arXiv 2024]、以完整轨迹为少样本范例的 Synapse [Synapse, ICLR 2024]、提炼自然语言经验规则的 ExpeL [ExpeL, AAAI 2024]、以可执行代码为技能库的 Voyager [Voyager, TMLR 2024]，以及以 Tips 与 Shortcuts 实现自进化的 Mobile-Agent-E [Mobile-Agent-E, arXiv 2025]。

这一主题的不足集中在四点：其一，图谱普遍为**离线一次性构建**，建成即冻结，应用界面更新后边失效而系统不自知，新页面也无法写回；其二，**边没有可靠性度量**——图中的边只记录"存在一条转移"，同一动作的多种结果（登录拦截、A/B 测试弹窗）被同等存入，规划器可能选中只在特定条件下成立的路径；其三，**图谱与模型的控制边界一刀切**——要么图谱只作 RAG 文本提示、模型仍每步推理（不节省任何模型调用），要么图谱直接接管导航跳转（边失效时没有回退机制），缺少对"哪些转移图谱可独立完成、哪些必须看屏幕决定"的逐边区分；其四，普遍**缺少后条件验证**，即没有机制核实"智能体实际到达了哪个页面"，使任何基于图谱的统计都建立在假设而非事实之上。

### 1.2.3 小模型端侧部署与大小模型协同

小模型端侧部署的可行性已有充分证据：MobileLLM [MobileLLM, ICML 2024] 系统研究了亚十亿参数端侧语言模型的架构设计；Octopus v2 [Octopus v2, arXiv 2024] 证明 2B 量级端侧模型在函数调用任务上可超过云端大模型方案的准确率与延迟；AutoDroid-V2 [AutoDroid-V2, arXiv 2024] 让端侧小模型生成脚本式多步代码而非逐步决策，以降低调用次数与误差累积。在大小模型协同方面，投机解码 [Speculative Decoding, ICML 2023] 开创了"小模型起草、大模型验证"的 token 级协同范式；FrugalGPT [FrugalGPT, arXiv 2023] 与 RouteLLM [RouteLLM, arXiv 2024] 分别以级联与学习路由在查询级分配大小模型；Minions [Minions, arXiv 2025] 提出端侧小模型与云端大模型在长文档任务内的协作协议；V-Droid [V-Droid, arXiv 2025] 将大模型用作每步动作候选的验证器而非生成器。

上述工作的不足在于协同粒度的两极分化：token 级（投机解码）与步级（V-Droid）协同要求大模型在每个 token 或每个动作处在场，无法摆脱高频云调用；查询级级联与路由（FrugalGPT、RouteLLM）在请求入口做一次性分配，大模型与小模型在任务内不再交互，无法对长程任务执行过程中逐渐累积的偏差进行持续纠偏；Minions 虽实现任务内协作，但面向静态的长文档推理，云端模型承担的是任务分解而非对有状态闭环控制过程的进度监督与完成裁决。换言之，现有协同机制中缺少**任务内、里程碑粒度、有状态**的监督形态——大模型以低频检查点介入、依靠持久化的会话状态跨检查点维持监督连续性，这正是长程 GUI 任务所需要的。

### 1.2.4 GUI 智能体基准评测

评测体系方面，AitW [AitW, NeurIPS 2023] 以 71.5 万条人类演示轨迹建立了离线动作匹配评测的事实标准；AndroidWorld [AndroidWorld, arXiv 2024] 提供 116 个可参数化任务与系统状态奖励信号的在线 Android 环境，是当前最主流的在线基准；AndroidLab [AndroidLab, ACL 2025] 在统一动作空间下同时支持语言与多模态模型的可复现评测，并提供指令微调数据；SPA-Bench [SPA-Bench, arXiv 2024] 覆盖 340 个中英文真实第三方应用任务并引入成本、耗时等多维指标；在底层能力评测上，ScreenSpot [SeeClick, ACL 2024] 建立了首个跨平台 GUI grounding 基准，而 ScreenSpot-Pro [ScreenSpot-Pro, ACM MM 2025] 在高分辨率专业软件场景下显示最先进模型的 grounding 准确率仅 18.9%，证明 GUI grounding 远未解决——这也从侧面支撑了本文"小模型 grounding 不可靠，需要系统级容错"的动机。

评测主题的不足在于：离线基准无法度量真实环境中的闭环成功率；在线基准的任务集中于开源与系统应用，缺少中文电商场景下"多条件商品筛选 + 价格区间约束 + 规格选择"这类长程多约束任务；过程性指标（任务推进到哪个阶段、在哪类页面失败）也普遍缺失。

### 1.2.5 研究现状小结

为直观对比，表 1-1 从图谱更新方式、边可靠性、图谱—模型边界、后条件验证与完成监督五个粗粒度维度比较了代表性结构化记忆方法与本文系统（逐文献的机制级深入辨析见第 6 章设计动机部分）。

表 1-1 代表性结构化记忆方法与本文系统的粗粒度对比

| 方法 | 图谱/记忆更新 | 边可靠性度量 | 图谱—模型边界 | 后条件验证 | 在线完成监督 |
|---|---|---|---|---|---|
| AutoDroid (UTG) [AutoDroid, MobiCom 2024] | 离线随机探索，建后冻结 | 无 | 文本注入，模型每步推理 | 无 | 无 |
| MobileGPT [MobileGPT, MobiCom 2024] | 层级树，任务期增补 | 无 | 树检索重放 | 无 | 无 |
| PG-Agent [PG-Agent, ACM MM 2025] | 离线批量建图，在线只读 | 无 | RAG 提示，模型每步推理 | 无 | 无 |
| KG-RAG [KG-RAG, EMNLP 2025] | 离线建库后冻结 | 无 | 向量检索提示 | 无 | 无 |
| WebNavigator [WebNavigator, arXiv 2026] | 离线爬取建图 | 哈希去重，无统计 | 图直接接管导航，无回退 | 无 | 无 |
| MobiAgent/AgentRR [MobiAgent, arXiv 2025] | 轨迹记录，手动纠正 | 无 | 二元重放匹配 | 无 | 里程碑仅用于离线判分 |
| 本文（Shopping-Agent/AMSG） | 每次成功任务在线增量更新 | 成功率 + 四阶段生命周期 | 逐边锚定性三档调度 | t+1 延迟验证 | 强 VLM 在线监督与完成裁决 |

综合上述四个主题，现有研究留下四个相互关联的缺口。**缺口一：能力—成本两难**——纯模型路线要么依赖昂贵的云端大模型与高频调用，要么受限于端侧小模型的长程能力短板，token 级、查询级与步级的大小模型协同均未提供任务内里程碑粒度的状态化监督。**缺口二：无状态重复探索**——多数智能体每个任务从零开始，已被反复验证的机械导航路径每次仍需消耗模型推理。**缺口三：无生命周期的图谱**——结构化记忆普遍离线一次性构建、边无可靠性度量、无后条件验证，无法应对真实应用的持续界面演化。**缺口四：二元的图谱—模型控制**——图谱要么只做提示不省调用、要么直接接管无回退，缺少逐边区分控制强度的机制。这四个缺口恰好对应本文 1.3 节的系统设计与三个创新点。

## 1.3 本文主要工作与创新点

针对上述研究缺口，本文设计并实现了面向移动端购物场景的小模型驱动 GUI 智能体系统 Shopping-Agent。系统以可量化端侧部署的小型 GUI 模型（AutoGLM-Phone-9B 等）为主执行器，整体架构由三个逻辑运行区域与一个监督层组成：决策区域回答"做什么"，以九阶段执行循环为唯一编排入口，承载机械护栏与三档调度；图谱区域回答"在哪、走哪"，以 AMSG 空间图谱完成页面定位、路径规划与快速路径供给，只做结构性导航不做语义判断；执行区域回答"怎么操作设备"，通过模型协议桥与设备抽象支持五个 GUI 模型家族与三类设备平台；监督层独立于运行时三区域，由强 VLM 在任务开始与里程碑处低频介入，通过会话记忆文件与任务计划同决策区域解耦。三层确定性结构——图谱加速机械导航、护栏接管确定性判断、强 VLM 里程碑低频监督——共同把小模型的长程可靠性从"寄望模型自我修正"转变为"系统设计的确定性兜底"。系统已在淘宝、京东两个真实电商应用上完成端到端验证。

本文的主要创新点如下：

**创新点 (1)**：针对纯小模型在长程任务上的焦点丢失、幻觉与伪完成问题，提出强 VLM 里程碑监督机制与会话记忆文件设计，强 VLM 仅在任务开始与里程碑处低频介入（约 1+步数/5 次调用，实测估算），通过四触发器去抖、原子计划修订与降级语义实现接近每步监督的可靠性（详见第 4 章）。

**创新点 (2)**：针对小模型的数字判断幻觉与伪完成，设计机械反幻觉护栏体系与确定性故障恢复机制，价格裁决、SpecGuard、单一完成权威、异常看门狗与空输出恢复均为纯代码确定性判断，零额外云调用，每类故障模式均有有界终止保证（详见第 5 章）。

**创新点 (3)**：针对无状态重复探索与静态图谱失效问题，设计 AMSG 自更新空间图谱与锚定性三档调度，以语义页面状态抽象、四阶段边生命周期、延迟后条件验证与 Fast Path/Co-pilot/模型路径三档调度使机械导航跳过模型推理（详见第 6 章）。

三个创新点分别对应缺口一（里程碑级监督填补 token 级验证与查询级路由之间的协同空档）、缺口二与缺口四的确定性判断侧（护栏以零云调用接管小模型不擅长的判断并保证有界终止）、缺口二至缺口四的导航侧（自更新图谱以生命周期与逐边锚定性同时解决静态失效、无可靠性度量与二元控制问题）。其有效性通过纯小模型、里程碑监督、每步规划三档消融在真机环境下验证（第 8 章），量化结果【实验数据待补充】。

## 1.4 论文组织结构

本文共分九章，组织结构如下：

第 1 章为绪论，阐述移动购物场景下 GUI 智能体的研究背景与端云两难问题，综述国内外研究现状并归纳四个研究缺口，给出本文的主要工作与创新点。

第 2 章介绍相关理论与技术，包括视觉语言模型与 GUI 智能体的基本范式、GUI grounding 模型、知识图谱与 Neo4j 图数据库，以及 ADB/HDC/XCTest 设备自动化技术，为后续章节提供技术基础。

第 3 章进行需求分析与系统总体设计，刻画环境与模型两类不确定性，提出非对称权威设计原则，给出三区域加监督层的总体架构、闭环数据流与九阶段执行循环。

第 4 章详细设计强 VLM 里程碑监督机制与会话记忆文件，包括 initialize/checkpoint/final_confirm 三类介入、四触发器去抖、原子计划修订、降级语义与 provider 链鉴别，以及会话记忆文件的结构与渲染注入。

第 5 章详细设计机械反幻觉护栏体系与确定性故障恢复机制，包括机械价格裁决、SpecGuard 购买安全守卫、完成门单一权威、异常看门狗与空输出恢复，并给出故障模式到确定性响应与有界终止的完整映射。

第 6 章从系统集成视角阐述 AMSG 自更新空间图谱与锚定性三档调度，包括语义页面状态抽象、边的锚定性划分与四阶段生命周期、RuntimeDAG 路径缓存、搜索 Compound 边合成以及页面分类器独立性红线。

第 7 章介绍系统实现与可视化，包括五模型家族适配与坐标桥、多平台设备抽象、WebUI 交互界面与记忆系统多数据面的实现。

第 8 章给出系统测试与实验框架，包括淘宝、京东真机实验环境、指标体系与三档消融矩阵设计，并报告实验结果【实验数据待补充】。

第 9 章总结全文工作，重申创新点及其验证结论，讨论系统局限并展望后续研究方向。

下一章首先介绍本文工作所依赖的相关理论与技术基础。
