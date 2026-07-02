# AMSG 完整实验方案

> **定位**：综合 MemGUI-Bench + MobileGym + 6 项主流 benchmark 方法论，为 AMSG 空间图谱与整体 agent 设计完整实验方案。覆盖实验数据构建、评价指标选择、真机/模拟器/公开测评集决策、基线实现、消融执行与统计严谨性。
> **版本**：2026-07-02（初版）
> **前置阅读**：`amsg-experiment-plan.md`（执行计划，含修复清单与阶段里程碑）、`amsg-sim-env-analysis.md`（模拟环境方案分析）、`amsg-paper-cn.md`（论文正文）

---

## 0 方法论来源总览

本方案综合以下 9 项工作的实验方法论，逐项提取可借鉴点并适配 AMSG 的购物场景：

| 来源 | 类型 | 核心方法论贡献 | 对 AMSG 的可借鉴点 |
|------|------|---------------|-------------------|
| **MemGUI-Bench** (arXiv 2602.06075) | 记忆评测基准 | 7 层次指标 + Progressive Scrutiny 三阶段验证管线 + 镜像任务对(pass@k) + MTPR 记忆隔离度量 | 边生命周期的 memory-specific 指标设计；快路径错误分类体系 |
| **MobileGym** (arXiv 2605.26114) | 模拟环境 + 基准 | 三层状态模型 + 声明式导航 FSM + AnswerSheet 协议 + 确定性代码裁判 + Sim-to-Real 95.1% 保留率 | 模拟购物 App 构建方案；可控实验环境 |
| **AndroidWorld** (ICLR 2025) | 动态基准 | 参数化任务模板(million+ instances) + OS 状态 rule-based 验证 + 三类泛化 split | 任务参数化方法；非 LLM-judge 验证 |
| **SimuWoB** (arXiv 2605.25160) | 合成模拟 | 两阶段 LLM 生成模拟 App + DOM-level API 验证 + 用户研究驱动任务设计 | 轻量级环境构建；executable validators |
| **PSPA-Bench** (arXiv 2603.29318) | 个性化基准 | Task Decomposition Graph（固定/灵活节点分离）+ APR/PPR 双指标 + 长期增量度量 ΔAPR | 任务分解方法论；个性化指标设计 |
| **GUI-CEval** (arXiv 2603.15039) | 中文综合基准 | 5 维能力诊断框架（感知/规划/反思/执行/评价）+ 真机评测 | 能力维度诊断；中文 App 评测经验 |
| **EComAgentBench** (arXiv 2606.17698) | 购物基准 | 分布式隐藏意图（visible/hidden/clarified）+ typed source-tagged rubrics | 购物任务意图分层；失败归因方法 |
| **ShoppingBench** (arXiv 2508.04266) | 购物基准 | 2.5M+ 真实商品 + 复杂约束（vouchers/budget/multi-product）+ 轨迹蒸馏训练 | 真实商品 grounding；约束组合设计 |
| **Mobile-Bench-v2** (arXiv 2505.11891) | 多路径评测 | slot-based 指令生成 + 噪声/歧义 split | 指令变体生成；鲁棒性 split |

---

## 1 实验数据构建

### 1.1 任务设计空间

结合购物场景特征与上述方法论，AMSG 任务设计覆盖以下维度：

```
任务空间 = App × 场景 × 约束类型 × 约束强度 × 干扰类型 × 指令变体
```

**App（2 个）**：淘宝、京东（含秒送外卖）

**场景（6 类）**：
| 场景 | 示例 | 典型步数 | 涉及页面类型 |
|------|------|---------|-------------|
| 单品搜索购买 | "在淘宝买一包 500g 的蓝山咖啡豆" | 8-15 | home→search_input→search_result→product_detail→spec_selection→cart |
| 多条件筛选 | "找一款 200-500 元、4.5 星以上、京东自营的蓝牙耳机" | 10-20 | search_result→filter_panel→search_result→product_detail |
| 跨店比价 | "比较淘宝和京东上 iPhone 15 128GB 的价格" | 15-30 | 跨 App 切换 ×2 |
| 凑单满减 | "在京东凑满 99 元免邮，加一件 20-30 元的商品" | 12-25 | cart→search_result→product_detail→cart |
| 即时零售 | "在京东秒送买一箱牛奶和面包，选最近的门店" | 8-18 | channel_home→shop_list→store→product_detail→cart→checkout |
| 异常恢复 | "购买过程中遇到弹窗/登录墙后继续完成任务" | 10-25 | 含 dialog/login/captcha 干扰 |

