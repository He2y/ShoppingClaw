# ColorAgent: Building A Robust, Personalized, and Interactive OS Agent

Ning Li$^{1*}\dagger$, Qiqiang Lin$^{2*}$, Zheng Wu$^{1\dagger}$, Xiaoyun Mo$^{2}$, Weiming Zhang$^{1\dagger}$, Yin Zhao$^{2}$, Xiangmou Qu$^{2}$, Jiam Zhou$^{2}$, Jun Wang$^{2}$, Congmin Zheng$^{1\dagger}$, Yuanyi Song$^{1\dagger}$, Hongjiang Chen$^{2}$, Heyuan Huang$^{2}$, Jihong Wang$^{2}$, Jiaxin Yin$^{2}$, Jingwei Yu$^{1\dagger}$, Junwei Liao$^{1\dagger}$, Qiuying Peng$^{2}$, Xingyu Lou$^{2\dagger}$, Jun Wang$^{2}$, Weiwen Liu$^{1\dagger}$, Zhuosheng Zhang$^{1\dagger}$, Weinan Zhang$^{1}$

 $ ^{1} $Shanghai Jiao Tong University  $ ^{2} $OPPO Research Institute lining01@sjtu.edu.cn linqiqiang1@oppo.com louxingyu@oppo.com {wliu, zhangzs}@sjtu.edu.cn

## Abstract

With the advancements in hardware, software, and large language model technologies, the interaction between humans and operating systems has evolved from the command-line interface to the rapidly emerging AI agent interactions. Building an operating system (OS) agent capable of executing user instructions and faithfully following user desires is becoming a reality. In this technical report, we present ColorAgent, an OS agent designed to engage in long-horizon, robust interactions with the environment while also enabling personalized and proactive user interaction. To enable long-horizon interactions with the environment, we enhance the model's capabilities through step-wise reinforcement learning and self-evolving training, while also developing a tailored multi-agent framework that ensures generality, consistency, and robustness. In terms of user interaction, we explore personalized user intent recognition and proactive engagement, positioning the OS agent not merely as an automation tool but as a warm, collaborative partner. We evaluate ColorAgent on the AndroidWorld and AndroidLab benchmarks, achieving success rates of 77.2% and 50.7%, respectively, establishing a new state of the art. Nonetheless, we note that current benchmarks are insufficient for a comprehensive evaluation of OS agents and propose further exploring directions in future work, particularly in the areas of evaluation paradigms, agent collaboration, and security.

## 1 Introduction

Over the past decades, the way humans interact with operating systems (OS) has undergone continuous transformation, from command-line (Stephenson, 1999) to graphical user interface (GUI) (Toby, 2001), and more recently to voice-assisted (Hoy, 2018) and AI-assisted (Mei et al., 2024) paradigms. These shifts point toward a future where the operating system itself becomes an intelligent mediator of user intent, embodied in what we call an OS Agent. An OS Agent is envisioned as a persistent, context-aware system that not only understands user instructions but can also autonomously orchestrate device functionalities to accomplish complex goals, towards a super-intelligent AI assistant bridging human and digital devices (Hu et al., 2025).

Recent advancements in (multimodal) large language models ((M)LLMs) and agentic frameworks have accelerated this vision, with numerous works building agents to complete user-specified tasks

<div style="text-align: center;"><img src="https://pplines-online.bj.bcebos.com/deploy/official/paddleocr/pp-ocr-vl-15//659c810e-51ca-4748-9f6f-c0be82342020/markdown_1/imgs/img_in_image_box_186_122_1038_527.jpg?authorization=bce-auth-v1%2FALTAKDN8mY5KlNI7zaRpLmOqrw%2F2026-05-03T07%3A59%3A57Z%2F-1%2F%2F3eba33bc75b522d9dd5270dc35ca4b509ab2041c21c5ead77f313be3c9566360" alt="Image" width="69%" /></div>


<div style="text-align: center;"><div style="text-align: center;">Figure 1: An overview of how the OS agent interacts with both the environment and the user.</div> </div>


by operating devices autonomously. Various studies (Hong et al., 2024; Gou et al., 2025; Wang et al., 2025a; Wu et al., 2025e; Wang et al., 2025b; Gu et al., 2025) focus on building end-to-end models with enhanced reasoning, perception, and grounding capabilities to support precise operations when completing user tasks. Meanwhile, popular agent frameworks have been proposed (Gur et al., 2024; Li et al., 2024b; Agashe et al., 2025; Ye et al., 2025), leveraging the cutting-edge (M)LLMs to decompose user tasks into structured decision-making pipelines.

Despite significant progress in automating task execution, existing agents are largely positioned as passive “task executors” rather than “interactive partners”. In contrast, an OS Agent focuses more on real-world, context-aware interactions, adapting not only to the environment but also to dynamic user needs. When interacting with the environment, the OS agent also acts as a task executor, but can stably interact with the environment in long-horizon and complex scenarios, which is not fully explored in previous works (Liu et al., 2025; Song et al., 2025). On the other hand, the OS Agent should be able to align with human intentions, including navigating ambiguous intentions and proactively interacting with the users. For instance, clarify a vague “Order a hamburger” request with a specific flavor by asking the user or reasoning according to the user’s historical interests instead of acting randomly. Unlike traditional agents that execute tasks in isolation, an OS Agent must continuously adjust its behavior in response to evolving user input and contextual shifts.

In this technical report, we propose ColorAgent, an OS agent designed to engage in long-horizon, robust interactions with the environment while also enabling personalized and proactive user interaction, as exemplified in Figure 1. ColorAgent is built as a GUI agent on mobile operating systems, which receives user instructions and operates mobile devices. To achieve robust environmental interaction, we present a tailored training paradigm and a sophisticated multi-agent framework. Furthermore, to foster a warm, collaborative partnership with the user, we explore novel mechanisms for personalized and proactive user interaction. The primary contributions of our work are as follows:

• Tailored Training Paradigm and Agent Framework. To support long-horizon and robust interactions in dynamic mobile environments, we introduce a twofold enhancement. At the model level, we adopt step-wise reinforcement learning and self-evolving training to refine the agent's grounding, perception, and reasoning abilities, enabling it to adapt to complex and dynamic GUI environments. At the framework level, we construct a multi-agent architecture that decouples task management from execution, integrates retrieval-augmented knowledge for better generalization, and employs hierarchical reflection to detect and recover from errors. Our twofold enhancement enables ColorAgent to accurately perceive and manipulate the environment, while also maintaining generalization, consistency, and stability across complex tasks.

<div style="text-align: center;"><img src="https://pplines-online.bj.bcebos.com/deploy/official/paddleocr/pp-ocr-vl-15//659c810e-51ca-4748-9f6f-c0be82342020/markdown_2/imgs/img_in_image_box_185_123_1038_526.jpg?authorization=bce-auth-v1%2FALTAKDN8mY5KlNI7zaRpLmOqrw%2F2026-05-03T07%3A59%3A58Z%2F-1%2F%2Fe49968917c163ba1ae5b7f618daac37c7548d016020653f61fb420782a06e66e" alt="Image" width="69%" /></div>


<div style="text-align: center;"><div style="text-align: center;">Figure 2: Our two-stage training framework for developing a powerful GUI model. The process begins with Step-wise Reinforcement Learning (left) to optimize the agent's core decision-making abilities on constructed training data. It then progresses to Self-evolving Training (right), a iterative loop that generates high-quality trajectory data to enable continuous improvement.</div> </div>


• Personalized and Proactive User Interaction. Beyond task execution, we emphasize the role of ColorAgent as a collaborative partner that aligns closely with human intent. To achieve this, we explore two complementary approaches. First, when additional user memory (e.g., histories, profiles, or preferences) is available, the agent can leverage explicit signals from past trajectories and implicit cues from user habits to personalize its behavior. Second, when no additional user memory is available, the agent proactively engages the user to clarify ambiguous intentions or incomplete instructions. It learns when to trust its environment and when to query the user, ensuring alignment through active dialogue. These mechanisms allow ColorAgent to move beyond a cold, utilitarian tool and evolve toward a warm, interactive partner.

We evaluate the autonomous task execution capability of ColorAgent on two widely adopted dynamic Android benchmarks, AndroidWorld (Rawles et al., 2025) and AndroidLab (Xu et al., 2024a). Experimental results demonstrate that ColorAgent achieves state-of-the-art (SOTA) performance, attaining success rates of 77.2% and 50.7%, respectively. In addition to assessing the agent's interaction with the environment, we also evaluate its human-agent interaction capabilities in specific scenarios. Results on MobileIAR (Wu et al., 2025d) and VeriOS-Bench (Wu et al., 2025c) indicate that our approach outperforms all baseline models, achieving performance scores of 58.66% and 68.98%, respectively. Despite these promising results, a comprehensive benchmark that captures the full spectrum of OS agent capabilities remains absent. To build a truly practical OS Agent, we advocate for the research community to move beyond isolated success-rate metrics and adopt more holistic evaluation paradigms that better reflect real-world complexity. Toward this goal, we outline several key research directions for advancing the field, including: (i) more comprehensive and nuanced evaluation protocols, (ii) better multi-agent collaboration frameworks, and (iii) robust mechanisms for safe and reliable execution.

## 2 Model Training

To develop a robust GUI model with precise interaction and deep reasoning capabilities as the backbone of ColorAgent, we utilize a two-stage progressive training paradigm. It addresses key challenges like perception ambiguity and action grounding by sequentially enhancing the model,

moving from single-step optimization to iterative, data-driven improvement. The overall paradigm is illustrated in Figure 2 and consists of two sequential stages:

• Stage I: Step-wise reinforcement learning ( $ \S $2.1) focuses on optimizing single-step decision-making. It employs reinforcement learning with tailored rewards to significantly improve the model's reasoning and action accuracy within complex GUI contexts.

