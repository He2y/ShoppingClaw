# AMSG 采集/构图管线规模化就绪度评估

> **定位**：回答"现有数据采集 + 构图管线能否支撑规模化多 app 任务执行与采集"。服务"先把采集管线做好再考虑实验"。非论文稿件。
> **方法**：活库实测（`scripts/inspect_graph_state.py` 查 `shopping-spatial-v4`）+ workflow 六维度评估 + 两路对抗核验（grounding-check / completeness）。**所有 file:line 经核验，已修正 workflow 输出中 `phone_agent/spatial/` vs `memory/exploration/` 的路径错误。**
> **版本**：2026-06-29 | 活配置 `AMSG_CONFIG=sava`（N=3, dominance≥0.8）

---

## 0  一句话结论

**不支持。** 当前管线只能"逐条手敲任务"地单进程采集，距"无人值守批量入图"差一整个编排层；更深的问题是一组**机制级缺陷**使得"采更多数据"会产出污染图谱而非可靠图谱：**采得越多、污染越多**。最高杠杆的单点修复是**解冷启动死锁**——它能让京东 12 条已审核的 hypothesis 边当天变成可导航 promoted 边，无需重采。

---

## 1  规模化就绪度记分卡

| 维度 | 判定 | 依据（核验后） |
|---|---|---|
| 1. 批量任务执行与编排 | ❌ **missing** | 三入口（`main.py:933`/`exploration/cli.py:270`/`webui.py:593`）全单任务；`phone_agent/experiment/` 不存在；唯一批量循环在 vendored `MobiAgent/runner/UI-TARS-agent/batch_task_executor.py` 且不接 AMSG/Neo4j；全代码库无 ThreadPool/asyncio/multiprocessing |
| 2. 在线学习→图谱持久化 | ⚠️ partial | 链路完整、MERGE 幂等、降级在；但 lifecycle 回写有**两处确定性断链** + 跨 app 写串扰（详见 §2 P0-C） |
| 3. 陌生 App 引导与探索建图 | ⚠️ partial | 单 app onboarding/planner/explorer/watchdog 高度自动化；缺多轮无人值守驱动器；离线边无法独立产 promoted |
| 4. 多 App 注册与领域 Schema | ✅ **亮点** | 加常规购物 app 改 `app_registry.yaml` 不改代码（已注册 9 app/8 购物，活库只用 3）；`shopping.yaml` 一套 schema 服务全购物 app。短板：开放词表 `new:*` 只感知不学习、超纲页被 onboarding 直接 drop、包名双源（`config/apps.py` APP_PACKAGES 硬编码） |
| 5. 冷启动提升缺口 | 🔴 manual-bottleneck | 三条注入路径（离线审核/人工辅助/手工轨迹）全被"写死 hypothesis + sava N=3 在线验证"锁死；无 bootstrap 直入开关、无 repromote/backfill CLI |
| 6. 规模化运维 | 🔴 manual-bottleneck | 单设备单进程全串行；`human_gate.py:53` Windows `input()` 无超时（captcha/登录无限阻塞）；`model/client.py:87` **无重试/退避/限流/超时/成本计数**；截图全分辨率 PNG 无压缩 |

---

## 2  关键缺口清单（按规模化阻断优先级）

### 🔴 P0-A｜冷启动死锁（机制性，最高杠杆）
- **是什么**：`spatial/review/apply.py:124-128` 把所有审核通过的边写死 `lifecycle_stage='hypothesis', verification_count=0`，无 `source_kind`/`bootstrap` 豁免；在线规划查询只召回 promoted 边（`spatial_graph_memory.py` `coalesce(a.lifecycle_stage,'promoted')='promoted'`）。
- **为什么卡**：hypothesis 边对 agent 不可见 → 不被主动重走 → sava N=3 门永远凑不满 → 永不 promoted。**京东 12 hypothesis/0 promoted 的直接代码根因。**
- **修法**：给 `apply_review()` 加 `bootstrap` 参数，冷 app（节点 < 阈值，复用 `domain_priors.coverage_is_cold`）的审核边/`human_assisted` 边直入 `promoted`；或在线侧对冷 app 放宽规划查询召回 hypothesis（标低置信）闭合验证回路。
- **工作量** S（0.5–1 天）。**不修这条，所有数据补采都是无效功。**

### 🔴 P0-B｜零号输入阻断：根本没有任务集
- **是什么**：全仓库**无 `datasets/`、零个 `*.jsonl`**（实测 `find` 返回空），`trajectories/` 仅 6 条全淘宝、任务靠手敲（文件名即任务）。
- **为什么卡**：所有批量运行器即便建好也无 N 个任务可读。先于一切编排层的根因。
- **修法**：建 `datasets/shopping_tasks.jsonl`（`{app, task, focus?, expected_app?}`，按购物链路阶段组织）。三种自动生成：① 从 `shopping.yaml` `coverage_transitions` 展开 ② 从成功轨迹反推 ③ 模板 `{app}` 占位。淘宝/京东各先 30 条覆盖现有 hypothesis 边。
- **工作量** S-M。

