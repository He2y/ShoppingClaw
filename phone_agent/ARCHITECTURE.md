# ClawGUI-Agent 系统架构详解

> 本文档详细介绍 `phone_agent` 目录下的核心模块设计原理、任务执行流程、上下文管理机制以及不同模型输出格式的处理策略。

---

## 1. 整体架构概述

ClawGUI-Agent 是一个视觉-语言模型（VLM）驱动的手机自动化框架，通过"截图 → 推理 → 执行"的闭环控制流，让 AI 自主完成手机操作任务。

### 1.1 核心设计理念

- **平台抽象**：通过 `DeviceFactory` 统一抽象 ADB（Android）、HDC（HarmonyOS）、XCTEST（iOS）三种设备后端
- **模型适配器模式**：不同 VLM 的输出格式差异极大，通过适配器层隔离变化
- **无状态 Agent**：Agent 本身不保存状态，状态（上下文、历史）由调用方或内部 `_context` 管理
- **内存感知**：可选的个性化记忆系统在每次任务后自动学习用户偏好和联系人信息

### 1.2 模块依赖关系

```
                    main.py / webui.py
                           │
                           ▼
                     PhoneAgent
                           │
         ┌─────────────────┼─────────────────┐
         │                 │                 │
         ▼                 ▼                 ▼
    ModelClient     ActionHandler      DeviceFactory
    (模型调用)      (动作解析执行)     (设备抽象层)
         │                 │                 │
         ▼                 ▼          ┌──────┼──────┐
    ModelAdapter       handlers/      │      │      │
    (模型适配器)      handler_*.py    ▼      ▼      ▼
         │               (专用)     ADB    HDC   XCTEST
         ▼                            │      │      │
    prompt templates              screenshot/input/device control
```

---

## 2. 核心执行流程（Agent Loop）

`PhoneAgent.run(task)` 是整个系统的入口，其执行流程如下：

```
┌─────────────────────────────────────────────────────────┐
│                    agent.run("打开微信发消息")              │
├─────────────────────────────────────────────────────────┤
│ 1. 初始化阶段                                            │
│    _context = [], _step_count = 0                        │
│    tracer.start_task()   ← 创建 episode 目录             │
│    memory_manager.start_task() ← 从任务描述中提取联系人    │
│    clear_history()       ← 清空 QwenVL/GUI-Owl 的历史   │
├─────────────────────────────────────────────────────────┤
│ 2. 执行循环 (_execute_step)                              │
│                                                         │
│  ┌─────────────── 截图 ───────────────┐               │
│  │ DeviceFactory.get_screenshot()      │               │
│  │ DeviceFactory.get_current_app()    │               │
│  └────────────────────────────────────┘               │
│                        │                               │
│                        ▼                               │
│  ┌─────────────── 构建消息 ─────────────┐              │
│  │ Adapter.build_messages()             │              │
│  │   ├─ AutoGLM: 追加式 append context  │              │
│  │   ├─ UITARS:  追加式 + limit 5 图  │              │
│  │   ├─ QwenVL:  重构建 system+user   │              │
│  │   ├─ MAIUI:   多消息式 + limit 3 图│              │
│  │   └─ GUI-Owl: 重构建 + limit 1 图  │              │
│  └────────────────────────────────────┘              │
│                        │                               │
│                        ▼                               │
│  ┌─────────────── 模型推理 ─────────────┐              │
│  │ ModelClient.request()               │              │
│  │   └─ 流式输出: thinking → action    │              │
│  └────────────────────────────────────┘              │
│                        │                               │
│                        ▼                               │
│  ┌─────────────── 解析响应 ─────────────┐              │
│  │ Adapter.parse_response()             │              │
│  │   └─ (thinking, action_str)         │              │
│  │ SpecialHandler.parse_action()        │              │
│  │   └─ ActionResult (结构化动作)       │              │
│  └────────────────────────────────────┘              │
│                        │                               │
│                        ▼                               │
│  ┌─────────────── 执行动作 ─────────────┐              │
│  │ SpecialHandler.execute()             │              │
│  │   └─ DeviceFactory.tap/swipe/type   │              │
│  └────────────────────────────────────┘              │
│                        │                               │
│                        ▼                               │
│  ┌─────────────── 记录追踪 ─────────────┐              │
│  │ context.append(assistant_message)   │              │
│  │ memory_manager.add_step()          │              │
│  │ tracer.record_step()                │              │
│  └────────────────────────────────────┘              │
│                                                         │
│  finished? ──否──→ 回到 "截图" 继续循环                  │
│     │                                                   │
│    是                                                   │
│     ▼                                                   │
│  end_task(): memory_manager.end_task() + tracer.end_task()│
└─────────────────────────────────────────────────────────┘
```

