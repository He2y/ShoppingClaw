<div align="center">
  <h1>ShoppingClaw: Intelligent Shopping Phone Agent</h1>
  <p>
    <img src="https://img.shields.io/badge/python-≥3.10-blue" alt="Python">
    <img src="https://img.shields.io/badge/license-Apache%202.0-green" alt="License">
    <img src="https://img.shields.io/badge/platform-Android%20|%20HarmonyOS%20|%20iOS-orange" alt="Platform">
  </p>
</div>

> **Note**: This project is developed based on [ClawGUI-Agent](https://github.com/openclaw/openclaw), reusing its model adapters while redesigning the system architecture and memory system to build a dedicated shopping agent.

**ShoppingClaw** is a VLM-driven phone agent specializing in **shopping scenarios**. It implements a closed-loop "screenshot → reasoning → action" control cycle, powered by **Unified Session State** and **Memory Decoupling** (inspired by [UI-Copilot](https://arxiv.org/abs/2604.13822)) — externalizing detailed observations and keeping the VLM context lightweight, with on-demand retrieval triggered by agent confusion signals.

## 📑 Table of Contents

- [Key Innovations](#-key-innovations)
- [Architecture](#-architecture)
- [Quick Start](#-quick-start)
- [Core Modules](#-core-modules)
  - [Unified Session State](#unified-session-state)
  - [Memory Decoupling Engine](#memory-decoupling-engine)
  - [Smart Retry & Error Handling](#smart-retry--error-handling)
  - [SpecGuard Safety](#specguard-safety)
  - [GraphRAG Navigation](#graphrag-navigation)
  - [Externalized Configuration](#externalized-configuration)
- [Supported Models](#-supported-models)
- [Directory Structure](#-directory-structure)
- [License](#-license)

## ✨ Key Innovations

| Innovation | Description | Why It Matters |
|------------|-------------|----------------|
| **Unified Session State** | Single source of truth replacing 3 fragmented components (SessionMemory + KnowledgeBase + StateManager) | Eliminates dual-write, reduces data redundancy by 50% |
| **Memory Decoupling** | Detailed observations stored externally; only progress summaries in VLM context | Reduces context size by ~70%, prevents progress confusion |
| **On-Demand Retrieval** | Agent memory queries triggered by 5 confusion signals, not pushed every step | Agent gets targeted help only when actually lost |
| **Smart Retry** | Exponential backoff for network errors, rate-limit cooldown, auth failure fast-fail | Recovers from transient failures without losing progress |
| **Dual-Core Memory Engine** | FAISS semantic vectors (2048d) + Neo4j spatial graph | Cross-session personalization without model training |
| **SpecGuard Self-Reflection** | Cross-references user's original task SKU against currently selected specs | Stops wrong purchases; distinguishes "already selected" from "needs asking" |
| **GraphRAG Navigation** | Three-layer retrieval: FAISS → Neo4j N-gram → MemoryStore | Shortcut execution for repeated tasks, zero VLM calls |
| **Externalized Config** | JSON config + code defaults fallback | Zero-code changes to add new shopping platforms |

## 🏗 Architecture

```
                       ┌─────────────────────────────┐
                       │    Entry: CLI / WebUI        │
                       └─────────────┬───────────────┘
                                     │ task
                                     ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        PhoneAgent Core                              │
│                                                                     │
│  Loop: Screenshot → GraphRAG → Clarify → MemoryDecouple             │
│         → VLM (with retry) → Parse + SpecGuard → Execute → Update  │
│                                                                     │
│  Config: ShoppingConfig (external JSON, code defaults fallback)     │
│  Errors: Category-based retry (recoverable / rate-limited / fatal)  │
└───┬───────────────┬──────────────┬──────────────┬───────────────────┘
    │               │              │              │
    ▼               ▼              ▼              ▼
┌────────┐  ┌───────────┐  ┌──────────┐  ┌───────────────────────────┐
│ Model  │  │  Action   │  │  Device  │  │    Memory System           │
│ Client │  │ Handlers  │  │ Factory  │  │                            │
│        │  │           │  │          │  │ MemoryManager (orchestrator)│
│ 5      │  │ 6         │  │ ADB      │  │  ┌───────────────────────┐ │
│ models │  │ handlers  │  │ HDC      │  │  │ RetrievalGateway      │ │
│        │  │           │  │ XCTEST   │  │  │ (5 signals + cooldown)│ │
│ retry: │  │ SpecGuard │  │          │  │  └───────────┬───────────┘ │
│ 3x+exp │  │ self-     │  │          │  │              │             │
│ backoff│  │ reflection│  │          │  │  ┌───────────▼───────────┐ │
└────────┘  └───────────┘  └──────────┘  │  │ UnifiedSessionState   │ │
                                          │  │ (single source)       │ │
                                          │  │ Products · Steps      │ │
                                          │  │ Reasoning · State IDs │ │
                                          │  └───────────┬───────────┘ │
                                          │              │             │
                                          │  ┌───────────▼───────────┐ │
                                          │  │  Dual-Core Backend    │ │
                                          │  │ FAISS 2048d + Neo4j   │ │
                                          │  │ (graceful degradation)│ │
                                          │  └───────────────────────┘ │
                                          └───────────────────────────┘
```

**Key insight**: The VLM context stays minimal — only progress summaries (1-2 lines) by default. Detailed product observations, reasoning chains, and step histories live in **UnifiedSessionState** (single source of truth, no dual-write). When the agent shows confusion, **RetrievalGateway** queries the state and injects only relevant information. Neo4j **GraphStore** degrades gracefully when unavailable — memory system functions without it.

### v3.0 Refactoring Summary

| Phase | Change | Impact |
|-------|--------|--------|
| **Phase 1** | Merged SessionMemory + KnowledgeBase + StateManager → UnifiedSessionState (559 lines) | -936 lines deleted, single-write pattern |
| **Phase 2** | Smart retry with exponential backoff + GraphStore graceful degradation | Network resilience, survives Neo4j downtime |
| **Phase 3** | Externalized config: `config/shopping.json` + `ShoppingConfig` dataclass | Zero-code platform/app additions |
| **Phase 4** | VLM-guided structured product extraction replacing heavy regex | Cleaner, prompt-driven, cross-model compatible |

See [DEVELOPMENT_DOCUMENTATION.md](DEVELOPMENT_DOCUMENTATION.md) for full architecture details.

## 🚀 Quick Start

### Requirements

- **Python**: >= 3.10
- **Package Manager**: [uv](https://github.com/astral-sh/uv) or conda
- **Optional**: [Neo4j](https://neo4j.com/) (spatial memory — system degrades gracefully without it)

### Installation

```bash
git clone https://github.com/He2y/ShoppingClaw.git
cd ShoppingClaw

# Option 1: uv (推荐)
uv venv .venv --python 3.12
source .venv/bin/activate  # Windows: .venv\Scripts\activate
uv pip install -e .

# Option 2: conda
conda create -n shoppingclaw python=3.12
conda activate shoppingclaw
pip install -e .
```

### Configuration

Copy the example config and fill in your credentials:

```bash
cp .env.example .env
```

Edit `.env` with your settings:

```bash
# Required — VLM Model
PHONE_AGENT_BASE_URL=https://open.bigmodel.cn/api/paas/v4
PHONE_AGENT_MODEL=autoglm-phone
PHONE_AGENT_API_KEY=your_api_key_here

# Optional — customize behavior
PHONE_AGENT_MAX_STEPS=100        # max steps per task
PHONE_AGENT_MAX_TOKENS=4096      # VLM output token limit
PHONE_AGENT_LANG=cn              # cn / en
ENABLE_MEMORY=true               # set false to disable memory

# Optional — spatial memory graph
NEO4J_URI=bolt://localhost:7687
NEO4J_PASSWORD=your_password
```

All env vars are documented in [.env.example](.env.example).

### Quick Test

```bash
# Check system requirements
python main.py --check-system

# List connected devices
python main.py --list-devices

# Run a simple task
python main.py "打开淘宝"

# Run a shopping task
python main.py --max-steps 30 "去淘宝苹果官方旗舰店买一个iPhone 17 Pro Max，银色，512G"

# Web UI
python webui.py --port 7860
```

## 🧩 Core Modules

### Unified Session State

**The central v3.0 refactoring.** Replaces three previously fragmented components with a single data model:

```
BEFORE (v2.0):                            AFTER (v3.0):
agent.py                                  agent.py
  ├─→ SessionMemory (343 lines)             │
  ├─→ KnowledgeBase (492 lines)             └─→ UnifiedSessionState (559 lines)
  └─→ StateManager  (101 lines)                   single write, single source
     DUAL-WRITE per observation
```

| Old Component | Merged Into | Key Improvement |
|---------------|-------------|-----------------|
| `SessionMemory` | `UnifiedSessionState.products` + `._current_product` | ProductStatus enum replaces string comparisons |
| `KnowledgeBase` | `UnifiedSessionState.steps` + `._reasoning_archive` | No more dual-write synchronization |
| `StateManager` | `UnifiedSessionState._current_state_id` | State lifecycle inlined — one less object |

**Data model**: `phone_agent/memory/core/`
- `Product` — name, price, specs, status enum (VIEWED/COMPARED/ADDED_TO_CART/PURCHASED)
- `StepRecord` — action type, target, thinking (short for context, full for archive)
- `UnifiedSessionState` — `reset()`, `record_step()`, `record_product()`, `progress_summary()`, `current_focus()`, `retrieve_by_keywords()`, `is_stagnating()`, `should_compress()`, `detect_retrieval_intent()`

### Memory Decoupling Engine

Inspired by UI-Copilot (Lu et al., 2026). Traditional GUI agents push full reasoning traces into the VLM context, causing bloat and task confusion in long sessions.

**Before (push-based)**:
```
Context = [System] + [Step1: full thinking + screenshot]
        + [Step2: full thinking + screenshot] + ...
        + [4-layer injection] → grows unboundedly
```

**After (Memory Decoupling)**:
```
Context = [System] + [Progress: Step 5 | current: Nike Air Max | 已看3件]
        + [Screenshot]
        + [only IF triggered: RetrievalGateway result]
        → stays minimal, ~80% smaller
```

**5 Retrieval Signals** (RetrievalGateway):

| Signal | Triggers | Retrieval |
|--------|----------|-----------|
| **Uncertainty** | "不确定", "忘记了", "之前看到" | Reasoning archive + product list |
| **Comparison** | "对比", "哪个更便宜" | Price comparison table |
| **Calculation** | "总共", "合计" | Cart items with prices |
| **Product Lookup** | "价格是多少", "什么颜色" | Fuzzy product name match |
| **Stagnation** | Same action on same page ≥2x | Recent step history |

Cooldown: ≥3 steps between retrievals to avoid context spam.

### Smart Retry & Error Handling

**File**: `phone_agent/core/errors.py` + `agent.py`

| Error Type | Strategy | Max Retries |
|-----------|----------|-------------|
| Network / Timeout | Exponential backoff (1s → 2s → 4s) | 3 |
| Rate Limit | Wait 60s, retry | 3 |
| Auth Failure | Immediate termination | 0 |
| Unknown | Log and terminate | 0 |

**GraphStore**: Neo4j unavailable → `self.driver = None` → all methods return empty defaults. System continues with FAISS-only memory.

### SpecGuard Safety

Three-layer protection that prevents wrong purchases on spec-selection pages. **v3.0 adds self-reflection** — cross-referencing the user's original task against what's actually selected:

```
Layer 1: System Prompt — "If any parameter is unspecified → Interact only"

Layer 2: Pre-inference Hints — "Current page has multiple specs available"

Layer 3: Code Safety Net (self-reflection) —
  ┌─ _extract_specs_from_task(task) → {"颜色": "银色", "容量": "512G"}
  ├─ _is_spec_selected(thinking, "颜色", "银色") → check "银色.*已选中"
  ├─ All selected → ✅ PASS
  ├─ Missing → 🛑 Force Interact with specific prompt
  └─ User didn't specify → 🛑 Force Interact + ask
```

Terminal actions (`finish`/`terminate`/`answer`) are never intercepted.

### GraphRAG Navigation

Three-layer retrieval for intelligent context matching:

```
Layer 1: TaskIndex FAISS (Semantic Vector Search)
  similarity ≥ 0.85 → Navigate Mode (ZERO VLM call)
  similarity ≥ 0.60 → Explore Mode (trajectory reference injected)
Layer 2: Neo4j N-gram (Keyword fallback — Chinese n-gram token overlap)
Layer 3: MemoryStore UI_STATE (Page feature supplement)
```

### Externalized Configuration

New in v3.0. Edit `config/shopping.json` to add platforms/apps/keywords without touching code:

```json
{
  "apps": ["淘宝", "京东", "天猫", "拼多多", ...],
  "platforms": ["京东", "淘宝", "天猫", ...],
  "spec_keywords": ["规格", "颜色", "容量", "尺码", ...],
  "purchase_keywords": ["领券购买", "立即购买", "加入购物车", ...]
}
```

Missing or invalid config → falls back to built-in `ShoppingConfig.default()`.

## 🔧 Supported Models

| Model | Adapter | Provider | Context |
|-------|---------|----------|---------|
| **AutoGLM-Phone** | `autoglm` | Zhipu AI | 20K |
| **UI-TARS-1.5-7B** | `uitars` | ByteDance | — |
| **Qwen2.5-VL / Qwen3-VL** | `qwenvl` | Alibaba | — |
| **MAI-UI-2B/8B** | `maiui` | Alibaba | — |
| **GUI-Owl-1.5** | `guiowl` | mPLUG | — |

## 📁 Directory Structure

```
ShoppingClaw/
├── main.py                        # CLI entry point
├── webui.py                       # Gradio Web UI
├── .env.example                   # Environment config template
├── config/
│   └── shopping.json              # Externalized shopping config (NEW v3.0)
├── phone_agent/
│   ├── agent.py                   # Agent core (execution loop + retry + SpecGuard)
│   ├── clarify.py                 # HITL task clarification
│   ├── tracer.py                  # Execution trace recording
│   ├── device_factory.py          # Cross-platform device factory
│   ├── core/                      # (NEW v3.0)
│   │   ├── __init__.py
│   │   └── errors.py              # ErrorCategory + AgentError
│   ├── config/                    # Prompts & configuration
│   │   ├── shopping_config.py     # ShoppingConfig dataclass (NEW v3.0)
│   │   ├── prompts_zh.py          # Chinese system prompt (AutoGLM default)
│   │   ├── prompts_en.py          # English system prompt
│   │   ├── prompts_uitars.py      # UI-TARS model prompt
│   │   ├── prompts_qwenvl.py      # QwenVL model prompt
│   │   ├── prompts_maiui.py       # MAI-UI model prompt
│   │   └── prompts_guiowl.py      # GUI-Owl model prompt
│   ├── model/                     # Model adapters (5 models)
│   │   ├── client.py              # OpenAI-compatible streaming client
│   │   └── adapters.py            # Model-specific message builders
│   ├── adb/                       # Android device control
│   ├── hdc/                       # HarmonyOS device control
│   ├── xctest/                    # iOS device control
│   ├── actions/                   # Action handlers (6 handlers, 13 action types)
│   └── memory/
│       ├── memory_manager.py      # Memory orchestrator (single-write)
│       ├── memory_store.py        # FAISS semantic core (2048d)
│       ├── graph_store.py         # Neo4j spatial core (graceful degradation)
│       ├── retrieval_gateway.py   # On-demand retrieval engine (5 signals)
│       ├── embedding_client.py    # Embedding API client (embedding-3)
│       ├── task_index.py          # Task FAISS index
│       ├── offline_explorer.py    # Offline page explorer
│       └── core/                  # (NEW v3.0 — Unified State)
│           ├── __init__.py
│           ├── product.py         # Product dataclass + ProductStatus enum
│           ├── step_record.py     # StepRecord dataclass
│           └── unified_state.py   # UnifiedSessionState (559 lines)
├── memory_db/                     # Persistent memory storage
└── references/                    # Research papers
```

### Deleted in v3.0

| File | Reason |
|------|--------|
| `memory/session_memory.py` (343 lines) | Merged into `memory/core/unified_state.py` |
| `memory/knowledge_base.py` (492 lines) | Merged into `memory/core/unified_state.py` |
| `memory/state_manager.py` (101 lines) | Merged into `memory/core/unified_state.py` |

## 📄 License

Apache License 2.0

---

**Citation**: If you use this project in your research, please cite both ShoppingClaw and ClawGUI-Agent.

**Documentation**: See [DEVELOPMENT_DOCUMENTATION.md](DEVELOPMENT_DOCUMENTATION.md) for exhaustive architecture details, data flow diagrams, and academic ablation study designs.
