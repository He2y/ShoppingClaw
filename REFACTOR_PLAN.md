# ClawGUI-Agent ��构重��计划

> 创建日期��2026-05-03
> 目标���解决状态管理��裂和记忆��统协调问��

---

## 一��问题��先级���级

### 🔴 P0 - 严重缺陷��必须立��修复���

1. **状态管理��裂** (7.1)
   - agent.py 和 MemoryManager 各自维护��态
   - agent.py ��接穿���访问 graph_store
   - 影响：状��不一���，记忆系��失效

2. **嵌��维度���裂** (7.2)
   - MemoryStore: 128d SimpleEmbedder
   - TaskIndex: 2048d embedding-3
   - 影响���两套向量��间无法协��工作

3. **FAISS 索引��代码** (7.3)
   - MemoryStore.search() 使用 O(n) 暴���遍历
   - FAISS 索引��建但���不使用
   - 影响：性能��题，���忆检索慢

4. **MD5 ��图哈希不��定** (7.7)
   - 同一界��每次���图哈希不同
   - 影响：图��态匹���永远失败

### 🟠 P1 - 中等缺陷（影响��能正���性）

5. **Navigate 模式无��信度���查** (7.5)
6. **Neo4j 静��降级** (7.6)
7. **失败���径无惩罚��习** (7.8)

### 🟡 P2 - 轻微缺陷（代码��量问���）

8. **clarify.py 孤立文件** (7.4)
9. **Interact 回复��获重���** (7.9)
10. **CONTACT_APP_BINDNG 拼写���误** (7.10)
11. **硬编�� 15 步轨迹��制** (7.11)
12. **semantic_layout 粒度过粗** (7.12)

---

## 二、重��策略���三阶��渐进���修复

### Phase 1: ���速修复（1-2天）
**目���：** 修复 P2 缺�� + 部分 P1，不��变架���

- [ ] 删��� clarify.py
- [ ] 删除 agent.py 中重��的 Interact 捕��代码
- [ ] 修复 CONTACT_APP_BINDNG 拼写错��
- [ ] Navigate 模式��加置信度��值检���
- [ ] Neo4j 不可���时抛出明��异常（��非静���降级��
- [ ] 轨��步数��制改为可��置参���

### Phase 2: 架构��复（3-5��）
**目标��** 解��� P0 问��，重���状态管理和��忆协���

#### 2.1 统一状态��理
- [ ] 创建 `StateManager` ��，封���状态追踪��辑
- [ ] 将 `_prev_state_id` / `_current_state_id` 移��� StateManager
- [ ] MemoryManager 持�� StateManager 实例
- [ ] agent.py 通过 MemoryManager ��口更���状态

#### 2.2 修复记��系统��调
- [ ] 封�� `MemoryManager.add_state_transition()` 方��
- [ ] agent.py 不再��接访��� `graph_store`
- [ ] 统一 `start_task()` / `end_task()` 的��态参数��递
- [ ] 创�� `_update_memory()` 统一��口点

#### 2.3 统一��入空���
- [ ] MemoryStore 切��到 embedding-3 client (2048d)
- [ ] 移除 SimpleEmbedder
- [ ] MemoryStore.search() 改用 FAISS index.search()
- [ ] 添加���级策略：API 不可用时��用本地��型

#### 2.4 稳��状态���识
- [ ] 替换 MD5(screenshot) 为���义哈希
- [ ] 方案 A: VLM 生成 semantic_layout (app + page + key_elements)
- [ ] 方案 B: Android uiautomator dump ��� View Hierarchy Hash
- [ ] 方�� C: 混合方案��优先 B，降���到 A）

### Phase 3: 架构优化��5-7天���
**目标���** 长期改进��提升���统鲁棒��

- [ ] 实现失��路径的置信度��罚机���
- [ ] 添��� ShortTermMemory 组件（��持跨���意图）
- [ ] semantic_layout 细化（��面级���识别��
- [ ] 记���系统可��测性���日志、指��、调��工具���
- [ ] 集成测试��盖（状��管理���记忆检索、��构建���

---

## 三、详细��计

### 3.1 StateManager 设计

