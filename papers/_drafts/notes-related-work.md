# 相关工作文献笔记（Related Work Notes）

> 用途：供两篇论文的"相关工作 / 研究现状"章节取材。
> 调研方式：WebSearch + arXiv 页面核实（2026-06-12）。所有条目均经搜索引擎核实存在；个别字段（作者顺序、录用 venue）无法完全确认处标注 [待核实]。
> 约定：每条 = 标题 / 首位作者 / venue / 年份 / arXiv id / 一句话定位 / 与本工作差异。

---

## ① 移动 GUI 智能体（Mobile GUI Agents）

### 1.1 AppAgent: Multimodal Agents as Smartphone Users
- 首位作者：Chi Zhang（腾讯）
- venue：arXiv 2023；后发表于 CHI 2025
- arXiv: 2312.13771
- 定位：最早的 LLM/VLM 驱动手机 App 操作智能体之一，通过"自主探索 + 人类演示"构建 App 知识文档，部署期查阅文档执行任务。
- 差异点：其知识是**非结构化文本文档**（per-element 描述），无页面拓扑、无状态图；本工作以图结构（页面/动作/里程碑）显式建模 App 状态空间，支持图检索而非文档检索。

### 1.2 Mobile-Agent: Autonomous Multi-Modal Mobile Device Agent with Visual Perception
- 首位作者：Junyang Wang（北交大 / 阿里）
- venue：arXiv 2024
- arXiv: 2401.16158
- 定位：纯视觉感知（OCR + 检测定位）的单体移动智能体，不依赖系统 XML 元数据。
- 差异点：单体、无长期记忆，每个任务从零开始；本工作引入跨任务可持久化的图谱记忆。

### 1.3 Mobile-Agent-v2: Mobile Device Operation Assistant with Effective Navigation via Multi-Agent Collaboration
- 首位作者：Junyang Wang（北交大 / 阿里）
- venue：NeurIPS 2024
- arXiv: 2406.01014
- 定位：planning / decision / reflection 三智能体协作 + 任务内记忆单元，缓解长程导航中的历史漂移。
- 差异点：记忆单元仅在**单任务生命周期内**有效；本工作的记忆跨任务、跨会话持久化，且 reflection 由独立的里程碑监督者承担、具有完成判定的最终裁决权。

### 1.4 Mobile-Agent-v3: Fundamental Agents for GUI Automation（GUI-Owl）
- 首位作者：Jiabo Ye（阿里通义实验室）
- venue：arXiv 2025
- arXiv: 2508.15144
- 定位：开源基座 GUI 模型 GUI-Owl（Qwen2.5-VL 后训练）+ 多智能体框架，AndroidWorld 73.3 SOTA。
- 差异点：靠大规模后训练与云端虚拟环境自进化提升能力，成本高；本工作不改模型参数，靠外置结构化记忆 + 监督者在推理期提升小模型表现。

### 1.5 AutoDroid: LLM-powered Task Automation in Android
- 首位作者：Hao Wen（清华 / AIR）
- venue：MobiCom 2024
- arXiv: 2308.15272
- 定位：随机探索构建 UI Transition Graph（UTG），以"探索记忆注入"增强 LLM 的 App 领域知识，HTML 式 UI 表示。
- 差异点：UTG 来自**离线随机探索**、与任务语义解耦；本工作的图谱从真实任务轨迹在线增量构建，节点携带任务/里程碑语义，并参与执行期决策与完成判定。

### 1.6 CogAgent: A Visual Language Model for GUI Agents
- 首位作者：Wenyi Hong（清华 / 智谱）
- venue：CVPR 2024（highlight）
- arXiv: 2312.08914
- 定位：18B 双分辨率编码器 VLM，1120×1120 输入识别微小 UI 元素，GUI 专用基座模型路线。
- 差异点：模型能力路线（预训练范式）；本工作是系统/架构路线，与此类基座模型正交、可叠加。