**约束类型（3 类 × 各 2-3 强度）**：
| 约束类型 | 弱 | 中 | 强 |
|---------|-----|-----|-----|
| 价格区间 | 单边（≤X 元） | 双边（X-Y 元） | 双边 + 多商品总价约束 |
| 规格选择 | 1 维（颜色） | 2 维（颜色+尺寸） | 3 维 + 互斥约束 |
| 品牌/平台 | 不限 | 指定品牌 | 指定品牌+自营/旗舰店 |

**干扰类型（5 类，对应真实 App 环境）**：
弹窗广告、登录墙、A/B 版本差异、促销浮层、加载延迟

### 1.2 任务生成方法（借鉴 AndroidWorld + SimuWoB + EComAgentBench）

采用**参数化模板 + 语义槽位实例化**两层结构：

**第一层：任务模板（YAML）**
```yaml
# 模板示例
id: shop_single_filtered
app: taobao
scenario: 单品搜索购买
template: "在{app_name}买{quantity}{unit}的{product}，要求{constraints}"
slots:
  app_name: [淘宝, 京东]
  quantity: [1, 2]
  unit: [包, 盒, 瓶, 个]
  product:  # 从商品池采样
    category: [咖啡豆, 蓝牙耳机, 充电宝, 洗衣液, 手机壳]
    attributes: [品牌, 规格, 口味]
  constraints:  # 从约束池组合
    - "价格不超过{price}元"
    - "{brand}品牌"
    - "评分{rating}以上"
    - "{platform}发货"
```

**第二层：实例化（程序化参数采样）**

借鉴 AndroidWorld 的 `random.Random(seed)` 确定性参数化：
- 每个模板用 5 个不同 seed 生成 5 个实例 → 保证可复现
- 商品从预设商品池采样（覆盖不同品类、价格带）
- 约束按组合覆盖（确保每种约束类型至少出现在 3 个任务中）

**规模目标**（thesis-sufficient）：
| 来源 | 数量 | 说明 |
|------|------|------|
| 模板 | 15-20 | 覆盖 6 场景 × 3 约束类的交叉 |
| 实例（淘宝） | 25-30 | 每个模板 1-2 个淘宝实例 |
| 实例（京东） | 15-20 | 每个模板 1 个京东实例 |
| 含干扰实例 | ≥10 | 含弹窗/登录墙，用于 RQ2 失败样本 |
| **总计** | **30-50** | 与 `amsg-experiment-plan.md` §6 一致 |

**借鉴 EComAgentBench 的意图分层**：
- 每个任务标注 visible intent（指令中明示的约束）和 hidden intent（需在执行中发现的隐含约束，如"选自营"未明说但期望如此）
- 失败分析时按意图来源分归因

### 1.3 数据构建的三种路径及选择

| 路径 | 成本 | 可控性 | 真实度 | AMSG 适用性 |
|------|------|--------|--------|------------|
| **A. 纯人工构建** | 高（2-4 周） | 最高 | 高 | 适合核心任务集 |
| **B. 探索管线自动采集** | 低（已有） | 中 | 最高 | 适合图谱构建，不适合任务设计 |
| **C. 强 VLM 合成** | 极低 | 低（需人审） | 中 | 适合扩充变体，不适合核心集 |

**推荐方案**：A（人工构建 15-20 模板 + 商品池）+ C（VLM 生成指令变体，人审后入库）+ B（探索管线补页面覆盖）

### 1.4 商品池构建

参考 ShoppingBench 的真实商品 grounding：
- 每个品类预选 10-15 个真实商品（含名称、价格区间、品牌、规格选项）
- 商品覆盖不同价格带（低/中/高）和品牌（知名/小众）
- 定期刷新（真机实验前 1 天确认价格/库存未变）

---

## 2 评价指标体系