• Stage II: Self-evolving training ( $ \S $2.2) tackles the data bottleneck by creating a self-sustaining data generation loop. This stage enables the model to automatically produce and refine high-quality interaction data, forming a reinforcing cycle that reduces the reliance on heavy manual annotation.

### 2.1 Stage I: Step-Wise Reinforcement Learning

Stage I employs step-wise sample-based reinforcement learning (RL) to enable the model to explore optimal actions given observations—specifically, historical interaction records and current GUI screenshots. This stage prioritizes equipping the model with effective decision-making patterns through exploration and feedback, instead of adhering to pre-defined "correct" reasoning patterns.

We define two core objectives for this stage: first, exploring robust single-step reasoning processes to improve decision accuracy across diverse GUI contexts; second, ensuring the model's outputs are both interpretable and executable, which lays a foundation for subsequent trajectory-level optimization. To accomplish these objectives, we designed a customized RL framework integrating two key components: (1) adaptive data construction, which simulates real-world GUI complexities; (2) rule-based rewards, which provide clear optimization signals.

#### 2.1.1 Data Construction

To ensure the diversity and representativeness of training data—critical for adapting to complex GUI environments—we leverage seven public GUI interaction datasets: Agwvis (Xu et al., 2024b), AITW (Rawles et al., 2023), AITZ (Zhang et al., 2024), AMEX (Chai et al., 2024), AndroidControl-High (Li et al., 2024a), GUIAct (Chen et al., 2024), and GUI-Odyssey (Lu et al., 2024). These datasets collectively provide large-scale offline GUI interaction trajectories. Each trajectory can be formally defined as  $ \{I, s_0, a_0, \cdots, s_T, a_T\} $, where  $ I $ denotes the user task instruction (e.g., "adjust screen brightness"),  $ s_t $ represents the GUI screenshot at step  $ t $, and  $ a_t $ is the ground-truth action executed at step  $ t $ to advance the task.

For step-wise reinforcement learning, data quality directly influences the model's ability to learn generalizable GUI interaction patterns. To guarantee data quality and align with RL training requirements, our data construction pipeline focuses on four essential aspects: step-wise splitting, multi-path augmentation, difficulty-based filtering, and cross-task enhancement.

Step-Wise Splitting. We decompose the aforementioned offline trajectories  $ \{I, s_0, a_0, \ldots, s_T, a_T\} $ into  $ T $ step-wise training samples  $ \{I, h_{t-1}, s_t, a_t\} $, where  $ h_{t-1} $ is the operational history in the previous  $ t-1 $ steps. It is generated by Qwen2.5-VL-72B (Bai et al., 2025) that summarizes action descriptions from each step in the historical operations. This decomposition preserves contextual dependencies between consecutive interaction steps—ensuring the model retains memory of prior actions, while enabling RL to optimize intermediate decision-making (rather than only final trajectory outcomes), which is critical for learning fine-grained GUI operation logic.

Multi-Path Augmentation. Traditional GUI interaction frameworks (Luo et al., 2025; Lu et al., 2025) adopt a rigid single-action annotation paradigm: they treat the pre-annotated action as the sole correct target, implicitly penalizing other valid alternatives. This design fails to align with real-world GUI interaction characteristics—where multiple distinct action paths often achieve the same task goal, driven by factors such as interface redundancy (e.g., dual-function control elements) and operation habits (e.g., gesture vs. command input), etc. Typical examples of such multi-path validity are widespread in practical use:

• Launching a target app: either by scrolling through the app drawer to click the app icon, or by directly invoking the predefined open_app function.

• Navigating back: either by tapping the on-screen "Back" arrow in the app interface, or by triggering the system_button("Back") command.

To address this mismatch, we use Qwen2.5-VL-72B to identify steps with multiple valid actions, expand annotations to include all verified alternatives, and assign them equal reward weight in training. This directly mitigates the bias of over-penalizing valid variants. While multi-path labeling may slightly compromise performance on static benchmarks (where only one “canonica” action is accepted), it substantially boosts generalization in real-world scenarios.

Difficulty-Based Filtering. To ensure that the training process focuses on informative and valuable samples, we introduce a difficulty-based filtering strategy. Specifically, we employ Qwen2.5-VL-72B to perform inference on each training sample, conducting eight independent runs per instance. The inference performance is quantified by the number of correct predictions  $ c \in [0, 8] $. The samples with either perfect predictions  $ (c = 8) $ or complete failures  $ (c = 0) $ contribute little to effective model learning. To address this imbalance, we discard all samples with  $ c = 8 $ and partially remove those with  $ c = 0 $. This filtering process yields a more balanced and informative dataset, thereby enhancing the overall learning efficacy and generalization performance of the model.

Cross-Task Enhancement. To mitigate the risk of overfitting and to bolster reasoning capabilities, the seven datasets we selected contain not only GUI interaction trajectories on mobile devices, but also data from computer and web platforms. Additionally, we incorporate two types of auxiliary tasks: (1) spatial localization-focused grounding tasks from AndroidControl-Low (Li et al., 2024a), which help the model accurately map the action intent to GUI element positions (e.g., locating a “back button” in crowded interfaces), and (2) geometric math tasks from Geometry3K (Lu et al., 2021) to strengthen the GUI model’s general reasoning capabilities.

#### 2.1.2 Group Relative Policy Optimization

We conduct step-wise reinforcement learning with the Group Relative Policy Optimization (GRPO) (Shao et al., 2024) algorithm. GRPO estimates the relative advantage values by sampling groups of responses, eliminating the need for a separate critic model for value estimation, providing a lightweight and stable alternative to Proximal Policy Optimization (PPO) (Schulman et al., 2017).

For each input sample by combining the user instruction  $ I $, current screenshot  $ s_t $, and history interactions  $ h_{t-1} $, the model generates  $ N $ candidate responses  $ O = \{o_1, o_2, \ldots, o_N\} $. Each response is evaluated by a rule-based reward function, yielding rewards  $ \{r_1, r_2, \ldots, r_N\} $. The estimated relative advantage  $ \hat{A}_i $ of the  $ i $-th response is computed as:

 $$ \hat{A}_{i}=\frac{r_{i}-\mathrm{m e a n}(\{r_{1},\cdots,r_{N}\})}{\mathrm{s t d}(\{r_{1},\cdots,r_{N}\})}. $$ 

This normalization enables comparison of responses within their group, capturing nuanced quality differences without an absolute reward scale.

After estimating the relative advantages, the policy is updated by maximizing a clipped surrogate objective, regularized by Kullback-Leibler (KL) divergence to prevent abrupt policy shifts:

 $$ \mathcal{J}_{\mathrm{G R P O}}(\theta)=\mathbb{E}\left[\frac{1}{N}\sum_{i=1}^{N}\min\left(\frac{\pi_{\theta}(o_{i})}{\pi_{\theta_{\mathrm{o l d}}}(o_{i})}\hat{A}_{i},\mathrm{c l i p}\left(\frac{\pi_{\theta}(o_{i})}{\pi_{\theta_{\mathrm{o l d}}}(o_{i})},1\pm\epsilon\right)\hat{A}_{i}\right)-\beta D_{\mathrm{K L}}(\pi_{\theta}||\pi_{\mathrm{r e f}})\right], $$ 

where  $ \pi_{\theta} $ and  $ \pi_{\theta_{\text{old}}} $ denote the current and previous policies,  $ \pi_{\text{ref}} $ is the reference model,  $ \epsilon $ (clipping threshold) and  $ \beta $ (KL coefficient) stabilize training, ensuring smooth policy evolution across diverse GUI environments.

#### 2.1.3 Rule-Based Rewards

For each response  $ o_i \in O $ generated by the model, it includes three parts: a thought containing the reasoning process (e.g., “To find the target item, I need …”), an action summary to describe the action in natural language (e.g., “Click the search button …”), and an executable action in JSON format. The action is composed of an action type and its possible parameters. Detailed action space is listed in Appendix A.

To evaluate the responses, we apply a rule-based reward consisting of two aspects: the format reward and the accuracy reward. The format reward  $ R_{fmt} $ assesses whether the response includes all three parts with a pre-defined format, and whether the action in JSON format can be correctly parsed. It is defined as:

 $$ R_{\mathrm{fmt}}=\begin{cases}1&if all parts are correctly formatted,\\0&otherwise.\end{cases} $$ 

The accuracy reward  $ R_{acc} $ measures whether both the predicted action type and its parameters are correct, defined as:

 $$ R_{\mathrm{acc}}=\begin{cases}1&if R_{type}=1\wedge R_{params}=1,\\0&otherwise.\end{cases} $$ 

Here,  $ R_{\text{type}} = 1 $ if the action type matches the ground truth, and  $ R_{\text{params}} = 1 $ if the action's parameters are correct. Specifically, coordinate-based actions are correct when the predicted coordinates fall within the ground-truth bounding box; text-related actions are validated by semantic similarity between the predicted and reference texts (e.g., F1 score); and swipe actions are correct when the predicted swipe direction matches the ground truth.

The final reward combines both the accuracy and format adherence:

 $$ R_{\mathrm{f i n a l}}=R_{\mathrm{a c c}}+\alpha\cdot R_{\mathrm{f m t}}, $$ 

where  $ \alpha = 0.2 $ is used to balance the two terms while maintaining the emphasis on action accuracy.

### 2.2 Stage II: Self-Evolving Training

