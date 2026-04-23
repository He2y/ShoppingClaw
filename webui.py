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
load_dotenv()  # must be before any os.getenv() calls used by Gradio component defaults
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
class StreamingAgent:
    """支持流式输出的 Agent 包装器"""
    
    def __init__(
        self,
        model_config: ModelConfig,
        agent_config: AgentConfig | IOSAgentConfig,
        device_type: DeviceType,
        model_type: str = "auto",  # 新增：模型类型 (auto/autoglm/uitars)
        user_id: str = "default",  # 用户 ID 用于记忆
    ):
        self.model_config = model_config
        self.agent_config = agent_config
        self.device_type = device_type
        self._context: list[dict[str, Any]] = []
        self._step_count = 0
        self._should_stop = False
        
        # 初始化模型客户端
        self.client = OpenAI(base_url=model_config.base_url, api_key=model_config.api_key)
        
        # 初始化记忆管理器
        self.memory_manager = None
        if HAS_MEMORY:
            try:
                self.memory_manager = get_memory_manager(user_id)
            except Exception as e:
                print(f"记忆系统初始化失败: {e}")
        
        # 确定模型类型并获取适配器
        if model_type == "auto":
            self._model_type = detect_model_type(model_config.model_name)
        elif model_type == "uitars":
            self._model_type = ModelType.UITARS
        elif model_type == "qwenvl":
            self._model_type = ModelType.QWENVL
        elif model_type == "maiui":
            self._model_type = ModelType.MAIUI
        elif model_type == "guiowl":
            self._model_type = ModelType.GUIOWL
        else:
            self._model_type = ModelType.AUTOGLM
        
        self._adapter = get_adapter(self._model_type)
        
        self._is_uitars = self._model_type == ModelType.UITARS
        self._is_qwenvl = self._model_type == ModelType.QWENVL
        self._is_maiui = self._model_type == ModelType.MAIUI
        self._is_guiowl = self._model_type == ModelType.GUIOWL
        
        # 保存原始任务用于 UI-TARS
        self._original_task = ""
        self._task_success = False
    
    def stop(self):
        """停止执行"""
        self._should_stop = True
    
    def reset(self):
        """重置状态"""
        self._context = []
        self._step_count = 0
        self._should_stop = False
        self._task_success = False
    
    def _prepare_message_for_print(self, message: dict) -> dict:
        """准备消息用于打印，移除base64图片数据以便显示"""
        import copy
        msg_copy = copy.deepcopy(message)
        
        if "content" in msg_copy:
            if isinstance(msg_copy["content"], list):
                for item in msg_copy["content"]:
                    if isinstance(item, dict) and item.get("type") == "image_url":
                        if "image_url" in item and "url" in item["image_url"]:
                            url = item["image_url"]["url"]
                            if url.startswith("data:image"):
                                # 截断base64数据，只显示前缀
                                item["image_url"]["url"] = url[:50] + "...[truncated]"
        
        return msg_copy
    
    def run_streaming(self, task: str) -> Generator[tuple[str, str, Image.Image | None], None, None]:
        """
        流式执行任务
        
        Yields:
            (thinking_log, action_log, screenshot) 元组
        """
        self._context = []
        self._step_count = 0
        self._should_stop = False
        self._task_success = False
        self._original_task = task  # 保存原始任务
        
        # 清除 adapter 操作历史（QwenVL / GUI-Owl 使用）
        if hasattr(self._adapter, 'clear_history'):
            self._adapter.clear_history()
        
        # 🧠 记忆系统：任务开始
        if self.memory_manager:
            self.memory_manager.start_task(task)
        
        thinking_log = ""
        action_log = ""
        
        # 显示使用的模型类型
        if self._is_uitars:
            model_type_name = "UI-TARS"
        elif self._is_qwenvl:
            model_type_name = "Qwen-VL"
        elif self._is_maiui:
            model_type_name = "MAI-UI"
        elif self._is_guiowl:
            model_type_name = "GUI-Owl"
        else:
            model_type_name = "AutoGLM"
        action_log += f"🤖 使用模型适配器: **{model_type_name}**\n"
        
        # 显示记忆系统状态
        if self.memory_manager:
            action_log += f"🧠 记忆系统: **已启用** (用户: {self.memory_manager.user_id})\n"
            # 显示检索到的相关记忆
            try:
                context = self.memory_manager.get_relevant_context(task)
                if context:
                    action_log += f"\n📋 **检索到的用户记忆:**\n```\n{context}\n```\n"
                else:
                    action_log += f"📋 暂无相关记忆\n"
            except Exception as e:
                action_log += f"⚠️ 记忆检索失败: {e}\n"

            # 🗺️ 知识图谱：三层匹配诊断（仅打印，不阻塞）
            try:
                from phone_agent.memory.graph_store import GraphStore
                gs = GraphStore()
                if gs.driver:
                    # Task semantic search
                    similar = gs.find_similar_tasks(task, top_k=1)
                    if similar:
                        best = similar[0]
                        traj = gs.get_task_trajectory(best.get("task_id", ""))
                        steps = traj.get("steps", [])
                        action_log += (
                            f"\n🗺️ **知识图谱匹配** [{best.get('app','?')}] "
                            f"「{best.get('description','')[:40]}」\n"
                        )
                        if steps:
                            action_log += "📋 **参考轨迹**（将注入 VLM 上下文）:\n"
                            for s in steps[:5]:
                                action_log += f"  {s['step']}. {s['action_type']} → {s['action_target'][:40]}\n"
                            if len(steps) > 5:
                                action_log += f"  ... (共 {len(steps)} 步)\n"
                    gs.close()
            except Exception as e:
                pass  # 图谱检索不影响主流程
        
        # 定义 takeover 回调函数（用于人工介入场景）
        def takeover_callback(message: str) -> None:
            """WebUI 的 takeover 回调：设置状态并等待用户继续"""
            global app_state
            app_state.waiting_for_takeover = True
            app_state.takeover_message = message
            app_state.takeover_continue_event = threading.Event()
            # 等待用户点击"继续执行"按钮
            app_state.takeover_continue_event.wait()
            # 重置状态
            app_state.waiting_for_takeover = False
            app_state.takeover_message = ""
            app_state.takeover_continue_event = None
        
        # 初始化 action handler
        if self._is_uitars:
            # UI-TARS 使用专用的 action handler
            action_handler = UITarsActionHandler(
                device_id=self.agent_config.device_id,
                takeover_callback=takeover_callback,
            )
        elif self._is_qwenvl:
            # Qwen-VL 使用专用的 action handler
            action_handler = QwenVLActionHandler(
                device_id=self.agent_config.device_id,
                takeover_callback=takeover_callback,
            )
        elif self._is_maiui:
            # MAI-UI 使用专用的 action handler
            from phone_agent.actions.handler_maiui import MAIUIActionHandler
            action_handler = MAIUIActionHandler(
                device_id=self.agent_config.device_id,
                takeover_callback=takeover_callback,
            )
        elif self._is_guiowl:
            # GUI-Owl 使用专用的 action handler
            action_handler = GUIOwlActionHandler(
                device_id=self.agent_config.device_id,
                takeover_callback=takeover_callback,
            )
        elif self.device_type == DeviceType.IOS:
            from phone_agent.actions.handler_ios import IOSActionHandler
            action_handler = IOSActionHandler(
                wda_url=self.agent_config.wda_url,
                device_id=self.agent_config.device_id,
                takeover_callback=takeover_callback,
            )
        else:
            from phone_agent.actions import ActionHandler
            action_handler = ActionHandler(
                device_id=self.agent_config.device_id,
                takeover_callback=takeover_callback,
            )
        
        # 获取设备工厂和截图函数
        if self.device_type == DeviceType.IOS:
            from phone_agent.xctest import get_screenshot as ios_get_screenshot
            get_screenshot_func = lambda: ios_get_screenshot(wda_url=self.agent_config.wda_url)
            get_current_app_func = lambda: action_handler.connection.get_current_app() or "Unknown"
        else:
            set_device_type(self.device_type)
            device_factory = get_device_factory()
            get_screenshot_func = lambda: device_factory.get_screenshot(self.agent_config.device_id)
            get_current_app_func = lambda: device_factory.get_current_app(self.agent_config.device_id)
        
        # 执行第一步
        result = yield from self._execute_step_streaming(
            task, True, thinking_log, action_log,
            get_screenshot_func, get_current_app_func, action_handler
        )
        
        if result["finished"]:
            return
        
        thinking_log = result["thinking_log"]
        action_log = result["action_log"]
        
        # 继续执行直到完成或达到最大步数
        while self._step_count < self.agent_config.max_steps and not self._should_stop:
            result = yield from self._execute_step_streaming(
                None, False, thinking_log, action_log,
                get_screenshot_func, get_current_app_func, action_handler
            )
            
            if result["finished"]:
                return
                
            thinking_log = result["thinking_log"]
            action_log = result["action_log"]
        
        if self._should_stop:
            action_log += "\n\n⚠️ 任务已被用户终止"
            # 🧠 记忆系统：任务被终止
            if self.memory_manager:
                self.memory_manager.end_task(success=False, result="用户终止")
            yield thinking_log, action_log, None
    
    def _execute_step_streaming(
        self,
        user_prompt: str | None,
        is_first: bool,
        thinking_log: str,
        action_log: str,
        get_screenshot_func,
        get_current_app_func,
        action_handler,
    ) -> Generator[tuple[str, str, Image.Image | None], None, dict]:
        """执行单个步骤并流式输出"""
        from phone_agent.actions.handler import parse_action, finish
        from phone_agent.config import get_system_prompt
        
        # 导入记忆相关模块
        if HAS_MEMORY:
            from phone_agent.memory.memory_manager import build_personalized_prompt
        
        self._step_count += 1
        
        # 添加步骤标题
        step_header = f"\n\n{'='*50}\n## 步骤 {self._step_count}\n{'='*50}\n"
        thinking_log += step_header
        action_log += step_header
        thinking_log += "\n### 💭 思考过程\n"
        
        # 获取截图
        try:
            screenshot = get_screenshot_func()
            current_app = get_current_app_func()
        except Exception as e:
            action_log += f"\n❌ 获取截图失败: {str(e)}"
            yield thinking_log, action_log, None
            return {"finished": True, "thinking_log": thinking_log, "action_log": action_log}
        
        # 转换截图为 PIL Image
        screenshot_img = None
        if screenshot and screenshot.base64_data:
            try:
                img_data = base64.b64decode(screenshot.base64_data)
                screenshot_img = Image.open(BytesIO(img_data))
            except:
                pass
        
        yield thinking_log, action_log, screenshot_img
        
        # 根据模型类型构建消息
        if self._is_uitars or self._is_qwenvl or self._is_maiui or self._is_guiowl:
            # 记录构建前是否为空（首轮）
            is_first_build = len(self._context) == 0
            
            # UI-TARS、Qwen-VL、MAI-UI、GUI-Owl 使用专用的消息格式
            self._context = self._adapter.build_messages(
                task=self._original_task,
                image_base64=screenshot.base64_data,
                current_app=current_app,
                context=self._context,
                lang=self.agent_config.lang,
                screen_width=screenshot.width,
                screen_height=screenshot.height,
            )
            
            # 🧠 首轮注入个性化记忆上下文（所有非 AutoGLM 模型都需要）
            if is_first_build and self.memory_manager and HAS_MEMORY:
                memory_context = self.memory_manager.get_relevant_context(self._original_task)
                if memory_context:
                    self._inject_memory_into_context(memory_context)
                    action_log += f"\n📋 **检索到的用户记忆:**\n```\n{memory_context}\n```\n"
            
            # 限制上下文中的图片数量
            if self._is_qwenvl or self._is_guiowl:
                # QwenVL / GUI-Owl: 只保留 1 张图片（当前）
                pass
            elif self._is_maiui:
                # MAI-UI: 保留最近 3 张图片
                if hasattr(self._adapter, 'limit_context'):
                    self._context = self._adapter.limit_context(self._context, max_images=3)
            elif hasattr(self._adapter, 'limit_context'):
                # UI-TARS: 保留最近 5 张图片
                self._context = self._adapter.limit_context(self._context, max_images=5)
            
            # # 打印当前构建的 messages
            # print("\n" + "="*80)
            # print("📨 当前 Messages:")
            # print("="*80)
            # import json
            # for msg in self._context:
            #     msg_to_print = self._prepare_message_for_print(msg)
            #     print(json.dumps(msg_to_print, ensure_ascii=False, indent=2))
            # print("="*80 + "\n")
        else:
            # AutoGLM 使用相同的消息格式
            if is_first:
                # 获取基础 system prompt
                base_prompt = get_system_prompt(self.agent_config.lang)
                
                # 🧠 注入个性化记忆上下文
                if self.memory_manager and HAS_MEMORY:
                    system_prompt = build_personalized_prompt(
                        base_prompt, self.memory_manager, user_prompt
                    )
                    # 显示个性化信息
                    context = self.memory_manager.get_relevant_context(user_prompt)
                    if context:
                        action_log_extra = f"\n\n📋 **检索到的用户记忆:**\n```\n{context}\n```\n"
                else:
                    system_prompt = base_prompt
                    action_log_extra = ""
                
                self._context.append(
                    MessageBuilder.create_system_message(system_prompt)
                )
                screen_info = MessageBuilder.build_screen_info(current_app)
                text_content = f"{user_prompt}\n\n{screen_info}"
                self._context.append(
                    MessageBuilder.create_user_message(
                        text=text_content, image_base64=screenshot.base64_data
                    )
                )
            else:
                screen_info = MessageBuilder.build_screen_info(current_app)
                text_content = f"** Screen Info **\n\n{screen_info}"
                self._context.append(
                    MessageBuilder.create_user_message(
                        text=text_content, image_base64=screenshot.base64_data
                    )
                )
            
            # # 打印当前构建的 messages
            # print("\n" + "="*80)
            # print("📨 当前 Messages:")
            # print("="*80)
            # import json
            # for msg in self._context:
            #     msg_to_print = self._prepare_message_for_print(msg)
            #     print(json.dumps(msg_to_print, ensure_ascii=False, indent=2))
            # print("="*80 + "\n")
        
        # 流式请求模型
        yield thinking_log, action_log, screenshot_img
        
        # UI-TARS 使用不同的推理参数
        if self._is_uitars:
            temperature = 0.0  # UI-TARS 建议使用 0
            top_p = 0.7
            frequency_penalty = 0.0
        else:
            temperature = self.model_config.temperature
            top_p = 0.85
            frequency_penalty = 0.2
        
        try:
            stream = self.client.chat.completions.create(
                messages=self._context,
                model=self.model_config.model_name,
                max_tokens=self.model_config.max_tokens,
                temperature=temperature,
                top_p=top_p,
                frequency_penalty=frequency_penalty,
                stream=True,
            )
            
            raw_content = ""
            in_action_phase = False
            # 根据模型类型使用不同的 action 标记
            if self._is_uitars:
                action_markers = ["Action:", "click(", "long_press(", "type(", "scroll(", 
                                  "open_app(", "drag(", "press_home(", "press_back(", 
                                  "finished(", "wait("]
            elif self._is_qwenvl:
                action_markers = ["<tool_call>", '"action":', "Action:", "tap(", "long_press(", "double_tap(", "swipe(",
                                  "type(", "type_name(", "open_app(", "back(", "home(",
                                  "wait(", "finish(", "terminate("]
            elif self._is_maiui:
                # MAI-UI 使用 <tool_call> 格式的 action
                action_markers = ["<tool_call>", '"action":', "terminate", "answer"]
            elif self._is_guiowl:
                # GUI-Owl 1.5 使用 <tool_call> 格式（官方格式）
                action_markers = ["<tool_call>", '"action":', "Action:", "terminate", "answer"]
            else:
                action_markers = ["finish(message=", "do(action="]
            
            pending_content = ""  # 待输出的内容缓冲
            last_yield_time = time.time()
            yield_interval = 0.3  # 300ms 更新一次界面
            
            import re as _re
            def _clean_thinking_tags(text: str) -> str:
                """清理 thinking/think 标签，避免 Markdown 渲染时被当作 HTML 吞掉"""
                return _re.sub(r'</?(?:thinking|think)>', '', text)
            
            for chunk in stream:
                if self._should_stop:
                    break
                    
                if len(chunk.choices) == 0:
                    continue
                
                # Handle reasoning_content (for reasoning models like MAI-UI-2B)
                reasoning_content = getattr(chunk.choices[0].delta, 'reasoning_content', None)
                if reasoning_content is not None:
                    raw_content += reasoning_content
                    if not in_action_phase:
                        pending_content += reasoning_content
                        current_time = time.time()
                        if current_time - last_yield_time >= yield_interval or len(pending_content) > 100:
                            thinking_log += _clean_thinking_tags(pending_content)
                            pending_content = ""
                            last_yield_time = current_time
                            yield thinking_log, action_log, None
                
                if chunk.choices[0].delta.content is not None:
                    content = chunk.choices[0].delta.content
                    raw_content += content
                    
                    if not in_action_phase:
                        # 检查是否进入动作阶段
                        for marker in action_markers:
                            if marker in raw_content:
                                in_action_phase = True
                                break
                        
                        if in_action_phase:
                            # 刚进入 action 阶段，先把缓冲中的 thinking 内容 flush 出去
                            if pending_content:
                                thinking_log += _clean_thinking_tags(pending_content)
                                pending_content = ""
                                yield thinking_log, action_log, None
                        else:
                            pending_content += content
                            # 批量更新：每隔一段时间或内容较多时才更新
                            current_time = time.time()
                            if current_time - last_yield_time >= yield_interval or len(pending_content) > 100:
                                thinking_log += _clean_thinking_tags(pending_content)
                                pending_content = ""
                                last_yield_time = current_time
                                yield thinking_log, action_log, None
            
            # 输出剩余的内容
            if pending_content:
                thinking_log += _clean_thinking_tags(pending_content)
            
        except Exception as e:
            action_log += f"\n❌ 模型请求错误: {str(e)}"
            yield thinking_log, action_log, screenshot_img
            return {"finished": True, "thinking_log": thinking_log, "action_log": action_log}
        
        # 根据模型类型解析响应
        if self._is_uitars:
            # UI-TARS 响应解析
            thinking, action_str = self._adapter.parse_response(raw_content)
            
            # 使用 UI-TARS action handler 解析
            uitars_action = action_handler.parse_response(raw_content)
            
            # 添加屏幕分辨率信息到日志（帮助调试坐标问题）
            action_log += f"\n### 🎯 执行动作\n📐 屏幕分辨率: {screenshot.width}x{screenshot.height}px\n```\nAction: {action_str}\n```\n"
            yield thinking_log, action_log, screenshot_img
            
            # 执行动作
            try:
                result = action_handler.execute(uitars_action, screenshot.width, screenshot.height)
                
                if result.success:
                    # 显示坐标转换信息（帮助调试定位问题）
                    if result.message:
                        action_log += f"\n✅ {result.message}"
                    else:
                        action_log += f"\n✅ 动作执行成功"
                else:
                    action_log += f"\n⚠️ 动作执行: {result.message}"
                    
            except Exception as e:
                action_log += f"\n❌ 动作执行失败: {str(e)}"
                yield thinking_log, action_log, screenshot_img
                return {"finished": True, "thinking_log": thinking_log, "action_log": action_log}
            
            # 移除上下文中的图片（UI-TARS 保留最近 5 张由 limit_context 处理）
            # 这里不需要 remove_images_from_message，因为 limit_context 已经限制了数量
            
            # 添加助手响应到上下文（保留模型的全部输出）
            self._context.append({
                "role": "assistant",
                "content": raw_content
            })
            
            # 检查是否完成
            finished = uitars_action.action_type == "finished" or result.should_finish
        elif self._is_qwenvl:
            # Qwen-VL 响应解析
            thinking, action_str = self._adapter.parse_response(raw_content)
            
            # 使用 Qwen-VL action handler 解析
            qwenvl_action = action_handler.parse_response(raw_content)
            
            # 添加屏幕分辨率信息到日志（帮助调试坐标问题）
            action_log += f"\n### 🎯 执行动作\n📐 屏幕分辨率: {screenshot.width}x{screenshot.height}px\n```\nAction: {action_str}\n```\n"
            yield thinking_log, action_log, screenshot_img
            
            # 执行动作
            try:
                result = action_handler.execute(qwenvl_action, screenshot.width, screenshot.height)
                
                if result.success:
                    action_log += f"\n✅ 动作执行成功"
                else:
                    action_log += f"\n⚠️ 动作执行: {result.message}"
                    
            except Exception as e:
                action_log += f"\n❌ 动作执行失败: {str(e)}"
                yield thinking_log, action_log, screenshot_img
                return {"finished": True, "thinking_log": thinking_log, "action_log": action_log}
            
            # QwenVL: 不添加 assistant 消息到历史
            # 只提取 Action 描述文本，通过 adapter.add_history() 添加到历史
            # 这样下一轮的 user message 会包含这个描述
            if hasattr(qwenvl_action, 'action_desc') and qwenvl_action.action_desc:
                action_description = qwenvl_action.action_desc.strip()
            else:
                # Fallback: 从 raw_content 中提取 Action: 后面的描述文本
                import re
                action_match = re.search(r'Action:\s*"([^"]+)"', raw_content)
                if action_match:
                    action_description = action_match.group(1).strip()
                else:
                    lines = raw_content.split('\n')
                    action_description = ""
                    for line in lines:
                        if line.strip().startswith('Action:'):
                            action_description = line.strip()[7:].strip()
                            action_description = action_description.strip('"').strip("'")
                            break
            
            # 添加到 adapter 的历史记录
            if action_description and hasattr(self._adapter, 'add_history'):
                self._adapter.add_history(action_description)
            
            # 检查是否完成（tool_call 格式用 terminate，旧格式用 finish）
            finished = qwenvl_action.action_type in ("finish", "terminate") or result.should_finish
        elif self._is_maiui:
            # MAI-UI 响应解析
            from phone_agent.actions.handler_maiui import MAIUIActionHandler, convert_maiui_to_autoglm
            
            thinking, action_str = self._adapter.parse_response(raw_content)
            
            # 使用 MAI-UI action handler 解析
            maiui_action = action_handler.parse_response(raw_content)
            
            # 转换为 AutoGLM 格式用于日志显示
            action_for_log = convert_maiui_to_autoglm(maiui_action, screenshot.width, screenshot.height)
            
            # 添加屏幕分辨率信息到日志
            action_log += f"\n### 🎯 执行动作\n📐 屏幕分辨率: {screenshot.width}x{screenshot.height}px\n```json\n{json.dumps(action_for_log, ensure_ascii=False, indent=2)}\n```\n"
            yield thinking_log, action_log, screenshot_img
            
            # 执行动作
            try:
                result = action_handler.execute(maiui_action, screenshot.width, screenshot.height)
                
                if result.success:
                    if result.message:
                        action_log += f"\n✅ {result.message}"
                    else:
                        action_log += f"\n✅ 动作执行成功"
                else:
                    action_log += f"\n⚠️ 动作执行: {result.message}"
                    
            except Exception as e:
                action_log += f"\n❌ 动作执行失败: {str(e)}"
                yield thinking_log, action_log, screenshot_img
                return {"finished": True, "thinking_log": thinking_log, "action_log": action_log}
            
            # 移除上下文中的图片（MAI-UI 保留最近 3 张由 limit_context 处理）
            # 这里不需要 remove_images_from_message，因为 limit_context 已经限制了数量
            
            # 添加助手响应到上下文（MAI-UI 使用纯字符串格式的 assistant 消息）
            self._context.append({
                "role": "assistant",
                "content": raw_content
            })
            
            # 检查是否完成
            finished = maiui_action.action_type in ["terminate", "answer"] or result.should_finish
        elif self._is_guiowl:
            # GUI-Owl 1.5 响应解析（官方 tool_call 格式）
            from phone_agent.actions.handler_guiowl import convert_guiowl_to_autoglm
            
            thinking, action_str = self._adapter.parse_response(raw_content)
            
            # 使用 GUI-Owl action handler 解析
            guiowl_action = action_handler.parse_response(raw_content)
            
            # 转换为 AutoGLM 格式用于日志显示
            action_for_log = convert_guiowl_to_autoglm(guiowl_action, screenshot.width, screenshot.height)
            
            # 添加屏幕分辨率信息到日志
            action_log += f"\n### 🎯 执行动作\n📐 屏幕分辨率: {screenshot.width}x{screenshot.height}px\n```json\n{json.dumps(action_for_log, ensure_ascii=False, indent=2)}\n```\n"
            yield thinking_log, action_log, screenshot_img
            
            # 执行动作
            try:
                result = action_handler.execute(guiowl_action, screenshot.width, screenshot.height)
                
                if result.success:
                    if result.message:
                        action_log += f"\n✅ {result.message}"
                    else:
                        action_log += f"\n✅ 动作执行成功"
                else:
                    action_log += f"\n⚠️ 动作执行: {result.message}"
                    
            except Exception as e:
                action_log += f"\n❌ 动作执行失败: {str(e)}"
                yield thinking_log, action_log, screenshot_img
                return {"finished": True, "thinking_log": thinking_log, "action_log": action_log}
            
            # GUI-Owl（官方格式）: 不添加 assistant 消息到历史
            # 只提取 Action 描述文本，通过 adapter.add_history() 添加到历史
            # 这样下一轮的 user message 会包含 Previous actions 历史
            action_description = ""
            if hasattr(guiowl_action, 'action_desc') and guiowl_action.action_desc:
                action_description = guiowl_action.action_desc.strip()
            elif hasattr(guiowl_action, 'description') and guiowl_action.description:
                action_description = guiowl_action.description.strip()
            else:
                # Fallback: 从 raw_content 中提取 Action: 后面的描述文本
                import re
                action_match_re = re.search(r'Action:\s*"?([^"\n]+)"?', raw_content)
                if action_match_re:
                    action_description = action_match_re.group(1).strip()
            
            # 添加到 adapter 的历史记录
            if action_description and hasattr(self._adapter, 'add_history'):
                self._adapter.add_history(action_description)
            
            # 同步 handler 的 action_history 到 adapter
            if hasattr(action_handler, 'action_history') and hasattr(self._adapter, '_action_history'):
                self._adapter._action_history = list(action_handler.action_history)
            
            # 检查是否完成
            finished = guiowl_action.action_type in ["terminate", "answer"] or result.should_finish
        else:
            # AutoGLM 响应解析
            thinking, action_str = self._parse_response(raw_content)
            
            # 解析动作
            try:
                action = parse_action(action_str)
            except ValueError:
                action = finish(message=action_str)
            
            action_log += f"\n### 🎯 执行动作\n```json\n{json.dumps(action, ensure_ascii=False, indent=2)}\n```\n"
            yield thinking_log, action_log, screenshot_img
            
            # 移除上下文中的图片
            self._context[-1] = MessageBuilder.remove_images_from_message(self._context[-1])
            
            # 检查是否是 Take_over 动作（需要人工介入）
            is_takeover = action.get("action") == "Take_over"
            if is_takeover:
                takeover_msg = action.get("message", "需要用户人工操作")
                action_log += f"\n\n⏸️ **需要人工介入**: {takeover_msg}\n"
                action_log += f"👉 请在手机上完成操作（如登录、验证码等），然后点击 **继续执行** 按钮\n"
                yield thinking_log, action_log, screenshot_img
            
            # 执行动作
            try:
                result = action_handler.execute(action, screenshot.width, screenshot.height)
                
                if result.success:
                    if is_takeover:
                        action_log += f"\n✅ 人工操作已完成，继续执行任务"
                    else:
                        action_log += f"\n✅ 动作执行成功"
                else:
                    action_log += f"\n⚠️ 动作执行: {result.message}"
                    
            except Exception as e:
                action_log += f"\n❌ 动作执行失败: {str(e)}"
                yield thinking_log, action_log, screenshot_img
                return {"finished": True, "thinking_log": thinking_log, "action_log": action_log}
            
            # 添加助手响应到上下文
            self._context.append(
                MessageBuilder.create_assistant_message(
                    f"<think>{thinking}</think><answer>{action_str}</answer>"
                )
            )
            
            # 检查是否完成
            finished = action.get("_metadata") == "finish" or result.should_finish
        
        # 🧠 记忆系统：记录每一步执行
        if self.memory_manager:
            try:
                self.memory_manager.add_step(
                    thinking=thinking,
                    action={"raw": raw_content[-300:]},
                    screenshot_app=current_app,
                )
            except Exception:
                pass  # 记忆追踪失败不影响主流程
        
        if finished:
            action_log += f"\n\n🎉 **任务完成**: {result.message or '已完成'}"
            self._task_success = True
            # 🧠 记忆系统：任务成功完成
            if self.memory_manager:
                self.memory_manager.end_task(
                    success=True,
                    result=result.message or "已完成"
                )
                action_log += f"\n🧠 记忆已更新"
        
        yield thinking_log, action_log, screenshot_img
        
        return {"finished": finished, "thinking_log": thinking_log, "action_log": action_log}
    
    def _inject_memory_into_context(self, memory_context: str):
        """
        将记忆上下文注入到对话的系统/首条消息中。
        
        遍历已构建的消息，找到 system 或第一条含文本的 user 消息，
        将记忆上下文追加到其文本内容末尾。
        支持 content 为 str 或 list[dict] 两种格式。
        """
        for i, msg in enumerate(self._context):
            role = msg.get("role", "")
            content = msg.get("content", "")
            
            if role == "system":
                self._append_to_message(i, content, memory_context)
                return
            
            # 如果没有 system 消息（如 UI-TARS），注入到第一条 user 消息
            if role == "user":
                if isinstance(content, str) and len(content) > 50:
                    self._context[i]["content"] = content + f"\n\n{memory_context}"
                    return
                elif isinstance(content, list):
                    self._append_to_message(i, content, memory_context)
                    return
    
    def _append_to_message(self, msg_idx: int, content, text_to_append: str):
        """将文本追加到消息内容的文本部分（支持 str 和 list 格式）。"""
        if isinstance(content, str):
            self._context[msg_idx]["content"] = content + f"\n\n{text_to_append}"
        elif isinstance(content, list):
            for j, item in enumerate(content):
                if isinstance(item, dict) and item.get("type") == "text":
                    self._context[msg_idx]["content"][j]["text"] = item["text"] + f"\n\n{text_to_append}"
                    return
            # 如果没找到 text 类型的 item，追加一个新的
            self._context[msg_idx]["content"].append({
                "type": "text",
                "text": text_to_append,
            })
    
    def _parse_response(self, content: str) -> tuple[str, str]:
        """解析模型响应"""
        # <answer> 标签优先（因为 <answer> 可能包裹 do()/finish()）
        if "<answer>" in content:
            parts = content.split("<answer>", 1)
            thinking = parts[0].replace("<think>", "").replace("</think>", "").strip()
            action = parts[1].replace("</answer>", "").strip()
            return thinking, action
        
        if "finish(message=" in content:
            parts = content.split("finish(message=", 1)
            thinking = parts[0].strip()
            action = "finish(message=" + parts[1]
            return thinking, action
        
        if "do(action=" in content:
            parts = content.split("do(action=", 1)
            thinking = parts[0].strip()
            action = "do(action=" + parts[1]
            return thinking, action
        
        return "", content


