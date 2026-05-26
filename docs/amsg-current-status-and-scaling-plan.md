# AMSG 当前状态与大规模建图计划

## 结论

AMSG 已经从“人工轨迹回放型空间记忆”推进到“可主动验证、可约束动作语义、可写入 Neo4j 的空间图谱原型”。当前 `shopping-spatial-v3` 已能覆盖淘宝购物主链路的关键安全边，并且能够区分淘宝真实环境中的两个易混淆动作：

- 商品详情页底部加购/购买类 CTA：用于触发 `product_detail -> spec_selection`
- 商品详情页顶部右侧购物车入口：用于触发 `product_detail -> cart`

这解决了此前一直找不到 `product_detail -> spec_selection` 和 `spec_selection -> cart` 的根因：淘宝的规格选择不是商品页上的独立页面，而是在点击底部加购/购买 CTA 后弹出的半屏规格面板；二次确认加购后通常回到商品详情页，再通过顶部购物车入口进入购物车。

## 已完成内容

### 1. AMSG 空间图谱核心层

已新增 `phone_agent/spatial/`，包含：

- `core.py`：`PageNode`、`AffordanceEdge`、`BeliefState`、`SemanticActionIR`、`DeviceActionIR`
- `schema_registry.py`：通用 schema 与购物 schema 加载、合并、别名匹配
- `semantics.py`：探索页面到 schema-instance node 的语义抽取
- `hypothesis.py`：根据 schema 生成边假设
- `active_builder.py`：frontier scoring
- `planner.py`：belief-guided route planning
- `verifier.py`：postcondition verification 与 negative memory
- `model_bridge.py`：图谱语义动作到执行动作的转换
- `storage_adapter.py`：探索结果与图谱存储适配
- `compat.py`：旧 `SpatialGraphMemory` 兼容入口

### 2. 多模型协议归一化

已新增 `phone_agent/model/protocol_bridge.py`，将 AutoGLM、UI-TARS、Qwen-VL、MAI-UI、GUI-Owl 的动作输出归一化为 `DeviceActionIR`，避免空间图谱绑定某一种模型的输出格式或坐标系。

### 3. 主动探索与质量门禁

`phone_agent/memory/offline_explorer.py` 已支持 active exploration mode：

- 根据 schema 缺失页面/缺失边生成探索提示
- 对高风险页面和高风险动作进行拦截
- 对错误 postcondition 进行过滤
- 对规格弹窗、支付页、购物车入口等易混淆页面做弱定位修复
- 拒绝异常 transition，避免污染图谱

`phone_agent/memory/rebuild_spatial_graph.py` 已支持 v3 图谱重建与报告输出。质量门禁现在允许淘宝真实加购路径：

```text
product_detail -> spec_selection
spec_selection -> product_detail
product_detail -> cart
```

并兼容少数 App 中可能存在的直接：

```text
spec_selection -> cart
```

### 4. 真实设备探索进展

当前已在真机淘宝环境中完成多轮探索，并写入 `shopping-spatial-v3`。

Neo4j 查询显示当前核心动作链包括：

```text
home -> search_input
search_input -> search_result
search_result -> product_detail
product_detail -> spec_selection
spec_selection -> product_detail
product_detail -> cart
```

其中 `product_detail -> cart` 已在真机中验证为点击顶部右侧购物车图标，坐标证据约为 `[848, 71]`；底部加购/购买 CTA 已被规则约束为 `product_detail -> spec_selection`，不会误记为进入购物车。

### 5. 测试状态

已通过：

```text
python -m pytest tests -q
69 passed
```

`docs/amsg-v3-neo4j-import-report.md` 显示：

- Artifacts: 17
- Screenshots/pages: 54
- Promoted edges: 12 / transitions seen 12
- Valid edge ratio: 1.0
- Safe schema coverage: 0.9231
- Quality gate: passed

## 当前能达到的效果

### 已具备

- 能把截图分类为通用/购物 schema 页面类型
- 能主动提出待验证的空间边，而不是只依赖人工轨迹
- 能在探索中拒绝明显错误或高风险 transition
- 能把多模型动作格式归一化，给图谱层提供稳定动作协议
- 能在 Neo4j 中形成可查询的 `UIState -> Action -> UIState` 链路
- 能对淘宝正常商品链路形成初步空间指导：
  - 搜索入口
  - 搜索结果
  - 商品详情
  - 规格弹窗
  - 二次确认加购
  - 购物车入口