To scale up trajectory data and reduce the heavy reliance on manual annotation, we propose a self-evolving training pipeline that establishes a robust reinforcing cycle of “data generation → model optimization → higher-quality data generation” based on the model refined in Stage I. As illustrated in the right panel of Figure 2, this iterative process begins with the query generation phase, where a dual-source strategy integrates human expertise and language model expansion. The generated queries are then processed through an iterative self-evolving cycle, which systematically generates, evaluates, and refines interaction data to continuously enhance model performance. Through this continual loop, the model progressively enhances its capabilities while breaking free from the limitations of static, manually annotated datasets.

#### 2.2.1 High-Quality Query Generation

To produce meaningful interaction trajectories, generating realistic, diverse, and task-relevant queries is essential. Our approach achieves this through a dual-source method that synergizes human expertise with a powerful language model. First, we utilize domain experts to craft a set of high-quality seed queries. This manual step ensures the queries are practical, executable, and reflect real-world user interactions, establishing a strong foundation of ecological validity. Subsequently, we employ a powerful large language model, DeepSeek-R1 (DeepSeek-AI et al., 2025), to expand upon these seeds, generating a wide array of syntactically and semantically varied queries. This model-driven expansion is crucial for ensuring comprehensive coverage of possible user interactions, including challenging corner cases that human experts may not intuitively consider.

#### 2.2.2 Iterative Self-Evolving Cycle

After generating high-quality queries, the training process enters an iterative self-evolving cycle designed to continuously refine both the data and the model itself. Each iteration of the self-evolving cycle consists of three sequential stages: trajectory rollout, trajectory filtering, and fine-tuning.

Trajectory Rollout. Using the pre-generated query pool, the current-stage model interacts with a dual-environment setup, combining Android virtual environments and ColorOS physical devices, to produce step-by-step interaction trajectories. For each query, the rollout is repeated multiple times to capture diverse interaction paths by varying initial conditions (starting pages) and decision-making strategies (a high temperature), which reflect the fluctuations of real-world user behavior and enrich the data diversity.

Trajectory Filtering. To ensure that only high-quality data enters training, we design a stringent filtering module composed of multiple specialized discriminators. Each discriminator evaluates a specific dimension of trajectory quality, such as task completion, action validity, path relevance, reasoning coherence, and other relevant criteria, which is detailed in Appendix B. The discriminators collectively cover a broad spectrum of critical evaluation aspects for trajectory validity.

Trajectories that pass all discriminators are retained for further fine-tuning. Those identified as incorrect trajectories (i.e., failing any of the discriminators) will be subjected to manual evaluation to locate and correct the flawed steps, and then be integrated into the fine-tuning dataset. These corrected trajectories are particularly valuable, as they address specific gaps in the model's decision-making and provide targeted training signals.

Fine-Tuning. The filtered and corrected trajectories are utilized for supervised fine-tuning on the current model. This fine-tuning step enhances the model's ability to generate accurate and coherent interaction trajectories, effectively preparing it for the next iteration of the self-evolving cycle. This iterative loop ensures that both the model and the quality of the data are mutually enhanced over time.

## 3 Agent Framework

### 3.1 Why the Single Agent Failed?

Although the single GUI agent (directly built upon the GUI model) has made significant advancements in capabilities such as grounding and reasoning through extensive post-training on large-scale datasets, a substantial gap remains before they can be robustly deployed in the high variability and uncertainty of real-world mobile environments. To diagnose the root causes of these failures, we analyzed the error distribution of a single agent on the Android-World (Rawles et al., 2025) benchmark. Our analysis, summarized in Figure 3, reveals a clear pattern: more than half of the failures stem from a lack of three core capabilities: generalization, reflection, and consistency. This data-driven insight underscores the fundamental limitations of a single-agent paradigm and motivates our adoption of a multi-agent framework.

<div style="text-align: center;"><img src="https://pplines-online.bj.bcebos.com/deploy/official/paddleocr/pp-ocr-vl-15//80cbdf63-e43a-4aa4-8417-e714a1e7d7ba/markdown_1/imgs/img_in_chart_box_806_971_1025_1187.jpg?authorization=bce-auth-v1%2FALTAKDN8mY5KlNI7zaRpLmOqrw%2F2026-05-03T08%3A00%3A05Z%2F-1%2F%2Fa363e5214188fb6b46ff8a670d7460101d2da8e4f7a81a4bb2339c93a7b39a3f" alt="Image" width="17%" /></div>


<div style="text-align: center;"><div style="text-align: center;">Figure 3: Error type distribution of the single agent.</div> </div>


#### 3.1.1 Limited Generalization

A single GUI agent, optimized on large-scale training datasets, can achieve strong in-domain performance but easily fails in out-of-domain environments or tasks (Wu et al., 2025b). For example, when performing a search operation, an agent knows to first click the search box and then enter the target content. However, a minor UI variation, such as a search box requiring two consecutive clicks to activate, can trap the agent in a repetitive and unproductive loop. This issue is compounded by the

agent’s lack of self-evolution: It is unable to learn from past trajectories to reuse prior experiences or avoid past mistakes. This inability to adapt to minor UI variations or learn from new experiences highlights a fundamental flaw: A single agent lacks a mechanism to dynamically incorporate external knowledge or evolve its strategy.

#### 3.1.2 Inconsistency and Lack of Memory

In real-world scenarios, there are often complex, long-horizon tasks that involve compositional goals and intricate dependencies (Guo et al., 2025). It's challenging for a single agent to maintain consistency and transfer information across long-term task execution. Take the task "Find the price of OPPO Find X9 in shopping apps A, B, and C, and add the one with the lowest price to the cart" as an example. The agent may exhibit flawed behavior, such as ignoring one of the apps, or fail entirely due to its inability to retain key information—the phone price in each app—throughout its execution. This example illustrates that without dedicated mechanisms for task decomposition, progress tracking, and cross-step memory management, a single agent is inherently incapable of handling the demands of compositional, long-horizon tasks.

#### 3.1.3 Difficulty in Error Recovery

When operating a mobile device, the agent may make various mistakes, such as clicking the wrong icon and entering an unintended page, or forgetting to activate the input field before entering text. If these initial errors are not corrected promptly, the agent may engage in meaningless exploration on the wrong page or fall into repetitive actions. Ultimately, the agent may mistakenly terminate the task, overlooking several critical steps.

Making mistakes is inevitable, even for the most powerful GUI agents. The key lies in how to detect errors and recover from them. While some work has attempted to enable the single agent with self-correction capabilities (Wu et al., 2025a; Wanyan et al., 2025), this is typically limited to individual steps, neglecting longer-term error recovery. Instead, enabling reflection at different scales of task execution can effectively enhance the robustness, thereby improving the applicability of GUI agents in real-world scenarios. After all, the cost of recovering from errors is far lower than the significant loss caused by task failure.

### 3.2 Multi-agent Framework

To address the fundamental limitations of a single agent detailed above, we construct a multi-agent framework $ ^{1} $ designed to significantly enhance its capabilities. As illustrated in Figure 4, our framework is built around a central execution module, which is augmented by three specialized, complementary modules, each directly targeting a core deficiency identified in Section 3.1:

• To combat limited generalization ( $ §\ 3.1.1 $), a Knowledge Retrieval module provides dynamic access to an external knowledge base.

- To overcome inconsistency and lack of memory ( $ \S $ 3.1.2), a Task Orchestration module decomposes complex goals and manages information flow across steps.

• To mitigate difficulty in error recovery ( $ \S $ 3.1.3), a Hierarchical Reflection module enables multi-level error detection and correction.

Together, these components form a synergistic system where the execution module’s core abilities are amplified, leading to more robust, consistent, and intelligent task completion. The following subsections will detail each of these components.

<div style="text-align: center;"><img src="https://pplines-online.bj.bcebos.com/deploy/official/paddleocr/pp-ocr-vl-15//80cbdf63-e43a-4aa4-8417-e714a1e7d7ba/markdown_3/imgs/img_in_image_box_198_159_1027_791.jpg?authorization=bce-auth-v1%2FALTAKDN8mY5KlNI7zaRpLmOqrw%2F2026-05-03T08%3A00%3A06Z%2F-1%2F%2Fad4b97f3abac574eac1c56e4102c854b099451bf61438e13c57fd9ff49faf94e" alt="Image" width="67%" /></div>


<div style="text-align: center;"><div style="text-align: center;">Figure 4: The architecture of our multi-agent framework. It consists of a central Execution module supported by three key components: the Task Orchestration module decomposes complex user instructions into manageable atomic tasks and handles memory transfer; the Knowledge Retrieval module provides relevant external information; and the Hierarchical Reflection module delivers multi-level feedback for error correction.</div> </div>


#### 3.2.1 Knowledge Augmentation with Retrieval

To enhance the agent’s adaptability to a wide range of tasks, we introduce a knowledge retrieval module that provides task-specific knowledge. Given a user instruction I or a decomposed atomic task  $ G_k $ as a query q, a retriever R searches a diverse knowledge database to find the most pertinent information. This knowledge base can contain manually constructed experiences, web-scraped content, or insights derived from historical trajectories. This entire process can be formally represented as:

 $$ K_{q}=R(q,D), $$ 

where  $ K_{q} $ is the retrieved knowledge with query q, and D is the knowledge database.

During task execution, the retrieved knowledge is provided to the execution module, equipping it with prior knowledge about the current environment and task, improving its reasoning ability and reducing errors. For example, when executing the task “Find my high-priority tasks in the Tasks app”, the knowledge retrieved from the knowledge base, “In the Task app, red represents high priority”, will play a critical role in completing the task.

#### 3.2.2 Task Management and Memory Transfer with Task Orchestration

The task orchestration module serves as the central hub for managing the entire task. Given a user instruction I, a task classifier TC first determines whether the current task is complex and decomposable (e.g., tasks that involve multiple sub-goals, cross-app interactions, or information transfer). If so, a task orchestrator TO will decompose it into a sequence of manageable atomic tasks:

 $$ \left\{G_{1},G_{2},\cdots\right\}=T C(I),\quad if T O(I)=Composite, $$ 

