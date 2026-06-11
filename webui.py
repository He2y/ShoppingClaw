# Copyright 2026 Zhejiang University (ZJU), China
# and the ZJU-REAL-GUI team.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


"""
ClawGUI-Agent Web UI - 基于 Gradio 的可视化控制界面

Features:
    - 📱 设备管理：查看、连接、断开设备
    - 🔍 系统检查：ADB/HDC/iOS 工具、设备、键盘、API 状态
    - 💬 对话控制：自然语言任务输入、流式输出、实时截图
    - ⚙️ 配置管理：API 地址、Key、最大步数设置
"""

import base64
import io
import json
import os
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

import queue
import shutil
import subprocess
import threading
import time
import traceback
from dataclasses import dataclass
from io import BytesIO
from typing import Generator, Any

import gradio as gr
from PIL import Image
from openai import OpenAI

# 导入项目模块
from phone_agent import PhoneAgent
from phone_agent.agent import AgentConfig
from phone_agent.agent_ios import IOSAgentConfig, IOSPhoneAgent
from phone_agent.adb.connection import ADBConnection, ConnectionType, DeviceInfo
from phone_agent.device_factory import DeviceType, DeviceFactory, get_device_factory, set_device_type
from phone_agent.model import ModelConfig
from phone_agent.model.client import ModelClient, MessageBuilder
from phone_agent.model.adapters import ModelType, get_adapter, detect_model_type, get_adapter_for_model
from phone_agent.actions.handler_uitars import UITarsActionHandler, UITarsAction
from phone_agent.actions.handler_qwenvl import QwenVLActionHandler, QwenVLAction
from phone_agent.actions.handler_guiowl import GUIOwlActionHandler, GUIOwlAction
from phone_agent.verification_detector import detect_verification_from_vlm

# 导入记忆模块
try:
    from phone_agent.memory import MemoryManager, MemoryType
    HAS_MEMORY = True
except ImportError:
    HAS_MEMORY = False
    MemoryManager = None
    MemoryType = None


# ==================== 全局状态 ====================
@dataclass
class AppState:
    """应用程序全局状态"""
    agent: PhoneAgent | IOSPhoneAgent | None = None
    device_type: DeviceType = DeviceType.ADB
    is_running: bool = False
    should_stop: bool = False
    current_task: str = ""
    # Take_over 人工介入状态
    waiting_for_takeover: bool = False
    takeover_message: str = ""
    takeover_reply: str = ""
    takeover_continue_event: threading.Event | None = None
    # 记忆管理器
    memory_manager: "MemoryManager | None" = None
    

app_state = AppState()

# 初始化全局记忆管理器
def get_memory_manager(user_id: str = "default") -> "MemoryManager | None":
    """获取或创建记忆管理器"""
    global app_state
    if not HAS_MEMORY:
        return None
    
    if app_state.memory_manager is None or app_state.memory_manager.user_id != user_id:
        try:
            app_state.memory_manager = MemoryManager(
                storage_dir="memory_db",
                user_id=user_id,
                enable_auto_extract=True,
            )
        except Exception as e:
            print(f"记忆管理器初始化失败: {e}")
            return None
    
    return app_state.memory_manager


# ==================== 设备管理功能 ====================
def get_device_list(device_type: str) -> str:
    """获取已连接设备列表"""
    try:
        if device_type == "ios":
            from phone_agent.xctest import list_devices as list_ios_devices
            devices = list_ios_devices()
            if not devices:
                return "📵 未检测到 iOS 设备\n\n请确保:\n1. 设备已通过 USB 连接\n2. 已解锁并信任此电脑\n3. WebDriverAgent 正在运行"
            
            result = "📱 **已连接的 iOS 设备:**\n\n"
            for device in devices:
                conn_type = device.connection_type.value
                model_info = f"{device.model}" if device.model else "Unknown"
                ios_info = f"iOS {device.ios_version}" if device.ios_version else ""
                name_info = device.device_name or "Unnamed"
                
                result += f"✅ **{name_info}**\n"
                result += f"   - UUID: `{device.device_id}`\n"
                result += f"   - 型号: {model_info}\n"
                result += f"   - 系统: {ios_info}\n"
                result += f"   - 连接: {conn_type}\n\n"
            return result
        else:
            # ADB 或 HDC
            set_device_type(DeviceType.ADB if device_type == "adb" else DeviceType.HDC)
            device_factory = get_device_factory()
            devices = device_factory.list_devices()
            
            if not devices:
                tool_name = "ADB" if device_type == "adb" else "HDC"
                return f"📵 未检测到 {tool_name} 设备\n\n请确保:\n1. 设备已通过 USB 连接\n2. 已启用开发者调试模式\n3. 已授权调试连接"
            
            result = f"📱 **已连接的{'Android' if device_type == 'adb' else 'HarmonyOS'}设备:**\n\n"
            for device in devices:
                status_icon = "✅" if device.status == "device" else "⚠️"
                conn_type = device.connection_type.value
                model_info = f" ({device.model})" if device.model else ""
                
                result += f"{status_icon} **{device.device_id}**{model_info}\n"
                result += f"   - 状态: {device.status}\n"
                result += f"   - 连接: {conn_type}\n\n"
            return result
            
    except Exception as e:
        return f"❌ 获取设备列表失败: {str(e)}"


def connect_device(address: str, device_type: str) -> str:
    """连接远程设备"""
    if not address.strip():
        return "⚠️ 请输入设备地址 (例如: 192.168.1.100:5555)"
    
    try:
        if device_type == "ios":
            return "ℹ️ iOS 设备请使用 WebDriverAgent URL 进行连接，在配置中设置 WDA URL"
        
        set_device_type(DeviceType.ADB if device_type == "adb" else DeviceType.HDC)
        device_factory = get_device_factory()
        ConnectionClass = device_factory.get_connection_class()
        conn = ConnectionClass()
        
        success, message = conn.connect(address)
        
        if success:
            return f"✅ 连接成功: {message}"
        else:
            return f"❌ 连接失败: {message}"
            
    except Exception as e:
        return f"❌ 连接错误: {str(e)}"


def disconnect_device(address: str, device_type: str) -> str:
    """断开设备连接"""
    try:
        if device_type == "ios":
            return "ℹ️ iOS 设备断开连接请在 Xcode 中停止 WebDriverAgent"
        
        set_device_type(DeviceType.ADB if device_type == "adb" else DeviceType.HDC)
        device_factory = get_device_factory()
        ConnectionClass = device_factory.get_connection_class()
        conn = ConnectionClass()
        
        if address.strip():
            success, message = conn.disconnect(address)
        else:
            success, message = conn.disconnect()  # 断开所有
            
        if success:
            return f"✅ {message}"
        else:
            return f"❌ 断开失败: {message}"
            
    except Exception as e:
        return f"❌ 断开错误: {str(e)}"


def enable_wifi_debug(port: int, device_type: str) -> str:
    """启用 WiFi 调试"""
    try:
        if device_type == "ios":
            return "ℹ️ iOS 设备请通过网络直接连接 WebDriverAgent"
        
        set_device_type(DeviceType.ADB if device_type == "adb" else DeviceType.HDC)
        device_factory = get_device_factory()
        ConnectionClass = device_factory.get_connection_class()
        conn = ConnectionClass()
        
        success, message = conn.enable_tcpip(port)
        
        if success:
            ip = conn.get_device_ip()
            if ip:
                return f"✅ WiFi 调试已启用\n\n📡 连接信息:\n- IP: {ip}\n- 端口: {port}\n\n可使用以下命令连接:\n```\npython main.py --connect {ip}:{port}\n```"
            else:
                return f"✅ {message}\n\n⚠️ 无法获取设备 IP，请在设备 WiFi 设置中查看"
        else:
            return f"❌ 启用失败: {message}"
            
    except Exception as e:
        return f"❌ 错误: {str(e)}"