# 全局流式 Agent
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
) -> Generator[tuple[str, str, Image.Image | None, gr.update], None, None]:
    """执行任务并流式输出结果"""
    global streaming_agent, app_state
    
    if not task.strip():
        yield "请输入任务描述", "", None, gr.update(interactive=True)
        return
    
    # 检查是否已有任务在运行
    if app_state.is_running:
        yield "⚠️ 已有任务在运行中，请先停止当前任务", "", None, gr.update(interactive=True)
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
        )
    
    # 创建流式 Agent，传入模型类型和用户 ID（用于记忆系统）
    streaming_agent = StreamingAgent(
        model_config, agent_config, dt,
        model_type=model_type,
        user_id=user_id.strip() or "default"
    )
    
    try:
        # 禁用开始按钮
        yield "", "", None, gr.update(interactive=False)
        
        # 执行任务
        for thinking, action, screenshot in streaming_agent.run_streaming(task):
            if app_state.should_stop:
                break
            yield thinking, action, screenshot, gr.update(interactive=False)
        
    except Exception as e:
        yield f"❌ 执行错误:\n{traceback.format_exc()}", "", None, gr.update(interactive=True)
    finally:
        app_state.is_running = False
        app_state.should_stop = False
        streaming_agent = None
        yield gr.update(), gr.update(), gr.update(), gr.update(interactive=True)


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


