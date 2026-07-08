"""parse_action robustness to degraded small-model output.

Real device (京东): the autoglm model recited a whole plan (echoing the injected
[进度]/[当前] context) and emitted taps as `Tap(429, 126)` (no brackets) /
`[Tap] (272, 84)` instead of `do(action="Tap", element=[...])`. The parser only
accepted `do(...)` and `Tap([x,y])`, so it looped on parse failures + context
resets. These guard the degraded formats so the agent advances.
"""

from __future__ import annotations

import pytest

from phone_agent.actions.handler import parse_action


def test_standard_do_tap_still_parses():
    a = parse_action('do(action="Tap", element=[100, 200])')
    assert a["action"] == "Tap" and a["element"] == [100, 200]


def test_bracketed_bare_tap_still_parses():
    a = parse_action("Tap([429, 126])")
    assert a["action"] == "Tap" and a["element"] == [429, 126]


def test_no_bracket_tap_parses():
    # The exact failing case: "Tap(429, 126)" without [ ] around coords.
    a = parse_action("Tap(429, 126)")
    assert a["_metadata"] == "do"
    assert a["action"] == "Tap"
    assert a["element"] == [429, 126]


def test_finish_still_parses():
    a = parse_action('finish(message="done")')
    assert a["_metadata"] == "finish"
    assert a["message"] == "done"


def test_type_bare_still_parses():
    a = parse_action('Type("4K显示器")')
    assert a["action"] == "Type" and a["text"] == "4K显示器"


def test_degraded_blob_with_bracketed_tap_takes_last():
    # A recitation blob that buries the verb in brackets and ends with more
    # text — the last tap is the model's final decision.
    blob = (
        "用户要求...点击搜索框\n"
        "[进度] Tap(429, 126)\n"
        "[当前] 当前页面: 京东\n"
        "我需要点击搜索框。\n"
        "[Tap] (272, 84)\n"
        "[当前] 当前页面: 京东搜索"
    )
    a = parse_action(blob)
    assert a["action"] == "Tap"
    assert a["element"] == [272, 84]  # last tap, not the first (429,126)


def test_pure_prose_without_action_still_raises():
    with pytest.raises(ValueError):
        parse_action("我正在思考下一步该怎么做，但还没有决定具体操作。")
