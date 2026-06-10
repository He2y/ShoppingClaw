"""HumanGate protocol and ConsoleHumanGate for interactive exploration pauses (Phase 4d)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class HumanAssistRequest:
    """Request for human assistance during exploration.

    Attributes:
        kind: Category of the request (e.g. "login", "captcha", "permission").
        app: Name of the app being explored.
        message: Human-readable description of what is needed.
        screenshot_path: Optional path to a saved screenshot for reference.
    """

    kind: str
    app: str
    message: str
    screenshot_path: str = ""


@runtime_checkable
class HumanGate(Protocol):
    """Protocol for human-in-the-loop assistance during exploration.

    Implementors block until the human completes (or skips) the requested action.
    """

    def request(self, req: HumanAssistRequest) -> str:
        """Handle a human assist request.

        Returns:
            "resumed" if the human completed the action and exploration can continue.
            "skip"    if the action was skipped or timed out.
        """
        ...


class ConsoleHumanGate:
    """Interactive console-based HumanGate.

    In interactive mode (default), prints the request details and blocks on
    ``input()`` until the user presses Enter (to resume) or types "skip".

    In non-interactive mode (``interactive=False`` or env
    ``EXPLORER_NONINTERACTIVE=1``), immediately returns "skip" without blocking.

    Note on Windows: ``input()`` blocks indefinitely — there is no OS-level
    timeout mechanism.  The ``timeout_s`` parameter is documented for future
    use but is NOT enforced on Windows.  Non-interactive mode is the only way
    to avoid blocking.
    """

    def __init__(self, timeout_s: float = 600.0, interactive: bool = True) -> None:
        self.timeout_s = timeout_s
        self._interactive = interactive

    def _is_interactive(self) -> bool:
        if not self._interactive:
            return False
        if os.environ.get("EXPLORER_NONINTERACTIVE", "").strip() == "1":
            return False
        return True

    def request(self, req: HumanAssistRequest) -> str:
        """Print request and optionally wait for human input.

        Returns "resumed" or "skip".
        """
        print("\n" + "=" * 60)
        print(f"[HumanGate] Human assistance required")
        print(f"  App:     {req.app}")
        print(f"  Kind:    {req.kind}")
        print(f"  Message: {req.message}")
        if req.screenshot_path:
            print(f"  Screenshot: {req.screenshot_path}")
        print("=" * 60)

        if not self._is_interactive():
            print("[HumanGate] Non-interactive mode — skipping.")
            return "skip"

        print("请在设备上完成操作后按回车继续，或输入 skip 放弃:")
        try:
            user_input = input().strip().lower()
        except (EOFError, OSError):
            # stdin not available (e.g., piped input exhausted)
            return "skip"

        if user_input == "skip":
            print("[HumanGate] Skipped by user.")
            return "skip"

        print("[HumanGate] Resumed.")
        return "resumed"
