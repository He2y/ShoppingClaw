# Phase 2 架构重构��结

> 日期: 2026-05-03
> 状态: ✅ 核���完成，��测试

## 已完成��重构

### 1. ��� 统一状��管理

#### StateManager 组件
- **��件**: `phone_agent/memory/state_manager.py`
- **���能**:
  - 统一状态追踪 (`_current_state_id`, `_prev_state_id`)
  - 任务���命周期管�� (`start_task`, `end_task`)
  - 状态历史维��
  - 稳定��状态���识计算

#### MemoryManager 集成
- **文件**: `phone_agent/memory/memory_manager.py`
- **新增���法**:
  - `update_state_and_transition()` - 统一���状态��新 + 图转���记录接口
  - `get_current_state_id()` - 获���当前状态ID
- **移��**: 旧的 `_task_start_state_id`, `_current_state_id`, `_task_end_state_id` 字段
- **改进**: `start_task()` 和 `end_task()` ���在通�� StateManager 管理��态

#### agent.py 重构
- **文件**: `phone_agent/agent.py`
- **变更**:
  - 移除 `_prev_state_id` 字段
  - 移除直接访�� `memory_manager.graph_store.add_state_transition()`
  - 使��统一���口 `memory_manager.update_state_and_transition()`
  - 添加 `ui_hash` 和 `semantic_layout` ���量作用域

### 2. 🔄 ��构改���

#### 前后��比

**��前（���裂架��）**:
```python
# agent.py 自己管理��态
self._prev_state_id = current_state_id

# agent.py 直���访问 graph_store
self.memory_manager.graph_store.add_state_transition(
    self._prev_state_id, current_state_id, action, task
)

# MemoryManager 也有���己的状��
self._current_state_id = ...
self._task_start_state_id = ...
```

**现��（统一架��）**:
```python
# agent.py 只调���统一接口
self.memory_manager.update_state_and_transition(
    screenshot_hash=ui_hash,
    semantic_layout=semantic_layout,
    action=action,
    task=self._current_task
)

# MemoryManager 内部���调
def update_state_and_transition(self, ...):
    new_state_id = self.state_manager.compute_state_id(...)
    prev_state, current_state = self.state_manager.update_state(new_state_id)
    if prev_state:
        self.graph_store.add_state_transition(prev_state, current_state, ...)
```

## 解决的��心问���

### 🔴 P0-1: 状态��理分裂 ���
- **问���**: agent.py 和 MemoryManager 各自维��状态，互��同步
- **解���**: 创建 StateManager 统一管理��MemoryManager 封装��口
- **��响**: 状态一致��得到保证��图转��记录���确

### 🔴 P0-7: MD5 截���哈希不��定 ��
- **问题**: 同一界面每次��图哈���不同
- **当前方案**: 使�� `semantic_layout` (app名��) 作为��要标���
- **Phase 3 ���划**: 升级为 VLM 语义��局或 View Hierarchy Hash

### 🟠 P1-5: Navigate 模���无置信度��查 ✅
- **问题**: 任意��配都��发 Navigate 模式
- **解决**: 添��� 0.8 置信度��值，��于阈���降级到 explore 模式

## 待完��的 Phase 2 任��

### 1. ⏳ 统一嵌��空间���高优先级��

**当前��题**:
- MemoryStore: 128d SimpleEmbedder（字���频率哈希��
- TaskIndex: 2048d embedding-3 API
- 两���向量空间无法��同工作

**解决方��**:
```python
# memory_store.py 改��
class MemoryStore:
    def __init__(self, ...):
        # 使��� embedding-3 client
        from .embedding_client import EmbeddingClient
        self.embedding_client = EmbeddingClient()
        self.embedding_dim = 2048  # 统一���度
        self.index = faiss.IndexFlatIP(self.embedding_dim)
    
    def _get_embedding(self, text: str):
        # 优先使用 embedding-3
        try:
            return self.embedding_client.get_embedding(text)
        except Exception as e:
            # 降级到 SimpleEmbedder
            return self.simple_embedder.encode([text])[0]
    
    def search(self, query: str, ...):
        # 使用 FAISS 索引��索（不��是 O(n) 遍历��
        query_emb = self._get_embedding(query)
        query_emb = query_emb / np.linalg.norm(query_emb)
        
        similarities, indices = self.index.search(
            query_emb.reshape(1, -1), k=top_k * 2
        )
        
        # 结合���要性加��
        results = []
        for sim, idx in zip(similarities[0], indices[0]):
            memory = self.memories[self.memory_ids[idx]]
            score = sim * 0.7 + memory.importance * 0.3
            results.append((memory, score))
        
        return sorted(results, key=lambda x: x[1], reverse=True)[:top_k]
```