### 2.1 四组指标（承袭论文 §4.4）

#### 任务级指标
| 指标 | 定义 | 测量方法 | 借鉴来源 |
|------|------|---------|---------|
| **任务成功率 (SR)** | 完成目标的任务数 / 总任务数 | 里程碑监督者判定 + 人工抽检（≥10% 样本） | AndroidWorld（rule-based）+ MemGUI-Bench（progressive scrutiny） |
| **平均步数** | 成功任务的执行步数均值 | 轨迹 step 计数 | 全基准通用 |
| **端到端延迟** | 任务开始到完成的壁钟时间 | harness 计时 | — |
| **小模型调用数** | 每任务 autoglm-phone-9b 调用次数 | dispatch="model" 步数 | — |
| **强 VLM 调用数** | 每任务强 VLM 调用次数（分类器 + 监督者） | vlm_call_count | — |
| **每任务成本** | API 调用费用合计 | token 统计 × 单价 | PSPA-Bench CPT |

#### 图谱级指标
| 指标 | 定义 | 测量方法 | 借鉴来源 |
|------|------|---------|---------|
| **快速路径命中率** | fast_path 步数 / 总步数 | dispatch 分档遥测 | — |
| **错误快路径率** | fast_path_fallback / (fast_path+fallback) | 后条件验证失败计数 | MemGUI-Bench IRR（概念同源：信息保真度） |
| **promoted 边精确率** | promoted 边中实际可靠的占比 | held-out 真机 page_type 验证 | MemGUI-Bench MTPR（记忆隔离度量） |
| **图谱覆盖率** | 已覆盖转移数 / 域模式覆盖目标数 | Neo4j 查询 | — |
| **边生命周期分布** | hypothesis/candidate/promoted/demoted 各阶段边数 | EdgeLifecycleManager 统计 | — |

#### 鲁棒性指标（借鉴 MemGUI-Bench 的 FRR + PSPA-Bench 的 Δ 指标）
| 指标 | 定义 | 测量方法 |
|------|------|---------|
| **自修复任务数** | UI 漂移注入后恢复到注入前性能所需任务数 | 注入 k 条边失效 → 跑任务至恢复 |
| **失败恢复率 (FRR)** | 失败后在下一次尝试中成功的概率 | pass@k 协议（k=3） |
| **跨任务学习增益 (ΔSR)** | 后期任务 SR - 前期任务 SR | 前 10 vs 后 10 任务 SR 差 |
| **干扰鲁棒性** | 含干扰任务的 SR / 纯净任务 SR | 配对比较 |

#### 建图成本指标
| 指标 | 定义 | 测量方法 |
|------|------|---------|
| **探索轮次** | 达到核心覆盖所需的探索轮数 | 分轮覆盖统计 |
| **人工审核漏斗** | 探索候选→审核批准→在线提升 各环节转化率 | staging pipeline 统计 |
| **建图时间** | 从零图谱到核心链路可用的总人工+机时 | 计时 |
| **冷启动任务成功率提升** | 建图前 vs 建图后 SR | 配对比较 |

### 2.2 MemGUI-Bench 启发的增量指标

MemGUI-Bench 的 7 层次指标体系中有几项可直接适配 AMSG：

| MemGUI-Bench 指标 | AMSG 适配 | 测量方式 |
|-------------------|-----------|---------|
| **IRR** (Information Retention Rate) | 边成功率统计的准确度——后条件验证确认"实际到达页 = 预期页"的比例 | `record_outcome(success) / total` |
| **MTPR** (Memory-Task Proficiency Ratio) | 图谱增强 SR / 纯 VLM SR，隔离"图谱记忆"的净贡献 | (S4 SR - S1 SR) / S1 SR |
| **FRR** (Failure Recovery Rate) | 边被降级后的重新提升率 | demoted→promoted 任务数统计 |
| **pass@k SR** | k 次尝试内至少成功一次的概率（衡量 long-term learning） | 同一任务跑 3 次，记录 n/3 成功 |
| **Avg Step Ratio** | 实际步数 / 最短路径步数 | Dijkstra 最短路径长度 / 实际步数 |

