"""Compatibility helpers for migrating legacy SpatialGraphMemory to AMSG."""

from __future__ import annotations

from typing import Any

from .core import SemanticActionIR
from .model_bridge import SpatialModelBridge


def next_action_to_semantic_ir(next_action: dict[str, Any]) -> SemanticActionIR:
    return SpatialModelBridge.semantic_action_from_next_action(next_action)
