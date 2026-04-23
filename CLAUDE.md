# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**ClawGUI-Agent** is a GUI phone automation framework built on [OpenClaw](https://github.com/openclaw/openclaw) and [nanobot](https://github.com/HKUDS/nanobot). It provides two core capabilities:
- **GUI Phone Control**: Vision-Language Model-driven closed-loop "screenshot → reasoning → action" cycle for Android, HarmonyOS, and iOS
- **ClawGUI-Eval Integration**: Natural-language evaluation pipeline for GUI grounding benchmarks

## Commands

```bash
# Install dependencies (requires uv or conda)
uv pip install -e .
uv pip install -e nanobot/

# Phone CLI (Android default)
python main.py --model autoglm-phone-9b --max-steps 50 "Open WeChat, send message to 张三"

# iOS CLI
python main.py --device-type ios "Open Safari and search"

# Web UI (Gradio)
python webui.py --port 7860

# Device management
python main.py --list-devices
python main.py --connect 192.168.1.100:5555
python main.py --enable-tcpip 5555
python main.py --list-apps

# nanobot gateway (chat platform control)
nanobot gateway

# System checks
python main.py --check-system  # full system requirements check
```

## Architecture

### Execution Loop (`phone_agent/agent.py`)

Each task follows: Screenshot → Memory Retrieval → History Construction → VLM Call → Action Parsing → Coordinate Normalization → Action Execution → Trace Recording → Memory Update. Loop repeats until `terminate`/`answer` action or `max_steps` reached.

### Key Components

| Component | Location | Purpose |
|------------|----------|---------|
| `PhoneAgent` | `phone_agent/agent.py` | Main agent, screenshot→VLM→action loop |
| `IOSPhoneAgent` | `phone_agent/agent_ios.py` | iOS variant using XCTest |
| `StreamingAgent` | `webui.py` | Gradio-compatible streaming agent with memory injection |
| Model Adapters | `phone_agent/model/adapters.py` | 5 VLM adapters (autoglm, uitars, qwenvl, maiui, guiowl) |
| Device Backends | `phone_agent/{adb,hdc,xctest}/` | Platform-specific device control |
| Action Handlers | `phone_agent/actions/handler*.py` | Platform-specific action execution |
| Prompt Templates | `phone_agent/config/prompts*.py` | Model-specific system prompts |
| Memory System | `phone_agent/memory/` | Vector-store personalized memory |
| nanobot | `nanobot/` | Chat platform gateway, 12+ channel integrations |

### Coordinate Systems

Different models use different coordinate systems — adapters in `phone_agent/model/adapters.py` normalize them:
- **AutoGLM**: `[0, 1000]` normalized
- **UI-TARS**: Absolute pixels in `smart_resize` space
- **Qwen-VL**: Absolute pixels
- **MAI-UI**: `[0, 1000]` normalized
- **GUI-Owl**: Absolute pixels (unique: uses `coordinate` + `coordinate2` for swipe)

### nanobot Integration

nanobot runs as a separate package (`nanobot/`). The `gui-mobile` skill bridges nanobot's chat platform control with the phone agent. The `clawgui-eval` skill provides natural-language benchmark launching.

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `PHONE_AGENT_BASE_URL` | `http://localhost:8000/v1` | Model API endpoint |
| `PHONE_AGENT_MODEL` | `autoglm-phone-9b` | Model name |
| `PHONE_AGENT_API_KEY` | `EMPTY` | API key |
| `PHONE_AGENT_MAX_STEPS` | `100` | Max steps per task |
| `PHONE_AGENT_DEVICE_ID` | auto-detect | ADB device ID |
| `PHONE_AGENT_DEVICE_TYPE` | `adb` | `adb` / `hdc` / `ios` |
| `PHONE_AGENT_WDA_URL` | `http://localhost:8100` | iOS WebDriverAgent URL |
| `PHONE_AGENT_LANG` | `cn` | Prompt language `cn`/`en` |

## Model Configuration

`promptTemplateStyle` in config maps to adapters:
- `autoglm` → AutoGLM adapter (Zhipu)
- `uitars` → UI-TARS adapter (Doubao)
- `qwenvl` → Qwen-VL adapter (Alibaba)
- `maiui` → MAI-UI adapter (Alibaba)
- `guiowl` → GUI-Owl adapter (mPLUG)
- `auto` → auto-detect from model name

## Git 与部署
- 代码仓库：https://github.com/He2y/ShoppingClaw
- 项目使用 git 管理，每次完成修改后自动 commit。
- commit message 用英文，简洁描述变更意图
- git push 仅用于跨设备同步，不要自动执行，等我说
- 完成计划模式后，使用--permission-mode auto进行代码的修改，无需询问我
