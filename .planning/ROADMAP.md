# Roadmap

## Milestone: v1.0 — ShoppingGraph 核心闭环

| Phase | 名称 | 状态 | 目标 |
|-------|------|------|------|
| 1 | OfflineExplorer VLM 自主探索 | ✅ 完成 | VLM 自主遍历购物 App，产出页面分类 + 跳转轨迹 |
| 2 | GraphStore 增强 | 📋 已规划 | 将探索数据灌入 Neo4j，构建 UIState 节点 + Action 边 + TaskTarget 节点 |
| 3 | ActTree / PathCache 加速 | 📋 待开始 | 高频路径前缀缓存，实现轨迹 replay 加速 |
| 4 | 端到端验证 | 📋 待开始 | 真实购物任务完整链路测试 |

## 需求覆盖

| REQ-ID | 需求 | Phase |
|--------|------|-------|
| REQ-01 | VLM 能自主探索购物 App 并发现所有页面类型 | 1 |
| REQ-02 | 探索数据持久化为结构化 JSON | 1 |
| REQ-03 | 将探索数据导入 Neo4j GraphStore | 2 |
| REQ-04 | 支持页面类型分类和语义去重 | 2 |
| REQ-05 | 支持基于 TaskIndex 的语义导航匹配 | 2 |
| REQ-06 | 高频路径前缀缓存 | 3 |
| REQ-07 | Navigate 模式下跳过 VLM 推理 | 3 |
| REQ-08 | 完整购物任务端到端测试 | 4 |
