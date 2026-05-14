<div align="center">
  <h1>ShoppingClaw: Intelligent Shopping Phone Agent</h1>
  <p>
    <img src="https://img.shields.io/badge/python-≥3.10-blue" alt="Python">
    <img src="https://img.shields.io/badge/license-Apache%202.0-green" alt="License">
    <img src="https://img.shields.io/badge/platform-Android%20|%20HarmonyOS%20|%20iOS-orange" alt="Platform">
  </p>
</div>

> **Note**: This project is developed based on [ClawGUI-Agent](https://github.com/openclaw/openclaw), reusing its model adapters while redesigning the system architecture and optimizing the memory system to build a dedicated shopping agent.

**ShoppingClaw** is a VLM-driven phone agent specializing in **shopping scenarios**. It implements a closed-loop "screenshot → reasoning → action" control cycle powered by **Memory Decoupling** (inspired by [UI-Copilot](https://arxiv.org/abs/2604.13822)) — externalizing detailed observations and keeping the VLM context lightweight, with on-demand retrieval triggered by agent confusion signals.

## 📑 Table of Contents

- [Key Innovations](#-key-innovations)
- [Architecture](#️-architecture)
- [Quick Start](#-quick-start)
- [Core Modules](#-core-modules)
  - [Memory Decoupling Engine](#memory-decoupling-engine)
  - [KnowledgeBase & RetrievalGateway](#knowledgebase--retrievalgateway)
  - [GraphRAG Navigation](#graphrag-navigation)
  - [SpecGuard Safety](#specguard-safety)
- [Supported Models](#-supported-models)
- [License](#-license)

## ✨ Key Innovations

| Innovation | Description | Why It Matters |
|------------|-------------|----------------|
| **Memory Decoupling** | Detailed observations stored externally; only progress summaries in VLM context | Reduces context size by ~70%, prevents progress confusion |
| **On-Demand Retrieval** | Agent memory queries triggered by confusion signals, not pushed every step | Agent gets targeted help only when actually lost |
| **Dual-Core Memory Engine** | FAISS semantic vectors + Neo4j spatial graph | Cross-session personalization without model training |
| **GraphRAG Navigation** | Three-layer retrieval for task matching | Shortcut execution for repeated tasks, zero VLM calls |
| **SpecGuard Safety Net** | Prompt + code dual-layer protection | Deterministically prevents wrong purchases |

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                      Entry Layer                                   │
│                   CLI (main.py)  │  WebUI (webui.py)              │
└─────────────────────────────┬────────────────────────────────────┘
                              │ task
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                      Agent Core (PhoneAgent)                       │
│                                                                    │
│  Execution Loop:                                                   │
│    Screenshot → GraphRAG Lookup → HITL Clarify                    │
│    → Message Build → MINIMAL Context Injection → VLM Inference   │
│    → Action Parse + SpecGuard → Execute → Memory Update           │
└──┬──────────────┬──────────────┬──────────────┬───────────────────┘
   │              │              │              │
   ▼              ▼              ▼              ▼
┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────────────────────┐
│  Model   │ │  Action  │ │  Device  │ │  Memory System             │
│ Adapters │ │ Handlers │ │  Factory │ │  ┌───────────────────────┐ │
│  (5)     │ │  (13)    │ │          │ │  │ KnowledgeBase (外置)  │ │
└──────────┘ └──────────┘ └─────┬────┘ │  │ - ProductObservations │ │
                                │       │  │ - StepRecords         │ │
                    ┌───────────┼───┐   │  │ - ReasoningArchive    │ │
                    ▼           ▼   ▼   │  └───────┬───────────────┘ │
                  ADB         HDC  XCTEST│         │                  │
                  (Android) (Harmony) (iOS)│ ┌──────▼───────────────┐ │
                                          │ │ RetrievalGateway     │ │
                                          │ │ (On-Demand)          │ │
                                          │ │   5 signal types:    │ │
                                          │ │   uncertainty        │ │
                                          │ │   comparison         │ │
                                          │ │   calculation        │ │
                                          │ │   product_lookup     │ │
                                          │ │   stagnation         │ │
                                          │ └──────┬───────────────┘ │
                                          │        │                  │
                                          │ ┌──────▼───────────────┐ │
                                          │ │ Dual-Core Backend    │ │
                                          │ │ FAISS 2048d + Neo4j  │ │
                                          │ └──────────────────────┘ │
                                          └──────────────────────────┘

    VLM CONTEXT (minimal)              KNOWLEDGE BASE (external)
    ┌─────────────────────┐           ┌──────────────────────────┐
    │ [进度] Step 5: ...   │           │ Products:                │
    │ [当前] Nike Air Max  │           │  - Air Max 899 (黑色)     │
    │                      │           │  - Air Force 1 799       │
    │  (on-demand only)    │  retrieve │ Steps:                   │
    │  [记忆检索] 已浏览:  │◄─────────│  Step 1: Launch 淘宝     │
    │  - Nike Air Max 899  │  trigger  │  Step 2: Type "Nike鞋"   │
    │  - Air Force 1 799   │           │  Step 3: Tap search...   │
    │                      │           │ Reasoning Archive:       │
    │  + screenshot        │           │  [step 1] "需要搜索..."  │
    └─────────────────────┘           │  [step 2] "输入关键词..." │
                                      └──────────────────────────┘
```

**Key insight**: The VLM context stays clean — only 1-2 lines of progress by default. Detailed product observations, complete reasoning chains, and full step histories live externally in KnowledgeBase. When the agent shows confusion (uncertainty keywords, stagnation, comparison intent), RetrievalGateway queries KnowledgeBase and injects only the relevant information. This is the opposite of the traditional "push everything every step" approach.

## 🚀 Quick Start

### Requirements

- **Python**: >= 3.11
- **Package Manager**: [uv](https://github.com/astral-sh/uv) or conda

### Installation

```bash
git clone https://github.com/He2y/ShoppingClaw.git
cd ShoppingClaw
uv venv .venv --python 3.12
source .venv/bin/activate
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

### Memory Decoupling Engine

The most significant architectural innovation, directly inspired by the UI-Copilot paper (Lu et al., 2026). In traditional GUI agents, the VLM context accumulates full reasoning traces, screenshots, and observations — causing context bloat that leads to memory degradation and progress confusion in long tasks (>15 steps).

**Before (traditional push-based)**:
```
Context = [System Prompt] + [Step1: full thinking + screenshot]
         + [Step2: full thinking + screenshot] + ...
         + [4-layer injection: summary + detailed + graph + safety]
         -> Context grows unboundedly, VLM loses track
```

**After (Memory Decoupling)**:
```
Context = [System Prompt] + [Progress: Step 5 | current: Nike Air Max]
         + [Screenshot]
         + [only IF triggered: RetrievalGateway result]
         -> Context stays minimal, ~80% smaller
```

Detailed observations are stored in **KnowledgeBase** and only retrieved on-demand when the agent signals confusion.

### KnowledgeBase & RetrievalGateway

**KnowledgeBase** is the external session knowledge store — equivalent to UI-Copilot's "K file":

| Data Type | Contents | Queryable By |
|-----------|----------|--------------|
| `ProductObservation` | Name, price, specs, status (viewed/cart/compared), step number | Keyword, status filter |
| `StepRecord` | Action type, target, truncated thinking, full thinking (archived) | Keywords in reasoning archive |
| `ProgressTracker` | Completed subtasks, remaining subtasks, current focus | Progress summary |
| `Constraints` | Budget, brand preference, platform | Constraint reminder |

**RetrievalGateway** monitors the agent's thinking for 5 signal types and queries KnowledgeBase only when triggered:

| Signal | Trigger Keywords | Retrieval Action |
|--------|-----------------|-----------------|
| **Uncertainty** | "不确定", "忘记了", "之前看到", "not sure" | Search reasoning archive + product list |
| **Comparison** | "对比", "哪个更便宜", "compare" | Generate price comparison table |
| **Calculation** | "总共", "合计", "total", "sum" | Return cart items with prices |
| **Product Lookup** | "价格是多少", "什么颜色", "那个商品" | Fuzzy product name match |
| **Stagnation** | Same action on same page >= 2x | Return recent step history |

**Cooldown**: Retrieval skips at least 3 steps between triggers to avoid context spam.

### GraphRAG Navigation

Three-layer retrieval for intelligent context matching:

```
Layer 1: TaskIndex FAISS (Semantic Vector Search)
  similarity >= 0.85 -> Navigate Mode (ZERO VLM call)
  similarity >= 0.60 -> Explore Mode (trajectory reference)
Layer 2: Neo4j N-gram (Keyword fallback)
Layer 3: MemoryStore UI_STATE (Page feature supplement)
```

### SpecGuard Safety

Three-layer protection that deterministically prevents wrong purchases:

| Layer | Mechanism | Trigger |
|-------|-----------|---------|
| Layer 1 | System Prompt Rules | Always |
| Layer 2 | Dynamic Safety Hints | Shopping app + spec page |
| Layer 3 | Code Safety Net | Post-inference override |

## 🔧 Supported Models

| Model | Adapter | Provider |
|-------|---------|----------|
| **AutoGLM-Phone-9B** | `autoglm` | Zhipu AI |
| **UI-TARS-1.5-7B** | `uitars` | ByteDance |
| **Qwen2.5-VL / Qwen3-VL** | `qwenvl` | Alibaba |
| **MAI-UI-2B/8B** | `maiui` | Alibaba |
| **GUI-Owl-1.5** | `guiowl` | mPLUG |

## 📁 Directory Structure

```
ShoppingClaw/
├── main.py                      # CLI entry point
├── webui.py                     # Gradio Web UI
├── phone_agent/
│   ├── agent.py                 # Agent core (execution loop)
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
│       ├── knowledge_base.py    # External session knowledge (NEW)
│       ├── retrieval_gateway.py # On-demand retrieval engine (NEW)
│       ├── memory_manager.py    # Unified memory orchestrator
│       ├── memory_store.py      # FAISS semantic core
│       ├── graph_store.py       # Neo4j spatial core
│       ├── session_memory.py    # Per-step product tracking
│       ├── state_manager.py     # Unified state tracking
│       └── task_index.py        # Task FAISS index
├── memory_db/                   # Persistent memory storage
└── references/                  # Research papers
```

## 📄 License

Apache License 2.0

---

**Citation**: If you use this project in your research, please cite both ShoppingClaw and ClawGUI-Agent.
