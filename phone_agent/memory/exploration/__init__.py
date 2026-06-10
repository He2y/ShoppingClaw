"""Offline exploration package.

Split from the legacy single-file ``phone_agent/memory/offline_explorer.py``;
that module remains as a thin compatibility shim re-exporting these names.
"""

from .classifier import PageClassifier
from .classifier_prompts import (
    _CLASSIFIER_FAST_SYSTEM_PROMPT,
    _CLASSIFIER_SYSTEM_PROMPT,
    build_fast_prompt,
    build_full_prompt,
)
from .explorer import OfflineExplorer, _build_taobao_task
from .page_evidence import (
    infer_page_type_from_reasoning,
    is_cart_page_evidence,
    is_login_page_evidence,
    is_settings_page_evidence,
)
from .prompts import _build_exploration_system_prompt
from .cli import main
from .safety import SafetyPolicy
from .task_builder import build_default_task, coverage_from_schema
from .transition_rules import TransitionRuleEngine
from .types import (
    CoverageReport,
    CoverageTarget,
    ExplorationStep,
    PageInfo,
    PageTypeSpace,
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
    "PageTypeSpace",
    "SafetyPolicy",
    "ShoppingPageType",
    "TransitionRuleEngine",
    "Trajectory",
    "build_default_task",
    "build_fast_prompt",
    "build_full_prompt",
    "coverage_from_schema",
    "infer_page_type_from_reasoning",
    "is_cart_page_evidence",
    "is_login_page_evidence",
    "is_settings_page_evidence",
    "main",
]
