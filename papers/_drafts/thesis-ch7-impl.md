# 第7章 系统实现与可视化

第6章完成了 AMSG 空间图谱与三档调度机制的设计，至此第3章总体设计与第4、5、6章三个核心机制的方案均已给出。本章回答"设计如何落地"的问题：与设计各章给出方案与图不同，本章聚焦实现要点、关键接口与运行效果，包括开发环境与"概念→实现文件"映射、五模型家族适配与坐标转换枢纽、设备抽象实现、记忆系统多数据面实现以及 WebUI 可视化。本章首先给出开发环境与实现概览，然后依次介绍模型适配层、设备抽象层与记忆系统的实现细节，最后介绍面向运行观测的 WebUI 可视化实现。

## 7.1 开发环境与实现概览

### 7.1.1 开发环境

系统采用 Python 实现，以 OpenAI 兼容接口对接模型服务，图谱持久化采用 Neo4j，用户偏好向量检索采用 FAISS，可视化界面基于 Gradio 构建。开发与运行环境如表 7-1 所示。

表 7-1 系统开发与运行环境

| 类别 | 项目 | 说明 |
|---|---|---|
| 开发语言 | Python 3.13 | 主体代码，类型注解风格 |
| 图数据库 | Neo4j | AMSG 空间图谱跨会话持久化（第6章） |
| 向量检索 | FAISS（numpy 兜底） | 用户偏好记忆的向量存储与检索 |
| 可视化框架 | Gradio ≥ 4.0 | WebUI 运行界面与离线审核界面 |
| 模型服务接口 | OpenAI 兼容 REST 接口 | 由 `PHONE_AGENT_BASE_URL` 指定端点（默认 `http://localhost:8000/v1`），小模型与强 VLM 均经此协议族接入 |
| Android 控制 | ADB | 截图、输入、应用管理 |
| HarmonyOS 控制 | HDC | 与 ADB 同构的命令通道 |
| iOS 控制 | XCTest / WebDriverAgent | HTTP 接口，地址由 `PHONE_AGENT_WDA_URL` 指定 |
| 图像处理 | Pillow | 截图压缩、裁剪与兜底图生成 |

### 7.1.2 概念到实现文件的映射

第3章至第6章引入的设计概念在代码库中均有明确的承载文件，二者的对应关系如表 7-2 所示。该映射既是实现的索引，也是设计可追溯性的证据：每一个设计概念都能定位到单一职责的实现模块。

表 7-2 设计概念与实现文件映射

| 设计概念（所在章节） | 实现文件 |
|---|---|
| 九阶段主循环（第3章） | `phone_agent/agent.py` |
| 里程碑监督者（第4章） | `phone_agent/milestone_supervisor.py` |
| 会话记忆文件（第4章） | `phone_agent/session_memory_file.py` |
| 任务计划与原子修订（第4章） | `phone_agent/task_plan.py` |
| 每步规划器（消融上界，第4章） | `phone_agent/step_planner.py` |
| 购买安全守卫 SpecGuard（第5章） | `phone_agent/core/spec_guard.py` |
| 验证检测（第5章） | `phone_agent/verification_detector.py` |
| 记忆管理（第3、7章） | `phone_agent/memory/memory_manager.py` |
| 会话状态（第3章） | `phone_agent/memory/core/unified_state.py` |
| 按需检索（本章 7.4 节） | `phone_agent/memory/retrieval_gateway.py` |
| 运行时图谱控制器（第6章） | `phone_agent/spatial/runtime_controller.py` |
| 动作顾问（第6章） | `phone_agent/spatial/action_advisor.py` |
| 边生命周期（第6章） | `phone_agent/spatial/edge_lifecycle.py` |
| 页面分类器（第6章） | `phone_agent/memory/exploration/classifier.py` 及 `classifier_prompts.py` |
| 域模式（第6章） | `phone_agent/spatial/schemas/shopping.yaml` |
| 模型协议桥（本章 7.2 节） | `phone_agent/model/protocol_bridge.py` |
| 模型适配器（本章 7.2 节） | `phone_agent/model/adapters.py` |
| 设备抽象（本章 7.3 节） | `phone_agent/device_factory.py` |
| WebUI 可视化（本章 7.5 节） | `webui.py` |