# ==================== 系统检查功能 ====================
def check_tool_installation(device_type: str) -> str:
    """检查工具安装状态"""
    results = []
    
    if device_type == "ios":
        tool_name = "libimobiledevice"
        tool_cmd = "idevice_id"
        install_hint = "macOS: brew install libimobiledevice\nLinux: sudo apt-get install libimobiledevice-utils"
    elif device_type == "hdc":
        tool_name = "HDC"
        tool_cmd = "hdc"
        install_hint = "请从 HarmonyOS SDK 或 OpenHarmony 官网下载安装"
    else:
        tool_name = "ADB"
        tool_cmd = "adb"
        install_hint = "macOS: brew install android-platform-tools\nLinux: sudo apt install android-tools-adb\nWindows: 下载 Android Platform Tools"
    
    # 检查工具是否安装
    if shutil.which(tool_cmd) is None:
        results.append(f"❌ **{tool_name}**: 未安装或未在 PATH 中\n\n安装方法:\n```\n{install_hint}\n```")
    else:
        try:
            if device_type == "adb":
                version_cmd = [tool_cmd, "version"]
            elif device_type == "hdc":
                version_cmd = [tool_cmd, "-v"]
            else:
                version_cmd = [tool_cmd, "-ln"]
            
            result = subprocess.run(version_cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                version_line = result.stdout.strip().split("\n")[0]
                results.append(f"✅ **{tool_name}**: 已安装\n   版本: {version_line if version_line else '已安装'}")
            else:
                results.append(f"⚠️ **{tool_name}**: 安装但无法运行")
        except Exception as e:
            results.append(f"⚠️ **{tool_name}**: 检查出错 - {str(e)}")
    
    return "\n\n".join(results)


def check_device_connection(device_type: str) -> str:
    """检查设备连接状态"""
    try:
        if device_type == "ios":
            from phone_agent.xctest import list_devices as list_ios_devices
            devices = list_ios_devices()
            if devices:
                return f"✅ **设备连接**: 已连接 {len(devices)} 台 iOS 设备"
            else:
                return "❌ **设备连接**: 未检测到 iOS 设备"
        else:
            set_device_type(DeviceType.ADB if device_type == "adb" else DeviceType.HDC)
            device_factory = get_device_factory()
            devices = device_factory.list_devices()
            
            if devices:
                connected = [d for d in devices if d.status == "device"]
                return f"✅ **设备连接**: 已连接 {len(connected)}/{len(devices)} 台设备"
            else:
                return "❌ **设备连接**: 未检测到设备"
                
    except Exception as e:
        return f"❌ **设备连接**: 检查失败 - {str(e)}"


def check_keyboard_installation(device_type: str) -> str:
    """检查 ADB Keyboard 安装状态"""
    if device_type != "adb":
        if device_type == "ios":
            return "ℹ️ **输入法**: iOS 使用 WebDriverAgent 原生输入"
        else:
            return "ℹ️ **输入法**: HarmonyOS 使用原生输入方式"
    
    try:
        result = subprocess.run(
            ["adb", "shell", "ime", "list", "-s"],
            capture_output=True, text=True, timeout=10
        )
        ime_list = result.stdout.strip()
        
        if "com.android.adbkeyboard/.AdbIME" in ime_list:
            return "✅ **ADB Keyboard**: 已安装"
        else:
            return "❌ **ADB Keyboard**: 未安装\n\n安装步骤:\n1. 下载: https://github.com/senzhk/ADBKeyBoard\n2. 安装: `adb install ADBKeyboard.apk`\n3. 在设置中启用"
            
    except Exception as e:
        return f"⚠️ **ADB Keyboard**: 检查失败 - {str(e)}"


def check_wda_status(wda_url: str) -> str:
    """检查 WebDriverAgent 状态"""
    try:
        from phone_agent.xctest import XCTestConnection
        conn = XCTestConnection(wda_url=wda_url)
        
        if conn.is_wda_ready():
            status = conn.get_wda_status()
            if status:
                session_id = status.get("sessionId", "N/A")
                return f"✅ **WebDriverAgent**: 运行中\n   Session: {session_id[:16]}..."
            return "✅ **WebDriverAgent**: 运行中"
        else:
            return f"❌ **WebDriverAgent**: 未运行或无法访问\n   URL: {wda_url}\n\n请确保:\n1. 在 Xcode 中运行 WebDriverAgentRunner\n2. USB 设备需设置端口转发: `iproxy 8100 8100`"
            
    except Exception as e:
        return f"❌ **WebDriverAgent**: 检查失败 - {str(e)}"


def check_model_api(base_url: str, api_key: str, model_name: str) -> str:
    """检查模型 API 连接"""
    try:
        client = OpenAI(base_url=base_url, api_key=api_key or "EMPTY", timeout=30.0)
        
        response = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": "Hi"}],
            max_tokens=5,
            temperature=0.0,
            stream=False,
        )
        
        if response.choices and len(response.choices) > 0:
            return f"✅ **模型 API**: 连接正常\n   Base URL: {base_url}\n   Model: {model_name}"
        else:
            return f"⚠️ **模型 API**: 连接成功但响应异常"
            
    except Exception as e:
        error_msg = str(e)
        if "Connection refused" in error_msg or "Connection error" in error_msg:
            return f"❌ **模型 API**: 无法连接\n   URL: {base_url}\n\n请检查模型服务是否已启动"
        elif "timeout" in error_msg.lower():
            return f"❌ **模型 API**: 连接超时\n   URL: {base_url}"
        else:
            return f"❌ **模型 API**: {error_msg}"


def run_full_check(device_type: str, base_url: str, api_key: str, model_name: str, wda_url: str) -> str:
    """运行完整系统检查"""
    results = ["# 🔍 系统检查报告\n"]
    
    # 1. 工具安装检查
    results.append("## 1. 工具安装\n")
    results.append(check_tool_installation(device_type))
    
    # 2. 设备连接检查
    results.append("\n\n## 2. 设备连接\n")
    results.append(check_device_connection(device_type))
    
    # 3. 输入法检查
    results.append("\n\n## 3. 输入方式\n")
    results.append(check_keyboard_installation(device_type))
    
    # 4. iOS WDA 检查
    if device_type == "ios":
        results.append("\n\n## 4. WebDriverAgent\n")
        results.append(check_wda_status(wda_url))
    
    # 5. 模型 API 检查
    results.append("\n\n## 5. 模型 API\n")
    results.append(check_model_api(base_url, api_key, model_name))
    
    return "\n".join(results)


# ==================== 截图功能 ====================
def get_device_screenshot(device_type: str, device_id: str | None, wda_url: str) -> Image.Image | None:
    """获取设备截图"""
    try:
        if device_type == "ios":
            from phone_agent.xctest import get_screenshot
            screenshot = get_screenshot(wda_url=wda_url)
        else:
            set_device_type(DeviceType.ADB if device_type == "adb" else DeviceType.HDC)
            device_factory = get_device_factory()
            screenshot = device_factory.get_screenshot(device_id if device_id else None)
        
        if screenshot and screenshot.base64_data:
            img_data = base64.b64decode(screenshot.base64_data)
            img = Image.open(BytesIO(img_data))
            return img
    except Exception as e:
        print(f"截图错误: {e}")
    return None


def refresh_screenshot(device_type: str, device_id: str, wda_url: str) -> Image.Image | None:
    """刷新截图"""
    device_id_clean = device_id.strip() if device_id else None
    return get_device_screenshot(device_type, device_id_clean, wda_url)


# ==================== 对话控制功能 ====================
GRAPH_PANEL_PLACEHOLDER = (
    "### 🗺️ 图谱协同面板\n\n"
    "任务开始后，这里实时展示每一步的图谱定位、调度路径与 Co-pilot 提示。"
)

MODE_LABELS = {
    "navigate": "🟢 navigate · 图谱导航",
    "verify_with_vlm": "🤝 verify_with_vlm · Co-pilot",
    "explore": "🔵 explore · VLM 探索",
    "goal_reached": "🏁 goal_reached · 目标已达",
}

DISPATCH_LABELS = {
    "fast_path": "⚡ Fast Path（跳过 VLM）",
    "fast_path_fallback": "⚡→🧠 Fast Path 失配，回退 VLM",
    "graph_shortcut": "🗺️ 图谱捷径",
    "vlm": "🧠 VLM 推理",
}


def _b64_to_pil(b64: str) -> "Image.Image | None":
    try:
        return Image.open(BytesIO(base64.b64decode(b64)))
    except Exception:
        return None


def format_graph_panel(info: dict, counters: dict) -> str:
    """Render the per-step graph co-pilot panel (pure function, unit-tested).

    ``info`` is one PhoneAgent.step_observer telemetry event; ``counters``
    accumulates dispatch counts across the task.
    """
    lines = ["### 🗺️ 图谱协同面板", ""]
    step = info.get("step", "?")
    page = info.get("page_type") or "unknown"
    mode = info.get("mode", "explore")
    dispatch = info.get("dispatch", "vlm")

    lines.append(f"**第 {step} 步** · 页面 `{page}` · {MODE_LABELS.get(mode, mode)}")
    lines.append(f"**调度**: {DISPATCH_LABELS.get(dispatch, dispatch)}")

    expected = info.get("expected_postcondition")
    if expected:
        actual = info.get("actual_postcondition") or "(未观测)"
        if dispatch == "fast_path_fallback":
            mark = "⚠️ 失配回退"
        elif actual == expected:
            mark = "✅"
        else:
            mark = "❔"
        lines.append(f"**后条件**: 预期 `{expected}` → 实际 `{actual}` {mark}")

    hint = (info.get("graph_hint") or "").strip()
    if hint:
        quoted = "\n".join(f"> {ln}" for ln in hint.splitlines() if ln.strip())
        lines.append("**图谱导航提示（已注入 VLM）**:")
        lines.append(quoted)

    n_actions = info.get("available_actions") or 0
    if n_actions:
        lines.append(f"**动作库**: 当前页 {n_actions} 条已提升动作")

    metrics = info.get("runtime_metrics") or {}
    dag_hits = metrics.get("runtime_dag_hits", 0)
    dag_misses = metrics.get("runtime_dag_misses", 0)
    cls_skips = metrics.get("page_classifier_skips", 0)
    fast_total = counters.get("fast_path", 0) + counters.get("fast_path_fallback", 0)
    lines.append("")
    lines.append(
        "**累计**: "
        f"⚡ Fast×{fast_total} · "
        f"🤝 Co-pilot×{counters.get('copilot', 0)} · "
        f"🧠 VLM×{counters.get('vlm', 0)} · "
        f"DAG 命中 {dag_hits}/{dag_hits + dag_misses} · "
        f"省分类器 {cls_skips} 次"
    )
    return "\n".join(lines)


