"""Ablation-ready configuration for AMSG optimizations.

Each optimization target has a toggle flag. ``AMSGOptimConfig.legacy()``
reproduces exact pre-optimization behavior; ``AMSGOptimConfig.full()``
enables every enhancement.  Individual flags can be mixed for ablation
studies (e.g. belief-only, planner-only, edge-only).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AMSGOptimConfig:
    """Master configuration for AMSG academic optimizations.

    Corresponds to the ablation matrix in the paper:

    =========== ======= ========== ============ ========== ==================
    Config       Belief  Planner    Edge Policy  Func       Heuristic Inject
    =========== ======= ========== ============ ========== ==================
    legacy       fixed   dijkstra   legacy       keyword    ON
    edge_only    fixed   dijkstra   verified     keyword    OFF
    belief_only  bayes   dijkstra   legacy       keyword    ON
    planner_only fixed   belief_a*  legacy       keyword    ON
    func_only    fixed   dijkstra   legacy       embedding  ON
    edge+belief  bayes   dijkstra   verified     keyword    OFF
    full         bayes   belief_a*  verified     embedding  OFF
    =========== ======= ========== ============ ========== ==================
    """

    # ------------------------------------------------------------------
    # Target 1: Generalizable Functionality Discovery
    # ------------------------------------------------------------------
    use_embedding_role_classifier: bool = False
    use_embedding_clusterer: bool = False
    embedding_similarity_threshold: float = 0.72

    # ------------------------------------------------------------------
    # Target 2: Multi-Signal Belief Localization
    # ------------------------------------------------------------------
    use_multi_signal_belief: bool = False
    belief_signal_weights: tuple[float, ...] = (0.30, 0.25, 0.25, 0.20)
    # Channel order: (visual, semantic, structural, temporal)
    belief_update_method: str = "bayesian"  # "bayesian" | "fixed"

    # ------------------------------------------------------------------
    # Target 3: Enhanced Planning
    # ------------------------------------------------------------------
    planner_backend: str = "dijkstra"  # "dijkstra" | "astar" | "belief_astar"
    temporal_decay_halflife: int = 50  # steps before edge staleness reaches ~63%
    exploration_bonus_weight: float = 0.3
    information_gain_weight: float = 0.2

    # ------------------------------------------------------------------
    # Target 5 & 6: Edge Lifecycle & Postcondition Verification
    # ------------------------------------------------------------------
    edge_promotion_policy: str = "verified"  # "verified" | "legacy"
    min_verification_count: int = 1  # at least N postcondition verifications
    outcome_dominance_threshold: float = 0.6  # dominant outcome ratio
    outcome_entropy_vlm_threshold: float = 0.8  # entropy above this triggers VLM
    enable_heuristic_injection: bool = True  # legacy=True, paper=False

    # ------------------------------------------------------------------
    # Preset constructors
    # ------------------------------------------------------------------

    @classmethod
    def legacy(cls) -> AMSGOptimConfig:
        """Reproduce exact pre-optimization behavior for regression testing."""
        return cls(
            enable_heuristic_injection=True,
            edge_promotion_policy="legacy",
        )

    @classmethod
    def full(cls) -> AMSGOptimConfig:
        """Enable all optimizations for paper experiments."""
        return cls(
            use_embedding_role_classifier=True,
            use_embedding_clusterer=True,
            use_multi_signal_belief=True,
            belief_update_method="bayesian",
            planner_backend="belief_astar",
            edge_promotion_policy="verified",
            min_verification_count=1,
            enable_heuristic_injection=False,
        )

    @classmethod
    def edge_only(cls) -> AMSGOptimConfig:
        """Ablation: only edge lifecycle verification, everything else legacy."""
        return cls(
            edge_promotion_policy="verified",
            min_verification_count=1,
            enable_heuristic_injection=False,
        )

    @classmethod
    def belief_only(cls) -> AMSGOptimConfig:
        """Ablation: only multi-signal belief, everything else legacy."""
        return cls(
            use_multi_signal_belief=True,
            belief_update_method="bayesian",
            enable_heuristic_injection=True,
            edge_promotion_policy="legacy",
        )

    @classmethod
    def planner_only(cls) -> AMSGOptimConfig:
        """Ablation: only enhanced planner, everything else legacy."""
        return cls(
            planner_backend="belief_astar",
            enable_heuristic_injection=True,
            edge_promotion_policy="legacy",
        )

    @classmethod
    def sava(cls) -> AMSGOptimConfig:
        """VLM-Primary architecture with real lifecycle verification.

        Actions must be verified >=3 times with >=80% dominance to be promoted.
        Promoted Grounded Actions can auto-execute on the Fast Path.
        Demoted Actions (dominance <40%) are removed from the Action Library.
        """
        return cls(
            edge_promotion_policy="verified",
            min_verification_count=3,
            outcome_dominance_threshold=0.8,
            outcome_entropy_vlm_threshold=0.5,
            enable_heuristic_injection=False,
        )

    @classmethod
    def from_env(cls) -> AMSGOptimConfig:
        """Resolve config preset from AMSG_CONFIG env var (default: legacy)."""
        import os
        preset = os.getenv("AMSG_CONFIG", "legacy").lower()
        presets = {
            "legacy": cls.legacy,
            "full": cls.full,
            "edge_only": cls.edge_only,
            "belief_only": cls.belief_only,
            "planner_only": cls.planner_only,
            "sava": cls.sava,
        }
        factory = presets.get(preset)
        return factory() if factory else cls.legacy()