模块的物理组织与第3章的"三区域 + 监督层"逻辑架构一一对应：监督层模块（`milestone_supervisor.py`、`session_memory_file.py`、`task_plan.py`）位于 `phone_agent/` 顶层且不依赖图谱与设备代码；图谱区域模块集中在 `phone_agent/spatial/` 子包；执行区域的模型与设备抽象分别位于 `phone_agent/model/` 与设备相关模块；决策区域由 `agent.py` 统一编排。这种"目录即区域"的组织方式使区域间的依赖方向（决策区域调用其余区域，监督层仅经会话记忆文件与计划解耦交互）在包结构层面即可检查。

### 7.1.3 主循环实现要点

主循环的实现采用"包装层 + 实现层"的两层结构：`PhoneAgent._execute_step()` 是遥测包装层，内部调用 `_execute_step_impl()` 完成第3章所述的九阶段执行流。包装层的职责是保证**每步恰好向 `step_observer` 回调发出一个遥测事件**——无论该步走 Fast Path、模型路径还是被护栏提前拦截返回，事件均携带统一字段：图谱模式（mode）、实际调度路径、注入的 `graph_hint`、Fast Path 后条件"预期→实际"判定结果以及当步截图。回调本身以异常防御方式调用，观测方崩溃不影响执行步。这一设计使 7.5 节的 WebUI 面板无需侵入九阶段逻辑即可获得完整的每步观测，也为第8章实验的逐步数据采集提供了统一出口。

## 7.2 五模型家族适配与坐标桥

### 7.2.1 五模型家族适配

系统通过 `phone_agent/model/adapters.py` 中的统一适配器架构支持五个 GUI 模型家族。各家族在坐标空间、上下文管理策略与响应格式上差异显著，适配要点如表 7-3 所示。

表 7-3 五模型家族适配表

| 模型家族 | 坐标空间 | 上下文策略 | 响应格式 |
|---|---|---|---|
| AutoGLM | [0,1000] 归一化 | 累积式（文本累加，图片仅保留当前帧） | `<answer>` / `do()`+`finish()` 调用式 |
| UI-TARS | 绝对像素（smart_resize 空间） | 最多 5 张图，超出裁剪最旧 | `Thought: … Action: …` |
| Qwen-VL | [0,999] 归一化 | 每轮重建上下文 | `<tool_call>` JSON |
| MAI-UI | [0,999] 归一化 | 最多 3 张图 | `<thinking>` + `<tool_call>` |
| GUI-Owl | [0,1.0] 小数（模型原生输出 0-999，解析后转 0-1） | 最多 1 张图（仅当前） | Action + `<tool_call>` |

适配器屏蔽了三类差异：其一，上下文策略差异——AutoGLM 采用文本累积而图片只保留当前帧，UI-TARS、MAI-UI、GUI-Owl 则按各自上限维护多帧图片窗口，Qwen-VL 每轮完全重建；其二，响应格式差异——五种原生输出格式各由专属解析器处理；其三，坐标空间差异——由下述模型协议桥统一消解。

### 7.2.2 ModelProtocolBridge 与坐标转换枢纽

`ModelProtocolBridge`（`phone_agent/model/protocol_bridge.py`）将五个家族的原生动作输出归一化为统一的设备动作中间表示 `DeviceActionIR`，使下游的动作处理器、SpecGuard 与图谱记账面对的是单一动作语义而非五套方言。其中坐标转换的枢纽是 `_scale_coord()` 函数：所有坐标空间不做两两直转，而是一律经 (0,1) 归一化中间表示互转，核心逻辑如下（节选）：

```python
def _scale_coord(coord, source_space, target_space, screen_size=None):
    if source_space == target_space:
        return coord
    x, y = coord
    width, height = screen_size or (1000, 1000)
    # 第一步：任意源空间 → (0,1) 归一化中间表示
    if source_space == "absolute":
        nx, ny = x / width, y / height
    elif source_space == "normalized_01":
        nx, ny = x, y
    elif source_space in {"normalized_999", "qwen_999", "maiui_999"}:
        nx, ny = x / 999.0, y / 999.0
    else:  # normalized_1000 / autoglm_1000 及默认
        nx, ny = x / 1000.0, y / 1000.0
    # 第二步：中间表示 → 目标空间（钳位到合法区间）
    if target_space == "absolute":
        return (min(max(nx, 0.0), 1.0) * width, min(max(ny, 0.0), 1.0) * height)
    if target_space == "normalized_01":
        return (min(max(nx, 0.0), 1.0), min(max(ny, 0.0), 1.0))
    ...  # 999 / 1000 空间同理按比例缩放
```

