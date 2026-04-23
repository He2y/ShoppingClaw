# ClawGUI-Agent 项目进展文档

> 更新日期：2026-04-23
> 状态：**开发中**

---

## 一、临时文件清理

已清理以下测试/调试产生的临时文件：

| 文件 | 类型 | 说明 |
|------|------|------|
| `debug.log` | 调试日志 | 运行日志 |
| `debug_jd.log` | 调试日志 | 京东测试日志 |
| `debug_jd_loop.log` | 调试日志 | 循环测试日志 |
| `test_output*.log` | 测试输出 | 3个测试日志文件 |
| `test.txt` | 临时文件 | 测试文本 |
| `test_neo4j.py` | 测试脚本 | Neo4j测试代码 |
| `fix_launch.py` | 临时脚本 | 修复脚本 |
| `ios.py` | 临时文件 | iOS测试文件 |
| `memory_db_offline_import/` | 测试数据 | 离线导入测试数据 |

---

## 二、项目整体结构

```
ClawGUI-Agent/
├── main.py                    # CLI 入口 (942行)
├── webui.py                   # Gradio Web界面 (2751行)
├── phone_agent/
│   ├── agent.py               # Android/HarmonyOS Agent (776行)
│   ├── agent_ios.py           # iOS专用Agent
│   ├── device_factory.py      # 设备抽象工厂
│   ├── tracer.py              # 任务执行追踪器
│   ├── model/
│   │   ├── adapters.py        # 5个VLM模型适配器
│   │   └── client.py          # OpenAI兼容HTTP客户端
│   ├── actions/               # 各模型专用Action Handler
│   ├── config/                # 提示词、App映射、时序配置
│   ├── adb/                   # Android ADB后端
│   ├── hdc/                   # HarmonyOS HDC后端
│   ├── xctest/                # iOS WebDriverAgent后端
│   └── memory/                # 记忆系统
│       ├── memory_manager.py   # 记忆管理层 (1043行)
│       ├── memory_store.py     # FAISS向量存储 (549行)
│       ├── graph_store.py      # Neo4j图存储 (365行)
│       └── __init__.py
├── nanobot/                   # 聊天平台网关
├── memory_db/                 # 持久化记忆数据 (32KB)
├── CLAUDE.md                  # 项目规范
├── CLAWGUI_UPGRADE_PLAN.md    # 升级计划
└── ARCHITECTURE.md            # 架构文档
```

---

## 三、主要模块变更分析

### 3.1 agent.py 变更

**文件行数变化**：原架构文档未记录精确行数，当前 776 行。

#### 核心变更内容

**1. 消息构建器 MessageBuilder**

原架构中未提及此组件。当前新增了 `MessageBuilder` 类用于统一构建消息格式：

```python
class MessageBuilder:
    @staticmethod
    def create_user_message(content: str) -> dict
    @staticmethod
    def create_assistant_message(content: str) -> dict
    @staticmethod
    def build_context_message(...)
    @staticmethod
    def build_memory_message(...)  # 注入个性化记忆上下文
```

**关键设计**：内存上下文通过 `build_memory_message()` 在推理前注入，而非拼接字符串。

**2. 个性化记忆上下文注入**

原架构描述的 `get_relevant_context()` 保持不变，但新增了更完整的内存管理方法：

```python
# 新增公开API
agent.add_user_preference(preference, category, importance)
agent.add_user_correction(original_action, correction)
agent.get_user_summary()
agent.get_memory_stats()
agent.clear_memories()
agent.export_memories()
agent.import_memories(memories)
```

**3. 状态追踪增强**

```python
# 新增状态追踪逻辑
self._prev_state_id          # 上一步的UI状态哈希
# 每次step后记录状态转换到GraphStore
if hasattr(self, '_prev_state_id') and current_state_id:
    self.memory_manager.graph_store.add_state_transition(
        self._prev_state_id,
        current_state_id,
        action,
        self._current_task
    )
self._prev_state_id = current_state_id
```

