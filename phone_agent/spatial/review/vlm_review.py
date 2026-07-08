"""VLM-powered transition judge — extracted from TrajectoryReviewer._vlm_validate.

Fixes the dangerous parse-failure fallback (approve-all → unreviewed).
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class VlmTransitionJudge:
    """Use a strong VLM to validate/reject staged transition candidates.

    Environment variables (same as TrajectoryReviewer):
        AMSG_STRONG_VLM_MODEL
        AMSG_STRONG_VLM_BASE_URL
        AMSG_STRONG_VLM_API_KEY
    """

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass
        self._model = model or os.getenv("AMSG_STRONG_VLM_MODEL", "")
        self._base_url = base_url or os.getenv("AMSG_STRONG_VLM_BASE_URL", "")
        self._api_key = api_key or os.getenv("AMSG_STRONG_VLM_API_KEY", "")
        self._client: Any = None

    # ── availability ───────────────────────────────────────────

    def available(self) -> bool:
        return bool(self._model and self._base_url and self._api_key)

    def _ensure_client(self) -> bool:
        if self._client is not None:
            return True
        if not self.available():
            return False
        try:
            from openai import OpenAI
            self._client = OpenAI(api_key=self._api_key, base_url=self._base_url)
            return True
        except Exception as exc:
            logger.warning("VlmTransitionJudge: failed to create OpenAI client: %s", exc)
            return False

    # ── main API ───────────────────────────────────────────────

    def judge_transitions(
        self,
        items: list[dict[str, Any]],
        task_context: str = "",
    ) -> dict[int, tuple[bool, str]]:
        """Ask VLM to validate each item dict and return {index: (approved, reason)}.

        On ANY parse failure returns {} (empty = all unreviewed, never approve-all).

        Args:
            items: list of dicts with keys: source_page_type, target_page_type,
                   action_description, observations, explorer_confidence.
            task_context: optional human-readable context.
        Returns:
            dict mapping 0-based item index to (approved: bool, reason: str).
            Empty dict on VLM unavailability or parse failure.
        """
        if not items:
            return {}
        if not self._ensure_client():
            logger.info("VlmTransitionJudge: VLM not available, skipping")
            return {}

        transitions_text = "\n".join(
            f"{i+1}. {it.get('source_page_type','?')} → {it.get('target_page_type','?')} "
            f"(action={it.get('action_description','?')}, "
            f"obs={it.get('observations',0)}, conf={it.get('explorer_confidence','')})"
            for i, it in enumerate(items)
        )
        context_line = f"\n任务上下文: {task_context}" if task_context else ""
        prompt = (
            "你是手机 App 页面导航图谱的质量审核员。"
            f"{context_line}\n\n"
            "以下是从离线探索中提取的候选页面转换。\n"
            "请判断每条转换是否是**真实有效的页面导航**。\n\n"
            "有效标准:\n"
            "- 从一个明确页面类型跳转到另一个\n"
            "- 操作类型与转换方向合理\n"
            "- 不由弹窗/广告/误分类造成\n\n"
            f"候选转换:\n{transitions_text}\n\n"
            "逐条输出 JSON 数组（不要其他文字）:\n"
            '[{"index": 1, "valid": true, "reason": "..."}, ...]'
        )

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=2048,
                temperature=0.0,
                stream=False,
            )
            content = response.choices[0].message.content or ""
        except Exception as exc:
            logger.warning("VlmTransitionJudge.judge_transitions: API error: %s", exc)
            return {}

        # Parse — failure returns {} (never approve-all)
        try:
            json_match = re.search(r"\[[\s\S]*\]", content)
            if not json_match:
                logger.warning(
                    "VlmTransitionJudge: no JSON array in response; treating all as unreviewed"
                )
                return {}
            verdicts = json.loads(json_match.group())
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "VlmTransitionJudge: JSON parse failure (%s); treating all as unreviewed", exc
            )
            return {}

        result: dict[int, tuple[bool, str]] = {}
        for v in verdicts:
            if not isinstance(v, dict):
                continue
            idx = int(v.get("index", 0)) - 1
            if 0 <= idx < len(items):
                result[idx] = (bool(v.get("valid")), str(v.get("reason") or ""))
        return result

    def blind_classify_pair(
        self,
        before_png_path: str | Path,
        after_png_path: str | Path,
        page_type_names: list[str],
    ) -> tuple[str, str] | None:
        """Independently classify a before/after screenshot pair (双盲).

        Neither screenshot is told which page type the explorer assigned.
        Returns (before_classified, after_classified) or None on failure.

        Args:
            before_png_path: Absolute or relative path to before-action PNG.
            after_png_path: Absolute or relative path to after-action PNG.
            page_type_names: List of candidate page type names (from schema).
        """
        if not self._ensure_client():
            return None

        before_b64 = _png_to_b64(Path(before_png_path))
        after_b64 = _png_to_b64(Path(after_png_path))
        if not before_b64 or not after_b64:
            return None

        types_list = ", ".join(page_type_names[:30]) if page_type_names else "unknown"
        prompt = (
            "请分别判断以下两张手机截图各是什么页面类型。\n"
            f"可用页面类型: {types_list}\n"
            "格式（不要其他文字）:\n"
            '{"before": "<page_type>", "after": "<page_type>"}'
        )

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "截图1（前）:"},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{before_b64}"}},
                            {"type": "text", "text": "截图2（后）:"},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{after_b64}"}},
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
                max_tokens=128,
                temperature=0.0,
                stream=False,
            )
            content = response.choices[0].message.content or ""
        except Exception as exc:
            logger.warning("VlmTransitionJudge.blind_classify_pair: API error: %s", exc)
            return None

        try:
            json_match = re.search(r"\{[\s\S]*\}", content)
            if not json_match:
                return None
            parsed = json.loads(json_match.group())
            before_cls = str(parsed.get("before") or "")
            after_cls = str(parsed.get("after") or "")
            if before_cls and after_cls:
                return (before_cls, after_cls)
            return None
        except (json.JSONDecodeError, ValueError):
            return None


def _png_to_b64(path: Path) -> str:
    """Read a PNG file and return base64 string, or "" on error."""
    import base64
    try:
        return base64.b64encode(path.read_bytes()).decode("ascii")
    except (OSError, IOError):
        return ""
