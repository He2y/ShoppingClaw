# WEBNAVIGATOR: GLOBAL WEB NAVIGATION VIA INTERACTION GRAPH RETRIEVAL

Xuanwang Zhang $ ^{1,2,3,*} $ Yuteng Han $ ^{1,2,*} $ Jinnan Qi $ ^{1,2} $ Mulong Xie $ ^{3} $

Zhen Wu $ ^{1,2,\dagger} $ Xinyu Dai $ ^{1,2} $

 $ ^{1} $National Key Laboratory for Novel Software Technology, Nanjing University, China  

 $ ^{2} $School of Artificial Intelligence, Nanjing University, China  

 $ ^{3} $Fellou AI zhangxuanwang@smail.nju.edu.cn, wuz@nju.edu.cn

https://fate-ubw.github.io/webNavigator_homepage/

https://github.com/fate-ubw/webNavigator

## ABSTRACT

Despite significant advances in autonomous web navigation, current methods remain far from human-level performance in complex web environments. We argue that this limitation stems from Topological Blindness, where agents are forced to explore via trial-and-error without access to the global topological structure of the environment. To overcome this limitation, we introduce WebNavigator, which reframes web navigation from probabilistic exploration into deterministic retrieval and pathfinding. WebNavigator constructs Interaction Graphs via zero-token cost heuristic exploration offline and implements a Retrieve-Reason-Teleport workflow for global navigation online. WebNavigator achieves state-of-the-art performance on WebArena and OnlineMind2Web. On WebArena multi-site tasks, WebNavigator achieves a 72.9% success rate, more than doubling the performance of enterprise-level agents. This work reveals that Topological Blindness, rather than model reasoning capabilities alone, is an underestimated bottleneck in autonomous web navigation.

## 1 INTRODUCTION

Despite achieving superhuman proficiency in automated code generation and complex mathematical theorem proving (Wang et al., 2025a; Hubert et al., 2025), autonomous agents continue to struggle with human-level performance in dynamic web navigation, particularly in complex cross-site scenarios (Zhou et al., 2024b; He et al., 2024; Drouin et al., 2024). Current state-of-the-art agents predominantly adhere to a reactive paradigm (Yao et al., 2023), grounding their decision-making on historical interactions, current observations, and internal priors of Large Language Models (LLMs). This reliance on local cues often leads to catastrophic failures in long-horizon tasks. The academic community often attributes these failures to the inherent limitations of LLMs in multi-step planning (Liu et al., 2024). However, we argue that this limitation stems from what we term Topological Blindness rather than simply insufficient model reasoning. Specifically, this refers to a condition in which agents can only plan with limited environmental information (historical interactions, current observations, and brittle prior knowledge derived from training data), while remaining blind to the global topological structure of websites.

This information deficit traps agents in a paradigm of Reactive Exploration, manifesting in: (1) unreliable planning due to truncated global awareness, (2) prohibitive computational costs from trial-and-error discovery, and (3) premature task termination. To mitigate this limitation, existing literature has explored two strategies, both of which face fundamental limitations: (1) Paradigm 1: Online Exploratory Search. Approaches utilizing search-based methods such as best-first search

algorithms and Monte Carlo Tree Search attempt to expand the agent's observation scope via lookahead planning and backtracking. (Koh et al., 2025; Yu et al., 2025; He et al., 2025). However, these methods are environment-agnostic. The knowledge acquired during inference is transient and disposable, requiring the agent to "reinvent the wheel" for every new task, resulting in significant latency and Token overhead (Ouyang et al., 2025; Wang et al., 2025b; Prabhu et al., 2025). (2) Paradigm 2: Learned Internal Planning and World Models. Some methods attempt to elicit environmental knowledge from models' internal knowledge (Sodhi et al., 2023; Erdogan et al., 2025; Yang et al., 2025) or learn latent transition rules to simulate environments (Gu et al., 2025; Chae et al., 2025). Yet, these suffer from a generalization-fidelity trade-off: parametric priors are often too sparse to handle site-specific nuances, while world models struggle with cross-site generalization, leading to compounding errors in simulation. We provide a comprehensive analysis of these approaches in Appendix A.

We posit that agents, much like expert human users, require a persistent “mental map” of the environment to achieve optimality. In structured web environments with finite (though large) observation spaces, we propose that navigation should be reframed from a probabilistic reasoning challenge into a deterministic retrieval and planning problem. We introduce WebNavigator, a two-phase framework comprising Offline Interaction Graph Construction and Online Retrieval-Augmented Navigation. Offline Interaction Graph Construction: Before task execution, WebNavigator employs a heuristic engine to systematically formalize the website’s topological structure into a directed Interaction Graph G. While traditional crawlers merely parse static hyperlinks (Wu et al., 2025), our engine interacts with dynamic elements to capture comprehensive representations, including DOM trees, accessibility trees, and screenshots. Crucially, this engine builds the Interaction Graph with zero-token cost, requiring no LLM involvement and only a homepage URL as input. After that, all nodes are embedded and indexed into a vector database (Günther et al., 2025), transforming the Interaction Graph into a retrievable knowledge base. Online Retrieval-Augmented Navigation: During inference, we introduce the Global-View Navigator (Fig. 1), which bridges the agent’s intent with the environment’s deterministic structure. This component encapsulates multimodal retrieval and graph traversal, enabling a Retrieve-Reason-Teleport workflow: (1) Retrieve. The agent issues a navigation query, and the Navigator retrieves the top-k relevant observations from the pre-constructed knowledge base. (2) Reason. A multimodal selector identifies the optimal observation. (3) Teleport. A pathfinding algorithm computes the shortest trajectory to teleport the agent to the target observation at zero-token cost. WebNavigator shifts agents from blind trial-and-error to global planning with only six actions. Notably, the Global-View Navigator serves as a modular component that can be integrated into various existing frameworks.

We evaluate WebNavigator on WebArena and Online-Mind2Web (Zhou et al., 2024b; Xue et al., 2025). WebNavigator achieves state-of-the-art performance in both WebArena and Online-Mind2Web. Our results demonstrate that WebNavigator achieves a 50.0% success rate on the most challenging multi-site tasks in WebArena, doubling the previous SOTA under identical configurations. Remarkably, leveraging Gemini-2.5-Pro, WebNavigator establishes a new performance ceiling of 72.9% on multi-site tasks, more than doubling the enterprise-level agent CUGA (Marreed et al., 2025). Furthermore, our experiments across 136 real-world websites in online-Mind2Web confirm the robust generalization of our paradigm. Collectively, these results identify Topological Blindness as a fundamental and overlooked bottleneck that constrains the potential of autonomous web agents.

## 2 PROBLEM FORMULATION

We formalize the web navigation task as a Partially Observable Markov Decision Process (POMDP), defined by the tuple  $ \langle S, \mathcal{A}, \mathcal{O}, \mathcal{T}, \mathcal{R} \rangle $ (Zhou et al., 2024b; Yu et al., 2025), where  $ S $ denotes the state space,  $ \mathcal{A} $ the action space,  $ \mathcal{O} $ the observation space,  $ T $ the transition function, and  $ \mathcal{R} $ the reward function. An agent receives an intent  $ i $ and an initial observation  $ o_0 $. At each time step  $ t $, the agent generates an action  $ a_t \in \mathcal{A} $ based on the current state  $ s_t $, where  $ s_t $ typically encodes the intent  $ i $, current observation  $ o_t \in \mathcal{O} $, and interaction history  $ h_t = (o_0, a_0, \ldots, a_{t-1}) $:

 $$ a_{t}\sim\pi_{\theta}(a_{t}\mid i,o_{t},h_{t}) $$ 

where  $ \theta $ represents the internal knowledge encoded in LLM parameters. After executing action  $ a_t $, the environment transitions to a new underlying state  $ s_{t+1} \in \mathcal{S} $ according to the transition function

<div style="text-align: center;"><div style="text-align: center;">Phase I: Offline Interaction Graph Construction</div> </div>


<div style="text-align: center;"><img src="https://pplines-online.bj.bcebos.com/deploy/official/paddleocr/pp-ocr-vl-15//72eacb6f-6dd2-4aa9-b885-74752f501df2/markdown_2/imgs/img_in_image_box_216_183_618_325.jpg?authorization=bce-auth-v1%2FALTAKDN8mY5KlNI7zaRpLmOqrw%2F2026-05-03T07%3A47%3A14Z%2F-1%2F%2F6eb782e71efef10b5f7655d87f9653a47a3d8cf79e074560fb0e6752359dcc0e" alt="Image" width="32%" /></div>


<div style="text-align: center;"><img src="https://pplines-online.bj.bcebos.com/deploy/official/paddleocr/pp-ocr-vl-15//72eacb6f-6dd2-4aa9-b885-74752f501df2/markdown_2/imgs/img_in_image_box_628_183_1006_325.jpg?authorization=bce-auth-v1%2FALTAKDN8mY5KlNI7zaRpLmOqrw%2F2026-05-03T07%3A47%3A14Z%2F-1%2F%2Fb72d18eb13bc13d47b9130767d79ea0cd6093f8ca2dd555dd5344fe27524417b" alt="Image" width="30%" /></div>


<div style="text-align: center;"><div style="text-align: center;">Phase II: Online Retrieval-Augmented Navigation</div> </div>


<div style="text-align: center;"><img src="https://pplines-online.bj.bcebos.com/deploy/official/paddleocr/pp-ocr-vl-15//72eacb6f-6dd2-4aa9-b885-74752f501df2/markdown_2/imgs/img_in_image_box_218_340_1004_664.jpg?authorization=bce-auth-v1%2FALTAKDN8mY5KlNI7zaRpLmOqrw%2F2026-05-03T07%3A47%3A14Z%2F-1%2F%2F6fc113608da970e7be08aae529bdc2f36acef8741d57a636603a80c522128732" alt="Image" width="64%" /></div>


<div style="text-align: center;"><div style="text-align: center;">Figure 1: Overview of WebNavigator. WebNavigator resolves Topological Blindness via a two-phase paradigm. (1) Offline Interaction Graph Construction. A heuristic auto-exploration engine discovers dynamic page observations at zero-token cost and indexes all observations into a vector database. (2) Online Retrieval-Augmented Navigation. The Global-View Navigator implements a three-stage workflow: Retrieve top-k candidates from the Interaction Graph via multimodal retrieval; Reason to identify the optimal target page; and Teleport by computing and executing the shortest path within the Interaction Graph, achieving globally optimal navigation.</div> </div>


 $ \mathcal{T}(s_t, a_t) $ and returns a subsequent observation  $ o_{t+1} $ to the agent. This process continues until the agent issues a termination command or exceeds the step limit, receiving a reward  $ r \in \{0, 1\} $ based on the functional correctness of task completion (Zhou et al., 2024b).

We define Topological Blindness as the condition where  $ \pi_{\theta} $ relies solely on the local information subset  $ \{o_t, h_t, \theta\} $ without access to the complete observation space  $ \mathcal{O} $ and transition function  $ \mathcal{T} $. This structural blindness forces agents into inefficient trial-and-error exploration. We argue that augmenting the policy with environmental knowledge resolves this limitation:

 $$ a_{t}\sim\pi_{\theta}(a_{t}\mid i,o_{t},h_{t},\mathcal{O},\mathcal{T}) $$ 

With complete environmental information, agents can in principle achieve globally optimal planning within model capacity bounds. However, providing complete  $ \mathcal{O} $ and  $ \mathcal{T} $ to agents remains impractical in real-world scenarios. This raises our central question: Can we construct a compact environmental representation that approximates  $ (\mathcal{O}, \mathcal{T}) $ to enable global planning in web navigation?

## 3 WEBNAVIGATOR

We decompose web navigation into the exploration and local execution stages (Erdogan et al., 2025). The exploration stage involves navigating across web pages to locate task-relevant observations, such as exploring from the homepage to find the product editing page. The local execution stage involves performing specific interactions within identified pages, such as filling out forms to update product information. Complex tasks alternate between these two stages multiple times. In this paper, we focus on achieving globally optimal planning in the exploration phase.

Previous work has shown that modeling observation transitions  $ T' : (o_t, a_t) \to o_{t+1} $ is sufficient to simulate web environments (Chae et al., 2025). Building on this insight, we introduce the Interaction Graph  $ \mathcal{G} = (\mathcal{V}, \mathcal{E}) $ to capture observation transitions. Nodes  $ v \in \mathcal{V} $ represent unique page

<div style="text-align: center;"><img src="https://pplines-online.bj.bcebos.com/deploy/official/paddleocr/pp-ocr-vl-15//72eacb6f-6dd2-4aa9-b885-74752f501df2/markdown_3/imgs/img_in_image_box_217_165_1005_523.jpg?authorization=bce-auth-v1%2FALTAKDN8mY5KlNI7zaRpLmOqrw%2F2026-05-03T07%3A47%3A15Z%2F-1%2F%2F245643014f6b8c3bf3c1a0b4ebfe2f9340720ba8a110a0a68ed5434c33988514" alt="Image" width="64%" /></div>


<div style="text-align: center;"><div style="text-align: center;">Figure 2: Trajectory comparison on a multi-site task (WebArena 760), which requires retrieving a specific customer address from the CMS to plan a route on the Map. WebNavigator achieves human-level planning via two navigate (domain, query) actions, whereas the ReAct baseline prematurely terminates due to Topological Blindness. The human expert trajectory is the gold standard.</div> </div>