---

## 3. 模型适配器体系（Model Adapters）

适配器是本框架最核心的设计。每个模型适配器负责：
1. **生成系统提示词**（`get_system_prompt`）
2. **解析模型输出**（`parse_response`）
3. **构建 API 消息格式**（`build_messages`）
4. **限制上下文图片数量**（`limit_context`，部分适配器需要）

### 3.1 适配器总览

| 适配器 | 模型 | 输出格式 | 消息构建策略 | 上下文图片数 |
|--------|------|----------|-------------|-------------|
| `AutoGLMAdapter` | AutoGLM, GLM-4V | `<answer>action</answer>` | 追加式（保留所有历史） | 无限制 |
| `UITarsAdapter` | Doubao UI-TARS | `Thought:\nAction:` | 追加式 | 最多 5 张 |
| `QwenVLAdapter` | Qwen2.5/3-VL | `Thought:\nAction:\n<tool_call>` | 重构建式 | 最多 8 张 |
| `MAUIAdapter` | MAI-UI | `<thinking>...</thinking><tool_call>` | 多消息式 | 最多 3 张 |
| `GUIOwlAdapter` | GUI-Owl | `Action:\n<tool_call>` | 重构建式 | 始终 1 张 |

### 3.2 消息构建策略详解

#### AutoGLM — 追加式（Append）

```
messages = context.copy()  # 保留所有历史消息
if first_turn:
    messages.append(system)
    messages.append(user(task + screen_info + image))
else:
    messages.append(user(screen_info + image))
```

优点：天然保留完整对话历史，适合长任务。
缺点：上下文随步数线性增长，可能超出模型上下文窗口。

#### UI-TARS — 追加式 + 图片限制

结构同 AutoGLM，但每轮 `build_messages` 后调用 `limit_context` 限制最多 5 张历史截图。早期截图从消息中移除（保留文本如 system prompt）。

#### QwenVL / GUI-Owl — 重构建式（Rebuild）

```
messages = [
    system_prompt,                        # 始终重置
    user(user_query + 操作历史 + image)    # 始终重置
]
```

关键设计：不向 context 中追加 assistant 消息，操作历史通过 `_action_history` 列表在 adapter 内部维护，每轮用 `build_qwenvl_user_query()` 将历史格式化为文本注入 user message。

**为什么这样做？** QwenVL/GUI-Owl 的 system prompt 包含 `<tools>` XML 定义，若每轮追加 assistant 消息，system prompt 会被挤压到对话中间。两种模型都设计为单张截图 + 指令的"快照"模式，不依赖多轮历史积累。

#### MAI-UI — 多消息追加式

MAI-UI 的消息格式严格遵循官方规范：
- 第一轮：`[system(纯文本), user(任务指令), user(截图)]`
- 后续轮：`[...history..., assistant(纯文本), user(截图)]`

`limit_context` 保留最近 3 张截图。

### 3.3 模型检测机制

`detect_model_type(model_name)` 通过正则匹配模型名称自动选择适配器：

```python
# 优先级从高到低（GUI-Owl > UI-TARS > Qwen-VL > MAI-UI > AutoGLM）
gui-owl   → GUIOWL
ui-tars   → UITARS
qwen.*vl  → QWENVL
mai[-_]ui → MAIUI
autoglm   → AUTOGLM
默认       → AUTOGLM
```

---

## 4. 动作处理体系（Action Handlers）

模型输出 `action_str` 后，需要：
1. **解析**为结构化动作（`parse_action` / `parse_response`）
2. **坐标转换**（从模型坐标空间 → 设备绝对像素）
3. **执行**（调用 `DeviceFactory`）

### 4.1 默认处理器（AutoGLM — `handler.py`）