### 1.7 UI-TARS: Pioneering Automated GUI Interaction with Native Agents
- 首位作者：Yujia Qin（字节跳动 Seed）
- venue：arXiv 2025
- arXiv: 2501.12326
- 定位：端到端原生 GUI agent 模型，统一动作空间 + System-2 推理（反思、里程碑识别内化于模型）。
- 差异点：里程碑识别是**模型内隐能力**，不可审计；本工作将里程碑作为显式外部监督信号，由独立监督者校验，可解释、可干预。

### 1.8 AutoGLM: Autonomous Foundation Agents for GUIs
- 首位作者：Xiao Liu [作者顺序待核实]（智谱 / 清华）
- venue：arXiv 2024
- arXiv: 2411.00820
- 定位：ChatGLM 系 GUI 基座 agent，"中间接口"分离 planning 与 grounding，自进化在线课程 RL；AndroidLab 36.2%。
- 差异点：本工作直接以 AutoGLM-Phone-9B 这类小模型为**执行器**，贡献在于其外围的图谱记忆与里程碑监督架构，而非训练新模型。

---

## ② 图谱 / 结构化记忆驱动的 GUI/Web 导航

### 2.1 PG-Agent: An Agent Powered by Page Graph
- 首位作者：Weizhi Chen（浙大）
- venue：ACM MM 2025
- arXiv: 2509.03536
- 定位：自动管线将顺序轨迹转为"页面图"（页面跳转判定 → 节点相似检查 → 图更新），RAG 检索图中感知指南，多智能体执行；AitW/Mind2Web 验证。
- 差异点：与本工作最接近的先行者。差异：① PG-Agent 的图为**离线一次性构建**、服务于感知指南检索；本工作图谱在线增量演化并直接驱动动作决策；② PG-Agent 无完成监督机制；③ 本工作节点含里程碑/任务进度语义，支持购物类长程任务的阶段判定。

### 2.2 KG-RAG: Enhancing GUI Agent Decision-Making via Knowledge Graph-Driven Retrieval-Augmented Generation
- 首位作者：[待核实]
- venue：EMNLP 2025（main, 2025.emnlp-main.274）
- arXiv: 2509.00366
- 定位：将不完整 UTG 经 LLM 离线图搜索转为结构化向量库，在线 embedding 检索导航路径辅助决策。
- 差异点：图谱→向量库的**离线扁平化**会损失拓扑结构；本工作执行期直接在图上做状态定位与路径推理，且图随执行更新。

### 2.3 WebNavigator: Global Web Navigation via Interaction Graph Retrieval
- 首位作者：[待核实]
- venue：arXiv 2026 [录用情况待核实]
- arXiv: 2603.20366
- 定位：离线构建网站交互图（Interaction Graph）+ 在线"Retrieve-and-Navigate"，提出"拓扑盲区（Topological Blindness）"问题，探索深度 1→4 成功率 63.2%→75.5%。
- 差异点：面向 Web、依赖可大规模爬取的离线探索；移动 App 无法廉价爬取，本工作以真实任务轨迹为图谱唯一来源，强调小样本下的图谱有效性。

### 2.4 MobileGPT（Explore, Select, Derive, and Recall: Augmenting LLM with Human-like Memory for Mobile Task Automation）
- 首位作者：Sunjae Lee（KAIST）
- venue：MobiCom 2024
- arXiv: 2312.03003
- 定位：层级化 App 记忆（屏幕→子任务→任务），Explore/Select/Derive/Recall 四阶段，重复任务成本降 68.8%。
- 差异点：记忆按**屏幕-子任务层级树**组织，依赖 XML 视图层级；本工作为图结构（容纳跨页面环路与多路径），纯视觉输入也可定位状态。

### 2.5 Get Experience from Practice: LLM Agents with Record & Replay（AgentRR）
- 首位作者：[待核实]（上交 IPADS）
- venue：arXiv 2025
- arXiv: 2505.17716
- 定位：把经典 record-replay 引入 agent：记录交互轨迹与决策过程，抽象为多级"经验"，replay 时以 check function 作信任锚。
- 差异点：经验是**线性轨迹模板**，靠多级抽象换泛化；本工作把轨迹融合进全局图谱，新任务可走从未整条出现过的组合路径。