```python
# phone_agent/memory/state_manager.py

class StateManager:
    """统一的状��追踪���理器"""
    
    def __init__(self):
        self._current_state_id: str | None = None
        self._prev_state_id: str | None = None
        self._task_start_state_id: str | None = None
        self._task_end_state_id: str | None = None
        self._state_history: list[str] = []
    
    def compute_state_id(self, screenshot_hash: str, semantic_layout: str) -> str:
        """计算��定的���态标识"""
        # Phase 2: 使用 semantic_layout + screenshot_hash
        # Phase 3: 升级为 View Hierarchy Hash
        return f"state_{semantic_layout}_{screenshot_hash[:8]}"
    
    def update_state(self, new_state_id: str) -> tuple[str | None, str]:
        """��新状��，返��� (prev_state_id, current_state_id)"""
        self._prev_state_id = self._current_state_id
        self._current_state_id = new_state_id
        self._state_history.append(new_state_id)
        return self._prev_state_id, self._current_state_id
    
    def start_task(self, initial_state_id: str):
        """任务开��时设置初��状态"""
        self._task_start_state_id = initial_state_id
        self._current_state_id = initial_state_id
        self._state_history = [initial_state_id]
    
    def end_task(self, final_state_id: str):
        """任��结束时��录最��状态"""
        self._task_end_state_id = final_state_id
    
    def get_current_state(self) -> str | None:
        return self._current_state_id
    
    def get_task_states(self) -> tuple[str | None, str | None]:
        """返回 (start_state_id, end_state_id)"""
        return self._task_start_state_id, self._task_end_state_id
```

### 3.2 MemoryManager ���构

```python
# phone_agent/memory/memory_manager.py

class MemoryManager:
    def __init__(self, ...):
        self.state_manager = StateManager()
        self.memory_store = MemoryStore(...)
        self.graph_store = GraphStore(...)
        # 移除 _current_state_id 等字���
    
    def start_task(self, task: str, initial_state_id: str):
        """任务开始 - 接受初始状��"""
        self.state_manager.start_task(initial_state_id)
        self._extract_from_task(task)
        # ...
    
    def update_state_and_transition(
        self, 
        new_state_id: str,
        action: dict,
        task: str
    ):
        """统��的状���更新 + 图转���记录"""
        prev_state, current_state = self.state_manager.update_state(new_state_id)
        
        if prev_state:
            self.graph_store.add_state_transition(
                prev_state, current_state, action, task
            )
    
    def end_task(self, success: bool, result: str, final_state_id: str):
        """任务结束 - 接��最终���态"""
        self.state_manager.end_task(final_state_id)
        start_state, end_state = self.state_manager.get_task_states()
        
        # 提交轨迹��图
        self._save_pending_trajectory()
        # ...
```

### 3.3 agent.py 简���

```python
# phone_agent/agent.py

class PhoneAgent:
    def __init__(self, ...):
        # 移除 _prev_state_id, _current_state_id
        # 所有状��管理��托给 MemoryManager
    
    def _execute_step(self, task: str, is_first: bool):
        # Phase 1: 截图���集
        screenshot = self.device_factory.get_screenshot(...)
        current_app = self.device_factory.get_current_app(...)
        
        # Phase 2: ���算状��标识���通过 MemoryManager）
        ui_hash = hashlib.md5(screenshot.base64_data.encode()).hexdigest()
        semantic_layout = self._compute_semantic_layout(current_app, screenshot)
        
        # Phase 3: 定位���下文（MemoryManager 内���更新状态��
        context_data = self.memory_manager.locate_and_get_context(
            ui_hash, semantic_layout, task
        )
        
        # ... VLM 推理 ...
        
        # Phase 6: 执行���作
        result = self.action_handler.execute(action, width, height)
        
        # Phase 7: 统一记��更新（包��状态���换）
        current_state_id = context_data["current_state_id"]
        self.memory_manager.update_memory(
            thinking=response.thinking,
            action=action,
            app=current_app,
            state_id=current_state_id,
            task=task
        )
```

### 3.4 统���嵌入��间

```python
# phone_agent/memory/memory_store.py

class MemoryStore:
    def __init__(self, ...):
        # 移��� SimpleEmbedder
        self.embedding_client = EmbeddingClient(...)
        self.embedding_dim = 2048  # 统一��度
        self.index = faiss.IndexFlatIP(self.embedding_dim)
    
    def add(self, memory_type: MemoryType, content: str, ...):
        # 使�� embedding-3 API
        embedding = self.embedding_client.get_embedding(content)
        
        # 添加�� FAISS 索引
        self._add_to_index(memory_id, embedding)
    
    def search(self, query: str, ...):
        # 使��� FAISS 索引��索
        query_emb = self.embedding_client.get_embedding(query)
        query_emb = query_emb / np.linalg.norm(query_emb)
        
        # FAISS 搜索
        similarities, indices = self.index.search(
            query_emb.reshape(1, -1), k=top_k * 2
        )
        
        # 结合���要性加权
        results = []
        for sim, idx in zip(similarities[0], indices[0]):
            memory = self.memories[self.memory_ids[idx]]
            score = sim * 0.7 + memory.importance * 0.3
            results.append((memory, score))
        
        return sorted(results, key=lambda x: x[1], reverse=True)[:top_k]
```