### 2.3 验证方法：三层递进裁决（借鉴 MemGUI-Bench Progressive Scrutiny）

AMSG 已有里程碑监督者做完成判定；借鉴 MemGUI-Bench 的三阶段验证管线增强任务成功判定：

| 阶段 | 方法 | 适用任务 | 成本 |
|------|------|---------|------|
| **S1 快速裁决** | 终态 page_type 匹配 + 购物车/订单页检测 | 明确终态任务（如"加入购物车"） | 零 API 调用 |
| **S2 语义裁决** | 监督者 (强 VLM) 分析完整轨迹 + 终态截图 | 复杂约束任务（如"选最便宜的"） | 1 次强 VLM 调用 |
| **S3 人工裁决** | 人工审核 S2 不确定的案例 | ≤10% 样本 | 人工 |

**关键原则**（继承 AndroidWorld + SimuWoB）：能用代码验证的不用模型验证，能用结构化判定的不用自由文本匹配。

---

## 3 实验基础设施决策：真机 vs 模拟器 vs 公开测评集

### 3.1 三维比较

| 维度 | 真机（淘宝/京东） | 模拟器（MobileGym + 自建购物 App） | 公开测评集（AndroidWorld 等） |
|------|------------------|----------------------------------|------------------------------|
| **环境真实度** | ★★★★★（弹窗/广告/AB/网络波动全保留） | ★★★（可控注入干扰，但无真实活体） | ★★（开源 App，无电商活体） |
| **可复现性** | ★★（价格/库存/AB 不可控） | ★★★★★（snapshot/seed 确定性重置） | ★★★★★ |
| **验证可靠性** | ★★（只能靠页面分类+VLM，无系统级状态） | ★★★★★（代码直接读环境状态） | ★★★★★（OS ContentProvider） |
| **规模扩展** | ★（1 台设备串行，captcha 需人工） | ★★★★★（256 并行实例） | ★★★★ |
| **购物场景覆盖** | ★★★★★（原生淘宝/京东） | ★★（需自建，仅核心链路） | ☆（无中文电商） |
| **建图采集** | ★★★★★（真实探索数据） | ★★★（模拟探索，缺真实干扰信号） | ☆ |
| **成本** | 设备 + 人工陪跑 | 服务器 + 开发（自建 App 2-4 周） | 机器时间 |

### 3.2 推荐策略：三层金字塔

```
         ▲
        /  \
       / 真机 \
      / 锚定层  \          ← 头条表 + RQ3 延迟 + 京东建图 + Sim2Real gap
     /──────────\
    /  模拟器层   \         ← RQ2 机制消融 + RQ1a 增长曲线 + 消融矩阵
   /──────────────\
  /   离线回放层    \        ← RQ2 生命周期对比（已有轨迹）+ RQ1a 佐证
 /──────────────────\
```

**为什么不用公开测评集**：
- AndroidWorld/AndroidLab 不含中文电商 App → 无法测试购物导航
- MemGUI-Bench 的任务设计思路可借鉴，但其 26 个 App 无电商场景
- Mobile-Bench-v2 的 slot-based 指令生成可借鉴，但任务不覆盖购物约束
- **结论**：公开测评集提供方法论参考，但不作为 AMSG 的直接评测平台

### 3.3 各 RQ 的评测环境分配

| RQ | 主要环境 | 为什么 | 辅助环境 |
|-----|---------|--------|---------|
| **头条** (S1-S4 对比) | 真机 | 需要真实干扰 + 端到端成功率 | — |
| **RQ1a** (活性增长) | 真机 + 离线回放 | 需要真实在线学习过程 | 模拟器（可控复跑验证趋势） |
| **RQ1b** (自修复) | 模拟器（注入漂移） | 需要可控 UI 漂移 | 真机（单点佐证） |
| **RQ2** (可靠性) | 离线回放 + 模拟器 | 需要可控失败注入 | 真机（held-out 真值） |
| **RQ3** (控制边界) | 真机 | 延迟必须真机测 | 模拟器（dispatch 分档计数） |
| **RQ4** (冷启动) | 真机 | 京东建图必须真机 | — |
| 消融矩阵 | 模拟器 | 需要严格变量控制 | 真机（关键消融佐证） |