def continue_after_takeover():
    """人工操作完成后继续执行"""
    global app_state
    
    if app_state.waiting_for_takeover and app_state.takeover_continue_event:
        app_state.takeover_continue_event.set()
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
    
    return "", "", "", None


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
) -> str:
    """保存配置到 .env 文件"""
    try:
        env_path = Path(".env")

        # 读取现有配置
        existing_lines = []
        if env_path.exists():
            with open(env_path, "r", encoding="utf-8") as f:
                existing_lines = f.readlines()

        # 配置映射
        config_updates = {
            "PHONE_AGENT_BASE_URL": base_url,
            "PHONE_AGENT_MODEL": model_name,
            "PHONE_AGENT_API_KEY": api_key,
            "PHONE_AGENT_MAX_STEPS": str(max_steps),
            "PHONE_AGENT_DEVICE_TYPE": device_type,
            "PHONE_AGENT_LANG": lang,
            "PHONE_AGENT_USER_ID": user_id,
            "PHONE_AGENT_WDA_URL": wda_url,
            "PHONE_AGENT_DEVICE_ID": device_id,
        }

        # 解析现有文件，保留注释和未匹配的行
        updated_keys = set()
        new_lines = []

        for line in existing_lines:
            stripped = line.strip()
            # 检查是否是配置行
            is_config_line = False
            for key in config_updates:
                if stripped.startswith(f"{key}="):
                    new_lines.append(f"{key}={config_updates[key]}\n")
                    updated_keys.add(key)
                    is_config_line = True
                    break
            if not is_config_line:
                new_lines.append(line)

        # 添加新的配置项（未在文件中存在的）
        for key, value in config_updates.items():
            if key not in updated_keys:
                new_lines.append(f"{key}={value}\n")

        # 写入文件
        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)

        return f"✅ 配置已保存到 .env 文件\n\n📋 保存内容:\n- BASE_URL: {base_url}\n- MODEL: {model_name}\n- MAX_STEPS: {max_steps}\n- DEVICE: {device_type}\n\n💡 重新启动应用使配置生效"

    except Exception as e:
        return f"❌ 保存失败: {str(e)}"


