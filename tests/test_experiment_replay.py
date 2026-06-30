"""Tests for the offline lifecycle replay engine.

conftest pins AMSG_CONFIG=legacy, so every test constructs the preset it needs
explicitly via AMSGOptimConfig.
"""

from __future__ import annotations

from phone_agent.experiment.replay import (
    compare_configs,
    n_sensitivity,
    replay,
    verify_p0c_recovery,
)
from phone_agent.experiment.trajectory import Observation
from phone_agent.spatial.amsg_config import AMSGOptimConfig


def _swipe_obs(n, source="page_a", target="page_b"):
    """n identical empty-target swipe observations (these MERGE into one edge)."""
    return [
        Observation(
            traj_id="t",
            step_index=i,
            app="淘宝",
            source_page_type=source,
            observed_target_page_type=target,
            action={"action_type": "Swipe", "start": [1, 1], "end": [2, 2]},
        )
        for i in range(n)
    ]


def _coord_obs(n, element, source="page_a", target="page_b"):
    """n identical coordinate-tap observations with the SAME element."""
    return [
        Observation(
            traj_id="t",
            step_index=i,
            app="淘宝",
            source_page_type=source,
            observed_target_page_type=target,
            action={"action_type": "Tap", "element": list(element)},
        )
        for i in range(n)
    ]


def test_sava_requires_three_verifications_to_promote():
    sava = AMSGOptimConfig.sava()
    two = replay(_swipe_obs(2), sava)
    assert two.n_edges == 1
    assert two.edges[0].verification_count == 2
    assert two.edges[0].stage == "hypothesis"
    assert two.n_promoted == 0

    three = replay(_swipe_obs(3), sava)
    assert three.edges[0].verification_count == 3
    assert three.edges[0].stage == "promoted"
    assert three.n_promoted == 1


def test_legacy_promotes_on_first_write():
    snap = replay(_swipe_obs(1), AMSGOptimConfig.legacy())
    assert snap.edges[0].verification_count == 1
    assert snap.edges[0].stage == "promoted"
    assert snap.n_promoted == 1


def test_coordinate_taps_with_distinct_coords_do_not_merge():
    """3 logically-same taps at different pixels -> 3 edges, each vc=1, none
    promoted under sava. This is the core data-gap mechanism."""
    obs = (
        _coord_obs(1, [389, 114])
        + _coord_obs(1, [391, 116])
        + _coord_obs(1, [388, 113])
    )
    snap = replay(obs, AMSGOptimConfig.sava())
    assert snap.n_edges == 3
    assert all(e.verification_count == 1 for e in snap.edges)
    assert snap.n_promoted == 0


def test_per_edge_dominance_is_structurally_one():
    """observed_target is part of the edge key, so an edge's own outcome_counts
    only ever holds one target -> dominance is always 1.0. Documents that the
    sava dominance gate is inert at the edge level; multimodality lives in the
    outcome distribution instead."""
    obs = _swipe_obs(3, target="x") + _swipe_obs(2, target="y")
    snap = replay(obs, AMSGOptimConfig.sava())
    assert {e.target_page_type for e in snap.edges} == {"x", "y"}
    assert all(e.dominance_ratio == 1.0 for e in snap.edges)


def test_outcome_distribution_captures_multimodality():
    """Same (source, action) leading to two different targets -> the outcome
    distribution is multi-modal with entropy > 0 (the real RQ2 'misbehaving
    action' signal that drives VLM verification)."""
    obs = _swipe_obs(1, target="x") + _swipe_obs(1, target="y")
    snap = replay(obs, AMSGOptimConfig.sava())
    dists = [d for d in snap.outcome_dists if d.source_page_type == "page_a"]
    assert len(dists) == 1
    assert dists[0].is_multimodal is True
    assert dists[0].entropy > 0.0
    assert snap.n_multimodal == 1


def test_single_peaked_distribution_has_zero_entropy():
    snap = replay(_swipe_obs(3, target="x"), AMSGOptimConfig.sava())
    dists = [d for d in snap.outcome_dists if d.source_page_type == "page_a"]
    assert dists[0].is_multimodal is False
    assert dists[0].entropy == 0.0
    assert snap.n_multimodal == 0


def test_p0c_recovery_on_repeated_identical_coordinate():
    """A coordinate edge seen 3x with identical pixels accumulates real vc=3;
    _build_lifecycle_dict must recover that via the raw-evidence fallback rather
    than fall to the default @1 branch (the P0-C fix, on real-style coords)."""
    res = verify_p0c_recovery(_coord_obs(3, [389, 114]))
    assert res.n_coord_edges == 1
    assert res.max_coord_vc == 3
    assert res.all_recovered is True
    assert res.proven_on_real_data is True


def test_compare_configs_legacy_promotes_at_least_as_many_as_sava():
    obs = (
        _swipe_obs(3, target="x")          # mergeable -> sava promotes
        + _coord_obs(1, [1, 2], target="y")  # singleton coord -> sava keeps hypothesis
        + _coord_obs(1, [3, 4], target="z")
    )
    snaps = compare_configs(obs)
    assert snaps["legacy"].n_promoted >= snaps["sava"].n_promoted
    # legacy promotes every edge it sees
    assert snaps["legacy"].n_promoted == snaps["legacy"].n_edges
    # sava promotes only the merged swipe edge
    assert snaps["sava"].n_promoted == 1


def test_n_sensitivity_is_monotonic_nonincreasing():
    # More verifications required -> fewer (or equal) promotable edges.
    obs = _swipe_obs(3, target="x") + _swipe_obs(5, source="page_c", target="page_d")
    sens = n_sensitivity(obs, ns=(1, 3, 5))
    assert sens[1] >= sens[3] >= sens[5]
    assert sens[3] == 2  # both edges reach vc>=3
    assert sens[5] == 1  # only the 5x edge reaches vc>=5


def test_no_verification_ablation_promotes_nothing():
    """verify=False (F2 ablation) casts no lifecycle vote -> no records exist."""
    snap = replay(_swipe_obs(5), AMSGOptimConfig.sava(), verify=False)
    assert snap.n_edges == 0
    assert snap.n_promoted == 0