where  $ \{G_{1}, G_{2}, \ldots\} $ are a series of atomic tasks. Unlike traditional planner Wang et al. (2024), which require task-specific understanding (such as decomposing the task “open wifi” into “open settings, find the wifi switch, turn on wifi”), our task orchestration operates at a higher level (for example, decomposing “turn on wifi, increase phone brightness” into two atomic tasks: “turn on wifi” and “increase phone brightness”), preserving the natural structure of the user’s intent.

The primary challenge in executing this sequence of atomic tasks is maintaining information flow and context. To address this, we introduce a memory transfer mechanism. After the execution module completes an atomic task  $ G_k $, a task extractor TE distills the critical information from the trajectory for completing  $ G_k $. A task rewriter TR then integrates this content into the next atomic task  $ G_{k+1} $ to form an updated, context-aware instruction  $ \hat{G}_{k+1} $:

 $$ \hat{G}_{k+1}=T R(G_{k+1},T E(\{s_{1},a_{1},\ldots,s_{n},a_{n}\})), $$ 

where  $ \{s_1, a_1, \ldots, s_n, a_n\} $ are the trajectory (screenshots and actions) when completing  $ G_k $. For example, after completing the first atomic task “View the expenses from expenses.jpg in Simple Gallery Pro.” of the user instruction “Add the expenses from expenses.jpg in Simple Gallery Pro to pro expense”, the task extractor captures the contents of “expenses.jpg”. After that, the task rewriter will reformulate the second atomic task into “Add the following expenses to pro expense: xxx”. This process allows the agent to handle complex dependencies and robustly execute long-horizon tasks.

#### 3.2.3 Error Detection and Recovery with Hierarchical Reflection

Inherited from MobileUse (Li et al., 2025), we incorporate a hierarchical reflection module to improve the resilience of autonomous mobile systems by supporting error identification and correction across various stages of task execution. It consists of three major components:

- Action Reflector is responsible for real-time monitoring of individual actions. For every step, it inspects the screenshots captured before and after the action execution to determine whether the intended outcome is achieved. When issues such as grounding errors, visual misperceptions, or interface misinterpretations arise, the Action Reflector produces diagnostic feedback that highlights the problem and its potential cause. The execution module then leverages this feedback to adjust its subsequent behavior.

• Trajectory Reflector oversees short sequences of actions to track ongoing progress. It analyzes recent steps (typically the last 3–5 actions) together with the corresponding action-level reflections to verify whether the execution remains coherent and aligned with the task objective. Once inconsistencies are found, it delivers corrective feedback to help the execution module refine its trajectory and continue moving effectively toward the final goal.

- Global Reflector provides an overall assessment at the task level. Triggered only once a task has reached a tentative endpoint, it reviews the full sequence of actions along with the latest screenshots to decide whether the original instruction has been satisfactorily completed. If the task is judged incomplete, the Global Reflector supplies feedback that prompts the execution module to resume and complete the remaining steps in subsequent iterations.

By integrating these three levels of reflection, the hierarchical module equips the agent with the ability to capture and correct errors at different granularities. Each reflector delivers targeted feed-

back that guides the execution module in revising strategies, reducing unnecessary repetition, and ensuring continuous alignment with user instructions throughout the execution process.

## 4 Experiments

### 4.1 Experimental Settings

Benchmarks. We evaluate the proposed training strategies and agent framework on two widely used mobile benchmarks, AndroidWorld (Rawles et al., 2025) and AndroidLab (Xu et al., 2024a), to assess their effectiveness in enhancing environment interaction. AndroidWorld comprises 116 tasks drawn from 20 mobile applications, while AndroidLab includes 138 tasks across 9 applications. In both benchmarks, the agent is provided with natural language instructions and required to dynamically interact with a pre-initialized Android environment, with task success determined according to benchmark-defined rules. In addition, several issues identified in the original benchmarks were fixed, with details provided in Appendix C.

Implementation Details. For step-wise reinforcement learning, we employ Qwen2.5-VL-72B (Bai et al., 2025) and GUI-Owl-32B (Ye et al., 2025) as our base models, utilizing an implementation of GRPO adapted from the Verl framework (Sheng et al., 2024). We utilize the full-parameter configuration  $ 8 \times 8 $ A800 GPUs across 2 epochs with a learning rate of  $ 1 \times 10^{-6} $. The rollout number is set to 5.

### 4.2 Main Results



<table border=1 style='margin: auto; word-wrap: break-word;'><tr><td style='text-align: center; word-wrap: break-word;'>Methods</td><td style='text-align: center; word-wrap: break-word;'>AndroidWorld (SR)</td><td style='text-align: center; word-wrap: break-word;'>AndroidLab (SR)</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Proprietary Models</td><td style='text-align: center; word-wrap: break-word;'></td><td style='text-align: center; word-wrap: break-word;'></td></tr><tr><td style='text-align: center; word-wrap: break-word;'>GPT-4o-2024-11-20 (Achiam et al., 2023)</td><td style='text-align: center; word-wrap: break-word;'>34.5</td><td style='text-align: center; word-wrap: break-word;'>31.2</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Claude-Sonnet-4-20250514-thinking (Anthropic, 2025)</td><td style='text-align: center; word-wrap: break-word;'>41.0</td><td style='text-align: center; word-wrap: break-word;'>40.6</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>UI-TARS-1.5 (Qin et al., 2025)</td><td style='text-align: center; word-wrap: break-word;'>64.2</td><td style='text-align: center; word-wrap: break-word;'>38.3</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>MobileRL (Xu et al., 2025)</td><td style='text-align: center; word-wrap: break-word;'>75.8</td><td style='text-align: center; word-wrap: break-word;'>46.8</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Open Models</td><td style='text-align: center; word-wrap: break-word;'></td><td style='text-align: center; word-wrap: break-word;'></td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Qwen2.5-VL-7B-Instruct (Bai et al., 2025)</td><td style='text-align: center; word-wrap: break-word;'>27.6</td><td style='text-align: center; word-wrap: break-word;'>10.1</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>GLM-4.1V-9B-Thinking (Hong et al., 2025)</td><td style='text-align: center; word-wrap: break-word;'>41.7</td><td style='text-align: center; word-wrap: break-word;'>24.6</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>UI-TARS-7B (Qin et al., 2025)</td><td style='text-align: center; word-wrap: break-word;'>33.0</td><td style='text-align: center; word-wrap: break-word;'>32.6</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>V-Droid (Dai et al., 2025)</td><td style='text-align: center; word-wrap: break-word;'>59.5</td><td style='text-align: center; word-wrap: break-word;'>38.3</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>UI-Venus (Gu et al., 2025)</td><td style='text-align: center; word-wrap: break-word;'>65.9</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>GUI-Owl-7B (Ye et al., 2025)</td><td style='text-align: center; word-wrap: break-word;'>66.4</td><td style='text-align: center; word-wrap: break-word;'>42.8</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Frameworks</td><td style='text-align: center; word-wrap: break-word;'></td><td style='text-align: center; word-wrap: break-word;'></td></tr><tr><td style='text-align: center; word-wrap: break-word;'>MobileUse (Li et al., 2025)</td><td style='text-align: center; word-wrap: break-word;'>62.9</td><td style='text-align: center; word-wrap: break-word;'>44.2</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Mobile-Agent-v3 (Ye et al., 2025)</td><td style='text-align: center; word-wrap: break-word;'>73.3</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Qwen2.5-VL-72B-Instruct (Bai et al., 2025)</td><td style='text-align: center; word-wrap: break-word;'>35.0</td><td style='text-align: center; word-wrap: break-word;'>31.9</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>+ Model Training</td><td style='text-align: center; word-wrap: break-word;'>64.7</td><td style='text-align: center; word-wrap: break-word;'>46.4</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>GUI-Owl-32B (Ye et al., 2025)</td><td style='text-align: center; word-wrap: break-word;'>54.3</td><td style='text-align: center; word-wrap: break-word;'>38.4</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>+ Model Training</td><td style='text-align: center; word-wrap: break-word;'>65.1</td><td style='text-align: center; word-wrap: break-word;'>48.6</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>+ Agent Framework</td><td style='text-align: center; word-wrap: break-word;'>77.2</td><td style='text-align: center; word-wrap: break-word;'>50.7</td></tr></table>

<div style="text-align: center;"><div style="text-align: center;">Table 1: Success Rate (%) on the AndroidWorld and AndroidLab benchmark.</div> </div>


As shown in Table 1, ColorAgent achieves highly competitive performance among proprietary models, open models, and frameworks on both the AndroidWorld and AndroidLab benchmarks. We attribute this success to two key, complementary factors that we analyze below: the significant performance gains from our model training paradigm and the enhanced robustness provided by our agent framework.

Model Training Performance. Directly applying large open-source vision-language model (VLM) yields limited success. However, with our step-wise reference learning and self-evolving training strategies, remarkable improvements are exhibited on the Qwen2.5-VL-72B-Instruct and GUI-Owl-32B models. The former achieved a huge performance improvement of 29.3% and 14.5%, respectively, in AndroidWorld and AndroidLab. Although GUI-Owl-32B has been trained on a large amount of GUI data, our training strategy can still bring further improvements. These results confirm that our training paradigm significantly enhances the reasoning, perception, and grounding capabilities of GUI models, achieving results that are competitive with the state-of-the-art models.

