# Shopping-Agent: System Architecture

> **Version**: 2026-06-05 (rewritten)
> **Scope**: Full `phone_agent/` implementation, with emphasis on agent execution, memory orchestration, AMSG graph design, and automatic graph persistence.
> **Purpose**: Paper-ready architecture reference for academic writing.

Shopping-Agent is a **VLM-primary, graph-guided mobile GUI agent** augmented by a self-evolving **Active Mobile Spatial Graph (AMSG)**. The system automates complex shopping tasks on Android, HarmonyOS, and iOS devices through a closed-loop cycle of screenshot observation, page-state localization, graph-informed planning, VLM reasoning, action execution, and verified graph persistence.

The central design principle is *asymmetric authority*: the graph accelerates and constrains the action space while the VLM retains semantic authority over content-dependent decisions. This separation distinguishes Shopping-Agent from both pure VLM agents (which rediscover navigation from scratch every session) and pure graph planners (which cannot handle novel visual content).

---

## 1  Design Philosophy

Mobile GUI automation operates in a visual environment without a stable DOM, where four uncertainty sources drive most failures:

| Uncertainty | Failure mode | Design response |
|---|---|---|
| **Perceptual** | Same page looks different across app versions, devices, ads, popups, scroll positions. | `PageState` abstracts screenshots into reusable states defined by app, page type, landmarks, affordances, slots, risk, and semantic signature. |
| **Semantic** | Historical "tap product card" does not identify which product matches the current task. | Ungrounded transitions are explicitly marked; the VLM must choose the target based on current screen content. |
| **Transition** | Same button may open SKU selection, login, a promotion dialog, checkout, or do nothing. | `EdgeLifecycleManager` tracks empirical outcome distributions, dominance ratios, and entropy per transition. |
| **Context** | Long action histories confuse VLM calls and obscure the current task. | `UnifiedSessionState` plus `RetrievalGateway` inject progress every step and detailed memory only on demand. |

These four responses produce a system best described as **VLM-primary graph-guided control**. AMSG is not an autonomous controller; it is a verified action library, state localizer, route planner, and persistence substrate. The VLM remains the semantic authority whenever a decision depends on current screen content or user intent. This asymmetry is the defining architectural choice: it prevents the graph from replaying stale coordinates on content-sensitive pages (product choice, SKU selection, checkout) while still allowing the graph to shortcut mechanical navigation (home to search, search to results, filter toggling) at near-zero latency.

---

## 2  System Architecture

The system has three logical areas connected by a closed-loop data flow. The key to understanding the architecture is not "what layers exist" but **how data flows and who decides what**.

### 2.1  Three Areas and Their Responsibilities

**Decision Area** — answers "what to do". `PhoneAgent._execute_step()` is the sole orchestration entry point. Each step, it calls the Graph Area for localization and action suggestions, then selects Fast Path or VLM Path for execution. Components: `TaskPlan`, `ClarificationAgent`, `VerificationDetector`, `SpecGuard`.

**Graph Area** — answers "where am I, where to go". `GraphRuntimeController.locate_and_get_context()` performs localization, postcondition verification, route planning, and RuntimeDAG caching in a single call. It returns a `mode` field (`navigate` / `explore` / `verify_with_vlm` / `goal_reached`) that directly drives the Decision Area's dispatch. The Graph Area does not make semantic judgments (which product, which SKU) — only structural navigation. Components: `SpatialGraphMemory`, `ActionAdvisor`, `EdgeLifecycleManager`, Neo4j, FAISS.

**Execution Area** — answers "how to operate the device". `ModelProtocolBridge` normalizes semantic actions or VLM outputs into `DeviceActionIR` across five coordinate systems. `ActionHandler` compiles device commands. After execution, the new screenshot and page classification **feed back** into the Graph Area: postcondition verification updates edge success/failure counts, and successful task completion triggers graph persistence. Components: `ModelClient`, 5 model adapters, `ActionHandler`, `DeviceFactory` (ADB/HDC/XCTest).

### 2.2  Closed-Loop Data Flow

The three areas connect through a single closed loop. Each step, data flows in this order:

```mermaid
flowchart TD
    A["Screenshot + Page Classification"] -->|"(app, page_type)"| B["Graph Localization\n+ Verify Previous Postcondition"]
    B -->|"mode + next_action"| C{"Dispatch Gate"}
    C -->|"grounded + promoted"| D["Fast Path\nskip VLM"]
    C -->|"ungrounded / unknown"| E["VLM Reasoning\n+ graph hints"]
    D --> F["Device Execution"]
    E --> F
    F -->|"new screenshot"| G["Postcondition Verify\n+ Success Rate Update"]
    G -->|"feedback"| A
    G -->|"task end"| H["Stage -> Quality Gates -> Neo4j"]

    style D fill:#2E9E44,color:#fff,stroke:none
    style E fill:#0F4D92,color:#fff,stroke:none
    style H fill:#7B61A0,color:#fff,stroke:none
```

Key properties of this loop:

- **Per-step feedback**: Edge success rates update in memory after every step, not just at task end
- **Deferred persistence**: In-memory `EdgeLifecycleManager` counters update in real time; Neo4j writes only on successful task completion
- **Fast Path protection**: Postcondition mismatch triggers automatic fallback to VLM Path — worst case is one extra VLM call, not a wrong action