observations, directed edges  $ e = (v, a, v') \in \mathcal{E} $ represent observation transitions, where executing action  $ a \in \mathcal{A} $ at node  $ v $ induces a transition to node  $ v' $. Each node  $ v $ is grounded via an observation mapping  $ \phi $, where  $ o_v = \phi(v) = (o_v^{\mathrm{vis}}, o_v^{\mathrm{str}}) $ denotes the multimodal observation comprising a screenshot  $ o_v^{\mathrm{vis}} $ and structural metadata  $ o_v^{\mathrm{str}} $ including DOM trees and accessibility trees. Given the Interaction Graph  $ \mathcal{G} $ that encodes the observation space  $ \mathcal{O} $ and transition dynamics  $ T' $, agents can achieve globally navigation in the exploration phase:

 $$ a_{t}\sim\pi_{\theta}(a_{t}\mid i,o_{t},h_{t},\mathcal{G}) $$ 

By conditioning on G, agents convert web navigation from probabilistic exploration into deterministic pathfinding, effectively addressing Topological Blindness. WebNavigator instantiates this paradigm through two phases: Offline Interaction Graph Construction and Online Retrieval-Augmented Navigation.

### 3.1 PHASE I: OFFLINE INTERACTION GRAPH CONSTRUCTION

Constructing Interaction Graphs  $ \mathcal{G} $ requires discovering dynamic web states that emerge through user interactions. Traditional crawlers parse static hyperlinks to discover URL-addressable pages but miss interaction-triggered states, thereby capturing only a narrow subset of the complete observation space  $ \mathcal{O} $ (Wu et al., 2025). Recent LLM-based exploration methods (Cheng et al., 2025b) suffer from probabilistic reasoning and context-window constraints, leading to redundant revisiting, incomplete coverage, and prohibitive token costs.

Heuristic Auto-Exploration Engine. As shown in Fig. 1 phase I, we develop a heuristic auto-exploration engine based on breadth-first search (BFS) that systematically interacts with dynamic elements to explore the observation space $\mathcal{O}$. Each discovered node $v \in \mathcal{V}$ is uniquely indexed by hashing its structural components $\text{Hash}(o_v^{\text{str}}, \text{url}_v)$, and the engine captures comprehensive representations including DOM structures, accessibility trees, and screenshots. Naive BFS is inefficient because web interactions typically induce only local changes. Child pages inherit most elements from their parents, resulting in redundant exploration. Inspired by the human strategy of avoiding re-clicking explored elements, we design an Adaptive BFS algorithm that leverages structural differencing between DOM trees. At each exploration step, we compute the structural difference between the current node $v$ and its parent $v_{\text{parent}}$ to identify newly added elements. We then extract interactive elements from these differential elements. This strategy significantly reduces exploration overhead by focusing on newly emerged interactive elements, which are substantially fewer than the total number of elements in practice. To reach interaction-triggered nodes such as toggled menus, the

algorithm simultaneously constructs  $ \mathcal{G} $ while utilizing it for pathfinding. Additionally, the engine employs a configurable block list to exclude hazardous operations and external links from exploration. Complete details are provided in Appendix C.

Graph Indexing for Retrieval. Upon completing exploration, all nodes  $ v \in V $ are embedded and indexed in a persistent database for multimodal retrieval during online navigation. Details about retrieval and navigation integration are described in Section 3.2.

### 3.2 PHASE II: ONLINE RETRIEVAL-AUGMENTED NAVIGATION

Directly providing the complete Interaction Graph  $ \mathcal{G} $ to LLMs is impractical due to context window constraints. Summarizing observations via LLMs not only adds token overhead but also risks losing critical information. WebNavigator resolves this through the Global-View Navigator, which implements a three-stage workflow: (1) **Retrieve**: identifying top-k candidate nodes  $ \mathcal{C} \subset \mathcal{V} $ via multimodal retrieval given navigation query  $ q $, (2) **Reason**: selecting the optimal target node  $ v^* \in \mathcal{C} $ from the candidates, and (3) **Teleport**: computing and executing the shortest path to  $ v^* $.

Retrieve. Recent studies in optical compression have demonstrated that vision-language models can process rendered text with significantly fewer tokens than text-only models without sacrificing reasoning performance (Cheng et al., 2025a; Wei et al., 2025). Building on this insight, WebNavigator adopts screenshots as optically compressed observations for multimodal retrieval, as they provide more compact representations than prohibitively long and noisy DOM trees. Given a navigation intent i, the agent aims to locate the optimal target observation  $ o^* \in \mathcal{O} $ where the task can be completed. To identify the corresponding node in  $ \mathcal{G} $, the agent formulates a navigation query q based on intent i to specify the target page functionality (e.g., for intent “Edit product X’s price to 50”, query “page to edit product information”) and encodes it into multi-vector representation  $ \mathbf{q} \in \mathbb{R}^{n \times d} $ using the same multimodal embedding model from Phase I, where n denotes the number of tokens and d is the embedding dimension per token (Günther et al., 2025). This query embedding q is then compared with the pre-indexed screenshot embeddings  $ \{\mathbf{v}_j \in \mathbb{R}^{m_j \times d}\}_{j=1}^{|\mathcal{V}|} $ by computing the late-interaction similarity score (Khattab & Zaharia, 2020):

 $$ s_{\mathrm{l a t e}}(\mathbf{q},\mathbf{v}_{j})=\frac{1}{n}\sum_{i=1}^{n}\max_{\ell\in\{1,\ldots,m_{j}\}}\mathbf{q}_{i}\cdot\mathbf{v}_{j,\ell}^{\top} $$ 

where  $ m_j $ denotes the number of tokens in screenshot,  $ \mathbf{q}_i \in \mathbb{R}^d $ denotes the  $ i $-th token embedding in the query, and  $ \mathbf{v}_{j,\ell} \in \mathbb{R}^d $ denotes the  $ \ell $-th token embedding in screenshot of node  $ v_j $. Unlike dense single-vector retrieval, late-interaction computes fine-grained token-level similarities, enabling more precise semantic matching while maintaining computational efficiency (Günther et al., 2025). The retrieval process identifies the top- $ k $ candidates  $ \mathcal{C} $ with the highest similarity scores:

 $$ \mathcal{C}=\{v_{1},\ldots,v_{k}\},\mathrm{~w h e r e~}v_{i}=\arg\max_{s_{\mathrm{l a t e}}}(\mathbf{q},\mathbf{v})_{\substack{v\in\mathcal{V}\backslash\{v_{1},\ldots,v_{i-1}\}}} $$ 

Reason. Since retrieval is based on semantic similarity, the top-$k$ candidates $C$ may contain visually similar but functionally distinct pages. To identify the optimal target node, we leverage a multimodal LLM as a zero-shot reasoner (prompt detailed in Appendix G.4) to select the most likely candidate $\hat{v}$ by analyzing the visual observations $\mathcal{O}_{\mathcal{C}} = \{o_{v_i}^{vis}\}_{j=1}^k$ along with the intent $i$:

 $$ \hat{v}=\arg\max_{v\in\mathcal{C}}P_{\theta}(v\mid i,\mathcal{O}_{C}) $$ 

where  $ P_{\theta} $ represents the model's probability over the candidate set C. By selecting from the top-k candidates rather than engaging in generative exploration, this approach fundamentally reduces navigation complexity from generation to verification (Cobbe et al., 2021; Pan et al., 2024).

Teleport. Once the target node  $ \hat{v} $ is determined, the Global-View Navigator computes the shortest path from the current observation  $ v_{current} $ to  $ \hat{v} $ by invoking the pathfinding mechanism used in Section 3.1:

 $$ \tau=(a_{1},\ldots,a_{m})=ShortestPath(v_{current},\hat{v},\mathcal{G}) $$ 

The Navigator executes this action sequence  $ \tau $, transitioning the agent to the target observation with zero-token cost.

Overall, through the Retrieve-Reason-Teleport workflow, the Global-View Navigator approaches globally optimal planning by transforming probabilistic exploration into deterministic retrieval and pathfinding over the pre-constructed graph. From the agent's perspective, this entire workflow is abstracted into a single high-level action that accepts a navigation query and enables teleportation to the task-relevant page, providing an LLM-friendly interface that eliminates the need to manage complex navigation logic.

### 3.3 WEBNAVIGATOR AGENT DESIGN

Unlike prior approaches that fragment capabilities across numerous atomic primitives (Sodhi et al., 2023; Zhou et al., 2024b), we aggregate cross-domain planning, task decomposition, and environment navigation into a high-density yet LLM-friendly interface. WebNavigator encapsulates the entire Retrieve-Reason-Teleport workflow into a unified action denoted as navigate (domain, query). This design establishes a paradigm of capability aggregation. The query parameter enables the agent to focus on one navigation subgoal at a time, naturally decomposing complex tasks into sequential steps. The domain parameter enables cross-domain planning by allowing dynamic switching between Interaction Graphs. The LLM specifies where to navigate, while the underlying workflow handles how to reach the target as a black box. In contrast to previous methods that fragment tab management capability into explicit actions such as tab_focus, new_tab, and tab_close (Zhou et al., 2024b), WebNavigator offers a significant advantage by subsuming these capabilities within the Retrieve-Reason-Teleport workflow. The underlying browser state is automatically managed while shielding the LLM from these mechanics. Methods such as SteP require designing domain-specific actions for each website, including find_commits for GitLab and search_customer for e-commerce (Sodhi et al., 2023). WebNavigator externalizes environmental knowledge into the Interaction Graph as queryable external memory. This design eliminates the need to handcraft specialized actions for every domain, allowing a generic interface to operate uniformly across websites. Centered on the navigate (domain, query) action, we augment with five primitives for local execution. This yields the most compact action space among existing methods (Table 6), reducing decision complexity and improving action selection reliability. Complete action definitions and prompts are detailed in Appendix G.3.

## 4 EXPERIMENT

To comprehensively evaluate whether WebNavigator addresses Topological Blindness, we evaluate it on two benchmarks: a controlled environment (WebArena) and diverse real-world websites (Online-Mind2Web).

Benchmarks. We primarily evaluate on WebArena (Zhou et al., 2024b), the most widely used benchmark for web navigation. WebArena comprises 812 carefully designed tasks spanning five representative websites: e-commerce (Shopping), social forums (Reddit), collaborative development (GitLab), content management systems (CMS), and mapping services (Map). WebArena employs programmatic validation mechanisms developed by human experts to assess functional correctness, enabling reliable measurement of task completion. To assess generalization capabilities beyond these controlled environments, we extend our evaluation to the real-world benchmark Online-Mind2Web (Xue et al., 2025), which consists of 300 diverse tasks across 136 live websites.

Baselines. We compare WebNavigator against methods including WebArena (Zhou et al., 2024b), Browsergym (Chezelles et al., 2024), Tree Search (Koh et al., 2025), Exact (Yu et al., 2025), Branch-and-browse (He et al., 2025), WebPilot (Zhang et al., 2025), Auto-Eval (Pan et al., 2024), WebDreamer (Gu et al., 2025), WMA (Chae et al., 2025), SteP (Sodhi et al., 2023), Plan-and-Act (Erdogan et al., 2025), Agent-e (Abuelsaad et al., 2024), AgentOccam (Yang et al., 2025) and CUGA (Marreed et al., 2025).

Implementation Details. To ensure a fair comparison, we strictly align our experimental setup with previous works (Xue et al., 2025; Yang et al., 2025). Specifically, we reproduce key baselines using GPT-4o as the unified backbone model. Detailed experimental parameters are provided in Appendix B.

<div style="text-align: center;"><div style="text-align: center;">Table 1: Main results on WebArena and Online-Mind2Web. Model denotes the base LLM or VLM for action generation. Act # indicates the number of actions. Success rate (SR) for different website domains. \textbf{Bold} and \textbf{underlined} values indicate the best and second-best performance among non-enterprise agents. Methods marked with * are our reproduced results. Models marked with † are finetuned.</div> </div>




<table border=1 style='margin: auto; word-wrap: break-word;'><tr><td rowspan="2">Method</td><td rowspan="2">Model</td><td rowspan="2">Act #</td><td colspan="6">WebArena</td><td colspan="2">Online-Mind2Web</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>SR(%)</td><td style='text-align: center; word-wrap: break-word;'>Multisite</td><td style='text-align: center; word-wrap: break-word;'>Shopping</td><td style='text-align: center; word-wrap: break-word;'>CMS</td><td style='text-align: center; word-wrap: break-word;'>Reddit</td><td style='text-align: center; word-wrap: break-word;'>GitLab</td><td style='text-align: center; word-wrap: break-word;'>Map</td><td style='text-align: center; word-wrap: break-word;'>SR(%)</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Webarena* (Zhou et al., 2024b)</td><td style='text-align: center; word-wrap: break-word;'>GPT-4o</td><td style='text-align: center; word-wrap: break-word;'>12</td><td style='text-align: center; word-wrap: break-word;'>21.1</td><td style='text-align: center; word-wrap: break-word;'>8.3</td><td style='text-align: center; word-wrap: break-word;'>24.6</td><td style='text-align: center; word-wrap: break-word;'>20.3</td><td style='text-align: center; word-wrap: break-word;'>22.6</td><td style='text-align: center; word-wrap: break-word;'>22.2</td><td style='text-align: center; word-wrap: break-word;'>18.4</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Browsergym (Chezelles et al., 2024)</td><td style='text-align: center; word-wrap: break-word;'>GPT-4o</td><td style='text-align: center; word-wrap: break-word;'>20</td><td style='text-align: center; word-wrap: break-word;'>31.4</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td colspan="11">Paradigm 1: Online Exploratory Search</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Tree search (Koh et al., 2025)</td><td style='text-align: center; word-wrap: break-word;'>GPT-4o</td><td style='text-align: center; word-wrap: break-word;'>12</td><td style='text-align: center; word-wrap: break-word;'>19.2</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>28.1</td><td style='text-align: center; word-wrap: break-word;'>16.5</td><td style='text-align: center; word-wrap: break-word;'>10.5</td><td style='text-align: center; word-wrap: break-word;'>13.3</td><td style='text-align: center; word-wrap: break-word;'>25.8</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Exact (Yu et al., 2025)</td><td style='text-align: center; word-wrap: break-word;'>GPT-4o</td><td style='text-align: center; word-wrap: break-word;'>16</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>23.5</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Branch and browse (He et al., 2025)</td><td style='text-align: center; word-wrap: break-word;'>GPT-4o</td><td style='text-align: center; word-wrap: break-word;'>12</td><td style='text-align: center; word-wrap: break-word;'>35.8</td><td style='text-align: center; word-wrap: break-word;'>18.8</td><td style='text-align: center; word-wrap: break-word;'>34.6</td><td style='text-align: center; word-wrap: break-word;'>26.4</td><td style='text-align: center; word-wrap: break-word;'>50.9</td><td style='text-align: center; word-wrap: break-word;'>36.7</td><td style='text-align: center; word-wrap: break-word;'>46.8</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>WebPilot (Zhang et al., 2025)</td><td style='text-align: center; word-wrap: break-word;'>GPT-4o</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>37.2</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>36.9</td><td style='text-align: center; word-wrap: break-word;'>24.7</td><td style='text-align: center; word-wrap: break-word;'>65.1</td><td style='text-align: center; word-wrap: break-word;'>39.4</td><td style='text-align: center; word-wrap: break-word;'>33.9</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Auto-Eval (Pan et al., 2024)</td><td style='text-align: center; word-wrap: break-word;'>GPT-4-Preview</td><td style='text-align: center; word-wrap: break-word;'>12</td><td style='text-align: center; word-wrap: break-word;'>20.2</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td colspan="11">Paradigm 2: Learned Internal Planning &amp; World Models</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>WebDreamer (Gu et al., 2025)</td><td style='text-align: center; word-wrap: break-word;'>GPT-4o</td><td style='text-align: center; word-wrap: break-word;'>12</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>35.0</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>WMA (Chae et al., 2025)</td><td style='text-align: center; word-wrap: break-word;'>GPT-4o</td><td style='text-align: center; word-wrap: break-word;'>12</td><td style='text-align: center; word-wrap: break-word;'>16.6</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>SteP (Sodhi et al., 2023)</td><td style='text-align: center; word-wrap: break-word;'>GPT-4-Turbo</td><td style='text-align: center; word-wrap: break-word;'>27</td><td style='text-align: center; word-wrap: break-word;'>33.0</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>37.0</td><td style='text-align: center; word-wrap: break-word;'>24.0</td><td style='text-align: center; word-wrap: break-word;'>59.0</td><td style='text-align: center; word-wrap: break-word;'>32.0</td><td style='text-align: center; word-wrap: break-word;'>30.0</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Plan-and-Act (Erdogan et al., 2025)</td><td style='text-align: center; word-wrap: break-word;'>Llama-70B $ \dagger $</td><td style='text-align: center; word-wrap: break-word;'>8</td><td style='text-align: center; word-wrap: break-word;'>45.7</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Agent-e (Abuelsaad et al., 2024)</td><td style='text-align: center; word-wrap: break-word;'>GPT-4o</td><td style='text-align: center; word-wrap: break-word;'>8</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>27.0</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>AgentOccam (Yang et al., 2025)</td><td style='text-align: center; word-wrap: break-word;'>GPT-4-Turbo</td><td style='text-align: center; word-wrap: break-word;'>8</td><td style='text-align: center; word-wrap: break-word;'>43.1</td><td style='text-align: center; word-wrap: break-word;'>14.6</td><td style='text-align: center; word-wrap: break-word;'>40.6</td><td style='text-align: center; word-wrap: break-word;'>45.6</td><td style='text-align: center; word-wrap: break-word;'>61.3</td><td style='text-align: center; word-wrap: break-word;'>37.8</td><td style='text-align: center; word-wrap: break-word;'>46.8</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>AgentOccam* (Yang et al., 2025)</td><td style='text-align: center; word-wrap: break-word;'>GPT-4o</td><td style='text-align: center; word-wrap: break-word;'>8</td><td style='text-align: center; word-wrap: break-word;'>42.9</td><td style='text-align: center; word-wrap: break-word;'>25.0</td><td style='text-align: center; word-wrap: break-word;'>33.7</td><td style='text-align: center; word-wrap: break-word;'>46.7</td><td style='text-align: center; word-wrap: break-word;'>60.4</td><td style='text-align: center; word-wrap: break-word;'>44.4</td><td style='text-align: center; word-wrap: break-word;'>40.4</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td colspan="11">Enterprise-Level Autonomous Agents</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>CUGA (Marreed et al., 2025)</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>61.7</td><td style='text-align: center; word-wrap: break-word;'>35.4</td><td style='text-align: center; word-wrap: break-word;'>58.3</td><td style='text-align: center; word-wrap: break-word;'>62.6</td><td style='text-align: center; word-wrap: break-word;'>75.5</td><td style='text-align: center; word-wrap: break-word;'>61.7</td><td style='text-align: center; word-wrap: break-word;'>64.2</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td colspan="11">New Paradigm: Retrieve and Navigate</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>WebNavigator (Ours)</td><td style='text-align: center; word-wrap: break-word;'>Qwen3-VL-32B-Instruct</td><td style='text-align: center; word-wrap: break-word;'>6</td><td style='text-align: center; word-wrap: break-word;'>47.8</td><td style='text-align: center; word-wrap: break-word;'>43.8</td><td style='text-align: center; word-wrap: break-word;'>44.9</td><td style='text-align: center; word-wrap: break-word;'>45.1</td><td style='text-align: center; word-wrap: break-word;'>75.5</td><td style='text-align: center; word-wrap: break-word;'>50.6</td><td style='text-align: center; word-wrap: break-word;'>44.0</td><td style='text-align: center; word-wrap: break-word;'>39.7</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>WebNavigator (Ours)</td><td style='text-align: center; word-wrap: break-word;'>GPT-4o</td><td style='text-align: center; word-wrap: break-word;'>6</td><td style='text-align: center; word-wrap: break-word;'>49.9</td><td style='text-align: center; word-wrap: break-word;'>50.0</td><td style='text-align: center; word-wrap: break-word;'>44.4</td><td style='text-align: center; word-wrap: break-word;'>48.6</td><td style='text-align: center; word-wrap: break-word;'>73.6</td><td style='text-align: center; word-wrap: break-word;'>42.2</td><td style='text-align: center; word-wrap: break-word;'>51.4</td><td style='text-align: center; word-wrap: break-word;'>41.3</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>WebNavigator (Ours)</td><td style='text-align: center; word-wrap: break-word;'>Claude-Sonnet-4</td><td style='text-align: center; word-wrap: break-word;'>6</td><td style='text-align: center; word-wrap: break-word;'>57.1</td><td style='text-align: center; word-wrap: break-word;'>50.0</td><td style='text-align: center; word-wrap: break-word;'>51.9</td><td style='text-align: center; word-wrap: break-word;'>58.2</td><td style='text-align: center; word-wrap: break-word;'>85.9</td><td style='text-align: center; word-wrap: break-word;'>50.0</td><td style='text-align: center; word-wrap: break-word;'>51.4</td><td style='text-align: center; word-wrap: break-word;'>38.7</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>WebNavigator (Ours)</td><td style='text-align: center; word-wrap: break-word;'>Gemini-2.5-Pro</td><td style='text-align: center; word-wrap: break-word;'>6</td><td style='text-align: center; word-wrap: break-word;'>63.3</td><td style='text-align: center; word-wrap: break-word;'>72.9</td><td style='text-align: center; word-wrap: break-word;'>51.9</td><td style='text-align: center; word-wrap: break-word;'>66.5</td><td style='text-align: center; word-wrap: break-word;'>85.9</td><td style='text-align: center; word-wrap: break-word;'>62.2</td><td style='text-align: center; word-wrap: break-word;'>53.2</td><td style='text-align: center; word-wrap: break-word;'>52.7</td></tr></table>

### 4.1 MAIN RESULTS

As shown in Table 1, WebNavigator substantially outperforms previous state-of-the-art methods on WebArena and Online-Mind2Web. Specifically, on WebArena, WebNavigator achieves 47.8% with Qwen3-VL-32B-Instruct, 49.9% with GPT-4o, 57.1% with Claude-Sonnet-4, and 63.3% with Gemini-2.5-Pro, surpassing WebPilot (37.2% from Paradigm 1) and Plan-and-Act (45.7% from Paradigm 2). Most significantly, WebNavigator achieves breakthrough performance on multi-site tasks, the most challenging cross-domain setting in WebArena. With GPT-4o and Claude-Sonnet-4, WebNavigator achieves a 50.0% success rate, surpassing AgentOccam by 100% relative improvement over its 25.0%. Compared with the enterprise-level agent, WebNavigator with Gemini-2.5-Pro achieves a 72.9%, more than twice the performance of the CUGA system. Overall, WebNavigator establishes a new performance ceiling on WebArena's multi-site tasks. On Online-Mind2Web, WebNavigator achieves 52.7% with Gemini-2.5-Pro and 41.3% with GPT-4o, establishing state-of-the-art performance on this challenging benchmark. Crucially, these results validate the generalization capability of WebNavigator across 136 diverse real-world websites. Notably, WebNavigator achieves these improvements with only 6 actions, the most compact action space among all compared methods.

Structural Foundations of Topological Blindness. The magnitude of performance improvement across domains directly correlates with the severity of Topological Blindness in each environment. Multi-site navigation represents the peak of Topological Blindness, where agents are blind to information in external domains. Consider WebArena task 760: “Show the route from Allentown, PA to where customer Amanda Kim lives.” As shown in Figure 2, without global awareness, the react agent is misled by local cues, leading to premature termination. In contrast, WebNavigator achieves human-level planning by acquiring cross-domain knowledge, encoded in GCMS and GMap. Specifically, WebNavigator teleports to the customer page in CMS to retrieve Amanda Kim’s city, then switches to Map to query the driving directions, thus completing the task.

Reddit represents a wide and shallow topology with over 90 forums. React agents can only observe a fraction of paths from the homepage, leading to low-probability guesses. Consider WebArena task 681, which requires posting a GAN-related repository to a relevant subreddit. In this case, an agent lacking global awareness is predisposed to converge on a locally plausible candidate such as /f/coolgithubprojects. In contrast, WebNavigator identifies the optimal targets such as /f/deeplearning and /f/MachineLearning by leveraging the complete forums encoded in  $ \mathcal{G}_{\text{Reddit}} $. This global visibility enables WebNavigator to achieve its most significant gain on Reddit. In deep, complex environments such as CMS, Shopping, and GitLab, agents become trapped by misleading surface pages that mask deeper, task-relevant pages. WebNavigator enables direct navigation to optimal pages, bypassing these traps with substantial gains on Shopping, CMS, and GitLab. Performance on the Map domain converges across models, where Gemini-2.5-Pro exhibiting no significant margin over GPT-4o or Claude-Sonnet-4. This uniformity is attributed to the constrained observation space of the Map domain ( $ |\mathcal{V}_{\text{Map}}| = 16 $), which mitigates the impact of Topological Blindness. In contrast, other domains contain more than 100 nodes, detailed in Table 2.

### 4.2 EMPIRICAL ANALYSIS OF THE TOPOLOGICAL SKELETON

Prior research characterizes web environments as a unified environment with effectively infinite observation spaces (Shi et al., 2017; Liu et al., 2024). In contrast, we hypothesize that individual websites possess compact topological skeletons, rather than viewing web environments as an intractable monolith. We define the topological skeleton as the compact graph representation of a website's interaction logic, where functionally equivalent pages (e.g., different product pages sharing identical interaction patterns) are collapsed into a single representative node. To isolate the skeleton from database content, we leverage the heuristic auto-exploration engine from Section 3.1 with a specific block list configuration. After exploring one representative product or repository, subsequent instances are treated as redundant because they share the same interaction patterns. Note that benchmark evaluations in Section 4.1 utilize unconstrained exploration to maximize task coverage. We quantify skeleton growth via discovery velocity, defined as  $ \mathcal{V}_d = (N_d - N_{d-1}) / (T_d - T_{d-1}) $, where  $ N_d $ and  $ T_d $ denote cumulative node count and exploration time at depth  $ d $. We analyze  $ N_d $ and  $ V_d $ across four WebArena domains (Reddit, CMS, Map, GitLab) as depth increases from 0 to 5. As shown in Fig. 3, discovery velocity for Reddit and CMS peaks at  $ d = 2 $ followed by a steady decline. This pattern indicates that the engine identifies the primary functional clusters early in the exploration, after which the marginal cost of discovering unique nodes increases significantly. The Map website possesses a highly compact topological skeleton. With only 29 unique nodes, its discovery velocity drops to zero at  $ d = 5 $, indicating the environment is fully captured. GitLab exhibits a distinct pattern. Discovery velocity declines until  $ d = 4 $ as the engine has fully explored shallow functional pages (e.g., dashboards, settings), but rises at  $ d = 5 $ as it uncovers repository fine-grained configurations with minimal redundancy.

<div style="text-align: center;"><img src="https://pplines-online.bj.bcebos.com/deploy/official/paddleocr/pp-ocr-vl-15//8ffaa378-b0bd-45be-8781-af9881686725/markdown_2/imgs/img_in_chart_box_226_1033_399_1256.jpg?authorization=bce-auth-v1%2FALTAKDN8mY5KlNI7zaRpLmOqrw%2F2026-05-03T07%3A47%3A21Z%2F-1%2F%2Fc848fe64e60dce970fc7485b4bd1d36d0138fbdb6e36e363dd4f28e0a9a80925" alt="Image" width="14%" /></div>


<div style="text-align: center;"><div style="text-align: center;">(a) Reddit</div> </div>


<div style="text-align: center;"><img src="https://pplines-online.bj.bcebos.com/deploy/official/paddleocr/pp-ocr-vl-15//8ffaa378-b0bd-45be-8781-af9881686725/markdown_2/imgs/img_in_chart_box_418_1032_600_1256.jpg?authorization=bce-auth-v1%2FALTAKDN8mY5KlNI7zaRpLmOqrw%2F2026-05-03T07%3A47%3A21Z%2F-1%2F%2F2e59ba1cb67e7dc75e4d12dc9c3ba4a1b23fcac3650835f83a3ae042fcd51351" alt="Image" width="14%" /></div>


<div style="text-align: center;"><div style="text-align: center;">(b) Map</div> </div>


<div style="text-align: center;"><img src="https://pplines-online.bj.bcebos.com/deploy/official/paddleocr/pp-ocr-vl-15//8ffaa378-b0bd-45be-8781-af9881686725/markdown_2/imgs/img_in_chart_box_614_1033_789_1257.jpg?authorization=bce-auth-v1%2FALTAKDN8mY5KlNI7zaRpLmOqrw%2F2026-05-03T07%3A47%3A21Z%2F-1%2F%2F883cac50fb7ed7927489140c9eea37b4356c550821749e1799e8abb64508b9ec" alt="Image" width="14%" /></div>


<div style="text-align: center;"><div style="text-align: center;">(c) CMS</div> </div>


<div style="text-align: center;"><img src="https://pplines-online.bj.bcebos.com/deploy/official/paddleocr/pp-ocr-vl-15//8ffaa378-b0bd-45be-8781-af9881686725/markdown_2/imgs/img_in_chart_box_809_1033_983_1256.jpg?authorization=bce-auth-v1%2FALTAKDN8mY5KlNI7zaRpLmOqrw%2F2026-05-03T07%3A47%3A21Z%2F-1%2F%2F7584b6524ae0d8efbe07f6772d20748348c5168d5d2ce4c2bf76dbc1f481e879" alt="Image" width="14%" /></div>


<div style="text-align: center;"><div style="text-align: center;">(d) GitLab</div> </div>


<div style="text-align: center;"><div style="text-align: center;">Figure 3: Discovery velocity and Node Growth at Different Depths. Blue bars (left axis) show cumulative nodes discovered at each depth. Orange lines (right axis) show discovery velocity in nodes per minute.</div> </div>


These patterns confirm that although content instances are theoretically infinite, the topological skeleton remains compact. Task-relevant pages concentrate at shallow depths, supporting our exploration depth settings in Table 2. Strong performance on more than 100 diverse websites in

Online-Mind2Web (Table 1) further validates these findings. Consequently, web navigation can be reframed from open-ended probabilistic exploration into deterministic retrieval and pathfinding.

### 4.3 IMPACT OF ENVIRONMENTAL KNOWLEDGE COMPLETENESS AND INFORMATION BANDWIDTH

We investigate the underlying mechanisms that enable WebNavigator to resolve Topological Blindness. Specifically, we conduct controlled experiments on the Reddit domain in WebArena. Fig. 4 presents the detailed results.

Knowledge Completeness. We investigate how the completeness of environmental knowledge affects agent performance. We control knowledge completeness by varying exploration depth from 1 to 4, with fixed GPT-4o, k = 30, Gemini-2.5-Flash selector, and Jina-embedding-v4 retriever. As shown in Fig. 4, the success rate rises sharply from 63.2% at depth 1 to 70.8% at depth 2. This upward trend persists at greater depths, reaching 73.6% at depth 3 and 75.5% at depth 4. At depth 1, the Interaction Graph captures only surface-level entry points, leaving many task-relevant pages undiscovered. When depth reaches 2, the Interaction Graph achieves sufficient coverage of task-relevant subreddits, marking a critical transition. Depths 3 and 4 provide additional gains by handling tasks requiring deeper navigation, though with smaller incremental improvements. These results confirm that suf-

<div style="text-align: center;"><div style="text-align: center;">Figure 4: Ablation study on depth, top-k, selector model, and retriever on the Reddit domain in WebArena. * denotes dense embedding mode.</div> </div>




<table border=1 style='margin: auto; word-wrap: break-word;'><tr><td style='text-align: center; word-wrap: break-word;'>Group</td><td style='text-align: center; word-wrap: break-word;'>Depth</td><td style='text-align: center; word-wrap: break-word;'>Top-k</td><td style='text-align: center; word-wrap: break-word;'>Selector Model</td><td style='text-align: center; word-wrap: break-word;'>Retriever</td><td style='text-align: center; word-wrap: break-word;'>SR(%)</td></tr><tr><td colspan="6">Depth Ablation</td></tr><tr><td rowspan="4">Depth</td><td style='text-align: center; word-wrap: break-word;'>1</td><td style='text-align: center; word-wrap: break-word;'>30</td><td style='text-align: center; word-wrap: break-word;'>Gemini-2.5-Flash</td><td style='text-align: center; word-wrap: break-word;'>Jina-embedding-v4</td><td style='text-align: center; word-wrap: break-word;'>63.2</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>2</td><td style='text-align: center; word-wrap: break-word;'>30</td><td style='text-align: center; word-wrap: break-word;'>Gemini-2.5-Flash</td><td style='text-align: center; word-wrap: break-word;'>Jina-embedding-v4</td><td style='text-align: center; word-wrap: break-word;'>70.8</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>3</td><td style='text-align: center; word-wrap: break-word;'>30</td><td style='text-align: center; word-wrap: break-word;'>Gemini-2.5-Flash</td><td style='text-align: center; word-wrap: break-word;'>Jina-embedding-v4</td><td style='text-align: center; word-wrap: break-word;'>73.6</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>4</td><td style='text-align: center; word-wrap: break-word;'>30</td><td style='text-align: center; word-wrap: break-word;'>Gemini-2.5-Flash</td><td style='text-align: center; word-wrap: break-word;'>Jina-embedding-v4</td><td style='text-align: center; word-wrap: break-word;'>75.5</td></tr><tr><td colspan="6">Top-k Ablation</td></tr><tr><td rowspan="4">Top-k</td><td style='text-align: center; word-wrap: break-word;'>3</td><td style='text-align: center; word-wrap: break-word;'>10</td><td style='text-align: center; word-wrap: break-word;'>Gemini-2.5-Flash</td><td style='text-align: center; word-wrap: break-word;'>Jina-embedding-v4</td><td style='text-align: center; word-wrap: break-word;'>71.7</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>3</td><td style='text-align: center; word-wrap: break-word;'>20</td><td style='text-align: center; word-wrap: break-word;'>Gemini-2.5-Flash</td><td style='text-align: center; word-wrap: break-word;'>Jina-embedding-v4</td><td style='text-align: center; word-wrap: break-word;'>73.6</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>3</td><td style='text-align: center; word-wrap: break-word;'>30</td><td style='text-align: center; word-wrap: break-word;'>Gemini-2.5-Flash</td><td style='text-align: center; word-wrap: break-word;'>Jina-embedding-v4</td><td style='text-align: center; word-wrap: break-word;'>73.6</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>3</td><td style='text-align: center; word-wrap: break-word;'>40</td><td style='text-align: center; word-wrap: break-word;'>Gemini-2.5-Flash</td><td style='text-align: center; word-wrap: break-word;'>Jina-embedding-v4</td><td style='text-align: center; word-wrap: break-word;'>75.5</td></tr><tr><td colspan="6">Selector Model Ablation</td></tr><tr><td rowspan="3">Selector</td><td style='text-align: center; word-wrap: break-word;'>3</td><td style='text-align: center; word-wrap: break-word;'>30</td><td style='text-align: center; word-wrap: break-word;'>GPT-4o</td><td style='text-align: center; word-wrap: break-word;'>Jina-embedding-v4</td><td style='text-align: center; word-wrap: break-word;'>72.6</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>3</td><td style='text-align: center; word-wrap: break-word;'>30</td><td style='text-align: center; word-wrap: break-word;'>Gemini-2.5-Flash</td><td style='text-align: center; word-wrap: break-word;'>Jina-embedding-v4</td><td style='text-align: center; word-wrap: break-word;'>73.6</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>3</td><td style='text-align: center; word-wrap: break-word;'>30</td><td style='text-align: center; word-wrap: break-word;'>Qwen3-VL-8B</td><td style='text-align: center; word-wrap: break-word;'>Jina-embedding-v4</td><td style='text-align: center; word-wrap: break-word;'>75.5</td></tr><tr><td colspan="6">Retriever Ablation</td></tr><tr><td rowspan="3">Retriever</td><td style='text-align: center; word-wrap: break-word;'>3</td><td style='text-align: center; word-wrap: break-word;'>30</td><td style='text-align: center; word-wrap: break-word;'>Gemini-2.5-Flash</td><td style='text-align: center; word-wrap: break-word;'>Jina-embedding-v4</td><td style='text-align: center; word-wrap: break-word;'>73.6</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>3</td><td style='text-align: center; word-wrap: break-word;'>30</td><td style='text-align: center; word-wrap: break-word;'>Gemini-2.5-Flash</td><td style='text-align: center; word-wrap: break-word;'>Jina-embedding-v4 $ ^{*} $</td><td style='text-align: center; word-wrap: break-word;'>67.0</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>3</td><td style='text-align: center; word-wrap: break-word;'>30</td><td style='text-align: center; word-wrap: break-word;'>Gemini-2.5-Flash</td><td style='text-align: center; word-wrap: break-word;'>Jina-clip-v2</td><td style='text-align: center; word-wrap: break-word;'>66.0</td></tr></table>

ficient knowledge completeness is necessary to address Topological Blindness.

Information Bandwidth. We investigate how the information bandwidth affects agent performance. The retrieval top-k parameter controls the number of candidates transferred from the knowledge base to the selector. Larger k expands the decision space of the Selector but increases computational overhead, while insufficient k risks excluding the optimal target. We vary k from 10 to 40 while keeping all other parameters fixed. As shown in Fig. 4, performance improves as k increases. At k = 40, WebNavigator achieves 75.5%, surpassing our main configuration of k = 30. More importantly, even with the narrowest bandwidth at k = 10, WebNavigator achieves 71.7% success rate, outperforming all baseline methods in Table 1. This robustness under constrained bandwidth demonstrates that knowledge completeness, rather than the retrieval channel width, is the primary determinant of performance.

Task Simplification. We investigate whether WebNavigator simplifies the web navigation task by transforming trajectory generation into candidate selection. Prior works have shown that selecting candidates is simpler than generating trajectories, as it transforms the problem from solution construction to correctness identification (Pan et al., 2024; Xue et al., 2025). We evaluate Selectors ranging from leading proprietary models to the lightweight Qwen3-VL-8B-Instruct. As shown in Fig. 4, the 8B model achieves a 75.5% success rate, performing on par with GPT-4o and Gemini-2.5-Flash. This consistency across model scales confirms that the WebNavigator successfully offloads navigation complexity from model reasoning to structured knowledge retrieval.

Retrieval Granularity. We examine whether the granularity of multimodal retrieval affects the performance of WebNavigator. Specifically, we compare late interaction retrieval, which leverages token-level alignment via multi-vector representations, against traditional dense methods. We

evaluate Jina-embedding-v4 in both late interaction and dense mode (denoted by * in Fig. 4), and Jina-clip-v2 (Koukounas et al., 2024) in dense mode only. As shown in Fig. 4, late interaction retrieval achieves a 73.6% success rate, substantially outperforming dense embedding approaches at 66-67%. Dense embeddings compress entire screenshots into fixed-size vectors, discarding fine-grained spatial cues such as specific buttons, form fields, or menu structures. In contrast, late interaction preserves local visual semantics through token-level matching, enabling the retriever to identify specific page regions that satisfy the navigation intent. This confirms that web navigation requires fine-grained retrieval rather than global compression.

## 5 CONCLUSION

This work introduces WebNavigator, a novel paradigm that overcomes Topological Blindness by transforming probabilistic exploration into deterministic retrieval and pathfinding, achieving global planning with only 6 actions. WebNavigator achieves state-of-the-art performance on WebArena and Online-Mind2Web. On WebArena multi-site tasks, WebNavigator demonstrates a performance ceiling of 72.9%. These results reveal that Topological Blindness represents an underestimated bottleneck in autonomous web navigation.

### IMPACT STATEMENT

This work aims to advance the development of autonomous web navigation agents. However, we acknowledge the potential risks associated with autonomous web interactions, including the possibility of malicious dual-use such as automating CAPTCHA bypassing or unauthorized data scraping. To strictly mitigate these concerns during our study, we adhered to rigorous safety protocols where the majority of evaluations were conducted in a sandboxed environment. Furthermore, all interactions with real-world websites were performed exclusively in a logged-out state without authentication. This approach ensured that the agent could not execute sensitive operations or access private user data, and we emphasize that future deployment must incorporate strict usage policies and human-in-the-loop mechanisms.

## REFERENCES

Tamer Abuelsaad, Deepak Akkil, Prasenjit Dey, Ashish Jagmohan, Aditya Vempaty, and Ravi Kokku. Agent-e: From autonomous web navigation to foundational design principles in agentic systems. arXiv preprint arXiv:2407.13032, 2024.

Hyungjoo Chae, Namyoung Kim, Kai Tzu-iunn Ong, Minju Gwak, Gwanwoo Song, Jihoon Kim, Sunghwan Kim, Dongha Lee, and Jinyoung Yeo. Web agents with world models: Learning and leveraging environment dynamics in web navigation. In The Thirteenth International Conference on Learning Representations, ICLR 2025, Singapore, April 24-28, 2025. OpenReview.net, 2025.

Jiale Cheng, Yusen Liu, Xinyu Zhang, Yulin Fei, Wenyi Hong, Ruiliang Lyu, Weihan Wang, Zhe Su, Xiaotao Gu, Xiao Liu, et al. Glyph: Scaling context windows via visual-text compression. arXiv preprint arXiv:2510.17800, 2025a.

Jiali Cheng, Anjishnu Kumar, Roshan Lal, Rishi Rajasekaran, Hani Ramezani, Omar Zia Khan, Oleg Rokhlenko, Sunny Chiu-Webster, Gang Hua, and Hadi Amiri. Atlas: Actor-critic task-completion with look-ahead action simulation. arXiv preprint arXiv:2510.22732, 2025b.

De Chezelles, Thibault Le Sellier, Sahar Omidi Shayegan, Lawrence Keunho Jang, Xing Han Lù, Ori Yoran, Dehan Kong, Frank F Xu, Siva Reddy, Quentin Cappart, et al. The browsersgym ecosystem for web agent research. arXiv preprint arXiv:2412.05467, 2024.

Karl Cobbe, Vineet Kosaraju, Mohammad Bavarian, Mark Chen, Heewoo Jun, Lukasz Kaiser, Matthias Plappert, Jerry Tworek, Jacob Hilton, Reiichiro Nakano, et al. Training verifiers to solve math word problems. arXiv preprint arXiv:2110.14168, 2021.

Alexandre Drouin, Maxime Gasse, Massimo Caccia, Issam H. Laradji, Manuel Del Verme, Tom Marty, David Vázquez, Nicolas Chapados, and Alexandre Lacoste. Workarena: How capable

are web agents at solving common knowledge work tasks? In Forty-first International Conference on Machine Learning, ICML 2024, Vienna, Austria, July 21-27, 2024, pp. 11642–11662. OpenReview.net, 2024.

Lutfi Eren Erdogan, Nicholas Lee, Sehoon Kim, Suhong Moon, Hiroki Furuta, Gopala Anu-manchipalli, Kurt Keutzer, and Amir Gholami. Plan-and-act: Improving planning of agents for long-horizon tasks. In Forty-second International Conference on Machine Learning, ICML 2025, Vancouver, BC, Canada, July 13-19, 2025, pp. 15419–15462. OpenReview.net, 2025.

Yu Gu, Kai Zhang, Yuting Ning, Boyuan Zheng, Boyu Gou, Tianci Xue, Cheng Chang, Sanjari Srivastava, Yanan Xie, Peng Qi, Huan Sun, and Yu Su. Is your LLM secretly a world model of the internet? model-based planning for web agents. Transactions on Machine Learning Research, 2025. ISSN 2835-8856.

Michael Günther, Saba Sturua, Mohammad Kalim Akram, Isabelle Mohr, Andrei Ungureanu, Bo Wang, Sedigheh Eslami, Scott Martens, Maximilian Werk, Nan Wang, et al. jina-embeddings-v4: Universal embeddings for multimodal multilingual retrieval. In Proceedings of the 5th Workshop on Multilingual Representation Learning (MRL 2025), pp. 531–550, 2025.

Hongliang He, Wenlin Yao, Kaixin Ma, Wenhao Yu, Yong Dai, Hongming Zhang, Zhenzhong Lan, and Dong Yu. Webvoyager: Building an end-to-end web agent with large multimodal models. In Lun-Wei Ku, Andre Martins, and Vivek Srikumar (eds.), Proceedings of the 62nd Annual Meeting of the Association for Computational Linguistics (Volume 1: Long Papers), ACL 2024, Bangkok, Thailand, August 11-16, 2024, pp. 6864–6890. Association for Computational Linguistics, 2024. doi: 10.18653/V1/2024.ACL-LONG.371.

Shiqi He, Yue Cui, Xinyu Ma, Yaliang Li, Bolin Ding, and Mosharaf Chowdhury. Branch-and-browse: Efficient and controllable web exploration with tree-structured reasoning and action memory. arXiv preprint arXiv:2510.19838, 2025.

Thomas Hubert, Rishi Mehta, Laurent Sartran, Miklós Z. Horváth, Goran Žužić, Eric Wieser, Aja Huang, Julian Schrittwieser, Yannick Schroecker, Hussain Masoom, Ottavia Bertolli, Tom Zahavy, Amol Mandhane, Jessica Yung, Iuliya Beloshapka, Borja Ibarz, Vivek Veeriah, Lei Yu, Oliver Nash, Paul Lezeau, Salvatore Mercuri, Calle Sönne, Bhavik Mehta, Alex Davies, Daniel Zheng, Fabian Pedregosa, Yin Li, Ingrid von Glehn, Mark Rowland, Samuel Albanie, Ameya Velingker, Simon Schmitt, Edward Lockhart, Edward Hughes, Henryk Michalewski, Nicolas Sonnerat, Demis Hassabis, Pushmeet Kohli, and David Silver. Olympiad-level formal mathematical reasoning with reinforcement learning. Nature, November 2025. ISSN 1476-4687. doi:10.1038/s41586-025-09833-y.

Omar Khattab and Matei Zaharia. Colbert: Efficient and effective passage search via contextualized late interaction over BERT. In Jimmy X. Huang, Yi Chang, Xueqi Cheng, Jaap Kamps, Vanessa Murdock, Ji-Rong Wen, and Yiqun Liu (eds.), Proceedings of the 43rd International ACM SIGIR conference on research and development in Information Retrieval, SIGIR 2020, Virtual Event, China, July 25-30, 2020, pp. 39–48. ACM, 2020. doi: 10.1145/3397271.3401075.

Jing Yu Koh, Stephen Marcus McAleer, Daniel Fried, and Ruslan Salakhutdinov. Tree search for language model agents. Transactions on Machine Learning Research, 2025. ISSN 2835-8856.

Andreas Koukounas, Georgios Mastrapas, Sedigheh Eslami, Bo Wang, Mohammad Kalim Akram, Michael Günther, Isabelle Mohr, Saba Sturua, Nan Wang, and Han Xiao. jina-clip-v2: Multilingual multimodal embeddings for text and images. arXiv preprint arXiv:2412.08802, 2024.

Xiao Liu, Bo Qin, Dongzhu Liang, Guang Dong, Hanyu Lai, Hanchen Zhang, Hanlin Zhao, Iat Long Iong, Jiadai Sun, Jiaqi Wang, et al. Autoglm: Autonomous foundation agents for guis. arXiv preprint arXiv:2411.00820, 2024.

Sami Marreed, Alon Oved, Avi Yaeli, Segev Shlomov, Ido Levy, Offer Akrabi, Aviad Sela, Asaf Adi, and Nir Mashkif. Towards enterprise-ready computer using generalist agent. arXiv preprint arXiv:2503.01861, 2025.

Siru Ouyang, Jun Yan, I Hsu, Yanfei Chen, Ke Jiang, Zifeng Wang, Rujun Han, Long T Le, Samira Daruki, Xiangru Tang, et al. Reasoningbank: Scaling agent self-evolving with reasoning memory. arXiv preprint arXiv:2509.25140, 2025.

Jiayi Pan, Yichi Zhang, Nicholas Tomlin, Yifei Zhou, Sergey Levine, and Alane Suhr. Autonomous evaluation and refinement of digital agents. arXiv preprint arXiv:2404.06474, 2024.

Viraj Prabhu, Yutong Dai, Matthew Fernandez, Jing Gu, Krithika Ramakrishnan, Yanqi Luo, Silvio Savarese, Caiming Xiong, Junnan Li, Zeyuan Chen, et al. Walt: Web agents that learn tools. arXiv preprint arXiv:2510.01524, 2025.

Tianlin Shi, Andrej Karpathy, Linxi Fan, Jonathan Hernandez, and Percy Liang. World of bits: An open-domain platform for web-based agents. In Doina Precup and Yee Whye Teh (eds.), Proceedings of the 34th International Conference on Machine Learning, volume 70 of Proceedings of Machine Learning Research, pp. 3135–3144. PMLR, 06–11 Aug 2017.

Paloma Sodhi, SRK Branavan, Yoav Artzi, and Ryan McDonald. Step: Stacked llm policies for web actions. arXiv preprint arXiv:2310.03720, 2023.

Xingyao Wang, Boxuan Li, Yufan Song, Frank F. Xu, Xiangru Tang, Mingchen Zhuge, Jiayi Pan, Yueqi Song, Bowen Li, Jaskirat Singh, Hoang H. Tran, Fuqiang Li, Ren Ma, Mingzhang Zheng, Bill Qian, Yanjun Shao, Niklas Muennighoff, Yizhe Zhang, Binyuan Hui, Junyang Lin, and et al. Openhands: An open platform for AI software developers as generalist agents. In The Thirteenth International Conference on Learning Representations, ICLR 2025, Singapore, April 24-28, 2025. OpenReview.net, 2025a.

Zora Zhiruo Wang, Jiayuan Mao, Daniel Fried, and Graham Neubig. Agent workflow memory. In Forty-second International Conference on Machine Learning, ICML 2025, Vancouver, BC, Canada, July 13-19, 2025, pp. 63897–63911. OpenReview.net, 2025b.

Haoran Wei, Yaofeng Sun, and Yukun Li. Deepseek-ocr: Contexts optical compression. arXiv preprint arXiv:2510.18234, 2025.

Jialong Wu, Wenbiao Yin, Yong Jiang, Zhenglin Wang, Zekun Xi, Runnan Fang, Linhai Zhang, Yulan He, Deyu Zhou, Pengjun Xie, and Fei Huang. Webwalker: Benchmarking llms in web traversal. In Wanxiang Che, Joyce Nabende, Ekaterina Shutova, and Mohammad Taher Pilehvar (eds.), Proceedings of the 63rd Annual Meeting of the Association for Computational Linguistics (Volume 1: Long Papers), ACL 2025, Vienna, Austria, July 27 - August 1, 2025, pp. 10290–10305. Association for Computational Linguistics, 2025.

Tianci Xue, Weijian Qi, Tianneng Shi, Chan Hee Song, Boyu Gou, Dawn Song, Huan Sun, and Yu Su. An illusion of progress? assessing the current state of web agents. arXiv preprint arXiv:2504.01382, 2025.

Ke Yang, Yao Liu, Sapana Chaudhary, Rasool Fakoor, Pratik Chaudhari, George Karypis, and Huzefa Rangwala. Agentoccam: A simple yet strong baseline for llm-based web agents. In The Thirteenth International Conference on Learning Representations, ICLR 2025, Singapore, April 24-28, 2025. OpenReview.net, 2025.

Shunyu Yao, Jeffrey Zhao, Dian Yu, Nan Du, Izhak Shafran, Karthik R. Narasimhan, and Yuan Cao. React: Synergizing reasoning and acting in language models. In The Eleventh International Conference on Learning Representations, ICLR 2023, Kigali, Rwanda, May 1-5, 2023. OpenReview.net, 2023.

Xiao Yu, Baolin Peng, Vineeth Vajipey, Hao Cheng, Michel Galley, Jianfeng Gao, and Zhou Yu. Exact: Teaching AI agents to explore with reflective-mcts and exploratory learning. In The Thirteenth International Conference on Learning Representations, ICLR 2025, Singapore, April 24-28, 2025. OpenReview.net, 2025.

Yao Zhang, Zijian Ma, Yunpu Ma, Zhen Han, Yu Wu, and Volker Tresp. Webpilot: A versatile and autonomous multi-agent system for web task execution with strategic exploration. In Toby Walsh, Julie Shah, and Zico Kolter (eds.), AAAI-25, Sponsored by the Association for the Advancement of Artificial Intelligence, February 25 - March 4, 2025, Philadelphia, PA, USA, pp. 23378–23386. AAAI Press, 2025. doi: 10.1609/AAAI.V39I22.34505.

Andy Zhou, Kai Yan, Michal Shlapentokh-Rothman, Haohan Wang, and Yu-Xiong Wang. Language agent tree search unifies reasoning, acting, and planning in language models. In Forty-first International Conference on Machine Learning, ICML 2024, Vienna, Austria, July 21-27, 2024, pp. 62138–62160. OpenReview.net, 2024a.

Shuyan Zhou, Frank F. Xu, Hao Zhu, Xuhui Zhou, Robert Lo, Abishek Sridhar, Xianyi Cheng, Tianyue Ou, Yonatan Bisk, Daniel Fried, Uri Alon, and Graham Neubig. Webarena: A realistic web environment for building autonomous agents. In The Twelfth International Conference on Learning Representations, ICLR 2024, Vienna, Austria, May 7-11, 2024. OpenReview.net, 2024b.

### A RELATED WORK

Paradigm 1: Online Exploratory Search. This paradigm attempts to mitigate Topological Blindness through search-based algorithms during task execution. Specifically, these approaches employ best-first search and Monte Carlo Tree Search (MCTS) guided by model-based value functions (Zhou et al., 2024a; Koh et al., 2025). To enhance reliability, reflective mechanisms leverage contrastive reflection or multi-agent debate to correct navigation errors (Pan et al., 2024; Yu et al., 2025). Additionally, other works utilize hierarchical structures to optimize local execution (Zhang et al., 2025; He et al., 2025). However, such exploration is transient and environment-agnostic, discarding the acquired structural knowledge after each episode. Unlike these reactive exploration approaches, our framework persists environmental knowledge into a reusable interaction graph.

Paradigm 2: Learned Internal Planning and World Models. This paradigm attempts to mitigate Topological Blindness by learning environmental knowledge within model parameters or building world models. One significant direction is the construction of explicit environmental simulators, which learn world models to predict state transitions(Gu et al., 2025; Chae et al., 2025). Another approach focuses on structural planning, utilizing policy stacks or planning trees to recursively break down long-term objectives(Sodhi et al., 2023; Yang et al., 2025). To improve quality, specialized architectures separate high-level planning from low-level execution(Erdogan et al., 2025). Despite their efficiency, these methods rely on probabilistic inference often yielding hallucinated transitions in novel environments. In contrast, our Retrieve-and-Navigate paradigm bypasses the need for unreliable internal simulations by grounding agent decisions in pre-constructed environmental knowledge.

### B IMPLEMENTATION DETAILS AND HYPERPARAMETER SETTINGS

Table 2 presents the hyperparameters used across both benchmarks. For WebArena, we conduct a systematic exploration up to depth 3, yielding Interaction Graphs that provide comprehensive coverage of the five websites. Detailed node statistics for each domain are provided in the table. For Online-Mind2Web, which encompasses 136 diverse real-world websites, we adopt a conservative exploration depth of 1. This strategy ensures broad coverage across all 136 websites within a limited time frame, capturing the primary Interaction Graph of each website. Despite this conservative setting, WebNavigator achieves SOTA performance as shown in Table 1, demonstrating that even shallow exploration enables effective generalization from controlled environments to real-world websites.

<div style="text-align: center;"><div style="text-align: center;">Table 2: Experimental configuration for WebArena and Online-Mind2Web benchmarks</div> </div>




<table border=1 style='margin: auto; word-wrap: break-word;'><tr><td style='text-align: center; word-wrap: break-word;'>Parameter</td><td style='text-align: center; word-wrap: break-word;'>Map</td><td style='text-align: center; word-wrap: break-word;'>CMS</td><td style='text-align: center; word-wrap: break-word;'>Shopping</td><td style='text-align: center; word-wrap: break-word;'>GitLab</td><td style='text-align: center; word-wrap: break-word;'>Reddit</td><td style='text-align: center; word-wrap: break-word;'>Online-Mind2Web</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Exploration Depth</td><td style='text-align: center; word-wrap: break-word;'>1</td><td style='text-align: center; word-wrap: break-word;'>2</td><td style='text-align: center; word-wrap: break-word;'>2</td><td style='text-align: center; word-wrap: break-word;'>2</td><td style='text-align: center; word-wrap: break-word;'>3</td><td style='text-align: center; word-wrap: break-word;'>1</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Graph Nodes</td><td style='text-align: center; word-wrap: break-word;'>16</td><td style='text-align: center; word-wrap: break-word;'>122</td><td style='text-align: center; word-wrap: break-word;'>570</td><td style='text-align: center; word-wrap: break-word;'>838</td><td style='text-align: center; word-wrap: break-word;'>225</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Observation</td><td style='text-align: center; word-wrap: break-word;'></td><td style='text-align: center; word-wrap: break-word;'></td><td style='text-align: center; word-wrap: break-word;'></td><td style='text-align: center; word-wrap: break-word;'>Acc. Tree + Image</td><td style='text-align: center; word-wrap: break-word;'></td><td style='text-align: center; word-wrap: break-word;'></td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Selector</td><td style='text-align: center; word-wrap: break-word;'></td><td style='text-align: center; word-wrap: break-word;'></td><td style='text-align: center; word-wrap: break-word;'></td><td style='text-align: center; word-wrap: break-word;'>Gemini-2.5-Flash</td><td style='text-align: center; word-wrap: break-word;'></td><td style='text-align: center; word-wrap: break-word;'></td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Selector Input</td><td style='text-align: center; word-wrap: break-word;'></td><td style='text-align: center; word-wrap: break-word;'></td><td style='text-align: center; word-wrap: break-word;'>Intent  $ i $ + top- $ k $ candidate screenshots</td><td style='text-align: center; word-wrap: break-word;'></td><td style='text-align: center; word-wrap: break-word;'></td><td style='text-align: center; word-wrap: break-word;'></td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Retriever</td><td style='text-align: center; word-wrap: break-word;'></td><td style='text-align: center; word-wrap: break-word;'></td><td style='text-align: center; word-wrap: break-word;'></td><td style='text-align: center; word-wrap: break-word;'>Jina-embedding-v4</td><td style='text-align: center; word-wrap: break-word;'></td><td style='text-align: center; word-wrap: break-word;'></td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Top- $ k $</td><td style='text-align: center; word-wrap: break-word;'></td><td style='text-align: center; word-wrap: break-word;'></td><td style='text-align: center; word-wrap: break-word;'></td><td style='text-align: center; word-wrap: break-word;'>30</td><td style='text-align: center; word-wrap: break-word;'></td><td style='text-align: center; word-wrap: break-word;'></td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Temperature</td><td style='text-align: center; word-wrap: break-word;'></td><td style='text-align: center; word-wrap: break-word;'></td><td style='text-align: center; word-wrap: break-word;'></td><td style='text-align: center; word-wrap: break-word;'>0.5</td><td style='text-align: center; word-wrap: break-word;'></td><td style='text-align: center; word-wrap: break-word;'></td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Top- $ p $</td><td style='text-align: center; word-wrap: break-word;'></td><td style='text-align: center; word-wrap: break-word;'></td><td style='text-align: center; word-wrap: break-word;'></td><td style='text-align: center; word-wrap: break-word;'>0.95</td><td style='text-align: center; word-wrap: break-word;'></td><td style='text-align: center; word-wrap: break-word;'></td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Max Steps</td><td style='text-align: center; word-wrap: break-word;'></td><td style='text-align: center; word-wrap: break-word;'></td><td style='text-align: center; word-wrap: break-word;'></td><td style='text-align: center; word-wrap: break-word;'>20</td><td style='text-align: center; word-wrap: break-word;'></td><td style='text-align: center; word-wrap: break-word;'></td></tr></table>

### C ALGORITHM DETAILS

Algorithm 1 presents the complete procedure for constructing the Interaction Graph G. Structural hashing provides deterministic node identification by hashing of the DOM trees and the URL. This primitive supports core mechanisms that optimize exploration. The algorithm employs adaptive structural differencing (Lines 11-15) to optimize exploration efficiency by computing set differences between parent and child structural representations, focusing only on newly emerged elements. To handle non-URL-accessible nodes, the algorithm features an interleaved navigation mechanism (Lines 8-9) that leverages ShortestPath on the partially constructed graph G.

Structural Hashing. Each element  $ e $ in the structural observation  $ o_v^{str} $ is assigned a deterministic hash based on its full XPath $ ^1 $:  $ h_e = \text{MD}^5^2(e_{\text{xpath}}) $. The structural representation of a node is defined as the set of all element hashes:  $ H_v = \{h_e \mid e \in \text{Elements}(v)\} $. Node identity combines this structural fingerprint with URL metadata:  $ id_v = \text{MD}^5(H_v \| \text{url}_v) $. This design ensures that nodes with identical DOM structures and URLs are identified as the same node, while any structural mutation (element addition, removal, or relocation) yields a distinct hash.

Structural Differencing. Given parent node  $ v_{parent} $ and child node  $ v $, we compute structural difference via set subtraction:  $ \Delta H_v = H_v \setminus H_{v_{parent}} $. Since both representations are hash sets, this operation executes in expected  $ O(|H_v|) $ time, avoiding expensive tree alignment algorithms.  $ \Delta H_v $ represents the hashes of elements newly introduced in the child relative to its parent.

Why Interleaved Navigation is Necessary. Unlike explicit graphs where all nodes are directly accessible, many observations lack direct URL access and are reachable only via specific interaction sequences, such as toggling a menu to reveal hidden content. To explore depth  $ d+1 $, the engine must navigate to each parent node  $ v_i $ at depth  $ d $. For nodes lacking direct URL access, navigating to  $ v_i $ requires replaying the action sequence from the root. This path reconstruction requirement introduces the interleaved exploration-navigation paradigm, where the algorithm simultaneously constructs  $ \mathcal{G} $ while utilizing it for pathfinding. Specifically, the engine constructs depth  $ d+1 $ by iterating through each parent node  $ v_i $ at depth  $ d $. To reach each  $ v_i $, the engine computes the shortest path from the root through  $ \mathcal{G} $ and executes the corresponding action sequence. Once  $ v_i $ is materialized in the browser, the engine discovers its children at depth  $ d+1 $. Table 3 summarizes notation used in Algorithm 1 that extends the main text.

Block List. Given that certain websites contain potentially dangerous operations (e.g., account deletion), we employ a Block List mechanism to bypass such hazardous actions during the exploration phase. The block list can be manually configured with regular expression rules to prohibit the exploration of any elements that match the specified criteria. Additionally, external links are automatically excluded.

<div style="text-align: center;"><div style="text-align: center;">Table 3: Additional notation for Algorithm 1</div> </div>




<table border=1 style='margin: auto; word-wrap: break-word;'><tr><td style='text-align: center; word-wrap: break-word;'>Symbol</td><td style='text-align: center; word-wrap: break-word;'>Description</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>$ \mathcal{Q} $</td><td style='text-align: center; word-wrap: break-word;'>BFS queue organized by exploration depth</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>$ \mathcal{L}_{d} $</td><td style='text-align: center; word-wrap: break-word;'>Set of nodes at depth  $ d $</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>$ \mathcal{R} $</td><td style='text-align: center; word-wrap: break-word;'>Set of interested accessibility roles (e.g., link, button)</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>$ \mathcal{I}_{v} $</td><td style='text-align: center; word-wrap: break-word;'>Interactive elements extracted from node  $ v $</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>$ H_{v} $</td><td style='text-align: center; word-wrap: break-word;'>Structural hash set  $ \{h_{e} \mid e \in \text{Elements}(v)\} $</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>$ \Delta H_{v} $</td><td style='text-align: center; word-wrap: break-word;'>Structural hash difference  $ H_{v} \setminus H_{v_{\text{parent}}} $</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>$ id_{v} $</td><td style='text-align: center; word-wrap: break-word;'>Unique identifier  $ MD5(H_{v} \parallel \text{url}_{v}) $</td></tr></table>

Algorithm 1 Adaptive BFS Exploration with Interleaved Navigation

Require: Start URL url0, Maximum depth D, Interested roles R
Ensure: Interaction Graph G = (V, E)
1: G ← (0, 0)
2: v0 ← Snapshot(url0)
3: V ← {v0}; Q ← [v0]
4: for d = 0 to D - 1 do
5:   Ld ← Q.dequeue(); Ld + 1 ← 0
6:   for each v ∈ Ld do
7:     {— Interleaved Navigation —}
8:     τ ← ShortestPath(v0, v, G)
9:     ostr ← ExecutePath(τ)
10:     {— Adaptive Structural Differencing —}
11:     Iv ← GetInteractiveElements(ostr, R)
12:     if ∃v_parent s.t. (v_parent, a, v) ∈ E then
13:         ΔHv ← Hv \ Hv_parent
14:         Iv ← {e ∈ Iv | h_e ∈ ΔHv}
15:       end if
16:     {— Explore New Elements —}
17:     for each e ∈ Iv do
18:         a ← CreateAction(e)
19:         v' ← ExecuteAndSnapshot(v, a)
20:         idv' ← MD5(Hv' || urlv')
21:         if idv' ∉ {idv | u ∈ V} then
22:         V ← V ∪ {v'}
23:         Ld + 1 ← Ld + 1 ∪ {v'}
24:       end if
25:       E ← E ∪ {(v, a, v')}
26:     end for
27:     end for
28:     Q.enqueue(Ld + 1)
29:   end for
30:   return G

<div style="text-align: center;"><div style="text-align: center;">Table 4: Function descriptions for Algorithm 1</div> </div>




<table border=1 style='margin: auto; word-wrap: break-word;'><tr><td style='text-align: center; word-wrap: break-word;'>Function</td><td style='text-align: center; word-wrap: break-word;'>Description</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Snapshot</td><td style='text-align: center; word-wrap: break-word;'>Captures a comprehensive observation of a specific page state, including both visual and structural representations, and returns an interaction graph node representing this page.</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>ShortestPath</td><td style='text-align: center; word-wrap: break-word;'>Computes the shortest action sequence from the root node  $ v_{0} $ to target node  $ v $ through the partially constructed graph  $ \mathcal{G} $. Returns an ordered action sequence  $ \tau = (a_{1}, \ldots, a_{m}) $.</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>ExecutePath</td><td style='text-align: center; word-wrap: break-word;'>Executes a navigation trajectory  $ \tau $ in the browser. Navigates to the starting node, then sequentially performs each action to reach the target node.</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>GetInteractiveElements</td><td style='text-align: center; word-wrap: break-word;'>Extracts all interactive elements matching the specified accessibility roles  $ \mathcal{R} $ from the structural observation  $ o_{v}^{\mathrm{str}} $.</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>CreateAction</td><td style='text-align: center; word-wrap: break-word;'>Constructs an action to trigger the given interactive element e.</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>ExecuteAndSnapshot</td><td style='text-align: center; word-wrap: break-word;'>Executes action  $ a $ on the web page represented by  $ v $ and returns the Snapshot of the resulting page state.</td></tr></table>

### D INTERACTION GRAPH MAINTENANCE AND INCREMENTAL UPDATE

The Interaction Graph  $ \mathcal{G} $ maintains currency with website evolution through a systematic verification and incremental update mechanism. The heuristic auto-exploration engine periodically executes a verification process that traverses all nodes  $ v \in \mathcal{V} $ and edges  $ e \in \mathcal{E} $ in the Interaction Graph. For each node  $ v $, the engine revisits its corresponding page and validates whether the node remains accessible. Similarly, for each edge  $ e = (v, a, v') \in \mathcal{E} $, the engine verifies whether executing action  $ a $ at node  $ v $ still produces the expected transition to  $ v' $. The engine removes invalid nodes and edges from  $ \mathcal{G} $. For nodes that remain valid after verification, the engine compares the current DOM tree with the cached version to identify newly added elements. The engine then explores from these new elements, adding newly discovered nodes and edges to  $ \mathcal{G} $. This incremental mechanism minimizes redundant exploration while enabling rapid graph updates. Through this mechanism, the Interaction Graph accurately reflects the latest website topology without requiring LLM involvement.

### E Efficiency Analysis

As shown in Table 5, WebNavigator demonstrates superior efficiency across different model configurations. Compared to AgentOccam, WebNavigator consistently reduces the average step count when using GPT-4o, Claude-Sonnet-4, or Gemini-2.5-Pro as backbone models. In the challenging Multi-site tasks, WebNavigator with GPT-4o achieves an average of 13.56 steps, compared to AgentOccam at 15.21, demonstrating substantial efficiency gains on the most complex cross-domain tasks.

<div style="text-align: center;"><div style="text-align: center;">Table 5: The average number of steps taken by different methods on WebArena. Methods marked with * are our reproduced results.</div> </div>




<table border=1 style='margin: auto; word-wrap: break-word;'><tr><td rowspan="2">Method</td><td colspan="2">Avg. Steps</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>All</td><td style='text-align: center; word-wrap: break-word;'>Multi-site</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>WebArena $ ^{*} $</td><td style='text-align: center; word-wrap: break-word;'>7.71</td><td style='text-align: center; word-wrap: break-word;'>13.65</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>AgentOccam $ ^{*} $</td><td style='text-align: center; word-wrap: break-word;'>9.88</td><td style='text-align: center; word-wrap: break-word;'>15.21</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>WebNavigator (Qwen3-VL-32B-Instruct)</td><td style='text-align: center; word-wrap: break-word;'>9.87</td><td style='text-align: center; word-wrap: break-word;'>13.40</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>WebNavigator (GPT-4o)</td><td style='text-align: center; word-wrap: break-word;'>8.97</td><td style='text-align: center; word-wrap: break-word;'>13.56</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>WebNavigator (Claude-Sonnet-4)</td><td style='text-align: center; word-wrap: break-word;'>8.93</td><td style='text-align: center; word-wrap: break-word;'>11.75</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>WebNavigator (Gemini2.5-Pro)</td><td style='text-align: center; word-wrap: break-word;'>8.98</td><td style='text-align: center; word-wrap: break-word;'>10.75</td></tr></table>

### F DETAILED ACTION SPACE COMPARISON ACROSS METHODS

We list the action space of WebArena (Zhou et al., 2024b), BrowserGym (Chezelles et al., 2024), AgentOccam (Yang et al., 2025), SteP (Sodhi et al., 2023) and WebNavigator (ours) in Table 6.

<div style="text-align: center;"><div style="text-align: center;">Table 6: Comprehensive Comparison of Action Spaces across WebArena, BrowserGym, AgentOcam, SteP and WebNavigator.</div> </div>




<table border=1 style='margin: auto; word-wrap: break-word;'><tr><td style='text-align: center; word-wrap: break-word;'>Action Type</td><td style='text-align: center; word-wrap: break-word;'>Description</td><td style='text-align: center; word-wrap: break-word;'>Web Arena</td><td style='text-align: center; word-wrap: break-word;'>Browser Gym</td><td style='text-align: center; word-wrap: break-word;'>Agent Occam</td><td style='text-align: center; word-wrap: break-word;'>SteP</td><td style='text-align: center; word-wrap: break-word;'>Web Navigator</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>click (elem)</td><td style='text-align: center; word-wrap: break-word;'>Click at an element</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>✓</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>type (elem, text)</td><td style='text-align: center; word-wrap: break-word;'>Type to an element</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>✓</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>go_back</td><td style='text-align: center; word-wrap: break-word;'>Visit the last URL</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>✓</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>note (content)</td><td style='text-align: center; word-wrap: break-word;'>Take notes</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>✓</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>stop (answer)</td><td style='text-align: center; word-wrap: break-word;'>Stop with an answer</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>✓</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>navigate (domain, query)</td><td style='text-align: center; word-wrap: break-word;'>Teleport via multimodal retrieval</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>✓</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>noop</td><td style='text-align: center; word-wrap: break-word;'>Do nothing</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>hover (elem)</td><td style='text-align: center; word-wrap: break-word;'>Hover on an element</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>press (key_comb)</td><td style='text-align: center; word-wrap: break-word;'>Press a key combination</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>scroll (dir)</td><td style='text-align: center; word-wrap: break-word;'>Scroll up and down</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>tab_focus (index)</td><td style='text-align: center; word-wrap: break-word;'>Focus on the i-th tab</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>new_tab</td><td style='text-align: center; word-wrap: break-word;'>Open a new tab</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>tab_close</td><td style='text-align: center; word-wrap: break-word;'>Close current tab</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>go_forward</td><td style='text-align: center; word-wrap: break-word;'>Undo go_back</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>goto (URL)</td><td style='text-align: center; word-wrap: break-word;'>Go to URL</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>dbclick (elem)</td><td style='text-align: center; word-wrap: break-word;'>Double-click at an element</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>clear (elem)</td><td style='text-align: center; word-wrap: break-word;'>Clear the content</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>focus (elem)</td><td style='text-align: center; word-wrap: break-word;'>Set the focus to an element</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>select_option (elem)</td><td style='text-align: center; word-wrap: break-word;'>Select elements within menu</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>drag_and_drop (elem)</td><td style='text-align: center; word-wrap: break-word;'>Drag and drop element to another</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>upload_file (elem, file)</td><td style='text-align: center; word-wrap: break-word;'>Upload files</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>send_msg_to_user (text)</td><td style='text-align: center; word-wrap: break-word;'>Send a message to the user</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>report_infeasible (text)</td><td style='text-align: center; word-wrap: break-word;'>Report instructions are infeasible</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>go_home</td><td style='text-align: center; word-wrap: break-word;'>Go to home page</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>branch (id, intent)</td><td style='text-align: center; word-wrap: break-word;'>Generate a new plan</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>prune (id, reason)</td><td style='text-align: center; word-wrap: break-word;'>Restore to a previous plan</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>find_commits (query)</td><td style='text-align: center; word-wrap: break-word;'>Search commits in a project</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>search_issues (query)</td><td style='text-align: center; word-wrap: break-word;'>Search and filter issues</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>create_project (query)</td><td style='text-align: center; word-wrap: break-word;'>Create project and add members</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>create_group (query)</td><td style='text-align: center; word-wrap: break-word;'>Create group and add members</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>find_subreddit (query)</td><td style='text-align: center; word-wrap: break-word;'>Find specific or relevant subreddit</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>find_user (user_name)</td><td style='text-align: center; word-wrap: break-word;'>Navigate to a user&#x27;s page</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>find_review (query)</td><td style='text-align: center; word-wrap: break-word;'>Find reviews for a product</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>find_order (query)</td><td style='text-align: center; word-wrap: break-word;'>Find order by customerid</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>search_customer (query)</td><td style='text-align: center; word-wrap: break-word;'>Search customers by query details</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>search_order (question)</td><td style='text-align: center; word-wrap: break-word;'>Search orders to answer questions</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>list_products (query)</td><td style='text-align: center; word-wrap: break-word;'>List products matching query</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>search_reviews (query)</td><td style='text-align: center; word-wrap: break-word;'>Search reviews for answers</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>find_directions (query)</td><td style='text-align: center; word-wrap: break-word;'>Find directions between locations</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>search_nearest (query)</td><td style='text-align: center; word-wrap: break-word;'>Find places near a location</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>-</td><td style='text-align: center; word-wrap: break-word;'>✓</td><td style='text-align: center; word-wrap: break-word;'>-</td></tr><tr><td style='text-align: center; word-wrap: break-word;'>Total Actions</td><td style='text-align: center; word-wrap: break-word;'></td><td style='text-align: center; word-wrap: break-word;'>12</td><td style='text-align: center; word-wrap: break-word;'>20</td><td style='text-align: center; word-wrap: break-word;'>8</td><td style='text-align: center; word-wrap: break-word;'>27</td><td style='text-align: center; word-wrap: break-word;'>6</td></tr></table>

### G PROMPT

### G.1 SYSTEM PROMPT

The system prompt defines the behavioral logic of WebNavigator via three integrated components: the Explore-Act Model, which distinguishes between exploration and local action phases; the Output Specifications detailed in Appendix G.2, which facilitate structured Chain-of-Thought reasoning; and the Action Space Definition detailed in Appendix G.3, which specifies the executable actions available to the agent.

#### System Prompt

You are an AI assistant performing tasks on a web browser. You will be provided with task objective, current step, web page observations, and other relevant information. You need to issue an action for this step.

Web Navigation Philosophy: The Explore-Act Model

Your operation must follow a strict two-phase model: the Exploration Phase and the Low-Level Action Phase. Any task can be composed of an Exploration Phase and a Low-Level Action Phase.

Covers Scenario 1: Explore -> low-level action

Covers Scenario 2: Explore -> low-level actions -> Explore -> low-level actions (can be repeated N times)

Covers Scenario 3: Explore (Domain A) -> low-level actions (Domain A) -> Explore (Domain B) -> low-level actions (Domain B) (can be repeated N times)

An Exploration Phase begins when you need to find a new starting point for a task or sub-task, and the current page is inefficient for direct navigation.

You MUST use optimal_navigate tool to initiate any Exploration Phase, which occurs when:

A) Starting a New Task: You are at the beginning of a mission, and the current page (e.g., start page or homepage) is not your target workspace.

Covers Scenario 1: Explore -> low-level action

B) Transitioning to a New Sub-Task (Single Domain): You have completed one part of a task and must now navigate to a completely different functional area of the same website to begin the next part.

Example: After successfully adding a new product in shopping_admin, your next goal is to create a marketing campaign for it. You would use this tool to jump from the Product Catalog to the Marketing Promotions page.

Covers Scenario 2: Explore -> low-level actions -> Explore -> low-level actions

C) Transitioning to a New Sub-Task (Cross-Domain): You have completed a sub-task on one website and now need to switch to a different domain to continue the mission.

Covers Scenario 3: Explore (Domain A) -> low-level actions (Domain A) -> Explore (Domain B) -> low-level actions (Domain B)

This phase involves direct interaction with the elements on the current page to achieve a specific goal. A) You are interacting with page elements: This includes filling out forms, clicking buttons within a modal, typing into search bars, selecting from dropdowns, or modifying data on the current page.

B) You are already on the correct page: If the target is already visible and reachable via one clicks, perform those clicks manually.

optimal_navigate is STRICTLY FORBIDDEN during the Low-Level Action Phase. Critical Rule:

Once you’ve used optimal_navigate to reach a page, this means the Exploration Phase is over, and the next step must be the Low-Level Action Phase. DO NOT use it again until you complete the current sub-task, or you will be severely punished!

Each task must invoke optimal_navigate at least once. Repeated calls to optimal_navigate are prohibited.

optimal_navigate is a retrieval engine that pre-explores website structures based on visual webpage features. Therefore, when you create a new repository, publish a new post, or list a new product, optimal_navigate cannot retrieve these newly created items. Any content written to the database will not be discoverable by optimal_navigate.In such cases, you must utilize the website's search bar to retrieve the newly created repository, published post, or listed product

STRICT RULE: Use stop exclusively for final answers. Never use it for intermediate notes; use the note action for that purpose.

Generate the response in the following format:

{output specifications}

CRITICAL FORMATTING RULE - VIOLATION WILL RESULT IN IMMEDIATE TERMINATION:

You are FORBIDDEN from generating ANY text surrounded by double asterisks (**) such as **AC-TION:**, **REASON:**, **PLAN:**, **OBJECTIVE:**, or similar bold markdown formatting.

This includes but is not limited to: **ACTION:**, **REASON:**, **PLAN:**, **OBJECTIVE:**, **ANALYSIS:**, **DECISION:**, **RESPONSE:**, **NEXT STEP:**, or any other capitalized words in double asterisks.

If you generate even ONE instance of this forbidden format, your response will be rejected and you will receive severe punishment.

Instead, always output content directly without any markdown formatting, headers, or structural markers.

You are ONLY allowed to use the following action commands. Strictly adheres to the given format. Only issue one single action.

{Action Space Definition}

### G.2 OUTPUT SPECIFICATIONS

This section dictates the structured format for the response of agent, requiring explicit fields for context retention, history summarization, and step-by-step reasoning. It enforces a chain-of-thought process to validate current observations and mode analysis before the final action is generated.

#### Output Specifications

REPEAT FINAL OBJECT:

Repeat the original objective to maintain context clarity across multiple react iterations.

Output the original objective exactly as it was stated at the beginning, ensuring all details and requirements are preserved. This helps prevent context degradation and keeps the LLM focused on the original goal in long-running interactions.

##### EXTRACT CONSTRAINTS:

Extract and classify ALL specific requirements from the objective in EXTRACT CONSTRAINTS section. Be exhaustive - identify every constraint, filter, or requirement mentioned or implied.

- time constraints: Any temporal requirements (e.g. years: “2022”, dates: “March 15”, periods: “last month”, “Q1”, “this week”, ranges: “between 2021-2023”)

- quantity constraints: Rankings (e.g. “top-1”, “most popular”), limits (e.g. “at least 5”, “maximum 10”), comparisons (e.g. “cheaper than $70”, “higher rated than 6.5”)

- entity constraints: Specific names, IDs, categories (e.g. “order #12345”, “electronics category”, “user john_doe”, “product X123”)

- logic constraints: Boolean conditions, dependencies (e.g. “only if”, “unless”, “when”, “except”, “provided that”)

- other condition constraints: Constraints that do not belong to the above categories Rules:

1. Be literal: Extract exact words/phrases from objective - don't interpret or infer beyond what's stated

2. Categorize precisely: Each constraint MUST be assigned to exactly one category above

3. Include implicit: If objective implies a constraint (e.g., "find the best" implies ranking), include it

4. No duplicates: Same constraint should only appear once, even if mentioned multiple times

5. Do not infer the completion status of tasks; this section is only for information extraction.

##### INTERACTION HISTORY SUMMARY:

Provide a precise step-by-step summary of EVERY interaction shown in the INTERACTION HISTORY section. This summary MUST maintain perfect numerical alignment with the actual history steps.

Strict Constraints:

1. Full Sequential Summary: You must summarize every step in the history in chronological order. It is strictly forbidden to skip any steps, even if a step is repetitive, failed, or part of a loop.

2. Pure Objective Description: This section only allows factual statements. It is strictly prohibited to include any reasoning, subjective thoughts, or future plans. Simply describe what action was taken and what result was produced.

3. Action Summarization, Not Copying: Use concise language to summarize the intent of the action. It is strictly forbidden to directly copy the original action commands (e.g., click [123]); instead, describe it as “Clicked the Add to Cart button.”

4. Historical Scope Only: Only summarize steps that have already been completed (Step 1 to Step N-1). Do not summarize the current step you are performing.

5. Retain Core Details: Ensure you capture key details from each step that affects the current state (e.g., specific values entered, specific filters selected, etc.).

#### CRITICAL STEP NUMBERING RULES:

- Count the EXACT number of <step_X_interaction> blocks in the INTERACTION HISTORY section

- If you see <step_0_interaction>, <step_1_interaction>, <step_2_interaction>, then summarize as "Step 0:", "Step 1:", "Step 2:".

- If the history contains steps 0 through N-1, your summary MUST contain exactly steps 0 through N-1

- NEVER skip step numbers, NEVER start from Step 1 if Step 0 exists, NEVER add extra steps

Mandatory Verification Process:

1. Count History Steps: Identify all <step_X_interaction> blocks and note their exact numbers

3. Complete Coverage: Summarize every single step shown in the history, no exceptions

Content Requirements:

- Factual Only: Pure objective description of what action was taken and what result occurred

- Concise Language: Summarize the intent, not the raw commands (e.g., “Clicked the search button” not “click [123]”)

- Key Details: Include critical information that affects current state (values entered, pages navigated to, etc.)

- No Future Plans: Only describe completed actions, never predict or suggest next steps.

Exact output format:

If current step = 0:

No history interactions available. This is the beginning of the task.

If current step > 0:

Step X: [Concise factual summary of action and result]

Step Y: [Concise factual summary of action and result]

(Where X, Y match the exact step numbers from INTERACTION HISTORY)

##### OBSERVATION DESCRIPTION:

Describe information in the CURRENT OBSERVATION section. Emphasize elements and features that are relevant or potentially helpful for fulfilling the objective in detail.

Strict Constraints:

1. Zero Reasoning: It is strictly prohibited to include any form of reasoning, interpretation, or assessment of task completion. Any inference in this section, however small, will negatively bias subsequent reasoning steps.

2. Precise Element Referencing: You MUST mention the specific element names along with their corresponding BIDs (e.g., [123]). This is critical for accurate element localization.

3. Pure Factual Description: Focus solely on describing the visible layout, text content, and interactive elements exactly as they appear on the page.

##### MODE ANALYSIS:

Analyze the current situation in MODE_ANALYSIS section to determine the operational mode. Base your analysis STRICTLY on the information provided in the previous context and observations. Your analysis MUST be a step-by-step reasoning process that answers the following questions before making a final conclusion:

1. Current Page vs. Objective: Is the current page directly useful for achieving the task objective? Does it contain the necessary tools (e.g., date filters, specific input fields, detailed data tables) required by the constraints?

2. Task Progress: Where are we in the overall task? Is this the very first step? Or have we just completed a sub-task and are about to start a new one?

3. Information Sufficiency: Based on the provided context, do we have enough information about the current page to proceed with specific actions, or do we need to explore and gather more information first?

##### CRITICAL CONSTRAINTS:

- You MUST base your mode analysis ONLY on the information already provided in the context

- You are FORBIDDEN from reasoning about or suggesting specific actions to execute

- Your role is SOLELY to predict which operational mode (EXPLORATION or LOW-LEVEL AC-

TION) would be most appropriate for the next phase of task completion

- Focus on mode prediction. NOT action planning

After your step-by-step reasoning, you MUST conclude your analysis on a new line with the format: Conclusion: The current mode is [MODE], where [MODE] is either EXPLORATION or LOW-LEVEL ACTION.
REASON:
Provide your rationale for proposing the subsequent action commands here.
CRITICAL: When generating actions with parameters, you MUST mention the corresponding web element's BID (element ID) and describe the spatial context in your reasoning. This ensures accurate action execution.
Key points:
- Always mention the BID (e.g., [3164]) in your reasoning
- Describe the spatial context (e.g., “top navigation area”, “main content”, “sidebar”)
- Explain what element you're interacting with (e.g., “Search GitLab textbox”)
e.g. Objective is “most recent”, but UI is “Sort by: Hot”. Reasoning: “Current view is sorted by popularity, not time. I must click the Sort button to switch to Newest to ensure I find the truly latest posts.”
ACTION:
Select your action here.

### G.3 ACTION SPACE DEFINITION

This section provides detailed action definitions and associated prompts for WebNavigator introduced in Section 3.3. Our agent operates with six actions in total. The high-level navigate(domain, query) action discussed in the methodology is implemented optimal_navigate[think][domain][query]. Table 6 compares our action space with prior methods.

Action Space Definition
CLICK:
click [id]: To click on an element with its numerical ID on the webpage. E.g., click [7] If clicking on a specific element doesn't trigger the transition to your desired web state, this is due to the element's lack of interactivity or GUI visibility. In such cases, move on to interact with OTHER similar or relevant elements INSTEAD.
TYPE:
type [id] [content] [press_enter_after=011]: To type content into a field with a specific ID. By default, the "Enter" key is pressed after typing unless press_enter_after is set to 0. E.g., type [15] [Carnegie Mellon University] [1]. If you can't find what you're looking for on your first attempt, consider refining your search keywords by breaking them down or trying related terms. To search for a GitLab user: type [2556] [user] [1]. To search for a project: type [2556] [project name] [1]. For example, type [2556] [keycloak/keycloak] [1]. Combining a search for an author and a project will fail. Identify what you're searching for (user, project, issue, etc.) and search for only that type of information.
STOP:
stop [answer]: To stop interaction and return response. Present your answer within the brackets. If the task doesn't require a textual answer or appears insurmountable, indicate "N/A" and additional reasons and all relevant information you gather as the answer. E.g., stop [5h 47min]

STRICT RULE: Use stop exclusively for final answers. Never use it for intermediate notes; use the note action for that purpose.
NOTE:
note [content]: To take note of all important info w.r.t. completing the task to enable reviewing it later. E.g., note [Spent $10 on 4/1/2024]
GO BACK:
go_back: To return to the previously viewed page.
OPTIMAL NAVIGATE
optimal_navigate [thinking][domain][query]:

Teleports the agent to a specific webpage by performing a visual-semantic search over pre-indexed screenshots of the website. Use this to jump between different functional areas (e.g., from Dashboard to Settings) without repeatedly clicking.

Argument Construction:

You must TRANSLATE the user's task into a VISUAL PAGE DESCRIPTION. Since the retrieval tool works by matching screenshots, you must simulate the visual environment of the target page.

1. **Length & Detail**: The thinking block MUST be at least 50 words. Do not be concise.

2. **Context Integration**: You must explicitly analyze the **User’s Objective** (Key entities, IDs, dates) and the **Current Observation** (Why is the current page not good enough?).

3. Domain Justification: You must explicitly state which of the 5 domains (shopping_admin, map, gitlab, reddit, shopping) is the correct target. Justify your choice based on the user's goal. For example, "The user wants to manage products, so I must use shopping_admin, not the customer-facing shopping site." This justification is mandatory.

4. **Visual Simulation**: Before generating the query, vividly describe what you expect to see on the target page. Mention specific UI elements like “tables with Status column”, “forms with Refund button”, “Sidebars with Analytics tab”.

Examples:

1. User Task: "Check Reddit for news then post on Twitter." Current Goal: Check Reddit.

Output: optimal_navigate [The immediate sub-goal is to browse news on Reddit. I am currently not on the Reddit domain. Based on the user's request to Check Reddit, the correct domain is Reddit. I need to jump to the specific subreddit listing page. The target page is not a specific post, but a feed. I expect to see the standard Reddit layout: a header with r/news, a feed of post titles, upvote arrows, and thumbnails.] [reddit] [news subreddit listing page with top stories]

[domain]:

Select the domain parameter based on the website required for the current task. For tasks that span multiple websites, you will need to use different domains for different sub-tasks, which means you must break down the overall mission appropriately.

The visual-semantic search string describing the TARGET PAGE STATE.

- Good: “Order #555 details page with refund option” (Describes the place)

- Bad: “Refund order #555” (Describes the action)

The query must include the critical conditions and entities from the source objection. If an entity needs to be inferred, first deduce a more suitable entity and its corresponding conditions in the [thinking] section before including them in the query. This will significantly enhance retrieval effectiveness.

Output format:

- You must strictly follow this format with NO extra text:

optimal_navigate [Your detailed visual reasoning (min 50 words) analyzing objective vs observation] [domain] [The visual-semantic description of the page]

### G.4 SELECTOR PROMPT

As the reasoning component within the Global-View Navigator, the Selector performs a three-step selection process on retrieved candidates: (1) Information sufficiency Analysis, (2) Relevance Filtering, and (3) Operational Efficiency Ranking.

#### Selector Prompt

You are a Goal-Oriented Navigation Module for an intelligent Web Agent. Your primary directive is to select a webpage that provides the most direct path to achieving the user's ultimate goal, prioritizing data completeness over superficial UI convenience.

Information: You will receive three types of information:

user_objective: The user's final, high-level task goal. This is your strategic compass.

retrieval_query: The description of intermediate steps generated by the Agent. This is your tactical instruction.

candidate_pages: A series of webpage screenshots retrieved based on the retrieval_query.

Core Task: Your decision process is a strict, hierarchical three-step evaluation:

Data Sufficiency Analysis (Highest Priority): First, evaluate each candidate page against the user_objective. Determine if the data presented on the page is complete and sufficient to answer the user's final question. A page showing a subset of data (e.g., a Shipped Orders page when the goal is

to find a customer’s *entire order history*, or a Top 10 Customers report when the goal is to find a specific customer by name) is considered insufficient and must be deprioritized.

CRITICAL: If NONE of the candidate pages provide sufficient data or match the user’s objective, you must strictly decide to return “None”. Do not force a selection from irrelevant or incomplete pages.

Relevance Filtering: Among the pages deemed to have sufficient data, use the retrieval_query as a benchmark to filter for pages that have the necessary UI elements and functionality for the next action. Operational Efficiency Ranking: Finally, from the remaining candidates, choose the one that allows you to complete the user_objective with the fewest subsequent operations. Remember, a path leading to an incomplete or incorrect answer is infinitely inefficient.

##### Reasoning Requirements:

At the beginning of your reasoning, you must explicitly restate the user_objective and retrieval_query. Your analysis for each key candidate must follow the three-step evaluation (Sufficiency -> Relevance -> Efficiency). You must explicitly state your conclusion about the data sufficiency of each page.

Clearly compare the pages, explaining why one is chosen over others based on this hierarchy. For instance:1) While page A is titled Products on Sale and has a very prominent search bar, its data is limited only to discounted items. This makes it insufficient for the user's objective of finding the price of *any* product. Page B, titled Product Catalog, contains all products and is therefore the correct choice, even if its search function is less obvious.

2) User Goal: Post in /f/relationship_advice. Comparison: 1. Page A (titled Create new page) is rejected immediately because it is for creating Wikis, not user posts. 2. Page B (titled Create submission) is relevant but inefficient. Its Forum dropdown shows Choose one..., meaning the agent must manually search and select the subreddit (2 extra steps). 3. Page C (the /f/relationship_advice forum index) is the optimal target. By selecting this page, the subsequent Submit action will inherit the current context and auto-fill the forum parameter, achieving the goal with minimum operations.

If no suitable page is found, explicitly explain why all candidates failed the Data Sufficiency or Relevance criteria.

Strict Output Format: Please output strictly according to the following JSON format.

{ "reasoning": "your reasoning content", "target_page": "image name OR None"}

JSON Format Mandatory Requirements - Must Be Strictly Followed:

The value of the reasoning field must be a continuous line of text, without any line breaks. Do not use Chinese quotation marks; if quoting is needed, use English single quotes.

Do not use backslash escape characters in strings. The target_page value must be the specific image name if a suitable page is found, or the string "None" if no page meets the criteria.

Output pure JSON directly, without any other content, and do not wrap it in markdown code blocks.