### 🔴 P0-C｜两处确定性 lifecycle 回写断链 + 跨 app 串扰
- **断链一（edge_key 旁路）**：`promote_staging_to_canonical` 先把 `action_target` enrich 成语义串（`spatial_graph_memory.py:1181`，如 `'center product_detail affordance'`），`_build_lifecycle_dict` 用 enriched key 查 `get_record()`（:2058），但 `record_observation` 当初用**原始** target 存记录（:1661-1663）→ 坐标点击边两 key 必不同 → `get_record()` 返回 None → 落默认分支。**在 sava（verified）下默认写 `hypothesis@1`**（注：这解释京东式污染；**不是**淘宝 promoted@1，见 §2.5）。注意提升门 `_is_promotable_edge`（:2109）用未 enrich 的 key、是一致的——被旁路的只是**持久化的 lifecycle 字段**。
- **断链二（intent≠action_type）**：`graph_lifecycle_store.py:44` 用 `coalesce(a.type,'')=row.intent` 匹配，但写入侧 `graph_store.py:1167` 存 `a.type=action_type`（如 `Swipe`/`Tap`），lifecycle 的 `intent` 经映射变 `scroll`/`type_text`/`go_back`。**凡 intent≠action_type 的动作（滑动/输入/返回）MATCH 必失败且静默跳过**（docstring 自承 "silently skipped"）。
- **跨 app 串扰**：`graph_lifecycle_store.py:42` 的 MATCH **无 `s.app/t.app` 约束**（对比 :79 读查询有）；坐标边 enriched target app 无关 → 多购物 app 共库时**确定性**跨 app 误更新。
- **为什么卡**：lifecycle 验证统计无法正确回写，规模化批跑时图谱**静默停止积累验证票**（持久化层 `except: pass`，:1760）。
- **修法**：统一 record/build 两侧 edge_key；统一 `a.type` 全链路口径；MATCH 加 app 过滤；`except:pass` 改 `logger.warning + 计数`。**必配回归测试**守护"promote 路径 vc 真实累加"（`tests/test_spatial_graph_memory.py` 实测零覆盖此不变量）。
- **工作量** S-M。

### 🟠 P1-A｜无批量执行编排层
- **修法**：建 `phone_agent/experiment/runner.py`：读 jsonl、`for task` 循环复用 PhoneAgent + `agent.run()/reset()`（reset 已存在 `agent.py:461`）、任务间设备复位钩子（`press_home + app_start(stop=True)`）、per-task try/except 隔离、记账、断点续跑。循环骨架照抄 `batch_task_executor.py:672-729`，执行体换 PhoneAgent。**工作量** M。

### 🟠 P1-B｜模型/强 VLM API 无护栏（workflow 评估完全遗漏，核验补出）
- **是什么**：`model/client.py:87` 单次 `chat.completions.create`，**无 try/except、max_retries、backoff、429 处理、显式 timeout**；StrongPlanner 同样。
- **为什么卡**：规模化 = 多 app×多轮×每步（小模型+强 VLM）高频付费云 API；单个 429/超时直接崩任务不可续跑；无速率护栏撞配额；无 token 计数成本不可控。**批跑最现实的工程与财务天花板。**
- **修法**：`client.py` 包重试（指数退避+jitter+429/超时识别）、加 timeout、加调用/token 计数。**工作量** S-M。

### 🟠 P1-C｜HumanGate 无超时 + 单设备串行
- HumanGate 加跨平台超时（Windows 独立线程读 stdin），超时 skip + "需人工补采"清单；captcha/登录事件汇总成批次人工待办。**工作量** S-M。
- 多设备并行：拆全局单例 device_factory（`device_id` 已贯穿底层）。**⚠️ 上并行前必须先做并发写控制**——多进程并发 SET 同一 Action 的 `verification_count`/`dominance` 会 last-writer-wins 损坏统计。**工作量** M-L，**优先级 P2**。

### 🟡 P2｜历史脏数据 + 开放词表死路
- **淘宝 35 条 promoted@vc=1 = 历史脏数据**（见 §2.5），先 purge/re-validate（复用 `cleanup_polluted_graph.py`/`inspect_graph_state.py`）——**否则经 `domain_priors` 跨 app 传播污染新 app**。
- **开放词表单向死路**：`new:*` 只导出文件，无代码 promote 进 schema；onboarding 超纲页直接 drop → 新 app 被同构成淘宝/京东骨架。补 schema_evolution 闭环。

