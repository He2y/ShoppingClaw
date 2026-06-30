# AMSG TrajReplay — Lifecycle Replay & Data-Gap Report

> Offline replay of saved trajectory observation streams into the (P0-C-fixed)
> edge-lifecycle pipeline. 100% software, no real device. Ground truth = the
> `page_type` the agent actually observed on-device (exogenous to the graph).
>
> Generated: 2026-06-30

## 0  One-line status

- trajectories=6 (success=6, failure=0, trivial<=1obs=2)
- usable observations=44 (cross-page=18, self-loop=26, dropped empty/unknown=20, drop-rate=31%)
- edges: sava promoted=2/34  legacy promoted=34/34
- multi-modal outcome dists=2 (entropy>VLM-threshold=2)
- P0-C recovery: coord-edges=27 recovered=27 discriminating(vc>=2)=3 max-coord-vc=2 proven-on-real-data=True

## 1  Corpus

| metric | value |
|---|---|
| trajectories | 6 |
| success / failure | 6 / 0 |
| trivial (<=1 usable obs) | 2 |
| raw steps (all trajs) | 70 |
| usable observations | 44 |
|   cross-page | 18 |
|   self-loop | 26 |
| dropped (empty/unknown page_type) | 20 |
| drop rate | 31% |

**Usable observations per task** (trajectories per task in parens):

| task | usable obs | # trajs |
|---|---|---|
| 去淘宝帮我买一个蓝牙耳机，要求500-1000元内，有降噪功能 | 24 | 2 |
| 去淘宝帮我买个4K显示器，价格2000-3000元，面板类型选择Mini-LED,帮我加入购物车 | 13 | 3 |
| 去淘宝帮我买一个蓝牙耳机，要求500-1000元内 | 7 | 1 |

**Usable observations per app:** 淘宝=43, System Home=1

## 2  sava vs legacy on identical input

| config | edges | promoted | candidate | hypothesis | vc histogram |
|---|---|---|---|---|---|
| sava | 34 | 2 | 0 | 32 | {1: 29, 2: 3, 4: 1, 5: 1} |
| legacy | 34 | 34 | 0 | 0 | {1: 29, 2: 3, 4: 1, 5: 1} |

**sava stage by action intent** (exposes the coordinate-tap vs mergeable split):

| intent | promoted | candidate | hypothesis |
|---|---|---|---|
| Launch | 0 | 0 | 1 |
| Swipe | 1 | 0 | 1 |
| Tap | 0 | 0 | 27 |
| Type | 1 | 0 | 1 |
| Wait | 0 | 0 | 2 |

> Note: an edge's own `dominance_ratio` is structurally 1.0 (the observed target is part of the edge key), so under sava the *only* binding promotion constraint is `verification_count>=3` and `risk!=high`. The dominance / multimodality signal lives in the OutcomeDistribution (source+action, target-agnostic) and drives `requires_vlm_verification`, not edge promotion.

**Promoted-edge utility** (are the promotions actually useful navigation?):

- sava promoted **2** edge(s): **0 cross-page** (useful navigation), **2 self-loop** (degenerate, stay-put).
  - [self-loop] `search_input` --[Type:∅]--> `search_input` (vc=4)
  - [self-loop] `filter_panel` --[Swipe:∅]--> `filter_panel` (vc=5)
- **Zero useful navigation edges earned promotion.** Both promotions are empty-target self-loops (typing keeps you on the input page; scrolling keeps you on the filter panel). Every cross-page transition in the corpus is a raw-pixel Tap that never repeats exactly, so none reached vc>=3.

## 3  N-sensitivity (DESCRIPTIVE on this corpus only — not a claim)

| min_verification_count N | # promotable edges |
|---|---|
| 1 | 34 |
| 3 | 2 |
| 5 | 1 |

> Red line (sim-env analysis): with an all-success corpus the binding constraint is N (dominance is trivially 1.0), so this table only shows how thin the corpus is. It is NOT a generalizable N∈{3,5} result and must not appear as a quantitative claim in the paper.

