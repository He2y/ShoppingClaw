"""Dedicated VLM page classifier with screenshot cropping and provider fallback."""

import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Dict

from dotenv import load_dotenv
from openai import OpenAI
from PIL import Image

from .classifier_prompts import (
    _CLASSIFIER_FAST_SYSTEM_PROMPT,
    _CLASSIFIER_SYSTEM_PROMPT,
    build_fast_prompt,
    build_full_prompt,
)
from .types import ShoppingPageType, _PAGE_TYPE_MAP


# Crop ratios for removing persistent UI chrome before classification.
# Bottom nav bar (首页/购物车/我的 tabs) appears on every screen and
# pollutes both regex matching and VLM classification. By cropping it
# out, the classifier sees only the actual page content.
_CROP_TOP_RATIO = 0.04    # Status bar
_CROP_BOTTOM_RATIO = 0.12  # Bottom nav bar + safe area


@dataclass(frozen=True)
class _ClassifierProvider:
    source: str
    api_key: str
    base_url: str
    model: str
    max_tokens_fast: int = 512
    max_tokens_full: int = 1024


class PageClassifier:
    """Dedicated page classifier using a fast VLM with cropped screenshots.

    The bottom navigation bar ("首页", "购物车", "我的" tabs) appears on
    every screen and is the root cause of false-positive page classification.
    We crop it out before sending the image to the classifier, so the VLM
    only sees the actual page content area.

    Uses the configured strong VLM by default because classification is a graph
    quality gate, not the low-level action executor.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        mode: str = "fast",
        timeout: float = 8.0,
        max_image_width: int = 720,
        page_type_space: Any = None,
        schema: Any = None,
    ):
        self._page_type_space = page_type_space
        self._schema = schema

        explicit_override = bool(api_key and base_url and model)
        providers: list[_ClassifierProvider] = []
        if explicit_override:
            # All three explicitly provided — use as-is
            providers.append(
                _ClassifierProvider(
                    source="explicit",
                    api_key=api_key or "EMPTY",
                    base_url=base_url or "http://localhost:8000/v1",
                    model=model or "autoglm-phone-9b",
                )
            )
        else:
            load_dotenv()
            configured_providers = (
                (
                    "amsg_strong_vlm",
                    os.environ.get("AMSG_STRONG_VLM_API_KEY"),
                    os.environ.get("AMSG_STRONG_VLM_BASE_URL"),
                    os.environ.get("AMSG_STRONG_VLM_MODEL"),
                ),
                (
                    "offline_vlm",
                    os.environ.get("OFFLINE_VLM_API_KEY"),
                    os.environ.get("OFFLINE_VLM_BASE_URL"),
                    os.environ.get("OFFLINE_VLM_MODEL"),
                ),
                (
                    "phone_agent",
                    os.environ.get("PHONE_AGENT_API_KEY", "EMPTY"),
                    os.environ.get("PHONE_AGENT_BASE_URL", "http://localhost:8000/v1"),
                    os.environ.get("PHONE_AGENT_MODEL", "autoglm-phone-9b"),
                ),
            )
            for candidate_source, candidate_key, candidate_base, candidate_model in configured_providers:
                if candidate_key and candidate_base and candidate_model:
                    providers.append(
                        _ClassifierProvider(
                            source=candidate_source,
                            api_key=candidate_key,
                            base_url=candidate_base,
                            model=candidate_model,
                        )
                    )
        if not providers:
            providers.append(
                _ClassifierProvider(
                    source="phone_agent",
                    api_key="EMPTY",
                    base_url="http://localhost:8000/v1",
                    model="autoglm-phone-9b",
                )
            )

        self.providers = providers
        self._clients: dict[str, Any] = {}
        self.timeout = timeout
        first_provider = self.providers[0]
        self.client = self._client_for_provider(first_provider)
        self.model = first_provider.model
        self.base_url = first_provider.base_url
        self.source = first_provider.source
        self.mode = mode
        self.max_image_width = max_image_width
        self.max_tokens_fast = 512
        self.max_tokens_full = 1024
        self.last_duration = 0.0
        self.last_diagnostics: dict[str, Any] = {}
        self._cache: dict[
            tuple[str, str, tuple[tuple[str, str], ...], int],
            tuple[tuple[ShoppingPageType, str, Dict[str, str]], dict[str, Any]],
        ] = {}

    def _get_system_prompt(self, mode: str) -> str:
        """Get the system prompt, using schema-driven prompts when space is available."""
        if self._page_type_space is not None and self._schema is not None:
            if mode == "fast":
                return build_fast_prompt(self._page_type_space, self._schema)
            else:
                return build_full_prompt(self._page_type_space, self._schema)
        # Legacy prompts
        return _CLASSIFIER_FAST_SYSTEM_PROMPT if mode == "fast" else _CLASSIFIER_SYSTEM_PROMPT

    def _client_for_provider(self, provider: _ClassifierProvider) -> Any:
        cache_key = f"{provider.source}|{provider.base_url}|{provider.model}"
        if cache_key not in self._clients:
            self._clients[cache_key] = OpenAI(
                base_url=provider.base_url,
                api_key=provider.api_key,
                timeout=self.timeout,
            )
        return self._clients[cache_key]

    def classify(self, screenshot_base64: str, width: int, height: int) -> tuple[ShoppingPageType, str, Dict[str, str]]:
        """Classify page type and extract elements from a cropped screenshot.

        This is the legacy enum-returning method.  New code should prefer
        ``classify_page`` which returns str page types and extra diagnostics.

        Args:
            screenshot_base64: Full screenshot as base64 string.
            width: Screenshot width in pixels.
            height: Screenshot height in pixels.

        Returns:
            (ShoppingPageType, summary, elements_dict). Returns (UNKNOWN, reason, {})
            on any failure.
        """
        page_type_str, summary, elements, extras = self.classify_page(screenshot_base64, width, height)
        # Map str → enum; new:* and unrecognized → UNKNOWN
        enum_val = _PAGE_TYPE_MAP.get(page_type_str, ShoppingPageType.UNKNOWN)
        return (enum_val, summary, elements)  # type: ignore[return-value]

    def classify_page(
        self,
        screenshot_base64: str,
        width: int,
        height: int,
    ) -> tuple[str, str, Dict[str, str], Dict[str, Any]]:
        """Classify a page and return string page type with extra diagnostics.

        Returns:
            (page_type_str, summary, elements, extras)
            where extras may contain ``new_type_description`` and classifier
            diagnostics.  ``page_type_str`` is normalized via space.normalize
            when a PageTypeSpace is available.
        """
        start_time = time.time()
        if self.mode == "off":
            self.last_duration = 0.0
            self.last_diagnostics = {
                "classifier_source": self.source,
                "classifier_model": self.model,
                "fallback_used": False,
                "raw_error": "",
            }
            return "unknown", "classifier disabled", {}, {}

        screenshot_key = hashlib.md5(screenshot_base64.encode()).hexdigest()
        provider_signature = tuple((provider.source, provider.model) for provider in self.providers)
        cache_key = (screenshot_key, self.mode, provider_signature, self.max_image_width)
        if cache_key in self._cache:
            self.last_duration = 0.0
            cached_result, cached_diagnostics = self._cache[cache_key]
            self.last_diagnostics = dict(cached_diagnostics)
            # Convert cached enum result to str for classify_page
            page_type_enum = cached_result[0]
            pt_str = page_type_enum.value if hasattr(page_type_enum, "value") else str(page_type_enum)
            return pt_str, cached_result[1], cached_result[2], {}

        try:
            cropped_b64 = self._crop_screenshot(screenshot_base64, width, height, self.max_image_width)
        except Exception as e:
            self.last_duration = time.time() - start_time
            self.last_diagnostics = {
                "classifier_source": self.source,
                "classifier_model": self.model,
                "fallback_used": False,
                "raw_error": f"crop error: {e}",
            }
            return "unknown", f"crop error: {e}", {}, {}

        prompt = self._get_system_prompt(self.mode)
        errors: list[str] = []
        for provider_index, provider in enumerate(self.providers):
            max_tokens = provider.max_tokens_fast if self.mode == "fast" else provider.max_tokens_full
            raw = ""
            try:
                client = self._client_for_provider(provider)
                response = client.chat.completions.create(
                    model=provider.model,
                    messages=[
                        {"role": "system", "content": prompt},
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {"url": f"data:image/png;base64,{cropped_b64}"},
                                },
                                {"type": "text", "text": "Classify this page as JSON."},
                            ],
                        },
                    ],
                    max_tokens=max_tokens,
                    temperature=0.0,
                )
                message = response.choices[0].message
                raw = getattr(message, "content", None) or ""
                reasoning = (
                    getattr(message, "reasoning_content", None)
                    or getattr(message, "reasoning", None)
                    or ""
                )
                if not raw.strip():
                    reason = "empty content"
                    if reasoning:
                        reason += ", reasoning-only"
                    raise ValueError(reason)
                try:
                    result = self._parse_json_object(raw)
                except Exception as parse_error:
                    inferred = self._infer_result_from_text(raw)
                    if not inferred:
                        raise ValueError(f"non-json classifier output: {parse_error}") from parse_error
                    result = inferred
                if not result:
                    raise ValueError("empty classifier result")

                raw_page_type = str(result.get("page_type", "")).strip()
                # Normalize through space if available, else use legacy map
                if self._page_type_space is not None:
                    page_type_str = self._page_type_space.normalize(raw_page_type)
                else:
                    # Normalize: keep known types, fallback to "unknown"
                    if raw_page_type in _PAGE_TYPE_MAP:
                        page_type_str = raw_page_type
                    else:
                        page_type_str = "unknown"

                summary = str(result.get("summary", "")).strip() or f"unnamed-{page_type_str}"
                elements = {} if self.mode == "fast" else result.get("elements", {})
                if not isinstance(elements, dict):
                    elements = {}

                extras: Dict[str, Any] = {}
                if result.get("new_type_description"):
                    extras["new_type_description"] = str(result["new_type_description"])

                self.client = client
                self.model = provider.model
                self.base_url = provider.base_url
                self.source = provider.source
                self.last_duration = time.time() - start_time
                diagnostics = {
                    "classifier_source": provider.source,
                    "classifier_model": provider.model,
                    "fallback_used": provider_index > 0,
                    "raw_error": "",
                    "raw_content": raw[:500],
                    "max_tokens": max_tokens,
                }
                self.last_diagnostics = diagnostics
                extras.update(diagnostics)

                # Cache the result in legacy format for backward compat
                enum_val = _PAGE_TYPE_MAP.get(page_type_str, ShoppingPageType.UNKNOWN)
                self._cache[cache_key] = ((enum_val, summary, elements), diagnostics)  # type: ignore

                return page_type_str, summary, elements, extras
            except Exception as e:
                errors.append(f"{provider.source}/{provider.model}: {e}")
                continue

        self.last_duration = time.time() - start_time
        raw_error = " | ".join(errors) if errors else "no classifier provider configured"
        self.last_diagnostics = {
            "classifier_source": self.source,
            "classifier_model": self.model,
            "fallback_used": len(self.providers) > 1,
            "raw_error": raw_error,
        }
        return "unknown", f"API/parse error: {raw_error}", {}, {"raw_error": raw_error}

    @staticmethod
    def _parse_json_object(raw: str) -> dict[str, Any]:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            first_newline = cleaned.find("\n")
            if first_newline != -1:
                cleaned = cleaned[first_newline + 1:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()
        if not cleaned.startswith("{"):
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start != -1 and end != -1 and end > start:
                cleaned = cleaned[start : end + 1]
        result = json.loads(cleaned)
        return result if isinstance(result, dict) else {}

    @staticmethod
    def _infer_result_from_text(raw: str) -> dict[str, str]:
        text = (raw or "").lower()
        if not text:
            return {}
        rules: tuple[tuple[str, tuple[str, ...], str], ...] = (
            ("settings", ("设置", "账号与安全", "隐私设置", "通用设置", "消息通知", "支付设置", "settings"), "设置页"),
            ("filter_panel", ("全部筛选", "价格区间", "筛选选项", "自定最低价", "自定最高价", "filter"), "商品筛选面板"),
            ("spec_selection", ("规格", "sku", "数量选择", "颜色分类", "机身颜色", "版本", "确认选择"), "商品规格选择"),
            ("checkout", ("订单确认", "提交订单", "收货地址", "配送方式"), "订单确认页"),
            ("cart", ("购物车", "全选", "结算", "商品列表"), "购物车列表"),
            ("product_detail", ("商品详情", "商品主图", "价格", "店铺", "加入购物车", "立即购买"), "商品详情页"),
            ("search_result", ("搜索结果", "综合", "销量", "筛选", "商品卡片"), "商品搜索结果"),
            ("search_input", ("历史搜索", "猜你想搜", "搜索框", "键盘"), "搜索输入页"),
            ("home", ("首页", "推荐", "频道导航", "搜索栏"), "电商首页"),
            ("login", ("登录", "验证码", "手机号"), "登录页"),
            ("address", ("地址", "收货人", "地址列表"), "地址页"),
        )
        for page_type, keywords, summary in rules:
            if any(keyword.lower() in text for keyword in keywords):
                return {"page_type": page_type, "summary": summary}
        return {"page_type": "unknown", "summary": "无法解析页面"}

    @staticmethod
    def _crop_screenshot(base64_str: str, width: int, height: int, max_width: int = 720) -> str:
        """Crop out status bar (top) and nav bar (bottom) from screenshot.

        Keeps only the main content area so the classifier isn't confused
        by persistent UI chrome.
        """
        img_data = base64.b64decode(base64_str)
        img = Image.open(BytesIO(img_data))

        crop_top = int(height * _CROP_TOP_RATIO)
        crop_bottom = int(height * (1.0 - _CROP_BOTTOM_RATIO))

        if crop_top >= crop_bottom or crop_bottom - crop_top < 100:
            # Screen too small to crop meaningfully, use as-is
            return base64_str

        cropped = img.crop((0, crop_top, width, crop_bottom))
        if max_width > 0 and cropped.width > max_width:
            ratio = max_width / cropped.width
            resized_height = max(1, int(cropped.height * ratio))
            cropped = cropped.resize((max_width, resized_height), Image.Resampling.LANCZOS)

        buf = BytesIO()
        cropped.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()
