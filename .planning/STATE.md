# Project State

## 当前状态

- **当前阶段**: Phase 1 完成，Phase 2 已规划
- **上次更新**: 2026-05-03

## Phase 1 成果

- `phone_agent/memory/offline_explorer.py` — VLM 自主探索器（~350 行）
- `phone_agent/memory/run_explorer.py` — CLI 入口
- 探索产出: `memory_db/exploration/{app}_pages_{ts}.json` + `{app}_trajectory_{ts}.json`
- 支持的页面类型: home, search_input, search_result, product_detail, spec_selection, cart, checkout, category, my_account, store, login, unknown

## 关键决策

1. 移动端不适合 WebNavigator 式的索引方案 → 采用 PG-Agent 式的轨迹驱动构建
2. 不重写 GraphStore → 增量增强现有 `phone_agent/memory/graph_store.py`（433 行 Neo4j 实现）
3. 复用现有基础设施: ModelClient, ActionHandler, DeviceFactory, parse_action()
4. 探索 Prompt 与任务 Prompt 分离：导航决策使用探索系统提示词，页面分类使用独立轻量调用

## 待解决问题

- GraphStore 当前只存线性链，不支持页面类型分类和语义合并
- MemoryManager._get_first_action() 只取首动作，无多步路径缓存
- 缺少 ActTree/PathCache 机制