def reload_config_from_env() -> dict:
    """从 .env 重新加载配置"""
    load_dotenv(override=True)
    return {
        "base_url": os.getenv("PHONE_AGENT_BASE_URL", "http://localhost:8000/v1"),
        "model_name": os.getenv("PHONE_AGENT_MODEL", "autoglm-phone-9b"),
        "api_key": os.getenv("PHONE_AGENT_API_KEY", ""),
        "max_steps": int(os.getenv("PHONE_AGENT_MAX_STEPS", "100")),
        "device_type": os.getenv("PHONE_AGENT_DEVICE_TYPE", "adb"),
        "lang": os.getenv("PHONE_AGENT_LANG", "cn"),
        "user_id": os.getenv("PHONE_AGENT_USER_ID", "default"),
        "wda_url": os.getenv("PHONE_AGENT_WDA_URL", "http://localhost:8100"),
        "device_id": os.getenv("PHONE_AGENT_DEVICE_ID", ""),
    }


# ==================== 构建 Gradio 界面 ====================
def create_ui():
    """创建 Gradio 界面"""
    
    # ============================================================
    #  CYBERPUNK COMMAND CENTER — custom CSS
    # ============================================================
    custom_css = """
    @import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@400;600;700;900&family=JetBrains+Mono:wght@300;400;500&family=Noto+Sans+SC:wght@300;400;500;700&display=swap');

    :root {
        --bg-void:     #050810;
        --bg-panel:    #0b1120;
        --bg-card:     #0f1629;
        --bg-card2:    #141d35;
        --border-dim:  #1e2d4a;
        --border-glow: #00f5ff44;
        --cyan:        #00f5ff;
        --cyan-dim:    #00f5ff88;
        --cyan-dark:   #0099aa;
        --pink:        #ff006e;
        --pink-dim:    #ff006e88;
        --amber:       #ffb700;
        --green:       #00e676;
        --red:         #ff3d5a;
        --text-primary: #e8edf5;
        --text-dim:    #7a8ba8;
        --text-code:   #a8d8ff;
    }

    /* ---- Base ---- */
    .gradio-container { font-family: 'Noto Sans SC', sans-serif !important; }

    body, .gradio-container, .gradio-container * {
        color: var(--text-primary) !important;
    }

    /* ---- Background grid circuit pattern ---- */
    #root, main, .gradio-blocks {
        background-color: var(--bg-void) !important;
        background-image:
            linear-gradient(var(--border-dim) 1px, transparent 1px),
            linear-gradient(90deg, var(--border-dim) 1px, transparent 1px),
            radial-gradient(ellipse at 50% 0%, #0d1f3c88 0%, transparent 70%);
        background-size: 40px 40px, 40px 40px, 100% 100%;
    }

    /* ---- Custom scrollbar ---- */
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: var(--bg-panel); }
    ::-webkit-scrollbar-thumb { background: var(--cyan-dark); border-radius: 3px; }

    /* ---- Header ---- */
    .claw-header {
        text-align: center;
        padding: 28px 20px 20px;
        position: relative;
    }
    .claw-header::before {
        content: '';
        position: absolute;
        bottom: 0; left: 50%; transform: translateX(-50%);
        width: 60%;
        height: 1px;
        background: linear-gradient(90deg, transparent, var(--cyan), transparent);
    }
    .claw-logo {
        font-family: 'Orbitron', sans-serif;
        font-size: 2.8em;
        font-weight: 900;
        letter-spacing: 4px;
        background: linear-gradient(90deg, var(--cyan) 0%, #a78bfa 50%, var(--pink) 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
        text-shadow: 0 0 40px #00f5ff55;
        margin-bottom: 8px;
    }
    .claw-subtitle {
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.82em;
        color: var(--text-dim);
        letter-spacing: 3px;
        text-transform: uppercase;
    }
    .claw-badge {
        display: inline-block;
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.68em;
        padding: 2px 10px;
        border: 1px solid var(--cyan-dim);
        border-radius: 20px;
        color: var(--cyan);
        margin-top: 8px;
        letter-spacing: 1px;
    }

    /* ---- Panel / Card ---- */
    .claw-panel {
        background: var(--bg-panel) !important;
        border: 1px solid var(--border-dim) !important;
        border-radius: 12px !important;
        padding: 16px 20px !important;
        position: relative;
        overflow: hidden;
    }
    .claw-panel::before {
        content: '';
        position: absolute;
        top: 0; left: 0; right: 0;
        height: 2px;
        background: linear-gradient(90deg, var(--cyan), var(--pink));
        opacity: 0.7;
    }
    .claw-panel-glow {
        box-shadow: 0 0 20px #00f5ff11, inset 0 1px 0 #ffffff08 !important;
    }

    /* ---- Section label ---- */
    .claw-section-label {
        font-family: 'Orbitron', sans-serif;
        font-size: 0.72em;
        font-weight: 600;
        letter-spacing: 3px;
        text-transform: uppercase;
        color: var(--cyan) !important;
        margin-bottom: 12px;
        display: flex;
        align-items: center;
        gap: 8px;
    }
    .claw-section-label::after {
        content: '';
        flex: 1;
        height: 1px;
        background: linear-gradient(90deg, var(--border-dim), transparent);
    }

    /* ---- Tab styling ---- */
    .tab-nav button, .gr-tabs button {
        font-family: 'JetBrains Mono', monospace !important;
        font-size: 0.82em !important;
        letter-spacing: 1px !important;
        color: var(--text-dim) !important;
        border-bottom: 2px solid transparent !important;
        transition: all 0.2s !important;
    }
    .tab-nav button:hover, .gr-tabs button:hover {
        color: var(--text-primary) !important;
        background: #ffffff08 !important;
    }
    .tab-nav button.selected, .gr-tabs button.selected {
        color: var(--cyan) !important;
        border-bottom-color: var(--cyan) !important;
        text-shadow: 0 0 10px var(--cyan-dim) !important;
    }

    /* ---- Input / Textbox ---- */
    .gr-textbox, .gr-number, .gr-dropdown {
        background: var(--bg-card) !important;
        border: 1px solid var(--border-dim) !important;
        border-radius: 8px !important;
        color: var(--text-primary) !important;
        font-family: 'JetBrains Mono', monospace !important;
        font-size: 0.85em !important;
        transition: border-color 0.2s, box-shadow 0.2s !important;
    }
    .gr-textbox:focus, .gr-number:focus, .gr-dropdown:focus {
        border-color: var(--cyan-dark) !important;
        box-shadow: 0 0 0 2px #00f5ff22, 0 0 15px #00f5ff15 !important;
    }
    .gr-textbox input, .gr-number input, .gr-dropdown select {
        color: var(--text-primary) !important;
        font-family: 'JetBrains Mono', monospace !important;
    }
    .gr-textbox label, .gr-number label, .gr-dropdown label,
    .gr-slider label, .gr-radio label, .gr-checkbox label {
        color: var(--text-dim) !important;
        font-family: 'JetBrains Mono', monospace !important;
        font-size: 0.78em !important;
        letter-spacing: 0.5px !important;
    }

    /* ---- Radio ---- */
    .gr-radio {
        background: transparent !important;
    }
    .gr-radio container {
        gap: 4px !important;
    }
    .gr-radio .gr-radio-item {
        background: var(--bg-card) !important;
        border: 1px solid var(--border-dim) !important;
        border-radius: 6px !important;
        padding: 6px 14px !important;
        font-family: 'JetBrains Mono', monospace !important;
        font-size: 0.8em !important;
        transition: all 0.2s !important;
    }
    .gr-radio .gr-radio-item:hover {
        border-color: var(--cyan-dark) !important;
        background: #00f5ff08 !important;
    }
    .gr-radio .gr-radio-item:has(input:checked) {
        border-color: var(--cyan) !important;
        background: #00f5ff15 !important;
        color: var(--cyan) !important;
        box-shadow: 0 0 10px #00f5ff22 !important;
    }

    /* ---- Slider ---- */
    .gr-slider input[type=range] {
        accent-color: var(--cyan) !important;
    }

    /* ---- Buttons ---- */
    .gr-button, button {
        font-family: 'JetBrains Mono', monospace !important;
        font-size: 0.82em !important;
        letter-spacing: 1px !important;
        border-radius: 8px !important;
        transition: all 0.2s !important;
    }
    button.primary {
        background: linear-gradient(135deg, #0099aa 0%, #006677 100%) !important;
        border: 1px solid var(--cyan-dark) !important;
        color: var(--cyan) !important;
        text-shadow: 0 0 8px var(--cyan-dim) !important;
        box-shadow: 0 0 15px #00f5ff22 !important;
    }
    button.primary:hover {
        background: linear-gradient(135deg, #00b8cc 0%, #0099aa 100%) !important;
        box-shadow: 0 0 25px #00f5ff44 !important;
        transform: translateY(-1px);
    }
    button.secondary {
        background: var(--bg-card2) !important;
        border: 1px solid var(--border-dim) !important;
        color: var(--text-dim) !important;
    }
    button.secondary:hover {
        border-color: var(--cyan-dark) !important;
        color: var(--text-primary) !important;
        background: #00f5ff0a !important;
    }
    button.stop, button.cancel {
        background: #ff006e18 !important;
        border: 1px solid var(--pink-dim) !important;
        color: var(--pink) !important;
    }
    button.stop:hover, button.cancel:hover {
        background: #ff006e33 !important;
        box-shadow: 0 0 15px #ff006e33 !important;
    }

    /* ---- Markdown / Output ---- */
    .gr-markdown {
        color: var(--text-primary) !important;
    }
    .gr-markdown h1, .gr-markdown h2, .gr-markdown h3 {
        font-family: 'Orbitron', sans-serif !important;
        color: var(--cyan) !important;
        letter-spacing: 1px !important;
    }
    .gr-markdown h1 { font-size: 1.3em !important; }
    .gr-markdown h2 { font-size: 1.1em !important; color: var(--text-primary) !important; }
    .gr-markdown h3 { font-size: 0.95em !important; color: var(--text-dim) !important; }
    .gr-markdown table {
        border-collapse: collapse;
        width: 100%;
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.82em;
    }
    .gr-markdown th {
        background: #00f5ff15 !important;
        color: var(--cyan) !important;
        padding: 6px 12px;
        border: 1px solid var(--border-dim);
        text-align: left;
    }
    .gr-markdown td {
        padding: 5px 12px;
        border: 1px solid var(--border-dim);
        color: var(--text-primary) !important;
    }
    .gr-markdown tr:nth-child(even) td {
        background: #ffffff04;
    }
    .gr-markdown code {
        font-family: 'JetBrains Mono', monospace !important;
        background: #00f5ff10 !important;
        color: var(--text-code) !important;
        padding: 1px 6px;
        border-radius: 4px;
        font-size: 0.85em !important;
    }
    .gr-markdown pre {
        background: var(--bg-card) !important;
        border: 1px solid var(--border-dim) !important;
        border-radius: 8px !important;
        padding: 12px !important;
    }
    .gr-markdown pre code {
        background: transparent !important;
        color: var(--text-code) !important;
    }
    .gr-markdown blockquote {
        border-left: 3px solid var(--cyan-dark) !important;
        background: #00f5ff08 !important;
        border-radius: 0 6px 6px 0 !important;
        padding: 8px 14px !important;
        color: var(--text-dim) !important;
        font-size: 0.88em !important;
    }
    .gr-markdown strong { color: var(--amber) !important; }

    /* ---- Thinking / Action boxes ---- */
    .thinking-box {
        background: var(--bg-card) !important;
        border-left: 3px solid var(--cyan) !important;
        border-radius: 0 10px 10px 0 !important;
        box-shadow: 0 0 20px #00f5ff0d inset !important;
        padding: 14px !important;
        color: #e8edf5 !important;
        font-family: 'JetBrains Mono', monospace !important;
        font-size: 0.88em !important;
        line-height: 1.6 !important;
        min-height: 120px !important;
    }
    .thinking-box * {
        color: #e8edf5 !important;
    }
    .thinking-box p {
        color: #e8edf5 !important;
        margin: 0.3em 0 !important;
    }
    .thinking-box code {
        background: #00f5ff15 !important;
        color: #00f5ff !important;
        padding: 2px 8px !important;
        border-radius: 4px !important;
        font-size: 0.85em !important;
    }
    .thinking-box strong {
        color: #ffb700 !important;
    }

    .action-box {
        background: var(--bg-card) !important;
        border-left: 3px solid var(--pink) !important;
        border-radius: 0 10px 10px 0 !important;
        box-shadow: 0 0 20px #ff006e0d inset !important;
        padding: 14px !important;
        color: #e8edf5 !important;
        font-family: 'JetBrains Mono', monospace !important;
        font-size: 0.88em !important;
        line-height: 1.6 !important;
        min-height: 100px !important;
    }
    .action-box * {
        color: #e8edf5 !important;
    }
    .action-box p {
        color: #e8edf5 !important;
        margin: 0.3em 0 !important;
    }
    .action-box code {
        background: #ff006e22 !important;
        color: #ff66b3 !important;
        padding: 2px 8px !important;
        border-radius: 4px !important;
        font-size: 0.85em !important;
    }
    .action-box strong {
        color: #00e676 !important;
    }

    /* ---- Screenshot ---- */
    .screenshot-container {
        border: 2px solid var(--border-dim) !important;
        border-radius: 10px !important;
        overflow: hidden;
        box-shadow: 0 0 30px #00000088, 0 0 2px #00f5ff33 !important;
    }
    .screenshot-container img {
        max-width: 100%;
        height: auto;
        display: block;
    }

    /* ---- Status indicators ---- */
    .status-dot {
        display: inline-block;
        width: 8px; height: 8px;
        border-radius: 50%;
        margin-right: 6px;
        animation: pulse 2s infinite;
    }
    .status-dot.green  { background: var(--green);  box-shadow: 0 0 6px var(--green); }
    .status-dot.cyan   { background: var(--cyan);   box-shadow: 0 0 6px var(--cyan); }
    .status-dot.amber  { background: var(--amber);  box-shadow: 0 0 6px var(--amber); }
    .status-dot.red    { background: var(--red);    box-shadow: 0 0 6px var(--red); }
    @keyframes pulse {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.4; }
    }

    /* ---- Footer ---- */
    .claw-footer {
        text-align: center;
        padding: 16px;
        color: var(--text-dim) !important;
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.72em;
        letter-spacing: 2px;
        border-top: 1px solid var(--border-dim);
    }

    /* ---- Status animation for thinking ---- */
    @keyframes thinking-pulse {
        0%, 100% { opacity: 1; text-shadow: 0 0 8px var(--cyan); }
        50% { opacity: 0.5; text-shadow: 0 0 4px var(--cyan); }
    }
    .thinking-active {
        animation: thinking-pulse 1.5s ease-in-out infinite;
        color: var(--cyan) !important;
    }

    /* ---- Step counter glow ---- */
    @keyframes step-glow {
        0%, 100% { text-shadow: 0 0 10px var(--amber); }
        50% { text-shadow: 0 0 20px var(--amber), 0 0 30px var(--amber); }
    }
    .step-update {
        animation: step-glow 0.5s ease-out;
    }

    /* ---- Success/Error flash ---- */
    @keyframes success-flash {
        0% { background: #00e67622; }
        100% { background: transparent; }
    }
    .action-success {
        animation: success-flash 1s ease-out;
    }

    /* ---- Thinking box typing effect placeholder ---- */
    .thinking-box:empty::before {
        content: '▶ AI 正在思考...';
        color: var(--cyan-dim) !important;
        font-style: italic;
    }
    .action-box:empty::before {
        content: '⏳ 等待执行...';
        color: var(--pink-dim) !important;
        font-style: italic;
    }

    /* ---- Panel hover effects ---- */
    .claw-panel:hover {
        border-color: var(--cyan-dark) !important;
        transition: border-color 0.3s ease;
    }

    /* ---- Neon glow on focus ---- */
    .gr-textbox:focus-within, .gr-number:focus-within {
        box-shadow: 0 0 0 2px #00f5ff33, 0 0 20px #00f5ff22 !important;
    }

    /* ---- Better table styling for graph tab ---- */
    .gr-markdown table {
        border: 1px solid var(--border-dim) !important;
        border-radius: 8px !important;
        overflow: hidden;
    }
    .gr-markdown th:first-child {
        border-top-left-radius: 7px !important;
    }
    .gr-markdown th:last-child {
        border-top-right-radius: 7px !important;
    }
    """
    
    with gr.Blocks(
        title="ClawGUI-Agent",
        theme=gr.themes.Default(
            primary_hue=gr.themes.Color(
                c50="#001a1f", c100="#003344",
                c200="#004d66", c300="#006688",
                c400="#008899", c500="#00a0ab",
                c600="#00b8cc", c700="#00f5ff",
                c800="#33f7ff", c900="#66faff", c950="#99fcff",
            ),
            secondary_hue=gr.themes.Color(
                c50="#1a0011", c100="#330022",
                c200="#4d0033", c300="#660044",
                c400="#880055", c500="#aa0066",
                c600="#cc0077", c700="#ff006e",
                c800="#ff3399", c900="#ff66b3", c950="#ff99cc",
            ),
            neutral_hue=gr.themes.Color(
                c50="#0b1120", c100="#0f1629",
                c200="#141d35", c300="#1e2d4a",
                c400="#2a3d5a", c500="#3a4d6a",
                c600="#4a5d7a", c700="#7a8ba8",
                c800="#a8b8cc", c900="#e8edf5", c950="#f5f7fa",
            ),
            font=("Noto Sans SC", "JetBrains Mono", "system-ui"),
        ),
        css=custom_css,
    ) as demo:

        # ============================================================
        #  HEADER
        # ============================================================
        gr.HTML("""
        <div class="claw-header">
            <div class="claw-logo">CLAWGUI-AGENT</div>
            <div class="claw-subtitle">AI-POWERED MOBILE AUTOMATION SYSTEM</div>
            <div class="claw-badge">DUAL-CORE MEMORY &nbsp;|&nbsp; NEO4j GRAPH ENGINE</div>
        </div>
        """)
        
        with gr.Tabs():
            # ==================== 配置管理 Tab ====================
            with gr.Tab("⚙️ 配置"):
                # ---- Robot Config ----
                gr.HTML('<div class="claw-section-label">🤖 ROBOT CONFIG</div>')
                device_type = gr.Radio(
                    choices=[("Android", "adb"), ("HarmonyOS", "hdc"), ("iOS", "ios")],
                    value="adb",
                    label="DEVICE TYPE",
                )
                with gr.Row():
                    with gr.Column(scale=2):
                        device_id = gr.Textbox(
                            label="DEVICE ID",
                            value=os.getenv("PHONE_AGENT_DEVICE_ID", ""),
                            placeholder="auto-detect",
                        )
                    with gr.Column(scale=1):
                        wda_url = gr.Textbox(
                            label="WDA URL (iOS)",
                            value=os.getenv("PHONE_AGENT_WDA_URL", "http://localhost:8100"),
                            placeholder="http://localhost:8100",
                        )

                gr.HTML('<div class="claw-section-label">🧠 MODEL CONFIG</div>')
                with gr.Row():
                    with gr.Column(scale=3):
                        base_url = gr.Textbox(
                            label="BASE URL",
                            value=os.getenv("PHONE_AGENT_BASE_URL", "http://localhost:8000/v1"),
                            placeholder="http://localhost:8000/v1",
                        )
                    with gr.Column(scale=2):
                        model_name = gr.Textbox(
                            label="MODEL NAME",
                            value=os.getenv("PHONE_AGENT_MODEL", "autoglm-phone-9b"),
                            placeholder="autoglm-phone-9b",
                        )
                    with gr.Column(scale=1):
                        api_key = gr.Textbox(
                            label="API KEY",
                            value=os.getenv("PHONE_AGENT_API_KEY", ""),
                            placeholder="sk-...",
                            type="password",
                        )

                model_type = gr.Radio(
                    choices=[
                        ("AUTO", "auto"), ("AutoGLM", "autoglm"),
                        ("UI-TARS", "uitars"), ("Qwen-VL", "qwenvl"),
                        ("MAI-UI", "maiui"), ("GUI-Owl", "guiowl"),
                    ],
                    value="auto",
                    label="MODEL ADAPTER",
                )

                with gr.Row():
                    with gr.Column(scale=1):
                        max_steps = gr.Slider(
                            minimum=1, maximum=300, step=1,
                            value=int(os.getenv("PHONE_AGENT_MAX_STEPS", "100")),
                            label="MAX STEPS",
                        )
                    with gr.Column(scale=1):
                        prompt_lang = gr.Radio(
                            choices=[("中文 CN", "cn"), ("English", "en")],
                            value=os.getenv("PHONE_AGENT_LANG", "cn"),
                            label="PROMPT LANG",
                        )
                    with gr.Column(scale=1):
                        memory_user_id_config = gr.Textbox(
                            label="USER ID (MEMORY)",
                            value=os.getenv("PHONE_AGENT_USER_ID", "default"),
                            placeholder="default",
                        )

                gr.HTML('<div class="claw-section-label">💾 CONFIG ACTIONS</div>')
                with gr.Row():
                    save_config_btn = gr.Button("💾 SAVE TO .ENV", variant="primary", scale=1)
                    reload_config_btn = gr.Button("↻ RELOAD FROM .ENV", variant="secondary", scale=1)
                save_config_output = gr.Markdown("")

                gr.HTML("""
                <div style="margin-top:8px; padding:10px 14px; background:#00f5ff08;
                            border:1px solid #1e2d4a; border-radius:8px; font-size:0.78em;
                            font-family:'JetBrains Mono',monospace; color:#7a8ba8;">
                    <span style="color:#00f5ff">●</span> 配置从 <code style="color:#a8d8ff">.env</code> 文件自动读取，
                    点击 <strong style="color:#00e676">SAVE TO .ENV</strong> 保存修改 &nbsp;|&nbsp;
                    <span style="color:#ffb700">●</span> 图谱: Neo4j &nbsp;
                    <span style="color:#ffb700">●</span> 向量: FAISS
                </div>
                """)
            
            # ==================== DEVICE Tab ====================
            with gr.Tab("📱 设备"):
                gr.HTML('<div class="claw-section-label">📡 CONNECTED DEVICES</div>')
                with gr.Row():
                    with gr.Column(scale=1):
                        device_list_output = gr.Markdown("> 点击 **SCAN** 刷新设备列表")
                        refresh_devices_btn = gr.Button("SCAN DEVICES", variant="primary")
                    with gr.Column(scale=1):
                        connect_address = gr.Textbox(
                            label="REMOTE ADDR",
                            placeholder="192.168.1.100:5555",
                        )
                        with gr.Row():
                            connect_btn = gr.Button("CONNECT", variant="primary")
                            disconnect_btn = gr.Button("DISCONNECT", variant="stop")
                        connect_output = gr.Markdown("")

                gr.HTML('<div class="claw-section-label">📶 WIFI DEBUG</div>')
                with gr.Row():
                    wifi_port = gr.Number(
                        label="PORT", value=5555, precision=0,
                    )
                    enable_wifi_btn = gr.Button("ENABLE WIFI DEBUG", variant="secondary")
                wifi_output = gr.Markdown("")

                refresh_devices_btn.click(fn=get_device_list, inputs=[device_type], outputs=[device_list_output])
                connect_btn.click(fn=connect_device, inputs=[connect_address, device_type], outputs=[connect_output])
                disconnect_btn.click(fn=disconnect_device, inputs=[connect_address, device_type], outputs=[connect_output])
                enable_wifi_btn.click(fn=enable_wifi_debug, inputs=[wifi_port, device_type], outputs=[wifi_output])

                # Config save/reload handlers
                save_config_btn.click(
                    fn=save_config_to_env,
                    inputs=[base_url, model_name, api_key, max_steps, device_type, prompt_lang, memory_user_id_config, wda_url, device_id],
                    outputs=[save_config_output],
                )
                reload_config_btn.click(
                    fn=lambda: reload_config_from_env(),
                    inputs=[],
                    outputs=[base_url, model_name, api_key, max_steps, device_type, prompt_lang, memory_user_id_config, wda_url, device_id],
                )

            # ==================== SYSTEM CHECK Tab ====================
            with gr.Tab("🔍 检查"):
                gr.HTML('<div class="claw-section-label">🔬 FULL SYSTEM CHECK</div>')
                run_check_btn = gr.Button("RUN DIAGNOSTICS", variant="primary")
                check_output = gr.Markdown("> 点击按钮开始诊断...")

                gr.HTML('<div class="claw-section-label">⚡ QUICK CHECKS</div>')
                with gr.Row():
                    check_tool_btn = gr.Button("TOOLS", variant="secondary")
                    check_device_btn = gr.Button("DEVICE", variant="secondary")
                    check_keyboard_btn = gr.Button("KEYBOARD", variant="secondary")
                    check_api_btn = gr.Button("API", variant="secondary")
                single_check_output = gr.Markdown("")

                run_check_btn.click(fn=run_full_check, inputs=[device_type, base_url, api_key, model_name, wda_url], outputs=[check_output])
                check_tool_btn.click(fn=check_tool_installation, inputs=[device_type], outputs=[single_check_output])
                check_device_btn.click(fn=check_device_connection, inputs=[device_type], outputs=[single_check_output])
                check_keyboard_btn.click(fn=check_keyboard_installation, inputs=[device_type], outputs=[single_check_output])
                check_api_btn.click(fn=check_model_api, inputs=[base_url, api_key, model_name], outputs=[single_check_output])
            
            # ==================== MEMORY Tab ====================
            with gr.Tab("🧠 记忆"):
                gr.HTML('<div class="claw-section-label">💾 DUAL-CORE MEMORY</div>')
                with gr.Row():
                    memory_user_id = gr.Textbox(
                        label="USER ID", value="default", placeholder="default",
                    )
                    refresh_stats_btn = gr.Button("REFRESH", variant="primary")
                memory_stats_output = gr.Markdown("> 点击 **REFRESH** 获取记忆系统状态")

                gr.HTML('<div class="claw-section-label">➕ ADD PREFERENCE</div>')
                with gr.Row():
                    with gr.Column(scale=3):
                        preference_input = gr.Textbox(
                            label="CONTENT", placeholder="e.g. 常用外卖平台是美团...",
                            lines=2,
                        )
                    with gr.Column(scale=1):
                        preference_category = gr.Dropdown(
                            label="CATEGORY",
                            choices=["general", "app", "contact", "habit", "ui"],
                            value="general",
                        )
                        preference_importance = gr.Slider(
                            label="IMPORTANCE", minimum=0.1, maximum=1.0,
                            value=0.6, step=0.1,
                        )
                add_preference_btn = gr.Button("ADD", variant="secondary")
                add_preference_output = gr.Markdown("")

                gr.HTML('<div class="claw-section-label">🔍 SEARCH MEMORIES</div>')
                with gr.Row():
                    search_query = gr.Textbox(
                        label="QUERY", placeholder="keyword search...",
                        scale=3,
                    )
                    search_top_k = gr.Slider(
                        label="TOP_K", minimum=1, maximum=20, value=5, step=1, scale=1,
                    )
                search_btn = gr.Button("SEARCH", variant="secondary")
                search_output = gr.Markdown("")

                gr.HTML('<div class="claw-section-label">💾 DATA MANAGEMENT</div>')
                with gr.Row():
                    export_btn = gr.Button("EXPORT", variant="secondary")
                    import_btn = gr.Button("IMPORT", variant="secondary")
                    clear_btn = gr.Button("CLEAR ALL", variant="stop")
                export_output = gr.Markdown("")
                export_json = gr.Textbox(
                    label="JSON DATA", placeholder="JSON appears here...",
                    lines=8,
                )

                refresh_stats_btn.click(fn=get_memory_stats, inputs=[memory_user_id], outputs=[memory_stats_output])
                add_preference_btn.click(fn=add_user_preference, inputs=[memory_user_id, preference_input, preference_category, preference_importance], outputs=[add_preference_output])
                search_btn.click(fn=search_memories, inputs=[memory_user_id, search_query, search_top_k], outputs=[search_output])
                export_btn.click(fn=export_memories_json, inputs=[memory_user_id], outputs=[export_output, export_json])
                import_btn.click(fn=import_memories_json, inputs=[memory_user_id, export_json], outputs=[export_output])
                clear_btn.click(fn=clear_all_memories, inputs=[memory_user_id], outputs=[export_output])

            # ==================== GRAPH Tab ====================
            with gr.Tab("🗺️ 图谱"):
                gr.HTML("""
                <div style="padding:10px 14px; background:#00f5ff08; border:1px solid #1e2d4a;
                            border-radius:8px; font-size:0.78em; font-family:'JetBrains Mono',monospace; color:#7a8ba8; margin-bottom:12px;">
                    GRAPH ENGINE — records full <span style="color:#a8d8ff">UIState→Action→UIState</span> chains.
                    Human review before commit prevents suboptimal paths polluting the knowledge base.
                </div>
                """)
                with gr.Row():
                    graph_status_output = gr.Markdown("> Click **REFRESH** to check Neo4j status")
                    refresh_graph_btn = gr.Button("REFRESH STATUS", variant="primary")

                gr.HTML('<div class="claw-section-label">🔍 SEARCH TRAJECTORIES</div>')
                with gr.Row():
                    graph_search_query = gr.Textbox(
                        label="QUERY", placeholder="e.g. 京东外卖 KFC 蓝牙耳机...",
                        scale=3,
                    )
                    graph_search_btn = gr.Button("SEARCH", variant="secondary", scale=1)
                graph_search_output = gr.Markdown("> Search results show full reference trajectories")
                graph_search_btn.click(fn=search_graph_trajectories, inputs=[graph_search_query], outputs=[graph_search_output])

                gr.HTML('<div class="claw-section-label">📋 COMMITTED / PENDING</div>')
                with gr.Row():
                    list_trajectories_btn = gr.Button("COMMITTED (Neo4j)", variant="secondary")
                    list_pending_btn = gr.Button("PENDING REVIEW", variant="secondary")
                graph_list_output = gr.Markdown("> Results appear here")
                list_trajectories_btn.click(fn=list_graph_trajectories, inputs=[memory_user_id], outputs=[graph_list_output])
                list_pending_btn.click(fn=list_pending_trajectories, inputs=[memory_user_id], outputs=[graph_list_output])

                gr.HTML('<div class="claw-section-label">✅ COMMIT TO GRAPH</div>')
                with gr.Row():
                    commit_index = gr.Number(label="INDEX #", value=0, precision=0)
                    commit_btn = gr.Button("COMMIT TO NEO4j", variant="primary")
                commit_output = gr.Markdown("")
                commit_btn.click(fn=commit_graph_trajectory, inputs=[memory_user_id, commit_index], outputs=[commit_output])

                gr.HTML("""
                <div style="margin-top:12px; padding:10px 14px; background:#0b1120; border:1px solid #1e2d4a;
                            border-radius:8px; font-family:'JetBrains Mono',monospace; font-size:0.75em; color:#7a8ba8;">
                    WORKFLOW: execute → <span style="color:#ff006e">PENDING</span> → review → <span style="color:#00e676">COMMIT</span> → future tasks auto-match
                </div>
                """)
                refresh_graph_btn.click(fn=refresh_neo4j_stats, inputs=[memory_user_id], outputs=[graph_status_output])

            # ==================== TASK CONTROL Tab ====================
            with gr.Tab("💬 执行"):
                with gr.Row():
                    # Left: task + logs
                    with gr.Column(scale=3):
                        gr.HTML('<div class="claw-section-label">⚡ TASK INPUT</div>')
                        task_input = gr.Textbox(
                            label="TASK", placeholder="e.g. 打开微信，发送消息给张三说'你好'",
                            lines=3,
                        )
                        with gr.Row():
                            start_btn = gr.Button("▶ EXECUTE", variant="primary", scale=2)
                            stop_btn = gr.Button("■ STOP", variant="stop", scale=1)
                            continue_btn = gr.Button("▶ CONTINUE", variant="secondary", scale=1)
                            new_btn = gr.Button("↺ RESET", variant="secondary", scale=1)

                        # Step indicator
                        gr.HTML("""
                        <div id="step-indicator" style="display:flex; align-items:center; gap:12px;
                            margin-bottom:8px; padding:8px 14px; background:#0f1629; border:1px solid #1e2d4a;
                            border-radius:8px; font-family:'JetBrains Mono',monospace;">
                            <span style="color:#00f5ff; font-size:0.75em; letter-spacing:2px;">STEP</span>
                            <span id="step-count" style="color:#ffb700; font-size:1.4em; font-weight:700; min-width:40px;">0</span>
                            <span style="color:#1e2d4a;">|</span>
                            <span id="current-app" style="color:#7a8ba8; font-size:0.78em;">---</span>
                            <span style="flex:1;"></span>
                            <span id="task-status" style="color:#00e676; font-size:0.72em; letter-spacing:1px;">READY</span>
                        </div>
                        """)

                        gr.HTML('<div class="claw-section-label" style="margin-top:4px;">💭 AI THINKING</div>')
                        thinking_output = gr.Markdown("", elem_classes=["thinking-box"])

                        gr.HTML('<div class="claw-section-label">🎯 ACTION EXECUTION</div>')
                        action_output = gr.Markdown("", elem_classes=["action-box"])

                    # Right: screenshot
                    with gr.Column(scale=2):
                        gr.HTML('<div class="claw-section-label">📱 SCREENSHOT</div>')
                        screenshot_display = gr.Image(
                            label="DEVICE PREVIEW", type="pil",
                            elem_classes=["screenshot-container"],
                        )
                        with gr.Row():
                            refresh_screenshot_btn = gr.Button("↺ REFRESH", size="sm")
                            auto_refresh = gr.Checkbox(label="AUTO (2s)", value=False)

                # Events
                start_btn.click(
                    fn=execute_task,
                    inputs=[
                        task_input, device_type, device_id,
                        base_url, api_key, model_name,
                        max_steps, wda_url, model_type,
                        memory_user_id_config, prompt_lang,
                    ],
                    outputs=[thinking_output, action_output, screenshot_display, start_btn],
                )
                stop_btn.click(fn=stop_task, outputs=[action_output])
                continue_btn.click(fn=continue_after_takeover, outputs=[action_output])
                new_btn.click(fn=new_conversation, outputs=[task_input, thinking_output, action_output, screenshot_display])
                refresh_screenshot_btn.click(fn=refresh_screenshot, inputs=[device_type, device_id, wda_url], outputs=[screenshot_display])

                auto_refresh_timer = gr.Timer(value=2, active=False)
                auto_refresh.change(fn=lambda e: gr.Timer(active=e), inputs=[auto_refresh], outputs=[auto_refresh_timer])
                auto_refresh_timer.tick(fn=refresh_screenshot, inputs=[device_type, device_id, wda_url], outputs=[screenshot_display])

            # ==================== HELP Tab ====================
            with gr.Tab("📖 DOC"):
                gr.HTML('<div class="claw-section-label">📖 QUICK START</div>')
                gr.Markdown("""
                **1.** Configure model API in **⚙️ CONFIG** — reads from `.env` by default
                **2.** Connect device in **📱 DEVICE**
                **3.** Run diagnostics in **🔍 CHECK**
                **4.** Execute task in **💬 EXECUTE**
                """)
        # Footer
        gr.HTML("""
        <div class="claw-footer">
            CLAWGUI-AGENT &nbsp;|&nbsp; DUAL-CORE MEMORY &nbsp;|&nbsp; NEO4j + FAISS &nbsp;|&nbsp; POWERED BY GRADIO
        </div>
        """)
    
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
