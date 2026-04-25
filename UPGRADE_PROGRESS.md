# ClawGUI Agent - 双核记忆架构 (GraphRAG) 升级进度报告

## 1. 架构变更与实现概述

根据升级计划 (`CLAWGUI_UPGRADE_PLAN.md` 和 `gleaming-marinating-hippo.md`)，系统已成功从基于 MD5 精确匹配的脆弱记忆系统，重构为基于 **GraphRAG（向量检索 + 图谱遍历）** 的语义记忆系统。

核心设计：
- **Layer 1**: 彻底废弃原本基于截图 MD5 的精确匹配，因为在不同设备或微小 UI 变化下，MD5 极易失效。
- **Layer 2 (GraphRAG 检索入口)**: 引入智谱大模型 **embedding-3** API。将用户的任务描述（TaskTarget）映射到高维语义空间，并使用 **FAISS** (`TaskIndex`) 进行快速的向量余弦相似度检索，精准定位 Neo4j 中的历史任务节点。
- **Layer 3 (图谱子图遍历与凝练)**:
  - 定位到高相似度任务后，在 Neo4j 中沿着 `NEXT_ACTION` 边遍历，提取完整的执行轨迹。
  - 新增 **上下文凝练模块 (`_condense_trajectory_context`)**，将数十步复杂的历史图谱路径清洗、压缩为简短的行动指南（如 `app→click→swipe→type`），长度控制在 200 字内，大幅降低 VLM 的视觉注意力干扰。

## 2. 核心模块重构清单

### 2.1 新增：向量嵌入层 (`embedding_client.py` & `task_index.py`)
- `phone_agent/memory/embedding_client.py`: 封装了 BigModel `embedding-3` 的 HTTP 调用。目前测试连通性正常，由于使用了最新版的 `embedding-3`，实际返回的向量维度为 **2048维**（注意：之前文档记录为 1536 维，代码已通过动态 `len()` 自适应）。
- `phone_agent/memory/task_index.py`: 封装 FAISS 索引层 (`IndexFlatIP`)，负责增量添加向量、序列化保存至本地 `npy`，并提供了从 Neo4j 数据库全量重建索引的 `rebuild_from_neo4j()` 接口。

### 2.2 改造：空间图谱引擎 (`graph_store.py`)
- 初始化时自动挂载 `TaskIndex`，尝试加载本地 FAISS 缓存。若缓存为空，则触发自动从 Neo4j 同步全量 `TaskTarget` 节点重建索引。
- 重写 `find_similar_tasks()` 方法：优先调用 `TaskIndex.search()` 进行向量相似度匹配，并补充返回相似度得分 (`similarity`)。保留了 N-gram 字符片段匹配作为备用 fallback。
- 修改 `commit_task_trajectory()`：在存储 Neo4j 的同时，同步调用 `TaskIndex.add_task()` 更新 FAISS 向量库，保持端到端一致性。

### 2.3 改造：记忆分发中心 (`memory_manager.py`)
- 移除了 MD5 的硬编码匹配逻辑。
- 升级 `locate_and_get_context()` 方法，引入双层判断：
  - **Navigate 模式** (相似度 $\ge 0.85$)：说明用户意图与历史轨迹高度吻合，直接提取图谱中的第一个动作节点执行，实现免 VLM 推理的零延迟操作。
  - **Explore 模式** (相似度 $0.60 \sim 0.85$)：调用新增的 `_condense_trajectory_context()` 和 `_condense_steps()` 方法，清洗并注入最高效的历史图谱参考路径给 VLM。

### 2.4 简化：主执行循环 (`agent.py`)
- 在 `_execute_step()` 阶段，移除了围绕 `ui_hash` 构建的复杂判断树。
- 完全依托 `memory_manager.locate_and_get_context()` 返回的 `mode` 与 `next_actions` 进行分发调度。

## 3. 当前运行状态与环境测试

- **.env 配置**：已成功注入并启用了真实的 `EMBEDDING_API_KEY`。
- **连通性测试**：成功调用 `embedding-3` 对“在淘宝买衣服”进行向量化，返回了 2048 维度的浮点数组（前几位：`0.00746, -0.02285...`），证明 API 鉴权与网络层工作正常。
- **依赖库**：`faiss-cpu` 等依赖库状态良好。

## 4. 下一步建议 (Next Steps)

1. **图谱冷启动验证**：建议启动项目执行一条历史测试用例，观察 `GraphStore` 是否能正确触发“从 Neo4j 重建 TaskIndex”，并顺利在 FAISS 中生成对应缓存文件 (`task_ids.json` / `task_index.npy`)。
2. **端到端流程验收**：使用不同的语义描述（如原任务为“点一份瑞幸咖啡”，现测试“买杯瑞幸”），观察日志中相似度打分，验证 Explore/Navigate 模式是否按阈值如期触发。
3. **维数硬编码调整**：若遇到 FAISS 索引维度报错，需确保 `EmbeddingClient` 初始化时的 `self.dimension` 从 1536 动态同步至 2048（或取单次调用的长度）。目前代码中 `self.dimension = self._client.dimension` 默认初始值为 1536，可能需要发起一次 dummy 调用来校准真实维度。