这一"星型"转换结构带来两个工程收益。第一，**图谱的模型无关性**：AMSG 图谱中的坐标锚定证据以归一化形式存储，同一 Neo4j 图谱可服务任意模型家族——某模型积累的提升边在切换到另一坐标空间的模型时，由协议桥在运行时完成坐标系转换，导航知识无需重建。第二，安全护栏的家族一致性：第5章的 SpecGuard 在 canonical action（即 `DeviceActionIR`）层面介入，而非各家族原生输出层面，因此购买提交点拦截对五个模型家族一致生效，这正是第5章"购物安全是系统属性而非提示工程"论断的实现基础。

## 7.3 设备抽象实现

### 7.3.1 DeviceFactory 与平台边界

`DeviceFactory`（`phone_agent/device_factory.py`）统一了 Android（ADB）与 HarmonyOS（HDC）两个平台：二者命令通道同构，工厂按 `PHONE_AGENT_DEVICE_TYPE` 选择后端，上层主循环对平台无感知。iOS 则是一条独立实现路径：由 `IOSPhoneAgent` 与 `IOSActionHandler`（`phone_agent/agent_ios.py`）通过 XCTest/WebDriverAgent 的 HTTP 接口驱动设备，**不经 DeviceFactory，且不挂载图谱栈与每步遥测**。如实陈述这一实现边界：iOS 路径目前仅具备基础的"截图→推理→动作"循环能力，第4至6章的监督、护栏与图谱机制尚未覆盖，该局限将在第9章作为不足讨论。

### 7.3.2 ActionHandler 实现要点

`ActionHandler` 将规范动作字典转换为具体设备调用，实现上有四个要点。其一，键盘管理：文本输入前切换至 ADB 键盘、清空输入框、写入文本，输入完成后恢复用户原输入法（IME），避免污染用户设备状态。其二，时序延迟：相邻操作间插入延迟，等待界面动画与网络加载稳定，降低"动作落在过渡帧"导致的失败。其三，同页复合动作序列：对图谱合成的 Compound 边（如第6章的"Type 查询词 + Tap 搜索"），在同一页面内按序执行多个原子动作，中间不回到模型。其四，截图兜底：当截图损坏或被系统安全机制拦截（如支付页禁止截屏）时，返回标记 `is_sensitive=True` 的兜底图而非抛出异常——该标记正是第5章异常看门狗的机械输入信号，连续出现即触发人工接管，构成感知层异常到护栏层响应的确定性接口。

## 7.4 记忆系统多数据面实现

### 7.4.1 五数据面

记忆系统由 `MemoryManager`（`phone_agent/memory/memory_manager.py`）统一管理五个数据面，各面的存储介质、生命周期与解决的问题如表 7-4 所示。

表 7-4 记忆系统五数据面

| 记忆面 | 存储 | 生命周期 | 服务对象 | 解决的问题 |
|---|---|---|---|---|
| 用户记忆 | FAISS | 跨会话 | 首步澄清/槽位填充 | "买个手机"缺规格——从历史偏好填充而不追问 |
| 会话状态 | `UnifiedSessionState` 内存对象 | 单任务 | 进度注入/逐步记账 | 进度追踪、商品提取、停滞检测 |
| 会话记忆文件 | JSON（原子落盘） | 单任务 | 抗遗忘锚+监督接口 | 长任务焦点丢失（第4章） |
| 图谱记忆 | Neo4j | 跨会话 | 图谱定位/Fast Path | 机械导航重复劳动（第6章） |
| 轨迹文件 | JSON | 单任务产物 | 任务后图谱演进 | 发现新转移供 VLM 审核 |

其中会话记忆文件的落盘采用"先写 `.json.tmp` 临时文件、再以 `os.replace` 原子替换"的方式，保证进程异常中断时磁盘上的会话文件始终是完整一致的版本——这是第4章"持久状态全在会话记忆文件、对话上下文可丢弃"恢复闭环的存储层前提。

### 7.4.2 按需检索与分层注入

`RetrievalGateway`（`phone_agent/memory/retrieval_gateway.py`)实现按需检索：网关监听模型每步的思考文本，仅当检测到回忆、比较、计算或停滞类信号时才触发详细检索（注入候选商品列表、购物车计算结果或历史步骤），并施加 3 步冷却期避免连续触发。按实测估算，典型 30 步任务中仅 3-5 步触发详细检索，其余各步只携带轻量进度摘要，从而在提供必要记忆的同时避免上下文膨胀。

