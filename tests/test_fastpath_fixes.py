"""Regression tests for Fast Path / lifecycle / commit-boundary fixes.

Covers:
- E1: compound fast actions use the "actions" key the handler actually reads
- E2: slot filling is substring-safe ("搜索<query>" gets filled)
- P2: lifecycle step counter advances once per agent step, not only on
  Fast Path success; _record_and_evolve no longer advances it
- P3: purchase-commit transitions are in the VLM verification boundary
- _needs_vlm no longer force-routes search_input+query away from Fast Path
"""

from types import SimpleNamespace

from phone_agent.agent import PhoneAgent, StepResult
from phone_agent.spatial.action_advisor import ActionAdvisor, ActionHint


def _bare_agent() -> PhoneAgent:
    agent = object.__new__(PhoneAgent)
    agent._step_count = 0
    agent._step_telemetry = {}
    agent.last_step_info = {}
    agent.step_observer = None
    agent.memory_manager = None
    return agent


# ── E1: compound key alignment ──────────────────────────────────────────────


def test_get_fast_action_compound_uses_actions_key():
    advisor = object.__new__(ActionAdvisor)
    hint = ActionHint(
        target_page="search_result",
        action_type="Compound",
        region="top",
        grounded=True,
        confidence=0.95,
        description="搜索",
        compound_steps=(
            {"action": "Tap", "element": [500, 100]},
            {"action": "Type", "text": "<query>"},
        ),
    )

    action = advisor.get_fast_action(hint)

    assert action["action"] == "Compound"
    assert "actions" in action          # ActionHandler._handle_compound reads this
    assert "steps" not in action        # the old key silently failed every time
    assert len(action["actions"]) == 2


# ── E2: substring slot filling ──────────────────────────────────────────────


def test_fill_action_slots_substring_replacement():
    action = {
        "action": "Compound",
        "actions": [
            {"action": "Tap", "element": [500, 100]},
            {"action": "Type", "text": "搜索<query>"},
            {"action": "Type", "text": "<missing_slot>"},
        ],
    }

    filled = PhoneAgent._fill_action_slots(action, {"query": "无线耳机"})

    assert filled["actions"][1]["text"] == "搜索无线耳机"
    # Unknown slots stay as-is (the handler's guard rejects them at runtime)
    assert filled["actions"][2]["text"] == "<missing_slot>"
    # Original action is not mutated
    assert action["actions"][1]["text"] == "搜索<query>"


def test_fill_action_slots_passthrough_for_non_compound():
    action = {"action": "Tap", "element": [1, 2]}
    assert PhoneAgent._fill_action_slots(action, {"query": "x"}) is action


# ── P2: lifecycle step counter advances exactly once per step ───────────────


class _FakeLifecycle:
    def __init__(self):
        self.advance_calls = 0
        self.outcomes = []

    def advance_step(self):
        self.advance_calls += 1

    def record_outcome(self, **kwargs):
        self.outcomes.append(kwargs)


def _agent_with_lifecycle() -> tuple[PhoneAgent, _FakeLifecycle]:
    agent = _bare_agent()
    lifecycle = _FakeLifecycle()
    agent.memory_manager = SimpleNamespace(
        spatial_graph_memory=SimpleNamespace(_edge_lifecycle=lifecycle),
    )
    return agent, lifecycle


def test_lifecycle_advances_once_per_step_for_all_paths():
    agent, lifecycle = _agent_with_lifecycle()
    agent._execute_step_impl = lambda u=None, f=False: StepResult(
        success=True, finished=False, action=None, thinking="", message=None,
    )

    agent._execute_step(None, False)
    agent._execute_step(None, False)

    assert lifecycle.advance_calls == 2


def test_record_and_evolve_records_outcome_without_advancing():
    agent, lifecycle = _agent_with_lifecycle()

    agent._record_and_evolve(
        "home", {"action": "Tap", "element": [1, 2]}, "search_input",
    )

    assert len(lifecycle.outcomes) == 1
    assert lifecycle.outcomes[0]["observed_target"] == "search_input"
    assert lifecycle.advance_calls == 0


def test_record_and_evolve_noop_without_observation():
    agent, lifecycle = _agent_with_lifecycle()

    agent._record_and_evolve("home", {"action": "Tap"}, None)

    assert lifecycle.outcomes == []


# ── P3: purchase-commit boundary ─────────────────────────────────────────────


def test_commit_transitions_require_vlm_verification():
    from phone_agent.memory.memory_manager import MemoryManager
    from phone_agent.spatial.schema_registry import vlm_verify_transitions_for

    pairs = vlm_verify_transitions_for("shopping")
    for pair in (
        ("spec_selection", "cart"),
        ("spec_selection", "checkout"),
        ("cart", "checkout"),
    ):
        assert pair in pairs
        assert MemoryManager._requires_vlm_verification(*pair) is True


# ── _needs_vlm: search slot substitution may use Fast Path ───────────────────


def test_needs_vlm_allows_fast_path_on_search_input_with_query():
    agent = _bare_agent()
    agent._task_plan = SimpleNamespace(
        goal_slots={"query": "无线耳机"},
        current_step=lambda: SimpleNamespace(target_page="search_result"),
        steps=[object()],
    )
    hint = ActionHint(
        target_page="search_result",
        action_type="Compound",
        region="top",
        grounded=True,
        confidence=0.95,
        description="输入并搜索",
        compound_steps=({"action": "Type", "text": "<query>"},),
    )

    assert agent._needs_vlm([hint], page_type="search_input") is False


# ── Compound search-edge synthesis (grounded dispatch for search) ────────────


def _tap_search_hint(confidence: float = 0.95) -> ActionHint:
    return ActionHint(
        target_page="search_result",
        action_type="Tap",
        region="top_right",
        grounded=False,  # bare tap submits stale text — co-pilot only
        confidence=confidence,
        description="点击搜索按钮",
        coordinates=(893, 71),
    )


def test_append_search_compound_synthesizes_from_tap():
    hints = [_tap_search_hint()]

    ActionAdvisor._append_search_compound(hints)

    assert len(hints) == 2
    compound = hints[1]
    assert compound.action_type == "Compound"
    assert compound.grounded is True
    assert compound.is_fast_executable()
    assert compound.confidence > 0.95
    assert compound.compound_steps[0] == {"action": "Type", "text": "<query>"}
    assert compound.compound_steps[1] == {"action": "Tap", "element": [893, 71]}


def test_append_search_compound_noop_without_tap_coordinates():
    hints = [
        ActionHint(
            target_page="search_result", action_type="Tap", region="top",
            grounded=True, confidence=0.9, description="无坐标", coordinates=None,
        )
    ]
    ActionAdvisor._append_search_compound(hints)
    assert len(hints) == 1


def test_append_search_compound_noop_when_compound_exists():
    existing = ActionHint(
        target_page="search_result", action_type="Compound", region="top",
        grounded=True, confidence=0.95, description="已有",
        compound_steps=({"action": "Type", "text": "<query>"},),
    )
    hints = [_tap_search_hint(), existing]
    ActionAdvisor._append_search_compound(hints)
    assert len(hints) == 2


def test_bare_search_tap_classified_ungrounded():
    from phone_agent.spatial.action_advisor import _UNGROUNDED_TRANSITIONS
    assert ("search_input", "search_result") in _UNGROUNDED_TRANSITIONS