### 2.5  vc=1 真相（核验修正，对论文关键）
活配置 sava=verified 下，**没有任何代码路径写得出 promoted@vc=1**（`add_state_transition` 不默认 promoted；default 分支在 verified 下写 hypothesis；promote 需 vc≥3）。故：
- **京东 hypothesis 污染** = P0-C 断链一在 sava 下写 hypothesis@1 + P0-A hypothesis 写死。
- **淘宝 promoted@vc=1** = **早期 legacy 配置遗留脏数据**（在线 flush 默认分支在 legacy 下直接写 promoted@1；`source_type=<none>` 在线学习 + conf=1.0 + vc=1 正是 legacy 默认分支签名）。
- **结论**：现有 promoted 边**不是 sava N=3 挣来的统计可靠边，是 legacy 脏数据**。RQ2 实验前图谱必须 sava 下重建干净。

---

## 3  规模化落地路线（先做这步再考虑实验）

### 阶段 0：解锁机制 + 备好输入（~1 周，纯代码，零真机，可全自动化）
1. **修 P0-A 冷启动死锁**（bootstrap 直入）→ 京东 12 hypothesis 立即变可用 promoted。
2. **修 P0-C 两处 lifecycle 断链 + 跨 app 过滤** + 回归测试 + 去 `except:pass`。
3. **建 P0-B 任务集** `datasets/shopping_tasks.jsonl`。
4. **清 P2 历史脏数据**（淘宝 35 条 promoted@1）。
5. **加 config 启动校验**（打印生效 AMSG_CONFIG，防 sava→legacy 静默回退）。

### 阶段 1：单机无人值守批跑（1–2 周，代码为主 + 一次性人工准备）
6. 建 ExperimentRunner（P1-A）；7. HumanGate 超时（P1-C）；8. API 重试/限流/成本计数（P1-B）。
- **必须人工**：每 app 一次性登录（持久化用户态）、首跑过 captcha、draft→confirmed、trap 词抽查。
- 交付：`python -m phone_agent.experiment.runner --tasks shopping_tasks.jsonl` 无人值守跑完几十任务。

### 阶段 2：多 app 扩展 + 吞吐放大（按需，2–4 周）
9. 批量 onboarding 脚手架（拼多多/美团/盒马/叮咚等已注册 app）；10. 多设备并行（先做并发写控制）；11. schema_evolution 闭环。

**人工天花板**：每 app 一次性登录/账号准备、首跑 captcha、draft 确认、trap 抽查、审核 batch 人工复核——**无法用代码消除**，按"每 app 0.5–1 天 × app 数"列入计划（购物 app 单账号风控使多设备并行还受账号资源约束）。

---

## 4  对实验计划的影响（衔接 P2.5 数据补采与京东建图）

1. **京东建图直接受益于 P0-A**：做完 bootstrap 直入，京东 12 条审核过的 hypothesis **当天提升为可导航 promoted**，不用重跑真机——P2.5 最高杠杆单点改动。
2. **P2.5 补采的前置硬依赖**：补采前必须先完成阶段 0+1，否则手肉逐条采、采到的边因 P0-C 断链无法回写验证统计、因 P0-A 死锁永卡 hypothesis——**采集量↑→污染量↑**。
3. **下单段结构性盲区**：checkout/payment 是 high_risk，`_is_promotable_edge` 对 high risk 不提升 → **"加购→结算→下单"段永远进不了 promoted**。实验覆盖目标须明确只承诺到加购前，或单独为下单段设豁免。
4. **假 success 污染**：`flush` 仅 success，但 ProgressWatchdog 耗尽 restart 预算会 `success=True` 假性结束、VLM 幻觉也会误判 → 假边入图。ExperimentRunner 须加 success 二次校验（强 VLM 复核终态截图）。
5. **跨 app 先验传播污染**：补京东/新 app 前**必须先清淘宝脏数据**，否则 `domain_priors` 把脏边当可信先验扩散。**正确顺序：清脏数据 → 修死锁 → 提升京东 → 建任务集 → 批跑补采**，不可颠倒。

---

## 附：核验要点（grounding-check / completeness 两路确认）
- sava=N=3/dominance≥0.8/verified 确认（`amsg_config.py:128-130` + `.env:37`）。
- edge_key 旁路 bug 存在确认；但在 sava 下写 hypothesis@1 非 promoted@1（淘宝 promoted@1 = legacy 遗留）。
- 离线审核边写死 hypothesis 确认（`apply.py:126-128`）。
- intent≠action_type MATCH 失败、跨 app 无过滤确认（`graph_lifecycle_store.py:42-46`）。
- API 无重试确认（`model/client.py` grep retry/backoff/429 全零）。
- 加 app 改 yaml 不改代码确认（`app_registry.yaml` 9 app/8 shopping）。
- 无任务集确认（`find *.jsonl` 空、`datasets/` 不存在）。