`get_injection_context` 按层组装每步注入内容：进度摘要（始终注入）、当前焦点（来自会话记忆文件的单一【当前目标】）、按需检索结果（仅触发步）与约束提醒（价格红线等）。需要说明的一处实现演化：早期版本的 `compress_session_history` 承担历史压缩职责，但其存在状态泄漏缺陷，且抗遗忘职能已由第4章的会话记忆文件整体取代，故现版本将其保留为 no-op 接口，仅维持调用兼容。

### 7.4.3 任务结束流程

`end_task` 按固定四步实现任务收尾：第一步保存轨迹文件（无论任务成败，作为任务产物与审计依据）；第二步学习用户偏好写入 FAISS（仅成功任务）；第三步图谱刷新（仅成功任务：暂存观测经规范化、质量过滤后写入 Neo4j，对应第6章的质量门）；第四步 VLM 轨迹审核（仅成功任务，新发现转移以 hypothesis 阶段导入）。成败分流在实现上由单一布尔参数驱动，保证"失败任务零图谱写入"的不变量不依赖调用方自觉。

## 7.5 WebUI 可视化

### 7.5.1 StreamingAgent 与单一编排入口

WebUI（`webui.py`）的核心是 `StreamingAgent`：它在 worker 线程中调用**真实的** `PhoneAgent.run()`，通过事件队列将执行过程流式推送到 Gradio 前端。关键工程论点是：WebUI 与 CLI 共享同一编排入口，可视化层只做包装与转发，不复制任何执行逻辑——这杜绝了"演示路径与真实路径分叉"导致的可视化失真，WebUI 中观察到的每个调度决策即是 CLI 运行时的真实决策。

`StreamingAgent` 通过三个流式钩子获取运行数据：`stream_callback` 提供模型逐 token 的思考流，使用户实时看到小模型的推理过程；`step_observer` 提供 7.1.3 节所述的每步遥测事件；`takeover_callback` 在护栏触发人工接管时弹出提示，并配合 Interact 回复框让用户以自然语言回答 Agent 的询问（如 SpecGuard 拦截越界购买后的确认），回复经回调注入任务上下文继续执行。

### 7.5.2 图谱协同面板

图谱协同面板由纯函数 `format_graph_panel(info, counters)` 渲染：输入为一个 `step_observer` 遥测事件与累计计数器，输出面板文本，无任何隐藏状态，便于单元测试覆盖。面板每步展示五类信息：当前页面类型与图谱模式（navigate/explore/verify_with_vlm/goal_reached）；本步实际调度档位（Fast Path / Co-pilot / 模型完整推理）；Fast Path 的后条件"预期页面→实际页面"判定结果；注入模型上下文的图谱导航提示原文（用户可核对图谱究竟告诉了模型什么）；以及累计统计——三档调度各自的命中次数、RuntimeDAG 命中率与省去页面分类器调用的次数。该面板使第6章三档调度的运行行为对用户完全透明，也是第8章统计调度分布数据的直接来源。

WebUI 运行界面如图 7-1 所示，左侧为任务输入与设备截图实时回显，右侧为思考流与图谱协同面板。

图 7-1 WebUI 运行界面（截图待补充）

图 7-2 给出一次 Fast Path 命中步的图谱协同面板细节，可见调度档位、后条件判定与累计统计的逐步更新。

图 7-2 图谱协同面板运行效果（截图待补充）

## 7.6 本章小结

本章完成了系统从设计到实现的落地论述：首先给出开发环境与"设计概念→实现文件"的完整映射，说明模块物理组织与三区域加监督层逻辑架构的对应关系，以及主循环遥测包装层的实现要点；其次介绍五模型家族适配与以 `_scale_coord()` 为枢纽的坐标桥实现，论证了图谱模型无关性与安全护栏家族一致性的实现基础；然后阐述 DeviceFactory 设备抽象、ActionHandler 实现要点与 iOS 路径的实现边界；继而给出记忆系统五数据面、按需检索与任务结束四步流程的实现；最后介绍以单一编排入口为原则的 WebUI 可视化与图谱协同面板。系统实现完成后，下一章对其功能与三个创新点进行系统测试与实验验证。
