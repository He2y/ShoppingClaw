# AMSG Formal Definitions

This document provides the mathematical formalization of the Active Mobile
Spatial Graph (AMSG).  Each definition maps to a concrete module in the
`phone_agent/spatial/` package.

---

## Definition 1 — Active Mobile Spatial Graph

$$
\mathcal{G} = (V,\; E^c,\; E^h,\; \Sigma,\; \mathcal{B},\; \Pi)
$$

| Symbol | Meaning | Code |
|--------|---------|------|
| $V$ | Finite set of **page states** (nodes) | `PageNode`, `PageState` |
| $E^c \subseteq V \times \mathcal{A} \times V$ | **Verified (committed) edges** — transitions confirmed by postcondition verification | `EdgeLifecycleRecord.stage == "promoted"` |
| $E^h \subseteq V \times \mathcal{A} \times V$ | **Hypothesis edges** — observed but not yet verified | `EdgeLifecycleRecord.stage ∈ {"hypothesis", "candidate"}` |
| $\Sigma$ | **Domain schema** constraining allowed page types and transitions | `MobileSchema`, `SchemaRegistry` |
| $\mathcal{B}$ | **Belief distribution** over $V$ | `BeliefDistribution` |
| $\Pi$ | **Planner** producing action sequences from belief to goal | `EnhancedPlanner` |

The graph is **self-maintaining**: edges are promoted from $E^h$ to $E^c$ only
after postcondition verification, and demoted back when failure rates exceed
a threshold.

---

## Definition 2 — Page State (Node)

$$
v = (\text{app},\; \tau,\; \mathbf{L},\; \mathbf{A},\; \mathbf{S},\; \rho,\; \sigma)
$$

| Field | Type | Description |
|-------|------|-------------|
| $\text{app}$ | string | Application identifier (canonicalized via `SchemaRegistry.normalize_app`) |
| $\tau$ | string | Page type (e.g. `home`, `product_detail`, `spec_selection`) |
| $\mathbf{L}$ | set of strings | Landmarks — stable visual anchors on the page |
| $\mathbf{A}$ | set of strings | Affordances — available interactive elements |
| $\mathbf{S}$ | dict | Slots — task-specific key-value pairs |
| $\rho$ | {normal, medium, high} | Risk level |
| $\sigma$ | string | Semantic signature (deterministic hash of the above) |

**State identity**: Two screenshots map to the same node $v$ when their
semantic signatures match under the state abstraction function
$\alpha: \text{Screenshot} \to V$.

Code: `core.py:PageNode`, `spatial_graph_memory.py:PageState`

---

## Definition 3 — Multi-Signal Observation Model

$$
P(o \mid v) = \sum_{c=1}^{C} \hat{w}_c \cdot \phi_c(o, v)
$$

where $\hat{w}_c = w_c / \sum_{j \in \text{available}} w_j$ redistributes
weight from unavailable channels.

| Channel $c$ | $\phi_c$ | Description |
|-------------|----------|-------------|
| 1 (visual) | Cosine similarity of VLM screenshot embeddings | Optional; `None` when no VLM |
| 2 (semantic) | Cosine similarity of text embeddings | Optional; `None` when no encoder |
| 3 (structural) | $0.35 \cdot [\text{app}=] + 0.35 \cdot [\tau=] + 0.20 \cdot J(\mathbf{L}) + 0.10 \cdot J(\mathbf{A})$ | Always available; wraps legacy `_page_similarity` |
| 4 (temporal) | $\hat{P}(v \mid v_{\text{prev}}, a_{\text{prev}})$ | Transition prior from history |

**Graceful degradation**: When channels 1–2 are unavailable, their weight
flows to channels 3–4, ensuring the system works without embeddings while
benefiting from them when present.

Code: `belief_localizer.py:MultiSignalLocalizer`

---

## Definition 4 — Frequency-Estimated Transition Model

$$
\hat{P}(v' \mid v, a) = \frac{n(v, a, v')}{\sum_{v''} n(v, a, v'')}
$$