### 2.6 MobiAgent: A Systematic Framework for Customizable Mobile Agents
- 首位作者：[待核实]（上交 IPADS-SAI）
- venue：arXiv 2025
- arXiv: 2509.00531
- 定位：MobiMind 模型族 + AgentRR 加速框架（ActTree 结构）+ MobiFlow 基准（milestone DAG）三件套系统。
- 差异点：其 milestone DAG 用于**离线评测判分**；本工作的里程碑用于**在线执行监督与完成裁决**。ActTree 加速与本工作图谱检索目标相近，但 ActTree 面向重放加速、不承担状态理解。

### 2.7 Beyond Training: Enabling Self-Evolution of Agents with MobiMem
- 首位作者：[待核实]
- venue：arXiv 2025/2026
- arXiv: 2512.15784
- 定位：agent 级 record-replay 记忆层：拦截 agent-设备交互，记录 UI 状态 + 决策上下文，模板抽象 + 自适应动作校验实现自进化。
- 差异点：聚焦"重放安全性/泛化"；本工作记忆是面向状态空间的图谱，除复用外还服务于页面分类与进度监督。

### 2.8 AutoDroid 的 UTG 记忆（见 1.5，交叉引用）
- 作为"UTG 作为先验知识"路线的代表，在 ② 中与 KG-RAG、PG-Agent 并列讨论：三者分别代表 UTG 文本注入、UTG 向量化、轨迹页面图三种结构化方式。

---

## ③ 智能体经验复用与记忆机制

### 3.1 Agent Workflow Memory (AWM)
- 首位作者：Zora Zhiruo Wang（CMU）
- venue：arXiv 2024 [ICML 2025 录用待核实]
- arXiv: 2409.07429
- 定位：从历史轨迹归纳可复用 workflow，离线/在线两种归纳模式；WebArena 相对提升 51.1%。
- 差异点：workflow 是**线性动作序列**的文本抽象，无环境状态模型；本工作的图谱同时编码"环境怎么变"与"任务怎么走"，复用粒度是图上的边/子路径。

### 3.2 Synapse: Trajectory-as-Exemplar Prompting with Memory for Computer Control
- 首位作者：Longtao Zheng（NTU）
- venue：ICLR 2024
- arXiv: 2306.07863
- 定位：状态抽象 + 完整轨迹作为 few-shot 范例 + 相似轨迹记忆检索，解决上下文长度与轨迹不完整问题。
- 差异点：exemplar 级复用要求"整条相似轨迹存在"；本工作图谱支持片段级、跨任务组合复用。

### 3.3 ExpeL: LLM Agents Are Experiential Learners
- 首位作者：Andrew Zhao（清华）
- venue：AAAI 2024（Oral）
- arXiv: 2308.10144
- 定位：无参数更新的经验学习：经验池收集 + 自然语言 insight 提炼，推理期召回。
- 差异点：insight 是自然语言规则、无环境结构；本工作经验承载在显式图结构中，检索按当前页面状态精确定位而非语义相似。

### 3.4 Voyager: An Open-Ended Embodied Agent with Large Language Models
- 首位作者：Guanzhi Wang（NVIDIA / Caltech）
- venue：TMLR 2024（arXiv 2023）
- arXiv: 2305.16291
- 定位：skill library 路线开创者：可执行代码作为技能存入库，组合调用实现终身学习（Minecraft）。
- 差异点：技能=代码，适用于有程序化 API 的环境；GUI 环境动作不可靠且界面漂移，本工作以"图谱 + 在线监督"替代"代码技能 + 自验证"。

### 3.5 Mobile-Agent-E: Self-Evolving Mobile Assistant for Complex Tasks
- 首位作者：Zhenhailong Wang（UIUC / 阿里）
- venue：arXiv 2025
- arXiv: 2501.11733
- 定位：Manager + 四子代理层级框架，长期记忆存 Tips（一般经验）与 Shortcuts（可复用动作序列）实现自进化。
- 差异点：Tips/Shortcuts 是扁平条目列表；本工作记忆有图拓扑且与页面状态绑定，检索精度不依赖文本相似度。