### 3.5 稳���状态标识��案

#### 方案 A: VLM 语义���局（Phase 2 实��）

```python
def _compute_semantic_layout(self, app: str, screenshot) -> str:
    """使用 VLM 生成语义布��描述"""
    prompt = f"""��析这��� {app} 应用的��图，用��短的���签描述当��页面：
    格���：app_name|page_type|key_element
    例如���微信|���天列表|搜索��
    例如：��东|商品��情|加入购��车按钮
    只返���标签，不��解释。"""
    
    # 调用 VLM（使用��存避���重复��用）
    layout = self._cached_layout_detection(screenshot, prompt)
    return layout.strip()
```

#### 方案 B: View Hierarchy Hash（Phase 3 实��）

```python
def _compute_view_hierarchy_hash(self, device_id: str) -> str:
    """Android: 使用 uiautomator dump 的���构哈希"""
    # 获取 UI 层次���构
    xml = self.device_factory.get_ui_hierarchy(device_id)
    
    # 提取��构特征（��略动���内容）
    tree = ET.fromstring(xml)
    structure = self._extract_structure_features(tree)
    
    # 计算稳��哈希
    return hashlib.sha256(structure.encode()).hexdigest()[:16]

def _extract_structure_features(self, node) -> str:
    """提取 UI 结构���征（忽略��本内容、��标）"""
    features = []
    for elem in node.iter():
        # 只���留类名和��源ID
        class_name = elem.get('class', '')
        resource_id = elem.get('resource-id', '')
        if resource_id:
            features.append(f"{class_name}#{resource_id}")
    return "|".join(features)
```

---

## 四、实��计划

### Week 1: Phase 1 快速修��
- Day 1: 删除死代��（clarify.py, 重��代码���
- Day 2: 修复��写错��、添加置信度��查、配置��参数

### Week 2: Phase 2 架构修��（核心）
- Day 1-2: 实现 StateManager + 重构 MemoryManager
- Day 3: 重构 agent.py 状态管理逻辑
- Day 4: 统一��入空��（embedding-3 ��成）
- Day 5: VLM 语义���局实�� + 测试

### Week 3: Phase 3 架构��化
- Day 1-2: View Hierarchy Hash 实��
- Day 3: 失���路径惩罚机制
- Day 4: ShortTermMemory ���件
- Day 5: ���成测�� + 文���更新

---

## 五��风险���估

### ��风险���
1. **嵌入空间迁��**
   - 风���：现有��忆数��需要���新嵌入
   - ��解：��供迁���脚本，保留��数据���份

2. **状态标识��更**
   - 风��：现���图数据中的 state_id 格式不兼容
   - 缓���：图数��版本���记，兼容��格式��取

### 中���险项
3. **MemoryManager API 变更**
   - ���险：webui.py 和其��调用���需要��步更新
   - 缓解��保留���容层，��步迁���

### 低风险项
4. **代码删除**
   - 风���：clarify.py ��能有未发��的依���
   - 缓解���全局搜索引用��运行测试

---

## 六、验��标准

### Phase 1 验证
- [ ] 所有 P2 缺��修复
- [ ] 代码通�� lint ��查
- [ ] 现有功��无回��

### Phase 2 ���证
- [ ] agent.py 不���直接访问 graph_store
- [ ] 状态管��统一��� MemoryManager
- [ ] MemoryStore ��用 FAISS 索��搜索
- [ ] 相同界��的状态 ID 稳定（至少 80% 命��率）

### Phase 3 验证
- [ ] View Hierarchy Hash 在相同界��下 95%+ 稳定
- [ ] 失败���务的路��置信���下降
- [ ] 集���测试覆盖��心流��

---

## 七、回滚��略

每个 Phase 完���后打 git tag：
- `refactor-phase1-complete`
- `refactor-phase2-complete`
- `refactor-phase3-complete`

��果发现��重问题，��以回���到上一个��定 tag���

---

*本计���基于 ARCHITECTURE_DETAILED.md v3.0 的缺陷分��制定���*