Agent Framework Performance. Beyond model-level improvements, incorporating the proposed Agent framework further boosts performance, particularly in complex, long-horizon tasks. Building upon GUI-Owl-32B with model training, the addition of our knowledge retrieval, task orchestration, and hierarchical reflection modules increases the success rate to 77.2% on AndroidWorld and 50.7% on AndroidLab. This establishes a new state of the art (SOTA) among open models and frameworks, outperforming prior systems such as MobileUse (62.9% / 44.2%) and Mobile-Agent-v3 (73.3% on AndroidWorld). The results highlight the complementary nature of our contributions: while training improves the intrinsic capabilities of the base models, the agent framework ensures more consistent task execution, better error recovery, and stronger adaptability in dynamic environments.

### 4.3 Further Analysis

<div style="text-align: center;"><img src="https://pplines-online.bj.bcebos.com/deploy/official/paddleocr/pp-ocr-vl-15//b1e4abc0-127d-4709-8cb8-6b0a63fa6ded/markdown_1/imgs/img_in_chart_box_181_644_590_914.jpg?authorization=bce-auth-v1%2FALTAKDN8mY5KlNI7zaRpLmOqrw%2F2026-05-03T07%3A59%3A55Z%2F-1%2F%2F96f88d0354edcf11d54fa746c73619d1f3c98e0693bf02f810df975f8d2f2ae3" alt="Image" width="33%" /></div>


<div style="text-align: center;"><div style="text-align: center;">Figure 5: Training reward dynamics of different models.</div> </div>




<table border=1 style='margin: auto; word-wrap: break-word;'><tr><td style='text-align: center; word-wrap: break-word;'>Methods</td><td style='text-align: center; word-wrap: break-word;'>AndroidWorld (SR)</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Qwen2.5-VL-72B-Instruct</td><td style='text-align: center; word-wrap: break-word;'>35.0</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>+ Step-wise RL</td><td style='text-align: center; word-wrap: break-word;'>58.3</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>+ Self-evolving</td><td style='text-align: center; word-wrap: break-word;'>64.7</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>GUI-Owl-32B</td><td style='text-align: center; word-wrap: break-word;'>54.3</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>+ Step-wise RL</td><td style='text-align: center; word-wrap: break-word;'>63.0</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>+ Self-evolving</td><td style='text-align: center; word-wrap: break-word;'>65.1</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>+ Hierarchical Reflection</td><td style='text-align: center; word-wrap: break-word;'>70.3</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>+ Task Orchestration</td><td style='text-align: center; word-wrap: break-word;'>72.8</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>+ Knowledge Retrieval</td><td style='text-align: center; word-wrap: break-word;'>77.2</td></tr></table>

<div style="text-align: center;"><div style="text-align: center;">Table 2: Ablation study on the AndroidWorld benchmark.</div> </div>


Training Dynamics and the Generalization Trade-off. The training dynamics, shown in Figure 5, reveal a valuable insight into the trade-off between model capacity and generalization. While both the 32B and 72B models demonstrate successful learning with rapidly increasing rewards, the larger 72B model ultimately achieves a higher final reward, indicating a superior fit to the training data. However, this superior training-fit does not translate to better performance on the downstream benchmarks. As reported in Table 1, the fine-tuned 32B model decisively outperforms the 72B model in the dynamic test environments. This discrepancy strongly suggests that the 72B model, despite its larger capacity, is more prone to overfitting the training data, which harms its ability to generalize to unseen scenarios. This finding underscores a critical challenge in developing GUI agents: balancing the expressive power of large models with the need for robust generalization remains an important avenue for future research.

Ablation Study. Table 2 presents the ablation results on the AndroidWorld benchmark. For Qwen2.5-VL-72B-Instruct, the baseline achieves only 35.0% success rate, while the introduction of step-wise reinforcement learning (RL) substantially improves performance to 58.3%, and further gains are observed when applying self-evolving training, reaching 64.7%. A similar trend is observed for GUI-Owl-32B, demonstrating the generality of our training strategies across model scales. Building upon the trained GUI-Owl-32B, we further analyze the contribution of each component in our agent framework. Hierarchical reflection enhances robustness to execution errors, improving the

<div style="text-align: center;"><img src="https://pplines-online.bj.bcebos.com/deploy/official/paddleocr/pp-ocr-vl-15//b1e4abc0-127d-4709-8cb8-6b0a63fa6ded/markdown_2/imgs/img_in_image_box_187_149_1043_704.jpg?authorization=bce-auth-v1%2FALTAKDN8mY5KlNI7zaRpLmOqrw%2F2026-05-03T07%3A59%3A56Z%2F-1%2F%2F47922e916affee01fc8eeab8d2204ca959695fa4437484d0b690715c7ac28a49" alt="Image" width="69%" /></div>


<div style="text-align: center;"><div style="text-align: center;">Figure 6: Comparison between a normal agent and a warm OS agent. The normal agent only follows the ordering instructions but may not select the burger type that aligns with human intent, whereas the warm OS agent accurately chooses the type of burger that the human actually wants.</div> </div>


success rate to 70.3%. Task orchestration, which decomposes complex instructions and manages memory, provides additional gains. Finally, the incorporation of knowledge retrieval yields the highest performance at 77.2%, highlighting the importance of external knowledge for tackling diverse tasks. These results confirm that both the training strategies and agent-level modules contribute complementary improvements, and together they enable ColorAgent to achieve state-of-the-art performance on AndroidWorld.

## 5 From Tool to Partner: Building Warm OS Agents Beyond Task Execution

Although agents are becoming increasingly capable of completing tasks (Ye et al., 2025; Wang et al., 2025a), focusing solely on enhancing their task-execution abilities will only result in a cold, utilitarian tool. To transform them into warm, collaborative partners like Jarvis in Iron Man, we must enhance their ability to understand and align with human intentions.

As shown in Figure 6, aligning with human intentions can be approached under two task settings: (i) with additional user memory (e.g., user interaction history, user profiles), and (ii) without additional user memory. In the setting with additional user memory, OS agents need to be capable of analyzing user memory to perform tasks in a personalized manner, whereas without additional user memory, OS agents should possess interactive capabilities to align with human intentions through interaction with users. For these two different settings, in ColorAgent, we explore two plug-and-play modules (Wu et al., 2025d;c): personalized user intent recognition and proactive engagement. $ ^{2} $

### 5.1 Personalized User Intent Recognition

In the setting with additional user memory, we explore a recognizer framework (Wu et al., 2025d) that can rewrite user-provided queries into user-specific personalized queries and generate personalized standard operating procedures (SOPs) based on the additional user memory. The framework consists of two phases: the intention flow extraction phase and the deployment phase.

In the intention flow extraction phase, the framework retrieves users' historical trajectories and extracts the corresponding SOPs for each query from these trajectories to construct a query-level explicit user intent knowledge base, while simultaneously refining user profiles through the analysis of historical trajectories to build a user-level implicit user intent knowledge base.

In the deployment phase, the framework first matches the current user query with the most similar query and SOP from the explicit user intent knowledge base using a retrieval-augmented generation (RAG)-based approach and extracts the SOP for the current user query via an SOP Extractor. It then rewrites the current user query and SOP into a personalized user query and SOP using a query rewriter. Therefore, the framework can incorporate relevant knowledge from both the explicit and implicit user intent knowledge bases into the operational mobile agent through personalized queries and SOPs, thereby achieving better alignment with human intent.

### 5.2 Proactive Engagement

In the setting without additional user memory, we explore a query-driven proactive human-agent-GUI interaction method (Wu et al., 2025c) to align with human intention by proactive engagement. And we employ a two-stage learning paradigm that facilitates the decoupling and utilization of meta-knowledge to construct the ask agent.

We categorize the required capabilities of the ask agent into two types of meta-knowledge: (i) the judgment of when to ask questions in a scenario and how to pose them, and (ii) the enhancement of action generation based on question-answer pairs to align with human intentions. In our training framework, we decouple the same screenshot based on meta-knowledge into two samples. Specifically, the prompt for the first sample does not include the history of question-answer pairs, and if it is determined that the current scenario does not require asking a question, it will output a non-ASK action normally; if the current scenario is deemed to require a question, the action will be changed to an ASK action. The prompt for the second sample, however, includes the history of question-answer pairs, and the action is always a non-ASK action. We then conduct interleaved training with these two types of samples to obtain the final ask agent that incorporates both types of knowledge.

The ask agent is specifically designed for human-agent-GUI interaction and can autonomously determine whether the current scenario is trustworthy. If the current scenario is trustworthy, the agent executes tasks automatically; if not, it aligns with human intent by proactively asking users for clarification. Therefore, we dynamically bridge the gap between full automation and precise human intent alignment by intelligently deciding when to execute tasks and when to seek guidance.

### 5.3 Experiment

To validate the practical value of our explored methods, we conduct experiments on MobileIAR (Wu et al., 2025d) (with additional user memory setting) and VeriOS-Bench (Wu et al., 2025c) (without additional user memory setting). MobileIAR is a user-specific OS agent benchmark with different ground truth annotations for different users, designed to test the capabilities of personalized OS agents. VeriOS-Bench, on the other hand, is an OS agent benchmark that includes a large number of untrustworthy scenarios, reflecting the trustworthiness of OS agents.

We conduct tests on these two benchmarks, with MobileIAR reporting IAR (intention alignment rate) and VeriOS-Bench reporting SR (step-wise success rate). Here, IAR requires the agent's output action to match the most intention-aligned action annotated in the MobileIAR dataset for



