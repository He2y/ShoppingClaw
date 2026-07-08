"""Tests for InterferenceHandler, HumanGate, and ConsoleHumanGate (Phase 4)."""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from phone_agent.memory.exploration.interference import (
    InterferenceEvent,
    InterferenceHandler,
    InterferenceOutcome,
    InterferencePolicy,
)
from phone_agent.memory.exploration.human_gate import (
    ConsoleHumanGate,
    HumanAssistRequest,
    HumanGate,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_screenshot_b64(color: tuple[int, int, int] = (200, 200, 200)) -> str:
    img = Image.new("RGB", (32, 32), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _make_page_info(page_type: str, summary: str = "test", app: str = "testapp") -> MagicMock:
    page = MagicMock()
    page.page_type = page_type
    page.semantic_summary = summary
    page.app = app
    page.screenshot_base64 = _make_screenshot_b64()
    page.width = 1080
    page.height = 2400

    def state_key() -> str:
        return f"{page_type}:{summary[:60]}"

    page.state_key = state_key
    return page


@dataclass(frozen=True)
class _FakePageTypeSpace:
    interference: frozenset[str] = frozenset({"dialog", "permission", "login", "ad"})


class _FakeActionHandler:
    def __init__(self) -> None:
        self.actions_executed: list[dict] = []

    def execute(self, action: dict, w: int, h: int) -> Any:
        self.actions_executed.append(action)
        result = MagicMock()
        result.success = True
        return result


class _RecordingHumanGate:
    def __init__(self, response: str) -> None:
        self._response = response
        self.requests: list[HumanAssistRequest] = []

    def request(self, req: HumanAssistRequest) -> str:
        self.requests.append(req)
        return self._response


# ── InterferenceHandler tests ─────────────────────────────────────────────────

class TestInterferenceHandlerIsInterference:
    def _make_handler(self) -> InterferenceHandler:
        space = _FakePageTypeSpace()
        return InterferenceHandler(
            policy=InterferencePolicy(),
            space=space,
            action_handler=_FakeActionHandler(),
            classify_fn=lambda s, a, n: _make_page_info("home"),
            capture_fn=lambda: MagicMock(base64_data=_make_screenshot_b64()),
            log_fn=lambda msg: None,
        )

    def test_dialog_is_interference(self) -> None:
        handler = self._make_handler()
        assert handler.is_interference(_make_page_info("dialog"))

    def test_home_not_interference(self) -> None:
        handler = self._make_handler()
        assert not handler.is_interference(_make_page_info("home"))

    def test_none_not_interference(self) -> None:
        handler = self._make_handler()
        assert not handler.is_interference(None)


class TestInterferenceHandlerDismiss:
    """Tests for the dismiss loop with fake classify sequences."""

    def _make_handler(
        self,
        classify_sequence: list[str],
        login_action: str = "back_out",
        human_gate: Optional[Any] = None,
        vlm_close_fn: Optional[Any] = None,
        strategies: tuple[str, ...] = ("back", "tap_outside"),
        max_attempts: int = 3,
    ) -> tuple[InterferenceHandler, _FakeActionHandler]:
        space = _FakePageTypeSpace()
        action_handler = _FakeActionHandler()

        # classify_fn pops from sequence; after exhausted returns last item
        idx = [0]
        def classify_fn(screenshot: Any, app: str, step: int) -> Any:
            i = min(idx[0], len(classify_sequence) - 1)
            idx[0] += 1
            return _make_page_info(classify_sequence[i])

        screenshot_mock = MagicMock()
        screenshot_mock.base64_data = _make_screenshot_b64()

        policy = InterferencePolicy(
            login_action=login_action,
            dismiss_strategies=strategies,
            dismiss_max_attempts=max_attempts,
        )
        handler = InterferenceHandler(
            policy=policy,
            space=space,
            action_handler=action_handler,
            classify_fn=classify_fn,
            capture_fn=lambda: screenshot_mock,
            log_fn=lambda msg: None,
            human_gate=human_gate,
            vlm_close_fn=vlm_close_fn,
        )
        return handler, action_handler

    def test_resolved_on_second_attempt(self) -> None:
        # First Back attempt returns dialog, second tap_outside attempt returns home
        # classify_fn is called AFTER each dismiss attempt: seq[0]="dialog", seq[1]="home"
        handler, action_handler = self._make_handler(["dialog", "home"])
        page = _make_page_info("dialog")
        outcome = handler.handle(page, 1080, 2400, source_page_key="home:test")
        assert outcome.status == "resolved"
        assert outcome.resumed_page is not None
        assert len(outcome.events) == 1
        assert outcome.events[0].resolution == "resolved"
        assert outcome.events[0].attempts == 2

    def test_needs_restart_after_all_attempts(self) -> None:
        # Always dialog — never resolves
        handler, _ = self._make_handler(["dialog"] * 10)
        page = _make_page_info("dialog")
        outcome = handler.handle(page, 1080, 2400, source_page_key="home:test")
        assert outcome.status == "needs_restart"
        assert len(outcome.events) == 1
        assert outcome.events[0].resolution == "needs_restart"

    def test_back_action_recorded(self) -> None:
        handler, action_handler = self._make_handler(["dialog", "home"])
        page = _make_page_info("dialog")
        handler.handle(page, 1080, 2400, source_page_key="home:test")
        # First strategy is "back" — Back action should have been executed
        back_actions = [
            a for a in action_handler.actions_executed
            if a.get("action") == "Back"
        ]
        assert len(back_actions) >= 1

    def test_interference_events_recorded(self) -> None:
        handler, _ = self._make_handler(["dialog", "home"])
        page = _make_page_info("dialog", summary="some overlay")
        outcome = handler.handle(page, 1080, 2400, source_page_key="product_detail:xyz")
        assert len(outcome.events) == 1
        evt = outcome.events[0]
        assert evt.page_type == "dialog"
        assert evt.source_page_key == "product_detail:xyz"
        assert evt.encountered_at != ""

    def test_event_to_dict(self) -> None:
        evt = InterferenceEvent(
            page_type="dialog",
            app="testapp",
            screenshot_hash="abc123",
            summary="test overlay",
            source_page_key="home:test",
            resolution="resolved",
            attempts=2,
            encountered_at="2025-01-01T00:00:00",
        )
        d = evt.to_dict()
        assert d["page_type"] == "dialog"
        assert d["resolution"] == "resolved"
        assert d["attempts"] == 2


class TestInterferenceHandlerLogin:
    def _space(self) -> _FakePageTypeSpace:
        return _FakePageTypeSpace()

    def test_login_with_human_gate_resumed(self) -> None:
        space = _FakePageTypeSpace()
        action_handler = _FakeActionHandler()
        resumed_page = _make_page_info("home")

        capture_count = [0]
        def capture_fn() -> Any:
            capture_count[0] += 1
            m = MagicMock()
            m.base64_data = _make_screenshot_b64()
            return m

        def classify_fn(screenshot: Any, app: str, step: int) -> Any:
            return resumed_page

        human_gate = _RecordingHumanGate("resumed")
        policy = InterferencePolicy(login_action="pause_for_human")
        handler = InterferenceHandler(
            policy=policy,
            space=space,
            action_handler=action_handler,
            classify_fn=classify_fn,
            capture_fn=capture_fn,
            log_fn=lambda msg: None,
            human_gate=human_gate,
        )

        login_page = _make_page_info("login")
        outcome = handler.handle(login_page, 1080, 2400, source_page_key="home:test")
        assert outcome.status == "human_resumed"
        assert outcome.resumed_page is resumed_page
        assert len(human_gate.requests) == 1
        assert human_gate.requests[0].kind == "login"

    def test_login_with_human_gate_skip_falls_to_back_out(self) -> None:
        space = _FakePageTypeSpace()
        action_handler = _FakeActionHandler()

        # After skip, classify sequence: login → home (resolved by first dismiss strategy)
        classify_seq = ["login", "home"]
        idx = [0]
        def classify_fn(screenshot: Any, app: str, step: int) -> Any:
            i = min(idx[0], len(classify_seq) - 1)
            idx[0] += 1
            return _make_page_info(classify_seq[i])

        def capture_fn() -> Any:
            m = MagicMock()
            m.base64_data = _make_screenshot_b64()
            return m

        human_gate = _RecordingHumanGate("skip")
        # Use back+tap_outside strategies so close_button skipping doesn't happen
        policy = InterferencePolicy(
            login_action="pause_for_human",
            dismiss_strategies=("back", "tap_outside"),
        )
        handler = InterferenceHandler(
            policy=policy,
            space=space,
            action_handler=action_handler,
            classify_fn=classify_fn,
            capture_fn=capture_fn,
            log_fn=lambda msg: None,
            human_gate=human_gate,
        )

        login_page = _make_page_info("login")
        outcome = handler.handle(login_page, 1080, 2400, source_page_key="home:test")
        # Human skipped → falls to back strategies → resolves
        assert outcome.status in ("resolved", "needs_restart")

    def test_login_back_out_no_human_gate(self) -> None:
        """login_action=back_out with no human_gate proceeds directly to dismiss loop."""
        space = _FakePageTypeSpace()
        action_handler = _FakeActionHandler()

        classify_seq = ["login", "home"]
        idx = [0]
        def classify_fn(screenshot: Any, app: str, step: int) -> Any:
            i = min(idx[0], len(classify_seq) - 1)
            idx[0] += 1
            return _make_page_info(classify_seq[i])

        def capture_fn() -> Any:
            m = MagicMock()
            m.base64_data = _make_screenshot_b64()
            return m

        # Use back+tap_outside to avoid close_button skip issues
        policy = InterferencePolicy(
            login_action="back_out",
            dismiss_strategies=("back", "tap_outside"),
        )
        handler = InterferenceHandler(
            policy=policy,
            space=space,
            action_handler=action_handler,
            classify_fn=classify_fn,
            capture_fn=capture_fn,
            log_fn=lambda msg: None,
        )
        login_page = _make_page_info("login")
        outcome = handler.handle(login_page, 1080, 2400, source_page_key="search_result:test")
        assert outcome.status == "resolved"


# ── ConsoleHumanGate tests ─────────────────────────────────────────────────────

class TestConsoleHumanGate:
    def test_noninteractive_returns_skip(self) -> None:
        gate = ConsoleHumanGate(interactive=False)
        req = HumanAssistRequest(kind="login", app="testapp", message="please login")
        result = gate.request(req)
        assert result == "skip"

    def test_env_noninteractive_returns_skip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EXPLORER_NONINTERACTIVE", "1")
        gate = ConsoleHumanGate(interactive=True)
        req = HumanAssistRequest(kind="captcha", app="testapp", message="solve captcha")
        result = gate.request(req)
        assert result == "skip"

    def test_interactive_enter_returns_resumed(self) -> None:
        gate = ConsoleHumanGate(interactive=True)
        req = HumanAssistRequest(kind="login", app="testapp", message="please login")
        with patch("builtins.input", return_value=""):
            result = gate.request(req)
        assert result == "resumed"

    def test_interactive_skip_input_returns_skip(self) -> None:
        gate = ConsoleHumanGate(interactive=True)
        req = HumanAssistRequest(kind="login", app="testapp", message="please login")
        with patch("builtins.input", return_value="skip"):
            result = gate.request(req)
        assert result == "skip"

    def test_eof_returns_skip(self) -> None:
        gate = ConsoleHumanGate(interactive=True)
        req = HumanAssistRequest(kind="login", app="testapp", message="please login")
        with patch("builtins.input", side_effect=EOFError):
            result = gate.request(req)
        assert result == "skip"

    def test_human_gate_protocol(self) -> None:
        """ConsoleHumanGate satisfies the HumanGate Protocol."""
        gate = ConsoleHumanGate(interactive=False)
        assert isinstance(gate, HumanGate)
