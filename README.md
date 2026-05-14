<div align="center">
  <img src="assets/clawgui-agent-text.png" alt="ClawGUI-Agent Text" height="40">
</div>
<div align="center">
  <table style="border: none; border-collapse: collapse;">
    <tr>
      <td style="border: none; padding: 0;"><img src="assets/clawgui-agent-banner.png" alt="ClawGUI-Agent Banner" height="60" style="vertical-align: middle;"></td>
      <td style="border: none; padding: 0 0 0 12px; vertical-align: middle;"><h1 style="margin: 0;">ClawGUI-Agent: Personal Phone GUI Assistant</h1></td>
    </tr>
  </table>
  <p>
    <img src="https://img.shields.io/badge/python-≥3.10-blue" alt="Python">
    <img src="https://img.shields.io/badge/license-Apache%202.0-green" alt="License">
    <img src="https://img.shields.io/badge/platform-Android%20|%20HarmonyOS%20|%20iOS-orange" alt="Platform">
  </p>
</div>

[English](README.md) | [中文](README_CN.md)

**ClawGUI-Agent** is a VLM-driven GUI phone automation framework specializing in **long-horizon shopping scenarios**. Built on [OpenClaw](https://github.com/openclaw/openclaw) and powered by [nanobot](https://github.com/HKUDS/nanobot), it implements a closed-loop "screenshot → reasoning → action" control cycle with a **dual-core memory engine** (FAISS semantic + Neo4j spatial) for cross-session personalization.

## 📑 Table of Contents

- [Key Features](#-key-features)
- [Architecture](#️-architecture)
- [How the Agent Works](#-how-the-agent-works)
- [Quick Start](#-quick-start)
  - [Requirements](#requirements)
  - [1. Installation](#1-installation)
  - [2. Initialize and Edit Configuration](#2-initialize-and-edit-configuration)
  - [3. Connect Android Device](#3-connect-android-device)
  - [4. Configure Chat Platforms (optional)](#4-configure-chat-platforms-optional)
- [Run](#-run)
- [Memory System](#-memory-system)
  - [Dual-Core Memory Engine](#dual-core-memory-engine)
  - [Session Product Memory](#session-product-memory)
  - [GraphRAG Retrieval](#graphrag-retrieval)
- [SpecGuard Safety Mechanism](#-specguard-safety-mechanism)
- [Supported GUI Models](#-supported-gui-models)
- [Web UI](#-web-ui)
- [Directory Structure](#-directory-structure)
- [License](#-license)

## ✨ Key Features

- **Dual-Core Memory Engine** — FAISS semantic vector store + Neo4j spatial graph store with unified state management, enabling cross-session personalization without model training
- **Session Product Memory** — Structured tracking of products (name/price/specs), progress summaries, and stagnation detection to prevent progress confusion and memory degradation in long tasks
- **Multi-Layer Context Injection** — 4-layer injection strategy: progress summary → detailed memory → graph semantics → safety hints, with on-demand triggering based on uncertainty signals
- **GraphRAG Navigation** — Three-layer retrieval (TaskIndex FAISS → Neo4j N-gram → MemoryStore FAISS) enabling shortcut execution for repeated tasks
- **SpecGuard Safety Net** — Prompt + code dual-layer protection that deterministically prevents erroneous purchase operations in shopping scenarios
- **nanobot Integration** — Remotely control phones from 12+ chat platforms including Feishu / DingTalk / Telegram / Discord / Slack / QQ
- **Multi-Model Support** — Compatible with AutoGLM, Qwen VL, UI-TARS, MAI-UI, GUI-Owl and more VLMs via OpenAI-compatible API

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                      Entry Layer (入口层)                         │
│                   CLI (main.py)  │  WebUI (webui.py)              │
└─────────────────────────────┬────────────────────────────────────┘
                              │ task
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                      Agent Core (代理核心)                        │
│                         PhoneAgent                               │
│                                                                  │
│  run(task): 14-phase execution loop                              │
│    ① Screenshot  ② Memory Lookup  ②.5 Compression               │
│    ③ HITL Clarify  ④ Navigate Check  ⑤ Message Build            │
│    ⑥ 4-Layer Context Injection  ⑦ VLM Inference                 │
│    ⑧ Action Parse + SpecGuard  ⑨ Execute  ⑩ Memory Update       │
│    ⑪ Interact Capture  ⑫ Completion  ⑬ Trace  ⑭ Return         │
└──┬──────────────┬──────────────┬──────────────┬──────────────────┘
   │              │              │              │
   ▼              ▼              ▼              ▼
┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────┐
│  Model   │ │  Action  │ │  Device  │ │  Memory System   │
│ Adapters │ │ Handlers │ │  Factory │ │ (Dual-Core)      │
│ (5)      │ │ (6)      │ │          │ │                  │
└──────────┘ └──────────┘ └─────┬────┘ │ ┌──────────────┐ │
                                │       │ │ Semantic Core│ │
                    ┌───────────┼───┐   │ │ (FAISS 2048d)│ │
                    ▼           ▼   ▼   │ │ embedding-3  │ │
                  ADB         HDC  XCTEST│ └──────────────┘ │
                  (Android) (Harmony) (iOS)│ ┌──────────────┐ │
                                          │ │ Spatial Core │ │
                                          │ │ (Neo4j Graph)│ │
                                          │ │ + TaskIndex  │ │
                                          │ │ +StateManager│ │
                                          │ │ +SessionMemory│ │
                                          │ └──────────────┘ │
                                          └──────────────────┘
```

## 🔄 How the Agent Works

The `PhoneAgent.run()` execution loop consists of 14 phases:

| Phase | Description |
|-------|-------------|
| ① Screenshot | Capture screen via ADB/HDC/XCTest |
| ② Memory Lookup | GraphRAG three-layer matching for task context |
| ②.5 Compression | VLM-based history compression every 5 steps |
| ③ HITL Clarify | Detect ambiguity and ask user for clarification |
| ④ Navigate Check | Execute graph shortcut if confidence ≥ 0.8 |
| ⑤ Message Build | Construct messages based on model adapter |
| ⑥ Context Injection | 4-layer injection (summary → detailed → graph → safety) |
| ⑦ VLM Inference | Stream thinking + action from GUI model |
| ⑧ Action Parse + SpecGuard | Parse action with safety override |
| ⑨ Execute | Send action to device backend |
| ⑩ Memory Update | Extract entities, update SessionMemory |
| ⑪ Interact Capture | Store user replies for context |
| ⑫ Completion | Check for finish/answer action |
| ⑬ Trace | Record step with session snapshot |
| ⑭ Return | Yield StepResult |

## 🚀 Quick Start

### Requirements

- **Python**: ≥ 3.11
- **Package Manager**: [uv](https://github.com/astral-sh/uv) (recommended) or conda + pip

### 1. Installation

#### Option A: uv (recommended)

```bash
cd clawgui-agent
uv venv .venv --python 3.12
source .venv/bin/activate
uv pip install -e .
uv pip install -e nanobot/
```

#### Option B: conda + pip

```bash
cd clawgui-agent
conda create -n opengui python=3.12 -y
conda activate opengui
pip install -e .
pip install -e nanobot/
```

### 2. Initialize and Edit Configuration

```bash
nanobot onboard
```

Edit `~/.nanobot/config.json`:

```json
{
  "agents": {
    "defaults": {
      "workspace": "/path/to/ClawGUI",
      "model": "glm-5",
      "provider": "zhipu",
      "maxTokens": 8192,
      "temperature": 0.1
    }
  },
  "providers": {
    "zhipu": {
      "apiKey": "YOUR_ZHIPU_API_KEY",
      "apiBase": "https://open.bigmodel.cn/api/paas/v4/"
    }
  },
  "tools": {
    "gui": {
      "enable": true,
      "deviceType": "adb",
      "deviceId": null,
      "maxSteps": 50,
      "useExternalModel": true,
      "guiBaseUrl": "https://openrouter.ai/api/v1",
      "guiApiKey": "YOUR_OPENROUTER_API_KEY",
      "guiModelName": "autoglm-phone",
      "promptTemplateLang": "en",
      "promptTemplateStyle": "autoglm"
    }
  }
}
```

### 3. Connect Android Device

1. **Enable Developer Mode**: Settings > About Phone > Build Number (tap 10x)
2. **Enable USB Debugging**: Settings > Developer Options > USB Debugging
3. **Verify connection**:

```bash
adb devices
# Expected: <device_id>   device
```

### 4. Configure Chat Platforms (optional)

Enable platforms in `channels` within `config.json`:

```json
"channels": {
  "feishu": {
    "enabled": true,
    "appId": "YOUR_APP_ID",
    "appSecret": "YOUR_APP_SECRET"
  }
}
```

## 🚀 Run

### Control Phone via nanobot Chat

```bash
nanobot gateway
```

Then send messages on configured chat platforms:

```
Open WeChat, send message to Zhang San
```

### CLI Direct Control

```bash
python main.py \
  --model autoglm-phone \
  --max-steps 100 \
  "在京东搜索Nike跑鞋，找黑色42码，预算500以内"
```

## 🧠 Memory System

### Dual-Core Memory Engine

The framework features a dual-core memory architecture:

| Core | Technology | Purpose |
|------|------------|---------|
| **Semantic Core** | FAISS IndexFlatIP + embedding-3 (2048d) | Vector similarity search for memories |
| **Spatial Core** | Neo4j Graph + TaskIndex | State transitions, task trajectories |

**Memory Types (14 types)**:

| Type | Importance | Description |
|------|------------|-------------|
| USER_PREFERENCE | 0.6 | User settings and preferences |
| CONTACT | 0.7 | Contact information |
| CONTACT_APP_BINDING | 0.8 | Contact → App binding (frequency-weighted) |
| APP_USAGE | 0.5 | App usage records |
| TASK_HISTORY | 0.4 | Task execution history |
| TASK_PATTERN | 0.6 | Task patterns and workflows |
| USER_CORRECTION | 1.0 | User corrections (highest priority) |
| PRODUCT_PREFERENCE | 0.5 | Product category preferences |
| UI_STATE | 0.4 | UI state features |
| UI_TRANSITION | 0.4 | UI state transitions |

### Session Product Memory

Addresses three failure modes in long-horizon tasks (validated by UI-Copilot):

| Failure Mode | Cause | Solution |
|--------------|-------|----------|
| **Progress Confusion** (43.8-66.7%) | Agent forgets completed steps | StepSummary chain + per-step injection |
| **Memory Degradation** (13.3-21.8%) | Long context loses information | Structured SessionMemory + on-demand detailed injection |
| **Math Hallucination** (6.7-10.9%) | VLM unreliable at numerical calculation | Structured product price extraction + constraint tracking |

**Data Model**:

```python
@dataclass
class ProductInfo:
    name: str                    # Product name
    price: float | None          # Price
    specs: dict[str, str]        # {"color": "black", "size": "42"}
    status: str                  # "viewed" | "added_to_cart" | "compared"
    first_seen_step: int         # Step number when first seen

@dataclass
class SessionMemory:
    task: str                    # Current task description
    platform: str                # Shopping platform (京东/淘宝/...)
    viewed_products: list[ProductInfo]
    cart_items: list[ProductInfo]
    completed_steps: list[StepSummary]
    constraints: dict[str, str]  # Budget/brand constraints
```

### GraphRAG Retrieval

Three-layer matching strategy for context retrieval:

```
Layer 1: TaskIndex FAISS (2048d)
  similarity ≥ 0.85 → Navigate mode (skip VLM, execute shortcut)
  similarity ≥ 0.60 → Explore mode (inject trajectory context)

Layer 2: Neo4j N-gram Fallback
  Chinese N-gram tokenization + token overlap scoring

Layer 3: MemoryStore UI_STATE Fallback
  Search UI_STATE memories for page features
```

## 🛡️ SpecGuard Safety Mechanism

Prevents erroneous purchase operations in shopping scenarios with three-layer protection:

| Layer | Mechanism | Trigger |
|-------|-----------|---------|
| **Layer 1** | System Prompt | Always active |
| **Layer 2** | Dynamic Injection | Shopping app + spec page detected |
| **Layer 3** | Code Safety Net | Post-inference override |

**Code Safety Net Logic**:

```python
def _spec_guard_check(action, thinking, current_app):
    if current_app not in SHOPPING_APPS:
        return None  # Allow
    if action.type == "Interact":
        return None  # Allow
    if has_spec_keywords(thinking) and has_purchase_intent(thinking):
        return Interact("Multiple options available, which do you prefer?")
    return None
```

## 🔧 Supported GUI Models

| Model | Adapter | Coordinate Space | Provider |
|-------|---------|-----------------|----------|
| **AutoGLM-Phone-9B** | `autoglm` | [0, 1000] normalized | Zhipu AI |
| **Doubao-1.5-UI-TARS** | `uitars` | Absolute pixels (smart_resize) | ByteDance |
| **Qwen2.5-VL / Qwen3-VL** | `qwenvl` | [0, 999] | Alibaba Cloud |
| **MAI-UI** | `maiui` | [0, 999] | Alibaba Cloud |
| **GUI-Owl** | `guiowl` | [0, 999] | mPLUG |

All models connect via **OpenAI-compatible API** and can be deployed locally with vLLM/SGLang.

## 🖥️ Web UI

```bash
python webui.py
```

Opens at `http://localhost:7860`:

- **Device Management**: Connect/disconnect, view status
- **Task Execution**: Real-time screenshots and AI reasoning
- **Manual Takeover**: Switch to manual control for CAPTCHAs
- **Memory Management**: View/edit/clear memory data

## 📁 Directory Structure

```
ClawGUI-Agent/
├── main.py                      # CLI entry point
├── webui.py                     # Gradio Web UI
├── phone_agent/
│   ├── agent.py                 # PhoneAgent (998 lines, 14-phase loop)
│   ├── clarify.py               # HITL clarification agent
│   ├── tracer.py                # Episode recording
│   ├── device_factory.py        # Device abstraction (ADB/HDC/XCTest)
│   ├── config/                  # Prompts & configuration
│   ├── model/                   # Model adapters (5 VLM adapters)
│   ├── adb/                     # Android device control
│   ├── hdc/                     # HarmonyOS device control
│   ├── xctest/                  # iOS device control
│   ├── actions/                 # Action handlers (13 action types)
│   └── memory/
│       ├── memory_manager.py    # Unified memory API (1464 lines)
│       ├── memory_store.py      # FAISS semantic core (666 lines)
│       ├── graph_store.py       # Neo4j spatial core (433 lines)
│       ├── session_memory.py    # Session product memory (343 lines)
│       ├── state_manager.py     # Unified state tracking
│       └── task_index.py        # Task FAISS index
├── nanobot/                     # Chat platform gateway
└── examples/                    # Usage examples
```

## 📄 License

This project is licensed under the [Apache License 2.0](LICENSE). The nanobot subproject is licensed under the [MIT License](nanobot/LICENSE).