`ActionHandler` 是默认处理器，通过 AST 解析 `do(action="Tap", element=[x, y])` 格式。

**解析流程**：
1. 预处理：去除 markdown 代码块、XML 标签、`\n` 转义
2. 优先尝试 JSON dict 解析（应对 `<tool_call>{"name":"mobile_use",...}` 格式）
3. 使用正则提取 `do(action=...)` 和 `finish(message=...)`
4. AST 解析作为 fallback

**坐标转换**（`_convert_relative_to_absolute`）：

模型输出坐标可能是以下三种格式之一：
```python
[x, y]                    # 点坐标
[x1, y1, x2, y2]         # 边界框
[[x1, y1, x2, y2]]       # 嵌套边界框
```
统一转换为绝对像素：`x_px = int(x / 1000 * screen_width)`

### 4.2 专用处理器对比

| 处理器 | 源格式 | 关键处理 |
|--------|--------|----------|
| `handler_uitars.py` | `click(point='<point>x y</point>')` | 从 `<point>` XML 标签提取坐标 |
| `handler_qwenvl.py` | `<tool_call>{"name":"mobile_use","arguments":{"action":"click","coordinate":[x,y]}}</tool_call>` | JSON 解析 + 0-999→绝对像素 |
| `handler_maiui.py` | `<thinking>...</thinking><tool_call>{"action":"click",...}</tool_call>` | 处理换行标签缺失、`</think>`→`</thinking>` 修复 |
| `handler_guiowl.py` | `Action: 描述\n<tool_call>{"name":"mobile_use",...}</tool_call>` | 坐标预处理（÷999 归一化） |
| `handler_ios.py` | 同 AutoGLM | 坐标 ÷3 后用 W3C WebDriver Actions API |

### 4.3 鲁棒性设计

MAI-UI 处理器展示了典型的鲁棒性处理：
- 自动修复缺失的 XML 闭合标签（`</think>` → `</thinking>`）
- 处理嵌套的 `<think><tool_call>...` 结构（从 tool_call 内提取真正的 thinking）
- 清理 `\n` 转义序列和格式噪声（````html` 等）
- JSON 正则搜索作为终极 fallback

---

## 5. 设备抽象层（Device Factory）

`DeviceFactory` 是跨平台抽象的核心，提供了统一的设备操作接口：

```python
class DeviceFactory:
    get_screenshot(device_id) → Screenshot
    get_current_app(device_id) → str
    tap(x, y, device_id)
    double_tap(x, y, device_id)
    long_press(x, y, duration_ms, device_id)
    swipe(x1, y1, x2, y2, duration_ms, device_id)
    back(device_id)
    home(device_id)
    launch_app(app_name, device_id)  # 通过 config/apps*.py 映射
    type_text(text, device_id)
    clear_text(device_id)
```

全局单例模式通过 `set_device_type()` 和 `get_device_factory()` 管理。

### 5.1 Android (ADB)

- **截图**：`screencap -p /sdcard/tmp.png` → pull 到本地 → PIL 解码
- **输入**：ADB Keyboard（`am broadcast -a ADB_INPUT_B64`），通过 `detect_and_set_adb_keyboard()` 自动切换输入法
- **应用启动**：`adb shell monkey -p {package} -c android.intent.category.LAUNCHER 1`
- **当前应用**：`dumpsys window windows` 解析

### 5.2 HarmonyOS (HDC)

- **截图**：`snapshot_display`（新）或 `screenshot`（旧）→ pull → JPEG→PNG 转换
- **输入**：`uitest uiInput text`（支持换行符特殊处理）
- **应用启动**：`aa start -b {bundle} -a {ability}`，需要 `config/apps_harmonyos.py` 中的 bundle→ability 映射
- **当前应用**：`hidumper -s WindowManagerService` 解析
- **按键**：使用具名按键（`Back`/`Home`）而非数字 keycode

### 5.3 iOS (XCTEST)

- **截图**：WDA `GET /screenshot`（主要），`idevicescreenshot`（fallback）
- **输入**：WDA `POST /wda/keys` + 剪贴板 `setPasteboard`（解决 WDA 无法直接输入中文的问题）
- **应用启动**：`POST /wda/apps/launch` + bundleId
- **当前应用**：WDA `GET /wda/activeAppInfo`
- **手势**：W3C WebDriver 指针动作 API（`pointerMove/pointerDown/pointerUp`）
- **缩放因子**：坐标需除以 3（iOS 屏幕逻辑分辨率 vs. 实际像素）

---

## 6. 模型客户端（ModelClient）

`ModelClient` 封装了对 VLM 推理服务的 HTTP 调用：

```python
class ModelClient:
    def request(self, messages: list[dict]) → ModelResponse:
        # 1. 构建 OpenAI-compatible 请求
        # 2. 流式发送 streaming=True
        # 3. 实时打印 thinking token
        # 4. 缓冲 action 部分
        # 5. 返回 ModelResponse(thinking, action_str, raw_content, metrics)