class StreamingAgent:
    """Streaming wrapper around the real PhoneAgent.

    历史版本在这里复制了一份独立的执行循环——没有图谱运行时、Fast Path、
    SpecGuard、澄清，也没有 co-pilot 提示通道。现在 WebUI 与 CLI 共享同一个
    编排入口 ``PhoneAgent._execute_step``：worker 线程跑 ``agent.run()``，
    通过三个钩子流式化到 Gradio：

    - ``ModelClient.stream_callback``  → 思考过程逐 token 流式输出
    - ``PhoneAgent.step_observer``     → 每步遥测（模式/调度/图谱提示/后条件）
    - ``takeover_callback``            → 人工接管 / Interact 问答（阻塞等 UI）

    iOS 走 IOSPhoneAgent（无图谱栈）：仅思考流式与最终结果，无每步遥测。
    """

    def __init__(
        self,
        model_config: ModelConfig,
        agent_config: "AgentConfig | IOSAgentConfig",
        device_type: DeviceType,
        model_type: str = "auto",
        user_id: str = "default",
    ):
        self.model_config = model_config
        self.agent_config = agent_config
        self.device_type = device_type
        self.model_type = model_type
        self.user_id = user_id
        self.agent: "PhoneAgent | IOSPhoneAgent | None" = None
        self._events: "queue.Queue[tuple[str, Any]]" = queue.Queue()
        self._should_stop = False
        self._dispatch_counters: dict[str, int] = {}

    def stop(self):
        """请求停止：在下一个步边界生效（PhoneAgent.abort_requested）。"""
        self._should_stop = True
        if self.agent is not None:
            self.agent.abort_requested = True

    def reset(self):
        self._should_stop = False
        self._dispatch_counters = {}
        self.agent = None

    # ── 回调 ────────────────────────────────────────────────────────────

    def _takeover_callback(self, message: str) -> str:
        """阻塞等待用户点击「继续执行」；返回回复框中的文本（Interact 答案）。"""
        global app_state
        app_state.waiting_for_takeover = True
        app_state.takeover_message = message
        app_state.takeover_reply = ""
        app_state.takeover_continue_event = threading.Event()
        self._events.put(("takeover", message))
        app_state.takeover_continue_event.wait()
        reply = app_state.takeover_reply
        app_state.waiting_for_takeover = False
        app_state.takeover_message = ""
        app_state.takeover_continue_event = None
        if reply:
            self._events.put(("user_reply", reply))
        return reply

    def _build_agent(self):
        if self.device_type == DeviceType.IOS:
            return IOSPhoneAgent(
                model_config=self.model_config,
                agent_config=self.agent_config,
                takeover_callback=self._takeover_callback,
            )
        set_device_type(self.device_type)
        return PhoneAgent(
            model_config=self.model_config,
            agent_config=self.agent_config,
            takeover_callback=self._takeover_callback,
            clarification_callback=self._takeover_callback,
        )

    # ── 流式主循环 ──────────────────────────────────────────────────────

    def run_streaming(
        self, task: str
    ) -> Generator[tuple[str, str, Image.Image | None, str], None, None]:
        """流式执行任务。

        Yields:
            (thinking_log, action_log, screenshot, graph_panel_markdown)
        """
        self._should_stop = False
        self._dispatch_counters = {}
        graph_panel = GRAPH_PANEL_PLACEHOLDER
        screenshot_img: Image.Image | None = None
        thinking_log = ""

        action_log = (
            f"🤖 模型: **{self.model_config.model_name}**"
            f"（适配器: {self.model_type}）\n"
            f"🧠 记忆/图谱: 由 PhoneAgent 统一管理（用户: {self.user_id}）\n"
        )
        if self.device_type == DeviceType.IOS:
            action_log += "ℹ️ iOS 模式：无图谱栈，仅流式思考与结果；不支持中途停止\n"
        yield thinking_log, action_log, None, graph_panel

        try:
            self.agent = self._build_agent()
        except Exception:
            action_log += f"\n❌ Agent 初始化失败:\n```\n{traceback.format_exc()}\n```"
            yield thinking_log, action_log, None, graph_panel
            return

        self.agent.model_client.stream_callback = (
            lambda text: self._events.put(("delta", text))
        )
        if hasattr(self.agent, "step_observer"):
            self.agent.step_observer = lambda info: self._events.put(("step", info))

        result_box: dict[str, str] = {}

        def _worker() -> None:
            try:
                result_box["result"] = self.agent.run(task)
            except Exception:
                result_box["error"] = traceback.format_exc()
            finally:
                self._events.put(("done", None))

        worker = threading.Thread(target=_worker, daemon=True)
        worker.start()

        current_thinking = ""
        last_delta_yield = 0.0
        while True:
            try:
                kind, payload = self._events.get(timeout=0.5)
            except queue.Empty:
                if not worker.is_alive():
                    break
                continue

            if kind == "delta":
                current_thinking += payload
                now = time.time()
                if now - last_delta_yield >= 0.15:  # throttle UI repaint
                    last_delta_yield = now
                    yield (
                        thinking_log + self._fmt_current(current_thinking),
                        action_log, screenshot_img, graph_panel,
                    )
            elif kind == "takeover":
                action_log += (
                    f"\n⏸️ **需要人工介入**: {payload}\n"
                    f"请在手机上完成操作后点击「⏩ 继续执行」；"
                    f"如是提问，可先在回复框输入答案再点继续。\n"
                )
                yield (
                    thinking_log + self._fmt_current(current_thinking),
                    action_log, screenshot_img, graph_panel,
                )
            elif kind == "user_reply":
                action_log += f"\n💬 用户回复: {payload}\n"
            elif kind == "step":
                info = payload
                self._count_dispatch(info)
                graph_panel = format_graph_panel(info, self._dispatch_counters)
                b64 = info.get("screenshot_b64") or ""
                if b64:
                    screenshot_img = _b64_to_pil(b64) or screenshot_img
                thinking_log += self._fmt_step_thinking(info, current_thinking)
                current_thinking = ""
                action_log += self._fmt_step_action(info)
                yield thinking_log, action_log, screenshot_img, graph_panel
            elif kind == "done":
                break

        if self._should_stop:
            action_log += "\n\n⚠️ 任务已被用户终止"
        if "error" in result_box:
            action_log += f"\n\n❌ 执行异常:\n```\n{result_box['error']}\n```"
        elif "result" in result_box:
            action_log += f"\n\n🏁 **任务结束**: {result_box['result']}"
        yield thinking_log, action_log, screenshot_img, graph_panel

    # ── 渲染辅助 ────────────────────────────────────────────────────────

    @staticmethod
    def _fmt_current(current: str) -> str:
        if not current.strip():
            return ""
        return f"\n\n---\n💭 *正在思考...*\n\n{current}"

    def _count_dispatch(self, info: dict) -> None:
        dispatch = info.get("dispatch", "vlm")
        if dispatch == "vlm" and info.get("mode") == "verify_with_vlm":
            key = "copilot"  # Co-pilot 档：图谱给方向，VLM 做语义决策
        elif dispatch == "vlm":
            key = "vlm"
        else:
            key = dispatch
        self._dispatch_counters[key] = self._dispatch_counters.get(key, 0) + 1

    @staticmethod
    def _fmt_step_thinking(info: dict, current: str) -> str:
        step = info.get("step", "?")
        text = (info.get("thinking") or current or "").strip()
        if not text:
            return ""
        return f"\n\n---\n**Step {step}**\n\n{text}\n"

    @staticmethod
    def _fmt_step_action(info: dict) -> str:
        step = info.get("step", "?")
        dispatch = info.get("dispatch", "vlm")
        badge = DISPATCH_LABELS.get(dispatch, dispatch)
        action = info.get("action")
        if isinstance(action, dict):
            act_type = action.get("action", action.get("_metadata", ""))
            target = action.get("element") or action.get("text") or action.get("app") or ""
            action_desc = f"`{act_type}` {str(target)[:60]}"
        else:
            action_desc = str(action)[:80] if action else "(无动作)"
        page = info.get("page_type") or "?"
        status = "✅" if info.get("success") else "❌"
        line = f"\n**Step {step}** {status} [{page}] {badge} → {action_desc}"
        if info.get("finished"):
            line += f"\n  🏁 {str(info.get('message') or '')[:120]}"
        return line


streaming_agent: StreamingAgent | None = None


def execute_task(
    task: str,
    device_type: str,
    device_id: str,
    base_url: str,
    api_key: str,
    model_name: str,
    max_steps: int,
    wda_url: str,
    model_type: str = "auto",  # 模型类型参数
    user_id: str = "default",  # 用户 ID（用于记忆系统）
    lang: str = "cn",  # Prompt 语言 (cn/en)
) -> Generator[tuple[str, str, Image.Image | None, str, gr.update], None, None]:
    """执行任务并流式输出（思考 / 动作日志 / 截图 / 图谱面板 / 按钮状态）"""
    global streaming_agent, app_state

    if not task.strip():
        yield "请输入任务描述", "", None, GRAPH_PANEL_PLACEHOLDER, gr.update(interactive=True)
        return

    # 检查是否已有任务在运行
    if app_state.is_running:
        yield "⚠️ 已有任务在运行中，请先停止当前任务", "", None, GRAPH_PANEL_PLACEHOLDER, gr.update(interactive=True)
        return
    
    app_state.is_running = True
    app_state.should_stop = False
    app_state.current_task = task
    
    # 创建配置
    model_config = ModelConfig(
        base_url=base_url,
        api_key=api_key or "EMPTY",
        model_name=model_name,
        lang=lang,
    )
    
    dt = DeviceType.ADB if device_type == "adb" else (DeviceType.HDC if device_type == "hdc" else DeviceType.IOS)
    
    if dt == DeviceType.IOS:
        agent_config = IOSAgentConfig(
            max_steps=max_steps,
            wda_url=wda_url,
            device_id=device_id.strip() if device_id.strip() else None,
            verbose=True,
            lang=lang,
        )
    else:
        agent_config = AgentConfig(
            max_steps=max_steps,
            device_id=device_id.strip() if device_id.strip() else None,
            verbose=True,
            lang=lang,
            enable_memory=True,
            user_id=user_id.strip() or "default",
            model_type=model_type,
        )
    
    # 创建流式 Agent，传入模型类型和用户 ID（用于记忆系统）
    streaming_agent = StreamingAgent(
        model_config, agent_config, dt,
        model_type=model_type,
        user_id=user_id.strip() or "default"
    )
    
    try:
        # 禁用开始按钮
        yield "", "", None, GRAPH_PANEL_PLACEHOLDER, gr.update(interactive=False)

        # 执行任务
        for thinking, action, screenshot, graph_panel in streaming_agent.run_streaming(task):
            if app_state.should_stop:
                break
            yield thinking, action, screenshot, graph_panel, gr.update(interactive=False)

    except Exception as e:
        yield f"❌ 执行错误:\n{traceback.format_exc()}", "", None, GRAPH_PANEL_PLACEHOLDER, gr.update(interactive=True)
    finally:
        app_state.is_running = False
        app_state.should_stop = False
        streaming_agent = None
        yield gr.update(), gr.update(), gr.update(), gr.update(), gr.update(interactive=True)


