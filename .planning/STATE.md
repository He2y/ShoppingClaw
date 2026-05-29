# Project State

## 当前状态

- **当前阶段**: Phase 2 完成，Phase 3 待开始
- **上次更新**: 2026-05-29

## Phase 2 成果 (GraphStore 增强)

- `get_task_trajectory()` 重构为迭代式图遍历，支持任意步数（原15步硬编码已移除）
- `ExplorationSession` 节点类型：区分离线探索 vs 在线执行轨迹
- `find_similar_pages()` 按 page_type + summary 关键词查询
- `create_exploration_session()` / `find_exploration_sessions()` API
- 离线探索导入时自动创建 ExplorationSession 节点
- 14 个新测试覆盖动态轨迹查询、ExplorationSession、find_similar_pages

## Phase 1 成果

- `phone_agent/memory/offline_explorer.py` — VLM 自主探索器
- `phone_agent/memory/run_explorer.py` — CLI 入口
- 探索产出: `memory_db/exploration/{app}_pages_{ts}.json` + `{app}_trajectory_{ts}.json`

## 关键决策

1. 移动端不适合 WebNavigator 式的索引方案 → 采用 PG-Agent 式的轨迹驱动构建
2. 不重写 GraphStore → 增量增强现有 `phone_agent/memory/graph_store.py`
3. 复用现有基础设施: ModelClient, ActionHandler, DeviceFactory, parse_action()
4. 探索 Prompt 与任务 Prompt 分离：导航决策使用探索系统提示词，页面分类使用独立轻量调用
5. 轨迹查询改用迭代式 walk（非 APOC 依赖），兼容所有 Neo4j 版本

## 待解决问题

- MemoryManager._get_first_action() 只取首动作，无多步路径缓存
- 缺少 ActTree/PathCache 机制（Phase 3 目标）