class ModelResponse:
    thinking: str       # 模型的思考过程
    action_str: str     # 原始 action 字符串
    raw_content: str    # 完整原始输出
    metrics: dict       # 性能指标（首 token 延迟、推理时间等）
```

**流式打印设计**：thinking 部分实时打印到 stdout（用于 CLI 可观测性），action 部分在遇到 action 标记后停止打印、开始缓冲，直到流结束。

---

## 7. 配置与提示词体系

### 7.1 系统提示词

每个适配器对应一套专用的提示词模板：

| 模板文件 | 适配器 | Action Space |
|----------|--------|-------------|
| `prompts_zh.py` / `prompts_en.py` | AutoGLM | Launch, Tap, Type, Swipe, Back, Home, Wait, finish |
| `prompts_uitars.py` | UI-TARS | click, long_press, type, scroll, open_app, drag, press_home, press_back, finished, wait |
| `prompts_qwenvl.py` | QwenVL | click, long_press, swipe, type, open_app, wait, answer, terminate |
| `prompts_maiui.py` | MAIUI | click, long_press, type, swipe, open, drag, system_button, wait, terminate, answer |
| `prompts_guiowl.py` | GUI-Owl | click, long_press, swipe, type, system_button, open, wait, answer, terminate, key, interact |

### 7.2 坐标系统差异

| 模型 | 坐标空间 | 说明 |
|------|----------|------|
| AutoGLM | `[0, 1000]` 归一化 | 除以 1000 得比例 |
| UI-TARS | 绝对像素（smart_resize 空间） | 直接使用，需知道缩放比 |
| QwenVL | `[0, 999]` 归一化 | 除以 999 得比例 |
| MAI-UI | `[0, 999]` 归一化 | 除以 999 得比例 |
| GUI-Owl | `[0, 999]` 归一化 | 除以 999 得比例 |

### 7.3 时间配置（Timing）

所有操作延迟集中管理于 `config/timing.py`：
- `keyboard_switch_delay`: 1.0s（切换输入法延迟）
- `text_input_delay`: 1.0s（输入文本延迟）
- `tap_delay` / `swipe_delay` / `back_delay` 等：各 1.0s

---

## 8. 个性化记忆系统（Memory）

### 8.1 架构

```
MemoryManager          MemoryStore
    │                      │
    ├─ add_step()         ├─ add() / search() / update() / delete()
    ├─ end_task()         └─ memories.json（持久化，JSON 文件存储）
    ├─ add_user_preference()
    └─ get_relevant_context()
```

### 8.2 记忆类型

| 类型 | 内容 | 重要性 |
|------|------|--------|
| `CONTACT` | 人名 | 0.7 |
| `CONTACT_APP_BINDING` | 联系人→App 关联 | 0.8 |
| `APP_USAGE` | 应用使用记录 | 0.5 |
| `USER_PREFERENCE` | 用户偏好 | 0.6 |
| `USER_CORRECTION` | 用户纠正（最高） | 1.0 |
| `TASK_HISTORY` | 任务历史 | 0.4 |
| `TASK_PATTERN` | 任务模式 | 0.6 |

### 8.3 上下文注入

`MemoryManager.get_relevant_context()` 在每次推理前被调用：
1. 从任务描述中提取联系人姓名（正则匹配）
2. 查找该联系人的 App 使用频率
3. 生成频率推荐（"使用QQ 5次"）
4. 追加用户偏好和历史任务提示
5. 格式化后注入 system prompt 或 user message

### 8.4 自动学习

`add_step()` 在每步执行后提取信息：
- 从任务描述中识别 App 名称 → 更新 `APP_USAGE`
- 识别联系人 → 更新 `CONTACT` 和 `CONTACT_APP_BINDING`
- 识别操作模式 → 更新 `TASK_PATTERN`

---

## 9. 执行追踪系统（Tracer）

`GUITracer` 记录完整任务执行轨迹，用于复现、调试和数据集构建：

```
<trace_dir>/
└── <episode_id>/
    ├── episode.json       # 任务元数据 + 每步记录
    └── images/
        ├── step0.png     # 每步的截图
        ├── step1.png
        └── ...
