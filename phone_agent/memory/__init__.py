"""
Personalized Memory Module for GUI Agent.

This module provides long-term memory capabilities for the phone agent,
enabling it to learn user preferences, habits, and past interactions.

Inspired by TeleMem (https://github.com/TeleAI-UAGI/TeleMem)
"""

from .memory_store import MemoryStore, Memory, MemoryType, ShoppingMetadata, GraphMetadata
from .graph_store import GraphStore
from .spatial_graph_memory import (
    GoalSpec,
    ExplorationImportResult,
    GraphQualityReport,
    PageBelief,
    PageState,
    RepairDecision,
    RoutePlan,
    RuntimeDAG,
    SpatialGraphMemory,
    TransitionEdge,
    VerificationResult,
)
from .manual_trajectory_importer import ManualTrajectoryImporter, ManualTrajectoryImportResult
from .memory_manager import MemoryManager
from .core import UnifiedSessionState, Product, ProductStatus, StepRecord
from .retrieval_gateway import RetrievalGateway, RetrievalResult
from .offline_explorer import CoverageReport, CoverageTarget, OfflineExplorer, ShoppingPageType, Trajectory, PageInfo, PageClassifier

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
    "SpatialGraphMemory",
    "PageState",
    "TransitionEdge",
    "PageBelief",
    "GoalSpec",
    "ExplorationImportResult",
    "GraphQualityReport",
    "RoutePlan",
    "RuntimeDAG",
    "RepairDecision",
    "VerificationResult",
    "ManualTrajectoryImporter",
    "ManualTrajectoryImportResult",
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
    "CoverageTarget",
    "CoverageReport",
    "ShoppingPageType",
    "Trajectory",
    "PageInfo",
    "PageClassifier",
    # Backward-compatible data type aliases
    "ProductInfo",
    "ProductObservation",
    "StepSummary",
]