**4. Action序列化格式**

新增 `do()` / `finish()` 格式化逻辑，用于保存到历史记录：

```python
if action.get("_metadata") == "finish":
    action_str_to_save = f'finish(message={repr(action.get("message", "") or "")})'
elif action.get("_metadata") == "do":
    params_str = ", ".join(f"{k}={repr(v)}" for k, v in action.items() if k not in ("_metadata", "action"))
    action_str_to_save = f'do(action={repr(action.get("action", ""))}' + (f', {params_str}' if params_str else "") + ')'
```

**与原架构差异**：
- 原架构的 Action 解析主要由 Handler 层负责
- 当前 agent.py 承担了更多序列化/格式化职责
- 消息构建器从 Agent 主类中独立出来

---

### 3.2 main.py 变更

**文件行数**：942 行（新增大量功能）

#### 核心新增功能

**1. 完整的系统检查 check_system_requirements()**

原架构仅在 `AgentConfig` 中简单配置。当前 main.py 实现了完整的系统检查：

- ADB/HDC/iOS 工具安装验证
- 设备连接状态检测
- ADB Keyboard 检查（Android）
- WebDriverAgent 就绪检查（iOS）
- 支持多设备自动选择

```python
def check_system_requirements(
    device_type: DeviceType = DeviceType.ADB,
    wda_url: str = "http://localhost:8100",
    device_id: str = None
) -> bool
```

**2. 模型API检查 check_model_api()**

```python
def check_model_api(base_url: str, model_name: str, api_key: str) -> bool
```
- 测试 API 连通性
- 验证模型可用性
- 提供友好的错误提示和解决方案

**3. Neo4j轨迹管理命令**

```bash
# 列出已提交的任务轨迹
python main.py --list-trajectories

# 提交成功轨迹到Neo4j
python main.py --commit-trajectory '在京东点KFC外卖'
```

`--list-trajectories` 直接查询 Neo4j 中的 TaskTarget 节点，展示任务描述、App、提交时间等信息。

**4. iOS设备管理增强**

- `--pair`: 配对 iOS 设备
- `--wda-status`: 查看 WebDriverAgent 状态
- `--list-devices` 支持 iOS 设备列表

```python
def handle_ios_device_commands(args) -> bool
```

**5. HarmonyOS 支持完善**

- `set_hdc_verbose(True)` 启用 HDC 详细日志
- HDC 设备连接/断开/TCPIP管理

**6. 设备命令统一处理**

```python
def handle_device_commands(args) -> bool
```
统一处理 ADB/HDC/iOS 的 `--connect`, `--disconnect`, `--enable-tcpip`, `--list-devices` 等命令。

**与原架构差异**：
- 原架构的 main.py 仅作为简单入口
- 当前 main.py 演变为完整的 CLI 工具，具备设备管理、系统检查、轨迹查询等能力
- 命令行参数从 ~10 个扩展到 20+ 个

---

### 3.3 memory/ 目录变更

**文件总行数**：1043 + 549 + 365 = 1957 行（相比原架构大幅扩展）

#### memory_store.py — 向量记忆存储 (549行)

**新增组件**：

```python
# 新增元数据类型
class ShoppingMetadata(TypedDict):
    product_category: str
    brand: str
    price_range: tuple[float, float]
    platform: str
    user_sentiment: str

class GraphMetadata(TypedDict):
    app_name: str
    activity_name: str
    state_id: str
    ...
```

**新增记忆类型**：

```python
class MemoryType(Enum):
    # 原架构已支持的基础类型...
    PRODUCT_PREFERENCE = "product_preference"      # 新增
    PRICE_SENSITIVITY = "price_sensitivity"       # 新增
    BRAND_AFFINITY = "brand_affinity"             # 新增
    SCENE_RECOMMENDATION = "scene_recommendation" # 新增
    UI_STATE = "ui_state"                         # 新增
    UI_TRANSITION = "ui_transition"               # 新增
```