### 3.4 模拟环境具体方案

两阶段实现（详见 `amsg-sim-env-analysis.md` §3）：

**阶段 1（立即，无真机依赖）**：离线轨迹回放（方案 A）
- 已有 ~4 条可用淘宝轨迹的 (action, page_type) 流
- 喂入 EdgeLifecycleManager 对比 sava vs legacy 配置
- 产出：RQ2 机制方向（定性，N≈4）

**阶段 2（补数据后，2-4 周）**：MobileGym 模拟购物 App（方案 D 升级版）
- 在 MobileGym 框架上构建简化版淘宝/京东核心链路
- 声明式 FSM 建模：home→search_input→search_result→product_detail→spec_selection→cart→checkout
- 状态数据：商品池（JSON）+ 用户购物车（writable state）
- 任务生成：参数化模板实例化（复用 MobileGym 的 416 模板 + AnswerSheet 协议）
- 验证：MobileGym 的确定性代码裁判（读购物车状态/商品列表状态）
- 干扰注入：弹窗概率、登录拦截概率、价格漂移概率（从真机频次估计）

**关键约束**（承袭 `amsg-sim-env-analysis.md` §4）：
- 转移核概率**必须从真机频次估计，禁用图谱自身 promoted 结论**
- 模拟环境只用于机制消融，不 claim 端到端成功率
- 所有 sim 结论经方案 B（Sim2Real Gap 审计）校验

---

## 4 基线实现方案

### 4.1 四个系统配置（承袭论文 §4.3 + 执行计划 §1.2）

| 系统 | 协议 | 图谱状态 | 调度策略 | 实现方式 |
|------|------|---------|---------|---------|
| **S1 纯 VLM** | 无图谱 | 无 | 每步完整 VLM | ActionAdvisor 返回空 + 禁 fast_path + 禁 graph_hint |
| **S2 静态 RAG** | PG-Agent 式 | 冻结只读 | VLM 每步 + 图谱提示 | 冻结图谱(禁 flush) + 强制 `_needs_vlm=True` + 保留 graph_hint |
| **S3 静态 Teleport** | WebNavigator 式 | 冻结 + 全部边可重放 | 全部 promoted 走 fast_path | 全部 promoted 边 fast-path + 禁降级 |
| **S4 AMSG 完整** | 本文方法 | 在线学习 | 锚定逐边调度 | 默认 sava + warm-start bootstrap |

**公平性保证**：
- S2/S3 的图谱快照 = AMSG 在第 K 个任务后的图谱，冻结
- K 取 10（≈ 核心链路 covered 的最小任务数）
- 敏感性分析：K ∈ {5, 10, 15} 对各基线 SR 的影响
- S1-S3 复用同一套感知/执行/存储基建，差异仅归因于控制协议

### 4.2 基线实现工作量

对应 `amsg-experiment-plan.md` §2 的 F5（基线协议变体开关），在 harness 配置层建立：
- `--no-graph`：关闭图谱（S1）
- `--freeze-graph`：冻结图谱更新（S2/S3）
- `--force-vlm-all`：强制每步 VLM（S2）
- `--force-teleport-all`：全部边 fast-path + 禁降级（S3）

---

## 5 消融实验执行方案

### 5.1 消融矩阵（承袭论文 §4.5，标注实现状态与执行环境）

| 消融 | 开关/实现 | 对应 RQ | 执行环境 | 预期样本量 |
|------|----------|---------|---------|-----------|
| 图谱冻结（单变量在线更新对照） | 关 flush + 关生命周期更新 | RQ1 | 真机 + 模拟器 | 真机 20 + sim 50 |
| 去生命周期（legacy 预设） | `AMSG_CONFIG=legacy` | RQ2 | 真机 + 回放 | 回放 4 + 真机 20 |
| 去后条件验证 | 待建（F2） | RQ2 | 回放 + 模拟器 | 回放 4 + sim 30 |
| 去锚定分类 → 全 RAG | 全 VLM 每步（= S2） | RQ3 | 真机 | 20 |
| 去锚定分类 → 全 Teleport | 全 fast-path（= S3） | RQ3 | 真机 | 20 |
| N 敏感性 | N ∈ {1,3,5} | RQ2 | 回放 + 模拟器 | 回放 4 + sim 30 |
| θ 敏感性 | θ ∈ {0.6,0.8,0.9} | RQ2 | 回放 + 模拟器 | 回放 4 + sim 30 |
| 去域先验 | `AMSG_DOMAIN_PRIORS=0` | RQ4 | 真机 | 京东建图 15 |
| 封闭词表 | `open_vocab=False` | RQ4 | 真机 | 京东建图 15 |
| 小模型单模型探索 | `AMSG_STRONG_PLANNER=0` | RQ4 | 真机 | 京东探索 3 轮 |

