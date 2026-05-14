<div align="center">
  <h1>ShoppingClaw: Intelligent Shopping Phone Agent</h1>
  <p>
    <img src="https://img.shields.io/badge/python-≥3.10-blue" alt="Python">
    <img src="https://img.shields.io/badge/license-Apache%202.0-green" alt="License">
    <img src="https://img.shields.io/badge/platform-Android%20|%20HarmonyOS%20|%20iOS-orange" alt="Platform">
  </p>
</div>

> **Note**: This project is developed based on [ClawGUI-Agent](https://github.com/openclaw/openclaw), reusing its model adapters while redesigning the system architecture and optimizing the memory system to build a dedicated shopping agent.

**ShoppingClaw** is a VLM-driven phone agent specializing in **shopping scenarios**. It implements a closed-loop "screenshot → reasoning → action" control cycle with a **dual-core memory engine** (FAISS semantic + Neo4j spatial) and **structured session memory** to handle long-horizon shopping tasks reliably.

## 📑 Table of Contents

- [Key Features](#-key-features)
- [Architecture](#️-architecture)
- [Quick Start](#-quick-start)
- [Memory System](#-memory-system)
- [SpecGuard Safety](#-specguard-safety)
- [Supported Models](#-supported-models)
- [License](#-license)

## ✨ Key Features

- **Dual-Core Memory Engine** — FAISS semantic vector store + Neo4j spatial graph store with unified state management, enabling cross-session personalization without model training
- **Session Product Memory** — Structured tracking of products (name/price/specs), progress summaries, and stagnation detection to prevent progress confusion in long shopping tasks
- **GraphRAG Navigation** — Three-layer retrieval (TaskIndex FAISS → Neo4j N-gram → MemoryStore FAISS) enabling shortcut execution for repeated tasks
- **SpecGuard Safety Net** — Prompt + code dual-layer protection that deterministically prevents erroneous purchase operations
- **Multi-Model Support** — Compatible with AutoGLM, Qwen VL, UI-TARS, MAI-UI, GUI-Owl via OpenAI-compatible API

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                      Entry Layer                                  │
│                   CLI (main.py)  │  WebUI (webui.py)              │
└─────────────────────────────┬────────────────────────────────────┘
                              │ task
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                      Agent Core                                   │
│                         PhoneAgent                                │
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
│ Adapters │ │ Handlers │ │  Factory │ │  (Dual-Core)     │
└──────────┘ └──────────┘ └─────┬────┘ │ ┌──────────────┐ │
                                │       │ │ Semantic Core│ │
                    ┌───────────┼───┐   │ │ (FAISS 2048d)│ │
                    ▼           ▼   ▼   │ └──────────────┘ │
                  ADB         HDC  XCTEST│ ┌──────────────┐ │
                  (Android) (Harmony) (iOS)│ Spatial Core │ │
                                          │ (Neo4j Graph)│ │
                                          │ +StateManager│ │
                                          │ +SessionMemory│ │
                                          │ +TaskIndex   │ │
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

# Create environment
uv venv .venv --python 3.12
source .venv/bin/activate

# Install dependencies
uv pip install -e .
```

### Configuration

Set environment variables:

```bash
export PHONE_AGENT_BASE_URL="https://open.bigmodel.cn/api/paas/v4/"
export PHONE_AGENT_MODEL="autoglm-phone-9b"
export PHONE_AGENT_API_KEY="YOUR_API_KEY"
export PHONE_AGENT_DEVICE_ID=""  # Auto-detect if empty
```

### Run

```bash
# CLI
python main.py "在京东搜索Nike跑鞋，找黑色42码，预算500以内"

# Web UI
python webui.py
```

## 🧠 Memory System

### Dual-Core Memory

| Core | Technology | Purpose |
|------|------------|---------|
| **Semantic Core** | FAISS + embedding-3 (2048d) | Vector similarity search |
| **Spatial Core** | Neo4j Graph + TaskIndex | State transitions, task trajectories |

### Session Product Memory

Addresses three failure modes in long shopping tasks:

| Failure Mode | Cause | Solution |
|--------------|-------|----------|
| **Progress Confusion** | Agent forgets completed steps | StepSummary chain + per-step injection |
| **Memory Degradation** | Long context loses information | Structured SessionMemory + on-demand injection |
| **Math Hallucination** | VLM unreliable at calculation | Structured price extraction + constraint tracking |

### GraphRAG Retrieval

```
Layer 1: TaskIndex FAISS
  similarity ≥ 0.85 → Navigate mode (skip VLM)
  similarity ≥ 0.60 → Explore mode (inject context)

Layer 2: Neo4j N-gram Fallback
  Token overlap scoring for keyword matching

Layer 3: MemoryStore UI_STATE Fallback
  Page feature supplement
```

## 🛡️ SpecGuard Safety

Three-layer protection for shopping scenarios:

| Layer | Mechanism | Trigger |
|-------|-----------|---------|
| **Layer 1** | System Prompt | Always active |
| **Layer 2** | Dynamic Injection | Shopping app + spec page |
| **Layer 3** | Code Safety Net | Post-inference override |

The code safety net prevents the agent from making purchase decisions when multiple options (color, size, etc.) are available without user confirmation.

## 🔧 Supported Models

| Model | Adapter | Coordinate Space |
|-------|---------|-----------------|
| **AutoGLM-Phone-9B** | `autoglm` | [0, 1000] |
| **UI-TARS** | `uitars` | Absolute pixels |
| **Qwen-VL** | `qwenvl` | [0, 999] |
| **MAI-UI** | `maiui` | [0, 999] |
| **GUI-Owl** | `guiowl` | [0, 999] |

## 📁 Directory Structure

```
ShoppingClaw/
├── main.py                      # CLI entry
├── webui.py                     # Web UI
├── phone_agent/
│   ├── agent.py                 # Agent core (14-phase loop)
│   ├── clarify.py               # HITL clarification
│   ├── device_factory.py        # Device abstraction
│   ├── model/                   # Model adapters (reused from ClawGUI-Agent)
│   ├── adb/                     # Android control
│   ├── hdc/                     # HarmonyOS control
│   ├── xctest/                  # iOS control
│   ├── actions/                 # Action handlers
│   └── memory/
│       ├── memory_manager.py    # Unified memory API
│       ├── memory_store.py      # FAISS semantic core
│       ├── graph_store.py       # Neo4j spatial core
│       ├── session_memory.py    # Session product memory
│       ├── state_manager.py     # State tracking
│       └── task_index.py        # Task FAISS index
└── memory_db/                   # Persistent storage
```

## 📄 License

Apache License 2.0
