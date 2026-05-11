"""
Personalized Memory Module for GUI Agent.

This module provides long-term memory capabilities for the phone agent,
enabling it to learn user preferences, habits, and past interactions.

Inspired by TeleMem (https://github.com/TeleAI-UAGI/TeleMem)
"""

from .memory_store import MemoryStore, Memory, MemoryType, ShoppingMetadata, GraphMetadata
from .graph_store import GraphStore
from .memory_manager import MemoryManager
from .state_manager import StateManager
from .session_memory import SessionMemory, ProductInfo, StepSummary
from .offline_explorer import OfflineExplorer, ShoppingPageType, Trajectory, PageInfo, PageClassifier

__all__ = [
    "MemoryStore",
    "Memory",
    "MemoryType",
    "ShoppingMetadata",
    "GraphMetadata",
    "GraphStore",
    "MemoryManager",
    "StateManager",
    "SessionMemory",
    "ProductInfo",
    "StepSummary",
    "OfflineExplorer",
    "ShoppingPageType",
    "Trajectory",
    "PageInfo",
    "PageClassifier",
]




