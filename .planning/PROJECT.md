# ShoppingGraph: 移动端购物场景图谱导航系统

## 愿景

将 WebNavigator 的图谱导航思想迁移至移动端购物场景，构建一个 **VLM 自主探索 + 结构化图谱 + 轨迹加速** 的智能 Agent 导航系统。

## 核心问题

移动端 App 不存在 URL/索引结构，页面跳转依赖 GUI 交互（点击、滑动、返回）。传统 VLM Agent 每一步都需要完整推理，效率低且不稳定。

## 解决方案

1. **离线探索**：VLM 自主遍历购物 App，发现页面类型和跳转关系
2. **图谱构建**：将探索数据构建为结构化 ShoppingGraph（UIState 节点 + Action 边 + TaskTarget 节点）
3. **轨迹加速**：在线执行时匹配图谱路径，跳过重复推理，实现 60-85% 的 replay 加速

## 技术参考

| 论文 | 核心思想 | 迁移点 |
|------|---------|--------|
| WebNavigator | Web 端图谱索引导航 | 页面类型分类、语义摘要 |
| PG-Agent | 轨迹驱动的 Page Graph 构建 | 从离散 episode → 图谱更新 |
| WebClipper | MNDAG 最小必要 DAG | 轨迹修剪：Dijkstra + 后向闭包 |
| MobiAgent | ActTree 前缀缓存 | 高频路径 60-85% replay 率 |
