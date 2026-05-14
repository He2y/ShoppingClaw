# PG-Agent: An Agent Powered by Page Graph

Weizhi Chen $ ^{*} $

Zhejiang Key Lab of Accessible

Perception & Intelligent Systems,

Zhejiang University

Hangzhou, China

chenweizhi@zju.edu.cn

Sheng Zhou†

Zhejiang Key Lab of Accessible Perception & Intelligent Systems,

Zhejiang University

Hangzhou, China

zhousheng_zju@zju.edu.cn

Ziwei Wang*

Zhejiang Key Lab of Accessible Perception & Intelligent Systems,

Zhejiang University

Hangzhou, China

wangziwei98@zju.edu.cn

Xiaoxuan Tang

Ant Group

Beijing, China

leahxx1226@outlook.com

Leyang Yang

Zhejiang Key Lab of Accessible

Perception & Intelligent Systems,

Zhejiang University

Hangzhou, China

yangleyang@zju.edu.cn

Yong Li†

Ant Group

Hangzhou, China

liyong.liy@antgroup.com

Jiajun Bu

Zhejiang Key Lab of Accessible

Perception & Intelligent Systems,

Zhejiang University

Hangzhou, China

bjj@zju.edu.cn

## Abstract

Graphical User Interface (GUI) agents possess significant commercial and social value, and GUI agents powered by advanced multimodal large language models (MLLMs) have demonstrated remarkable potential. Currently, existing GUI agents usually utilize sequential episodes of multi-step operations across pages as the prior GUI knowledge, which fails to capture the complex transition relationship between pages, making it challenging for the agents to deeply perceive the GUI environment and generalize to new scenarios. Therefore, we design an automated pipeline to transform the sequential episodes into page graphs, which explicitly model the graph structure of the pages that are naturally connected by actions. To fully utilize the page graphs, we further introduce Retrieval-Augmented Generation (RAG) technology to effectively retrieve reliable perception guidelines of GUI from them, and a tailored multi-agent framework PG-Agent with task decomposition strategy is proposed to be injected with the guidelines so that it can generalize to unseen scenarios. Extensive experiments on various benchmarks demonstrate the effectiveness of PG-Agent, even with limited episodes for page graph construction. Our codes will be publicly available at https://github.com/chenwz-123/PG-Agent.

Wei Jiang

Ant Group

Beijing, China

jonny.jw@antgroup.com

### CCS Concepts

• Human-centered computing → Human computer interaction (HCI); Interaction design.

### Keywords

GUI Agent; Retrieval-Augmented Generation; Multimodal Large Language Model

##### ACM Reference Format:

Weizhi Chen, Ziwei Wang, Leyang Yang, Sheng Zhou, Xiaoxuan Tang, Jiajun Bu, Yong Li, and Wei Jiang. 2025. PG-Agent: An Agent Powered by Page Graph. In Proceedings of the 33rd ACM International Conference on Multimedia (MM '25), October 27–31, 2025, Dublin, Ireland. ACM, New York, NY, USA, 13 pages. https://doi.org/10.1145/3746027.3755189

## 1 Introduction

The Graphical User Interface (GUI) has become crucial for humans in interacting with mobile devices and websites. Recently, there has been a notable increase in interest in GUI agents that can autonomously perform tasks by interacting with the user interface  $ [27] $. It is emerging as a significant topic of study in disciplines such as software engineering and human-computer interaction  $ [19, 31, 36] $, among several others. Early works employed parsing tools to transform the pages into HTML presentations, utilizing large language models (LLMs) to analyze the page layouts to make decisions  $ [14, 39] $. With the rapid development of multimodal large language models (MLLMs)  $ [1, 2, 6, 28, 42] $, MLLM-based GUI agents become the mainstream architecture, which are able to analyze the screen and generate actions end-to-end.

The GUI agents are conducted in a structured enclosed space where different pages are naturally interconnected through operations like clicking. Therefore, it is essential for the agent to possess the awareness of possible actions and their consequent pages. However, existing works [8, 22, 32] have collected abundant knowledge

<div style="text-align: center;"><img src="https://pplines-online.bj.bcebos.com/deploy/official/paddleocr/pp-ocr-vl-15//18850f7e-98cd-4e3c-b963-37c6e05bdea6/markdown_1/imgs/img_in_image_box_113_167_576_369.jpg?authorization=bce-auth-v1%2FALTAKDN8mY5KlNI7zaRpLmOqrw%2F2026-05-03T07%3A47%3A09Z%2F-1%2F%2Fa217e78db3a602dad79818f2bb7cba259cb6ddcad3d4e3b792f14dcc415a8cff" alt="Image" width="37%" /></div>


<div style="text-align: center;"><div style="text-align: center;">Figure 1: Illustration of PG-Agent. (i) Convert chain-like episodes into a semantically rich page graph; (ii) With page graph as GUI prior knowledge, RAG technology assists the tailored multi-agent workflow to enhance GUI navigation.</div> </div>


from diverse devices, but usually treat them as independent items. For example, the navigation tasks on GUI involving sequences of multi-step operations across different pages, where each step provides crucial semantics and functionality for the task. Such linear knowledge restricts the agent to focusing mainly on the consecutive steps and thus lacks the perceptions of possible actions leading to other pages simultaneously. As a result, the semantic association information between pages is not fully integrated into the model, leading to significant challenges when the model performs complex tasks, especially on new tasks. During the deployment phase, it is probable that a single sequential episode cannot directly instruct the agent to finish the task, while the transition relationships from multiple episodes can provide more clues about possible actions and corresponding results to pave a new way to the target page. This raises a natural question: "How to explicitly model the semantic relationships among pages and enhance the perception capability of GUI Agents in new scenarios?"

Fortunately, the pages of GUI screens naturally form a page graph connected by the actions, and a sequential episode is essentially a path sampling on this graph. Inspired by this concept, we can reconstruct the sequential episodes into the page graph, which offers a more comprehensive understanding of the page transitions, rather than the fragmented page connections brought by discrete episodes. Any traversal path in the page graph can be regarded as an effective recombination of original independent knowledge items. Meanwhile, positioning at a node, the agent can easily obtain possible actions from the outgoing edges and perceive consequent pages to assist the navigation process.

Besides, to utilize the page graph as prior knowledge for the agent planning, the Retrieval-Augmented Generation (RAG) technology  $ [15] $ is able to effectively leverage it without any parameter modifications, which enables the agent to adapt to different scenarios by simply switching the page graph. In this way, RAG is also able to explicitly retrieve the graph-structured information from the page graph, offering superior semantic perceptions of page transitions. Moreover, the exploration in the page graph of RAG is actually an accessing and integrating process of real actions in episodes, providing an authentic and reliable set of possible actions as guidelines. Previous works have adopted the RAG to retrieve guidelines like descriptions of widget functions  $ [16] $ or reference trajectories in similar tasks  $ [45] $, which are usually discrete without graph structure to perceive the current scenario deeply. Therefore, a tailored retrieval strategy applied in the page graph is also critical.

To tackle the aforementioned challenges, we propose an automated pipeline to transform the sequential episodes into the page graphs, including three stages of page jump determination, node similarity check and page graph update. During the process, we check every action tuple (i.e., the action and the pages before and after it) and gradually update the page graph by combining consecutive in-page operations into one edge and similar pages as one node. Moreover, to retrieving guidelines from the page graphs and fully utilize them, we also design a multi-agent framework PG-Agent enhanced by tailored RAG technology. First, we use the summary of the current screen to locate similar nodes in the page graph and conduct breadth first search (BFS) to explore available actions, deriving guidelines like "perform some actions can lead to accomplish some tasks". With comprehensive guidelines, we divide the reasoning process into several agents following existing works [35, 38], incorporate the task decomposition and inject the guidelines into the sub-task planning process, where the perceptions of the GUI scenario are particularly critical. We conduct extensive experiments on three benchmarks and the results demonstrate the effectiveness of PG-Agent, even if we only sample a few episodes to construct the page graph. The primary contributions of this paper can be summarized as follows:

- To model the transition relationships between GUI pages, We design an automated pipeline to reconstruct the discrete episodes into page graphs, which serve as external prior knowledge bases.

- We propose PG-Agent, a tailored multi-agent framework augmented by RAG technology. With the incorporation of task decomposition, guidelines retrieved from page graphs offer more targeted planning reference.

- Experimental results on three benchmarks demonstrate that PG-Agent exhibits superior navigation ability with the page graphs. Even if the episodes for page graph construction is limited, the effectiveness remains evident.

## 2 Related Work

### 2.1 Retrieval-Augmented Generation

Retrieval-Augmented Generation (RAG) can solve the issues of knowledge out-of-distribution in large models, such as output hallucination, lack of domain-specific knowledge, and outdated information by dynamically parsing the input content and retrieving relevant external knowledge [46]. Previous research on RAG mainly focused on question-answering related tasks [24, 33], such as using Table-to-Text methods to convert tabular data into textual form to enhance the document QA capability of LLM [25], using multi-modal embedding technology to uniform knowledge of different modalities to enhance multi-modal QA of the foundational model [13, 21], and utilizing the chunking methods to truncate the query and realize multi-granularity retrieval of external knowledge [5, 43]. Given that graph structures effectively represent complex data relationships and enable efficient information retrieval, the application of RAG technology to graph data has emerged as an interesting research focus [30]. GraphRAG [10] adopts a clustering approach by connecting small text blocks via semantic similarity, then applying community detection to group

<div style="text-align: center;"><img src="https://pplines-online.bj.bcebos.com/deploy/official/paddleocr/pp-ocr-vl-15//18850f7e-98cd-4e3c-b963-37c6e05bdea6/markdown_2/imgs/img_in_image_box_109_183_1111_433.jpg?authorization=bce-auth-v1%2FALTAKDN8mY5KlNI7zaRpLmOqrw%2F2026-05-03T07%3A47%3A09Z%2F-1%2F%2F4965de7a59bfaddb7c9e4b359d2e22bec38b855c9c077351013222fb33d15882" alt="Image" width="81%" /></div>


<div style="text-align: center;"><div style="text-align: center;">Figure 2: The overall pipeline of page graph construction. It comprises three stages: page jump determination, node similarity check and page graph update.</div> </div>


nodes, and finally summarizing query answers by analyzing node community responses. To model document relationships: Munikoti et al. [26] developed a heterogeneous document graph capturing multiple document-level relations, while Li et al. [17] and Wang et al. [37] established passage-level connections based on shared keywords. In the mobile agent scenario, there are also some works that use RAG to enhance the base model by providing additional interaction knowledge [16, 45]. However, they treat the traffic data as independent trajectory chains. We argue that in GUI scenarios, the data formed by the jump relationship between different screen pages is a global graph structure rather than discrete chains. Thus, ignoring the structured signals between pages will limit the model's knowledge learning in this domain.

### 2.2 GUI Agent

Recent progress has begun to adopt LLMs [34, 41, 45] to build autonomous agents, leveraging LLMs' extensive world knowledge and strong reasoning capabilities for task planning and execution to achieve human-like capabilities. Structural text replaces the original GUI image input into the LLMs. With the emergence of MLLMs, visual signals of images are projected into natural language space. Therefore, existing research tends to directly use MLLMs to build agents, so as to naturally process the visual information in the GUI field. One notable approach is to use large-scale general models, such as GPT-4v [28], as GUI agents. Many studies use prompt engineering to guide these models to perform complex tasks. AppAgent [44] is built on GPT-4v, generating guidance documents through exploration phase to assist decision-making. Mobile-Agent v2 [35] first proposes multi-agent collaboration in GUI scenarios to improve the decision-making effect of each step. Another research direction focuses on fine-tuning smaller MLLMs [7, 12, 18] using GUI-specific datasets to bridge the domain gap between common images and GUI screens.

## 3 Method

In this section, we will illustrate how to transform chained action episodes into structured page graphs, along with readable guideline documents. Subsequently, we introduce PG-Agent that is tailored to leverage the page graphs to achieve precise GUI navigation.

### 3.1 Page Graph Construction

Naturally, the pages and their links within a website or an application form a graph structure, and an episode to complete a navigation task actually represents a walking path in this graph. Thus, with existing episodes in a specific GUI scenario where relevant websites or applications are limited, we can construct the corresponding page graph as future guidance in this scenario. We design our pipeline purely based on visual clues without additional modal inputs such as the page's DOM or HTML architecture. The overall pipeline for page graph construction is shown in Figure 2, including three stages: page jump determination, node similarity check and page graph update.

Page Jump Determination. Assuming an action tuple in the episode E with task T is  $ (I_{\text{before}}, A, I_{\text{after}}) $, where  $ I_{\text{before}} $ and  $ I_{\text{after}} $ represent the screenshot images before and after the action A, respectively. First, the actions need to be converted into natural language. For actions involving specific coordinates, their meanings will be lost when separated from the corresponding images. Therefore, a MLLM [2] is used to summarize them:

 $$ S_{a c t i o n}=\operatorname{M L L M}(I_{b e f o r e},A,\mathbb{P}_{a c t i o n}), $$ 

where  $ S_{action} $ is the summary of the action and  $ P_{action} $ is the prompt for action summary. Then, considering fewer redundant nodes and retrieval effectiveness, only unique pages are adopted to build the page graph. Therefore, it is first necessary to determine whether the action A triggers the page jump.

 $$ Y_{j u m p}=\operatorname{M L L M}(I_{b e f o r e},I_{a f t e r},S_{a c t i o n},\mathbb{P}_{j u m p}), $$ 

where  $ Y_{jump} \in [Yes, No] $ represents the determination result and  $ \mathbb{P}_{jump} $ is the prompt template for page jump assessment. If the result is "No", the action is usually in-page operation like typing words or opening a drawer that do not lead to new pages, we will store this action summary  $ S_{action} $ in a queue  $ Q_{action} $ and directly process the next action tuple in the episode.

Node Similarity Check. When the result  $ Y_{jump} $ is "Yes", it means this action successfully leads to different pages. Then we take the following step to summarize the image after action based on the overall function of the page and key components displayed for node similarity check:

 $$ S_{p a g e}=\operatorname{M L L M}(I_{a f t e r},\mathbb{P}_{p a g e}), $$ 

<div style="text-align: center;"><img src="https://pplines-online.bj.bcebos.com/deploy/official/paddleocr/pp-ocr-vl-15//18850f7e-98cd-4e3c-b963-37c6e05bdea6/markdown_3/imgs/img_in_image_box_210_173_1014_570.jpg?authorization=bce-auth-v1%2FALTAKDN8mY5KlNI7zaRpLmOqrw%2F2026-05-03T07%3A47%3A10Z%2F-1%2F%2F86b7eb2c784c645b7433053eb6147c9434518b382b9f52392e16125eb92857d4" alt="Image" width="65%" /></div>


<div style="text-align: center;"><div style="text-align: center;">Figure 3: The framework of multi-agent workflow. It comprises two parts: RAG pipeline and multi-agent group.</div> </div>


where  $ S_{page} $ denotes the summary of the page and  $ \mathbb{P}_{page} $ is the template for page summary processing. Next, a dual-level similarity check from semantic aspect and pixel aspect was carried out. From semantic aspect, we employ similarity search with retrieval model to retrieve the top- $ n $ most similar page summaries from nodes of page graph  $ \mathcal{G} = (\mathcal{N}, \mathcal{V}) $, which is empty initially:

 $$ S_{n o d e}=\operatorname{R e t r i e v a l}(S_{p a g e},\mathcal{G}), $$ 

where  $ S_{node} = [S_1, S_2, \ldots, S_n] $ is composed of retrieved page summaries. Subsequently, we use the MLLM to further select the index of the most similar one:

 $$ i d=\operatorname{M L L M}(I_{a f t e r},S_{n o d e},\mathbb{P}_{s e l e c t}), $$ 

where  $ id \in [1, 2, \ldots, n] $ and  $ P_{select} $ is the prompt for index selection. From pixel aspect, we extract original image  $ I_{id} $ corresponding to selected page summary and compare it with image  $ I_{after} $ to finally conclude whether the page can pass the node similarity check:

 $$ Y_{d i s s i m i l a r}=M L L M(I_{a f t e r},I_{i d},\mathbb{P}_{d i s s i m i l a r}), $$ 

where  $ Y_{dissimilar} \in [Yes, No] $ is the check result and  $ \mathbb{P}_{dissimilar} $ is the prompt for similarity check.

Page Graph Update. When the result  $ Y_{dissimilar} $ is "Yes", it means that this new page is unique enough among existing nodes in the page graph G, and we will create a new node  $ N_{new} = (S_{after}, L_{after}) $ to represent the image  $ I_{after} $, where  $ L_{after} $ is the image location of  $ I_{after} $. Image location L will only be utilized in Equation 6 to get original image  $ I_{id} $, so the final page graph will not contain the pixel information of images, avoiding the huge space occupancy of page graph. Besides, the node representing the image  $ I_{before} $ can be formulated as  $ N_{before} $. Following, we incorporate the action summary  $ S_{action} $ into stored action queue  $ Q_{action} $ and combine it with the task description T of the episode as a new edge  $ \mathcal{E}_{new} = (Q_{action}, T) $. In this way, we can guide the agent to follow these actions when completing similar tasks. Next, we insert directed tuple  $ (N_{before}, \mathcal{E}_{new}, N_{new}) $ into page graph G to finish update:

 $$ \mathcal{G}=\mathcal{G}\cup(N_{{b e f o r e}},\mathcal{E}_{{n e w}},N_{{n e w}}). $$ 

If the decision of similarity check is “No”, page  $ I_{after} $ can actually be represented by existing node  $ N_{id} $ of image  $ I_{id} $, so the tuple to be inserted will be changed to  $ (N_{before}, \mathcal{E}_{new}, N_{id}) $:

 $$ \mathcal{G}=\mathcal{G}\cup(N_{b e f o r e},\mathcal{E}_{n e w},N_{i d}). $$ 

### 3.2 Multi-agent Workflow

The workflow of agent framework could be formalized as a Markov Decision Process (MDP) [4, 34, 41]. Previous work mainly uses a LLM, such as GPT-4 [29], to structure image text with the help of additional parsing tools [11, 45], or a separate MLLM [23, 44], such as GPT-4V [28], to preprocess the image using the Set-of-Marks strategy [40]. Then the design of the agent workflow is completed based on the prompt engineering. However, under the warper paradigm, the long-content poses a challenge to the reasoning performance of the model, making the model at risk of being "lost-in-the-middle" [20].

In this section, we adopt the multi-agent workflow that logically connects multiple MLLM-based agents with different roles. Each agent receives different input content and only completes specific tasks, which alleviates the context processing pressure of the model, and then spends more efforts on the task reasoning stage. Based on this architecture proposed before  $ [35] $, we further introduce the task decomposition concept into agent group.

Our multi-agent workflow is shown in Figure 3 and it mainly consists of two key parts: (1) RAG pipeline, which retrieves helpful guidelines from page graph based on screen status; (2) Multi-agent group: agents with different roles, i.e., global planning agent, observation agent, sub-task planning agent and decision agent.

Guidelines Retrieval. The guidelines retrieved from the page graph are the core mechanism to enhance the generalization capability of agents in new GUI scenarios. First, we prompt the MLLM to analyze the current screen status  $ I_{t} $ and generate a screen state

description  $ S_{I_t} $. Subsequently,  $ S_{I_t} $ is vectorized to retrieve the top n most similar nodes N from page graph G:

 $$ S_{I_{t}}=\mathrm{M L L M}(I_{t},\mathbb{P}_{s u m}), $$ 

 $$ \mathcal{N}=\operatorname{Retrieval}(S_{I_{t}},\mathcal{G}), $$ 

where  $ \mathbb{P}_{sum} $ represents the template for screen summary. Then we extract the action queues Q stored in the outgoing edges E of node set N. Besides, starting from every outgoing edge  $ E_i $, we conduct BFS with l layers and gather the tasks stored in the explored edges:

 $$ T_{i}=\mathrm{B F S}(\mathcal{E}_{i},\mathcal{G}), $$ 

where  $ T_{i} $ is the gathered achievable tasks from the edge  $ \mathcal{E}_{i} $. We combine the action queue and achievable tasks as the guidelines:

 $$ G_{I_{t}}=\left[\left(Q_{1},T_{1}\right),\left(Q_{2},T_{2}\right),\cdots,\left(Q_{k},T_{k}\right)\right], $$ 

where k is the number of retrieved guidelines. Each tuple  $ (Q_i, T_i) $ donates that the agent could follow the action queue  $ Q_i $ to complete tasks recorded in set  $ T_i $.

Global Planning Agent.  $ \mathcal{P}_{agent}^{G} $ is used to perform a global high-level sub-task decomposition of the user's task  $ T_g $, breaking down the complex task into relatively simple and abstract sub-tasks (i.e., the global plan). In this way, the guidelines can inspire the agent to focus on completing the current sub-task. This process can formulated as:

 $$ \mathcal{R}_{g}=\mathcal{P}_{a g e n t}^{G}(I_{t},T_{g}). $$ 

Observation Agent.  $ O_{agent} $ is responsible for transforming the pixel information into textual perceptions. It observes the screen and provides useful visual clues along with a high-level abstract functional description. In this stage, we introduce the historical interaction record  $ \tau_{<t} $ from the previous moment to help  $ O_{agent} $ perceive task progress. With user's task  $ T_g $,  $ O_{agent} $ can be formulated as:

 $$ \mathcal{R}_{o}=\mathcal{O}_{a g e n t}(I_{t},T_{g},\tau_{<t}), $$ 

where  $ \tau_{<t} = (I_0, a_0, I_1, a_1, ..., I_{t-1}, a_{t-1}) $ and  $ a_t $ represents the action executed at time-step  $ t $. The goal of the  $ O_{agent} $ is to directly provide explicit screen details to the decision agent  $ \mathcal{D}_{agent} $, so that it can pay more attention in reasoning.

Sub-Task Planning Agent.  $ \mathcal{P}_{agent}^{S} $ selects a sub-task that matches the current screen state from the global plan  $ \mathcal{R}_{g} $, provides a detailed description of the current task suggestion, and generates a candidate action list. Based on screen observation  $ \mathcal{R}_{o} $, global plan  $ \mathcal{R}_{g} $, retrieved guidelines  $ G_{I_{t}} $, and historical trajectory  $ \tau_{<t} $, this process can be formulated as:

 $$ \mathcal{R}_{s}=\mathcal{P}_{a g e n t}^{S}(I_{t},\mathcal{R}_{o},\mathcal{R}_{g},G_{I_{t}},\tau_{<t}). $$ 

Decision Agent.  $ \mathcal{D}_{agent} $ eventually chooses the specific action to be performed in the current screen state  $ I_{t} $ from the candidate action list  $ R_{s} $ via analyzing the previously generated content. The decision process can be formulated as:

 $$ \mathcal{R}_{d}=\mathcal{D}_{a g e n t}(I_{t},\mathcal{R}_{o},\mathcal{R}_{s},G_{I_{t}},\tau_{<t}). $$ 

As shown in Figure 3, when given a screen image  $ I_t $, RAG pipeline will summarizes  $ I_t $, vectorizes [9] the summary to retrieve similar nodes from page graph G and explore them to generate guidelines  $ G_{I_t} $. Then, Observation Agent  $ O_{agent} $ will carefully perceive the screen status  $ I_t $ and produce detailed description of

<div style="text-align: center;"><img src="https://pplines-online.bj.bcebos.com/deploy/official/paddleocr/pp-ocr-vl-15//18850f7e-98cd-4e3c-b963-37c6e05bdea6/markdown_4/imgs/img_in_chart_box_636_168_1114_596.jpg?authorization=bce-auth-v1%2FALTAKDN8mY5KlNI7zaRpLmOqrw%2F2026-05-03T07%3A47%3A11Z%2F-1%2F%2F4dfc2050b880da38f2fd16803db3ceec27d9e48f0bec530eecc9df73ea3af1ab" alt="Image" width="39%" /></div>


<div style="text-align: center;"><div style="text-align: center;">Figure 4: The data distribution of guidelines in AITW dataset. The x-axis represents the scenario category and the y-axis represents the number of retrieved guidelines at each step.</div> </div>


the page. Next, Global Planning Agent  $ \mathcal{P}_{agent}^{G} $ will decouple user's global task  $ T_g $ into several clear, coherent and relatively easy sub-tasks  $ \mathcal{R}_g $. Afterwards, Sub-Task Planning Agent  $ \mathcal{P}_{agent}^{S} $ will conduct in-depth analysis of the context information, including  $ I_t $,  $ \mathcal{R}_o $,  $ \mathcal{R}_g $,  $ G_{I_t} $ and  $ \tau_{<t} $, and complete the fine-grained plan  $ \mathcal{R}_s $ of the current sub-task under the help of guidelines  $ G_{I_t} $. Finally, the Decision Agent  $ \mathcal{D}_{agent} $ will use  $ \mathcal{R}_o $,  $ \mathcal{R}_s $,  $ G_{I_t} $, and  $ \tau_{<t} $ to generate the final decision  $ \mathcal{R}_d $ to predict the action that should be performed in the current state to advance the task  $ T_g $.

For more details on page graph and multi-agent workflow, please refer to the Supplementary Material.

## 4 Experiment

### 4.1 Experimental Setting

Benchmark Dataset. To assess the navigation ability in both mobile and website environment, we evaluate our PG-Agent on two GUI agent datasets: Android in the Wild (AITW) [32], Mind2Web [8] and GUI Odyssey [22]:

• AITW: The episodes in AITW dataset are collected in Android mobile phone environment, which are divided into five scenarios: General, Install, GoogleApps, Single, and WebShopping. We follow the split setting of SeeClick [7]. For simplicity, we randomly sample 1/10 episodes from training split to construct concise page graphs based on different scenarios. The specific statistics are shown in Table 8.

- Mind2Web: Mind2Web dataset contains over 2,000 open-ended tasks collected from 137 real websites, covering five scenarios: Entertainment, Travel, Shopping, Service, and Info. Also, we randomly sample episodes from the training set. The benchmark on Mind2Web is not divided by scenarios, but categorized into cross-task, cross-website, and cross-domain to test the generalization.

<div style="text-align: center;"><div style="text-align: center;">Table 1: Comparison of PG-Agent with different methods on Mind2Web dataset. The best and second-best results in each column are highlighted in bold font and underlined.</div> </div>




<table border=1 style='margin: auto; word-wrap: break-word;'><tr><td rowspan="2">Method</td><td colspan="3">Cross-Task</td><td colspan="3">Cross-Website</td><td colspan="3">Cross-Domain</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Ele.Acc</td><td style='text-align: center; word-wrap: break-word;'>Op.F1</td><td style='text-align: center; word-wrap: break-word;'>Step SR</td><td style='text-align: center; word-wrap: break-word;'>Ele.Acc</td><td style='text-align: center; word-wrap: break-word;'>Op.F1</td><td style='text-align: center; word-wrap: break-word;'>Step SR</td><td style='text-align: center; word-wrap: break-word;'>Ele.Acc</td><td style='text-align: center; word-wrap: break-word;'>Op.F1</td><td style='text-align: center; word-wrap: break-word;'>Step SR</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>MindAct</td><td style='text-align: center; word-wrap: break-word;'>55.1</td><td style='text-align: center; word-wrap: break-word;'>75.7</td><td style='text-align: center; word-wrap: break-word;'>52.0</td><td style='text-align: center; word-wrap: break-word;'>42.0</td><td style='text-align: center; word-wrap: break-word;'>65.2</td><td style='text-align: center; word-wrap: break-word;'>38.9</td><td style='text-align: center; word-wrap: break-word;'>42.1</td><td style='text-align: center; word-wrap: break-word;'>66.5</td><td style='text-align: center; word-wrap: break-word;'>39.6</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>GPT-4V</td><td style='text-align: center; word-wrap: break-word;'>46.4</td><td style='text-align: center; word-wrap: break-word;'>73.4</td><td style='text-align: center; word-wrap: break-word;'>40.2</td><td style='text-align: center; word-wrap: break-word;'>38.0</td><td style='text-align: center; word-wrap: break-word;'>67.8</td><td style='text-align: center; word-wrap: break-word;'>32.4</td><td style='text-align: center; word-wrap: break-word;'>42.4</td><td style='text-align: center; word-wrap: break-word;'>69.3</td><td style='text-align: center; word-wrap: break-word;'>36.8</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Qwen2.5-VL-72B</td><td style='text-align: center; word-wrap: break-word;'>31.9</td><td style='text-align: center; word-wrap: break-word;'>84.6</td><td style='text-align: center; word-wrap: break-word;'>26.2</td><td style='text-align: center; word-wrap: break-word;'>35.7</td><td style='text-align: center; word-wrap: break-word;'>80.7</td><td style='text-align: center; word-wrap: break-word;'>27.9</td><td style='text-align: center; word-wrap: break-word;'>32.0</td><td style='text-align: center; word-wrap: break-word;'>83.2</td><td style='text-align: center; word-wrap: break-word;'>25.0</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>OmniParser</td><td style='text-align: center; word-wrap: break-word;'>42.4</td><td style='text-align: center; word-wrap: break-word;'>87.6</td><td style='text-align: center; word-wrap: break-word;'>39.4</td><td style='text-align: center; word-wrap: break-word;'>41.0</td><td style='text-align: center; word-wrap: break-word;'>84.8</td><td style='text-align: center; word-wrap: break-word;'>36.5</td><td style='text-align: center; word-wrap: break-word;'>45.5</td><td style='text-align: center; word-wrap: break-word;'>85.7</td><td style='text-align: center; word-wrap: break-word;'>42.0</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>PG-Agent</td><td style='text-align: center; word-wrap: break-word;'>59.0</td><td style='text-align: center; word-wrap: break-word;'>84.7</td><td style='text-align: center; word-wrap: break-word;'>52.9</td><td style='text-align: center; word-wrap: break-word;'>57.3</td><td style='text-align: center; word-wrap: break-word;'>81.2</td><td style='text-align: center; word-wrap: break-word;'>48.7</td><td style='text-align: center; word-wrap: break-word;'>60.2</td><td style='text-align: center; word-wrap: break-word;'>84.5</td><td style='text-align: center; word-wrap: break-word;'>53.3</td></tr></table>

<div style="text-align: center;"><div style="text-align: center;">Table 2: Comparison of PG-Agent with different methods on GUI Odyssey dataset. The best and second-best results in each column are highlighted in bold font and underlined.</div> </div>




<table border=1 style='margin: auto; word-wrap: break-word;'><tr><td style='text-align: center; word-wrap: break-word;'>Method</td><td style='text-align: center; word-wrap: break-word;'>Tool</td><td style='text-align: center; word-wrap: break-word;'>Information</td><td style='text-align: center; word-wrap: break-word;'>Shopping</td><td style='text-align: center; word-wrap: break-word;'>Media</td><td style='text-align: center; word-wrap: break-word;'>Social</td><td style='text-align: center; word-wrap: break-word;'>Multi-Apps</td><td style='text-align: center; word-wrap: break-word;'>Overall</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>GeminiProVision</td><td style='text-align: center; word-wrap: break-word;'>3.3</td><td style='text-align: center; word-wrap: break-word;'>4.0</td><td style='text-align: center; word-wrap: break-word;'>2.3</td><td style='text-align: center; word-wrap: break-word;'>4.3</td><td style='text-align: center; word-wrap: break-word;'>1.5</td><td style='text-align: center; word-wrap: break-word;'>3.2</td><td style='text-align: center; word-wrap: break-word;'>4.9</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>CogAgent</td><td style='text-align: center; word-wrap: break-word;'>11.8</td><td style='text-align: center; word-wrap: break-word;'>15.7</td><td style='text-align: center; word-wrap: break-word;'>10.7</td><td style='text-align: center; word-wrap: break-word;'>9.2</td><td style='text-align: center; word-wrap: break-word;'>11.7</td><td style='text-align: center; word-wrap: break-word;'>13.1</td><td style='text-align: center; word-wrap: break-word;'>10.7</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>GPT-4V</td><td style='text-align: center; word-wrap: break-word;'>18.8</td><td style='text-align: center; word-wrap: break-word;'>23.5</td><td style='text-align: center; word-wrap: break-word;'>20.2</td><td style='text-align: center; word-wrap: break-word;'>19.2</td><td style='text-align: center; word-wrap: break-word;'>16.9</td><td style='text-align: center; word-wrap: break-word;'>13.8</td><td style='text-align: center; word-wrap: break-word;'>19.0</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>GPT-4o</td><td style='text-align: center; word-wrap: break-word;'>20.4</td><td style='text-align: center; word-wrap: break-word;'>20.8</td><td style='text-align: center; word-wrap: break-word;'>16.3</td><td style='text-align: center; word-wrap: break-word;'>31.9</td><td style='text-align: center; word-wrap: break-word;'>15.4</td><td style='text-align: center; word-wrap: break-word;'>21.3</td><td style='text-align: center; word-wrap: break-word;'>16.7</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Qwen2.5-VL-72B</td><td style='text-align: center; word-wrap: break-word;'>46.6</td><td style='text-align: center; word-wrap: break-word;'>60.0</td><td style='text-align: center; word-wrap: break-word;'>44.0</td><td style='text-align: center; word-wrap: break-word;'>32.4</td><td style='text-align: center; word-wrap: break-word;'>46.1</td><td style='text-align: center; word-wrap: break-word;'>54.6</td><td style='text-align: center; word-wrap: break-word;'>42.4</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>PG-Agent</td><td style='text-align: center; word-wrap: break-word;'>48.6</td><td style='text-align: center; word-wrap: break-word;'>61.5</td><td style='text-align: center; word-wrap: break-word;'>47.2</td><td style='text-align: center; word-wrap: break-word;'>35.5</td><td style='text-align: center; word-wrap: break-word;'>46.9</td><td style='text-align: center; word-wrap: break-word;'>52.6</td><td style='text-align: center; word-wrap: break-word;'>47.7</td></tr></table>

ability of the agent. Therefore, the Service and Info scenarios that only appear at cross-domain test do not have corresponding page graphs, so we will use the page graphs of other scenarios during evaluation.

• GUI Odyssey: GUI Odyssey dataset is designed to evaluate the navigation ability of the agent in cross-app tasks. This dataset contains more than 7,000 episodes with an average of 15+ steps, including 6 different scenarios from 201 apps. Similarly, we sample part of training episodes of GUI Odyssey to build page graphs.

The specific statistics of episodes sampling of dataset Mind2Web and GUI Odyssey are listed in supplementary materials.

Model. In this paper, we adopt BGE-M3 [3] as our vectorization model and FAISS technique [9] for similarity retrieval. Besides, we utilize Qwen2.5-VL-72B [2] as our base MLLM considering its strong ability at understanding GUI screens.

Hyperparameters. According to the statistics of retrieved guidelines (GL), as shown in Figure.4, we set the maximum number of GL (Equation 12) to 20 for AITW dataset. Besides, we set 20 for GUI Odyssey and 10 for Mind2Web, whose distribution of GL in different scenarios can be seen in the supplementary materials. Besides, we set the maximum number of layers l for BFS to 3 and the number of retrieved nodes of page similarity search n to 4.

### 4.2 Main Result

AITW. We follow the setting of AITW to calculate the action matching score as the metric. As shown in Table 3, PG-Agent yields the best average performance compared to current API-based agents. Among the scenarios, the action accuracy in GoogleApps is the most prominent, which exceeds state-of-art result by 13.4%. The result demonstrates that graph RAG technique can help API-based agent improve the execution accuracy in scenarios where prior knowledge is available. For error cases in Single scenario, we find the length of these episodes is short and the step where the task is supposed to end has ambiguity, i.e., our agent tends to continue executing some actions to completely finish the task. We further analyze this situation in the Supplementary Materials.

<div style="text-align: center;"><div style="text-align: center;">Table 3: Comparison of PG-Agent with different methods on AITW dataset. The best and second-best results in each column are highlighted in bold font and underlined.</div> </div>




<table border=1 style='margin: auto; word-wrap: break-word;'><tr><td style='text-align: center; word-wrap: break-word;'>Method</td><td style='text-align: center; word-wrap: break-word;'>General</td><td style='text-align: center; word-wrap: break-word;'>Install</td><td style='text-align: center; word-wrap: break-word;'>G.Apps</td><td style='text-align: center; word-wrap: break-word;'>Single</td><td style='text-align: center; word-wrap: break-word;'>WebShop.</td><td style='text-align: center; word-wrap: break-word;'>Overall</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>ChatGPT-CoT</td><td style='text-align: center; word-wrap: break-word;'>5.9</td><td style='text-align: center; word-wrap: break-word;'>4.4</td><td style='text-align: center; word-wrap: break-word;'>10.5</td><td style='text-align: center; word-wrap: break-word;'>9.4</td><td style='text-align: center; word-wrap: break-word;'>8.4</td><td style='text-align: center; word-wrap: break-word;'>7.7</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>PaLM2-CoT</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>39.6</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>GPT-4V</td><td style='text-align: center; word-wrap: break-word;'>41.7</td><td style='text-align: center; word-wrap: break-word;'>42.6</td><td style='text-align: center; word-wrap: break-word;'>49.8</td><td style='text-align: center; word-wrap: break-word;'>72.8</td><td style='text-align: center; word-wrap: break-word;'>45.7</td><td style='text-align: center; word-wrap: break-word;'>50.5</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Qwen2.5-VL-72B</td><td style='text-align: center; word-wrap: break-word;'>35.9</td><td style='text-align: center; word-wrap: break-word;'>58.5</td><td style='text-align: center; word-wrap: break-word;'>58.8</td><td style='text-align: center; word-wrap: break-word;'>50.7</td><td style='text-align: center; word-wrap: break-word;'>36.6</td><td style='text-align: center; word-wrap: break-word;'>48.1</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>OmniParser</td><td style='text-align: center; word-wrap: break-word;'>48.3</td><td style='text-align: center; word-wrap: break-word;'>57.8</td><td style='text-align: center; word-wrap: break-word;'>51.6</td><td style='text-align: center; word-wrap: break-word;'>77.4</td><td style='text-align: center; word-wrap: break-word;'>52.9</td><td style='text-align: center; word-wrap: break-word;'>57.5</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>PG-Agent</td><td style='text-align: center; word-wrap: break-word;'>51.9</td><td style='text-align: center; word-wrap: break-word;'>62.4</td><td style='text-align: center; word-wrap: break-word;'>65.0</td><td style='text-align: center; word-wrap: break-word;'>64.7</td><td style='text-align: center; word-wrap: break-word;'>53.7</td><td style='text-align: center; word-wrap: break-word;'>59.5</td></tr></table>

Mind2Web. In Mind2Web dataset, we calculate element accuracy (Ele.Acc), operation f1 (Op.F1) and step success rate (Step SR) as the metrics. Results in Table 1 show that PG-Agent achieves the optimal performance in both Ele.Acc and Step.SR metric, and the second-best in Op.F1. Besides, the significant improvements can be observed in cross-domain split, where we lack for relevant prior knowledge in Service and Info scenarios. This indicates that the episodes from other scenarios also provide valuable reference, which proves the generality of constructed page graph.

GUI Odyssey. For GUI Odyssey, we adhere the original metric setting [22]. From the results in Table 2, we can observe PG-Agent produces the best results in most scenarios and surpasses other

<div style="text-align: center;"><div style="text-align: center;">Table 4: Ablation results on Mind2Web. The best and second-best results in each column are highlighted in bold font and underlined. GL, STP-Agent, D-Agent are the abbreviations of guidelines, sub-task planning agent and decision agent respectively.</div> </div>




<table border=1 style='margin: auto; word-wrap: break-word;'><tr><td rowspan="2">Method</td><td colspan="3">Cross-Task</td><td colspan="3">Cross-Website</td><td colspan="3">Cross-Domain</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Ele.Acc</td><td style='text-align: center; word-wrap: break-word;'>Op.F1</td><td style='text-align: center; word-wrap: break-word;'>Step SR</td><td style='text-align: center; word-wrap: break-word;'>Ele.Acc</td><td style='text-align: center; word-wrap: break-word;'>Op.F1</td><td style='text-align: center; word-wrap: break-word;'>Step SR</td><td style='text-align: center; word-wrap: break-word;'>Ele.Acc</td><td style='text-align: center; word-wrap: break-word;'>Op.F1</td><td style='text-align: center; word-wrap: break-word;'>Step SR</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>PG-Agent</td><td style='text-align: center; word-wrap: break-word;'>59.0</td><td style='text-align: center; word-wrap: break-word;'>84.7</td><td style='text-align: center; word-wrap: break-word;'>52.9</td><td style='text-align: center; word-wrap: break-word;'>57.3</td><td style='text-align: center; word-wrap: break-word;'>81.2</td><td style='text-align: center; word-wrap: break-word;'>48.7</td><td style='text-align: center; word-wrap: break-word;'>60.2</td><td style='text-align: center; word-wrap: break-word;'>84.5</td><td style='text-align: center; word-wrap: break-word;'>53.3</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>w/ GL in D-Agent</td><td style='text-align: center; word-wrap: break-word;'>58.1</td><td style='text-align: center; word-wrap: break-word;'>84.0</td><td style='text-align: center; word-wrap: break-word;'>50.7</td><td style='text-align: center; word-wrap: break-word;'>57.5</td><td style='text-align: center; word-wrap: break-word;'>82.0</td><td style='text-align: center; word-wrap: break-word;'>48.5</td><td style='text-align: center; word-wrap: break-word;'>59.9</td><td style='text-align: center; word-wrap: break-word;'>82.7</td><td style='text-align: center; word-wrap: break-word;'>51.5</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>w/ GL in STP-Agent</td><td style='text-align: center; word-wrap: break-word;'>59.4</td><td style='text-align: center; word-wrap: break-word;'>84.5</td><td style='text-align: center; word-wrap: break-word;'>52.4</td><td style='text-align: center; word-wrap: break-word;'>56.9</td><td style='text-align: center; word-wrap: break-word;'>83.1</td><td style='text-align: center; word-wrap: break-word;'>48.0</td><td style='text-align: center; word-wrap: break-word;'>59.5</td><td style='text-align: center; word-wrap: break-word;'>83.4</td><td style='text-align: center; word-wrap: break-word;'>52.1</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>w/o GL</td><td style='text-align: center; word-wrap: break-word;'>58.9</td><td style='text-align: center; word-wrap: break-word;'>82.8</td><td style='text-align: center; word-wrap: break-word;'>50.2</td><td style='text-align: center; word-wrap: break-word;'>57.6</td><td style='text-align: center; word-wrap: break-word;'>80.6</td><td style='text-align: center; word-wrap: break-word;'>47.6</td><td style='text-align: center; word-wrap: break-word;'>59.4</td><td style='text-align: center; word-wrap: break-word;'>81.3</td><td style='text-align: center; word-wrap: break-word;'>50.4</td></tr></table>

<div style="text-align: center;"><div style="text-align: center;">Table 5: Ablation results of guidelines for different actions on Mind2Web. The metric is Op.F1 value, where the best result is highlighted in bold, and w/o GL means removing the RAG pipeline.</div> </div>




<table border=1 style='margin: auto; word-wrap: break-word;'><tr><td rowspan="2">Method</td><td colspan="3">Cross-Task</td><td colspan="3">Cross-Website</td><td colspan="3">Cross-Domain</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>CLICK</td><td style='text-align: center; word-wrap: break-word;'>SELECT</td><td style='text-align: center; word-wrap: break-word;'>TYPE</td><td style='text-align: center; word-wrap: break-word;'>CLICK</td><td style='text-align: center; word-wrap: break-word;'>SELECT</td><td style='text-align: center; word-wrap: break-word;'>TYPE</td><td style='text-align: center; word-wrap: break-word;'>CLICK</td><td style='text-align: center; word-wrap: break-word;'>SELECT</td><td style='text-align: center; word-wrap: break-word;'>TYPE</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>PG-Agent</td><td style='text-align: center; word-wrap: break-word;'>90.9</td><td style='text-align: center; word-wrap: break-word;'>31.2</td><td style='text-align: center; word-wrap: break-word;'>49.5</td><td style='text-align: center; word-wrap: break-word;'>88.0</td><td style='text-align: center; word-wrap: break-word;'>47.3</td><td style='text-align: center; word-wrap: break-word;'>44.0</td><td style='text-align: center; word-wrap: break-word;'>88.8</td><td style='text-align: center; word-wrap: break-word;'>47.1</td><td style='text-align: center; word-wrap: break-word;'>56.3</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>w/o GL</td><td style='text-align: center; word-wrap: break-word;'>92.9</td><td style='text-align: center; word-wrap: break-word;'>29.8</td><td style='text-align: center; word-wrap: break-word;'>29.6</td><td style='text-align: center; word-wrap: break-word;'>91.0</td><td style='text-align: center; word-wrap: break-word;'>40.5</td><td style='text-align: center; word-wrap: break-word;'>26.0</td><td style='text-align: center; word-wrap: break-word;'>89.6</td><td style='text-align: center; word-wrap: break-word;'>40.6</td><td style='text-align: center; word-wrap: break-word;'>31.7</td></tr></table>

API-based agents, while only in Multi-Apps scenario it is suboptimal. This demonstrates that the guidelines retrieved from page graph cast some insights into unfamiliar scenario for the agent and actually improve the planning process and execution process during the navigation.

### 4.3 Ablation Study

In our PG-Agent, as shown in Figure 3, our graph RAG pipeline extracts relevant guidelines (GL) from the page graph (Section 3.1) and acts in the planning stage (Sub-Task Planning Agent) and decision stage (Decision Agent) respectively. In this section, we use the control variable method to analyze the impact of GL on PG-Agent. Specifically, we prompt the retrieved GL to different agents, and the results are shown in Table 6 and Table 4. It can be seen that on AITW[32], our PG-Agent achieves the best results, while removing GL (w/o GL) leads to a general decline in performance.

<div style="text-align: center;"><div style="text-align: center;">Table 6: Ablation results on AITW. The best and second-best results in each column are highlighted in bold font and underlined. GL, STP-Agent, D-Agent are the abbreviations of Guidelines, Sub-Task Planning Agent and Decision Agent respectively.</div> </div>




<table border=1 style='margin: auto; word-wrap: break-word;'><tr><td style='text-align: center; word-wrap: break-word;'>Method</td><td style='text-align: center; word-wrap: break-word;'>General</td><td style='text-align: center; word-wrap: break-word;'>Install</td><td style='text-align: center; word-wrap: break-word;'>G.Apps</td><td style='text-align: center; word-wrap: break-word;'>Single</td><td style='text-align: center; word-wrap: break-word;'>WebShop.</td><td style='text-align: center; word-wrap: break-word;'>Overall</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>PG-Agent</td><td style='text-align: center; word-wrap: break-word;'>51.9</td><td style='text-align: center; word-wrap: break-word;'>62.4</td><td style='text-align: center; word-wrap: break-word;'>65.0</td><td style='text-align: center; word-wrap: break-word;'>64.7</td><td style='text-align: center; word-wrap: break-word;'>53.7</td><td style='text-align: center; word-wrap: break-word;'>59.5</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>w/ GL in D-Agent</td><td style='text-align: center; word-wrap: break-word;'>50.8</td><td style='text-align: center; word-wrap: break-word;'>60.5</td><td style='text-align: center; word-wrap: break-word;'>63.8</td><td style='text-align: center; word-wrap: break-word;'>66.6</td><td style='text-align: center; word-wrap: break-word;'>53.4</td><td style='text-align: center; word-wrap: break-word;'>59.0</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>w/ GL in STP-Agent</td><td style='text-align: center; word-wrap: break-word;'>51.4</td><td style='text-align: center; word-wrap: break-word;'>59.5</td><td style='text-align: center; word-wrap: break-word;'>62.8</td><td style='text-align: center; word-wrap: break-word;'>66.1</td><td style='text-align: center; word-wrap: break-word;'>52.8</td><td style='text-align: center; word-wrap: break-word;'>58.5</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>w/o GL</td><td style='text-align: center; word-wrap: break-word;'>50.0</td><td style='text-align: center; word-wrap: break-word;'>59.8</td><td style='text-align: center; word-wrap: break-word;'>63.4</td><td style='text-align: center; word-wrap: break-word;'>65.4</td><td style='text-align: center; word-wrap: break-word;'>52.7</td><td style='text-align: center; word-wrap: break-word;'>58.3</td></tr></table>

Meanwhile, introducing GL to different agents also brings performance improvements. Furthermore, we find that the benefits of introducing GL in the Decision Agent (D-Agent) are greater than those in the Sub-Task Planning Agent (STP-Agent). The same results are also observable in Table 4, but in the web navigation tasks [8], agents have different preferences for GL under different tasks; for example, in the Cross-Task and Cross-Domain split, introducing GL to STP-Agent is better than D-Agent, but this result is reversed in the Cross-Website split. We attribute these results to differences in interaction logic across scenarios and varying navigation preferences of the base model for different devices.

To further validate GL's advantages, we conduct a fine-grained analysis of its impact on each decision step in the Mind2Web[8] dataset. As shown in Table 5, the introduction of GL greatly improves the Opt.F1 score of the 'SELECT' and 'TYPE' actions. Regarding the decrease in performance of the 'CLICK' action, we analyze the data and find that the reason is due to the inconsistency of the labels in the dataset itself; that is, the 'SELECT' action has two label definitions at the same time: 1) two consecutive 'CLICK' actions; 2) a single 'SELECT' action. Our PG-Agent tends to choose more reasonable 'SELECT' actions. However, this can lead to situations where it is judged as having failed, even when it executes the correct action. As a result, the Opt.F1 score for the 'SELECT' type decreases.

### 4.4 Page Graph Analysis

For publicly available datasets (e.g., AITW, Mind2Web and GUI Odyssey), there are abundant data of episodes for the construction of page graphs, but it actually takes substantial costs for data collection. Therefore, in the previous evaluation, we only sample a small subset of episodes to build the graph, demonstrating the practicality of our framework. In this section, we use the full episodes from the dataset to build the graph and compare the result with Section 4.2. As shown in Table 7, we find that PG-Agent utilizing full episodes only yields better performance in specific scenarios, where the random sampling version remains a competitive accuracy score. The results indicate that even if there are only limited episodes for reference, the page graph built from them can still provide effective guidance for PG-Agent.

<div style="text-align: center;"><div style="text-align: center;">Table 7: The impact of the page graph on PG-Agent constructed with different data of episodes, where random sampling follows the setting in Section 4.1, while full episodes means we utilize all episodes from the training set.</div> </div>




<table border=1 style='margin: auto; word-wrap: break-word;'><tr><td rowspan="2">Data Source</td><td colspan="5">AITW</td><td colspan="3">Mind2Web</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>General</td><td style='text-align: center; word-wrap: break-word;'>Install</td><td style='text-align: center; word-wrap: break-word;'>G.Apps</td><td style='text-align: center; word-wrap: break-word;'>Single</td><td style='text-align: center; word-wrap: break-word;'>WebShop.</td><td style='text-align: center; word-wrap: break-word;'>Cross-Task</td><td style='text-align: center; word-wrap: break-word;'>Cross-Website</td><td style='text-align: center; word-wrap: break-word;'>Cross-Domain</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Random Sampling</td><td style='text-align: center; word-wrap: break-word;'>51.9</td><td style='text-align: center; word-wrap: break-word;'>62.4</td><td style='text-align: center; word-wrap: break-word;'>65.0</td><td style='text-align: center; word-wrap: break-word;'>64.7</td><td style='text-align: center; word-wrap: break-word;'>53.7</td><td style='text-align: center; word-wrap: break-word;'>52.9</td><td style='text-align: center; word-wrap: break-word;'>48.7</td><td style='text-align: center; word-wrap: break-word;'>53.3</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Full Episodes</td><td style='text-align: center; word-wrap: break-word;'>50.5</td><td style='text-align: center; word-wrap: break-word;'>59.5</td><td style='text-align: center; word-wrap: break-word;'>63.2</td><td style='text-align: center; word-wrap: break-word;'>61.4</td><td style='text-align: center; word-wrap: break-word;'>54.6</td><td style='text-align: center; word-wrap: break-word;'>52.6</td><td style='text-align: center; word-wrap: break-word;'>50.0</td><td style='text-align: center; word-wrap: break-word;'>54.0</td></tr></table>

<div style="text-align: center;"><img src="https://pplines-online.bj.bcebos.com/deploy/official/paddleocr/pp-ocr-vl-15//c57a7e1b-93ab-47c9-bdfd-f4ef0b73c8bb/markdown_2/imgs/img_in_image_box_157_384_566_660.jpg?authorization=bce-auth-v1%2FALTAKDN8mY5KlNI7zaRpLmOqrw%2F2026-05-03T07%3A46%3A48Z%2F-1%2F%2Faa03d39067827fa73caf6fc2017b326cc81e2f4ae803da2e1080fa33717fbbfe" alt="Image" width="33%" /></div>


<div style="text-align: center;"><div style="text-align: center;">(a) Install</div> </div>


<div style="text-align: center;"><img src="https://pplines-online.bj.bcebos.com/deploy/official/paddleocr/pp-ocr-vl-15//c57a7e1b-93ab-47c9-bdfd-f4ef0b73c8bb/markdown_2/imgs/img_in_image_box_654_381_1061_661.jpg?authorization=bce-auth-v1%2FALTAKDN8mY5KlNI7zaRpLmOqrw%2F2026-05-03T07%3A46%3A48Z%2F-1%2F%2Fa1ec6d0a0386820eec57b666c9189e62f766020a2ce51a02b0a05dd27478f520" alt="Image" width="33%" /></div>


<div style="text-align: center;"><div style="text-align: center;">(b) WebShopping</div> </div>


<div style="text-align: center;"><div style="text-align: center;">Figure 5: Examples of page graph visualizations of scenarios in AITW dataset.</div> </div>


<div style="text-align: center;"><div style="text-align: center;">Table 8: Statistics of sampled episodes in different scenarios of dataset AITW and corresponding page graph.</div> </div>




<table border=1 style='margin: auto; word-wrap: break-word;'><tr><td style='text-align: center; word-wrap: break-word;'>Scenario</td><td style='text-align: center; word-wrap: break-word;'># Episodes</td><td style='text-align: center; word-wrap: break-word;'># Images</td><td style='text-align: center; word-wrap: break-word;'># Nodes</td><td style='text-align: center; word-wrap: break-word;'># Edges</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>General</td><td style='text-align: center; word-wrap: break-word;'>43</td><td style='text-align: center; word-wrap: break-word;'>341</td><td style='text-align: center; word-wrap: break-word;'>132</td><td style='text-align: center; word-wrap: break-word;'>168</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Install</td><td style='text-align: center; word-wrap: break-word;'>55</td><td style='text-align: center; word-wrap: break-word;'>538</td><td style='text-align: center; word-wrap: break-word;'>208</td><td style='text-align: center; word-wrap: break-word;'>286</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>G.Apps</td><td style='text-align: center; word-wrap: break-word;'>24</td><td style='text-align: center; word-wrap: break-word;'>198</td><td style='text-align: center; word-wrap: break-word;'>67</td><td style='text-align: center; word-wrap: break-word;'>93</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Single</td><td style='text-align: center; word-wrap: break-word;'>55</td><td style='text-align: center; word-wrap: break-word;'>194</td><td style='text-align: center; word-wrap: break-word;'>92</td><td style='text-align: center; word-wrap: break-word;'>70</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>WebShop.</td><td style='text-align: center; word-wrap: break-word;'>56</td><td style='text-align: center; word-wrap: break-word;'>712</td><td style='text-align: center; word-wrap: break-word;'>201</td><td style='text-align: center; word-wrap: break-word;'>323</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Total</td><td style='text-align: center; word-wrap: break-word;'>231</td><td style='text-align: center; word-wrap: break-word;'>1983</td><td style='text-align: center; word-wrap: break-word;'>700</td><td style='text-align: center; word-wrap: break-word;'>940</td></tr></table>

To analyze the page graph deeply, we also collect the statistics of sampled episodes and the page graph. From Table 8, we can find that the nodes in page graph is much less than the images of corresponding episodes, suggesting that there are lots of repeated pages in the same scenario. Meanwhile, the number of edges is also smaller than the number of actions (usually the same as number of images), which suggests that some consecutive in-page actions have been combined at one edge for the simplicity of the graph structure. More statistics on Mind2Web and GUI Odyssey datasets are listed in the Supplementary Materials.

Besides, we visualize some page graphs constructed by the episodes from different scenarios in AITW dataset. As shown in Figure 5, we can see that the in-degree or out-degree of some nodes in the graphs is greater than 1, indicating that some similar pages in the episodes share the same nodes. We also visualize some cases of

<div style="text-align: center;"><img src="https://pplines-online.bj.bcebos.com/deploy/official/paddleocr/pp-ocr-vl-15//c57a7e1b-93ab-47c9-bdfd-f4ef0b73c8bb/markdown_2/imgs/img_in_image_box_644_773_1109_998.jpg?authorization=bce-auth-v1%2FALTAKDN8mY5KlNI7zaRpLmOqrw%2F2026-05-03T07%3A46%3A48Z%2F-1%2F%2Fde882603322c397beaab2135735fd391ab51bd5c3327d9ee11dfe548d179b79b" alt="Image" width="37%" /></div>


<div style="text-align: center;"><div style="text-align: center;">Figure 6: Cases of original images sharing the same node.</div> </div>


these similar pages in Figure 6, demonstrating the effectiveness the dual-level similarity check.

## 5 Conclusion

In this paper, we design an automated pipeline to reconstruct the discrete chained episodes into the page graph, capturing the complex transition relationships between screen pages. To fully utilize this prior knowledge as the perceptions of GUI scenarios, we further propose a tailored multi-agent framework PG-Agent equipped with the RAG technology to retrieve perception guidelines from the graph to improve the planning and execution process. Extensive experiments on three benchmark datasets illustrate the effectiveness of PG-Agent powered by page graphs, even when the available episodes are limited.

## Acknowledgments

This work was supported by the National Natural Science Foundation of China (Grant No. 62372408, 62476245). This work was supported by Ant Group Research Fund.

## References

[1] Meta AI. 2024. Llama 3. https://github.com/meta-llama/llama3 Accessed: 2024-11-12.

[2] Shuai Bai, Keqin Chen, Xuejing Liu, Jialin Wang, Wenbin Ge, Sibo Song, Kai Dang, Peng Wang, Shijie Wang, Jun Tang, et al. 2025. Qwen2. 5-VL Technical Report. arXiv preprint arXiv:2502.13923 (2025).

[3] Jianlv Chen, Shitao Xiao, Peitian Zhang, Kun Luo, Defu Lian, and Zheng Liu. 2024. BGE M3-Embedding: Multi-Lingual, Multi-Functionality, Multi-Granularity Text Embeddings Through Self-Knowledge Distillation. arXiv:2402.03216 [cs.CL]

[4] Minghao Chen, V: Binbin Lin, and Xiaofei He. 2024. A LLM Agents via Inte-//arxiv.org/

[5] Tong Chen, Hongwei Wang, Sihao Chen, Wenhao Yu, Kaixin Ma, Xinran Zhao, and Hongming and Zhang. 2024. Dense X Retrieval: What Retrieval Granularity Should We Use?. In Proceedings of the 2024 Conference on Empirical Methods in Natural Language Processing. Association for Computational Linguistics, Miami, Florida, USA, 15159–15177. doi:10.18653/v1/2024.emnlp-main.845

[6] Zhe Chen, Weiyun Wang, Hao Tian, Shenglong Ye, Zhangwei Gao, Erfei Cui, Wenwen Tong, Kongzhi Hu, Jiapeng Luo, Zheng Ma, et al. 2024. How far are we to gpt-4v? closing the gap to commercial multimodal models with open-source suites. arXiv preprint arXiv:2404.16821 (2024).

[7] Kanzhi Cheng, Qiushi Sun, Yougang Chu, Fangzhi Xu, Li YanTao, Jianbing Zhang, and Zhiyong Wu. 2024. SeeClick: Harnessing GUI Grounding for Advanced Visual GUI Agents. In Proceedings of the 62nd Annual Meeting of the Association for Computational Linguistics (Volume 1: Long Papers). Association for Computational Linguistics, Bangkok, Thailand, 9313–9332. https://aclanthology.org/2024.aclong.505

[8] Xiang Deng, Yu Gu, Boyuan Zheng, Shijie Chen, Samuel Stevens, Boshi Wang, Huan Sun, and Yu Su. 2023. Mind2Web: Towards a Generalist Agent for the Web. In Advances in Neural Information Processing Systems 36: Annual Conference on Neural Information Processing Systems 2023, NeurIPS 2023, New Orleans, LA, USA, December 10 - 16, 2023.

[9] Matthijs Douze, Alexandr Guzhva, Chengqi Deng, Jeff Johnson, Gergely Szilvasy, Pierre-Emmanuel Mazaré, Maria Lomeli, Lucas Hosseini, and Hervé Jégou. 2024. The Faiss library. (2024). arXiv:2401.08281 [cs.LG]

[10] Darren Edge, Ha Trinh, Newman Cheng, Joshua Bradley, Alex Chao, Apurva Mody, Steven Truitt, Dasha Metropolitansky, Robert Osazuwa Ness, and Jonathan Larson. 2025. From Local to Global: A Graph RAG Approach to Query-Focused Summarization. arXiv:2404.16130 [cs.CL] https://arxiv.org/abs/2404.16130

[11] Yao Fu, Dong-Ki Kim, Jaekyeom Kim, Sungryull Sohn, Lajanugen Logeswaran, Kyunghoon Bae, and Honglak Lee. 2024. AutoGuide: Automated Generation and Selection of Context-Aware Guidelines for Large Language Model Agents. arXiv:2403.08978 [cs.CL] https://arxiv.org/abs/2403.08978

[12] Wenyi Hong, Weihan Wang, Qingsong Lv, Jiazheng Xu, Wenmeng Yu, Junhui Ji, Yan Wang, Zihan Wang, Yuxiao Dong, Ming Ding, et al. 2024. Cogagent: A visual language model for gui agents. In Proceedings of the IEEE/CVF Conference on Computer Vision and Pattern Recognition. 14281–14290.

[13] Ziniu Hu, Ahmet Iscen, Chen Sun, Zirui Wang, Kai-Wei Chang, Yizhou Sun, Cordelia Schmid, David A. Ross, and Alireza Fathi. 2023. REVEAL: Retrieval-Augmented Visual-Language Pre-Training with Multi-Source Multimodal Knowledge Memory. arXiv:2212.05221 [cs.CV] https://arxiv.org/abs/2212.05221

[14] Hanyu Lai, Xiao Liu, Iat Long Iong, Shuntian Yao, Yuxuan Chen, Pengbo Shen, Hao Yu, Hanchen Zhang, Xiaohan Zhang, Yuxiao Dong, and Jie Tang. 2024. AutoWebGLM: A Large Language Model-based Web Navigating Agent. In Proceedings of the 30th ACM SIGKDD Conference on Knowledge Discovery and Data Mining. 5295---5306.

[15] Patrick Lewis, Ethan Perez, Aleksandra Piktus, Fabio Petroni, Vladimir Karpukhin, Naman Goyal, Heinrich Küttler, Mike Lewis, Wen tau Yih, Tim Rocktäschel, Sebastian Riedel, and Douwe Kiela. 2021. Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks. arXiv:2005.11401 [cs.CL] https://arxiv.org/abs/2005.11401

[16] Yanda Li, Chi Zhang, Wanqi Yang, Bin Fu, Pei Cheng, Xin Chen, Ling Chen, and Yunchao Wei. 2024. AppAgent v2: Advanced Agent for Flexible Mobile Interactions. arXiv:2408.11824 [cs.HC] https://arxiv.org/abs/2408.11824

[17] Zijian Li, Qingyan Guo, Jiawei Shao, Lei Song, Jiang Bian, Jun Zhang, and Rui Wang. 2024. Graph Neural Network Enhanced Retrieval for Question Answering of LLMs. arXiv:2406.06572 [cs.CL] https://arxiv.org/abs/2406.06572

[18] Kevin Qinghong Lin, Linjie Li, Difei Gao, Zhengyuan Yang, Shiwei Wu, Zechen Bai, Weixian Lei, Lijuan Wang, and Mike Zheng Shou. 2024. Showui: One vision-language-action model for gui visual agent. arXiv preprint arXiv:2411.17465 (2024).

[19] Mario Linares-Vásquez, Kevin Moran, and Denys Poshyvanyk. 2017. Continuous, evolutionary and large-scale: A new perspective for automated mobile app testing. In 2017 IEEE International Conference on Software Maintenance and Evolution (ICSME). IEEE, 399–410.

[20] Nelson F. Liu, Kevin Lin, John Hewitt, Ashwin Paranjape, Michele Bevilacqua, Fabio Petroni, and Percy Liang. 2023. Lost in the Middle: How Language Models Use Long Contexts. arXiv:2307.03172 [cs.CL] https://arxiv.org/abs/2307.03172

[21] Xinwei Long, Jiali Zeng, Fandong Meng, Zhiyuan Ma, Kaiyan Zhang, Bowen Zhou, and Jie Zhou. 2024. Generative Multi-Modal Knowledge Retrieval with Large Language Models. arXiv:2401.08206 [cs.IR] https://arxiv.org/abs/2401.08206

[22] Quanfeng Lu, Wenqi Shao, Zitao Liu, Fanqing Meng, Boxuan Li, Botong Chen, Siyuan Huang, Kaipeng Zhang, Yu Qiao, and Ping Luo. 2024. GUI Odyssey: A Comprehensive Dataset for Cross-App GUI Navigation on Mobile Devices. arXiv:2406.08451 [cs.CV] https://arxiv.org/abs/2406.08451

[23] Yadong Lu, Jianwei Yang, Yelong Shen, and Ahmed Awadallah. 2024. OmniParser for Pure Vision Based GUI Agent. arXiv:2408.00203 [cs.CV] https://arxiv.org/abs/2408.00203

[24] Dehai Min, Nan Hu, Rihui Jin, Nuo Lin, Jiaoyan Chen, Yongrui Chen, Yu Li, Guilin Qi, Yun Li, Nijun Li, and Qianren Wang. 2024. Exploring the Impact of Table-to-Text Methods on Augmenting LLM-based Question Answering with Domain Hybrid Data. In Proceedings of the 2024 Conference of the North American Chapter of the Association for Computational Linguistics: Human Language Technologies (Volume 6: Industry Track). Association for Computational Linguistics, Mexico City, Mexico, 464–482. https://aclanthology.org/2024.naacl-industry.41/

[25] Dehai Min, Nan Hu, Rihui Jin, Nuo Lin, Jiaoyan Chen, Yongrui Chen, Yu Li, Guilin Qi, Yun Li, Nijun Li, and Qianren Wang. 2024. Exploring the Impact of Table-to-Text Methods on Augmenting LLM-based Question Answering with Domain Hybrid Data. arXiv:2402.12869 [cs.CL] https://arxiv.org/abs/2402.12869

[26] Sai Munikoti, Anurag Acharya, Sridevi Wagle, and Sameera Horawalavithana. 2023. ATLANTIC: Structure-Aware Retrieval-Augmented Language Model for Interdisciplinary Science. arXiv:2311.12289 [cs.CL] https://arxiv.org/abs/2311.12289

[27] Dang Nguyen, Jian Chen, Yu Wang, Gang Wu, Namyong Park, Zhengmian Hu, Hanjia Lyu, Junda Wu, Ryan Aponte, Yu Xia, Xintong Li, Jing Shi, Hongjie Chen, Viet Dac Lai, Zhouhang Xie, Sungchul Kim, Ruiyi Zhang, Tong Yu, Mehrab Tanjim, Nesreen K. Ahmed, Puneet Mathur, Seunghyun Yoon, Lina Yao, Branislav Kveton, Thien Huu Nguyen, Trung Bui, Tianyi Zhou, Ryan A. Rossi, and Franck Dernoncourt. 2024. GUI Agents: A Survey. arXiv:2412.13501 [cs.AI] https://arxiv.org/abs/2412.13501

[28] OpenAI. 2023. GPT-4V(ision) System Card. (1 2023). doi:10.26181/25479208.v1

[29] OpenAI. 2024. GPT-4 Technical Report. arXiv:2303.08774 [cs.CL] https://arxiv.org/abs/2303.08774

[30] Boci Peng, Yun Zhu, Yongchao Liu, Xiaohu Shi, Chuntao Hong, Yan Zhang, and Siliang Tang. 2024. Graph Retrieval-Augmented Generation: A Survey. arXiv:2408.08921 [cs.AI] https://arxiv.org/abs/2408.08921

[31] Yujia Qin, Yining Ye, Junjie Fang, Haoming Wang, Shihao Liang, Shizuo Tian, Junda Zhang, Jiahao Li, Yunxin Li, Shijue Huang, et al. 2025. UI-TARS: Pioneering Automated GUI Interaction with Native Agents. arXiv preprint arXiv:2501.12326 (2025).

[32] Christopher Rawles, Alice Li, Daniel Rodriguez, Oriana Riva, and Timothy P. Lillicrap. 2023. Android in the Wild: A Large-Scale Dataset for Android Device Control. CoRR abs/2307.10088 (2023). arXiv:2307.10088

[33] Zhengliang Shi, Shuo Zhang, Weiwei Sun, Shen Gao, Pengjie Ren, Zhumin Chen, and Zhaochun Ren. 2024. Generate-then-Ground in Retrieval-Augmented Generation for Multi-hop Question Answering. In Proceedings of the 62nd Annual Meeting of the Association for Computational Linguistics (Volume 1: Long Papers). Association for Computational Linguistics, Bangkok, Thailand, 7339–7353. doi:10.18653/v1/2024.acl-long.397

[34] Noah Shinn, Federico Cassano, Ashwin Gopinath, Karthik Narasimhan, and Shunyu Yao. 2023. Reflexion: Language agents with verbal reinforcement learning. Advances in Neural Information Processing Systems 36 (2023), 8634–8652.

[35] Junyang Wang, Haiyang Xu, Haitao Jia, Xi Zhang, Ming Yan, Weizhou Shen, Ji Zhang, Fei Huang, and Jitao Sang. 2025. Mobile-agent-v2: Mobile device operation assistant with effective navigation via multi-agent collaboration. Advances in Neural Information Processing Systems 37 (2025), 2686–2710.

[36] Lei Wang, Chen Ma, Xueyang Feng, Zeyu Zhang, Hao Yang, Jingsen Zhang, Zhiyuan Chen, Jiakai Tang, Xu Chen, Yankai Lin, Wayne Xin Zhao, Zhewei Wei, and Jirong Wen. 2024. A survey on large language model based autonomous agents. Frontiers of Computer Science 18, 6 (March 2024). doi:10.1007/s11704-024-40231-1

[37] Yu Wang, Nedim Lipka, Ryan A. Rossi, Alexa Siu, Ruiyi Zhang, and Tyler Derr. 2023. Knowledge Graph Prompting for Multi-Document Question Answering. arXiv:2308.11730 [cs.CL] https://arxiv.org/abs/2308.11730

[38] Zhenhailong Wang, Haiyang Xu, Junyang Wang, Xi Zhang, Ming Yan, Ji Zhang, Fei Huang, and Heng Ji. 2025. Mobile-Agent-E: Self-Evolving Mobile Assistant for Complex Tasks. arXiv:2501.11733 [cs.CL] https://arxiv.org/abs/2501.11733

[39] Zhiyong Wu, Chengcheng Han, Zichen Ding, Zhenmin Weng, Zhoumianze Liu, Shunyu Yao, Tao Yu, and Lingpeng Kong. 2024. Os-copilot: Towards generalist computer agents with self-improvement. arXiv preprint arXiv:2402.07456 (2024).

[40] Jianwei Yang, Hao Zhang, Feng Li, Xueyan Zou, Chunyuan Li, and Jianfeng Gao. 2023. Set-of-Mark Prompting Unleashes Extraordinary Visual Grounding in GPT-4V. arXiv:2310.11441 [cs.CV] https://arxiv.org/abs/2310.11441

[41] Shunyu Yao, Jeffrey Zhao, Dian Yu, Nan Du, Izhak Shafran, Karthik Narasimhan, and Yuan Cao. 2023. React: Synergizing reasoning and acting in language models. In International Conference on Learning Representations (ICLR).

[42] Yuan Yao, Tianyu Yu, Ao Zhang, Chongyi Wang, Junbo Cui, Hongji Zhu, Tianchi Cai, Haoyu Li, Weilin Zhao, Zhihui He, et al. 2024. MiniCPM-V: A GPT-4V Level MLLM on Your Phone. arXiv preprint arXiv:2408.01800 (2024).

[43] Antonio Jimeno Yepes, Yao You, Jan Milczek, Sebastian Laverde, and Renyu Li. 2024. Financial Report Chunking for Effective Retrieval Augmented Generation. arXiv:2402.05131 [cs.CL] https://arxiv.org/abs/2402.05131

[44] Chi Zhang, Zhao Yang, Jiaxuan Liu, Yucheng Han, Xin Chen, Zebiao Huang, Bin Fu, and Gang Yu. 2023. Appagent: Multimodal agents as smartphone users. arXiv preprint arXiv:2312.13771 (2023).

[45] Andrew Zhao, Daniel Huang, Quentin Xu, Matthieu Lin, Yong-Jin Liu, and Gao Huang. 2024. Expel: Llm agents are experiential learners. In Proceedings of the AAAI Conference on Artificial Intelligence, Vol. 38. 19632–19642.

[46] Siyun Zhao, Yuqing Yang, Zilong Wang, Zhiyuan He, Luna K. Qiu, and Lili Qiu. 2024. Retrieval Augmented Generation (RAG) and Beyond: A Comprehensive Survey on How to Make your LLMs use External Data More Wisely. arXiv:2409.14924 [cs.CL] https://arxiv.org/abs/2409.14924

### A Structural details of page graph.

In this section, we show the details of the page graphs of Mind2Web (Table 9) and GUI Odyssey (Table 10) generated using the page graph construction pipeline. "# Episodes" represents the number of episodes we sampled from the original data. "# Images" is the number of screenshots in the sampled data. "# Nodes" and "# Edges" denote the number of nodes and edges in the page graph after pipeline processing, i.e., page jump determination, node similarity check and page graph update (Algorithm 1).

<div style="text-align: center;"><div style="text-align: center;">Table 9: Statistics of sampled episodes in different scenarios of dataset Mind2Web and corresponding page graph.</div> </div>




<table border=1 style='margin: auto; word-wrap: break-word;'><tr><td style='text-align: center; word-wrap: break-word;'>Scenario</td><td style='text-align: center; word-wrap: break-word;'># Episodes</td><td style='text-align: center; word-wrap: break-word;'># Images</td><td style='text-align: center; word-wrap: break-word;'># Nodes</td><td style='text-align: center; word-wrap: break-word;'># Edges</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Entertainment</td><td style='text-align: center; word-wrap: break-word;'>63</td><td style='text-align: center; word-wrap: break-word;'>342</td><td style='text-align: center; word-wrap: break-word;'>113</td><td style='text-align: center; word-wrap: break-word;'>95</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Travel</td><td style='text-align: center; word-wrap: break-word;'>122</td><td style='text-align: center; word-wrap: break-word;'>1001</td><td style='text-align: center; word-wrap: break-word;'>172</td><td style='text-align: center; word-wrap: break-word;'>159</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Shopping</td><td style='text-align: center; word-wrap: break-word;'>67</td><td style='text-align: center; word-wrap: break-word;'>484</td><td style='text-align: center; word-wrap: break-word;'>131</td><td style='text-align: center; word-wrap: break-word;'>111</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Total</td><td style='text-align: center; word-wrap: break-word;'>252</td><td style='text-align: center; word-wrap: break-word;'>1827</td><td style='text-align: center; word-wrap: break-word;'>416</td><td style='text-align: center; word-wrap: break-word;'>365</td></tr></table>

<div style="text-align: center;"><div style="text-align: center;">Table 10: Statistics of sampled episodes in different scenarios of dataset GUI-Odyssey and corresponding page graph.</div> </div>




<table border=1 style='margin: auto; word-wrap: break-word;'><tr><td style='text-align: center; word-wrap: break-word;'>Scenario</td><td style='text-align: center; word-wrap: break-word;'># Episodes</td><td style='text-align: center; word-wrap: break-word;'># Images</td><td style='text-align: center; word-wrap: break-word;'># Nodes</td><td style='text-align: center; word-wrap: break-word;'># Edges</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Tool</td><td style='text-align: center; word-wrap: break-word;'>25</td><td style='text-align: center; word-wrap: break-word;'>311</td><td style='text-align: center; word-wrap: break-word;'>119</td><td style='text-align: center; word-wrap: break-word;'>157</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Information</td><td style='text-align: center; word-wrap: break-word;'>17</td><td style='text-align: center; word-wrap: break-word;'>314</td><td style='text-align: center; word-wrap: break-word;'>96</td><td style='text-align: center; word-wrap: break-word;'>133</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Shopping</td><td style='text-align: center; word-wrap: break-word;'>8</td><td style='text-align: center; word-wrap: break-word;'>144</td><td style='text-align: center; word-wrap: break-word;'>58</td><td style='text-align: center; word-wrap: break-word;'>66</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Media</td><td style='text-align: center; word-wrap: break-word;'>16</td><td style='text-align: center; word-wrap: break-word;'>166</td><td style='text-align: center; word-wrap: break-word;'>60</td><td style='text-align: center; word-wrap: break-word;'>85</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Social</td><td style='text-align: center; word-wrap: break-word;'>15</td><td style='text-align: center; word-wrap: break-word;'>210</td><td style='text-align: center; word-wrap: break-word;'>72</td><td style='text-align: center; word-wrap: break-word;'>100</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Multi-Apps</td><td style='text-align: center; word-wrap: break-word;'>35</td><td style='text-align: center; word-wrap: break-word;'>736</td><td style='text-align: center; word-wrap: break-word;'>210</td><td style='text-align: center; word-wrap: break-word;'>388</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Total</td><td style='text-align: center; word-wrap: break-word;'>116</td><td style='text-align: center; word-wrap: break-word;'>1881</td><td style='text-align: center; word-wrap: break-word;'>615</td><td style='text-align: center; word-wrap: break-word;'>929</td></tr></table>

### B Guidance statistics on each benchmark.

In our PG-Agent, guidelines (GL) represent GUI knowledge retrieved from the prior knowledge base, i.e., page graph (Section 3.1), and are used to enhance the agent's decision making in GUI navigation flow. Here we exhibit the more GL distribution in Figure 7 (Mind2Web) and Figure 8 (GUI Odyssey) show the other two benchmarks details of retrieved GLs. It can be seen that for different datasets, our RAG strategy can effectively retrieve enough GLs to assist the agent's action decision.

<div style="text-align: center;"><img src="https://pplines-online.bj.bcebos.com/deploy/official/paddleocr/pp-ocr-vl-15//c57a7e1b-93ab-47c9-bdfd-f4ef0b73c8bb/markdown_4/imgs/img_in_chart_box_656_403_1091_771.jpg?authorization=bce-auth-v1%2FALTAKDN8mY5KlNI7zaRpLmOqrw%2F2026-05-03T07%3A46%3A50Z%2F-1%2F%2Faef455a24a32fa8a25f38c773f71a8bdc4e499917ea9be9f7474538e986f9aea" alt="Image" width="35%" /></div>


<div style="text-align: center;"><div style="text-align: center;">Figure 7: The data distribution of guidelines in Mind2Web dataset.</div> </div>


<div style="text-align: center;"><img src="https://pplines-online.bj.bcebos.com/deploy/official/paddleocr/pp-ocr-vl-15//c57a7e1b-93ab-47c9-bdfd-f4ef0b73c8bb/markdown_4/imgs/img_in_chart_box_658_898_1089_1314.jpg?authorization=bce-auth-v1%2FALTAKDN8mY5KlNI7zaRpLmOqrw%2F2026-05-03T07%3A46%3A50Z%2F-1%2F%2F847d8d458e63fae5ccf567d3a699be766a0109d6b539415e3687354d40eb9c5c" alt="Image" width="35%" /></div>


<div style="text-align: center;"><div style="text-align: center;">Figure 8: The data distribution of guidelines in GUI Odyssey dataset.</div> </div>


<div style="text-align: center;"><img src="https://pplines-online.bj.bcebos.com/deploy/official/paddleocr/pp-ocr-vl-15//9d0eaad0-3582-478d-81b1-e7708b173691/markdown_0/imgs/img_in_image_box_97_180_1123_1087.jpg?authorization=bce-auth-v1%2FALTAKDN8mY5KlNI7zaRpLmOqrw%2F2026-05-03T07%3A47%3A18Z%2F-1%2F%2Fc4ea16bae0b7a421fea8ffa85cca548b260807a8944ac880d7e8413fbe6aa6c3" alt="Image" width="83%" /></div>


<div style="text-align: center;"><div style="text-align: center;">Figure 9: Navigation process of PG-Agent.</div> </div>


### C Case Study

In this section, we select a part of cases for detailed analysis. When facing the Click operation, we use red rectangle to mark ground-truth, and green rectangle to mark the location where PG-Agent clicks.

As shown in Figure 9, our PG-Agent can successfully complete tasks with long steps. At the same time, we figure out that the inconsistency between ground-truth and the model's judgment on when the task should be completed led to some failed cases. In Figure 10(a), the task is to search Amazon. After getting the search results of Amazon, PG-Agent believes that it is necessary to click Amazon's website to complete the whole task. A similar situation occurs in Figure 10(b). When secure checkout is performed, the pop-up sign in interface naturally means that the task is not over yet, further sign in is required to complete. This is exactly what PG-Agent thinks, which is inconsistent while the answer determines the task is already completed.

<div style="text-align: center;"><div style="text-align: center;">Task: Search amazon</div> </div>


<div style="text-align: center;"><img src="https://pplines-online.bj.bcebos.com/deploy/official/paddleocr/pp-ocr-vl-15//9d0eaad0-3582-478d-81b1-e7708b173691/markdown_1/imgs/img_in_image_box_256_194_922_733.jpg?authorization=bce-auth-v1%2FALTAKDN8mY5KlNI7zaRpLmOqrw%2F2026-05-03T07%3A47%3A19Z%2F-1%2F%2F556513bd646456940f697bc7ef9dffc9890b8bb38d226aeb6b851d3be3268a3e" alt="Image" width="54%" /></div>


Task: Go to cart section and secure checkout

<div style="text-align: center;"><img src="https://pplines-online.bj.bcebos.com/deploy/official/paddleocr/pp-ocr-vl-15//9d0eaad0-3582-478d-81b1-e7708b173691/markdown_1/imgs/img_in_image_box_111_802_1064_1248.jpg?authorization=bce-auth-v1%2FALTAKDN8mY5KlNI7zaRpLmOqrw%2F2026-05-03T07%3A47%3A20Z%2F-1%2F%2Fa55d5cbe65a6fbfd15b30b26962886b8868360159afa8abcd2fe981702f3a40b" alt="Image" width="77%" /></div>


<div style="text-align: center;"><div style="text-align: center;">Figure 10: Cases determined as failure steps in "Single" scenario.</div> </div>


Algorithm 2 The workflow of PG-Agent.

Input: user's goal  $ T_g $; current screen state  $ I_t $; Maximum length of episode H; page graph G; Observation Agent  $ O_{agent} $; Global Planning Agent  $ \mathcal{P}_{agent}^G $; Sub-Task Planning Agent  $ \mathcal{P}_{agent}^S $; Decision Agent  $ \mathcal{D}_{agent} $.

Output: Action decision  $ R_d $ based on current screen state  $ I_t $.

1: // Guidelines Retrieval

2:  $ G_{I_t} \leftarrow RAG(I_t, \mathcal{G}) $

3: // Task Decomposition

4:  $ \mathcal{R}_g \leftarrow \mathcal{P}_{agent}^G(I_t, T_g) $

5:  $ t \leftarrow 0 $

6:  $ \tau \leftarrow [] $

7: while  $ t < H $ and  $ \mathcal{R}_d \neq $ "COMPLETE" do

8: // Observation Generation

9:  $ \mathcal{R}_o \leftarrow O_{agent}(I_t, T_g, \tau < t) $

10: // Candidate Action Generation

11:  $ \mathcal{R}_s \leftarrow \mathcal{P}_{agent}^S(I_t, \mathcal{R}_o, \mathcal{R}_g, G_{I_t}, \tau < t) $

12: // Final Decision

13:  $ \mathcal{R}_d \leftarrow \mathcal{D}_{agent}(I_t, \mathcal{R}_o, \mathcal{R}_s, G_{I_t}, \tau < t) $

14:  $ \tau \leftarrow \tau \cup \mathcal{R}_d $

15:  $ t \leftarrow t + 1 $

16: end while

### D Pseudocode of page graph construction and multi-agent workflow.

In this part, we present the details of two core modules in the form of pseudocode, (i) the generation pipeline of the page graph (Algorithm 1) and (ii) the workflow of the Agent (Algorithm 2). Through Algorithm 1, we can automatically transform discrete chain-like episodes into high-quality page graph as GUI prior knowledge base. Then, with the empowerment of page graph, the multi-agent system as Algorithm 1 can effectively complete the GUI navigation task.

Algorithm 1 The pipeline of page graph construction.

Input: Actions A;Images I;Image Locations L;Task T.

Output: Page graph G.

1:  $ N_{before} \leftarrow \emptyset $

2:  $ Q_{action} \leftarrow [] $

3:  $ \mathcal{G} \leftarrow [] $

4: for  $ i \in 1, 2, ... | E | do $

5: // Page Jump Determination

6: if i > 1 then

7:  $ S_{action}^{(i-1)} \leftarrow \{I^{(i-1)}, A^{(i-1)}\} $

8:  $ Q_{action} \leftarrow Q_{action} \cup \{S_{action}^{(i-1)}\} $

9:  $ Y_{jump}^{(i)} \leftarrow \{I^{(i-1)}, I^{(i)}, S_{action}^{(i-1)}\} $

10: if  $ Y_{jump}^{(i)} = 'No' $ then

11: Continue

12: end if

13: end if

14: // Node Similarity Check

15:  $ S_{page}^{(i)} \leftarrow I^{(i)} $

16:  $ S_{node}^{(i)} \leftarrow \{S_{page}^{(i)}, G\} $

17: id  $ \leftarrow \{I^{(i)}, S_{node}^{(i)}\} $

18:  $ I_{id}, N_{id} \leftarrow \{id, L, G\} $

19:  $ Y_{dissimilar}^{(i)} \leftarrow \{I^{(i)}, I_{id}\} $

20: // Page Graph Update

21: if  $ Y_{dissimilar}^{(i)} = 'Yes' $ then

22:  $ N_{new} \leftarrow \{I^{(i)}, L^{(i)}\} $

23: else

24:  $ N_{new} \leftarrow N_{id} $

25: end if

26: if i > 1 then

27:  $ E_{new} \leftarrow \{Q_{action}, T\} $

28:  $ \mathcal{G} = \mathcal{G} \cup (N_{before}, E_{new}, N_{new}) $

29: end if

30:  $ N_{before} \leftarrow N_{new} $

31: end for

32: return  $ \mathcal{G} $

