"""Tests for trajectory loading + observation extraction (offline replay)."""

from __future__ import annotations

import json

from phone_agent.experiment.trajectory import (
    Trajectory,
    derive_action_target,
    derive_action_type,
    extract_observations,
    load_trajectories,
)


def _traj(steps, traj_id="t1", task="buy", success=True, apps=("淘宝",)):
    return Trajectory(
        traj_id=traj_id,
        task=task,
        success=success,
        apps=tuple(apps),
        n_steps=len(steps),
        steps=tuple(steps),
    )


def test_adjacent_pairing_builds_source_to_observed_target():
    steps = [
        {"action_type": "Tap", "action_params": {"element": [1, 2]}, "page_type": "search_input", "app": "淘宝"},
        {"action_type": "Tap", "action_params": {"element": [3, 4]}, "page_type": "search_result", "app": "淘宝"},
        {"action_type": "unknown", "action_params": {"message": "done"}, "page_type": "search_result", "app": "淘宝"},
    ]
    obs = extract_observations(_traj(steps))
    # step0->step1: search_input -> search_result.
    # step1->step2: search_result -> search_result (self-loop, kept).
    # terminal unknown is the last step -> never a source.
    assert len(obs) == 2
    assert obs[0].source_page_type == "search_input"
    assert obs[0].observed_target_page_type == "search_result"
    assert obs[0].action_type == "Tap"
    assert obs[1].is_self_loop


def test_empty_and_unknown_page_types_are_dropped():
    steps = [
        {"action_type": "Launch", "action_params": {"app": "淘宝"}, "page_type": "home", "app": "System Home"},
        {"action_type": "Tap", "action_params": {"element": [1, 2]}, "page_type": "", "app": "淘宝"},
        {"action_type": "Tap", "action_params": {"element": [3, 4]}, "page_type": "search_input", "app": "淘宝"},
    ]
    obs = extract_observations(_traj(steps))
    # step0->step1: target "" dropped. step1->step2: source "" dropped. => 0 usable.
    assert obs == []


def test_terminal_action_source_is_dropped_midstream():
    steps = [
        {"action_type": "answer", "action_params": {"message": "x"}, "page_type": "home", "app": "淘宝"},
        {"action_type": "Tap", "action_params": {"element": [1, 2]}, "page_type": "search_input", "app": "淘宝"},
    ]
    assert extract_observations(_traj(steps)) == []


def test_two_step_real_trajectory_yields_zero_observations():
    # Mirrors 20260605_111128: ["" , search_input] then terminal -> 0 usable.
    steps = [
        {"action_type": "Tap", "action_params": {"element": [389, 114]}, "page_type": "", "app": "淘宝"},
        {"action_type": "unknown", "action_params": {"message": "..."}, "page_type": "search_input", "app": "淘宝"},
    ]
    assert extract_observations(_traj(steps)) == []


def test_action_target_derivation_matches_record_observation():
    # Tap -> raw coords (coordinate-keyed, won't merge across runs).
    assert derive_action_target({"action_type": "Tap", "element": [389, 114]}) == "[389, 114]"
    # Type -> NO text fallback in record_observation -> empty (merges).
    assert derive_action_target({"action_type": "Type", "text": "4K"}) == ""
    # Swipe -> no element/target -> empty (merges).
    assert derive_action_target({"action_type": "Swipe", "start": [1, 2], "end": [3, 4]}) == ""
    # Semantic target wins when present.
    assert derive_action_target({"action_type": "Tap", "semantic_target": "搜索框", "element": [1, 2]}) == "搜索框"


def test_is_coordinate_keyed_flag():
    obs = extract_observations(
        _traj(
            [
                {"action_type": "Tap", "action_params": {"element": [1, 2]}, "page_type": "a", "app": "x"},
                {"action_type": "Swipe", "action_params": {"start": [1, 1], "end": [2, 2]}, "page_type": "b", "app": "x"},
                {"action_type": "Tap", "action_params": {"semantic_target": "btn", "element": [9, 9]}, "page_type": "c", "app": "x"},
                {"action_type": "unknown", "action_params": {"message": "done"}, "page_type": "d", "app": "x"},
            ]
        )
    )
    assert obs[0].is_coordinate_keyed is True   # raw element tap
    assert obs[1].is_coordinate_keyed is False  # swipe (empty target)
    assert obs[2].is_coordinate_keyed is False  # semantic target present


def test_derive_action_type_fallbacks():
    assert derive_action_type({"action_type": "Tap"}) == "Tap"
    assert derive_action_type({"action": "Swipe"}) == "Swipe"
    assert derive_action_type({}) == "unknown"


def test_load_trajectories_reads_dir(tmp_path):
    sample = {
        "task": "buy",
        "success": True,
        "apps": ["淘宝"],
        "steps": 2,
        "step_details": [
            {"action_type": "Tap", "action_params": {"element": [1, 2]}, "page_type": "a", "app": "淘宝"},
            {"action_type": "Tap", "action_params": {"element": [3, 4]}, "page_type": "b", "app": "淘宝"},
        ],
    }
    (tmp_path / "20260101_000000_ok_demo.json").write_text(
        json.dumps(sample, ensure_ascii=False), encoding="utf-8"
    )
    trajs = load_trajectories(tmp_path)
    assert len(trajs) == 1
    assert trajs[0].task == "buy"
    assert trajs[0].success is True
    assert len(extract_observations(trajs[0])) == 1