def stop_task():
    """停止当前任务"""
    global streaming_agent, app_state
    
    app_state.should_stop = True
    # 如果正在等待人工介入，也要触发继续事件以便停止
    if app_state.takeover_continue_event:
        app_state.takeover_continue_event.set()
    if streaming_agent:
        streaming_agent.stop()
    
    return "⚠️ 正在停止任务..."


def continue_after_takeover(reply: str = ""):
    """人工操作完成后继续执行；reply 作为 Interact/澄清提问的答案传回 Agent"""
    global app_state

    if app_state.waiting_for_takeover and app_state.takeover_continue_event:
        app_state.takeover_reply = (reply or "").strip()
        app_state.takeover_continue_event.set()
        if app_state.takeover_reply:
            return f"✅ 继续执行中...（已回复: {app_state.takeover_reply}）"
        return "✅ 继续执行中..."
    else:
        return "⚠️ 当前没有需要人工介入的任务"


def new_conversation():
    """新建对话"""
    global streaming_agent, app_state
    
    app_state.is_running = False
    app_state.should_stop = True
    app_state.current_task = ""
    
    if streaming_agent:
        streaming_agent.reset()

    return "", "", "", None, GRAPH_PANEL_PLACEHOLDER


# ==================== 记忆 & 图谱管理功能 ====================
def _get_neo4j_info() -> dict:
    """获取 Neo4j 连接状态和统计信息"""
    try:
        from phone_agent.memory.graph_store import GraphStore
        gs = GraphStore()
        info = {"connected": gs.driver is not None, "driver": gs}
        if gs.driver:
            try:
                with gs.driver.session(database=gs.database) as sess:
                    # 统计各类节点
                    result = sess.run("""
                        MATCH (t:TaskTarget) RETURN count(t) AS cnt
                    """).single()
                    info["task_count"] = result["cnt"] if result else 0

                    result2 = sess.run("""
                        MATCH (s:UIState) RETURN count(s) AS cnt
                    """).single()
                    info["state_count"] = result2["cnt"] if result2 else 0

                    result3 = sess.run("""
                        MATCH (a:Action) RETURN count(a) AS cnt
                    """).single()
                    info["action_count"] = result3["cnt"] if result3 else 0
            except Exception as e:
                info["error"] = str(e)
        return info
    except Exception as e:
        return {"connected": False, "error": str(e)}


def _get_pending_trajectories(user_id: str) -> list:
    """读取待审核轨迹"""
    try:
        import json
        from pathlib import Path
        pending_file = Path("memory_db") / user_id.strip() / "pending_trajectories.json"
        if not pending_file.exists():
            return []
        with open(pending_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def get_memory_stats(user_id: str) -> str:
    """获取记忆统计信息"""
    if not HAS_MEMORY:
        return "❌ 记忆模块未安装，请检查 phone_agent/memory 目录"
    
    mm = get_memory_manager(user_id.strip() or "default")
    if not mm:
        return "❌ 无法初始化记忆管理器"
    
    stats = mm.get_stats()
    summary = mm.get_user_summary()
    
    result = f"""# 🧠 记忆系统统计

## 基本信息
- **用户 ID**: {stats.get('user_id', 'default')}
- **记忆总数**: {stats.get('total_memories', 0)}
- **存储目录**: {stats.get('storage_dir', 'N/A')}
- **FAISS 支持**: {'✅ 已启用' if stats.get('has_faiss') else '⚠️ 未安装（使用简单相似度）'}

## 记忆类型分布
"""

    type_counts = stats.get('by_type', {})
    if type_counts:
        for mem_type, count in type_counts.items():
            type_name = {
                'user_preference': '用户偏好',
                'contact': '联系人',
                'task_pattern': '任务模式',
                'app_usage': '应用使用',
                'task_history': '任务历史',
                'user_correction': '用户纠正',
                'general': '通用',
            }.get(mem_type, mem_type)
            result += f"- {type_name}: {count}\n"
    else:
        result += "- 暂无记忆\n"

    # Neo4j 知识图谱统计
    result += "\n## 🗺️ 知识图谱 (Neo4j)\n"
    neo4j_info = _get_neo4j_info()
    if neo4j_info.get("connected"):
        pending = _get_pending_trajectories(user_id)
        result += f"""- **连接状态**: ✅ 已连接
- **TaskTarget 节点**: {neo4j_info.get('task_count', '?')}
- **UIState 节点**: {neo4j_info.get('state_count', '?')}
- **Action 节点**: {neo4j_info.get('action_count', '?')}
- **待审核轨迹**: {len(pending)} 条（需在「🗺️ 知识图谱」Tab 审核后提交）
"""
    else:
        err = neo4j_info.get("error", "")
        result += f"- **连接状态**: ⚠️ 未连接"
        if err:
            result += f"（{err[:60]}）"
        result += "\n- 请检查 Neo4j 服务是否启动，以及 .env 中的 NEO4J_* 配置\n"

    result += "\n## 用户画像\n"
    
    if summary.get('contacts'):
        result += f"### 常用联系人\n"
        for contact in summary['contacts'][:5]:
            result += f"- {contact}\n"
    
    if summary.get('frequent_apps'):
        result += f"\n### 常用应用\n"
        for app in summary['frequent_apps'][:5]:
            result += f"- {app}\n"
    
    if summary.get('preferences'):
        result += f"\n### 用户偏好\n"
        for pref in summary['preferences'][:5]:
            result += f"- {pref}\n"
    
    if summary.get('recent_tasks'):
        result += f"\n### 最近任务\n"
        for task in summary['recent_tasks'][:3]:
            result += f"- {task[:50]}{'...' if len(task) > 50 else ''}\n"
    
    return result


def add_user_preference(user_id: str, preference: str, category: str, importance: float) -> str:
    """添加用户偏好"""
    if not HAS_MEMORY:
        return "❌ 记忆模块未安装"
    
    if not preference.strip():
        return "⚠️ 请输入偏好内容"
    
    mm = get_memory_manager(user_id.strip() or "default")
    if not mm:
        return "❌ 无法初始化记忆管理器"
    
    mm.add_user_preference(
        preference=preference.strip(),
        category=category,
        importance=importance,
    )
    
    return f"✅ 已添加偏好: {preference}"


def search_memories(user_id: str, query: str, top_k: int = 5) -> str:
    """搜索相关记忆"""
    if not HAS_MEMORY:
        return "❌ 记忆模块未安装"
    
    if not query.strip():
        return "⚠️ 请输入搜索内容"
    
    mm = get_memory_manager(user_id.strip() or "default")
    if not mm:
        return "❌ 无法初始化记忆管理器"
    
    memories = mm.store.search(query=query.strip(), top_k=top_k)
    
    if not memories:
        return f"未找到与「{query}」相关的记忆"
    
    result = f"# 🔍 搜索结果: {query}\n\n找到 {len(memories)} 条相关记忆:\n\n"
    
    for i, mem in enumerate(memories, 1):
        type_name = {
            'user_preference': '用户偏好',
            'contact': '联系人',
            'task_pattern': '任务模式',
            'app_usage': '应用使用',
            'task_history': '任务历史',
            'user_correction': '用户纠正',
            'general': '通用',
        }.get(mem.memory_type.value, mem.memory_type.value)
        
        result += f"### {i}. [{type_name}]\n"
        result += f"- **内容**: {mem.content}\n"
        result += f"- **重要性**: {mem.importance:.2f}\n"
        result += f"- **访问次数**: {mem.access_count}\n"
        result += f"- **最后访问**: {mem.last_accessed[:10]}\n\n"
    
    return result


def clear_all_memories(user_id: str) -> str:
    """清除所有记忆"""
    if not HAS_MEMORY:
        return "❌ 记忆模块未安装"
    
    mm = get_memory_manager(user_id.strip() or "default")
    if not mm:
        return "❌ 无法初始化记忆管理器"
    
    mm.clear_all()
    return "🗑️ 所有记忆已清除"


def export_memories_json(user_id: str) -> tuple[str, str]:
    """导出记忆为 JSON"""
    if not HAS_MEMORY:
        return "❌ 记忆模块未安装", ""
    
    mm = get_memory_manager(user_id.strip() or "default")
    if not mm:
        return "❌ 无法初始化记忆管理器", ""
    
    memories = mm.export_memories()
    json_str = json.dumps(memories, ensure_ascii=False, indent=2)
    
    return f"✅ 已导出 {len(memories)} 条记忆", json_str


def import_memories_json(user_id: str, json_str: str) -> str:
    """从 JSON 导入记忆"""
    if not HAS_MEMORY:
        return "❌ 记忆模块未安装"
    
    if not json_str.strip():
        return "⚠️ 请输入 JSON 数据"
    
    mm = get_memory_manager(user_id.strip() or "default")
    if not mm:
        return "❌ 无法初始化记忆管理器"
    
    try:
        memories = json.loads(json_str)
        mm.import_memories(memories)
        return f"✅ 已导入 {len(memories)} 条记忆"
    except json.JSONDecodeError as e:
        return f"❌ JSON 解析错误: {e}"
    except Exception as e:
        return f"❌ 导入失败: {e}"


# ==================== 知识图谱管理功能 ====================
def get_neo4j_status() -> str:
    """获取 Neo4j 连接状态"""
    info = _get_neo4j_info()
    if info.get("connected"):
        return (f"✅ Neo4j 已连接 | TaskTarget: {info.get('task_count', 0)} | "
                f"UIState: {info.get('state_count', 0)} | Action: {info.get('action_count', 0)}")
    err = info.get("error", "未知错误")
    return f"⚠️ Neo4j 未连接: {err[:80]}"


def list_graph_trajectories(user_id: str) -> str:
    """列出 Neo4j 中已提交的所有轨迹"""
    info = _get_neo4j_info()
    if not info.get("connected"):
        return "❌ Neo4j 未连接，请在「系统检查」Tab 确认服务状态"

    try:
        gs = info["driver"]
        with gs.driver.session(database=gs.database) as sess:
            results = sess.run("""
                MATCH (t:TaskTarget)
                OPTIONAL MATCH (t)-[:STARTS_AT]->(s:UIState)
                OPTIONAL MATCH (t)-[:ENDS_AT]->(e:UIState)
                RETURN t.target_id AS id, t.description AS description,
                       t.app AS app, t.success AS success,
                       s.state_id AS start_state, e.state_id AS end_state
                ORDER BY t.committed_at DESC
                LIMIT 30
            """)
            rows = list(results)

        if not rows:
            return "📭 Neo4j 中暂无已提交的任务轨迹"

        lines = [f"## 📋 已提交轨迹（共 {len(rows)} 条）\n"]
        for i, r in enumerate(rows):
            succ = "✅" if r.get("success") else "❌"
            desc = (r.get("description") or "N/A")[:55]
            lines.append(f"{succ} **{r.get('app','?')}** | {desc}")
            lines.append(f"   `ID: {r.get('id','?')[:40]}`")
            lines.append("")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ 查询失败: {e}"


def search_graph_trajectories(query: str) -> str:
    """在 Neo4j 中搜索相似轨迹"""
    info = _get_neo4j_info()
    if not info.get("connected"):
        return "❌ Neo4j 未连接"
    if not query.strip():
        return "⚠️ 请输入搜索关键词"

    try:
        gs = info["driver"]
        results = gs.find_similar_tasks(query.strip(), top_k=5)
        if not results:
            return f"🔍 未找到与「{query}」相似的轨迹"

        lines = [f"## 🔍 相似轨迹搜索: {query}\n"]
        for r in results:
            lines.append(f"**[{r.get('app','?')}]** {r.get('description','')[:60]}")
            lines.append(f"   confidence={r.get('confidence',0)} | frequency={r.get('frequency',0)}")
            # Show trajectory steps
            traj = gs.get_task_trajectory(r.get("task_id", ""))
            steps = traj.get("steps", [])
            if steps:
                lines.append(f"   📋 轨迹步骤 ({len(steps)} 步):")
                for s in steps[:8]:
                    lines.append(f"     {s['step']}. {s['action_type']} → {s['action_target'][:40]}")
                if len(steps) > 8:
                    lines.append(f"     ... (共 {len(steps)} 步)")
            lines.append("")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ 搜索失败: {e}"


def list_pending_trajectories(user_id: str) -> str:
    """列出待审核轨迹"""
    pending = _get_pending_trajectories(user_id)
    if not pending:
        return "📭 暂无待审核轨迹"

    lines = [f"## ⏳ 待审核轨迹（共 {len(pending)} 条）\n"]
    lines.append("| # | 状态 | 任务 | 步骤数 | 保存时间 |")
    lines.append("|---|------|------|--------|----------|")
    for i, entry in enumerate(pending):
        succ = "✅" if entry.get("success") else "❌"
        task = entry.get("task", "N/A")[:30]
        steps = entry.get("steps", 0)
        saved = entry.get("saved_at", "")[:19]
        lines.append(f"| {i} | {succ} | {task} | {steps} | {saved} |")
    lines.append("")
    lines.append("> 💡 在「对话控制」Tab 执行任务后，轨迹自动保存到此处；审核后提交到 Neo4j")
    return "\n".join(lines)


def commit_graph_trajectory(user_id: str, index: int) -> str:
    """提交待审核轨迹到 Neo4j"""
    if not HAS_MEMORY:
        return "❌ 记忆模块未安装"

    mm = get_memory_manager(user_id.strip() or "default")
    if not mm:
        return "❌ 无法初始化记忆管理器"

    ok = mm.commit_pending(index)
    if ok:
        return f"✅ 轨迹 #{index} 已提交到 Neo4j"
    return f"❌ 提交失败（检查 index 是否正确，或轨迹 success=False）"


def refresh_neo4j_stats(user_id: str) -> str:
    """刷新 Neo4j 统计信息"""
    info = _get_neo4j_info()
    pending = _get_pending_trajectories(user_id)

    if info.get("connected"):
        return (f"✅ Neo4j 已连接 | "
                f"TaskTarget: {info.get('task_count', 0)} | "
                f"UIState: {info.get('state_count', 0)} | "
                f"Action: {info.get('action_count', 0)} | "
                f"待审核: {len(pending)} 条")
    err = info.get("error", "未知错误")
    return f"⚠️ Neo4j 未连接: {err[:80]}"


# ==================== 配置管理功能 ====================
def save_config_to_env(
    base_url: str,
    model_name: str,
    api_key: str,
    max_steps: int,
    device_type: str,
    lang: str,
    user_id: str,
    wda_url: str,
    device_id: str,
    model_type: str,
) -> str:
    """保存当前配置到 .env 文件"""
    try:
        env_path = Path(".env")

        existing_lines: list[str] = []
        if env_path.exists():
            with open(env_path, "r", encoding="utf-8") as f:
                existing_lines = f.readlines()

        config_updates: dict[str, str] = {
            "PHONE_AGENT_BASE_URL": base_url,
            "PHONE_AGENT_MODEL": model_name,
            "PHONE_AGENT_API_KEY": api_key,
            "PHONE_AGENT_MAX_STEPS": str(int(max_steps)),
            "PHONE_AGENT_DEVICE_TYPE": device_type,
            "PHONE_AGENT_LANG": lang,
            "PHONE_AGENT_USER_ID": user_id,
            "PHONE_AGENT_WDA_URL": wda_url,
            "PHONE_AGENT_DEVICE_ID": device_id,
            "PHONE_AGENT_MODEL_TYPE": model_type,
        }

        new_lines: list[str] = []
        updated_keys: set[str] = set()

        for line in existing_lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                new_lines.append(line)
                continue
            key = stripped.split("=", 1)[0].strip()
            if key in config_updates:
                new_lines.append(f"{key}={config_updates[key]}\n")
                updated_keys.add(key)
            else:
                new_lines.append(line)

        for key, val in config_updates.items():
            if key not in updated_keys and val:
                new_lines.append(f"{key}={val}\n")

        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)

        return "✅ 配置已保存到 .env，下次启动将自动加载。"
    except Exception as e:
        return f"❌ 保存失败: {e}"