### 仍有限制

- 当前 v3 图谱仍是小规模探索，覆盖的是主链路，不是全量淘宝状态空间
- `checkout`、`payment`、`address` 等高风险页只应做识别和规避，不应自动执行
- `search_result -> product_detail` 仍受商品卡片布局、广告卡片、直播卡片影响
- `spec_selection` 内部的规格选择策略还需要更细的 slot schema
- 大规模探索需要任务队列、探索预算、失败边重试策略和设备状态恢复机制

## 下一步：大规模建图方案

### 阶段 1：定义建图任务队列

不要让 Agent “自由探索”。应维护一组可控 frontier task：

```text
home -> search_input
search_input -> search_result
search_result -> product_detail
product_detail -> spec_selection
spec_selection -> product_detail
product_detail -> cart
cart -> checkout_boundary
dialog -> underlying_page
filter_panel -> search_result
```

每个任务都包含：

- 起始页面条件
- 目标 postcondition
- 禁止动作
- 允许的 locator region
- 最大步数
- 成功判定
- 失败恢复动作

### 阶段 2：按页面类型分桶探索

优先覆盖高价值页面类型，而不是按 App 随机游走：

1. `home/search_input/search_result`
2. `product_detail/spec_selection`
3. `cart/checkout_boundary`
4. `dialog/filter_panel`
5. 非购物 App 的 `settings/list/detail/form/dialog`

每个桶内部做多样化采样：

- 不同关键词
- 不同商品类型
- 不同价格区间
- 不同弹窗干扰
- 不同登录状态
- 不同活动态按钮文案

### 阶段 3：引入边质量评分

每条边记录：

- 成功次数
- 失败次数
- postcondition mismatch 次数
- locator region
- 触发文本/图标证据
- 是否跨页面类型稳定
- 是否依赖具体商品或活动文案

低质量边不进入主图，只进入 negative memory 或 staging graph。

### 阶段 4：形成 v4 建图流水线

建议新增：

```text
phone_agent/spatial/build_jobs.py
phone_agent/spatial/exploration_queue.py
phone_agent/spatial/edge_quality.py
phone_agent/spatial/coverage_report.py
```

目标是把当前单次 `offline_explorer` 命令升级为批量任务：

```text
seed state
-> frontier job
-> active exploration
-> verifier
-> staging graph
-> quality gate
-> promoted graph
-> report
```

### 阶段 5：实验设计

购物 App 主实验：

- 淘宝为主
- 京东/拼多多做迁移验证
- 任务覆盖搜索、筛选、详情、规格、购物车、结算前停止、弹窗恢复

通用手机环境泛化实验：

- 设置 App：查找设置项
- 工具类 App：列表/详情/表单
- 通信类 App：只做低风险查看，不发送真实消息

对比：

- VLM-only
- manual trajectory graph
- free exploration graph
- shopping-spatial-v2
- AMSG v3
- AMSG v4 batch builder

核心指标：

- Task Success Rate
- Average Steps
- Loop Rate
- Wrong Page Entry Rate
- High-Risk Mis-trigger Rate
- Postcondition Mismatch Recovery Rate
- Graph Construction Cost
- Valid Edge Ratio
- Schema Coverage
- VLM Call Count

## 工作区审核结论

应提交：

- AMSG 当前状态与大规模建图计划
- `SpatialGraphMemory` 中 `filter_panel` 的页面关键词、地标和动作补充
- 项目根目录 `AGENTS.md`
- 参考论文归档到 `references/GraphReference/`

应清理：

- `tmp/`
- `__pycache__/` 和被测试改写的 `.pyc`
- 本地 `.claude/settings.local.json` 改动
- `memory_db/default/*` 运行时记忆改动
- 旧 v1 诊断草稿和过时报告

暂不提交：

- 仍硬编码 `shopping-spatial-v1` 的旧诊断脚本
- 声称“完全成功”的旧报告，因为它与后续 honest report 和 v3 结果冲突
