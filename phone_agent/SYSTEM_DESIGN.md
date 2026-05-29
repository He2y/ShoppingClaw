# Mobile-ShoppingAgent System Design

> Technical specification for the Active Mobile Spatial Graph (AMSG) framework.
> Covers system architecture, formal definitions, module design, and key innovations.
> Intended as the engineering reference for academic paper writing.

---

## 1. Problem Statement

Mobile shopping apps (Taobao, JD, Pinduoduo) present a fundamentally different automation challenge than web pages:

1. **Non-deterministic transitions** --- the same "Add to Cart" button on a product page may open a spec selection sheet (60%), a login page (20%), a promotional popup (15%), or produce no visible change (5%). Web pages are nearly deterministic by comparison.
2. **No DOM, no URL** --- the agent observes only screenshots and must reason about page identity through visual and semantic signals, not stable selectors.
3. **High-risk actions are irreversible** --- submitting an order, confirming payment, or modifying an address cannot be undone by pressing "Back".
4. **UI layouts change across versions and A/B tests** --- a graph edge learned yesterday may be invalid today.

Existing GUI agent frameworks (PG-Agent, MobiAgent, AgentRR, WebNavigator) treat transitions as deterministic observations: see a transition once, write it to the graph, and replay it forever. This assumption fails on real mobile apps.

**Core thesis**: In dynamic mobile GUIs, a spatial graph must be *self-maintaining* --- edges must be earned through postcondition verification, their reliability must be tracked as outcome distributions, and the planner must be aware of transition uncertainty.

---

## 2. Formal Definitions

The system is built on 9 formal definitions. Each maps to a concrete code module.

### Definition 1: Active Mobile Spatial Graph

$$\mathcal{G} = (V,\; E^c,\; E^h,\; \Sigma,\; \mathcal{B},\; \Pi)$$

| Symbol | Meaning | Code |
|--------|---------|------|
| $V$ | Finite set of page states (nodes) | `PageState`, `PageNode` |
| $E^c$ | Verified (committed) edges | `EdgeLifecycleRecord.stage == "promoted"` |
| $E^h$ | Hypothesis edges (unverified) | `EdgeLifecycleRecord.stage in {"hypothesis", "candidate"}` |
| $\Sigma$ | Domain schema (page type taxonomy + canonical transitions) | `MobileSchema`, `SchemaRegistry` |
| $\mathcal{B}$ | Belief distribution over $V$ | `BeliefDistribution` |
| $\Pi$ | Planner producing action sequences | `EnhancedPlanner` |

The graph is *self-maintaining*: edges are promoted from $E^h$ to $E^c$ after postcondition verification, and demoted back on observed failure.

### Definition 2: Page State (Node)

$$v = (\text{app},\; \tau,\; \mathbf{L},\; \mathbf{A},\; \mathbf{S},\; \rho,\; \sigma)$$

| Field | Type | Description |
|-------|------|-------------|
| app | string | Canonicalized application identifier |
| $\tau$ | enum | Page type (home, search\_input, search\_result, product\_detail, spec\_selection, cart, checkout, ...) |
| $\mathbf{L}$ | set | Landmarks --- stable visual anchors that identify the page |
| $\mathbf{A}$ | set | Affordances --- interactive elements the agent can act upon |
| $\mathbf{S}$ | dict | Slots --- task-specific key-value pairs (search query, product name, spec choices) |
| $\rho$ | enum | Risk level $\in$ {normal, medium, high} |
| $\sigma$ | string | Semantic signature --- deterministic hash for deduplication |

Two screenshots map to the same node when their semantic signatures match.

### Definition 3: Multi-Signal Observation Model

$$P(o \mid v) = \sum_{c=1}^{C} \hat{w}_c \cdot \phi_c(o, v)$$

Four observation channels, with graceful weight redistribution when a channel is unavailable:

| Channel $c$ | Signal $\phi_c$ | Default Weight | Availability |
|-------------|----------------|----------------|-------------|
| 1. Visual | Cosine similarity of VLM screenshot embeddings | 0.30 | Optional |
| 2. Semantic | Cosine similarity of text embeddings | 0.25 | Optional |
| 3. Structural | $0.35[\text{app}=] + 0.35[\tau=] + 0.20 J(\mathbf{L}) + 0.10 J(\mathbf{A})$ | 0.25 | Always |
| 4. Temporal | $\hat{P}(v \mid v_{\text{prev}}, a_{\text{prev}})$ from frequency model | 0.20 | After first transition |

$J(\cdot)$ denotes Jaccard similarity. When a channel returns $\text{None}$ (unavailable), its weight is redistributed proportionally to available channels.

### Definition 4: Frequency-Estimated Transition Model

$$\hat{P}(v' \mid v, a) = \frac{n(v, a, v')}{\sum_{v''} n(v, a, v'')}$$

Purely observation-driven. Feeds the temporal channel (Def 3) and the outcome distribution (Def 9).

### Definition 5: Planning Objective

$$\pi^* = \arg\min_\pi \sum_{t=0}^{T} C'(e_t) - \lambda \, H(\mathcal{B}_t) \cdot u(e_t)$$

Enhanced edge cost:

$$C'(e) = \underbrace{1 + 3\,r_{\text{fail}} + p_{\text{risk}} - 0.3\,c_{\text{conf}}}_{\text{base cost}} + \underbrace{\alpha \cdot s(e)}_{\text{staleness}} - \underbrace{\frac{\beta}{\sqrt{1 + n_{\text{visit}}(t)}}}_{\text{exploration bonus}} + \underbrace{\gamma \cdot H_{\mathcal{O}}(e)}_{\text{entropy penalty}}$$

| Term | Formula | Effect |
|------|---------|--------|
| Base cost | $1 + 3\,r_{\text{fail}} + p_{\text{risk}} - 0.3\,c_{\text{conf}}$ | Penalize failure-prone and risky edges |
| Staleness $s(e)$ | $1 - \exp(-\Delta t / \text{halflife})$ | Stale edges become less attractive |
| Exploration bonus | $\beta / \sqrt{1 + n_{\text{visit}}}$ | Under-visited states become more attractive |
| Entropy penalty | $\gamma \cdot H_{\mathcal{O}}(v, a)$ | Unpredictable transitions are penalized |

Three pluggable backends: Dijkstra (legacy), A\* (schema heuristic), Belief-A\* (full optimization).

### Definition 6: Functionality Discovery

$$\mathcal{C}: (\text{element},\; \tau,\; \text{context}) \to (\text{role},\; \text{type})$$

Two implementations behind a `RoleClassifier` protocol:

| Variant | Method | Learning |
|---------|--------|----------|
| $\mathcal{C}_K$ (Keyword) | Deterministic rule matching | None (legacy) |
| $\mathcal{C}_E$ (Embedding) | $\text{role} = \arg\min_j d(\phi(\text{element}), \mu_j)$ | Online: verified postconditions grow prototypes |

### Definition 7: Bayesian Belief Update