where $n(v, a, v')$ counts observed transitions from state $v$ via action
$a$ arriving at state $v'$.

Code: `spatial_graph_memory.py:record_observation`, `edge_lifecycle.py:OutcomeDistribution`

---

## Definition 5 — Planning Objective

$$
\pi^* = \arg\min_\pi \sum_{t=0}^{T} C'(e_t) - \lambda \, H(\mathcal{B}_t) \cdot u(e_t)
$$

**Enhanced edge cost**:

$$
C'(e) = \underbrace{1 + 3 r_{\text{fail}} + p_{\text{risk}} - 0.3 c_{\text{conf}}}_{\text{base cost}} \;+\; \alpha \cdot s(e) \;-\; \frac{\beta}{\sqrt{1 + n_{\text{visit}}(t)}} \;+\; \gamma \cdot H_{\mathcal{O}}(e)
$$

| Term | Meaning |
|------|---------|
| $r_{\text{fail}}$ | Failure rate = fail_count / attempts |
| $p_{\text{risk}}$ | Risk penalty: normal=0, medium=0.8, high=2.0 |
| $c_{\text{conf}}$ | Edge confidence ∈ [0, 1] |
| $s(e) = 1 - \exp\left(-\frac{\Delta t}{\text{halflife}}\right)$ | Temporal staleness |
| $n_{\text{visit}}(t)$ | Visit count for target state |
| $H_{\mathcal{O}}(e)$ | Outcome distribution entropy (Def 9) |

**A\* heuristic** (admissible):

$$
h(v) = \min_{\tau \in \text{goals}} d_\Sigma(\tau(v), \tau)
$$

where $d_\Sigma$ is the minimum hop count in the schema transition graph.

Code: `enhanced_planner.py:EnhancedPlanner`

---

## Definition 6 — Functionality Discovery

$$
\mathcal{C}: (\text{element},\; \tau,\; \text{context}) \to (\text{role},\; \text{type})
$$

Two implementations:

1. **Keyword classifier** $\mathcal{C}_K$: deterministic rule matching (legacy)
2. **Embedding classifier** $\mathcal{C}_E$: $\text{role} = \arg\min_j d(\phi(\text{element}),\; \mu_j)$
   where $\mu_j$ are prototype embeddings grown online via verified postconditions

Code: `role_classifier.py:RoleClassifier`

---

## Definition 7 — Bayesian Belief Update

$$
\mathcal{B}_t(v) = \eta \cdot P(o_t \mid v) \cdot \sum_{v'} P(v \mid v', a_{t-1}) \cdot \mathcal{B}_{t-1}(v')
$$

where $\eta$ is the normalization constant.

**Properties**:
- Shannon entropy $H(\mathcal{B}) = -\sum_v \mathcal{B}(v) \log \mathcal{B}(v)$
  feeds into the planner's information-gain term
- MAP estimate $v^* = \arg\max_v \mathcal{B}(v)$ is used for action grounding

Code: `belief_localizer.py:MultiSignalLocalizer.update`

---

## Definition 8 — Edge Lifecycle

$$
e: \;\text{hypothesis} \;\xrightarrow[\text{verify}(n \geq k)]{\text{dom\_ratio} \geq \theta}\; \text{candidate} \;\xrightarrow{\text{quality gate}}\; \text{promoted} \;\xrightarrow[\text{fail\_rate} > \delta]{\text{optional}}\; \text{demoted}
$$

| Stage | Condition | Graph Membership |
|-------|-----------|-----------------|
| hypothesis | $n_{\text{verify}} < k$ | $E^h$ only; not used for planning |
| candidate | $n_{\text{verify}} \geq k$ but dominance ratio $< \theta$ or high risk | $E^h$; available for planning with penalty |
| promoted | dominance $\geq \theta$, not high-risk | $E^c$; persisted to Neo4j |
| demoted | failure rate exceeds $\delta$ after promotion | Removed from $E^c$, returned to $E^h$ |

**Key insight**: In dynamic mobile GUIs, the same action (e.g. "add to cart")
can produce multiple outcomes (spec dialog, login page, promotion popup,
no-op). The lifecycle ensures only *reliably predictable* transitions enter
the committed graph.

Code: `edge_lifecycle.py:EdgeLifecycleManager`

---

## Definition 9 — Outcome Distribution

$$
\mathcal{O}(v, a) = \{(v'_1, p_1), \ldots, (v'_m, p_m)\}
\quad\text{where}\quad
p_i = \frac{n(v, a, v'_i)}{\sum_j n(v, a, v'_j)}
$$

**Outcome entropy**:

$$
H_{\mathcal{O}}(v, a) = -\sum_{i=1}^{m} p_i \log p_i
$$

- High $H_{\mathcal{O}}$ ⟹ unpredictable transition ⟹ **VLM verification required**
- Low $H_{\mathcal{O}}$ ⟹ deterministic transition ⟹ graph shortcut safe

This replaces the hardcoded VLM-verification transition list
`{(search_result, product_detail), (product_detail, spec_selection)}`
with a **data-driven decision boundary**.

Code: `edge_lifecycle.py:OutcomeDistribution`