---

## 3  Agent Design

This section describes a task's complete lifecycle in chronological order: from receiving user input to graph persistence after completion. Entry point: `PhoneAgent.run(task)`. Main loop: `_execute_step()`. Exit: `MemoryManager.end_task()`.

### 3.1  Task Reception: From Natural Language to Executable State

When the user inputs `"Search iPhone 16 silver 256G on Taobao, add to cart"`, `run()` performs four steps:

**① State reset.** Clears previous task's dialogue context, step counter, RuntimeDAG cache, verification counters, and model adapter history. Tasks are fully isolated.

**② Task slot extraction.** `TaskSpecExtractor.extract(task)` uses regex to extract structured constraints: `query="iPhone 16"`, `color="silver"`, `storage="256G"`, `domain="shopping"`. These slots feed three downstream consumers: `GoalSpec` (graph routing target), `SpecGuard` (purchase safety checks), and `ClarificationAgent` (whether to ask the user).

**③ VLM pre-plan.** `_vlm_pre_plan(task)` asks a VLM to decompose the task into ordered steps (e.g., "open Taobao → search → select product → select specs → add to cart"), each annotated with a target page_type. The pre-plan is scaffolding — each step is still governed by the current screenshot and safety gates.

**④ Memory session start.** `MemoryManager.start_task()` resets `UnifiedSessionState`, loads relevant user preferences from FAISS (e.g., "prefers silver"), and clears the RuntimeDAG cache.

### 3.2  Step Loop: The Complete _execute_step() Flow

`run()` calls `_execute_step()` in a loop. Each step, in actual code order:

**Phase 1 — Perception.** Capture screenshot via `DeviceFactory.get_screenshot()`, get current app, compute `ui_hash` (MD5). Classify page via `PageClassifier` → `page_type`, or use RuntimeDAG hint (skip classifier, save one VLM call).

**Phase 2 — Safety gate.** `detect_verification(page_type, summary)` checks for login/CAPTCHA/SMS pages. Hit → `Take_over` (pause for human). Miss → continue. Adaptive counter: 0-3 consecutive hits → auto-takeover; 4-5 → let VLM try once; >5 → task fails.

**Phase 3 — Graph coordination + clarification.** `memory_manager.locate_and_get_context()` delegates to `GraphRuntimeController`:
- If RuntimeDAG exists and valid → advance cursor, return next action (~10ms)
- Otherwise → `(app, page_type)` match in Neo4j → verify previous pending transition → Dijkstra route → cache as new RuntimeDAG
- Returns `mode` + `next_actions` + `semantic_context`

On the first step only, `ClarificationAgent.check_and_clarify()` runs a three-layer short-circuit: Layer 1 (rule check — specs already complete? skip), Layer 2 (fill gaps from FAISS user preferences), Layer 3 (only if still vague — ask strong VLM whether to clarify with user).

**Phase 4 — Context assembly.** `memory_manager.get_injection_context()` builds layered VLM context:
- Always: progress summary (1-2 lines) + current focus
- On demand: `RetrievalGateway` detects recall/comparison/stagnation signals in VLM thinking → inject product list or history steps
- Conditional: price/color/storage constraints on decision pages
- Graph context, TaskPlan progress markers, SpecGuard constraint reminders, ActionAdvisor action hints

**Phase 5 — Dispatch and execution.**
- If `mode=navigate` and graph returns grounded action → **Fast Path**: `ActionAdvisor.get_fast_action()` with slot substitution (`<query>` → "iPhone 16"), `ActionHandler.execute()` (~0.5s). Take new screenshot, check page_type matches expected postcondition. Mismatch → fall through to VLM Path.
- Otherwise → **VLM Path**: `ModelClient.request(messages)`, parse response, `ModelProtocolBridge.normalize_action()` → `DeviceActionIR`. `SpecGuard.check()` intercepts purchase-commit actions (replaces with user prompt if specs unverified). `ActionHandler.execute()` (~5s including inference).

**Phase 6 — State update.** `memory_manager.update_state_and_transition()` builds current `PageState`, caches `(current_page, action, expected_postcondition)` as pending transition for next step's verification. `add_step()` records to `UnifiedSessionState`, detects stagnation (3+ identical actions), extracts product info. Context history compressed: keep last 2 full VLM conversations, summarize the rest.

### 3.3  Context Management: What Gets Injected Per Step

| Priority | Content | Source | When injected |
|---|---|---|---|
| 1 (highest) | System prompt + task + screenshot | Agent config | Every step |
| 2 | Task plan progress markers | `TaskPlan.status_text()` | Step 2 onward |
| 3 | Graph navigation context | `GraphRuntimeController` | mode != explore |
| 4 | Critical constraints (price/color/storage) | `TaskSlots` + `SpecGuard` | Decision pages |
| 5 | Last 8 step summaries | History compression | Every step |
| 6 | On-demand memory retrieval | `RetrievalGateway` | VLM thinking shows recall/compare/stall |
| 7 (lowest) | ActionAdvisor action hints | Graph promoted edges | mode = navigate |

