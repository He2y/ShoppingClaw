# ClawGUI-Agent 购物助手升级方案：基于“双核记忆引擎”的在线自进化架构

## Context

购物场景路径深、状态复杂、动态弹窗干扰多，仅依靠 VLM 实时推理的“无状态” Agent 容易发生迷航和死循环。
本方案从第一性原理出发，将原始设计的“地图导航图谱”与系统现有的**个性化 Memory 系统** (`phone_agent/memory`) 进行深度架构融合，构建为**“双核记忆引擎” (Dual-Core Memory Engine)**：

1. **语义记忆核 (Semantic Memory - Vector DB/FAISS)**：负责“了解用户”。不仅记忆历史的联系人和APP绑定，更增强了针对购物场景的偏好提取（如商品偏好、品牌喜好、历史习惯、短期购物意图）。
2. **空间记忆核 (Spatial Memory - Graph DB/Neo4j)**：负责“了解世界”。利用离线轨迹构建初始地图，在线通过状态哈希、图谱查询进行 GPS 定位。在图内快速导航；在图外依靠 VLM 探索。
3. **在线自进化**：探索成功后，在线将新的动作序列固化回空间记忆图谱中，并将分析出的新购物习惯沉淀到语义记忆中。

---

## 一、双核记忆架构设计 (Unified Memory Architecture)

我们将重构 `phone_agent/memory` 模块，使其成为唯一的数据和上下文出口。

### 1.1 语义记忆 (Semantic Memory) 增强
针对购物场景，在现有的 `MemoryStore` 基础上新增以下 Memory Types：
- `PRODUCT_PREFERENCE` (商品偏好)：记录用户对特定品类的倾向（如“买数码产品只看京东自营”）。
- `PRICE_SENSITIVITY` (价格敏感度)：记录用户的比价习惯或常使用的优惠方式（如“买衣服一般在200-500元之间”）。
- `BRAND_AFFINITY` (品牌忠诚度)：记录品牌黑白名单。
- `SHOPPING_CONTEXT` (当前购物上下文)：这是一个短期记忆。跨prompt维持意图（如“正在看Nike的鞋”，后续指令“加入购物车”即可关联）。

**Metadata 结构扩展**：
现有的 `metadata` 字典扩展为支持更复杂的 JSON 结构，如：
```json
{
  "category": "electronics",
  "keywords": ["Apple", "Type-C"],
  "price_range": {"min": 5000, "max": 8000},
  "intent": "wishlist"
}
```

### 1.2 空间记忆 (Spatial Memory / Neo4j) 详细设计
由新增的 `graph_store.py` 负责封装。

**节点设计 (Nodes)**

| 节点 Label | 属性 | 说明 |
|-----------|------|------|
| `:UIState` | `state_id`<br>`app`<br>`semantic_layout` (VLM对页面布局的抽象概括)<br>`view_hierarchy_hash`<br>`is_popup` | **界面抽象节点**。<br>代表一类逻辑页面（如：淘宝首页）。 |
| `:Action` | `action_id`<br>`type`<br>`target_desc`<br>`reasoning`<br>`params` | **动作节点**。<br>从“边”独立出来，记录执行意图。 |
| `:TaskTarget` | `target_id`<br>`app`<br>`task_type`<br>`description` | **任务目标节点**。 |

**边设计 (Edges)**

| 边关系 | 源节点 -> 目标节点 | 属性 | 说明 |
|--------|------------------|------|------|
| `[:NEXT_ACTION]` | `:UIState` -> `:Action` | `confidence`<br>`frequency` | 在某状态下，执行该动作的频率和置信度。 |
| `[:PRODUCES]` | `:Action` -> `:UIState` | `success_rate`<br>`avg_time` | 动作执行后导向的新状态。 |
| `[:STARTS_AT]` | `:TaskTarget` -> `:UIState` | - | 任务开始时的初始状态。 |
| `[:ENDS_AT]` | `:TaskTarget` -> `:UIState` | `success` | 任务终止状态。 |
| `[:SOLVES_POPUP]` | `:UIState` (is_popup) -> `:Action` | - | **抗干扰设计**：遇到该弹窗时的专用绕过动作。 |

---

## 二、离线图谱构建与数据适配

利用 `MobiAgent/collect/manual/data/` 的离线轨迹数据生成初始图谱。

