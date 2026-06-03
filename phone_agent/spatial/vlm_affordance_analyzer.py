"""VLM-driven structural affordance analysis for AMSG pages.

Replaces keyword-based FunctionalityExtractor with VLM analysis that
identifies **page-level capabilities** rather than element-level instances.

Each page gets a compact set of structural affordances:
  - Stable across sessions (not tied to specific product/query)
  - Resilient to UI updates (defined by function, not position)
  - Reusable for planning (agent knows "I can search" not "I can click 耳机")
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StructuralAffordance:
    """A reusable page-level capability."""
    role: str
    description: str
    element_pattern: str
    expected_postcondition: str = ""
    risk_level: str = "normal"
    is_navigation: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "description": self.description,
            "element_pattern": self.element_pattern,
            "expected_postcondition": self.expected_postcondition,
            "risk_level": self.risk_level,
            "is_navigation": self.is_navigation,
        }


_ANALYSIS_PROMPT = (
    "你是移动应用UI分析师。分析这个页面截图，列出所有**结构性功能**（不是具体内容）。\n\n"
    "结构性功能 = 这个页面类型始终具备的交互能力，不受具体商品/搜索词/活动影响。\n"
    "例如：搜索结果页始终有'筛选'功能，但具体商品卡片不是结构性功能（内容会变）。\n\n"
    "输出JSON数组，每个元素：\n"
    "{\n"
    '  "role": "语义角色名（英文下划线，如 open_search / filter_results / go_back）",\n'
    '  "description": "一句话中文描述这个功能做什么",\n'
    '  "element_pattern": "触发元素的通用描述（如[搜索框]而非[显示耳机的搜索框]）",\n'
    '  "expected_postcondition": "执行后预期到达的页面类型，如search_result（无则留空）",\n'
    '  "risk_level": "normal/medium/high",\n'
    '  "is_navigation": true\n'
    "}\n\n"
    "原则：\n"
    "- 只列结构性功能，不列具体商品/活动/推荐内容\n"
    "- 商品卡片列表算一个功能'browse_products'，不要列每个商品\n"
    "- 搜索框算一个功能'execute_search'，不要列具体搜索词\n"
    "- 底部导航栏的每个入口各算一个功能\n"
    "- 通常每页5-10个结构性功能\n"
)


class VLMAffordanceAnalyzer:
    """Use strong VLM to extract structural affordances from page screenshots."""

    def __init__(self) -> None:
        self._client: Any | None = None
        self._model: str = ""
        self._configured = False
        self._init_client()

    def _init_client(self) -> None:
        from dotenv import load_dotenv
        load_dotenv()
        for base_key, model_key, api_key in [
            ("AMSG_STRONG_VLM_BASE_URL", "AMSG_STRONG_VLM_MODEL", "AMSG_STRONG_VLM_API_KEY"),
            ("OFFLINE_VLM_BASE_URL", "OFFLINE_VLM_MODEL", "OFFLINE_VLM_API_KEY"),
        ]:
            base_url = os.environ.get(base_key, "")
            model = os.environ.get(model_key, "")
            key = os.environ.get(api_key, "")
            if base_url and model and key:
                try:
                    from openai import OpenAI
                    self._client = OpenAI(base_url=base_url, api_key=key, timeout=20.0)
                    self._model = model
                    self._configured = True
                except Exception:
                    pass
                break

    @property
    def configured(self) -> bool:
        return self._configured

    def analyze_page(
        self,
        screenshot_base64: str,
        page_type: str,
        app_name: str = "",
    ) -> list[StructuralAffordance]:
        if not self._configured or not self._client:
            return []

        user_text = (
            f"App: {app_name}\n"
            f"页面类型: {page_type}\n"
            f"请分析截图中的结构性功能，输出JSON数组。"
        )
        try:
            response = self._client.chat.completions.create(
                model=self._model, temperature=0, max_tokens=1200,
                messages=[
                    {"role": "system", "content": _ANALYSIS_PROMPT},
                    {"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_base64}"}},
                        {"type": "text", "text": user_text},
                    ]},
                ],
            )
            content = response.choices[0].message.content or ""
            return self._parse_affordances(content, page_type)
        except Exception:
            return []

    def analyze_page_without_screenshot(
        self,
        page_type: str,
        elements: dict[str, str],
        summary: str = "",
        app_name: str = "",
    ) -> list[StructuralAffordance]:
        """Analyze from element data when screenshot is unavailable."""
        if not self._configured or not self._client:
            return _fallback_affordances(page_type, elements)

        elements_str = ", ".join(f"{k}: {v}" for k, v in list(elements.items())[:10])
        user_text = (
            f"App: {app_name}\n"
            f"页面类型: {page_type}\n"
            f"页面描述: {summary}\n"
            f"检测到的UI元素: {elements_str}\n"
            f"基于以上信息，列出这个页面的结构性功能，输出JSON数组。"
        )
        try:
            response = self._client.chat.completions.create(
                model=self._model, temperature=0, max_tokens=1000,
                messages=[
                    {"role": "system", "content": _ANALYSIS_PROMPT},
                    {"role": "user", "content": user_text},
                ],
            )
            content = response.choices[0].message.content or ""
            return self._parse_affordances(content, page_type)
        except Exception:
            return _fallback_affordances(page_type, elements)

    def _parse_affordances(self, content: str, page_type: str) -> list[StructuralAffordance]:
        content = content.strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?", "", content).strip()
            content = re.sub(r"```$", "", content).strip()

        match = re.search(r"\[.*\]", content, flags=re.DOTALL)
        if not match:
            return []
        try:
            items = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []

        result = []
        for item in items:
            if not isinstance(item, dict) or not item.get("role"):
                continue
            result.append(StructuralAffordance(
                role=str(item["role"]),
                description=str(item.get("description", "")),
                element_pattern=str(item.get("element_pattern", "")),
                expected_postcondition=str(item.get("expected_postcondition", "")),
                risk_level=str(item.get("risk_level", "normal")),
                is_navigation=bool(item.get("is_navigation", True)),
            ))
        return result


def _fallback_affordances(page_type: str, elements: dict[str, str]) -> list[StructuralAffordance]:
    """Deterministic fallback when VLM is unavailable."""
    affordances = []

    if page_type == "home":
        affordances = [
            StructuralAffordance("open_search", "点击搜索框进入搜索", "搜索框", "search_input"),
            StructuralAffordance("browse_categories", "浏览分类或功能入口", "分类/功能图标区域"),
            StructuralAffordance("browse_products", "浏览推荐商品", "商品推荐流", "product_detail"),
            StructuralAffordance("nav_to_cart", "底部导航到购物车", "底部导航-购物车", "cart", "medium"),
            StructuralAffordance("nav_to_account", "底部导航到个人中心", "底部导航-我的", "my_account"),
        ]
    elif page_type == "search_input":
        affordances = [
            StructuralAffordance("execute_search", "输入关键词并搜索", "搜索框+搜索按钮", "search_result"),
            StructuralAffordance("pick_history", "点击历史搜索词", "历史搜索标签", "search_result"),
            StructuralAffordance("go_back", "返回上一页", "返回按钮"),
        ]
    elif page_type == "search_result":
        affordances = [
            StructuralAffordance("browse_products", "点击商品卡片进入详情", "商品卡片", "product_detail"),
            StructuralAffordance("filter_results", "打开筛选面板", "筛选按钮", "filter_panel"),
            StructuralAffordance("sort_results", "切换排序方式", "排序标签栏"),
            StructuralAffordance("refine_search", "修改搜索关键词", "搜索框", "search_input"),
            StructuralAffordance("go_back", "返回上一页", "返回按钮"),
        ]
    elif page_type == "product_detail":
        affordances = [
            StructuralAffordance("add_to_cart", "加入购物车(可能触发规格选择)", "加入购物车按钮", "spec_selection", "medium"),
            StructuralAffordance("buy_now", "立即购买(可能触发规格选择)", "立即购买/领券购买按钮", "spec_selection", "medium"),
            StructuralAffordance("view_cart", "查看购物车", "顶部购物车图标", "cart", "medium"),
            StructuralAffordance("view_specs", "查看规格选项", "规格选择栏", "spec_selection"),
            StructuralAffordance("go_back", "返回上一页", "返回按钮"),
        ]
    elif page_type == "spec_selection":
        affordances = [
            StructuralAffordance("confirm_add_cart", "确认规格并加购", "加入购物车按钮", "cart", "medium"),
            StructuralAffordance("select_variant", "选择颜色/规格", "规格选项"),
            StructuralAffordance("close_panel", "关闭规格面板", "关闭按钮/返回", "product_detail"),
        ]
    elif page_type == "cart":
        affordances = [
            StructuralAffordance("checkout", "去结算", "结算按钮", "checkout", "high"),
            StructuralAffordance("edit_items", "编辑购物车商品", "编辑/删除按钮"),
            StructuralAffordance("nav_to_home", "底部导航到首页", "底部导航-首页", "home"),
        ]
    elif page_type == "filter_panel":
        affordances = [
            StructuralAffordance("apply_filter", "确认筛选条件", "确定按钮", "search_result"),
            StructuralAffordance("reset_filter", "重置筛选", "清除按钮"),
            StructuralAffordance("close_filter", "关闭筛选面板", "关闭/返回", "search_result"),
        ]
    else:
        affordances = [
            StructuralAffordance("go_back", "返回上一页", "返回按钮"),
        ]

    return affordances