<table border=1 style='margin: auto; word-wrap: break-word;'><tr><td style='text-align: center; word-wrap: break-word;'>Methods</td><td style='text-align: center; word-wrap: break-word;'>MobileIAR (IAR)</td><td style='text-align: center; word-wrap: break-word;'>VeriOS-Bench (SR)</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Qwen2.5-VL-3B-Instruct</td><td style='text-align: center; word-wrap: break-word;'>12.07</td><td style='text-align: center; word-wrap: break-word;'>5.35</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>OS-Atlas-Pro-7B</td><td style='text-align: center; word-wrap: break-word;'>36.11</td><td style='text-align: center; word-wrap: break-word;'>44.39</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>UI-TARS-7B-SFT</td><td style='text-align: center; word-wrap: break-word;'>37.05</td><td style='text-align: center; word-wrap: break-word;'>49.73</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>UI-TARS-7B-DPO</td><td style='text-align: center; word-wrap: break-word;'>36.19</td><td style='text-align: center; word-wrap: break-word;'>49.73</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>UI-TARS-1.5-7B</td><td style='text-align: center; word-wrap: break-word;'>36.88</td><td style='text-align: center; word-wrap: break-word;'>48.13</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Qwen2.5-VL-7B-Instruct</td><td style='text-align: center; word-wrap: break-word;'>15.30</td><td style='text-align: center; word-wrap: break-word;'>23.53</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Qwen2.5-VL-32B-Instruct</td><td style='text-align: center; word-wrap: break-word;'>37.32</td><td style='text-align: center; word-wrap: break-word;'>45.45</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Qwen2.5-VL-72B-Instruct</td><td style='text-align: center; word-wrap: break-word;'>53.75</td><td style='text-align: center; word-wrap: break-word;'>54.01</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Qwen-VL-max</td><td style='text-align: center; word-wrap: break-word;'>17.99</td><td style='text-align: center; word-wrap: break-word;'>18.72</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>GPT-4o</td><td style='text-align: center; word-wrap: break-word;'>31.57</td><td style='text-align: center; word-wrap: break-word;'>40.64</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Ours</td><td style='text-align: center; word-wrap: break-word;'>58.66</td><td style='text-align: center; word-wrap: break-word;'>68.98</td></tr></table>

<div style="text-align: center;"><div style="text-align: center;">Table 3: Experimental results on MobileIAR and VeriOS-Bench. Our explored methods achieve optimal performance.</div> </div>


the current user, while SR requires the output action to align with the ground truth action annotated in VeriOS-Bench.

As shown in Table 3, the experimental results show that our methods outperform popular OS agents, regardless of the availability of additional user memory. This represents a key step in transforming OS agents from mere tools into warm partners.

## 6 Future Work

Our ColorAgent represents an initial step toward building an OS Agent that can seamlessly interact with both the environment and the user. While our work demonstrates promising progress, constructing an agent that is stable, reliable, and entirely trustworthy in real-world scenarios remains an ambitious challenge. To ultimately evolve into a super-intelligent AI assistant that users can depend on for long-term interaction, several critical issues must be addressed.

Evaluation Paradigm. Although our ColorAgent achieves impressive results on existing benchmarks, it's important to note that current benchmarks remain inadequate for a comprehensive evaluation of OS Agents. (i) There exists a substantial disparity between benchmark tasks and the complexities of real-world scenarios. Current benchmarks involve a limited range of applications, are dominated by simple tasks, and fail to reflect the user demands of complex tasks. Furthermore, they barely consider exceptional or unpredictable situations, which are intrinsic to real environments. (ii) Current evaluation focuses narrowly on task success rates, while neglecting user-centered dimensions such as the accuracy of intent recognition, the capacity for self-evolution through sustained interaction, and the overall quality of user experience. To mitigate these deficiencies, we are developing a novel benchmark designed to more faithfully approximate real-world scenarios, which will provide a more reliable evaluation environment and guide the development of practical systems for OS Agents.

Agent Collaboration. While our proposed multi-agent framework has demonstrated clear advantages over single-agent approaches, the design space for multi-agent collaboration remains largely unexplored. Future work includes exploring different collaborative architectures, such as centralized, sequential, or fully connected multi-agent systems (Yang et al., 2024), each offering different trade-offs in terms of scalability, flexibility, and communication overhead. Moreover, the issue of collaboration penalties among agents deserves further investigation. While cooperation can improve overall task coverage, it may also constrain the autonomy of individual agents and introduce efficiency bottlenecks. In the future, we aim to develop a more efficient multi-agent collaboration paradigm, offering scalable and robust solutions for OS Agents.

Security. In ColorAgent, we have introduced preliminary security mechanisms by enabling the agent to proactively query humans in untrustworthy scenarios. However, the large-scale deployment of OS Agents in real-world environments demands more comprehensive safeguards. One key direction is designing a safe and controllable sandbox environment, which allows the agent to satisfy user requirements while preventing unintended damage under abnormal conditions. Moreover, the ability of OS Agents to handle exceptional scenarios should be further strengthened to improve robustness during execution. Finally, fine-grained permission control is needed to clearly define the capability and operational boundaries of OS Agents (Wu et al., 2025b), thereby avoiding excessive intervention in both the environment and the user's activities.

## 7 Conclusion

In this report, we introduce ColorAgent, a mobile OS agent that supports both long-horizon, robust environment interaction and personalized, proactive user interaction. By combining model training with step-wise reinforcement learning and self-evolving mechanisms, together with a carefully designed multi-agent framework, ColorAgent achieves long-horizon, robust interactions with dynamic environments. Furthermore, our exploration of user intent recognition and human-agent interaction enables ColorAgent to transcend the role of a mere task-execution tool, evolving toward a warm and human-aligned OS agent.

## Acknowledgments

We acknowledge the support of the SJTU-OPPO Joint Lab on Artificial Intelligence.

## References

Josh Achiam, Steven Adler, Sandhini Agarwal, Lama Ahmad, Ilge Akkaya, Florencia Leoni Aleman, Diogo Almeida, Janko Altenschmidt, Sam Altman, Shyamal Anadkat, et al. Gpt-4 technical report. arXiv preprint arXiv:2303.08774, 2023.

Saaket Agashe, Kyle Wong, Vincent Tu, Jiachen Yang, Ang Li, and Xin Eric Wang. Agent s2: A compositional generalist-specialist framework for computer use agents, 2025. URL https://arxiv.org/abs/2504.00906.

Anthropic. Introducing claude 4, 2025. URL https://www.anthropic.com/news/claude-4.

Shuai Bai, Keqin Chen, Xuejing Liu, Jialin Wang, Wenbin Ge, Sibo Song, Kai Dang, Peng Wang, Shijie Wang, Jun Tang, et al. Qwen2. 5-vl technical report. arXiv preprint arXiv:2502.13923, 2025.

Yuxiang Chai, Siyuan Huang, Yazhe Niu, Han Xiao, Liang Liu, Dingyu Zhang, Shuai Ren, and Hongsheng Li. Amex: Android multi-annotation expo dataset for mobile gui agents. arXiv preprint arXiv:2407.17490, 2024.

Wentong Chen, Junbo Cui, Jinyi Hu, Yujia Qin, Junjie Fang, Yue Zhao, Chongyi Wang, Jun Liu, Guirong Chen, Yupeng Huo, et al. Guicourse: From general vision language models to versatile gui agents. arXiv preprint arXiv:2406.11317, 2024.

Gaole Dai, Shiqi Jiang, Ting Cao, Yuanchun Li, Yuqing Yang, Rui Tan, Mo Li, and Lili Qiu. Advancing mobile gui agents: A verifier-driven approach to practical deployment, 2025. URL https://arxiv.org/abs/2503.15937.

DeepSeek-AI, Daya Guo, Dejian Yang, Haowei Zhang, Junxiao Song, Ruoyu Zhang, Runxin Xu, Qihao Zhu, Shirong Ma, Peiyi Wang, Xiao Bi, Xiaokang Zhang, Xingkai Yu, Yu Wu, Z. F. Wu, Zhibin Gou, Zhihong Shao, Zhuoshu Li, Ziyi Gao, Aixin Liu, Bing Xue, Bingxuan Wang, Bochao Wu, Bei Feng, Chengda Lu, Chenggang Zhao, Chengqi Deng, Chenyu Zhang, Chong Ruan,

Damai Dai, Deli Chen, Dongjie Ji, Erhang Li, Fangyun Lin, Fucong Dai, Fuli Luo, Guangbo Hao, Guanting Chen, Guowei Li, H. Zhang, Han Bao, Hanwei Xu, Haocheng Wang, Honghui Ding, Huajian Xin, Huazuo Gao, Hui Qu, Hui Li, Jianzhong Guo, Jiashi Li, Jiawei Wang, Jingchang Chen, Jingyang Yuan, Junjie Qiu, Junlong Li, J. L. Cai, Jiaqi Ni, Jian Liang, Jin Chen, Kai Dong, Kai Hu, Kaige Gao, Kang Guan, Kexin Huang, Kuai Yu, Lean Wang, Lecong Zhang, Liang Zhao, Litong Wang, Liyue Zhang, Lei Xu, Leyi Xia, Mingchuan Zhang, Minghua Zhang, Minghui Tang, Meng Li, Miaojun Wang, Mingming Li, Ning Tian, Panpan Huang, Peng Zhang, Qiancheng Wang, Qinyu Chen, Qiushi Du, Ruiqi Ge, Ruisong Zhang, Ruizhe Pan, Runji Wang, R. J. Chen, R. L. Jin, Ruyi Chen, Shanghao Lu, Shangyan Zhou, Shanhuang Chen, Shengfeng Ye, Shiyu Wang, Shuiping Yu, Shunfeng Zhou, Shuting Pan, S. S. Li, Shuang Zhou, Shaoqing Wu, Shengfeng Ye, Tao Yun, Tian Pei, Tianyu Sun, T. Wang, Wangding Zeng, Wanjia Zhao, Wen Liu, Wenfeng Liang, Wenjun Gao, Wenqin Yu, Wentao Zhang, W. L. Xiao, Wei An, Xiaodong Liu, Xiaohan Wang, Xiaokang Chen, Xiaotao Nie, Xin Cheng, Xin Liu, Xin Xie, Xingchao Liu, Xinyu Yang, Xinyuan Li, Xuecheng Su, Xuheng Lin, X. Q. Li, Xiangyue Jin, Xiaojin Shen, Xiaosha Chen, Xiaowen Sun, Xiaoxiang Wang, Xinnan Song, Xinyi Zhou, Xianzu Wang, Xinxia Shan, Y. K. Li, Y. Q. Wang, Y. X. Wei, Yang Zhang, Yanhong Xu, Yao Li, Yao Zhao, Yaofeng Sun, Yaohui Wang, Yi Yu, Yichao Zhang, Yifan Shi, Yiliang Xiong, Ying He, Yishi Piao, Yisong Wang, Yixuan Tan, Yiyang Ma, Yiyuan Liu, Yongqiang Guo, Yuan Ou, Yuduan Wang, Yue Gong, Yuheng Zou, Yujia He, Yunfan Xiong, Yuxiang Luo, Yuxiang You, Yuxuan Liu, Yuyang Zhou, Y. X. Zhu, Yanhong Xu, Yanping Huang, Yaohui Li, Yi Zheng, Yuchen Zhu, Yunxian Ma, Ying Tang, Yukun Zha, Yuting Yan, Z. Z. Ren, Zehui Ren, Zhangli Sha, Zhe Fu, Zhean Xu, Zhenda Xie, Zhengyan Zhang, Zhewen Hao, Zhicheng Ma, Zhigang Yan, Zhiyu Wu, Zhui Gu, Zijia Zhu, Zijun Liu, Zilin Li, Ziwei Xie, Ziyang Song, Zizheng Pan, Zhen Huang, Zhipeng Xu, Zhongyu Zhang, and Zhen Zhang. Deepseek-r1: Incentivizing reasoning capability in llms via reinforcement learning, 2025. URL https://arxiv.org/abs/2501.12948.