```

`episode.json` 格式：
```json
{
  "task_name": "打开微信",
  "timestamp": "2026-04-22T10:00:00",
  "episode": [
    {
      "step": 0,
      "model_output": "<think>...</think><answer>...",
      "action": {"action": "Tap", "element": [500, 300]},
      "finished": false
    }
  ]
}
```

---

## 10. 关键设计决策总结

### 10.1 适配器模式的选择

不使用统一的"标准格式"，而是每个模型用专属适配器。原因是：
- 不同模型的 system prompt 差异极大（工具定义格式、坐标系统）
- 输出格式各不相同（XML标签、JSON结构、自由文本）
- 消息格式也不同（有的追加、有的重构建）
- 强制统一会导致 adapter 中出现大量 `if model == X` 分支，失去适配器的价值

### 10.2 坐标系统的处理

坐标转换统一在 Action Handler 层处理，而非 Adapter 层：
- Handler 知道设备的实际屏幕分辨率
- Adapter 只负责从模型输出中提取原始坐标值
- 这样适配器保持"纯解析"职责，Handler 负责"纯执行"

### 10.3 内存系统的必要性

记忆系统不是框架必需的，但对用户体验至关重要：
- 手机场景中，用户经常说"发消息给张三"——Agent 需要知道"张三"是谁
- 不同用户使用不同的 App 完成同一任务（有人用微信有人用 QQ）
- 联系人与 App 的绑定关系是强个性化的
- 通过 `contact_app_binding` + 频率统计实现这一点

### 10.4 流式输出 vs. 缓冲

ModelClient 使用流式推理，但采用"打印 thinking + 缓冲 action"的策略：
- thinking 实时打印：用户可见 AI 推理过程，提升信任度
- action 缓冲：避免 action 部分被 thinking 打印打断，保证格式完整
- 区分 `_parse_response`（适配器解析 thinking/action）和 `parse_action`（handler 解析结构化动作）

---

## 11. 目录结构速查

```
phone_agent/
├── agent.py              # PhoneAgent 主类，执行循环
├── agent_ios.py          # iOS 专用 Agent
├── device_factory.py     # 设备抽象工厂
├── tracer.py             # 任务执行追踪器
│
├── model/
│   ├── adapters.py       # 5 个模型适配器 + 检测逻辑
│   └── client.py          # OpenAI-compatible HTTP 客户端
│
├── actions/
│   ├── handler.py         # 默认 AutoGLM 动作处理器
│   ├── handler_uitars.py  # UI-TARS 处理器
│   ├── handler_qwenvl.py  # QwenVL 处理器
│   ├── handler_maiui.py   # MAI-UI 处理器
│   ├── handler_guiowl.py  # GUI-Owl 处理器
│   └── handler_ios.py     # iOS 处理器
│
├── config/
│   ├── prompts*.py        # 各模型专用提示词模板
│   ├── apps*.py           # Android/HarmonyOS/iOS App 包名映射
│   ├── timing.py          # 统一时间配置
│   └── i18n.py            # 国际化字符串
│
├── adb/                   # Android ADB 后端
│   ├── connection.py      # ADB 连接管理
│   ├── device.py          # tap/swipe/type 实现
│   ├── screenshot.py      # 截图获取
│   └── input.py           # 输入法切换
│
├── hdc/                   # HarmonyOS HDC 后端（结构同 adb/）
├── xctest/                # iOS XCTEST 后端（WebDriverAgent）
│
└── memory/
    ├── memory_manager.py  # 记忆管理层（自动提取、上下文生成）
    └── memory_store.py    # 记忆持久化存储（JSON）
```