### 5.2 消融实验的统计处理

- 每个配置运行 ≥3 次（真机）/ ≥5 次（模拟器）
- 报告均值 ± 95% CI
- 配对 t-test（真机用配对以控制活体环境变化）
- 效应量：Cohen's d（消融 vs 全量）

---

## 6 实验执行流程

### 6.1 实验矩阵

```
系统 (4) × 任务集 (2 App) × 任务实例 (30-50) × 重复次数 (3) = 720-1200 次真机任务执行
+ 离线回放 (4 轨迹 × 5 配置) = 20 次回放
+ 模拟器 (30 任务 × 8 消融配置 × 5 重复) = 1200 次 sim 执行
```

### 6.2 执行阶段（对齐 `amsg-experiment-plan.md` §4）

**P0: 对齐地基**（1 天）
- 审计 Neo4j 真实边数、各 lifecycle 分布
- 确认所有开关存废状态
- 产物：`experiments/baseline-graph-audit.md`

**P1: 搭 harness**（3-5 天）
- ExperimentRunner (H1) + 遥测落盘 (F3) + 聚合器 (H4)
- 6 条现有任务端到端跑通、产出指标表
- 产物：`experiments/harness/` 含 results.tsv

**P2: 任务集**（2-3 天）
- 按 §1.2 模板 + 商品池生成 30-50 任务
- 覆盖 6 场景 × 3 约束类 × 2 App
- 产物：`experiments/tasks.yaml`

**P2.5: 补采失败/干扰轨迹**（3-5 天真机）
- ≥10 条含弹窗/登录墙/captcha/选错商品的轨迹
- 让 OutcomeDistribution 出现真实多峰
- 产物：`memory_db/default/trajectories/` 新增轨迹

**P3: 修实现**（3-5 天）
- F1: 冷启动 promoted 注入
- F2: 去后条件验证开关
- F4: DB 漂移注入工具
- F5: 基线协议变体开关
- F6: sim 模式旁路 page_classifier

**P4: 京东建图**（2-3 天真机）
- 五阶段管线 → 核心链路 promoted
- OracleGoldenPath 补 3 条购买边（方案 C）

**P5: 跑实验**（7-10 天）
- 真机：头条表 + RQ1a + RQ3 + RQ4
- 离线：RQ2 回放对比
- 模拟器：消融矩阵（如 sim 环境就绪）

**P6: 出图成文**（3-5 天）
- 填 §4 占位 → 表 4-1~4-4 + 图
- `nature-reviewer` 模拟过审

### 6.3 真机实验的实操注意事项（借鉴 GUI-CEval + AndroidWorld 经验）

**环境控制**：
- 实验前 1 天确认目标商品在架、价格未变
- 每次实验记录环境快照（App 版本、系统时间、网络状态）
- 同期连续跑同批任务，减少时间漂移

**中断处理**：
- captcha/login 蜂鸣暂停 → 人工接力 → 人工段不计边
- 网络超时/设备异常 → 标记、重试（最多 3 次）、记录丢弃原因

**数据记录**：
- 每步 JSONL（已有 telemetry）
- 每任务 TSV（F3 产出）
- 失败任务轨迹照样落盘（不入图，留待故障分析）
- 实验 config.yaml（含所有开关状态、环境变量、App 版本）

---

## 7 论文呈现方案

### 7.1 表格规划

