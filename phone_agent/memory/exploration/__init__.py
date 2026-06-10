"""Offline exploration package.

Split from the legacy single-file ``phone_agent/memory/offline_explorer.py``;
that module remains as a thin compatibility shim re-exporting these names.
"""

from .classifier import PageClassifier
from .classifier_prompts import _CLASSIFIER_FAST_SYSTEM_PROMPT, _CLASSIFIER_SYSTEM_PROMPT
from .explorer import OfflineExplorer, _build_taobao_task
from .prompts import _build_exploration_system_prompt
from .cli import main
from .types import (
    CoverageReport,
    CoverageTarget,
    ExplorationStep,
    PageInfo,
    ShoppingPageType,
    Trajectory,
    _HIGH_RISK_PAGE_TYPES,
    _PAGE_TYPE_MAP,
    _PAGE_TYPE_SUMMARY,
    _SPEC_TRIGGER_TOKENS,
    _UNSAFE_ACTION_TOKENS,
)

__all__ = [
    "CoverageReport",
    "CoverageTarget",
    "ExplorationStep",
    "OfflineExplorer",
    "PageClassifier",
    "PageInfo",
    "ShoppingPageType",
    "Trajectory",
    "main",
]