**SimpleEmbedder**：原架构使用简单的字符串哈希。当前实现了基于字符级特征的嵌入：

```python
class SimpleEmbedder:
    # 字符频率特征 + N-gram特征
    # 支持归一化余弦相似度计算
    def encode(self, texts: list[str]) -> list[list[float]]
```

**持久化格式变更**：
- `memories_meta.json`: 元数据
- `embeddings.npy`: NumPy格式的向量（新增，支持高效加载）

**与原架构差异**：
- 原架构使用 JSON 文件简单存储
- 当前增加了 FAISS 向量索引（可选，支持）和 NumPy 向量持久化
- 新增购物场景专用的元数据结构

---

#### memory_manager.py — 记忆管理层 (1043行)

**新增核心方法**：

```python
class MemoryManager:
    def start_task(self, task: str, start_state_id: str | None = None)
    def end_task(self, success: bool, result: str = "", end_state_id: str | None = None)
    def commit_pending(self, index: int = 0) -> bool  # 提交待审核轨迹
```

**Phase 4: 在线动态图构建**

```python
# 在 agent.py 中每次step后调用
self.memory_manager.graph_store.add_state_transition(
    self._prev_state_id, current_state_id, action, self._current_task
)
```

**新增联系人-应用绑定频率学习**：

```python
def _learn_contact_app_binding(self, apps_used: list[str])
def _update_contact_app_binding(self, contact: str, app: str)
```

**三层记忆匹配策略**（`locate_and_get_context`）：

```
1. Graph MD5精确匹配    → 状态完全一致，触发 Navigation Shortcut
2. Graph 任务语义匹配   → 找相似历史轨迹，返回动作序列
3. FAISS 向量 fallback  → 仅补充探索上下文
```

**待审核轨迹持久化**：

```python
def _save_pending_trajectory(self, task, success, result, steps, apps, start_state, end_state)
# 保存到 memory_db/default/pending_trajectories.json
```

**与原架构差异**：
- 原架构的 `get_relevant_context()` 返回简单的上下文字符串
- 当前实现了完整的 **三层匹配策略**，融合 FAISS 和 Neo4j 图查询
- 新增"待审核轨迹"机制——成功任务先暂存，需人工审核后提交 Neo4j
- 新增联系人-应用绑定频率统计（基于 `use_count`）

---

#### graph_store.py — Neo4j图存储 (365行)

**原架构未提及此文件**——这是全新的组件。

**核心功能**：

```python
class GraphStore:
    def get_current_state(self, state_hash: str) -> Optional[Dict]  # MD5精确查找
    def get_state_by_semantic(self, semantic_layout: str, limit: int)  # 语义fallback
    def get_next_actions(self, state_hash: str, min_confidence: float) -> List[Dict]  # 快捷动作
    def find_similar_tasks(self, task_description: str, app: str, top_k: int) -> List[Dict]  # 任务相似度
    def get_task_trajectory(self, task_id: str) -> Dict  # 获取完整动作序列
    def commit_task_trajectory(...) -> bool  # 提交轨迹到Neo4j
    def add_state_transition(...)  # 记录状态转换（在线探索时）
```

**中文N-gram分词**：

```python
def _tokenize_chinese(self, text: str) -> set[str]:
    # 单字符 + 2-gram + 3-gram
    # 用于与Neo4j中空格分隔的中文字符串匹配
```

**图数据模型**：

```
(UIState) ←STARTS_AT/ENDS_AT→ (TaskTarget)
(UIState) -[NEXT_ACTION]-> (Action) -[PRODUCES]-> (UIState)
```

**与原架构关系**：原架构 Section 8 仅描述了 JSON 文件存储的简单记忆系统。当前引入了完整的图数据库，支持：
- 状态空间的精确记忆
- 任务轨迹的语义复用
- 在线探索时的动态图构建

---

## 四、与原架构(ARCHITECTURE.md)的对比

### 4.1 架构演进总结