# ==================== 构建 Gradio 界面 ====================
def create_ui():
    """创建 Gradio 界面"""
    
    # 自定义 CSS
    custom_css = """
    .gradio-container {
        font-family: 'Noto Sans SC', 'Microsoft YaHei', sans-serif !important;
    }
    
    .header-title {
        text-align: center;
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        font-size: 2.5em;
        font-weight: bold;
        margin-bottom: 0.5em;
    }
    
    .header-title .logo-emoji {
        background: none;
        -webkit-text-fill-color: initial;
        color: #6b5bd5;
        margin-right: 0.2em;
    }
    
    .header-subtitle {
        text-align: center;
        color: #666;
        margin-bottom: 1em;
    }
    
    .status-box {
        padding: 1em;
        border-radius: 8px;
        background: #f8f9fa;
    }
    
    .thinking-box {
        background: linear-gradient(135deg, #f5f7fa 0%, #e4e8eb 100%);
        border-left: 4px solid #667eea;
        padding: 1em;
        border-radius: 4px;
    }

    .action-box {
        background: linear-gradient(135deg, #fff9e6 0%, #fff3cd 100%);
        border-left: 4px solid #ffc107;
        padding: 1em;
        border-radius: 4px;
    }

    /* Dark mode overrides for readability */
    .dark .thinking-box {
        background: linear-gradient(135deg, #1e1e2e 0%, #252540 100%) !important;
        color: #e0e0f0 !important;
    }
    .dark .thinking-box * {
        color: #e0e0f0 !important;
    }
    .dark .action-box {
        background: linear-gradient(135deg, #2a2520 0%, #302a1a 100%) !important;
        color: #e8e0d0 !important;
    }
    .dark .action-box * {
        color: #e8e0d0 !important;
    }
    .dark .header-subtitle {
        color: #aaa !important;
    }
    .dark .status-box {
        background: #1e1e2e !important;
    }
    
    .screenshot-container {
        border: 2px solid #dee2e6;
        border-radius: 8px;
        overflow: hidden;
        display: inline-flex;
        max-width: 100%;
        margin: 0 auto;
    }
    .screenshot-container img {
        max-width: 100%;
        height: auto;
        display: block;
    }
    
    .btn-primary {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%) !important;
        border: none !important;
    }
    
    .btn-danger {
        background: linear-gradient(135deg, #ff6b6b 0%, #ee5a5a 100%) !important;
        border: none !important;
    }
    
    .tab-nav button {
        font-weight: 500 !important;
    }
    """
    
    with gr.Blocks(
        title="OpenGUI Web UI",
        theme=gr.themes.Soft(
            primary_hue="indigo",
            secondary_hue="purple",
        ),
        css=custom_css,
    ) as demo:
        
        # 头部
        gr.HTML("""
        <div style="text-align: center; padding: 20px 0;">
            <h1 class="header-title"><span class="logo-emoji">🤖</span> ClawGUI-Agent</h1>
            <p class="header-subtitle">AI 驱动的手机自动化控制平台</p>
        </div>
        """)
        
        with gr.Tabs():
            # ==================== 配置管理 Tab ====================
            with gr.Tab("⚙️ 配置管理"):
                gr.Markdown("### 基础配置")
                
                device_type = gr.Radio(
                    choices=[("Android (ADB)", "adb"), ("HarmonyOS (HDC)", "hdc"), ("iOS", "ios")],
                    value="adb",
                    label="设备类型",
                    info="选择您的设备类型"
                )
                
                gr.Markdown("### 模型 API 配置")
                
                with gr.Row():
                    with gr.Column(scale=2):
                        base_url = gr.Textbox(
                            label="Base URL",
                            value=os.getenv("PHONE_AGENT_BASE_URL", "http://localhost:8000/v1"),
                            placeholder="http://localhost:8000/v1",
                            info="模型服务的 API 地址"
                        )
                    
                    with gr.Column(scale=2):
                        model_name = gr.Textbox(
                            label="模型名称",
                            value=os.getenv("PHONE_AGENT_MODEL", "autoglm-phone-9b"),
                            placeholder="autoglm-phone-9b 或 doubao-1-5-ui-tars-250428",
                            info="要使用的模型名称"
                        )
                
                with gr.Row():
                    model_type = gr.Radio(
                        choices=[
                            ("自动检测", "auto"),
                            ("AutoGLM", "autoglm"),
                            ("UI-TARS (Doubao)", "uitars"),
                            ("Qwen-VL (Qwen2.5/3-VL)", "qwenvl"),
                            ("MAI-UI (通义)", "maiui"),
                            ("GUI-Owl (mPLUG)", "guiowl"),
                        ],
                        value="auto",
                        label="模型类型",
                        info="选择模型的 action space 类型，自动检测会根据模型名称判断"
                    )
                
                with gr.Row():
                    with gr.Column(scale=2):
                        api_key = gr.Textbox(
                            label="API Key",
                            value=os.getenv("PHONE_AGENT_API_KEY", ""),
                            placeholder="sk-...",
                            type="password",
                            info="API 密钥（如果需要）"
                        )
                    
                    with gr.Column(scale=1):
                        max_steps = gr.Slider(
                            minimum=1,
                            maximum=200,
                            value=int(os.getenv("PHONE_AGENT_MAX_STEPS", "100")),
                            step=1,
                            label="最大步数",
                            info="单个任务最大执行步数"
                        )
                
                with gr.Row():
                    prompt_lang = gr.Radio(
                        choices=[("中文", "cn"), ("English", "en")],
                        value=os.getenv("PHONE_AGENT_LANG", "cn"),
                        label="Prompt 语言",
                        info="System Prompt 使用的语言，影响模型的思考和输出语言"
                    )
                
                gr.Markdown("### iOS 专属配置")
                
                with gr.Row():
                    wda_url = gr.Textbox(
                        label="WebDriverAgent URL",
                        value=os.getenv("PHONE_AGENT_WDA_URL", "http://localhost:8100"),
                        placeholder="http://localhost:8100",
                        info="iOS 设备的 WDA 服务地址"
                    )
                
                gr.Markdown("### 设备 ID（可选）")
                
                with gr.Row():
                    device_id = gr.Textbox(
                        label="设备 ID",
                        value=os.getenv("PHONE_AGENT_DEVICE_ID", ""),
                        placeholder="留空自动选择第一个设备",
                        info="指定设备 ID（多设备时使用）"
                    )
                
                gr.Markdown("### 🧠 记忆系统配置")
                
                with gr.Row():
                    memory_user_id_config = gr.Textbox(
                        label="用户 ID",
                        value=os.getenv("PHONE_AGENT_USER_ID", "default"),
                        placeholder="default",
                        info="不同用户 ID 对应独立的记忆库，用于多用户场景"
                    )

                gr.Markdown("---")
                with gr.Row():
                    save_config_btn = gr.Button("💾 保存配置到 .env", variant="primary")
                    config_save_status = gr.Markdown("")
            
            # ==================== 设备管理 Tab ====================
            with gr.Tab("📱 设备管理"):
                with gr.Row():
                    with gr.Column(scale=1):
                        gr.Markdown("### 已连接设备")
                        device_list_output = gr.Markdown("点击刷新查看设备列表")
                        refresh_devices_btn = gr.Button("🔄 刷新设备列表", variant="primary")
                    
                    with gr.Column(scale=1):
                        gr.Markdown("### 远程连接")
                        connect_address = gr.Textbox(
                            label="设备地址",
                            placeholder="192.168.1.100:5555",
                            info="输入远程设备的 IP:端口"
                        )
                        
                        with gr.Row():
                            connect_btn = gr.Button("🔌 连接", variant="primary")
                            disconnect_btn = gr.Button("⛔ 断开", variant="stop")
                        
                        connect_output = gr.Markdown("")
                
                gr.Markdown("### WiFi 调试")
                
                with gr.Row():
                    wifi_port = gr.Number(
                        label="调试端口",
                        value=5555,
                        precision=0,
                        info="TCP/IP 调试端口"
                    )
                    enable_wifi_btn = gr.Button("📡 启用 WiFi 调试", variant="secondary")
                
                wifi_output = gr.Markdown("")
                
                # 事件绑定
                refresh_devices_btn.click(
                    fn=get_device_list,
                    inputs=[device_type],
                    outputs=[device_list_output]
                )
                
                connect_btn.click(
                    fn=connect_device,
                    inputs=[connect_address, device_type],
                    outputs=[connect_output]
                )
                
                disconnect_btn.click(
                    fn=disconnect_device,
                    inputs=[connect_address, device_type],
                    outputs=[connect_output]
                )
                
                enable_wifi_btn.click(
                    fn=enable_wifi_debug,
                    inputs=[wifi_port, device_type],
                    outputs=[wifi_output]
                )
            
            # ==================== 系统检查 Tab ====================
            with gr.Tab("🔍 系统检查"):
                gr.Markdown("### 系统环境检查")
                gr.Markdown("检查所有必要组件是否正确安装和配置")
                
                run_check_btn = gr.Button("🚀 运行完整检查", variant="primary", size="lg")
                
                check_output = gr.Markdown("点击按钮开始检查...")
                
                gr.Markdown("### 单项检查")
                
                with gr.Row():
                    check_tool_btn = gr.Button("🔧 检查工具安装")
                    check_device_btn = gr.Button("📱 检查设备连接")
                    check_keyboard_btn = gr.Button("⌨️ 检查输入法")
                    check_api_btn = gr.Button("🌐 检查 API 连接")
                
                single_check_output = gr.Markdown("")
                
                # 事件绑定
                run_check_btn.click(
                    fn=run_full_check,
                    inputs=[device_type, base_url, api_key, model_name, wda_url],
                    outputs=[check_output]
                )
                
                check_tool_btn.click(
                    fn=check_tool_installation,
                    inputs=[device_type],
                    outputs=[single_check_output]
                )
                
                check_device_btn.click(
                    fn=check_device_connection,
                    inputs=[device_type],
                    outputs=[single_check_output]
                )
                
                check_keyboard_btn.click(
                    fn=check_keyboard_installation,
                    inputs=[device_type],
                    outputs=[single_check_output]
                )
                
                check_api_btn.click(
                    fn=check_model_api,
                    inputs=[base_url, api_key, model_name],
                    outputs=[single_check_output]
                )
            
            # ==================== 记忆管理 Tab ====================
            with gr.Tab("🧠 记忆管理"):
                gr.Markdown("""
                ### 🧠 双核记忆系统

                | 存储 | 技术 | 内容 |
                |------|------|------|
                | **FAISS 向量库** | Sentence Embedding | 个性化偏好、联系人、应用习惯 |
                | **Neo4j 图谱** | 知识图谱 | UI 状态图、动作轨迹、任务模式 |

                **功能特点**：
                - 🎯 自动学习常用联系人和应用偏好
                - 📝 记录任务历史和执行模式
                - 🔍 语义搜索相关记忆
                - 🔄 自动去重避免冗余
                - 🗺️ Neo4j 图谱记录完整动作轨迹（需人工审核后提交）
                """)
                
                with gr.Row():
                    memory_user_id = gr.Textbox(
                        label="用户 ID",
                        value="default",
                        placeholder="输入用户标识",
                        info="不同用户 ID 对应不同的记忆库"
                    )
                    refresh_stats_btn = gr.Button("🔄 刷新统计", variant="primary")
                
                memory_stats_output = gr.Markdown("点击「刷新统计」查看记忆系统状态")
                
                gr.Markdown("---")
                gr.Markdown("### 添加用户偏好")
                
                with gr.Row():
                    with gr.Column(scale=3):
                        preference_input = gr.Textbox(
                            label="偏好内容",
                            placeholder="例如：喜欢使用深色模式、常用外卖平台是美团...",
                            lines=2
                        )
                    with gr.Column(scale=1):
                        preference_category = gr.Dropdown(
                            label="类别",
                            choices=["general", "app", "contact", "habit", "ui"],
                            value="general"
                        )
                        preference_importance = gr.Slider(
                            label="重要性",
                            minimum=0.1,
                            maximum=1.0,
                            value=0.6,
                            step=0.1
                        )
                
                add_preference_btn = gr.Button("➕ 添加偏好", variant="secondary")
                add_preference_output = gr.Markdown("")
                
                gr.Markdown("---")
                gr.Markdown("### 搜索记忆")
                
                with gr.Row():
                    search_query = gr.Textbox(
                        label="搜索内容",
                        placeholder="输入关键词搜索相关记忆...",
                        scale=3
                    )
                    search_top_k = gr.Slider(
                        label="结果数量",
                        minimum=1,
                        maximum=20,
                        value=5,
                        step=1,
                        scale=1
                    )
                
                search_btn = gr.Button("🔍 搜索", variant="secondary")
                search_output = gr.Markdown("")
                
                gr.Markdown("---")
                gr.Markdown("### 数据管理")
                
                with gr.Row():
                    export_btn = gr.Button("📤 导出记忆", variant="secondary")
                    import_btn = gr.Button("📥 导入记忆", variant="secondary")
                    clear_btn = gr.Button("🗑️ 清除所有", variant="stop")
                
                export_output = gr.Markdown("")
                export_json = gr.Textbox(
                    label="JSON 数据",
                    placeholder="导出的 JSON 数据将显示在这里，也可粘贴 JSON 进行导入",
                    lines=10,
                    visible=True
                )
                
                # 事件绑定
                refresh_stats_btn.click(
                    fn=get_memory_stats,
                    inputs=[memory_user_id],
                    outputs=[memory_stats_output]
                )
                
                add_preference_btn.click(
                    fn=add_user_preference,
                    inputs=[memory_user_id, preference_input, preference_category, preference_importance],
                    outputs=[add_preference_output]
                )
                
                search_btn.click(
                    fn=search_memories,
                    inputs=[memory_user_id, search_query, search_top_k],
                    outputs=[search_output]
                )
                
                export_btn.click(
                    fn=export_memories_json,
                    inputs=[memory_user_id],
                    outputs=[export_output, export_json]
                )
                
                import_btn.click(
                    fn=import_memories_json,
                    inputs=[memory_user_id, export_json],
                    outputs=[export_output]
                )
                
                clear_btn.click(
                    fn=clear_all_memories,
                    inputs=[memory_user_id],
                    outputs=[export_output]
                )

            # ==================== 知识图谱 Tab ====================
            with gr.Tab("🗺️ 知识图谱"):
                gr.Markdown("""
                ### 🗺️ Neo4j 知识图谱

                知识图谱记录 Agent 执行的完整轨迹（状态-动作-状态链），
                每次成功任务后保存到「待审核」，人工审核后提交到图谱。
                未来相似任务可直接复用参考轨迹，实现自我进化。

                **三层匹配策略**：
                - 🔗 **Graph MD5 精确匹配** → 状态完全一致，触发快捷导航
                - 🔍 **Graph 任务语义匹配** → 跨 APP 找相似轨迹，注入参考动作序列
                - 📚 **FAISS 向量 fallback** → 补充探索上下文，永不触发 Navigate
                """)

                with gr.Row():
                    graph_status_output = gr.Markdown("🔄 点击「刷新状态」获取 Neo4j 连接信息")
                    refresh_graph_btn = gr.Button("🔄 刷新状态", variant="primary", scale=0)

                gr.Markdown("---")
                gr.Markdown("### 🔍 轨迹搜索")

                with gr.Row():
                    graph_search_query = gr.Textbox(
                        label="搜索关键词",
                        placeholder="例如：京东外卖、KFC、蓝牙耳机...",
                        scale=3
                    )
                    graph_search_btn = gr.Button("🔍 搜索相似轨迹", variant="secondary", scale=1)

                graph_search_output = gr.Markdown("搜索结果将显示在这里，可查看完整参考轨迹")
                graph_search_btn.click(
                    fn=search_graph_trajectories,
                    inputs=[graph_search_query],
                    outputs=[graph_search_output]
                )

                gr.Markdown("---")
                gr.Markdown("### 📋 已提交轨迹（Neo4j）")

                with gr.Row():
                    list_trajectories_btn = gr.Button("📋 查看已提交轨迹", variant="secondary")
                    list_pending_btn = gr.Button("⏳ 查看待审核轨迹", variant="secondary")

                graph_list_output = gr.Markdown("点击上方按钮查看轨迹列表")
                list_trajectories_btn.click(
                    fn=list_graph_trajectories,
                    inputs=[memory_user_id],
                    outputs=[graph_list_output]
                )
                list_pending_btn.click(
                    fn=list_pending_trajectories,
                    inputs=[memory_user_id],
                    outputs=[graph_list_output]
                )

                gr.Markdown("---")
                gr.Markdown("### ✅ 提交待审核轨迹")

                with gr.Row():
                    commit_index = gr.Number(
                        label="轨迹编号",
                        value=0,
                        precision=0,
                        info="在上方「待审核轨迹」列表中查看编号"
                    )
                    commit_btn = gr.Button("✅ 提交到 Neo4j", variant="primary")

                commit_output = gr.Markdown("")
                commit_btn.click(
                    fn=commit_graph_trajectory,
                    inputs=[memory_user_id, commit_index],
                    outputs=[commit_output]
                )

                gr.Markdown("---")
                gr.Markdown("""
                ### 📖 使用流程

                1. **执行任务** → 在「对话控制」Tab 执行任务，轨迹自动保存
                2. **查看待审核** → 点击「⏳ 查看待审核轨迹」，确认步骤是否正确
                3. **提交到图谱** → 输入编号，点击「✅ 提交到 Neo4j」
                4. **复用轨迹** → 未来相似任务自动匹配，参考轨迹注入 VLM 上下文
                """)

                # 页面加载时自动刷新状态
                refresh_graph_btn.click(
                    fn=refresh_neo4j_stats,
                    inputs=[memory_user_id],
                    outputs=[graph_status_output]
                )

                # ==================== 对话控制 Tab ====================
            with gr.Tab("💬 对话控制"):
                with gr.Row():
                    # 左侧：输入和日志
                    with gr.Column(scale=3):
                        gr.Markdown("### 任务输入")
                        task_input = gr.Textbox(
                            label="任务描述",
                            placeholder="例如：打开微信，发送消息给张三说'你好'",
                            lines=3,
                            info="用自然语言描述您想让 AI 执行的任务"
                        )
                        
                        with gr.Row():
                            start_btn = gr.Button("▶️ 开始执行", variant="primary", scale=2)
                            stop_btn = gr.Button("⏹️ 停止", variant="stop", scale=1)
                            continue_btn = gr.Button("⏩ 继续执行", variant="secondary", scale=1)
                            new_btn = gr.Button("🔄 新对话", variant="secondary", scale=1)

                        takeover_reply_input = gr.Textbox(
                            label="人工介入回复（可选）",
                            placeholder="Agent 提问时（Interact/澄清），在此输入答案后点「⏩ 继续执行」",
                            lines=1,
                        )

                        gr.Markdown("### 💭 AI 思考过程")
                        thinking_output = gr.Markdown(
                            "",
                            elem_classes=["thinking-box"]
                        )
                        
                        gr.Markdown("### 🎯 动作执行日志")
                        action_output = gr.Markdown(
                            "",
                            elem_classes=["action-box"]
                        )
                    
                    # 右侧：截图预览
                    with gr.Column(scale=2):
                        gr.Markdown("### 📱 设备截图")
                        screenshot_display = gr.Image(
                            label="实时截图",
                            type="pil",
                            elem_classes=["screenshot-container"],
                        )
                        
                        with gr.Row():
                            refresh_screenshot_btn = gr.Button("🔄 刷新截图", size="sm")
                            auto_refresh = gr.Checkbox(
                                label="自动刷新 (2秒)",
                                value=False,
                                info="自动定时刷新截图"
                            )

                        graph_panel_output = gr.Markdown(
                            GRAPH_PANEL_PLACEHOLDER,
                            elem_classes=["action-box"],
                        )

                # 事件绑定
                start_btn.click(
                    fn=execute_task,
                    inputs=[
                        task_input, device_type, device_id,
                        base_url, api_key, model_name,
                        max_steps, wda_url, model_type,
                        memory_user_id_config,  # 用户 ID（记忆系统）
                        prompt_lang  # Prompt 语言 (cn/en)
                    ],
                    outputs=[thinking_output, action_output, screenshot_display, graph_panel_output, start_btn]
                )

                stop_btn.click(
                    fn=stop_task,
                    outputs=[action_output]
                )

                continue_btn.click(
                    fn=continue_after_takeover,
                    inputs=[takeover_reply_input],
                    outputs=[action_output]
                )

                new_btn.click(
                    fn=new_conversation,
                    outputs=[task_input, thinking_output, action_output, screenshot_display, graph_panel_output]
                )
                
                refresh_screenshot_btn.click(
                    fn=refresh_screenshot,
                    inputs=[device_type, device_id, wda_url],
                    outputs=[screenshot_display]
                )

                # 自动刷新定时器 (2秒)
                auto_refresh_timer = gr.Timer(value=2, active=False)
                
                def toggle_auto_refresh(enabled):
                    return gr.Timer(active=enabled)
                
                auto_refresh.change(
                    fn=toggle_auto_refresh,
                    inputs=[auto_refresh],
                    outputs=[auto_refresh_timer]
                )
                
                auto_refresh_timer.tick(
                    fn=refresh_screenshot,
                    inputs=[device_type, device_id, wda_url],
                    outputs=[screenshot_display]
                )
            
            # ==================== 帮助文档 Tab ====================
            with gr.Tab("📖 帮助"):
                gr.Markdown("""
                # ClawGUI-Agent Web UI 使用指南
                
                ## 🚀 快速开始
                
                1. **配置模型 API**：在「配置管理」页面设置模型服务地址和密钥
                2. **连接设备**：确保手机已通过 USB 连接，并启用开发者调试
                3. **运行检查**：在「系统检查」页面验证所有组件状态
                4. **开始使用**：在「对话控制」页面输入任务，点击开始执行
                
                ## 🤖 支持的模型
                
                ### AutoGLM (默认)
                - 模型名称：`autoglm-phone-9b`
                - Action Space：`do(action="Tap", element=[x, y])` 格式
                - 适用于本地部署的 AutoGLM 模型
                
                ### UI-TARS (Doubao)
                - 模型名称：`doubao-1-5-ui-tars-250428`
                - Action Space：`click(point='<point>x y</point>')` 格式
                - 适用于火山引擎的 Doubao-1.5-UI-TARS 模型
                - API 地址：`https://ark.cn-beijing.volces.com/api/v3`
                - 需要在火山引擎控制台获取 API Key
                
                ### Qwen-VL (Qwen2.5-VL / Qwen3-VL)
                - 模型名称：`Qwen2.5-VL-72B-Instruct`、`Qwen3-VL-32B` 等
                - Action Space：`tap(x, y)`、`swipe(x1, y1, x2, y2)` 格式
                - 适用于阿里云/vLLM/Ollama 部署的 Qwen-VL 系列模型
                - 支持阿里云 DashScope API 或本地 vLLM 部署
                - 阿里云 API：`https://dashscope.aliyuncs.com/compatible-mode/v1`
                
                ### GLM-4V (GLM-4.6V / GLM-4.1V)
                - 模型名称：`GLM-4.6V-flash`、`GLM-4.1V-9B-thinking` 等
                - Action Space：`do(action="Tap", element=[x, y])` 格式（与 AutoGLM 相同）
                - 适用于智谱 GLM-4V 系列视觉语言模型
                - 支持 vLLM/transformers 本地部署
                - **自动使用 AutoGLM 适配器**，无需单独选择模型类型
                
                ### MAI-UI (通义 MAI-Mobile)
                - 模型名称：`MAI-UI`、`MAI-Mobile` 等
                - Action Space：`{"action": "click", "coordinate": [x, y]}` JSON 格式
                - 基于阿里云通义 [MAI-UI](https://github.com/Tongyi-MAI/MAI-UI) 项目
                - 坐标系统：0-999 归一化坐标
                - 输出格式：`<thinking>...</thinking><tool_call>...</tool_call>`
                - 支持 click、long_press、type、swipe、open、drag、system_button、wait、terminate、answer 动作
                - 智谱 API：`https://open.bigmodel.cn/api/paas/v4`
                
                ### GUI-Owl (mPLUG)
                - 模型名称：`GUI-Owl-7B`、`GUI-Owl-32B`、`GUI-Owl-1.5-8B-Instruct` 等
                - Action Space：`{"action": "click", "coordinate": [x, y]}` JSON 格式
                - 基于阿里巴巴通义 [mPLUG/GUI-Owl](https://github.com/X-PLUG/MobileAgent) 项目
                - 坐标系统：默认使用绝对像素坐标（区别于其他模型的归一化坐标）
                - 输出格式：`### Thought ### ... ### Action ### {JSON} ### Description ### ...`
                - 支持 click、long_press、swipe、type、system_button、open、wait、answer、terminate 动作
                - swipe 使用起点/终点坐标对（coordinate + coordinate2）
                - 建议使用 vLLM 部署，参考：`vllm serve GUI-Owl-1.5-8B-Instruct --max-model-len 32768`
                
                > **提示**：选择「自动检测」会根据模型名称自动选择正确的 action space
                
                ## 📱 设备连接指南
                
                ### Android 设备
                1. 在手机设置中启用「开发者选项」
                2. 打开「USB 调试」
                3. 用 USB 线连接电脑，在手机上授权调试
                4. 安装 ADB Keyboard 输入法（用于中文输入）
                
                ### iOS 设备
                1. 使用 Xcode 运行 WebDriverAgent
                2. 设置端口转发：`iproxy 8100 8100`
                3. 在配置中设置 WDA URL
                
                ### HarmonyOS 设备
                1. 启用开发者模式和 USB 调试
                2. 安装 HDC 工具
                3. 连接设备并授权
                
                ## 💡 使用技巧
                
                - **任务描述**：尽量具体清晰，例如「打开微信，搜索联系人张三，发送消息：明天见」
                - **中断任务**：如果 AI 执行出错，可以点击「停止」按钮中断
                - **查看进度**：思考过程和动作日志会实时显示 AI 的决策过程
                - **截图刷新**：可以手动刷新或开启自动刷新查看设备屏幕
                - **模型选择**：如果使用火山引擎的 UI-TARS 模型，请选择对应的模型类型
                
                ## ⚠️ 注意事项
                
                - 确保手机屏幕保持常亮，不要锁屏
                - 敏感操作（如支付）可能会被阻止截图
                - 建议在任务执行时不要手动操作手机
                - UI-TARS 和 Qwen-VL 模型建议使用 temperature=0 以获得稳定输出
                - Qwen-VL 模型支持更长的上下文，适合复杂多步骤任务
                
                ## 🖥️ 本地部署小模型须知
                
                如果你使用本地部署的小模型（如 ui-tars-1.5-7b、qwen3-vl-8b-instruct），可能会遇到定位不准确的问题。这是因为：
                
                1. **模型能力限制**：7B/8B 级别的小模型在 GUI grounding 任务上的能力不如大模型（72B+）
                2. **分辨率信息**：本地部署需要正确传递屏幕分辨率信息，我们已自动处理
                
                ### 改善建议
                
                - **使用更大的模型**：如 qwen2.5-vl-32b 或 qwen2.5-vl-72b
                - **降低推理参数**：设置 temperature=0，top_p=0.7
                - **vLLM 部署优化**：确保使用最新版本的 vLLM，正确配置多模态处理
                - **云端 API 备选**：如果本地模型效果不佳，可使用阿里云/火山引擎的 API
                
                > **提示**：系统会自动将屏幕分辨率信息传递给模型，帮助改善坐标准确性
                
                ## 🧠 个性化记忆系统
                
                ClawGUI-Agent 内置了个性化记忆系统，参考了 [TeleMem](https://github.com/TeleAI-UAGI/TeleMem) 的设计理念：
                
                ### 功能特点
                
                - **自动学习**：Agent 会自动学习您的常用联系人、应用和操作习惯
                - **语义去重**：相似的记忆会自动合并，避免冗余存储
                - **上下文增强**：执行任务时会自动检索相关记忆，提供个性化服务
                - **持久化存储**：记忆保存在本地，重启后仍然有效
                
                ### 使用方法
                
                1. 在「记忆管理」页面可以查看和管理所有记忆
                2. 手动添加用户偏好，帮助 Agent 更好地理解您
                3. 搜索功能可以查找相关记忆
                4. 支持导入/导出记忆数据
                
                ### 记忆类型
                
                - **用户偏好**：您的个人喜好和习惯
                - **联系人**：常用的联系人信息
                - **应用使用**：常用应用和使用频率
                - **任务历史**：成功完成的任务记录
                - **用户纠正**：您对 Agent 的纠正反馈
                
                > 💡 **提示**：记忆系统会随着使用自动变得更智能，无需手动配置

                ## 🗺️ 知识图谱自我进化

                ClawGUI-Agent 内置了 **Neo4j 知识图谱**，实现 Agent 的自我进化：

                ### 架构：双核记忆

                | 存储 | 技术 | 作用 |
                |------|------|------|
                | **FAISS 向量库** | Sentence Embedding | 个性化偏好、联系人、使用习惯 |
                | **Neo4j 图谱** | 知识图谱 + MD5 哈希 | UI 状态图、动作轨迹、任务模式 |

                ### Neo4j 图谱结构

                ```
                TaskTarget（任务节点）
                  └── STARTS_AT → UIState（起始状态）
                  └── ENDS_AT   → UIState（结束状态）

                UIState（UI 状态节点）
                  └── NEXT_ACTION → Action（动作） → PRODUCES → UIState
                ```

                ### 三层匹配策略

                1. **🔗 Graph MD5 精确匹配**：截图 MD5 完全一致 → 触发快捷导航（直接执行历史动作序列）
                2. **🔍 Graph 任务语义匹配**：n-gram 关键词跨 APP 匹配相似任务 → 注入完整参考轨迹到 VLM
                3. **📚 FAISS 向量 fallback**：补充个性化偏好上下文

                ### 自我进化流程

                ```
                任务执行成功
                    ↓
                end_task() → pending_trajectories.json（不自动提交！）
                    ↓
                人工审核轨迹步骤是否正确
                    ↓
                review_trajectories.py 或 WebUI「🗺️ 知识图谱」Tab → 提交到 Neo4j
                    ↓
                未来相似任务 → find_similar_tasks() → get_task_trajectory()
                    ↓
                参考轨迹注入 VLM 上下文，引导 Agent 执行
                ```

                ### 关键设计：人工审核门

                **不自动提交到 Neo4j** —— 因为 Agent 不是每次都走最优路径。
                只有人工审核确认后的轨迹才进入图谱，避免污染知识库。

                ### 使用方法

                1. 在「🗺️ 知识图谱」Tab 可查看连接状态、搜索相似轨迹
                2. 执行任务后，在同一 Tab 查看「待审核轨迹」
                3. 确认步骤正确后，输入编号提交到 Neo4j
                4. 未来相似任务执行时，日志区会显示「🗺️ 知识图谱匹配」和参考轨迹

                ## 🔗 更多资源
                
                - [项目 GitHub](https://github.com/THUDM/Open-AutoGLM)
                - [TeleMem 记忆系统](https://github.com/TeleAI-UAGI/TeleMem)
                - [ADB Keyboard 下载](https://github.com/senzhk/ADBKeyBoard)
                - [WebDriverAgent 文档](https://github.com/appium/WebDriverAgent)
                - [Doubao-1.5-UI-TARS 文档](https://www.volcengine.com/docs/82379/1536429)
                """)
        
        # 页脚
        gr.HTML("""
        <div style="text-align: center; padding: 20px; color: #666; border-top: 1px solid #eee; margin-top: 20px;">
            <p>ClawGUI-Agent Web UI | Powered by Gradio</p>
        </div>
        """)
    
        # 配置保存事件
        save_config_btn.click(
            fn=save_config_to_env,
            inputs=[
                base_url, model_name, api_key, max_steps,
                device_type, prompt_lang, memory_user_id_config,
                wda_url, device_id, model_type,
            ],
            outputs=[config_save_status],
        )

    return demo


# ==================== 主函数 ====================
def main():
    """主入口"""
    import argparse
    
    parser = argparse.ArgumentParser(description="ClawGUI-Agent Web UI")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="服务器地址")
    parser.add_argument("--port", type=int, default=7860, help="服务器端口")
    parser.add_argument("--share", action="store_true", help="创建公共链接")
    parser.add_argument("--auth", type=str, help="认证信息，格式: username:password")
    
    args = parser.parse_args()
    
    # 创建界面
    demo = create_ui()
    
    # 解析认证信息
    auth = None
    if args.auth:
        parts = args.auth.split(":")
        if len(parts) == 2:
            auth = (parts[0], parts[1])
    
    print(f"""
╔══════════════════════════════════════════════════════╗
║         🤖 ClawGUI-Agent Web UI                  ║
║                                                      ║
║   启动中...                                          ║
║   地址: http://{args.host}:{args.port}                      ║
║                                                      ║
╚══════════════════════════════════════════════════════╝
    """)
    
    # 启动服务
    demo.launch(
        server_name=args.host,
        server_port=args.port,
        share=args.share,
        auth=auth,
        show_error=True,
    )


if __name__ == "__main__":
    main()