Boyu Gou, Ruohan Wang, Boyuan Zheng, Yanan Xie, Cheng Chang, Yiheng Shu, Huan Sun, and Yu Su. Navigating the digital world as humans do: Universal visual grounding for GUI agents. In The Thirteenth International Conference on Learning Representations, 2025. URL https://openreview.net/forum?id=kxnoqaisCT.

Zhangxuan Gu, Zhengwen Zeng, Zhenyu Xu, Xingran Zhou, Shuheng Shen, Yunfei Liu, Beitong Zhou, Changhua Meng, Tianyu Xia, Weizhi Chen, et al. Ui-venus technical report: Building high-performance ui agents with rft. arXiv preprint arXiv:2508.10833, 2025.

Yuan Guo, Tingjia Miao, Zheng Wu, Pengzhou Cheng, Ming Zhou, and Zhuosheng Zhang. Atomic-to-compositional generalization for mobile agents with a new benchmark and scheduling system. arXiv preprint arXiv:2506.08972, 2025.

Izeddin Gur, Hiroki Furuta, Austin V Huang, Mustafa Safdari, Yutaka Matsuo, Douglas Eck, and Aleksandra Faust. A real-world webagent with planning, long context understanding, and program synthesis. In The Twelfth International Conference on Learning Representations, 2024. URL https://openreview.net/forum?id=9JQtrumvg8.

Wenyi Hong, Weihan Wang, Qingsong Lv, Jiazheng Xu, Wenmeng Yu, Junhui Ji, Yan Wang, Zihan Wang, Yuxiao Dong, Ming Ding, et al. Cogagent: A visual language model for gui agents. In Proceedings of the IEEE/CVF Conference on Computer Vision and Pattern Recognition, pp. 14281–14290, 2024.

Wenyi Hong, Wenmeng Yu, Xiaotao Gu, Guo Wang, Guobing Gan, Haomiao Tang, Jiale Cheng, Ji Qi, Junhui Ji, Lihang Pan, et al. Glm-4.1 v-thinking: Towards versatile multimodal reasoning with scalable reinforcement learning. arXiv e-prints, pp. arXiv-2507, 2025.

Matthew B Hoy. Alexa, siri, cortana, and more: an introduction to voice assistants. Medical reference services quarterly, 37(1):81–88, 2018.

Xueyu Hu, Tao Xiong, Biao Yi, Zishu Wei, Ruixuan Xiao, Yurun Chen, Jiasheng Ye, Meiling Tao, Xiangxin Zhou, Ziyu Zhao, et al. Os agents: A survey on mlIm-based agents for general computing devices use. arXiv preprint arXiv:2508.04482, 2025.

Ning Li, Xiangmou Qu, Jiamu Zhou, Jun Wang, Muning Wen, Kounianhua Du, Xingyu Lou, Qiuying Peng, and Weinan Zhang. Mobileuse: A gui agent with hierarchical reflection for autonomous mobile operation. arXiv preprint arXiv:2507.16853, 2025.

Wei Li, William Bishop, Alice Li, Chris Rawles, Folawiyo Campbell-Ajala, Divya Tyamagundlu, and Oriana Riva. On the effects of data scale on computer control agents. arXiv e-prints, pp. arXiv-2406, 2024a.

Yanda Li, Chi Zhang, Wanqi Yang, Bin Fu, Pei Cheng, Xin Chen, Ling Chen, and Yunchao Wei. Appagent v2: Advanced agent for flexible mobile interactions, 2024b. URL https://arxiv.org/abs/2408.11824.

Shunyu Liu, Minghao Liu, Huichi Zhou, Zhenyu Cui, Yang Zhou, Yuhao Zhou, Wendong Fan, Ge Zhang, Jiajun Shi, Weihao Xuan, et al. Verigui: Verifiable long-chain gui dataset. arXiv preprint arXiv:2508.04026, 2025.

Pan Lu, Ran Gong, Shibiao Jiang, Liang Qiu, Siyuan Huang, Xiaodan Liang, and Song-Chun Zhu. Inter-gps: Interpretable geometry problem solving with formal language and symbolic reasoning. arXiv preprint arXiv:2105.04165, 2021.

Quanfeng Lu, Wenqi Shao, Zitao Liu, Fanqing Meng, Boxuan Li, Botong Chen, Siyuan Huang, Kaipeng Zhang, Yu Qiao, and Ping Luo. Gui odyssey: A comprehensive dataset for cross-app gui navigation on mobile devices. arXiv preprint arXiv:2406.08451, 2024.

Zhengxi Lu, Yuxiang Chai, Yaxuan Guo, Xi Yin, Liang Liu, Hao Wang, Han Xiao, Shuai Ren, Guanjing Xiong, and Hongsheng Li. Ui-r1: Enhancing efficient action prediction of gui agents by reinforcement learning, 2025. URL https://arxiv.org/abs/2503.21620.

Run Luo, Lu Wang, Wanwei He, and Xiaobo Xia. Gui-r1 : A generalist r1-style vision-language action model for gui agents, 2025. URL https://arxiv.org/abs/2504.10458.

Kai Mei, Xi Zhu, Wujiang Xu, Wenyue Hua, Mingyu Jin, Zelong Li, Shuyuan Xu, Ruosong Ye, Yingqiang Ge, and Yongfeng Zhang. Aios: Llm agent operating system. arXiv preprint arXiv:2403.16971, 2024.

Yujia Qin, Yining Ye, Junjie Fang, Haoming Wang, Shihao Liang, Shizuo Tian, Junda Zhang, Jiahao Li, Yunxin Li, Shijue Huang, et al. Ui-tars: Pioneering automated gui interaction with native agents. arXiv preprint arXiv:2501.12326, 2025.

Christopher Rawles, Alice Li, Daniel Rodriguez, Oriana Riva, and Timothy Lillicrap. Androidinthewild: A large-scale dataset for android device control. Advances in Neural Information Processing Systems, 36:59708–59728, 2023.

Christopher Rawles, Sarah Clinckemaillie, Yifan Chang, Jonathan Waltz, Gabrielle Lau, Marybeth Fair, Alice Li, William E Bishop, Wei Li, Folawiyo Campbell-Ajala, Daniel Kenji Toyama, Robert James Berry, Divya Tyamagundlu, Timothy P Lillicrap, and Oriana Riva. Android-world: A dynamic benchmarking environment for autonomous agents. In The Thirteenth International Conference on Learning Representations, 2025. URL https://openreview.net/forum?id=il5yUQsrjC.

John Schulman, Filip Wolski, Prafulla Dhariwal, Alec Radford, and Oleg Klimov. Proximal policy optimization algorithms. arXiv preprint arXiv:1707.06347, 2017.

Zhihong Shao, Peiyi Wang, Qihao Zhu, Runxin Xu, Junxiao Song, Xiao Bi, Haowei Zhang, Mingchuan Zhang, YK Li, Y Wu, et al. Deepseekmath: Pushing the limits of mathematical reasoning in open language models, 2024. URL https://arxiv.org/abs/2402.03300, 2(3):5, 2024.

Guangming Sheng, Chi Zhang, Zilingfeng Ye, Xibin Wu, Wang Zhang, Ru Zhang, Yanghua Peng, Haibin Lin, and Chuan Wu. Hybridflow: A flexible and efficient rlhf framework. arXiv preprint arXiv:2409.19256, 2024.

Yuanyi Song, Heyuan Huang, Qiqiang Lin, Yin Zhao, Xiangmou Qu, Jun Wang, Xingyu Lou, Weiwen Liu, Zhuosheng Zhang, Jun Wang, Yong Yu, Weinan Zhang, and Zhaoxiang Wang. Colorbench: Benchmarking mobile agents with graph-structured framework for complex long-horizon tasks, 2025. URL https://arxiv.org/abs/2510.14621.

Neal Stephenson. In the beginning... was the command line. Avon Books New York, 1999.