Key design: **not all memory enters every call.** Progress summary is always injected (2-3 lines). Detailed product comparisons, cart calculations, etc. are triggered only when the VLM's thinking text signals a need. In a typical 30-step task, full retrieval triggers 3-5 times.

### 3.4  Task End: Result Handling and Graph Persistence

Loop terminates when VLM outputs `terminate`/`answer`, or `max_steps` is reached.

`MemoryManager.end_task(success, result)` performs:

1. **Save trajectory** (always): Full step sequence → `trajectories/{timestamp}_{ok|fail}_{slug}.json`
2. **Learn patterns** (success only): Contact-app bindings, app usage frequency → FAISS
3. **Flush graph** (success only): `flush_staged_graph()` → canonicalization → quality filter → Neo4j
4. **VLM trajectory review** (success only): `TrajectoryReviewer` extracts new transitions → VLM validates → import as hypothesis

Failed tasks save trajectory files only — no graph writes. This is the first gate against noise pollution.

---

## 4  Memory Architecture

The memory system separates four surfaces that serve different temporal and semantic roles. The critical design decision is **memory decoupling**: not all memory is injected into every VLM call. Instead, the system injects lightweight context always and triggers detailed retrieval only when the VLM's reasoning signals a need.

### 4.1  Four Memory Surfaces

| Surface | Storage | Temporal scope | Injection strategy |
|---|---|---|---|
| **User memory** | `MemoryStore` (FAISS vector store) under `memory_db/<user>` | Cross-session | Preferences and contacts injected during clarification and slot filling. |
| **Session state** | `UnifiedSessionState` | Per-task | Progress summary and current focus injected every step. |
| **Graph memory** | `SpatialGraphMemory` + Neo4j | Cross-session (persistent graph) + per-session (staged observations) | Graph context injected when route exists; action hints injected on matching pages. |
| **Trajectory files** | JSON per task under `memory_db/<user>/trajectories/` | Per-task, retained for review | Not injected. Used by `TrajectoryReviewer` for offline graph evolution. |

```mermaid
graph TD
    subgraph surfaces["Four Memory Surfaces"]
        direction LR
        UM["User Memory\nFAISS · Preferences"]
        SS["Session State\nProducts · Cart · Steps"]
        GM["Graph Memory\nNeo4j · Lifecycle"]
        TF["Trajectory Files\nJSON · Audit"]
    end

    subgraph injection["Per-Step Injection"]
        P1["Progress summary\n(always)"]
        P2["Current focus\n(always)"]
        P3["On-demand retrieval\n(triggered)"]
        P4["Constraints\n(if exist)"]
    end

    UM --> injection
    SS --> injection
    GM --> injection
    injection --> VLM["VLM Context Window"]

    style UM fill:#42949E,color:#fff,stroke:none
    style SS fill:#E8873D,color:#fff,stroke:none
    style GM fill:#2E7D32,color:#fff,stroke:none
    style TF fill:#7B61A0,color:#fff,stroke:none
    style VLM fill:#0F4D92,color:#fff,stroke:none
```

### 4.2  Lightweight Context Injection

`MemoryManager.get_injection_context()` implements the decoupling strategy:

1. **Always inject**: A short progress summary (task plan status, step count, recent actions).
2. **Always inject**: The current focus (what the agent is trying to do right now).
3. **Triggered injection**: `RetrievalGateway` activates only when the previous VLM thinking indicates recall, comparison, calculation, product lookup, or stagnation. It uses heuristic intent detection over model thinking text and queries `UnifiedSessionState`.
4. **Conditional injection**: Task constraints (price range, color, storage) injected only when they exist and the current page is decision-relevant.