### 3.6 AppAgentX: Evolving GUI Agents as Proficient Smartphone Users
- 首位作者：[待核实]
- venue：arXiv 2025
- arXiv: 2503.02268
- 定位：从重复执行轨迹中抽象高阶动作（一步替代多步），演化出更熟练的执行链。
- 差异点：演化目标是**动作压缩提效**；本工作目标是状态理解 + 完成正确性，效率收益是图谱复用的副产品。

### 3.7 AgentRR / MobiMem（见 2.5 / 2.7，交叉引用）
- 在 ③ 中作为"record-replay 式经验复用"与"轨迹归纳式复用（AWM/Synapse）"的对照组讨论。

---

## ④ 小模型端侧部署与大小模型协同/级联

### 4.1 Fast Inference from Transformers via Speculative Decoding
- 首位作者：Yaniv Leviathan（Google）
- venue：ICML 2023（Oral）
- arXiv: 2211.17192
- 定位：小模型起草、大模型并行验证，输出分布无损，2–3× 加速；"小模型提案 + 大模型验证"范式的源头。
- 差异点：token 级协同；本工作把同一范式提升到**任务步骤级**：小 VLM 执行动作，大模型监督者在里程碑粒度验证/否决。

### 4.2 FrugalGPT: How to Use Large Language Models While Reducing Cost and Improving Performance
- 首位作者：Lingjiao Chen（Stanford）
- venue：arXiv 2023 [正式 venue 待核实]
- arXiv: 2305.05176
- 定位：LLM 级联：先调用廉价模型，置信度不足再升级到昂贵模型，成本降至 1/98 仍保持 GPT-4 水平。
- 差异点：级联按**单次查询置信度**升级；本工作按任务执行的里程碑状态决定是否引入大模型介入，升级信号来自环境证据而非模型自信度。

### 4.3 RouteLLM: Learning to Route LLMs with Preference Data
- 首位作者：Isaac Ong（UC Berkeley）
- venue：arXiv 2024 [ICLR 2025 录用待核实]
- arXiv: 2406.18665
- 定位：用 Chatbot Arena 偏好数据训练 router，在强/弱模型间按 query 路由，成本降 2× 以上。
- 差异点：路由是请求前的一次性决策；本工作中大小模型在**同一任务内持续协作**（执行-监督），而非二选一。

### 4.4 Minions: Cost-efficient Collaboration Between On-device and Cloud Language Models
- 首位作者：Avanika Narayan（Stanford）
- venue：arXiv 2025
- arXiv: 2502.15964
- 定位：端侧小模型读全文、云端大模型分解子任务的协作协议（MinionS），成本降 5.7× 保留 97.9% 性能。
- 差异点：面向长文档静态推理；本工作面向**有状态的 GUI 闭环控制**，云端模型的角色是进度监督与完成裁决而非任务分解。

### 4.5 MobileLLM: Optimizing Sub-billion Parameter Language Models for On-Device Use Cases
- 首位作者：Zechun Liu（Meta）
- venue：ICML 2024
- arXiv: 2402.14905
- 定位：亚十亿参数端侧 LM 架构设计（深窄结构、embedding 共享、分组查询注意力）。
- 差异点：解决"小模型本身多强"；本工作解决"给定小模型，系统如何兜住其能力短板"，二者互补。

### 4.6 Octopus v2: On-device Language Model for Super Agent
- 首位作者：Wei Chen（Stanford）
- venue：arXiv 2024
- arXiv: 2404.01744
- 定位：2B 端侧模型以 functional token 做函数调用，准确率与延迟超 GPT-4 方案，端侧 agent 可行性证据。
- 差异点：API/函数调用型 agent；本工作是视觉 GUI 操作型，无可靠函数接口，故需外部监督保证正确性。

### 4.7 AutoDroid-V2: Boosting SLM-based GUI Agents via Code Generation
- 首位作者：Hao Wen [待核实]（清华 AIR）
- venue：arXiv 2024
- arXiv: 2412.18116
- 定位：让端侧小模型（SLM）生成脚本式多步代码而非逐步决策，降低端侧 GUI agent 的调用次数与误差累积。
- 差异点：以"代码化批量执行"换可靠性；本工作保留逐步闭环（适应动态界面），以图谱先验 + 里程碑监督换可靠性。

