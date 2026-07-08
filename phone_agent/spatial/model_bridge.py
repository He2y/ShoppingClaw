"""Bridge between AMSG semantic actions and model/device protocols."""

from __future__ import annotations

import ast
from typing import Any

from phone_agent.model.protocol_bridge import ModelProtocolBridge

from .core import DeviceActionIR, SemanticActionIR


class SpatialModelBridge:
    """Compiles graph-native semantic actions into executable device actions."""

    @staticmethod
    def semantic_action_from_next_action(next_action: dict[str, Any]) -> SemanticActionIR:
        target_desc = next_action.get("target_desc")
        action_params: dict[str, Any] = {}
        if isinstance(target_desc, str) and target_desc:
            try:
                parsed = ast.literal_eval(target_desc)
                if isinstance(parsed, dict):
                    action_params = parsed
            except (SyntaxError, ValueError):
                action_params = {}
        target_locator = dict(next_action.get("target_locator") or {}) or SpatialModelBridge._locator_from_params(action_params)
        semantic_target = str(next_action.get("target") or action_params.get("semantic_target") or "")
        intent = SpatialModelBridge._intent_from_action_type(str(next_action.get("type") or action_params.get("action") or ""))
        return SemanticActionIR(
            intent=intent,
            semantic_target=semantic_target,
            target_locator=target_locator,
            slots={key: str(value) for key, value in action_params.items() if key in {"text", "app"}},
            expected_postcondition=str(next_action.get("postcondition") or ""),
            risk=str(next_action.get("risk") or "normal"),
            confidence=float(next_action.get("confidence") or 0.0),
            source_edge_id=str(next_action.get("edge_id") or next_action.get("source_edge_id") or ""),
            grounding_required=not bool(target_locator),
        )

    @staticmethod
    def compile_direct_action(
        semantic_action: SemanticActionIR,
        *,
        screen_width: int,
        screen_height: int,
        source_model: Any = "spatial_graph",
    ) -> DeviceActionIR | None:
        intent = semantic_action.intent.lower()
        locator = semantic_action.target_locator
        metadata = {
            "_expected_postcondition": semantic_action.expected_postcondition,
            "semantic_target": semantic_action.semantic_target,
            "source_edge_id": semantic_action.source_edge_id,
        }

        if intent in {"go_back", "back"}:
            return DeviceActionIR("system_button", button="Back", screen_size=(screen_width, screen_height), source_model=str(source_model), metadata=metadata)
        if intent in {"home", "go_home"}:
            return DeviceActionIR("system_button", button="Home", screen_size=(screen_width, screen_height), source_model=str(source_model), metadata=metadata)
        if intent in {"wait"}:
            return DeviceActionIR("wait", duration=2, screen_size=(screen_width, screen_height), source_model=str(source_model), metadata=metadata)
        if intent in {"type", "type_text", "submit_query", "submit_search"} and semantic_action.slots.get("text"):
            return DeviceActionIR("type", text=semantic_action.slots["text"], screen_size=(screen_width, screen_height), source_model=str(source_model), metadata=metadata)
        if intent in {"open_app", "launch"} and semantic_action.slots.get("app"):
            return DeviceActionIR("open_app", app_name=semantic_action.slots["app"], screen_size=(screen_width, screen_height), source_model=str(source_model), metadata=metadata)

        coordinate, coordinate_space = SpatialModelBridge._coordinate_from_locator(locator)
        if coordinate is None:
            return None
        action_type = "long_press" if intent == "long_press" else "click"
        return DeviceActionIR(
            action_type=action_type,
            coordinate=coordinate,
            coordinate_space=coordinate_space,
            screen_size=(screen_width, screen_height),
            source_model=str(source_model),
            metadata=metadata,
        )

    @staticmethod
    def compile_to_autoglm_action(semantic_action: SemanticActionIR, *, screen_width: int, screen_height: int, source_model: Any = "spatial_graph") -> dict[str, Any] | None:
        device_action = SpatialModelBridge.compile_direct_action(
            semantic_action,
            screen_width=screen_width,
            screen_height=screen_height,
            source_model=source_model,
        )
        if not device_action:
            return None
        return ModelProtocolBridge.to_autoglm_action(device_action)

    @staticmethod
    def grounding_instruction(semantic_action: SemanticActionIR) -> str:
        return (
            "[AMSG Grounding]\n"
            f"Intent: {semantic_action.intent}\n"
            f"Target: {semantic_action.semantic_target}\n"
            f"Expected page after action: {semantic_action.expected_postcondition or 'unknown'}\n"
            "Use the current screenshot to ground this semantic target. Do not change the intended action."
        )

    @staticmethod
    def _locator_from_params(action_params: dict[str, Any]) -> dict[str, Any]:
        if not action_params:
            return {}
        if "element" in action_params:
            return {"element": action_params["element"], "coordinate_space": "normalized_1000"}
        if "coordinate" in action_params:
            return {"coordinate": action_params["coordinate"], "coordinate_space": "normalized_999"}
        if "bbox" in action_params:
            return {"bbox": action_params["bbox"], "coordinate_space": action_params.get("coordinate_space", "normalized_1000")}
        return {}

    @staticmethod
    def _coordinate_from_locator(locator: dict[str, Any]) -> tuple[tuple[float, float] | None, str]:
        if not locator:
            return None, "normalized_1000"
        coordinate_space = str(locator.get("coordinate_space") or "normalized_1000")
        for key in ("element", "coordinate", "bbox"):
            value = locator.get(key)
            if isinstance(value, list):
                element = value[0] if len(value) == 1 and isinstance(value[0], list) else value
                try:
                    if len(element) >= 4:
                        return ((float(element[0]) + float(element[2])) / 2, (float(element[1]) + float(element[3])) / 2), coordinate_space
                    if len(element) >= 2:
                        return (float(element[0]), float(element[1])), coordinate_space
                except (TypeError, ValueError):
                    return None, coordinate_space
        return None, coordinate_space

    @staticmethod
    def _intent_from_action_type(action_type: str) -> str:
        lowered = action_type.lower()
        mapping = {
            "tap": "click",
            "click": "click",
            "long press": "long_press",
            "long_press": "long_press",
            "type": "type_text",
            "input": "type_text",
            "swipe": "scroll",
            "back": "go_back",
            "home": "home",
            "wait": "wait",
            "launch": "open_app",
            "open_app": "open_app",
        }
        return mapping.get(lowered, lowered or "unknown")
