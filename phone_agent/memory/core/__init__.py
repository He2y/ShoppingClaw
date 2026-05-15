"""
Core data structures for unified session state management.

Merges the previously fragmented SessionMemory + KnowledgeBase + StateManager
into a single UnifiedSessionState — one source of truth for all session data.
"""

from .product import Product, ProductStatus, product_name_match
from .step_record import StepRecord
from .unified_state import UnifiedSessionState

__all__ = [
    "Product",
    "ProductStatus",
    "StepRecord",
    "UnifiedSessionState",
    "product_name_match",
]
