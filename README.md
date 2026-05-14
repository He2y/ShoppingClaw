<div align="center">
  <h1>ShoppingClaw: Intelligent Shopping Phone Agent</h1>
  <p>
    <img src="https://img.shields.io/badge/python-≥3.10-blue" alt="Python">
    <img src="https://img.shields.io/badge/license-Apache%202.0-green" alt="License">
    <img src="https://img.shields.io/badge/platform-Android%20|%20HarmonyOS%20|%20iOS-orange" alt="Platform">
  </p>
</div>

> **Note**: This project is developed based on [ClawGUI-Agent](https://github.com/openclaw/openclaw), reusing its model adapters while redesigning the system architecture and optimizing the memory system to build a dedicated shopping agent.

**ShoppingClaw** is a VLM-driven phone agent specializing in **shopping scenarios**. It implements a closed-loop "screenshot → reasoning → action" control cycle with a **dual-core memory engine** and **structured session memory** to handle long-horizon shopping tasks reliably.

## 📑 Table of Contents

- [Key Innovations](#-key-innovations)
- [Architecture](#️-architecture)
- [Quick Start](#-quick-start)
- [Core Modules](#-core-modules)
  - [Dual-Core Memory Engine](#dual-core-memory-engine)
  - [Session Product Memory](#session-product-memory)
  - [GraphRAG Navigation](#graphrag-navigation)
  - [SpecGuard Safety](#specguard-safety)
- [Supported Models](#-supported-models)
- [License](#-license)

## ✨ Key Innovations

| Innovation | Description | Benefit |
|------------|-------------|---------|
| **Dual-Core Memory Engine** | FAISS semantic vectors + Neo4j spatial graph | Cross-session personalization without model training |
| **Session Product Memory** | Structured tracking of products, prices, specs | Prevents progress confusion in long tasks |
| **4-Layer Context Injection** | Summary → Detailed → Graph → Safety | On-demand memory supply based on uncertainty signals |
| **GraphRAG Navigation** | Three-layer retrieval for task matching | Shortcut execution for repeated tasks, zero VLM calls |
| **SpecGuard Safety Net** | Prompt + code dual-layer protection | Deterministically prevents wrong purchases |

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                      Entry Layer                                  │
│                   CLI (main.py)  │  WebUI (webui.py)              │
└─────────────────────────────┬────────────────────────────────────┘
                              │ task
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                      Agent Core (PhoneAgent)                      │
│                                                                  │
│  14-Phase Execution Loop:                                        │
│    Screenshot → Memory Lookup → HITL Clarify → Navigate Check    │
│    → Message Build → Context Injection → VLM Inference           │
│    → Action Parse → Execute → Memory Update → Completion         │
└──┬──────────────┬──────────────┬──────────────┬──────────────────┘
   │              │              │              │
   ▼              ▼              ▼              ▼
┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────┐
│  Model   │ │  Action  │ │  Device  │ │  Memory System   │
│ Adapters │ │ Handlers │ │  Factory │ │  (Dual-Core)     │
│  (5)     │ │  (13)    │ │          │ │                  │
└──────────┘ └──────────┘ └─────┬────┘ │ ┌──────────────┐ │
                                │       │ │ Semantic Core│ │
                    ┌───────────┼───┐   │ │ (FAISS 2048d)│ │
                    ▼           ▼   ▼   │ └──────────────┘ │
                  ADB         HDC  XCTEST│ ┌──────────────┐ │
                  (Android) (Harmony) (iOS)│ Spatial Core │ │
                                          │ (Neo4j +     │ │
                                          │  TaskIndex + │ │
                                          │  StateManager│ │
                                          │  +SessionMem)│ │
                                          └──────────────┘ │
                                          └──────────────────┘
```

## 🚀 Quick Start

### Requirements

- **Python**: ≥ 3.11
- **Package Manager**: [uv](https://github.com/astral-sh/uv) or conda

### Installation

```bash
# Clone repository
git clone https://github.com/He2y/ShoppingClaw.git
cd ShoppingClaw

# Create environment with uv
uv venv .venv --python 3.12
source .venv/bin/activate

# Install dependencies
uv pip install -e .
```

### Configuration

```bash
export PHONE_AGENT_BASE_URL="https://open.bigmodel.cn/api/paas/v4/"
export PHONE_AGENT_MODEL="autoglm-phone-9b"
export PHONE_AGENT_API_KEY="YOUR_API_KEY"
```

### Run

```bash
# CLI
python main.py "在京东搜索Nike跑鞋，找黑色42码，预算500以内"

# Web UI
python webui.py
```

## 🧩 Core Modules

### Dual-Core Memory Engine

A unified memory architecture combining two complementary storage systems:

| Core | Technology | Role |
|------|------------|------|
| **Semantic Core** | FAISS + embedding-3 (2048d) | Vector similarity search for user preferences, contacts, task history |
| **Spatial Core** | Neo4j Graph + TaskIndex | State transitions, task trajectories, navigation shortcuts |

**14 Memory Types** including: `USER_PREFERENCE`, `CONTACT`, `CONTACT_APP_BINDING`, `APP_USAGE`, `TASK_HISTORY`, `TASK_PATTERN`, `USER_CORRECTION`, `PRODUCT_PREFERENCE`, `UI_STATE`, `UI_TRANSITION`, etc.

**Key Features**:
- Automatic deduplication via FAISS similarity (≥0.85)
- Access-based importance boosting
- Cross-session persistence

### Session Product Memory

Addresses three critical failure modes in long shopping tasks (validated by UI-Copilot research):

| Failure Mode | Percentage | Cause | Our Solution |
|--------------|------------|-------|--------------|
| **Progress Confusion** | 43.8-66.7% | Agent forgets completed steps | StepSummary chain + per-step injection |
| **Memory Degradation** | 13.3-21.8% | Long context loses information | Structured SessionMemory + on-demand detailed injection |
| **Math Hallucination** | 6.7-10.9% | VLM unreliable at calculations | Structured product/price extraction + constraint tracking |

**Tracked Information**:
- Product details (name, price, specs, status)
- Shopping cart contents
- Completed step summaries
- User constraints (budget, brand preferences)

**On-Demand Triggering**: Detailed memory injection activates automatically when the agent shows uncertainty signals (e.g., "不确定", "忘记了", "之前看到") or detects stagnation (consecutive same actions).

### GraphRAG Navigation

Three-layer retrieval strategy for intelligent context matching:

```
┌─────────────────────────────────────────────────────────────┐
│ Layer 1: TaskIndex FAISS (Semantic Vector Search)           │
│                                                             │
│   Task text → 2048d embedding → FAISS similarity search     │
│                                                             │
│   similarity ≥ 0.85 → Navigate Mode                         │
│       Execute historical action directly, ZERO VLM call     │
│                                                             │
│   similarity ≥ 0.60 → Explore Mode                          │
│       Inject compressed trajectory context for VLM reference │
└─────────────────────────────────────────────────────────────┘
                              ↓ (fallback)
┌─────────────────────────────────────────────────────────────┐
│ Layer 2: Neo4j N-gram (Keyword Matching)                    │
│   Chinese N-gram tokenization + token overlap scoring       │
└─────────────────────────────────────────────────────────────┘
                              ↓ (supplement)
┌─────────────────────────────────────────────────────────────┐
│ Layer 3: MemoryStore UI_STATE (Page Features)               │
│   Search UI_STATE memories for additional page context      │
└─────────────────────────────────────────────────────────────┘
```

**Navigate Mode**: When a similar task (≥0.85 similarity) was executed before, the agent can skip VLM reasoning entirely and execute the first action from the historical trajectory — achieving **instant navigation with zero inference cost**.

### SpecGuard Safety

A three-layer protection system that **deterministically** prevents erroneous purchase operations:

| Layer | Mechanism | When Active |
|-------|-----------|-------------|
| **Layer 1** | System Prompt Rules | Always |
| **Layer 2** | Dynamic Safety Hints | Shopping app + spec page detected |
| **Layer 3** | Code Safety Net | Post-inference override |

**How It Works**:

1. **Prompt Layer**: Core rules in system prompt — "If any parameter is unspecified, the only valid action is Interact (ask user)"

2. **Dynamic Injection**: When detecting shopping app + spec selection page, inject mandatory Interact prompt

3. **Code Override**: Post-inference check — if thinking contains spec keywords (颜色/尺码/容量) AND purchase intent (立即购买/加入购物车), but action is NOT Interact → **force override to Interact** with contextual question

This ensures the agent **never makes purchase decisions** on behalf of the user when multiple options exist.

## 🔧 Supported Models

| Model | Adapter | Provider |
|-------|---------|----------|
| **AutoGLM-Phone-9B** | `autoglm` | Zhipu AI |
| **UI-TARS-1.5-7B** | `uitars` | ByteDance |
| **Qwen2.5-VL / Qwen3-VL** | `qwenvl` | Alibaba |
| **MAI-UI-2B/8B** | `maiui` | Alibaba |
| **GUI-Owl-1.5** | `guiowl` | mPLUG |

All models connect via **OpenAI-compatible API** — deploy locally with vLLM/SGLang or use cloud services.

## 📁 Directory Structure

```
ShoppingClaw/
├── main.py                      # CLI entry point
├── webui.py                     # Gradio Web UI
├── phone_agent/
│   ├── agent.py                 # Agent core (14-phase loop)
│   ├── clarify.py               # HITL task clarification
│   ├── tracer.py                # Execution trace recording
│   ├── device_factory.py        # Cross-platform device factory
│   ├── config/                  # Prompts & configuration
│   ├── model/                   # Model adapters (from ClawGUI-Agent)
│   ├── adb/                     # Android device control
│   ├── hdc/                     # HarmonyOS device control
│   ├── xctest/                  # iOS device control
│   ├── actions/                 # Action handlers (13 types)
│   └── memory/
│       ├── memory_manager.py    # Unified memory API (orchestrator)
│       ├── memory_store.py      # FAISS semantic core
│       ├── graph_store.py       # Neo4j spatial core
│       ├── session_memory.py    # Session product memory
│       ├── state_manager.py     # Unified state tracking
│       └── task_index.py        # Task FAISS index
├── memory_db/                   # Persistent memory storage
└── references/                  # Research papers
```

## 📄 License

Apache License 2.0

---

**Citation**: If you use this project in your research, please cite both ShoppingClaw and ClawGUI-Agent.
