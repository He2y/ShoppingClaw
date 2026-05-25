"""Model protocol bridge for model-agnostic graph actions.

The graph layer speaks ``DeviceActionIR``. This bridge handles model-specific
coordinate/action conventions and produces the canonical action dictionary used
by the device ActionHandler when direct execution is safe.
"""

from __future__ import annotations

import ast
import json
import re
from typing import Any

from phone_agent.spatial.core import Coordinate, DeviceActionIR, ScreenSize


def _as_model_type_name(model_type: Any) -> str:
    value = getattr(model_type, "value", model_type)
    return str(value or "").lower()


def _center_from_element(value: Any) -> Coordinate | None:
    if not isinstance(value, (list, tuple)):
        return None
    element = list(value)
    if len(element) == 1 and isinstance(element[0], (list, tuple)):
        element = list(element[0])
    try:
        if len(element) >= 4:
            return ((float(element[0]) + float(element[2])) / 2, (float(element[1]) + float(element[3])) / 2)
        if len(element) >= 2:
            return (float(element[0]), float(element[1]))
    except (TypeError, ValueError):
        return None
    return None


def _scale_coord(coord: Coordinate, source_space: str, target_space: str, screen_size: ScreenSize | None = None) -> Coordinate:
    if source_space == target_space:
        return coord
    x, y = coord
    width, height = screen_size or (1000, 1000)
    width = max(1, int(width))
    height = max(1, int(height))

    if source_space == "absolute":
        nx = x / width
        ny = y / height
    elif source_space == "normalized_01":
        nx, ny = x, y
    elif source_space in {"normalized_999", "qwen_999", "maiui_999"}:
        nx, ny = x / 999.0, y / 999.0
    elif source_space in {"normalized_1000", "autoglm_1000"}:
        nx, ny = x / 1000.0, y / 1000.0
    else:
        nx, ny = x / 1000.0, y / 1000.0

    if target_space == "absolute":
        return (max(0.0, min(nx, 1.0)) * width, max(0.0, min(ny, 1.0)) * height)
    if target_space == "normalized_01":
        return (max(0.0, min(nx, 1.0)), max(0.0, min(ny, 1.0)))
    if target_space in {"normalized_999", "qwen_999", "maiui_999"}:
        return (max(0.0, min(nx, 1.0)) * 999, max(0.0, min(ny, 1.0)) * 999)
    return (max(0.0, min(nx, 1.0)) * 1000, max(0.0, min(ny, 1.0)) * 1000)


