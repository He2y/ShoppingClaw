"""Active Mobile Spatial Graph (AMSG) primitives."""

from .active_builder import ActiveGraphBuilder, FrontierWeights, ScoredHypothesis
from .coverage_metrics import FunctionalityCoverageMetrics, compute_functionality_coverage
from .core import (
    AffordanceEdge,
    BeliefCandidate,
    BeliefState,
    DeviceActionIR,
    PageNode,
    SemanticActionIR,
    VerificationResult,
)
from .exploration_queue import ExplorationJob, ExplorationQueueBuilder, FunctionalityFrontierWeights
from .functionality import FunctionalityExtractor, FunctionalityItem
from .functionality_cluster import FunctionalityCluster, FunctionalityClusterer
from .hypothesis import EdgeHypothesis, EdgeHypothesisGenerator
from .planner import SpatialPlanner, SpatialRoutePlan
from .quality_gate import FunctionalityQualityGate, FunctionalityQualityResult
from .reporting import (
    build_amsg_dry_run_report,
    build_amsg_v4_functionality_report,
    format_amsg_markdown,
    format_amsg_v4_markdown,
)
from .schema_registry import MobileSchema, SchemaRegistry, get_default_registry
from .screen_cluster import ScreenCluster, ScreenClusterer
from .semantics import ScreenSemanticsExtractor
from .task_synthesis import AMSGModelConfig, TaskSynthesizer, resolve_embedding_config, resolve_strong_vlm_config
from .verifier import NegativeEdgeMemory, PostconditionVerifier

__all__ = [
    "ActiveGraphBuilder",
    "AffordanceEdge",
    "BeliefCandidate",
    "BeliefState",
    "DeviceActionIR",
    "EdgeHypothesis",
    "EdgeHypothesisGenerator",
    "FrontierWeights",
    "FunctionalityCluster",
    "FunctionalityClusterer",
    "FunctionalityCoverageMetrics",
    "FunctionalityExtractor",
    "FunctionalityFrontierWeights",
    "FunctionalityItem",
    "FunctionalityQualityGate",
    "FunctionalityQualityResult",
    "MobileSchema",
    "NegativeEdgeMemory",
    "PageNode",
    "PostconditionVerifier",
    "SchemaRegistry",
    "ScoredHypothesis",
    "ScreenCluster",
    "ScreenClusterer",
    "ScreenSemanticsExtractor",
    "SemanticActionIR",
    "SpatialPlanner",
    "SpatialRoutePlan",
    "TaskSynthesizer",
    "VerificationResult",
    "AMSGModelConfig",
    "build_amsg_dry_run_report",
    "build_amsg_v4_functionality_report",
    "compute_functionality_coverage",
    "format_amsg_markdown",
    "format_amsg_v4_markdown",
    "get_default_registry",
    "resolve_embedding_config",
    "resolve_strong_vlm_config",
]
