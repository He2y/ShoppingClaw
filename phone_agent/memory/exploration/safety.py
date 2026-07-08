"""Schema-driven safety policy for offline exploration."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SafetyPolicy:
    """Safety constraints for exploration derived from a domain schema."""

    high_risk_page_types: frozenset[str]
    unsafe_tokens: tuple[str, ...]
    risky_cta_tokens: tuple[str, ...]
    risky_cta_allowed_pages: frozenset[str]
    trap_tokens: tuple[str, ...] = ()

    @classmethod
    def from_schema(cls, schema: Any, profile: Any = None) -> "SafetyPolicy":
        """Build a SafetyPolicy from a merged MobileSchema."""
        high_risk = frozenset(
            name
            for name, spec in schema.page_types.items()
            if getattr(spec, "risk", "normal") == "high"
        )
        exp = schema.exploration
        unsafe_tokens = exp.unsafe_tokens
        risky_cta_tokens = exp.risky_cta_tokens
        risky_cta_allowed = frozenset(exp.risky_cta_allowed_pages)
        trap_tokens = exp.trap_tokens

        # Apply profile overrides if provided
        if profile is not None:
            extra_unsafe = tuple(getattr(profile, "extra_unsafe_tokens", ()))
            if extra_unsafe:
                seen: set[str] = set(unsafe_tokens)
                extras: list[str] = []
                for t in extra_unsafe:
                    if t not in seen:
                        seen.add(t)
                        extras.append(t)
                unsafe_tokens = unsafe_tokens + tuple(extras)
            extra_trap = tuple(getattr(profile, "extra_trap_tokens", ()))
            if extra_trap:
                seen2: set[str] = set(trap_tokens)
                extras2: list[str] = []
                for t in extra_trap:
                    if t not in seen2:
                        seen2.add(t)
                        extras2.append(t)
                trap_tokens = trap_tokens + tuple(extras2)

        return cls(
            high_risk_page_types=high_risk,
            unsafe_tokens=unsafe_tokens,
            risky_cta_tokens=risky_cta_tokens,
            risky_cta_allowed_pages=risky_cta_allowed,
            trap_tokens=trap_tokens,
        )

    def is_safe_action(self, page_info: Any, action: dict, reasoning: str = "") -> bool:
        """Return True iff the action is safe to execute on page_info."""
        # Retreat/no-op actions carry no risk by themselves — blocking Back
        # because the reasoning text mentions 结算/checkout traps the agent
        # on the very page it is trying to leave.
        action_type = str(action.get("action") or action.get("action_type") or "").lower()
        if action.get("_metadata") == "finish" or action_type in {"back", "wait"}:
            return True
        page_type = _page_type_str(page_info)
        if page_type in self.high_risk_page_types:
            return False
        action_text = json.dumps(action, ensure_ascii=False).lower()
        if self.action_text_has_unsafe_token(action_text, page_info):
            return False
        return not self.unsafe_intent_mentioned(reasoning or "", page_info)

    def action_text_has_unsafe_token(self, text: str, page_info: Any) -> bool:
        """Return True if the serialized action text contains an unsafe token."""
        if any(token.lower() in text for token in self.unsafe_tokens):
            return True
        page_type = _page_type_str(page_info)
        if page_type in self.risky_cta_allowed_pages:
            return False
        return any(token.lower() in text for token in self.risky_cta_tokens)

    def unsafe_intent_mentioned(self, reasoning: str, page_info: Any = None) -> bool:
        """Return True if the reasoning text expresses intent to perform an unsafe action."""
        action_markers = ("我将", "我要", "准备", "下一步", "接下来", "现在", "让我", "tap")
        negation_markers = ("不要", "不能", "禁止", "避免", "不应该", "不会", "不点击", "不要点击")
        task_markers = ("用户要求", "任务流程", "具体步骤", "根据任务", "给出了", "包括：", "需要：")
        page_type = _page_type_str(page_info) if page_info is not None else ""
        for raw_line in reasoning.splitlines():
            line = raw_line.strip().lower()
            if not line:
                continue
            if any(marker in line for marker in task_markers):
                continue
            if line[:2].rstrip(".、").isdigit():
                continue
            if any(marker in line for marker in negation_markers):
                continue
            if not any(marker in line for marker in action_markers):
                continue
            if any(token.lower() in line for token in self.unsafe_tokens):
                return True
            if page_type not in self.risky_cta_allowed_pages:
                if any(token.lower() in line for token in self.risky_cta_tokens):
                    return True
        return False


def _page_type_str(page_info: Any) -> str:
    """Extract page type string from PageInfo (handles both str and Enum .value)."""
    if page_info is None:
        return ""
    pt = getattr(page_info, "page_type", "")
    if hasattr(pt, "value"):
        return str(pt.value)
    return str(pt)