Brian H Toby. Expgui, a graphical user interface for gsas. Applied Crystallography, 34(2):210–213, 2001.

Haoming Wang, Haoyang Zou, Huatong Song, Jiazhan Feng, Junjie Fang, Junting Lu, Longxiang Liu, Qinyu Luo, Shihao Liang, Shijue Huang, et al.  Ui-tars-2 technical report: Advancing gui agent with multi-turn reinforcement learning. arXiv preprint arXiv:2509.02544, 2025a.

Junyang Wang, Haiyang Xu, Haitao Jia, Xi Zhang, Ming Yan, Weizhou Shen, Ji Zhang, Fei Huang, and Jitao Sang. Mobile-agent-v2: Mobile device operation assistant with effective navigation via multi-agent collaboration. In A. Globerson, L. Mackey, D. Belgrave, A. Fan, U. Paquet, J. Tomczak, and C. Zhang (eds.), Advances in Neural Information Processing Systems, volume 37, pp. 2686–2710. Curran Associates, Inc., 2024. URL https://proceedings.neurips.cc/paper_files/paper/2024/file/0520537ba799d375b8ff552395c337a-Paper-Conference.pdf.

Xinyuan Wang, Bowen Wang, Dunjie Lu, Junlin Yang, Tianbao Xie, Junli Wang, Jiaqi Deng, Xiaole Guo, Yiheng Xu, Chen Henry Wu, et al. Opencua: Open foundations for computer-use agents. arXiv preprint arXiv:2508.09123, 2025b.

Yuyang Wanyan, Xi Zhang, Haiyang Xu, Haowei Liu, Junyang Wang, Jiabo Ye, Yutong Kou, Ming Yan, Fei Huang, Xiaoshan Yang, Weiming Dong, and Changsheng Xu. Look before you leap: A gui-critic-rl model for pre-operative error diagnosis in gui automation, 2025. URL https://arxiv.org/abs/2506.04614.

Penghao Wu, Shengnan Ma, Bo Wang, Jiaheng Yu, Lewei Lu, and Ziwei Liu. Gui-reflection: Empowering multimodal gui models with self-reflection behavior, 2025a. URL https://arxiv.org/abs/2506.08012.

Zheng Wu, Pengzhou Cheng, Zongru Wu, Lingzhong Dong, and Zhuosheng Zhang. Gem: Gaussian embedding modeling for out-of-distribution detection in gui agents. arXiv preprint arXiv:2505.12842, 2025b.

Zheng Wu, Heyuan Huang, Xingyu Lou, Xiangmou Qu, Pengzhou Cheng, Zongru Wu, Weiwen Liu, Weinan Zhang, Jun Wang, Zhaoxiang Wang, et al. Verios: Query-driven proactive human-agent-gui interaction for trustworthy os agents. arXiv preprint arXiv:2509.07553, 2025c.

Zheng Wu, Heyuan Huang, Yanjia Yang, Yuanyi Song, Xingyu Lou, Weiwen Liu, Weinan Zhang, Jun Wang, and Zhuosheng Zhang. Quick on the uptake: Eliciting implicit intents from human demonstrations for personalized mobile-use agents. arXiv preprint arXiv:2508.08645, 2025d.

Zhiyong Wu, Zhenyu Wu, Fangzhi Xu, Yian Wang, Qiushi Sun, Chengyou Jia, Kanzhi Cheng, Zichen Ding, Liheng Chen, Paul Pu Liang, and Yu Qiao. OS-ATLAS: Foundation action model for generalist GUI agents. In The Thirteenth International Conference on Learning Representations, 2025e. URL https://openreview.net/forum?id=n9PDaFNi8t.

Yifan Xu, Xiao Liu, Xueqiao Sun, Siyi Cheng, Hao Yu, Hanyu Lai, Shudan Zhang, Dan Zhang, Jie Tang, and Yuxiao Dong. Androidlab: Training and systematic benchmarking of android autonomous agents, 2024a. URL https://arxiv.org/abs/2410.24024.

Yifan Xu, Xiao Liu, Xinghan Liu, Jiaqi Fu, Hanchen Zhang, Bohao Jing, Shudan Zhang, Yuting Wang, Wenyi Zhao, and Yuxiao Dong. Mobilerl: Advancing mobile use agents with adaptive online reinforcement learning, 2025. URL https://github.com/THUDM/MobileRL.

Yiheng Xu, Zekun Wang, Junli Wang, Dunjie Lu, Tianbao Xie, Amrita Saha, Doyen Sahoo, Tao Yu, and Caiming Xiong. Aguvis: Unified pure vision agents for autonomous gui interaction. arXiv preprint arXiv:2412.04454, 2024b.

Yingxuan Yang, Qiuying Peng, Jun Wang, Ying Wen, and Weinan Zhang. Llm-based multi-agent systems: Techniques and business perspectives. arXiv preprint arXiv:2411.14033, 2024.

Jiabo Ye, Xi Zhang, Haiyang Xu, Haowei Liu, Junyang Wang, Zhaoqing Zhu, Ziwei Zheng, Feiyu Gao, Junjie Cao, Zhengxi Lu, et al. Mobile-agent-v3: Foundamental agents for gui automation. arXiv preprint arXiv:2508.15144, 2025.

Jiwen Zhang, Jihao Wu, Yihua Teng, Minghui Liao, Nuo Xu, Xiao Xiao, Zhongyu Wei, and Duyu Tang. Android in the zoo: Chain-of-action-thought for gui agents. arXiv preprint arXiv:2403.02713, 2024.

### A Action Space



<table border=1 style='margin: auto; word-wrap: break-word;'><tr><td style='text-align: center; word-wrap: break-word;'>Action Type</td><td style='text-align: center; word-wrap: break-word;'>Parameters</td><td style='text-align: center; word-wrap: break-word;'>Description</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>click</td><td style='text-align: center; word-wrap: break-word;'>coordinate</td><td style='text-align: center; word-wrap: break-word;'>Click the screen at the specified  $ (x, y) $ coordinate.</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>long_press</td><td style='text-align: center; word-wrap: break-word;'>coordinate, time</td><td style='text-align: center; word-wrap: break-word;'>Press and hold on the screen at  $ (x, y) $ for a specified number of seconds.</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>swipe</td><td style='text-align: center; word-wrap: break-word;'>coordinate, coordinate2</td><td style='text-align: center; word-wrap: break-word;'>Swipe from the starting coordinate  $ (x, y) $ to the end coordinate  $ (x2, y2) $.</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>type</td><td style='text-align: center; word-wrap: break-word;'>text</td><td style='text-align: center; word-wrap: break-word;'>Input text into the currently focused input box.</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>clear_text</td><td style='text-align: center; word-wrap: break-word;'>\</td><td style='text-align: center; word-wrap: break-word;'>Clear the content of the active input box.</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>system_button</td><td style='text-align: center; word-wrap: break-word;'>button</td><td style='text-align: center; word-wrap: break-word;'>Press a system button: Back, Home, Menu, or Enter.</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>open</td><td style='text-align: center; word-wrap: break-word;'>text</td><td style='text-align: center; word-wrap: break-word;'>Launch an app on the device by name.</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>wait</td><td style='text-align: center; word-wrap: break-word;'>time</td><td style='text-align: center; word-wrap: break-word;'>Wait for a specified number of seconds.</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>answer</td><td style='text-align: center; word-wrap: break-word;'>text</td><td style='text-align: center; word-wrap: break-word;'>Answer the user query.</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>terminate</td><td style='text-align: center; word-wrap: break-word;'>status</td><td style='text-align: center; word-wrap: break-word;'>Terminate the current task and report whether it was a success or failure.</td></tr></table>

<div style="text-align: center;"><div style="text-align: center;">Table 4: Action space.</div> </div>


### B Details of Each Discriminator

The trajectory filtering module employs multiple specialized discriminators, each evaluating a specific dimension of trajectory quality. The discrimination aspects are:

• Task Completion: Assesses whether the trajectory successfully achieves the targeted task by comparing the final GUI state with the expected outcome detailed in the query description.

- Action Validity: Evaluates the accuracy of individual actions, including the validity of coordinates for click actions, semantic alignment for text inputs, and adherence to established GUI operational rules.

• Path Relevance: Determines the logical necessity of each step in the trajectory, ensuring that actions are relevant and contribute meaningfully to task progress.

- Reasoning Coherence: Checks for logical consistency in consecutive actions, confirming that preceding steps effectively support the transition into subsequent operations.

• Redundancy Check: Identifies and reduces excessive actions within the trajectory by evaluating the necessity of each operational step, thereby minimizing redundant operations and repetition of erroneous actions.

• User-Centric Evaluation: Assesses task success from the user’s perspective, prioritizing user experience and satisfaction with the achieved results.

• Behavioral Analysis: Evaluates the agent’s goal-directed behavior and decision-making quality, assessing adaptability to changing conditions.

### C Benchmark Issue Fixes

AndroidWorld When conducting multiple experiments using the same Android Virtual Device (AndroidWorldAVD), we observed that the internal state of certain apps could vary depending on tasks executed in previous runs (for example, the camera might remain in photo or video mode). Although a robust agent should ideally handle all possible states, such variations can introduce instability and inconsistency in evaluation. To mitigate this issue, we implemented a modification: before executing any task involving Audio Recorder, Camera, Tasks, Markor, Simple Calendar Pro, or Chrome apps, we reset the corresponding app to ensure a consistent internal state across all task runs.

AndroidLab When performing tasks related to Clock, we fixed an issue that prevented the correct extraction of information from the clock interface. For Settings-related tasks, we addressed problems that caused failures in retrieving the app storage, system brightness, and app notification information.

Our evaluation code on the AndroidWorld and AndroidLab benchmarks is available at https://github.com/MadeAgents/mobile-use.