### 4.8 V-Droid（Advancing Mobile GUI Agents: A Verifier-Driven Approach to Practical Deployment）
- 首位作者：[待核实]
- venue：arXiv 2025
- arXiv: 2503.15937
- 定位：verifier 驱动范式：LLM 不做生成器而做动作候选打分验证器，AndroidWorld 59.5%。
- 差异点：验证发生在**每步动作选择**层；本工作的监督者在**里程碑/任务完成**层验证，且执行器与监督者异构（小 VLM + 大 LLM），二者可叠加。

---

## ⑤ GUI 智能体基准与评测

### 5.1 Android in the Wild (AitW): A Large-Scale Dataset for Android Device Control
- 首位作者：Christopher Rawles（Google）
- venue：NeurIPS 2023（Datasets & Benchmarks）
- arXiv: 2307.10088
- 定位：715k episodes / 30k 指令的大规模人类演示数据集，离线动作匹配评测的事实标准。
- 差异点：离线静态评测，无法度量真实环境闭环成功率；本工作主张真机在线评测 + 里程碑级过程指标。

### 5.2 AndroidWorld: A Dynamic Benchmarking Environment for Autonomous Agents
- 首位作者：Christopher Rawles（Google）
- venue：arXiv 2024 [ICLR 2025 录用待核实]
- arXiv: 2405.14573
- 定位：116 个可参数化任务 + 系统状态奖励信号的全功能在线 Android 环境，当前最主流的在线基准。
- 差异点：任务集中于开源/系统 App，缺少中文电商等复杂商业 App 的长程多约束任务；本工作评测面向真实购物场景（多条件商品筛选、价格区间、规格选择）。

### 5.3 AndroidLab: Training and Systematic Benchmarking of Android Autonomous Agents
- 首位作者：Yifan Xu（清华）
- venue：ACL 2025
- arXiv: 2410.24024
- 定位：统一动作空间下同时支持 LLM/LMM 的可复现环境，9 个 App、138 任务，并提供指令微调数据（开源模型 4.59%→21.50%）。
- 差异点：本工作执行器（AutoGLM-Phone 系）即在该体系下训练评测，故 AndroidLab 是直接的基线参照；差异在本工作不重训模型而做推理期增强。

### 5.4 GUI Odyssey: A Comprehensive Dataset for Cross-App GUI Navigation on Mobile Devices
- 首位作者：Quanfeng Lu（OpenGVLab）
- venue：ICCV 2025
- arXiv: 2406.08451
- 定位：8,834 条跨 App 导航轨迹（212 个 App、1.4K 组合），暴露单 App 训练范式在跨 App 任务上的失效。
- 差异点：评测仍为离线步级匹配；本工作的图谱天然记录跨 App 转移边，可作为跨 App 泛化的结构化先验。

### 5.5 SeeClick / ScreenSpot: Harnessing GUI Grounding for Advanced Visual GUI Agents
- 首位作者：Kanzhi Cheng（南大 / 上海 AI Lab）
- venue：ACL 2024
- arXiv: 2401.10935
- 定位：提出 GUI grounding 预训练 + 首个跨平台 grounding 基准 ScreenSpot（610 截图 / 1,272 指令）。
- 差异点：度量单步元素定位；本工作关注任务级长程正确性，grounding 只是执行器的底层能力之一。

### 5.6 ScreenSpot-Pro: GUI Grounding for Professional High-Resolution Computer Use
- 首位作者：Kaixin Li（NUS）
- venue：ACM MM 2025（先发于 ICLR 2025 Workshop）
- arXiv: 2504.07981
- 定位：高分辨率专业软件 grounding 基准（23 应用 / 5 行业），SOTA 模型仅 18.9%，证明 grounding 远未解决。
- 差异点：支撑本工作"小模型 grounding 不可靠 → 需要系统级容错"的动机论证。

### 5.7 SPA-Bench: A Comprehensive Benchmark for SmartPhone Agent Evaluation
- 首位作者：Jingxuan Chen（华为诺亚等）
- venue：arXiv 2024 / OpenReview [ICLR 2025 录用待核实]
- arXiv: 2410.15164
- 定位：340 个中英文真实第三方 App 任务、集成 11 个 agent 的统一在线评测，含成本/耗时等 7 维指标。
- 差异点：与本工作最契合的评测理念（中文第三方 App + 多维指标）；本工作进一步引入里程碑级过程正确性指标。

