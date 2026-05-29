"""Tests for multi-signal belief localizer (Definition 3 & 7)."""

from phone_agent.spatial.amsg_config import AMSGOptimConfig
from phone_agent.spatial.belief_localizer import (
    BeliefDistribution,
    BeliefEntry,
    MultiSignalLocalizer,
    ObservationSignals,
    cosine_similarity,
    structural_similarity,
)


class FakeState:
    def __init__(self, state_id, app="", page_type="", landmarks=(), affordances=()):
        self.state_id = state_id
        self.app = app
        self.page_type = page_type
        self.landmarks = landmarks
        self.affordances = affordances


def test_cosine_similarity_identical():
    a = (1.0, 0.0, 0.0)
    b = (1.0, 0.0, 0.0)
    assert abs(cosine_similarity(a, b) - 1.0) < 1e-6


def test_cosine_similarity_orthogonal():
    a = (1.0, 0.0)
    b = (0.0, 1.0)
    assert abs(cosine_similarity(a, b)) < 1e-6


def test_cosine_similarity_empty():
    assert cosine_similarity((), ()) == 0.0


def test_structural_similarity_exact_match():
    score = structural_similarity(
        "taobao", "product_detail", ("price", "title"), ("buy",),
        "taobao", "product_detail", ("price", "title"), ("buy",),
    )
    assert abs(score - 1.0) < 1e-6


def test_structural_similarity_no_match():
    score = structural_similarity(
        "taobao", "home", (), (),
        "jd", "cart", (), (),
    )
    assert score == 0.0


def test_structural_similarity_partial():
    score = structural_similarity(
        "taobao", "product_detail", ("price",), ("buy",),
        "taobao", "search_result", ("price",), (),
    )
    # app match: 0.35, page_type mismatch: 0, landmark: 0.20, affordance: 0
    assert abs(score - 0.55) < 0.01


def test_belief_distribution_entropy_single():
    dist = BeliefDistribution(entries=(BeliefEntry("s1", 1.0),))
    assert dist.entropy < 0.01


def test_belief_distribution_entropy_uniform():
    dist = BeliefDistribution(entries=(
        BeliefEntry("s1", 0.5),
        BeliefEntry("s2", 0.5),
    ))
    assert dist.entropy > 0.6


def test_belief_distribution_map_state():
    dist = BeliefDistribution(entries=(
        BeliefEntry("best", 0.7),
        BeliefEntry("other", 0.3),
    ))
    assert dist.map_state is not None
    assert dist.map_state.state_id == "best"
    assert dist.confidence == 0.7


def test_multi_signal_bayesian_update():
    config = AMSGOptimConfig(
        use_multi_signal_belief=True,
        belief_update_method="bayesian",
    )
    loc = MultiSignalLocalizer(config)
    signals = ObservationSignals(
        app="taobao", page_type="product_detail",
        landmarks=("price", "title"), affordances=("buy",),
    )
    candidates = [
        FakeState("s1", "taobao", "product_detail", ("price", "title"), ("buy",)),
        FakeState("s2", "taobao", "search_result", ("list",), ()),
    ]
    dist = loc.update(signals, candidates)
    assert len(dist.entries) == 2
    assert dist.entries[0].state_id == "s1"  # better structural match
    assert dist.entries[0].probability > dist.entries[1].probability


def test_multi_signal_fixed_mode():
    config = AMSGOptimConfig(belief_update_method="fixed")
    loc = MultiSignalLocalizer(config)
    signals = ObservationSignals(app="x", page_type="home")
    candidates = [FakeState("s1"), FakeState("s2")]
    dist = loc.update(signals, candidates)
    assert len(dist.entries) == 2
    # Fixed mode gives uniform probabilities
    assert abs(dist.entries[0].probability - 0.5) < 0.01


def test_temporal_channel_from_transition_history():
    config = AMSGOptimConfig(
        use_multi_signal_belief=True,
        belief_update_method="bayesian",
    )
    loc = MultiSignalLocalizer(config)
    # Record transition: s1 --search--> s2
    loc.record_transition("s1", "search", "s2")
    loc.record_transition("s1", "search", "s2")
    loc.record_transition("s1", "search", "s3")

    # Set up last state/action for temporal channel
    loc._last_state_id = "s1"
    loc._last_action = "search"

    signals = ObservationSignals(app="x", page_type="search_result")
    candidates = [
        FakeState("s2", "x", "search_result"),
        FakeState("s3", "x", "search_result"),
    ]
    dist = loc.update(signals, candidates)
    # s2 should have higher probability due to temporal prior (2/3 vs 1/3)
    s2_prob = next(e.probability for e in dist.entries if e.state_id == "s2")
    s3_prob = next(e.probability for e in dist.entries if e.state_id == "s3")
    assert s2_prob > s3_prob


def test_graceful_degradation_no_embeddings():
    """When no embeddings, only structural + temporal channels are used."""
    config = AMSGOptimConfig(
        use_multi_signal_belief=True,
        belief_update_method="bayesian",
        belief_signal_weights=(0.3, 0.25, 0.25, 0.2),
    )
    loc = MultiSignalLocalizer(config)
    signals = ObservationSignals(
        app="taobao", page_type="home",
        # No visual_embedding, no semantic_embedding
    )
    candidates = [FakeState("s1", "taobao", "home")]
    dist = loc.update(signals, candidates)
    assert dist.entries[0].probability == 1.0  # single candidate → 1.0