### 2. ⏳ ��定状���标识（中��先级���

**Phase 2 ��案**: VLM 语义布局
```python
def _compute_semantic_layout(self, app: str, screenshot) -> str:
    """使用 VLM 生成语义��局描��"""
    prompt = f"""分���这个 {app} ��用的截��，用简��的标���描述当��页面���
    格式��app_name|page_type|key_element
    例如��微信|聊天��表|搜索框
    只���回标��，不��解释。"""
    
    layout = self._cached_layout_detection(screenshot, prompt)
    return layout.strip()
```

**Phase 3 方���**: View Hierarchy Hash（Android）
```python
def _compute_view_hierarchy_hash(self, device_id: str) -> str:
    """Android: 使用 uiautomator dump 的结构哈��"""
    xml = self.device_factory.get_ui_hierarchy(device_id)
    tree = ET.fromstring(xml)
    structure = self._extract_structure_features(tree)
    return hashlib.sha256(structure.encode()).hexdigest()[:16]
```

### 3. ⏳ 数据迁移��本

**需���迁移��数据**:
- ���有 128d 向量需要重新��入为 2048d
- 图���据中的 state_id 格式可能��要更���

**迁移���本**:
```python
# scripts/migrate_embeddings.py
def migrate_memory_store():
    """��� 128d SimpleEmbedder 迁移到 2048d embedding-3"""
    old_store = MemoryStore(storage_dir="memory_db/default")
    embedding_client = EmbeddingClient()
    
    for memory_id, memory in old_store.memories.items():
        # 重新生成��入
        new_embedding = embedding_client.get_embedding(memory.content)
        memory.embedding = new_embedding
    
    # 重建 FAISS 索引
    old_store._rebuild_index()
    old_store.save()
```

## 验证清单

### Phase 2 完成标��
- [x] StateManager 创��并集成
- [x] MemoryManager 提供���一接口
- [x] agent.py ��再直接��问 graph_store
- [x] 状态��理统���到 MemoryManager
- [ ] MemoryStore 使用 embedding-3 (2048d)
- [ ] MemoryStore.search() ��用 FAISS ��引
- [ ] 相同���面的状态 ID 稳定（80%+ 命中率）
- [ ] 单元��试覆���核心功能

### ���试计划
```bash
# 1. 语法��查
python -m py_compile phone_agent/agent.py
python -m py_compile phone_agent/memory/*.py

# 2. ���元测��
pytest tests/test_state_manager.py
pytest tests/test_memory_manager.py

# 3. 集成测��
python main.py --model autoglm-phone-9b "打开��信"
```

## 下��步行动

### 立即执行（��天）
1. 实现 MemoryStore 的 embedding-3 集���
2. 修�� search() ���法使用 FAISS ��引
3. 添加��级策���（API ���可用��使用 SimpleEmbedder）

### ���期（1-2天��
4. 编���数据迁移��本
5. 添加��元测��
6. 运行��成测��验证

### ��期（3-5天，Phase 3）
7. 实现 VLM 语义布局生��
8. ��现失���路径��罚机���
9. 添加 ShortTermMemory ���件

## ��险与���解

### 高��险 🔴
- **嵌入��间迁���**: 需要重新��入所���现有数据
  - 缓解: 提供迁��脚本���保留旧数��备份
  - 回滚: 保留 SimpleEmbedder 作��降级方案

### ��风险 ���️
- **API ��赖**: embedding-3 API 不可用时系��降级
  - 缓解: 实现自��降级��� SimpleEmbedder
  - 监��: 添加 API 可用性��查

### 低风险 ✅
- **状��管理���构**: 已��成，���后兼容
- **接口变��**: MemoryManager 新增方��，不���坏现��接口

---

*��报告基�� REFACTOR_PLAN.md Phase 2 的实���执行情况��成*