$$\mathcal{B}_t(v) = \eta \cdot P(o_t \mid v) \cdot \sum_{v'} P(v \mid v', a_{t-1}) \cdot \mathcal{B}_{t-1}(v')$$

- Shannon entropy $H(\mathcal{B}) = -\sum_v \mathcal{B}(v) \log \mathcal{B}(v)$ feeds the planner's information-gain term.
- MAP estimate $v^* = \arg\max_v \mathcal{B}(v)$ is used for action grounding.

### Definition 8: Edge Lifecycle

$$e: \text{hypothesis} \xrightarrow{n \geq k,\; \text{dom} \geq \theta} \text{candidate} \xrightarrow{\text{gate}} \text{promoted} \xrightarrow{\text{fail\_rate} > \delta} \text{demoted}$$

| Stage | Condition | Graph Membership | Planning Role |
|-------|-----------|-----------------|---------------|
| hypothesis | $n_{\text{verify}} < k$ | $E^h$ only | Not used |
| candidate | $n \geq k$, dominance $< \theta$ or high risk | $E^h$ | Available with penalty |
| promoted | dominance $\geq \theta$, normal risk | $E^c$ | Persisted to Neo4j |
| demoted | fail rate $> \delta$ after promotion | Removed from $E^c$ | Returned to $E^h$ |

Default parameters: $k = 1$, $\theta = 0.6$, $\delta$ = configurable.

### Definition 9: Outcome Distribution

$$\mathcal{O}(v, a) = \{(v'_1, p_1), \ldots, (v'_m, p_m)\}$$

$$H_{\mathcal{O}}(v, a) = -\sum_{i=1}^{m} p_i \log p_i$$

| Entropy | Interpretation | Agent Behavior |
|---------|---------------|----------------|
| Low $H_{\mathcal{O}}$ | Deterministic transition | Graph shortcut safe, skip VLM |
| High $H_{\mathcal{O}}$ | Unpredictable transition | VLM co-pilot verification required |

Replaces hardcoded VLM verification lists with a **data-driven decision boundary**: when $H_{\mathcal{O}} > \theta_{\text{vlm}}$ (default 0.8), the agent automatically triggers VLM verification for that transition.

---

## 3. System Architecture

### 3.1 Module Map

```
phone_agent/
  spatial/                           # AMSG core modules
    amsg_config.py                   # AMSGOptimConfig (13 fields, 5 presets)
    edge_lifecycle.py                # Def 8-9: EdgeLifecycleManager, OutcomeDistribution
    belief_localizer.py              # Def 3,7: MultiSignalLocalizer, BeliefDistribution
    enhanced_planner.py              # Def 5: EnhancedPlanner (3 backends)
    role_classifier.py               # Def 6: RoleClassifier protocol + 2 implementations
    core.py                          # PageNode, BeliefState, AffordanceEdge
    semantics.py                     # ScreenSemanticsExtractor
    hypothesis.py                    # EdgeHypothesisGenerator
    active_builder.py                # ActiveGraphBuilder (frontier scoring)
    schema_registry.py               # MobileSchema, SchemaRegistry
    functionality.py                 # FunctionalityExtractor
    functionality_cluster.py         # FunctionalityClusterer + EmbeddingFunctionalityClusterer
    runtime_controller.py            # GraphRuntimeController (Def 9 VLM verification)
    verifier.py                      # PostconditionVerifier
    coverage_metrics.py              # FunctionalityCoverageMetrics
    reporting.py                     # Report builders and formatters
    FORMALIZATION.md                 # 9 formal definitions

  memory/                            # Persistence and integration
    spatial_graph_memory.py          # SpatialGraphMemory (wires all AMSG modules)
    graph_store.py                   # Neo4j CRUD, TaskIndex
    memory_manager.py                # MemoryManager (agent-facing API)
    memory_store.py                  # FAISS vector store
    task_index.py                    # FAISS task semantic search
    offline_explorer.py              # VLM-driven app exploration
    state_manager.py                 # UI state tracking

  agent.py                           # PhoneAgent (main loop)
  model/adapters.py                  # VLM adapters (5 models)
  actions/handler*.py                # Platform-specific action execution
```

### 3.2 Execution Loop

```
Screenshot ──> Belief Localization ──> Goal Inference ──> Route Planning ──> Action Selection
    ^               (Def 3,7)            (Def 6)          (Def 5,8,9)          |
    |                                                                          v
    └───────── Postcondition Verification <── Action Execution <── Edge Lifecycle Update
                     (Def 8,9)                                        (Def 4)
```

Each agent step:

1. **Screenshot** --- capture current screen from device (ADB/HDC/XCTest).
2. **Belief Localization** --- `MultiSignalLocalizer.update()` fuses 4 observation channels into a probability distribution over known page states (Def 3, 7).
3. **Goal Inference** --- `GoalSpec.from_task()` extracts target page types and task slots from the natural language instruction (Def 6).
4. **Route Planning** --- `EnhancedPlanner.plan()` searches for the lowest-cost path through the graph. Cost incorporates edge staleness, exploration bonus, and outcome entropy (Def 5). Only promoted edges ($E^c$) from `EdgeLifecycleManager` are trusted; hypothesis edges carry a penalty.
5. **Action Selection** --- `next_planned_action()` emits the first step from the route. High-entropy transitions trigger VLM co-pilot verification (Def 9).
6. **Action Execution** --- `ActionHandler.execute()` converts the abstract action to device-specific commands.
7. **Edge Lifecycle Update** --- `EdgeLifecycleManager.record_outcome()` records the observed postcondition. The edge advances through the lifecycle state machine (Def 8). `OutcomeDistribution` updates the entropy for future planning (Def 9).

### 3.3 Configuration and Ablation

`AMSGOptimConfig` is a frozen dataclass with 13 fields controlling all optimization targets. Five preset constructors support ablation experiments:

| Preset | Belief | Planner | Edge Policy | Functionality | Heuristic Injection |
|--------|--------|---------|-------------|--------------|---------------------|
| `legacy()` | fixed | dijkstra | legacy | keyword | ON |
| `full()` | bayesian | belief\_a\* | verified | embedding | OFF |
| `edge_only()` | fixed | dijkstra | verified | keyword | OFF |
| `belief_only()` | bayesian | dijkstra | legacy | keyword | ON |
| `planner_only()` | fixed | belief\_a\* | legacy | keyword | ON |

The `legacy()` preset reproduces exact pre-optimization behavior. The `full()` preset enables all academic contributions. Each intermediate preset isolates a single optimization target for ablation.

---

## 4. Key Modules

### 4.1 Edge Lifecycle Manager (`edge_lifecycle.py`, Def 8-9)

**This is the primary differentiator from prior work.**

Prior approaches (PG-Agent, AgentRR) write every observed transition to the graph immediately. On mobile apps, this produces unreliable graphs because:
- The same action produces different outcomes depending on app state, login status, A/B tests, and network conditions.
- A single false-positive edge (e.g., "Tap Add-to-Cart always opens spec\_selection") causes the planner to skip VLM reasoning, leading to cascading failures.

AMSG introduces a lifecycle state machine:

```python
@dataclass(frozen=True)
class EdgeLifecycleRecord:
    edge_key: str                    # "source_type|intent|target|region|target_type"
    stage: str                       # "hypothesis" | "candidate" | "promoted" | "demoted"
    outcome_counts: dict[str, int]   # {"spec_selection": 5, "login": 2, "popup": 1}
    dominance_ratio: float           # dominant_count / total
    verification_count: int
    risk_level: str

@dataclass(frozen=True)
class OutcomeDistribution:
    outcomes: dict[str, int]         # page_type -> observation count

    @property
    def entropy(self) -> float:      # Shannon entropy (Def 9)
        ...
```

**Core API**:
- `record_outcome(source_page_type, intent, action_target, observed_target, risk)` --- records what actually happened after an action. Updates both the edge lifecycle record and the outcome distribution.
- `requires_vlm_verification(source_page_type, action_key) -> bool` --- returns `True` when outcome entropy exceeds the VLM threshold. This replaces hardcoded verification sets.
- `get_promotable_edges()` --- returns edges meeting promotion criteria ($n \geq k$, dominance $\geq \theta$, non-high-risk).

**Integration**: `SpatialGraphMemory.record_observation()` calls `_edge_lifecycle.record_outcome()` on every real-device execution. `RuntimeController._requires_vlm_verification()` queries the lifecycle manager before trusting a graph edge.

### 4.2 Multi-Signal Belief Localizer (`belief_localizer.py`, Def 3, 7)

Mobile apps lack URLs or DOM selectors. The agent must localize itself within the graph from a screenshot alone. Prior approaches use fixed similarity scores or simple hash matching. AMSG uses Bayesian belief fusion:

```python
class MultiSignalLocalizer:
    def update(self, signals: ObservationSignals, candidates: list[PageState]) -> BeliefDistribution:
        # For each candidate: posterior = likelihood * prior
        # likelihood = weighted sum of 4 channel similarities
        # Normalize to get probability distribution
```

**Four channels**:
1. **Visual**: cosine similarity of VLM screenshot embeddings (optional, unavailable on low-end devices).
2. **Semantic**: cosine similarity of text embeddings from page content (optional).
3. **Structural**: weighted match on app, page\_type, landmarks, affordances (always available, wraps legacy logic).
4. **Temporal**: frequency-estimated $P(v \mid v_{\text{prev}}, a_{\text{prev}})$ from transition history (available after first transition).

**Graceful degradation**: when a channel is unavailable, its weight is redistributed proportionally to available channels. On a device with no embedding model, only structural + temporal channels are used.

**Key output**: `BeliefDistribution.entropy` feeds the planner's information-gain term. High belief entropy means the agent is uncertain about its location, which makes exploratory actions more valuable.

### 4.3 Enhanced Planner (`enhanced_planner.py`, Def 5)

Three pluggable backends behind a single `plan()` dispatch:

**Backend 1: Dijkstra** --- exact reproduction of the pre-optimization shortest-path algorithm. Uses `edge.weighted_cost` only.

**Backend 2: A\*** --- adds a schema-aware admissible heuristic:
$$h(v) = \min_{\tau \in \text{goals}} d_\Sigma(\tau(v), \tau)$$
where $d_\Sigma$ is the BFS distance in the domain schema. This accelerates search on sparse graphs.

**Backend 3: Belief-A\*** --- the full optimization. Edge cost includes:
- **Temporal staleness**: $s(e) = 1 - \exp(-\Delta t / \text{halflife})$. Edges not traversed recently become less attractive, forcing the planner to prefer recently-verified paths.
- **Exploration bonus**: $\beta / \sqrt{1 + n_{\text{visit}}}$. Under-visited states are rewarded, encouraging coverage.
- **Information gain**: $\lambda \cdot H(\mathcal{B}) \cdot s(e)$. When belief entropy is high (agent is lost), stale edges become even less attractive because they offer no localization value.
- **Outcome entropy penalty**: $\gamma \cdot H_{\mathcal{O}}(v, a)$. Edges whose outcomes are unpredictable are penalized, steering the planner toward deterministic paths.

### 4.4 Role Classifier (`role_classifier.py`, Def 6)

Classifies UI elements into functional roles (e.g., "search\_box", "add\_to\_cart\_button", "price\_display").

**KeywordRoleClassifier**: deterministic rule matching from `DATA_HINTS` and `FUNCTION_HINTS` tables. Zero behavior change from legacy. Used when `use_embedding_role_classifier=False`.

**EmbeddingRoleClassifier**: prototype-based classification with online learning.
- `classify()`: computes cosine similarity to known role prototypes. Falls back to keyword classifier below threshold.
- `register_verified_role()`: when a postcondition verification confirms a role, the embedding is added as a new prototype. Deduplication at cosine similarity > 0.95.

### 4.5 Runtime Controller (`runtime_controller.py`)

Orchestrates the full locate-plan-execute cycle. Key integration points:

- **`_requires_vlm_verification(source_page_type, target_page_type)`**: queries `EdgeLifecycleManager` for outcome entropy. If entropy exceeds `outcome_entropy_vlm_threshold`, returns `True` --- the agent must use VLM co-pilot to verify the transition before trusting the graph edge. Falls back to a hardcoded set `VLM_VERIFY_TRANSITIONS` only when no lifecycle data exists.
- **`locate_and_get_context()`**: full orchestration pipeline. Tries RuntimeDAG hint first (fast path), then locate + plan + enrich.
- **`compile_task_dag()`**: compiles a task-local in-memory DAG for multi-step execution without repeated localization.

### 4.6 Offline Explorer (`offline_explorer.py`)

VLM-driven app exploration with coverage-guided task generation:

- Accepts a natural language task description ("Cover the core shopping flow: home -> search -> product -> spec -> cart").
- Runs a closed loop: screenshot -> VLM decides action -> page classifier verifies -> record transition -> execute -> repeat.
- **Dual VLM architecture**: action model (e.g., AutoGLM, UI-TARS) decides what to do; classifier model (e.g., Qwen-VL) independently verifies the resulting page type. This cross-validation prevents misclassification from corrupting the graph.
- **Safety guardrails**: blocks actions targeting payment, checkout, login, address pages. Automatically backs out from high-risk pages.
- **Coverage targets**: tracks discovered page types and transitions against a target set. Stops exploration when coverage is complete.
- **Active exploration mode**: uses `EdgeHypothesisGenerator` and `ActiveGraphBuilder` to score frontier hypotheses and suggest the most coverage-valuable next action.

### 4.7 SpatialGraphMemory (`spatial_graph_memory.py`)

Central integration layer that wires all AMSG modules:

```python
class SpatialGraphMemory:
    def __init__(self, graph_store, config=None):
        self._amsg_config = config or AMSGOptimConfig.legacy()
        self._edge_lifecycle = EdgeLifecycleManager(self._amsg_config)
        self._localizer = MultiSignalLocalizer(self._amsg_config)
        self._enhanced_planner = EnhancedPlanner(self._amsg_config)
```

Key responsibilities:
- **`locate(screen, task)`**: delegates to `MultiSignalLocalizer` when `use_multi_signal_belief=True`.
- **`plan(belief, goal_spec)`**: delegates to `EnhancedPlanner` when `planner_backend != "dijkstra"`.
- **`record_observation(before, action, after)`**: feeds `EdgeLifecycleManager.record_outcome()`.
- **`_load_edges(state_id)`**: respects `enable_heuristic_injection` flag. When `False` (paper mode), no hardcoded edges are injected.
- **`import_exploration_staging()` + `promote_staging_to_canonical()`**: offline exploration import pipeline with page canonicalization, transition filtering, search compound synthesis, and quality reporting.

---

## 5. Key Innovations (vs Prior Work)

### 5.1 Edge Lifecycle with Postcondition Verification (Def 8)

| Aspect | PG-Agent / AgentRR | AMSG |
|--------|-------------------|------|
| Edge creation | Observe once, write immediately | Hypothesis stage; not used for planning |
| Edge promotion | N/A | Requires $k$ postcondition verifications + dominance $\geq \theta$ |
| Edge demotion | Never | Fail rate exceeds $\delta$ after promotion |
| Same-action multiple outcomes | Not modeled | `OutcomeDistribution` tracks per-action outcome entropy |

### 5.2 Data-Driven VLM Verification (Def 9)

| Aspect | Prior work | AMSG |
|--------|-----------|------|
| When to use VLM | Always, or hardcoded transition set | Outcome entropy $H_{\mathcal{O}} > \theta_{\text{vlm}}$ |
| Adaptation | Fixed | Learns from observations; new transitions are automatically classified |
| Overhead | High (VLM on every step) or brittle (miss non-listed transitions) | Minimal: VLM only where uncertainty is real |

### 5.3 Multi-Signal Belief Localization (Def 3, 7)

| Aspect | Prior work | AMSG |
|--------|-----------|------|
| Page identification | Hash match or fixed similarity | 4-channel Bayesian fusion |
| Embedding dependency | Required | Graceful degradation; works with structural + temporal only |
| Uncertainty awareness | Binary (found/not found) | Full entropy measure feeds planner |

### 5.4 Uncertainty-Aware Planning (Def 5)

| Aspect | Dijkstra (prior) | Belief-A\* (AMSG) |
|--------|-----------------|-------------------|
| Edge cost | Static weight | Dynamic: staleness + exploration bonus + entropy penalty |
| Heuristic | None | Schema-BFS admissible heuristic |
| Information seeking | No | $\lambda \cdot H(\mathcal{B}) \cdot u(e)$ term rewards uncertainty reduction |
| Outcome uncertainty | Ignored | $\gamma \cdot H_{\mathcal{O}}$ penalty steers away from chaotic transitions |

### 5.5 Heuristic Injection Control

Legacy systems inject hardcoded edges (e.g., "product\_detail -> spec\_selection via Add-to-Cart, confidence=0.70") to compensate for sparse graphs. AMSG makes this a toggle:
- `enable_heuristic_injection=True` (legacy): injected edges fill graph gaps.
- `enable_heuristic_injection=False` (paper): all edges must be earned through real-device verification. This is essential for the "self-maintaining graph" claim.

---

## 6. Data Flow

### 6.1 Online Execution

```
User: "Search for wireless headphones and add the cheapest one to cart"
  |
  v
GoalSpec.from_task() --> target_page_types=("spec_selection",), slots={"query": "wireless headphones"}
  |
  v
MultiSignalLocalizer.update(screenshot_signals, graph_candidates)
  --> BeliefDistribution(home: 0.85, search_input: 0.10, ...)
  |
  v
EnhancedPlanner.plan(start="state_home", goals={"spec_selection"})
  --> Route: home -> search_input -> search_result -> product_detail -> spec_selection
  |  (cost accounts for staleness, exploration bonus, outcome entropy)
  |
  v
RuntimeDAG compiled; next_planned_action() = Tap(search_bar)
  |
  v
ActionHandler.execute(Tap, [500, 100])
  --> Screenshot captured
  |
  v
EdgeLifecycleManager.record_outcome(
    source="home", intent="Tap", target="search_bar",
    observed_target="search_input"
)
  --> EdgeLifecycleRecord: hypothesis -> promoted (deterministic transition)
  --> OutcomeDistribution: {"search_input": 1}, entropy=0.0
  |
  v
(loop continues until goal reached or max_steps)
```

### 6.2 Offline Exploration

```
CLI: python -m phone_agent.memory.offline_explorer --app Taobao --task "Cover home->search->product->spec->cart"
  |
  v
OfflineExplorer.explore()
  |
  +--> Launch app
  +--> Loop (max_steps):
  |      Screenshot -> Action VLM decides action -> PageClassifier verifies page type
  |      -> Record transition (with rejection filter) -> Execute action
  |      -> Check coverage target
  |
  +--> Save results: pages.json + transitions.json + trajectory.json
  |
  +--> (if --auto-import-graph):
         SpatialGraphMemory.import_exploration_staging()
           --> Canonicalize pages (semantic dedup)
           --> Filter transitions (safety, plausibility)
           --> Synthesize search compound actions
         SpatialGraphMemory.promote_staging_to_canonical()
           --> Merge with existing graph
           --> Persist to Neo4j
```

---

## 7. Test Coverage

| Module | Tests | Test File |
|--------|-------|-----------|
| Edge Lifecycle (Def 8, 9) | 10 | `tests/test_edge_lifecycle.py` |
| Belief Localizer (Def 3, 7) | 13 | `tests/test_belief_localizer.py` |
| Enhanced Planner (Def 5) | 11 | `tests/test_enhanced_planner.py` |
| Role Classifier (Def 6) | 8 | `tests/test_role_classifier.py` |
| Spatial Graph Memory | 32+ | `tests/test_spatial_graph_memory.py` |
| AMSG Integration | 20+ | `tests/test_amsg_spatial.py` |
| V4 Functionality | 10+ | `tests/test_amsg_v4_functionality.py` |
| GraphStore V4 Runtime | 1 | `tests/test_graph_store_v4_runtime.py` |

All tests pass under `AMSGOptimConfig.legacy()` (regression) and `AMSGOptimConfig.full()` (optimization).

---

## 8. Environment and Dependencies

| Component | Technology |
|-----------|-----------|
| Graph database | Neo4j (bolt protocol, optional) |
| Vector index | FAISS (embedding-3, 2048d for memory, 1536d for task index) |
| VLM adapters | AutoGLM, UI-TARS, Qwen-VL, MAI-UI, GUI-Owl |
| Device backends | ADB (Android), HDC (HarmonyOS), XCTest (iOS) |
| Chat gateway | nanobot (12+ channel integrations) |
| Language | Python 3.11+, frozen dataclasses, Protocol typing |