class ModelProtocolBridge:
    """Normalize model-native actions and compile canonical device actions."""

    @staticmethod
    def normalize_action(
        action: Any,
        *,
        model_type: Any = "autoglm",
        screen_size: ScreenSize | None = None,
    ) -> DeviceActionIR:
        model = _as_model_type_name(model_type)
        if isinstance(action, str):
            action = ModelProtocolBridge._parse_action_string(action)
        if hasattr(action, "action_type") and hasattr(action, "params"):
            action = {"action_type": getattr(action, "action_type"), **dict(getattr(action, "params") or {})}
        if not isinstance(action, dict):
            return DeviceActionIR(action_type="unknown", screen_size=screen_size, source_model=model)

        if model in {"autoglm", "glm", "glm4v"} or "_metadata" in action or "action" in action and action.get("action") in {"Tap", "Swipe", "Back", "Home", "Type", "Launch", "Wait"}:
            return ModelProtocolBridge._normalize_autoglm(action, model, screen_size)
        if model in {"uitars", "ui-tars", "tars"}:
            return ModelProtocolBridge._normalize_uitars(action, model, screen_size)
        if model in {"qwenvl", "qwen", "qwen-vl"}:
            return ModelProtocolBridge._normalize_tool_call(action, model, screen_size, default_space="normalized_999")
        if model in {"maiui", "mai-ui"}:
            return ModelProtocolBridge._normalize_tool_call(action, model, screen_size, default_space="normalized_999")
        if model in {"guiowl", "gui-owl"}:
            return ModelProtocolBridge._normalize_tool_call(action, model, screen_size, default_space="normalized_01")
        return ModelProtocolBridge._normalize_tool_call(action, model, screen_size, default_space="normalized_1000")

    @staticmethod
    def to_autoglm_action(device_action: DeviceActionIR) -> dict[str, Any]:
        """Compile a ``DeviceActionIR`` to the canonical ActionHandler format."""
        metadata = dict(device_action.metadata)
        if device_action.action_type in {"terminate", "answer", "finish"}:
            return {"_metadata": "finish", "message": device_action.text or metadata.get("message", "Task completed")}

        action: dict[str, Any] = {"_metadata": "do"}
        action_type = device_action.action_type
        if action_type in {"click", "tap"}:
            if not device_action.coordinate:
                return {"_metadata": "do", "action": "Interact", "message": "Need grounding for target"}
            x, y = _scale_coord(device_action.coordinate, device_action.coordinate_space, "normalized_1000", device_action.screen_size)
            action.update({"action": "Tap", "element": [int(round(x)), int(round(y))]})
        elif action_type == "long_press":
            if not device_action.coordinate:
                return {"_metadata": "do", "action": "Interact", "message": "Need grounding for target"}
            x, y = _scale_coord(device_action.coordinate, device_action.coordinate_space, "normalized_1000", device_action.screen_size)
            action.update({"action": "Long Press", "element": [int(round(x)), int(round(y))]})
        elif action_type == "swipe":
            if not device_action.coordinate or not device_action.coordinate2:
                return {"_metadata": "do", "action": "Wait", "duration": "1 seconds"}
            x1, y1 = _scale_coord(device_action.coordinate, device_action.coordinate_space, "normalized_1000", device_action.screen_size)
            x2, y2 = _scale_coord(device_action.coordinate2, device_action.coordinate_space, "normalized_1000", device_action.screen_size)
            action.update({"action": "Swipe", "start": [int(round(x1)), int(round(y1))], "end": [int(round(x2)), int(round(y2))]})
        elif action_type in {"type", "type_text"}:
            action.update({"action": "Type", "text": device_action.text})
        elif action_type in {"open", "open_app", "launch"}:
            action.update({"action": "Launch", "app": device_action.app_name or device_action.text})
        elif action_type == "system_button":
            button = (device_action.button or "Back").lower()
            action["action"] = "Home" if button == "home" else "Back"
        elif action_type == "wait":
            duration = device_action.duration if device_action.duration is not None else 2
            action.update({"action": "Wait", "duration": f"{duration} seconds"})
        elif action_type == "interact":
            action.update({"action": "Interact", "message": device_action.text})
        else:
            action.update({"action": action_type})

        if device_action.text and action.get("action") not in {"Type", "Launch", "Interact"}:
            action["text"] = device_action.text
        for key, value in metadata.items():
            if key.startswith("_") or key in {"semantic_target", "source_edge_id"}:
                action[key] = value
        return action

    @staticmethod
    def to_native_payload(device_action: DeviceActionIR, model_type: Any) -> dict[str, Any] | str:
        """Compile a device action to a model-native action payload for prompting/tests."""
        model = _as_model_type_name(model_type)
        if model in {"autoglm", "glm", "glm4v"}:
            return ModelProtocolBridge.to_autoglm_action(device_action)
        if model in {"uitars", "ui-tars", "tars"}:
            return ModelProtocolBridge._to_uitars_payload(device_action)
        if model in {"qwenvl", "qwen", "qwen-vl", "maiui", "mai-ui", "guiowl", "gui-owl"}:
            action = ModelProtocolBridge._to_tool_call_arguments(device_action, model)
            return {"name": "mobile_use", "arguments": action}
        return ModelProtocolBridge.to_autoglm_action(device_action)

    @staticmethod
    def _normalize_autoglm(action: dict[str, Any], model: str, screen_size: ScreenSize | None) -> DeviceActionIR:
        action_name = str(action.get("action") or "").lower()
        if action.get("_metadata") == "finish":
            return DeviceActionIR(action_type="terminate", text=str(action.get("message") or ""), screen_size=screen_size, source_model=model)
        if action_name in {"tap", "double tap", "long press"}:
            coord = _center_from_element(action.get("element"))
            return DeviceActionIR(
                action_type="long_press" if action_name == "long press" else "click",
                coordinate=coord,
                coordinate_space="normalized_1000",
                screen_size=screen_size,
                source_model=model,
            )
        if action_name == "swipe":
            return DeviceActionIR(
                action_type="swipe",
                coordinate=_center_from_element(action.get("start")),
                coordinate2=_center_from_element(action.get("end")),
                coordinate_space="normalized_1000",
                screen_size=screen_size,
                source_model=model,
            )
        if action_name in {"type", "type_name"}:
            return DeviceActionIR(action_type="type", text=str(action.get("text") or ""), screen_size=screen_size, source_model=model)
        if action_name == "launch":
            return DeviceActionIR(action_type="open_app", app_name=str(action.get("app") or ""), screen_size=screen_size, source_model=model)
        if action_name in {"back", "home"}:
            return DeviceActionIR(action_type="system_button", button=action_name.title(), screen_size=screen_size, source_model=model)
        if action_name == "wait":
            return DeviceActionIR(action_type="wait", duration=ModelProtocolBridge._duration_to_seconds(action.get("duration")), screen_size=screen_size, source_model=model)
        if action_name == "interact":
            return DeviceActionIR(action_type="interact", text=str(action.get("message") or ""), screen_size=screen_size, source_model=model)
        return DeviceActionIR(action_type=action_name or "unknown", screen_size=screen_size, source_model=model)

    @staticmethod
    def _normalize_uitars(action: dict[str, Any], model: str, screen_size: ScreenSize | None) -> DeviceActionIR:
        action_type = str(action.get("action_type") or action.get("action") or "").lower()
        params = dict(action.get("params") or action)
        if action_type in {"click", "long_press"}:
            coord = ModelProtocolBridge._parse_uitars_point(params.get("point") or params.get("start_box") or params.get("bbox"))
            if coord and screen_size:
                coord = ModelProtocolBridge._uitars_to_absolute(coord, screen_size)
                space = "absolute"
            else:
                space = "uitars_smart_resize"
            return DeviceActionIR(
                action_type="long_press" if action_type == "long_press" else "click",
                coordinate=coord,
                coordinate_space=space,
                screen_size=screen_size,
                source_model=model,
            )
        if action_type == "scroll":
            return DeviceActionIR(action_type="swipe", coordinate_space="absolute", screen_size=screen_size, source_model=model)
        if action_type == "type":
            return DeviceActionIR(action_type="type", text=str(params.get("content") or ""), screen_size=screen_size, source_model=model)
        if action_type == "open_app":
            return DeviceActionIR(action_type="open_app", app_name=str(params.get("app_name") or ""), screen_size=screen_size, source_model=model)
        if action_type == "press_back":
            return DeviceActionIR(action_type="system_button", button="Back", screen_size=screen_size, source_model=model)
        if action_type == "press_home":
            return DeviceActionIR(action_type="system_button", button="Home", screen_size=screen_size, source_model=model)
        if action_type == "finished":
            return DeviceActionIR(action_type="terminate", text=str(params.get("content") or ""), screen_size=screen_size, source_model=model)
        return DeviceActionIR(action_type=action_type or "unknown", screen_size=screen_size, source_model=model)

    @staticmethod
    def _normalize_tool_call(action: dict[str, Any], model: str, screen_size: ScreenSize | None, default_space: str) -> DeviceActionIR:
        if "arguments" in action and isinstance(action["arguments"], dict):
            action = dict(action["arguments"])
        action_type = str(action.get("action") or action.get("action_type") or "").lower()
        coord = _center_from_element(action.get("coordinate"))
        coord2 = _center_from_element(action.get("coordinate2"))
        if action_type in {"click", "tap"}:
            return DeviceActionIR(action_type="click", coordinate=coord, coordinate_space=default_space, screen_size=screen_size, source_model=model)
        if action_type == "long_press":
            return DeviceActionIR(action_type="long_press", coordinate=coord, duration=ModelProtocolBridge._duration_to_seconds(action.get("time")), coordinate_space=default_space, screen_size=screen_size, source_model=model)
        if action_type == "swipe":
            return DeviceActionIR(action_type="swipe", coordinate=coord, coordinate2=coord2, coordinate_space=default_space, screen_size=screen_size, source_model=model)
        if action_type in {"type", "type_name"}:
            return DeviceActionIR(action_type="type", text=str(action.get("text") or ""), screen_size=screen_size, source_model=model)
        if action_type in {"open", "open_app"}:
            return DeviceActionIR(action_type="open_app", app_name=str(action.get("app_name") or action.get("text") or ""), screen_size=screen_size, source_model=model)
        if action_type == "system_button":
            return DeviceActionIR(action_type="system_button", button=str(action.get("button") or "Back"), screen_size=screen_size, source_model=model)
        if action_type == "wait":
            return DeviceActionIR(action_type="wait", duration=ModelProtocolBridge._duration_to_seconds(action.get("time")), screen_size=screen_size, source_model=model)
        if action_type in {"terminate", "answer"}:
            return DeviceActionIR(action_type=action_type, text=str(action.get("text") or action.get("status") or ""), screen_size=screen_size, source_model=model)
        if action_type == "interact":
            return DeviceActionIR(action_type="interact", text=str(action.get("text") or ""), screen_size=screen_size, source_model=model)
        return DeviceActionIR(action_type=action_type or "unknown", screen_size=screen_size, source_model=model)

    @staticmethod
    def _to_tool_call_arguments(device_action: DeviceActionIR, model: str) -> dict[str, Any]:
        if device_action.action_type in {"click", "long_press"}:
            target_space = "normalized_01" if model in {"guiowl", "gui-owl"} else "normalized_999"
            coord = _scale_coord(device_action.coordinate or (500, 500), device_action.coordinate_space, target_space, device_action.screen_size)
            return {"action": device_action.action_type, "coordinate": [int(round(coord[0])), int(round(coord[1]))] if target_space != "normalized_01" else [coord[0], coord[1]]}
        if device_action.action_type == "swipe":
            target_space = "normalized_01" if model in {"guiowl", "gui-owl"} else "normalized_999"
            coord = _scale_coord(device_action.coordinate or (500, 700), device_action.coordinate_space, target_space, device_action.screen_size)
            coord2 = _scale_coord(device_action.coordinate2 or (500, 300), device_action.coordinate_space, target_space, device_action.screen_size)
            return {"action": "swipe", "coordinate": list(coord), "coordinate2": list(coord2)}
        if device_action.action_type == "type":
            return {"action": "type", "text": device_action.text}
        if device_action.action_type == "open_app":
            return {"action": "open_app", "app_name": device_action.app_name}
        if device_action.action_type == "system_button":
            return {"action": "system_button", "button": device_action.button or "Back"}
        if device_action.action_type == "wait":
            return {"action": "wait", "time": device_action.duration or 2}
        return {"action": device_action.action_type}

    @staticmethod
    def _to_uitars_payload(device_action: DeviceActionIR) -> str:
        if device_action.action_type in {"click", "long_press"}:
            coord = _scale_coord(device_action.coordinate or (500, 500), device_action.coordinate_space, "absolute", device_action.screen_size)
            fn = "long_press" if device_action.action_type == "long_press" else "click"
            return f"{fn}(point='<point>{int(coord[0])} {int(coord[1])}</point>')"
        if device_action.action_type == "type":
            return f"type(content={device_action.text!r})"
        if device_action.action_type == "system_button":
            return "press_home()" if (device_action.button or "").lower() == "home" else "press_back()"
        if device_action.action_type == "open_app":
            return f"open_app(app_name={device_action.app_name!r})"
        if device_action.action_type == "wait":
            return "wait()"
        return f"{device_action.action_type}()"

    @staticmethod
    def _parse_action_string(action: str) -> Any:
        text = action.strip()
        if "<tool_call>" in text:
            match = re.search(r"<tool_call>\s*(.*?)\s*</tool_call>", text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(1).strip())
                except json.JSONDecodeError:
                    return {}
        if text.startswith("{"):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {}
        if text.startswith("do") or text.startswith("finish"):
            try:
                tree = ast.parse(text, mode="eval")
                if isinstance(tree.body, ast.Call):
                    result = {"_metadata": "finish" if text.startswith("finish") else "do"}
                    for keyword in tree.body.keywords:
                        result[keyword.arg] = ast.literal_eval(keyword.value)
                    return result
            except Exception:
                return {}
        fn_match = re.match(r"([A-Za-z_]\w*)\s*\((.*)\)\s*$", text, re.DOTALL)
        if fn_match:
            name = fn_match.group(1)
            args = fn_match.group(2)
            payload: dict[str, Any] = {"action_type": name, "action": name}
            for key in ("point", "start_box", "bbox", "content", "text", "app_name"):
                value = ModelProtocolBridge._extract_kwarg(args, key)
                if value is not None:
                    payload[key] = value
            if name.lower() in {"tap", "click", "long_press", "double_tap"} and "coordinate" not in payload and "point" not in payload:
                numbers = re.findall(r"-?\d+(?:\.\d+)?", args)
                if len(numbers) >= 2:
                    payload["coordinate"] = [float(numbers[0]), float(numbers[1])]
            return payload
        return {"raw": text}

    @staticmethod
    def _extract_kwarg(args: str, key: str) -> Any:
        match = re.search(rf"{re.escape(key)}\s*=\s*('([^']*)'|\"([^\"]*)\"|([^,\)]+))", args, re.DOTALL)
        if not match:
            return None
        raw = match.group(2) if match.group(2) is not None else match.group(3) if match.group(3) is not None else match.group(4)
        if raw is None:
            return None
        value = raw.strip()
        try:
            return ast.literal_eval(value)
        except Exception:
            return value

    @staticmethod
    def _parse_uitars_point(point: Any) -> Coordinate | None:
        if not point:
            return None
        text = str(point)
        numbers = re.findall(r"-?\d+(?:\.\d+)?", text)
        if len(numbers) >= 4:
            x1, y1, x2, y2 = (float(item) for item in numbers[:4])
            return ((x1 + x2) / 2, (y1 + y2) / 2)
        if len(numbers) >= 2:
            return (float(numbers[0]), float(numbers[1]))
        return None

    @staticmethod
    def _uitars_to_absolute(coord: Coordinate, screen_size: ScreenSize) -> Coordinate:
        from phone_agent.actions.handler_uitars import smart_resize

        width, height = screen_size
        resized_h, resized_w = smart_resize(height, width)
        return (coord[0] / resized_w * width, coord[1] / resized_h * height)

    @staticmethod
    def _duration_to_seconds(value: Any) -> float:
        if value is None:
            return 2.0
        if isinstance(value, (int, float)):
            return float(value)
        match = re.search(r"\d+(?:\.\d+)?", str(value))
        return float(match.group(0)) if match else 2.0