### 5.8 MobiFlow（MobiAgent 内的基准组件，见 2.6）
- venue：arXiv 2025（arXiv: 2509.00531 内）
- 定位：基于 milestone DAG 的移动 agent 评测套件，承认"同一任务多条合法路径"。
- 差异点：其 milestone 仅用于评测判分；本工作把同等思想前移到执行期监督。亦是"里程碑 DAG"概念先例，相关工作中必须引用并辨析。

---

## 两篇论文各自的引用映射建议

> 假设：论文 A = 图谱记忆驱动的移动 GUI 智能体（AMSG 主线）；论文 B = 里程碑监督的大小模型混合架构（supervisor 主线）。若实际切分不同，可按下表行级重组。

### 论文 A（图谱记忆 / AMSG）
| 章节段落 | 应引文献 | 论述功能 |
|---|---|---|
| 移动 GUI agent 背景 | AppAgent、Mobile-Agent v1/v2、CogAgent、UI-TARS、AutoGLM | 交代领域谱系：框架路线 vs 基座模型路线，引出"小模型执行器 + 外部记忆"定位 |
| 结构化记忆（核心对照） | **PG-Agent、KG-RAG、AutoDroid(UTG)、MobileGPT、WebNavigator** | 最近邻工作，逐一辨析：离线 vs 在线增量、检索用途 vs 决策用途、树/向量库 vs 图 |
| 经验复用 | AWM、Synapse、ExpeL、Voyager、Mobile-Agent-E、AppAgentX、AgentRR | 论证"线性轨迹/扁平条目复用"的泛化上限，引出图谱片段级组合复用 |
| 评测 | AitW、AndroidWorld、AndroidLab、GUI Odyssey、SPA-Bench | 说明现有基准缺少中文电商长程任务，论证自建真机评测的必要性 |

### 论文 B（里程碑监督 / 大小模型协同）
| 章节段落 | 应引文献 | 论述功能 |
|---|---|---|
| 端侧小模型动机 | MobileLLM、Octopus v2、AutoGLM、AutoDroid-V2 | 端侧部署可行但能力有限，确立"小模型执行器"前提 |
| 大小模型协同（核心对照） | **Speculative Decoding、FrugalGPT、RouteLLM、Minions、V-Droid** | 谱系：token 级验证 → 查询级级联/路由 → 任务内协作 → 步级验证；本工作定位为"里程碑级监督"，填补步级与任务级之间的空档 |
| 反思/验证机制 | Mobile-Agent-v2(reflection)、UI-TARS(内化里程碑)、MobiFlow(milestone DAG 判分)、MobiMem(动作校验) | 辨析：内化反思不可审计、评测期里程碑不参与执行；本工作把里程碑做成在线监督信号 + 单一完成裁决权 |
| 评测 | AndroidWorld、AndroidLab、SPA-Bench、ScreenSpot-Pro | ScreenSpot-Pro 支撑小模型 grounding 短板动机；SPA-Bench 支撑成本/耗时多维指标设计 |

### 两篇共享、需统一口径的引用
- AutoGLM（执行器出处，两篇都引，表述需一致）
- MobiAgent/MobiFlow 与 AgentRR（同时涉及图谱复用与里程碑，两篇都需辨析，避免相互矛盾的差异点表述）
- AndroidWorld / AndroidLab / SPA-Bench（基线与评测设置，数字引用需同源同版本）

---

## 核实状态总表

| 状态 | 条目 |
|---|---|
| 已核实（arXiv 页面/搜索确认） | 全部 38 条主条目的存在性与 arXiv id |
| [待核实] 字段 | AutoGLM 首作顺序；KG-RAG / WebNavigator / AgentRR / MobiAgent / MobiMem / AppAgentX / V-Droid 首位作者；AWM、RouteLLM、AndroidWorld、SPA-Bench 的正式录用 venue；FrugalGPT 正式 venue |
| 未收录原因示例 | "Mobile-Bench"、"AndroidArena" 等未做核实，本轮不收录 |
