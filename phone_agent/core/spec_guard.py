"""
SpecGuard — Purchase safety guard for shopping scenarios.

Intercepts spec-commit actions (add to cart, checkout, payment) and
cross-references the user's original task requirements against the
VLM's current selection.  Two entry points:

- ``get_context_hints()``  — inject VLM hints on spec/checkout pages
- ``check()``             — intercept before purchase commit
"""

from __future__ import annotations

import json
import re
from typing import Any

from phone_agent.core.task_spec import TaskSpecExtractor


# Semantic targets that indicate a purchase-commit action
_SPEC_COMMIT_TARGETS: frozenset[str] = frozenset({
    "confirm_spec_add_to_cart",
    "confirm_add_to_cart_success",
    "confirm_spec",
    "add_to_cart",
    "buy_now",
    "submit_order",
    "checkout",
    "payment",
})

_COMMIT_KEYWORDS: tuple[str, ...] = (
    "确定", "确认", "加入购物车", "加购", "立即购买", "购买",
    "提交订单", "结算", "支付", "confirm", "add to cart",
    "buy now", "checkout", "submit order", "pay",
)


class SpecGuard:
    """Purchase safety guard — stateless except for config reference.

    All dynamic data (task, vlm_plan, thinking) is passed explicitly.
    """

    def __init__(self, shopping_config: Any) -> None:
        self._shopping_config = shopping_config
        self._extractor = TaskSpecExtractor()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_context_hints(
        self,
        current_app: str,
        page_type: str | None,
        task: str,
        vlm_plan: dict[str, Any] | None = None,
    ) -> list[str]:
        """Return hints injected into VLM context on shopping spec pages."""
        if not self._is_shopping_app(current_app):
            return []
        if page_type not in {"spec_selection", "checkout", "payment"}:
            return []

        requested = self._requested_spec_slots(task, vlm_plan)
        if requested:
            return [
                "[SpecGuard]\n"
                f"用户已明确指定规格：{TaskSpecExtractor.format_specs_cn(requested)}。\n"
                "不要再询问用户。请先在当前页面选择这些规格；只有在规格已经匹配后，"
                "才能点击确认/加入购物车/立即购买。\n"
                "如果页面没有对应规格或无法判断是否匹配，先选择可见的匹配项或回退重试，"
                "不要进入结算、支付、地址或登录页面。"
            ]

        return [
            "[SpecGuard]\n"
            "当前可能处于规格选择或下单确认页面，但用户没有明确指定规格。\n"
            "不要默认替用户选择具体 SKU；如果下一步会确认规格、加入购物车或购买，"
            "应先询问用户需要哪个规格。"
        ]

    def check(
        self,
        action: dict[str, Any],
        thinking: str,
        current_app: str,
        page_type: str | None,
        task: str,
        vlm_plan: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Cross-reference user specs before purchase commit.

        Returns a modified Interact action if interception is needed,
        or ``None`` to let the original action proceed.
        """
        if not self._is_shopping_app(current_app):
            return None

        # Skip actions that are already interactive / terminal
        if action.get("action") == "Interact" or action.get("action_type") == "Interact":
            return None
        _metadata = (action.get("_metadata") or "").lower()
        if _metadata == "finish":
            return None
        _action_name = (action.get("action") or "").lower()
        if _action_name in ("terminate", "answer"):
            return None

        # Only guard spec-commit pages
        if page_type not in ("spec_selection", "checkout", "payment"):
            return None
        if not self._is_spec_commit_action(action, thinking, page_type):
            return None

        # Cross-reference user's original task
        user_requested_specs = self._requested_spec_slots(task, vlm_plan)

        if user_requested_specs:
            missing = [
                f"{k}={v}"
                for k, v in user_requested_specs.items()
                if not _is_spec_selected(thinking, k, v)
            ]
            if not missing:
                print(f"✅ [SpecGuard] 用户指定SKU已全部选中 ({user_requested_specs})，放行")
                return None

            if page_type not in ("checkout", "payment"):
                print(
                    f"⚠️ [SpecGuard] 用户已指定SKU但模型思考未证明已选中 "
                    f"({user_requested_specs})，不追问用户；交由VLM按显式规格继续选择"
                )
                return None

            question = (
                f"您要求的是{'，'.join(missing)}，"
                f"但当前页面尚未选择。请确认规格后继续。"
            )
        else:
            question = _build_spec_question(thinking)

        print(f"\n{'─' * 50}")
        print("🛑 [SpecGuard] 规格确认拦截")
        print(f"   用户任务: {task[:100]}")
        if user_requested_specs:
            print(f"   用户指定SKU: {user_requested_specs}")
        print(f"   模型试图: {thinking[:100]}...")
        print(f"   强制为: Interact → {question}")
        print(f"{'─' * 50}")

        return {
            "_metadata": "do",
            "action": "Interact",
            "action_type": "Interact",
            "message": question,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _is_shopping_app(self, current_app: str) -> bool:
        return any(app in (current_app or "") for app in self._shopping_config.apps)

    def _requested_spec_slots(
        self, task: str, vlm_plan: dict[str, Any] | None,
    ) -> dict[str, str]:
        """Merge explicit SKU constraints from task text and VLM pre-plan."""
        slots = self._extractor.extract(task)
        specs: dict[str, str] = dict(slots.spec_dict)  # Chinese-keyed

        # Enrich with VLM plan specs
        if isinstance(vlm_plan, dict):
            plan_specs = vlm_plan.get("specs", {})
            if isinstance(plan_specs, dict):
                for key, value in plan_specs.items():
                    if value:
                        cn_key = TaskSpecExtractor.normalize_spec_key(key)
                        if cn_key not in specs:
                            specs[cn_key] = str(value)
        return specs

    @staticmethod
    def _is_spec_commit_action(
        action: dict[str, Any], thinking: str, page_type: str | None,
    ) -> bool:
        """Return True only for actions that commit a spec/purchase choice."""
        if page_type not in ("spec_selection", "checkout", "payment"):
            return False

        action_name = str(action.get("action") or action.get("action_type") or "").lower()
        if action_name in ("interact", "back", "wait", "type", "launch", "home"):
            return False

        semantic_target = str(action.get("semantic_target") or action.get("target") or "").lower()
        if semantic_target in _SPEC_COMMIT_TARGETS:
            return True
        if any(token in semantic_target for token in _SPEC_COMMIT_TARGETS):
            return True

        selection_tokens = ("choose_spec", "select_spec", "颜色", "容量", "尺码", "规格项", "option")
        if any(token.lower() in semantic_target for token in selection_tokens):
            return False

        action_text = json.dumps(action, ensure_ascii=False).lower()
        combined = f"{action_text}\n{thinking}".lower()
        return any(kw.lower() in combined for kw in _COMMIT_KEYWORDS)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _is_spec_selected(thinking: str, spec_key: str, spec_value: str) -> bool:
    """Check whether VLM thinking indicates a spec value is selected."""
    val = spec_value.upper().replace(" ", "")
    val_short = val.replace("GB", "G").replace("TB", "T")

    if spec_value not in thinking and val not in thinking and val_short not in thinking:
        return False

    for sv in (spec_value, val, val_short):
        if re.search(
            rf"{re.escape(sv)}.{{0,20}}(?:已选中|已选[^项]|已选择|已勾选|✔|✓|\bselected\b|当前)",
            thinking,
        ):
            return True
        if re.search(rf"{re.escape(spec_key)}.*?{re.escape(sv)}", thinking) and any(
            m in thinking for m in ("已选中", "已选", "已选择", "已勾选")
        ):
            return True

    return False


def _build_spec_question(thinking: str) -> str:
    """Build a contextual question when the user hasn't specified exact SKU."""
    if "颜色" in thinking and "容量" in thinking:
        return "这里有多种颜色和容量可选，请问您需要哪个配置？"
    if "颜色" in thinking:
        return "有多种颜色可选，请问您喜欢哪个颜色？"
    if "容量" in thinking:
        return "有多种容量可选，请问您需要多大容量？"
    if "温度" in thinking:
        return "请问您需要什么温度？"
    if "糖度" in thinking:
        return "请问您需要什么糖度？"
    return "请问您需要什么规格和配置？"