| 维度 | 原架构设计 | 当前实现 | 变化 |
|------|-----------|---------|------|
| **记忆存储** | JSON文件 | FAISS向量 + JSON + NumPy | 增加了向量语义搜索能力 |
| **图记忆** | 无 | Neo4j图数据库 | 全新增量，支持状态图和轨迹复用 |
| **轨迹管理** | 仅记录到文件 | 待审核→提交Neo4j | 增加了人工审核环节 |
| **CLI工具** | 简单入口 | 完整CLI（设备管理/系统检查/轨迹查询） | 功能大幅扩展 |
| **个性化** | 基础上下文注入 | 三层匹配策略（精确/语义/向量） | 智能程度显著提升 |
| **多平台** | ADB/HDC/XCTEST | 保持不变，增加详细检查 | 无架构变化 |

### 4.2 新增设计模式

**1. 待审核轨迹机制**

```
任务完成 → 保存到 pending_trajectories.json → 人工审核 → commit_pending() → Neo4j
```

防止错误轨迹污染知识图谱。

**2. 三层记忆匹配**

```
用户任务 → 1.MD5精确匹配(Navigate模式) → 2.任务语义匹配(Explore+轨迹参考) → 3.FAISS fallback
```

优先精确记忆，次优语义复用，最后向量补充。

**3. 联系人-应用频率绑定**

```
CONTACT_APP_BINDNG: {
    "contact": "张三",
    "app": "微信",
    "binding_key": "张三_微信",
    "use_count": 5,
    "importance": 0.75
}
```

基于历史使用频率推荐应用，而非简单匹配。

### 4.3 关键设计决策

**保留的设计（原架构核心，保持不变）**：
- 适配器模式：每个模型专用适配器，不强制统一格式
- 坐标转换在 Handler 层而非 Adapter 层
- 消息格式通过适配器独立管理

**演进的设计**：
- 记忆系统从"JSON存储"演进为"向量+图数据库混合"
- CLI 从"简单入口"演变为"完整工具链"
- 轨迹从"记录"演进为"知识沉淀"（经人工审核）

**待完善**：
- Graph MD5 精确匹配需要 UI 状态哈希的生成逻辑（目前由外部传入）
- Neo4j 连接失败时的降级处理可以更优雅
- FAISS 向量索引在内存受限场景下的优化

---

## 五、当前项目状态

### 已完成

- [x] Android/HarmonyOS/iOS 三平台 Agent
- [x] 5个 VLM 模型适配器（AutoGLM/UI-TARS/QwenVL/MAI-UI/GUI-Owl）
- [x] CLI 工具（设备管理/系统检查/任务执行）
- [x] Gradio WebUI（流式输出/记忆管理/轨迹可视化）
- [x] 双核记忆架构（FAISS向量 + Neo4j图）
- [x] 个性化上下文注入（三层匹配）
- [x] 轨迹审核与提交机制

### 开发中

- [ ] UI 状态哈希生成逻辑完善
- [ ] Neo4j 图谱的 Explorer 界面
- [ ] 多用户隔离的记忆存储
- [ ] 记忆遗忘/重要性衰减机制

### 待启动

- [ ] E2E 测试覆盖
- [ ] nanobot 集成（聊天平台控制）
- [ ] ClawGUI-Eval 评测流水线

---

## 六、文件统计

| 文件 | 行数 | 说明 |
|------|------|------|
| `webui.py` | 2751 | Gradio Web界面（最大文件） |
| `main.py` | 942 | CLI入口 |
| `phone_agent/memory/memory_manager.py` | 1043 | 记忆管理层 |
| `phone_agent/agent.py` | 776 | Agent主类 |
| `phone_agent/memory/memory_store.py` | 549 | 向量存储 |
| `phone_agent/memory/graph_store.py` | 365 | 图存储 |
| **核心代码总计** | ~6452 | |

---

*本文档由 Claude Code 自动生成，对比基准：`phone_agent/ARCHITECTURE.md`（原始架构设计文档）*