| 表格 | 内容 | 数据来源 |
|------|------|---------|
| 表 4-1 | 头条结果：4 系统 × 2 App × 5 任务级指标 | P5 头条真机 |
| 表 4-2 | RQ1-RQ4 核心结果 | 各 RQ 实验 |
| 表 4-3 | 消融矩阵结果（9 消融 × 3 指标） | P5 消融实验 |
| 表 4-4 | 建图成本漏斗 | P4 京东建图 |
| 表 4-5 | Sim2Real Gap 审计 | 方案 B |

### 7.2 图表规划

| 图 | 内容 |
|----|------|
| 图 4-1 | 四系统三维散点图（SR × 延迟 × 调用次数） |
| 图 4-2 | RQ1a 增长曲线：fast_path 命中率 vs 成功任务数 |
| 图 4-3 | dispatch 分档堆叠图（fast_path / co-pilot / full VLM 占比随任务演化） |
| 图 4-4 | RQ2 错误快路径率：sava vs legacy vs N∈{1,3,5} |
| 图 4-5 | RQ3 控制边界三维权衡（标注 S2/S3 两个极端的位置） |
| 图 4-6 | RQ4 建图漏斗（探索候选→审核→在线提升 各环节转化率） |

---

## 8 风险与诚实边界

### 8.1 已知局限（写入论文 §5）

1. **样本量有限**：真机实验 30-50 任务、thesis-scale，不做大规模统计推断
2. **App 同质性**：淘宝与京东同为头部电商、界面范式相近，跨 App 泛化需更多异质 App 验证
3. **模拟环境的 grounding gap**：模拟购物 App 无法测试 grounding 能力（无像素输入）
4. **干扰建模局限**：模拟器中的干扰概率从有限真机频次估计，CI 宽
5. **单执行器**：所有实验固定 autoglm-phone-9b，未验证执行器独立性

### 8.2 绝不能宣称的（承袭 `amsg-sim-env-analysis.md` §4）

- 离线 sim 端到端成功率 ≈ 真机成功率
- N∈{3,5} 定量敏感性（补采失败样本前）
- "图谱即概率模拟器"为已验证 novelty（多峰为空）
- 显式建模了电商干扰频率
- 解决了 captcha
- 任何 sim 延迟数字

### 8.3 审稿人预判与防御

| 攻击点 | 防御 |
|--------|------|
| "用图测图" | 方案 A 外生真值（held-out 真机 page_type）+ 方案 D 真机频次校准 |
| "sim 凭什么迁移" | 方案 B gap 度量 + "可 claim 矩阵"前置方法节 |
| "6 条全成功凭什么证 RQ2" | P2.5 补采失败轨迹；补采前只报方向不报量级 |
| "为什么不用 AndroidWorld" | 不含中文电商；我们需要的是图谱在真实干扰下的表现 |
| "为什么不和其他 agent 比" | 本文贡献是图谱机制而非 agent；基线是同一框架的控制协议对比 |

---

## 9 参考文献（本方案引用的方法论来源）

1. Liu et al., "MemGUI-Bench: Benchmarking Memory of Mobile GUI Agents in Dynamic Environments," arXiv:2602.06075, 2026.
2. Rawles et al., "AndroidWorld: A Dynamic Benchmarking Environment for Autonomous Agents," ICLR 2025.
3. "MobileGym: A Simulation Platform for Mobile GUI Agents," arXiv:2605.26114, 2025.
4. "SimuWoB: Simulating Real-World Mobile Apps for Fast and Faithful GUI Agent Benchmarking," arXiv:2605.25160, 2025.
5. "PSPA-Bench: A Personalized Benchmark for Smartphone GUI Agent," arXiv:2603.29318, 2025.
6. "GUI-CEval: A Hierarchical and Comprehensive Chinese Benchmark for Mobile GUI Agents," arXiv:2603.15039, 2025.
7. "EComAgentBench: Benchmarking Shopping Agents on Long-Horizon Tasks with Distributed Hidden Intent," arXiv:2606.17698, 2026.
8. "ShoppingBench: A Real-World Intent-Grounded Shopping Benchmark for LLM-based Agents," arXiv:2508.04266, 2025.
9. "Mobile-Bench-v2: A More Realistic and Comprehensive Benchmark for VLM-based Mobile Agents," arXiv:2505.11891, 2025.
