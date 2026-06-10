"""Interference detection and dismissal handler for offline exploration (Phase 4a)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from .human_gate import HumanAssistRequest, HumanGate
from .stability import perceptual_hash


@dataclass(frozen=True)
class InterferencePolicy:
    """Configuration for how interference pages are handled."""

    dismiss_max_attempts: int = 3
    dismiss_strategies: tuple[str, ...] = ("close_button", "back", "tap_outside")
    login_action: str = "back_out"          # "back_out" | "pause_for_human"
    # Captcha challenges cannot be dismissed away - backing out just postpones
    # them. Default is to hand over to the human operator when a gate exists.
    captcha_action: str = "pause_for_human"  # "back_out" | "pause_for_human"
    pause_timeout_s: float = 600.0


# Summary tokens that reveal a captcha even when the classifier said "dialog".
_CAPTCHA_SUMMARY_TOKENS = ("人机验证", "安全验证", "滑块验证", "拼图验证", "按轨迹", "轨迹绘制", "captcha")


@dataclass(frozen=True)
class InterferenceEvent:
    """Record of an interference page encounter and how it was resolved."""

    page_type: str
    app: str
    screenshot_hash: str
    summary: str
    source_page_key: str
    resolution: str
    attempts: int
    encountered_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_type": self.page_type,
            "app": self.app,
            "screenshot_hash": self.screenshot_hash,
            "summary": self.summary,
            "source_page_key": self.source_page_key,
            "resolution": self.resolution,
            "attempts": self.attempts,
            "encountered_at": self.encountered_at,
        }


@dataclass(frozen=True)
class InterferenceOutcome:
    """Result of attempting to dismiss an interference page."""

    status: str                          # "resolved"|"needs_restart"|"human_resumed"|"unresolved"
    events: tuple[InterferenceEvent, ...]
    resumed_page: Any | None = None      # PageInfo | None


class InterferenceHandler:
    """Handles interference pages (dialogs, permissions, ads, login walls).

    Dismiss strategies are attempted in order:
      1. "close_button" — asks vlm_close_fn for close button coords, taps them.
      2. "back"         — executes a Back action.
      3. "tap_outside"  — taps at (500, 120) to dismiss overlay.

    After each attempt the screen is re-captured and re-classified.  If the
    landing page is no longer in the interference set, the page is resolved.

    Back-intercept dialogs (挽留弹窗): if Back was just tried and the landing
    page is still a dialog, the next close_button attempt passes a hint asking
    for 「退出/离开/狠心离开」 semantic buttons.

    Login / captcha walls: when page_type == "login" and policy.login_action ==
    "pause_for_human" and a human_gate is provided, the gate is invoked.  If
    the human resumes, a fresh capture+classify is returned.  If they skip/timeout,
    falls through to back_out behavior.
    """

    def __init__(
        self,
        policy: InterferencePolicy,
        space: Any,           # PageTypeSpace
        action_handler: Any,  # ActionHandler
        classify_fn: Callable[[Any, str, int], Any],   # (screenshot, app, step) -> PageInfo
        capture_fn: Callable[[], Any],                  # () -> screenshot
        log_fn: Callable[[str], None],
        human_gate: HumanGate | None = None,
        vlm_close_fn: Callable[[str, int, int, str], tuple[int, int] | None] | None = None,
    ) -> None:
        self._policy = policy
        self._space = space
        self._action_handler = action_handler
        self._classify_fn = classify_fn
        self._capture_fn = capture_fn
        self._log = log_fn
        self._human_gate = human_gate
        self._vlm_close_fn = vlm_close_fn

    def is_interference(self, page_info: Any) -> bool:
        """Return True if page_info.page_type is in the interference set."""
        if page_info is None:
            return False
        pt = _page_type_str(page_info)
        return pt in self._space.interference

    def handle(
        self,
        page_info: Any,
        screen_w: int,
        screen_h: int,
        source_page_key: str,
    ) -> InterferenceOutcome:
        """Attempt to dismiss an interference page.

        Args:
            page_info: The interference PageInfo currently on screen.
            screen_w:  Screen width in pixels.
            screen_h:  Screen height in pixels.
            source_page_key: The state_key() of the page before this transition.

        Returns:
            InterferenceOutcome with status and accumulated events.
        """
        page_type = _page_type_str(page_info)
        events: list[InterferenceEvent] = []

        # --- Login / captcha: hand over to the human operator ---
        if self._wants_human_assist(page_type, page_info):
            outcome = self._request_human_assist(page_type, page_info, source_page_key, events)
            if outcome is not None:
                return outcome
            # gate unavailable or operator skipped → fall through to dismissal

        # --- Dismiss loop ---
        attempt = 0
        just_tried_back = False
        current_page_info = page_info

        for strategy in self._policy.dismiss_strategies:
            attempt += 1
            if attempt > self._policy.dismiss_max_attempts:
                break

            # Execute strategy
            if strategy == "back":
                self._log(f"  [interference] strategy=back attempt={attempt}")
                try:
                    self._action_handler.execute(
                        {"_metadata": "do", "action": "Back"},
                        screen_w,
                        screen_h,
                    )
                except Exception as exc:
                    self._log(f"  [interference] back failed: {exc}")
                just_tried_back = True

            elif strategy == "close_button":
                hint = ""
                if just_tried_back and _page_type_str(current_page_info) in self._space.interference:
                    # Back-intercept dialog (挽留弹窗): look for exit-semantic button
                    hint = "找「退出」「离开」「狠心离开」「不看了」等语义的退出按钮"
                self._log(f"  [interference] strategy=close_button attempt={attempt} hint={hint!r}")
                if self._vlm_close_fn is not None:
                    b64 = getattr(current_page_info, "screenshot_base64", "") or ""
                    coords = None
                    try:
                        coords = self._vlm_close_fn(b64, screen_w, screen_h, hint)
                    except Exception as exc:
                        self._log(f"  [interference] vlm_close_fn error: {exc}")
                    if coords is not None:
                        try:
                            self._action_handler.execute(
                                {"_metadata": "do", "action": "Tap", "element": list(coords)},
                                screen_w,
                                screen_h,
                            )
                        except Exception as exc:
                            self._log(f"  [interference] tap close button failed: {exc}")
                    else:
                        self._log("  [interference] close_button: no coords returned, skipping")
                        continue
                else:
                    self._log("  [interference] close_button: no vlm_close_fn, skipping strategy")
                    continue
                just_tried_back = False

            elif strategy == "tap_outside":
                self._log(f"  [interference] strategy=tap_outside attempt={attempt}")
                try:
                    self._action_handler.execute(
                        {"_metadata": "do", "action": "Tap", "element": [500, 120]},
                        screen_w,
                        screen_h,
                    )
                except Exception as exc:
                    self._log(f"  [interference] tap_outside failed: {exc}")
                just_tried_back = False

            else:
                self._log(f"  [interference] unknown strategy: {strategy}")
                continue

            # Wait and re-capture
            time.sleep(1.5)
            try:
                screenshot = self._capture_fn()
                next_page = self._classify_fn(screenshot, current_page_info.app, 0)
            except Exception as exc:
                self._log(f"  [interference] capture/classify after dismiss failed: {exc}")
                continue

            current_page_info = next_page
            next_pt = _page_type_str(next_page)
            self._log(f"  [interference] post-dismiss page_type={next_pt}")

            if next_pt not in self._space.interference:
                # Resolved
                event = InterferenceEvent(
                    page_type=page_type,
                    app=page_info.app,
                    screenshot_hash=perceptual_hash(page_info.screenshot_base64 or ""),
                    summary=page_info.semantic_summary,
                    source_page_key=source_page_key,
                    resolution="resolved",
                    attempts=attempt,
                    encountered_at=datetime.now().isoformat(),
                )
                events.append(event)
                return InterferenceOutcome(
                    status="resolved",
                    events=tuple(events),
                    resumed_page=next_page,
                )

        # All dismiss attempts exhausted. If the stubborn page looks like a
        # captcha that the classifier labeled as a generic dialog, give the
        # human operator a chance before restarting the app - a restart only
        # postpones the challenge.
        if page_type != "captcha" and self._looks_like_captcha(current_page_info):
            self._log("  [interference] undismissable page looks like a captcha — asking human")
            outcome = self._request_human_assist("captcha", current_page_info, source_page_key, events)
            if outcome is not None:
                return outcome

        event = InterferenceEvent(
            page_type=page_type,
            app=page_info.app,
            screenshot_hash=perceptual_hash(page_info.screenshot_base64 or ""),
            summary=page_info.semantic_summary,
            source_page_key=source_page_key,
            resolution="needs_restart",
            attempts=attempt,
            encountered_at=datetime.now().isoformat(),
        )
        events.append(event)
        return InterferenceOutcome(
            status="needs_restart",
            events=tuple(events),
            resumed_page=None,
        )

    # ------------------------------------------------------------------
    # Human assist
    # ------------------------------------------------------------------

    def _wants_human_assist(self, page_type: str, page_info: Any) -> bool:
        if page_type == "login":
            return self._policy.login_action == "pause_for_human"
        if page_type == "captcha" or self._looks_like_captcha(page_info):
            return getattr(self._policy, "captcha_action", "pause_for_human") == "pause_for_human"
        return False

    @staticmethod
    def _looks_like_captcha(page_info: Any) -> bool:
        summary = str(getattr(page_info, "semantic_summary", "") or "").lower()
        return any(token in summary for token in _CAPTCHA_SUMMARY_TOKENS)

    def _request_human_assist(
        self,
        kind: str,
        page_info: Any,
        source_page_key: str,
        events: list[InterferenceEvent],
    ) -> InterferenceOutcome | None:
        """Invoke the human gate; None means unavailable/skipped (caller falls through)."""
        if self._human_gate is None:
            return None
        if kind == "captcha":
            message = (
                "探索器遇到人机验证（滑块/拼图/轨迹绘制等），程序无法自动完成。\n"
                "请在设备上手动完成验证后按回车继续探索；如需放弃该方向请输入 skip。"
            )
        else:
            message = (
                "探索器遇到登录墙。请在设备上完成登录后按回车继续。"
                "如需跳过请输入 skip。"
            )
        req = HumanAssistRequest(
            kind=kind,
            app=page_info.app,
            message=message,
            screenshot_path="",
        )
        result = self._human_gate.request(req)
        if result != "resumed":
            return None
        screenshot = self._capture_fn()
        resumed_page = self._classify_fn(screenshot, page_info.app, 0)
        events.append(
            InterferenceEvent(
                page_type=kind,
                app=page_info.app,
                screenshot_hash=perceptual_hash(getattr(page_info, "screenshot_base64", "") or ""),
                summary=getattr(page_info, "semantic_summary", ""),
                source_page_key=source_page_key,
                resolution="human_resumed",
                attempts=1,
                encountered_at=datetime.now().isoformat(),
            )
        )
        return InterferenceOutcome(
            status="human_resumed",
            events=tuple(events),
            resumed_page=resumed_page,
        )


def _page_type_str(page_info: Any) -> str:
    """Extract page type string from PageInfo."""
    if page_info is None:
        return ""
    pt = getattr(page_info, "page_type", "")
    if hasattr(pt, "value"):
        return str(pt.value)
    return str(pt)