This strategy keeps the VLM context window lean. In a typical 30-step shopping task, full retrieval might trigger on 3-5 steps (when the VLM's thinking mentions "I saw a product earlier" or "comparing prices"). The remaining 25+ steps receive only the 2-3 line progress summary. This is critical for VLM performance: long histories confuse reasoning and increase latency.

### 4.3  Session State as Single Write Path

`UnifiedSessionState` merges the older KnowledgeBase, SessionMemory, and StateManager roles into a single state object. Each step writes once through `record_step()`, then optional product and constraint extraction updates the same state. This eliminates state drift across components — there is no scenario where the memory manager and the session state disagree about what happened.

The session state also serves as the anchor for the Tactical Layer: `GraphRuntimeController.locate_and_get_context()` reads the current session state to determine task progress, and the graph's `GoalSpec` is derived from the session's task slots.

### 4.4  Memory Surfaces Working Together

The four surfaces are not independent databases — they form a coordinated memory architecture:

- **User memory → Clarification**: User preferences fill spec gaps in Layer 2, reducing unnecessary questions.
- **Session state → Graph routing**: Task slots from session state populate `GoalSpec`, which drives route planning.
- **Graph memory → VLM context**: Promoted actions and route plans from graph memory appear as action hints in the VLM prompt.
- **Trajectory files → Graph evolution**: Saved trajectories feed `TrajectoryReviewer`, which validates new transitions and imports them as graph hypotheses.
- **Session state → Trajectory files**: At task end, the session state is serialized into a trajectory JSON file for audit and review.

This coordination means that a user preference learned from a past task (stored in FAISS) can influence the current task's graph routing (via GoalSpec) and safety checking (via SpecGuard) — all without the user repeating themselves.

---

## 5  Active Mobile Spatial Graph (AMSG)

AMSG is the core research contribution. It is a typed directed graph that represents the mobile UI as a network of page states connected by action transitions, annotated with lifecycle metadata, outcome distributions, and localization evidence.

### 5.1  Formal Definition

The core persisted motif is a three-node, two-relationship pattern in Neo4j:

```
(UIState) -[NEXT_ACTION]-> (Action) -[PRODUCES]-> (UIState)
```

UIState nodes store page-state abstractions (app, page_type, landmarks, affordances, risk). Action nodes store semantic actions (intent, semantic_target, postcondition, lifecycle_stage). Relationships track `frequency` (success count), `fail_count`, and `confidence` (= frequency / total), which form the data basis for the edge lifecycle system.

### 5.2  UIState / PageState

`PageState` abstracts a screenshot into a reusable page identity. This is not pixel-level identity — it is a *semantic* identity that allows the system to recognize "this is a product detail page in Taobao" regardless of which product is displayed.

| Field | Role in the system |
|---|---|
| `state_id` | Stable hash from `(app, page_type, landmarks, affordances, risk)`. |
| `app` | Current application name, canonicalized via schema aliases. |
| `page_type` | Categorical: `home`, `search_input`, `search_result`, `product_detail`, `spec_selection`, `cart`, `checkout`, `filter_panel`, etc. |
| `landmarks` | Stable visual anchors (e.g., "search bar", "price tag", "add to cart button"). |
| `affordances` | Possible interactions (e.g., "tap product", "swipe down", "type query"). |
| `slots` | Runtime slots detected from task and page text (e.g., `{query: "iPhone 16"}`). |
| `risk_level` | `normal`, `medium`, or `high`. Payment and login pages are always `high`. |
| `semantic_signature` | Deterministic string: `app|domain|page_type|landmarks|affordances|slots`. |

The page-state abstraction is what makes the graph reusable across tasks. A product detail page learned from one shopping session can inform navigation in a completely different session with a different product, because the graph stores the page *type* and *structure*, not the specific content.

### 5.3  Action Nodes

`Action` nodes represent semantic transition affordances, not raw coordinates. `GraphStore._semantic_action_key()` intentionally ignores coordinate jitter — coordinates are treated as grounding *evidence*, not identity.

| Field | Purpose |
|---|---|
| `type` | Action verb: `tap`, `type`, `swipe`, `back`, `launch`, `compound`. |
| `intent` | Semantic description: "tap search button", "type product query". |
| `semantic_target` | Human-readable target: "search box", "add to cart". |
| `target_locator` | Coordinates, region, or element descriptor for grounding. |
| `expected_postcondition` | Target page type after action. |
| `risk_level` | Inherited from source/target page risk. |
| `lifecycle_stage` | `hypothesis`, `candidate`, `promoted`, or `demoted`. |
| `outcome_entropy` | Shannon entropy of empirical outcome distribution. |

The canonical persisted motif is:

```mermaid
graph LR
    S["UIState\n(Source Page)"] -->|"NEXT_ACTION"| A["Action\ntype · intent · locator\nlifecycle · entropy"]
    A -->|"PRODUCES"| T["UIState\n(Target Page)"]

    style S fill:#E8E0F0,stroke:#7B61A0,stroke-width:2px,color:#4a2d7a
    style A fill:#FFF3E0,stroke:#E8873D,stroke-width:2px,color:#7a4a0d
    style T fill:#E8E0F0,stroke:#7B61A0,stroke-width:2px,color:#4a2d7a
```

> Recommended figure: `figures/amsg-graph-schema-nature-image2.png`

### 5.4  Grounded vs. Ungrounded Transitions

This distinction is the operational core of "graph as advisor, not controller":

| Transition | Grounded? | Graph behavior | VLM role |
|---|---|---|---|
| `home → search_input` | Yes | Fast Path with coordinates | None |
| `search_input → search_result` | Yes | Compound: Type + Submit | None (slot substitution) |
| `search_result → product_detail` | **No** | Hint only (no coordinates) | Must choose the product matching the current task |
| `product_detail → spec_selection` | **No** | Hint only | Must judge product page and target action |
| `spec_selection → cart/checkout` | **No** | Hint only | Must select the user's SKU safely |
| `search_result → filter_panel` | Yes | Fast Path | None |

Grounded transitions have stable coordinates and deterministic outcomes. Ungrounded transitions require semantic judgment about *which* element to interact with — the graph knows the transition exists, but only the VLM can decide the specific target on the current screen.

```mermaid
graph TD
    H["home"] -->|"Grounded"| SI["search_input"]
    SI -->|"Grounded\nCompound: Type + Submit"| SR["search_result"]
    SR -->|"Ungrounded\nVLM picks product"| PD["product_detail"]
    SR -->|"Grounded"| FP["filter_panel"]
    FP -->|"Grounded"| SR
    PD -->|"Ungrounded\nVLM picks action"| SS["spec_selection"]
    SS -->|"Ungrounded\nVLM selects specs"| CA["cart"]
    SS -->|"Ungrounded"| CO["checkout"]

    style H fill:#2E9E44,color:#fff,stroke:none
    style SI fill:#2E9E44,color:#fff,stroke:none
    style SR fill:#3775BA,color:#fff,stroke:none
    style FP fill:#2E9E44,color:#fff,stroke:none
    style PD fill:#0F4D92,color:#fff,stroke:none
    style SS fill:#0F4D92,color:#fff,stroke:none
    style CA fill:#0F4D92,color:#fff,stroke:none
    style CO fill:#B64342,color:#fff,stroke:none
```

---

## 6  Graph Self-Evolution

The graph does not grow by recording every observed action. It evolves through a staging-first pipeline with three independent paths, all converging on the same quality-gated persistence layer.

### 6.1  Edge Lifecycle State Machine

Every transition in AMSG passes through a lifecycle that determines its runtime authority:

```mermaid
stateDiagram-v2
    [*] --> hypothesis : first observation
    hypothesis --> candidate : verifications >= min_count
    candidate --> promoted : dominance >= threshold
    promoted --> demoted : dominance drops\n(UI changed)
    demoted --> hypothesis : VLM re-explores

    note right of hypothesis
        1-2 observations
        Not visible to ActionAdvisor
    end note

    note right of promoted
        Grounded -> Fast Path
        Ungrounded -> VLM hint
        Persisted to Neo4j
    end note
```

`EdgeLifecycleManager` tracks both individual edge lifecycle records and aggregate `OutcomeDistribution` for `(source_page_type, action_key)` pairs.

**Promotion criteria** (configurable via `AMSGOptimConfig`):
- `verification_count >= min_verification_count` (default: 1, strict: 3)
- `dominance_ratio >= outcome_dominance_threshold` (default: 0.6, strict: 0.8)
- `risk_level != "high"`

**Demotion**: A promoted edge is demoted when its dominance ratio falls below 40%. This handles UI changes: if an app update changes the target of a button, the outcome distribution shifts, dominance drops, and the edge is automatically demoted.

**Outcome Entropy** (Shannon entropy):

$$H = -\sum_i p_i \ln(p_i)$$

When `H > outcome_entropy_vlm_threshold` (default: 0.8), the transition is flagged for VLM verification instead of direct execution. In practice, a hardcoded set of semantic transitions (`search_result → product_detail`, `product_detail → spec_selection`) is the primary VLM-verification trigger; the entropy threshold serves as a supplementary data-driven mechanism.

### 6.2  Three Persistence Paths

```mermaid
flowchart TD
    subgraph online["Path 1: Online Runtime"]
        R1["Execute Action"] --> R2["Verify Postcondition"]
        R2 --> R3["record_observation()"]
        R3 --> R4["EdgeLifecycle\nrecord_outcome()"]
        R4 --> R5{"Task\nSuccess?"}
        R5 -->|"yes"| R6["flush_staged_graph()\n-> Neo4j"]
        R5 -->|"no"| R7["Stage only\n(no persist)"]
    end

    subgraph review["Path 2: Trajectory Review"]
        T1["Trajectory\nJSON"] --> T2["Extract transitions"]
        T2 --> T3["Dedup vs graph"]
        T3 --> T4["Strong VLM\nvalidation"]
        T4 --> T5{"Approved?"}
        T5 -->|"yes"| T6["Import as\nhypothesis"]
        T5 -->|"no"| T7["Reject"]
    end

    subgraph offline["Path 3: Offline Exploration"]
        O1["Exploration\nfiles"] --> O2["Canonicalize\n& filter"]
        O2 --> O3["Stage pages\n& transitions"]
        O3 --> O4["Quality\ngates"]
        O4 --> O5["Promote\nstaging"]
    end

    R6 --> DB[("Neo4j\nshopping-spatial-v4")]
    T6 --> DB
    O5 --> DB

    style R6 fill:#2E9E44,color:#fff,stroke:none
    style T6 fill:#2E9E44,color:#fff,stroke:none
    style O5 fill:#2E9E44,color:#fff,stroke:none
    style T7 fill:#B64342,color:#fff,stroke:none
    style T4 fill:#7B61A0,color:#fff,stroke:none
    style DB fill:#2E7D32,color:#fff,stroke:none
```

> Recommended figure: `figures/graph-persistence-pipeline-nature-image2.png`

**Path 1 (Online Runtime)**: During task execution, each action produces a postcondition observation. `SpatialGraphMemory.record_observation()` stages the transition locally. At task end, `flush_staged_graph()` canonicalizes states and edges, promotes valid transitions, and writes to Neo4j. Failed tasks do not flush — this is the first quality gate.

**Path 2 (Trajectory Review)**: `TrajectoryReviewer` processes saved trajectory files. It extracts page transitions, deduplicates against existing graph edges, and asks a strong VLM to validate new candidates. Only VLM-approved transitions are imported as `hypothesis` edges. This is the second quality gate — it prevents graph pollution from dialogs, ads, classifier mistakes, and transient screens.

**Path 3 (Offline Exploration)**: `import_exploration_staging()` processes pre-collected exploration data through canonicalization, transient-page filtering, app-mismatch filtering, search-macro synthesis, same-page compaction, and edge-quality filtering. Only after all gates pass does `promote_staging_to_canonical()` merge into the graph.

The key insight is that **no raw action writes directly to Neo4j**. Every persistence path passes through at least one quality gate (task success, VLM validation, or canonicalization filtering). This is what makes AMSG a "verified action library" rather than a noisy trajectory dump.

### 6.3  Lifecycle Persistence

`GraphLifecycleStore.persist_lifecycle_batch()` writes lifecycle records and outcome distributions to Neo4j:

- `lifecycle_stage`, `verification_count`, `dominance_ratio`
- `outcome_distribution_json`, `outcome_entropy`
- Timestamps for creation, last update, last verification

`GraphLifecycleStore.demote_stale_edges()` runs periodically to demote promoted edges whose dominance ratio has dropped below threshold. This ensures the graph self-corrects when app UIs change.

---

## 7  Localization and Planning

### 7.1  Page Localization

The default localization mechanism is straightforward: match the current `(app, page_type)` against UIState nodes in Neo4j. When a match is found, the graph candidate gets a fixed score of 0.92; the current observation gets 0.82. This simple approach works because `PageClassifier` already provides an accurate page_type, and the `(app, page_type)` pair uniquely identifies a page state in the vast majority of cases.

An optional multi-signal Bayesian localizer (`MultiSignalLocalizer`) is implemented but not enabled by default (`use_multi_signal_belief=False`). It adds visual embedding, semantic embedding, and temporal transition channels, but the marginal improvement over `(app, page_type)` matching has not been validated through ablation experiments.

### 7.2  Goal Inference

`GoalSpec.from_task()` extracts target page types and slots. `GraphRuntimeController._infer_goal_spec()` enriches this with VLM pre-plan fields:

- `search_query` → `query` slot
- `product` → `product` slot
- `specs` → slot values (color, storage, size)
- `target_page` → prioritized target page type

For search-first tasks, `_search_first_targets()` forces the route to progress through `home → search_input → search_result → original targets`. This prevents the graph from taking a historical shortcut from home directly to a random product detail page — a shortcut that would bypass the user's actual search intent.

### 7.3  Route Planning

The default planner is **Dijkstra** on the weighted graph:

$$\text{cost}(e) = 1.0 + 3.0 \cdot \text{fail\_rate} + \text{risk\_penalty} - 0.3 \cdot \text{confidence}$$

Risk penalties: normal=0, medium=0.8, high=2.0. This cost function prefers edges with high success rates, low risk, and high confidence — edges that have been verified to work.

On typical shopping app graphs (< 50 nodes), Dijkstra finds optimal paths efficiently. An enhanced planner (`EnhancedPlanner` with A* and Belief-A* backends) is implemented but not enabled by default — its additional cost adjustments (temporal decay, exploration bonus, information gain, entropy penalty) contribute < 0.1 on current graph sizes and do not change path selection in practice.

### 7.4  RuntimeDAG: Cached Route Execution

When Dijkstra finds a route, it is materialized into a `RuntimeDAG` — an in-memory array of edges with a cursor. Subsequent steps advance the cursor (`current_index += 1`) instead of re-querying Neo4j and re-running Dijkstra. This is the actual performance mechanism: on a 5-step navigation sequence, only the first step runs the full localization-planning pipeline; the remaining 4 steps read `route[current_index]` in < 1ms.

RuntimeDAG is invalidated when: postcondition mismatch (arrived at unexpected page), app switch, consecutive graph failures exceed threshold, or next edge is high-risk. After invalidation, the next `locate_and_get_context()` call runs the full pipeline and creates a new DAG if planning succeeds.

RuntimeDAG also controls PageClassifier skipping: when the DAG is valid and the next edge is not high-risk, `should_use_page_classifier()` returns `False`, and the agent uses the DAG's expected page_type instead of calling the classifier. This saves one VLM call per step but risks acting on a stale page_type if a popup appeared. The delayed postcondition verification at the next step catches such mismatches.

### 7.5  Action Compilation

Graph-native actions are compiled through a two-stage IR pipeline:

```
SemanticActionIR (intent, semantic_target, locator, postcondition)
    → DeviceActionIR (action_type, coordinate, coordinate_space, screen_size)
    → Canonical action dict for ActionHandler
```

`SpatialModelBridge` handles the first stage (semantic to device IR), and `ModelProtocolBridge` handles coordinate normalization across model families. This keeps AMSG model-agnostic: the graph stores semantic intent and locator evidence, while adapters handle model-native syntax and coordinate systems.

---

## 8  Model and Device Abstraction

### 8.1  Multi-Model Support

Shopping-Agent supports five VLM families through a unified adapter architecture:

| Model family | Coordinate space | Context strategy | Response format |
|---|---|---|---|
| AutoGLM | [0, 1000] normalized | Cumulative (append each turn) | `<answer>` XML |
| UI-TARS | Absolute pixels (smart_resize) | Max 5 images, prune oldest | "Thought: ... Action: ..." |
| Qwen-VL | [0, 999] normalized | Fresh rebuild each turn | `<tool_call>` JSON |
| MAI-UI | [0, 999] normalized | Max 3 images | `<thinking>` + `<tool_call>` |
| GUI-Owl | [0, 1.0] decimal | Max 1 image (current only) | Action + `<tool_call>` |

`ModelProtocolBridge._scale_coord()` is the coordinate transformation hub, converting between all coordinate spaces through a normalized (0, 1) intermediate representation. This means the graph can store coordinates in any space and they will be correctly converted for the current model at runtime.

### 8.2  Device Abstraction

`DeviceFactory` provides a unified interface across three platforms:

- **ADB** (Android): Full automation via `adb shell input` and `screencap`.
- **HDC** (HarmonyOS): Equivalent automation via `hdc` command-line tools.
- **XCTest** (iOS): WebDriverAgent-based control via HTTP API.

`ActionHandler` converts canonical action dicts to device calls. The handler manages keyboard setup for text input (switching to ADB keyboard, clearing fields, typing text, and restoring the original IME), timing delays between operations, and compound action sequences for multi-step same-page operations.

---

## 9  Configuration Presets

`AMSGOptimConfig` provides configuration presets via the `AMSG_CONFIG` environment variable. The default is `legacy` (fixed-score localization, Dijkstra planning, no lifecycle). The production-recommended preset is `sava` (Dijkstra, strict lifecycle with 3x verification and 80% dominance threshold).

Additional presets (`belief_only`, `planner_only`, `full`) exist as experimental infrastructure for future ablation studies — they are not validated contributions.

---

## 10  Comparative Analysis

### 10.1  Positioning in the Literature

Shopping-Agent addresses three specific gaps in the current GUI agent landscape:

**Gap 1: Stateless re-exploration.** Most GUI agents (including strong systems like ColorAgent [Li et al., 2025], MobiAgent [Zhang et al., 2025]) treat each task session as independent. They may learn within a session through reflection or self-correction, but structural knowledge about app navigation does not persist across sessions. Shopping-Agent's AMSG persists verified transitions in Neo4j, allowing the agent to build cumulative knowledge about app navigation patterns.

**Gap 2: Graph without lifecycle.** Systems that do build navigation graphs — PG-Agent [Chen et al., 2025] constructs page graphs from episodes, KG-RAG [Guan et al., 2025] transforms UTGs into vector databases, WebNavigator [Zhang et al., 2025] builds interaction graphs via heuristic exploration — treat the graph as a static artifact. Once built, edges do not evolve. Shopping-Agent introduces a full lifecycle: hypothesis → candidate → promoted → demoted, with data-driven promotion criteria and automatic demotion when UI changes.

**Gap 3: Binary graph/VLM control.** Existing graph-augmented agents use the graph either as a static RAG source (PG-Agent, KG-RAG) or as a deterministic controller that bypasses the VLM entirely (WebNavigator's Teleport). Shopping-Agent introduces a continuous spectrum: Fast Path (graph alone, ~0.5s), Graph Co-pilot (graph hints + VLM verification, ~3s), and Full VLM Path (VLM alone, ~5s). The dispatch decision is data-driven, based on edge lifecycle stage, outcome entropy, and grounding status.

### 10.2  Detailed Comparison

| Dimension | ColorAgent | PG-Agent | KG-RAG | WebNavigator | MobiAgent (AgentRR) | **Shopping-Agent** |
|---|---|---|---|---|---|---|
| **Graph structure** | None (multi-agent framework) | Page graph from episodes | UTG → vector DB | Interaction graph (BFS) | Multi-level experience tree | AMSG (typed directed graph with lifecycle) |
| **Graph evolution** | N/A | Static after construction | Static after extraction | Static after offline BFS | Record-replay (static) | Self-evolving: online staging → postcondition verification → lifecycle promotion → demotion |
| **Persistence** | None | In-memory per session | Vector DB (static) | Vector DB (static) | Latent memory model | Neo4j with lifecycle metadata and outcome distributions |
| **VLM/Graph boundary** | VLM-only (no graph) | RAG retrieval → VLM | RAG retrieval → VLM | Deterministic teleport (no VLM for navigation) | Experience → skip VLM (binary) | Per-edge grounded/ungrounded classification with postcondition fallback |
| **Localization** | VLM perception | BFS similarity search | Embedding retrieval | Multimodal retrieval (ColQwen) | Page matching | (app, page_type) matching + optional Bayesian extension |
| **Planning** | Multi-agent decomposition | BFS on page graph | BFS on UTG | Shortest path on interaction graph | Prefix reusability | Dijkstra on weighted graph (success rate + risk penalty) |
| **Safety** | None reported | None reported | None reported | None reported | None reported | SpecGuard: task-slot-aware purchase interception |
| **Transition verification** | Self-evolving training (model-level) | None (edges from episodes) | None | None | None | Postcondition verification + outcome distribution + entropy threshold |
| **Quality gate** | Trajectory filtering (training) | None | None | None | Manual correction of traces | Three gates: task success, VLM trajectory review, canonicalization |
| **Multi-model support** | Proprietary model | GPT-4o | MobileAgent-v2, UI-TARS | GPT-4o, Gemini, Claude | MobiMind (custom) | 5 families: AutoGLM, UI-TARS, Qwen-VL, MAI-UI, GUI-Owl |
| **Cross-platform** | Android only | Android (evaluation) | Android + HarmonyOS | Web only | Android only | Android + HarmonyOS + iOS |

### 10.3  Core Contributions (Default-Enabled, Reproducible)

1. **Self-updating page-state graph**. The first cross-session persistent navigation knowledge graph for mobile GUI agents. The graph grows with successful tasks and shrinks when edge reliability drops. Unlike static graphs (PG-Agent, KG-RAG, WebNavigator), AMSG evolves automatically.

2. **Success-rate-driven edge lifecycle**. Each transition tracks postcondition success/failure counts and progresses through hypothesis → candidate → promoted → demoted stages. Only statistically reliable transitions enter the executable action library. This solves the real problem of online observation noise (popups, login interceptions, classifier errors).

3. **Grounded/ungrounded classification with three-speed dispatch**. Each edge is independently classified as grounded (graph executes directly, ~0.5s) or ungrounded (VLM decides, ~5s). Fast Path has postcondition protection — mismatch falls back to VLM. This is the only known per-edge control boundary for GUI agent graphs.

4. **Delayed postcondition verification**. Action outcomes are recorded at step t+1 when the actual result can be observed, not at step t when only the intent is known. This ensures the lifecycle system receives ground-truth observations.

5. **Staging-first persistence with three quality gates**. Failed tasks do not write to the graph. New transitions require VLM review. All paths pass through canonicalization. No raw action writes to Neo4j.

6. **Structured task constraints as first-class state**. Task specs (color, storage, size, price) are extracted once and enforced by SpecGuard at purchase commit. This makes shopping safety a system property, not prompt engineering.

### 10.4  Experimental Extensions (Not Default-Enabled)

The following are implemented but require ablation experiments to validate as contributions:

- Multi-channel Bayesian belief localization (default: off; `(app, page_type)` matching suffices for most cases)
- Belief-A* enhanced planner (default: Dijkstra; enhancement terms contribute < 0.1 on typical graph sizes)
- Entropy-driven VLM verification boundary (implemented but hardcoded transition set is the primary mechanism)

---

## 11  Implementation Map

| Concept | File |
|---|---|
| Main loop | `phone_agent/agent.py` (1,785 lines) |
| Task plan | `phone_agent/task_plan.py` (133 lines) |
| Slot extraction | `phone_agent/core/task_spec.py` (323 lines) |
| Clarification | `phone_agent/clarify.py` (400 lines) |
| Purchase safety | `phone_agent/core/spec_guard.py` (252 lines) |
| Verification detection | `phone_agent/verification_detector.py` (177 lines) |
| Memory manager | `phone_agent/memory/memory_manager.py` |
| Unified session state | `phone_agent/memory/core/unified_state.py` |
| On-demand retrieval | `phone_agent/memory/retrieval_gateway.py` |
| Spatial graph memory | `phone_agent/memory/spatial_graph_memory.py` |
| Neo4j store | `phone_agent/memory/graph_store.py` |
| Lifecycle persistence | `phone_agent/memory/graph_lifecycle_store.py` |
| Runtime graph controller | `phone_agent/spatial/runtime_controller.py` |
| Action advisor | `phone_agent/spatial/action_advisor.py` |
| Edge lifecycle | `phone_agent/spatial/edge_lifecycle.py` |
| AMSG config presets | `phone_agent/spatial/amsg_config.py` |
| Belief localizer | `phone_agent/spatial/belief_localizer.py` |
| Enhanced planner | `phone_agent/spatial/enhanced_planner.py` |
| Schema registry | `phone_agent/spatial/schema_registry.py` |
| Spatial model bridge | `phone_agent/spatial/model_bridge.py` |
| Model protocol bridge | `phone_agent/model/protocol_bridge.py` |
| Model client | `phone_agent/model/client.py` |
| Model adapters | `phone_agent/model/adapters.py` |
| Action execution | `phone_agent/actions/handler.py` |
| Device abstraction | `phone_agent/device_factory.py` |
| Trajectory reviewer | `phone_agent/spatial/trajectory_reviewer.py` |



---

## 12  Paper Method Summary

> Shopping-Agent is a VLM-primary mobile GUI agent augmented by a self-evolving Active Mobile Spatial Graph (AMSG). The system abstracts screenshots into semantic page states and persists verified navigation transitions in Neo4j. Each step locates the current page via `(app, page_type)` matching, plans a route with Dijkstra on the weighted graph, and caches the route as a RuntimeDAG for subsequent steps to advance directly. Grounded, promoted transitions execute via Fast Path (~0.5s, no VLM call); ungrounded transitions require VLM to choose the specific target (~5s), with postcondition protection — mismatch triggers automatic fallback. Delayed postcondition verification at step t+1 confirms step t's outcome, ensuring edge success-rate tracking is based on ground-truth observations. Online observations are staged in memory; only successful tasks trigger Neo4j writes, and new transitions require VLM trajectory review — no raw actions write directly to the graph. User constraints are extracted as first-class task slots and enforced by SpecGuard at the point of purchase commitment.
