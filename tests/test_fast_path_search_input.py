"""Fast Path on search_input must execute the SEARCH, never a skip-search edge.

Real device (淘宝): the plan's step after search was filter_panel, so
_effective_plan_target advanced search_input -> filter_panel and a promoted
search_input->filter_panel edge fast-fired before "显示器" was ever searched.
Fix: on search_input the effective target is pinned to search_result, so the
compound type+search action stays eligible while the skip-search shortcut is
blocked.
"""

from __future__ import annotations

import types

from phone_agent.agent import PhoneAgent


class _Hint:
    def __init__(self, target_page, fast=True, conf=1.0):
        self.target_page = target_page
        self._fast = fast
        self.confidence = conf

    def is_fast_executable(self):
        return self._fast


def _plan(current_target, next_target=None):
    steps = [types.SimpleNamespace(target_page=current_target, status="current")]
    if next_target:
        steps.append(types.SimpleNamespace(target_page=next_target, status="pending"))
    return types.SimpleNamespace(steps=steps, current_step=lambda: steps[0])


# ── real _effective_plan_target pinning ──────────────────────────────


def test_effective_target_pinned_to_search_result_even_if_next_is_filter():
    agent = types.SimpleNamespace(_task_plan=_plan("search_input", next_target="filter_panel"))
    # Standing on search_input must target search_result, NOT the next step
    # (filter_panel) — that is the exact bug.
    assert PhoneAgent._effective_plan_target(agent, "search_input") == "search_result"


def test_effective_target_unchanged_off_search_input():
    agent = types.SimpleNamespace(_task_plan=_plan("filter_panel", next_target="search_result"))
    assert PhoneAgent._effective_plan_target(agent, "home") == "filter_panel"


# ── selection given the pinned target ────────────────────────────────


def _sel_agent():
    return types.SimpleNamespace(
        _task_plan=_plan("search_input"),
        _effective_plan_target=lambda pt: "search_result" if pt == "search_input" else "x",
    )


def test_filter_panel_shortcut_blocked_on_search_input():
    agent = _sel_agent()
    hints = [_Hint("filter_panel", fast=True, conf=1.0)]
    assert PhoneAgent._select_fast_action(agent, hints, "search_input") is None
    assert PhoneAgent._needs_vlm(agent, hints, "search_input") is True


def test_compound_search_selected_on_search_input():
    agent = _sel_agent()
    compound = _Hint("search_result", fast=True, conf=0.96)
    assert PhoneAgent._select_fast_action(agent, [compound], "search_input") is compound
    assert PhoneAgent._needs_vlm(agent, [compound], "search_input") is False


def test_search_wins_over_filter_panel_on_search_input():
    # The exact regression: a high-confidence filter_panel edge must NOT win
    # over the search.
    agent = _sel_agent()
    sel = PhoneAgent._select_fast_action(
        agent, [_Hint("filter_panel", True, 1.0), _Hint("search_result", True, 0.96)], "search_input",
    )
    assert sel is not None and sel.target_page == "search_result"