### 2.1 格式映射
手动收集的 `actions.json` 和 `react.json` 将被映射为 Neo4j 中的节点和边。
- **`:UIState` 的生成**：脚本计算界面的 `view_hierarchy_hash`。调用 VLM 对 `index.jpg` 生成 `semantic_layout`。
- **`:Action` 的生成**：整合 `actions.json` 和 `react.json`。
- **建图**：脚本建立 `(UIState_n) -[:NEXT_ACTION]-> (Action_n) -[:PRODUCES]-> (UIState_{n+1})` 的长链。

### 2.2 初始状态对齐库
为加速在线定位，生成一个 **状态特征向量库 (FAISS)**，包含所有初始 `:UIState` 的 `semantic_layout` 向量。

---

## 三、Agent 在线执行流程详细设计

修改 `phone_agent/agent.py` 中的主循环 `_execute_step()`。所有上下文获取统一由 `memory_manager.py` 调度。

### 3.1 状态定位与意图获取 (Locate & Context Fetch)
1. 获取截图和当前屏幕的 UI 树，计算 Hash。
2. Agent 调用 `memory_manager.locate_and_get_context(ui_hash, semantic_layout, task)`。
3. `MemoryManager` 内部调度：
   - **空间定位**：通过 FAISS 泛化匹配或 Hash 精确匹配，去 Graph 查询当前的 `current_state_id`。
   - **偏好获取**：从 Semantic Memory (FAISS) 检索用户的购物偏好、品牌喜好等。

### 3.2 双核决策模式 (Decision & Act)
从 `MemoryManager` 返回上下文后：
- **A. 导航模式 (Navigate / 空间引导)**：
  如果 Graph 查询到了高置信度的 `[:NEXT_ACTION]` 捷径，或者遇到了匹配了 `[:SOLVES_POPUP]` 的弹窗状态。
  - *行为*：跳过高延迟的 VLM 全局推理，直接调用底层工具执行（如“关闭弹窗”或“点击搜索”）。
- **B. 探索模式 (Explore / 语义推理)**：
  处于未知状态，或找不到通向目标的路径。
  - *行为*：将获取到的“用户购物偏好”注入 Prompt，调用 VLM 走完整的推理想象。

### 3.3 在线执行上下文记录
内存中维护：
```python
trajectory_cache = [
    {"state": ui_state_1, "action": act_1},
    {"state": ui_state_2, "action": act_2},
    ...
]
```

---

## 四、在线动态建图与自进化机制

在任务终止时 (`end_task`) 触发“双核更新”：

### 4.1 空间记忆（图谱）更新
1. **去重与合并**：对 `trajectory_cache` 中的未知状态，生成新 `:UIState`。
2. **写入边与节点**：提取 VLM `thinking` 作为 `:Action` 的 `reasoning`，构建新的状态动作链。
3. **强化学习**：
   - 任务成功，提升走过路径的 `success_rate`。
   - 任务失败（死循环、弹窗拦截），惩罚对应边，或将状态标记为 `is_popup=True`。

### 4.2 语义记忆（用户偏好）沉淀
在 `end_task` 时分析全程对话和 VLM 的 `thinking`：
- 如果发现了明显的价格抱怨（“太贵了”）、品牌选择等，抽取为 `PRICE_SENSITIVITY` 或 `PRODUCT_PREFERENCE` 写入 Semantic Memory (FAISS)。
- 定期运行图算法，在后台发掘捷径（Heuristic Shortcut Discovery），提升捷径 Action 的频率权重。

---

## 五、模块化实施步骤

**Phase 1：数据适配与离线基建**
- 编写 `scripts/import_manual_data.py`，将离线数据转换为 Neo4j 节点和边。
- 修改 `memory_store.py` 扩展 Metadata 和 Shopping Memory Types。

**Phase 2：统一记忆系统枢纽**
- 实现 `phone_agent/memory/graph_store.py` 封装 Neo4j Cypher。
- 改造 `memory_manager.py`，整合 FAISS 与 Graph，提供统一接口。

**Phase 3：Agent 引擎双轨制改造**
- 修改 `agent.py::_execute_step()`。
- 接入统一记忆枢纽，实现“弹窗速通导航”、“偏好注入”与“VLM探索模式”的自动分发。

**Phase 4：动态自进化闭环**
- 在 `end_task()` 加入空间记忆（Graph路线）和语义记忆（购物偏好）的双重更新逻辑。