## 4  P0-C verification on real coordinates

| metric | value |
|---|---|
| coordinate edges checked | 27 |
| recovered real vc (vs default@1) | 27 |
| discriminating (true vc>=2) | 3 |
| discriminating recovered | 3 |
| max coordinate vc | 2 |
| proven on real data | True |

## 5  RQ2 sub-claim support (this corpus)

| claim | supported? | evidence |
|---|---|---|
| Lifecycle accumulates real verification counts on a real observation stream | ✅ yes | 5 edge(s) reached verification_count>=2 by merging repeated observations |
| Lifecycle promotes USEFUL (cross-page) navigation edges from this corpus | ❌ no | 0 cross-page edge(s) promoted; 2 promoted edge(s) are degenerate self-loops (empty-target Type/Swipe that stay on the page) — cross-page taps key on raw pixels that drift between runs (max coordinate vc=2), so none reach vc>=3 |
| Outcome distributions flag NON-DETERMINISTIC transitions (same action -> >1 outcome) | ✅ yes | 2 multi-modal distribution(s), 2 above the VLM-verify entropy threshold (0.5) — but these are BENIGN timing races (identical pixel, 'did the tap register'), since all 6 trajs are labelled success |
| Outcome distributions flag ADVERSARIAL interference (popup / login-wall / captcha / wrong-product) | ❌ no | 0 failure/interference trajs in corpus — the headline RQ2 'catch misbehavior' claim has zero supporting samples until these are collected |
| sava is strictly more conservative than legacy (fewer promotions) | ✅ yes | legacy promoted 34/34 (every edge on first write) vs sava 2/34 on identical input |

### 5.1  The multi-modal distributions actually observed

These are the (page, action) pairs with >1 distinct outcome. They are the *real* RQ2 non-determinism signal in this corpus — and notably they are coordinate taps at an IDENTICAL pixel that nonetheless landed on different pages (a tap-registration / loading race), NOT adversarial interference:

| source page | action | outcomes | entropy | > VLM thr? |
|---|---|---|---|---|
| search_input | Tap:[893, 71] | {'search_input': 2, 'search_result': 2} | 0.693 | yes |
| search_result | Tap:[936, 166] | {'search_result': 1, 'filter_panel': 1} | 0.693 | yes |

## 6  What real-device collection must still gather

1. Collect >=10 real-device trajectories that DELIBERATELY hit failure / interference (popup, login-wall, captcha, wrong-product selection). The corpus already shows 2 BENIGN (timing-race) multi-modal distribution(s), but ZERO adversarial-interference ones — the headline RQ2 'catch misbehavior' claim needs the latter.
2. ZERO cross-page navigation edges promoted under sava: the only promotions are degenerate empty-target self-loops. Either (a) make promotion key on the enriched SEMANTIC target so repeated cross-page taps merge, or (b) accept that raw-pixel taps cannot earn promotion and rely on bootstrap promotion for navigation edges.
3. 3 edge(s) sit at vc=2 (one verified repeat short of sava promotion). For EMPTY-target edges (Swipe/Type) re-running the same task tips them over; for coordinate taps it will not, because the pixels drift between runs.
4. For coordinate-tap edges to accumulate vc across runs, promotion must key on the enriched SEMANTIC target (region+page affordance), not raw pixels; otherwise each run is a fresh edge.

## 7  Honest boundaries (carry into any write-up)

- No end-to-end success rate (zero pixels replayed, grounding untested).
- No latency / wall-clock (replay has no real timing).
- No quantitative N∈{3,5} claim — the corpus has zero LABELLED failures, and its only multimodality is benign timing-race non-determinism, not adversarial interference.
- RQ2 mechanism findings are QUALITATIVE and from an N≈6, all-success corpus (optimistic).
- The 2 promoted edges are degenerate empty-target self-loops; zero useful navigation edges earned promotion from this corpus.
- Ground truth is the trajectory's observed page_type — the only design that escapes the 'test the graph with the graph' circularity.
