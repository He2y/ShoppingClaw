# AMSG 进度与下一步执行计划（handoff）

> **用途**：冷启动接续工作的交接文档。下次开工先读这份。
> **日期**：2026-06-29 | **分支**：refactor/architecture-optimization
> **当前阶段**：采集/构图管线 Stage 0 已完成 → 下一步 Option A（离线 TrajReplay runner，**无需真机**）

---

## 0  一句话状态

管线数据正确性地基（Stage 0）已夯实，京东已可导航，脏数据已隔离。**下一步先做不需要真机、真能无人值守的离线 TrajReplay runner**——验证修复、出首批 RQ2 方向、量化真机数据缺口；之后再做绕不开人工的真机监督式采集。

---

## 1  已完成工作（本会话全程）

### 1.1  调研与评估（3 份过程文档，均在 papers/）
- `amsg-experiment-plan.md` — 实验执行计划：RQ1-4 可测性裁决、混合评测策略、Fast Path vs RuntimeDAG 澄清、消融矩阵。
- `amsg-sim-env-analysis.md` — 三篇论文（Beyond-the-GUI/PhoneBuddy/MobileForge）精读 + 对抗核验：5 个模拟环境方案排序，结论"先补数据再造 sim"。
- `amsg-pipeline-scalability.md` — 采集/构图管线规模化评估：记分卡 + 缺口清单 + vc=1 真相。

### 1.2  Stage 0 代码修复（全部完成，每步独立提交，全量 402 测试绿）
| commit | 内容 |
|---|---|
| `fb29c2a` | **P0-A** `apply_review(bootstrap=True, force=True)` 冷启动直入 promoted（opt-in、默认仍 hypothesis、high-risk 边排除） |
| `c398c34` | `scripts/bootstrap_cold_app.py` + **京东 12 hypothesis→12 promoted 实际提升**（vc=3、备份回滚） |
| `bd8110a` | **P0-C** lifecycle 写修复：`_build_lifecycle_dict` 原始证据回退查询（坐标边真实统计不再落假 hypothesis@1）+ `persist_lifecycle_batch` 加 app 作用域 + 2 处 except:pass→logger.warning |
| `2789efd` | **config 启动校验**（防静默回退 legacy 污染）+ **脏边屏蔽**（`mark_legacy_dirty.py` 标记淘宝 35 条 + domain_priors 跨 app 先验排除 dirty_legacy） |

### 1.3  沉淀的可复用工具（scripts/）
- `inspect_graph_state.py` — 只读图谱审计（补数据后随时核对进度）
- `bootstrap_cold_app.py` — 冷 app 边提升（加新 app 时复用，dry-run+备份）
- `mark_legacy_dirty.py` — legacy 脏边标记/回滚

### 1.4  当前活库状态（`shopping-spatial-v4`，23 节点）
| app | promoted | 性质 | 跨 app 先验 |
|---|---|---|---|
| 京东 | 12 (vc=3) | bootstrap，人工审核，干净 | ✅ 可借 |
| 淘宝 | 35 (vc=1) | legacy 脏数据，已标记 dirty_legacy | 🚫 已屏蔽 |
| system_home | 2 节点 | — | — |

---

## 2  关键认知（避免重复踩坑，下次必读）

1. **Fast Path 档 ≠ RuntimeDAG**：RQ3 真卖点是 Fast Path 调度档（`dispatch="fast_path"`，跳小模型），它本就可测；RuntimeDAG（跳管线/分类器）结构性坏掉，降为 limitation。
2. **vc=1 真相**：淘宝 35 promoted@vc=1 是**早期 legacy 配置遗留脏数据**（非 sava N=3 挣得）；京东曾全 hypothesis 是 `apply.py` 写死 + edge_key 旁路 bug。当前 sava 下无任何路径写得出 promoted@vc=1。
3. **数据贫瘠是真瓶颈**，不是环境成本——模拟器造不出从没记录过的失败。实测仅 ≈4 条可用轨迹、全成功零失败、`_expected_postcondition` 全库 1 个点。
4. **真机无法无人值守**：高干扰 + 人机验证有不可消除的人工地板。离线后端才能真无人值守；真机后端追求"人工最小化监督式"，不是消除人工。
5. **`dirty_legacy` 只影响跨 app 先验**：淘宝自身导航（`spatial_graph_memory.py:2351/2371` 的 per-app 查询）**不过滤** dirty_legacy，淘宝运行时照常可用；脏标记只防其跨 app 传播污染新 app。
6. **P0-C 深层残留（动 online 学习前必读）**：`record_observation` 在 lifecycle key 的 intent 位填 `action_type`("Tap")，而 `load_lifecycle_records` 读 `a.intent`(mapped "tap")——跨会话 reload 后新观测无法与已有计数合并。需专门统一 5 处 key 体系 + 全面测试。

---

## 3  下一步：Option A — 离线 TrajReplay runner（⭐ 不需要真机，可先做）

**目标**：把现有轨迹的观测流离线回放进（已修复的 P0-C）lifecycle/调度管线，产出首批 RQ2 机制方向 + 验证修复 + 量化真机数据缺口。**100% 软件、可循环、真无人值守**。

