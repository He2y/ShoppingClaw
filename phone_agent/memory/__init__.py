"""
Personalized Memory Module for GUI Agent.

This module provides long-term memory capabilities for the phone agent,
enabling it to learn user preferences, habits, and past interactions.

Inspired by TeleMem (https://github.com/TeleAI-UAGI/TeleMem)
"""

from .memory_store import MemoryStore, Memory, MemoryType, ShoppingMetadata, GraphMetadata
from .graph_store import GraphStore
from .memory_manager import MemoryManager
from .core import UnifiedSessionState, Product, ProductStatus, StepRecord
from .retrieval_gateway import RetrievalGateway, RetrievalResult
from .offline_explorer import OfflineExplorer, ShoppingPageType, Trajectory, PageInfo, PageClassifier

# Backward-compatible data type aliases
ProductInfo = Product
ProductObservation = Product
StepSummary = StepRecord

__all__ = [
    # Core store
    "MemoryStore",
    "Memory",
    "MemoryType",
    "ShoppingMetadata",
    "GraphMetadata",
    "GraphStore",
    "MemoryManager",
    # Unified state (new)
    "UnifiedSessionState",
    "Product",
    "ProductStatus",
    "StepRecord",
    # Retrieval
    "RetrievalGateway",
    "RetrievalResult",
    # Offline explorer
    "OfflineExplorer",
    "ShoppingPageType",
    "Trajectory",
    "PageInfo",
    "PageClassifier",
    # Backward-compatible data type aliases
    "ProductInfo",
    "ProductObservation",
    "StepSummary",
]