**前置依赖**：无。用现有 6 条轨迹 + 已修复管线即可起步，**连真机前就能做**。

**为什么先做它**：① 你担心的"无人值守"问题在这一半不存在；② 直接验证 P0-C 修复在真实观测流上确实让坐标边正确累计验证；③ 用数据坐实"6 条全成功够 RQ2 到什么程度"，告诉真机采集到底非补不可的是什么（失败/干扰样本）。

### 3.1  要建什么
1. **`phone_agent/experiment/__init__.py` + `replay_lifecycle.py`**
   - `load_trajectories(dir)` → 读 `memory_db/default/trajectories/*.json`
   - `extract_observations(traj)` → `[(source_page_type, action, observed_target_page_type)]`；**清洗空/unknown page_type 步**（实测 ~20% 步无可用标签）
   - `replay(observations, config)` → 新建 `SpatialGraphMemory(config)`，逐步 `record_observation(...)`，返回 lifecycle 快照（每边 stage / verification_count / dominance / outcome 分布）
   - `compare_configs(observations)` → `sava` vs `legacy`（再叠加开/关后条件验证）对比
2. **报告聚合**：per-config 对比表（stage 分布、vc 累计、错误快路径率=would-be fast_path_fallback、promoted 精确率、N∈{1,3,5} 敏感性）+ **数据缺口报告**（可用轨迹数、成功/失败覆盖、哪些 RQ2 子主张有/无数据支撑）
3. **（可选）"去后条件验证"开关（原 F2）**——回放时可关 t+1 验证做 RQ2 核心消融（验证"统计真实性根基"主张）
4. **CLI**：`python -m phone_agent.experiment.replay_lifecycle --trajectories memory_db/default/trajectories --report`

### 3.2  TDD
- 合成轨迹单测：构造含坐标边的观测流，断言 sava 下达标边 promoted、未达标 hypothesis；断言 P0-C 修复（坐标边 vc 真实累计非默认@1，复用 `tests/test_lifecycle_persistence.py` 的模式）。
- 跑现有 6 条轨迹出真实报告。
- 跑全量测试套件确认无回归（带 `--basetemp`）。

### 3.3  验收
- 产出 sava vs legacy 的 lifecycle 对比表。
- 证明 P0-C：坐标边在真实观测流上正确累计 vc（非默认@1）。
- 产出**数据缺口报告**：量化"6 条全成功轨迹够 RQ2 到什么程度"，明确还缺多少失败/干扰样本。

### 3.4  产出对接
- RQ2 机制方向（**定性，明标 N≈6、全成功语料偏乐观**，不出 N 定量敏感性——sim-env 分析红线）。
- 数据缺口报告 → 直接定义真机采集阶段要补的失败/干扰轨迹数量与类型。

### 3.5  诚实边界（写报告时遵守）
- 不能 claim 任何端到端成功率（零像素，grounding 未测）、任何延迟（回放无壁钟）、任何 N∈{3,5} 定量（语料零失败）。
- 真值来源 = 轨迹实测 page_type（外生，独立于图谱预测）→ 这是 RQ2 唯一能洗脱"用图测图"循环论证的设计，方法节要讲清。

---

## 4  后续待做（需真机 / 更大投入，Option A 之后）

| 项 | 性质 | 说明 |
|---|---|---|
| **真机监督式采集 harness** | 需真机 + **人工地板** | 不是"无人值守"，是"人工最小化"：① 登录态持久化（每 app 登录一次跨任务复用，干掉绝大多数登录墙）② 机械层全自动（崩溃重启/弹窗消解/前台漂移拉回，复用 interference.py）③ captcha 蜂鸣队列接力（超时标"待人工"跳过，人攒批集中解）④ 断点续跑 ⑤ 强 VLM success 二次校验（防假 success 入图） |
| **P0-B 任务集** | 软件 | `datasets/shopping_tasks.jsonl`，从 `shopping.yaml` coverage 展开 + 成功轨迹反推。批量采集的输入，两条路共用 |
| **P2.5 补失败/干扰轨迹** | 需真机 | 故意触发弹窗/登录墙/captcha/选错商品，让 OutcomeDistribution 出现真实多峰、让 RQ2 有"会错"样本。**Option A 的数据缺口报告会量化要补多少** |
| **深层 key 统一** | 软件 | §2.6 的 intent action_type vs mapped 跨会话合并问题。动 online 学习/跨会话累积前做 |

---

## 5  建议执行顺序

```
下次开工(可无真机):  Option A 离线 runner  +  P0-B 任务集
                          │
                          ▼ (拿到数据缺口报告)
连上真机后:          真机监督式采集 harness  →  P2.5 补失败/干扰轨迹
                          │
                          ▼
                     深层 key 统一(动跨会话累积前)  →  正式实验(头条表/延迟/RQ2定量)
```

**起步点**：`phone_agent/experiment/replay_lifecycle.py`（Option A §3.1）。无需真机，随时可开。
