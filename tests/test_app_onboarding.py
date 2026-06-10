"""Tests for Phase 3: app onboarding + AppProfile + draft gate.

Coverage:
- AppProfile YAML round-trip via AppProfileRegistry
- AppProfileRegistry.resolve by alias / package / display_name
- AppOnboarder with fake device + fake VLM → draft profile written
- Invalid domain from VLM → falls back to common_mobile with halved confidence
- VLM failure → fallback profile with confidence 0.0
- Draft gate: auto_import refused for draft, allowed for confirmed
- Explorer + profile: coverage overrides, unsafe/trap tokens reach SafetyPolicy
"""

from __future__ import annotations

import base64
import json
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tiny_png_b64() -> str:
    """Return a tiny but valid PNG encoded as base64."""
    from PIL import Image

    img = Image.new("RGB", (4, 4), color=(100, 150, 200))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _make_fake_screenshot(b64: str = "", width: int = 1080, height: int = 1920) -> Any:
    sc = MagicMock()
    sc.base64_data = b64 or _make_tiny_png_b64()
    sc.width = width
    sc.height = height
    return sc


def _make_fake_device_factory(screenshot_b64: str = "") -> Any:
    factory = MagicMock()
    sc = _make_fake_screenshot(screenshot_b64)
    factory.get_screenshot.return_value = sc
    factory.launch_app.return_value = True
    return factory


# ---------------------------------------------------------------------------
# A. AppProfile YAML round-trip
# ---------------------------------------------------------------------------


def test_app_profile_yaml_roundtrip(tmp_path: Path) -> None:
    from phone_agent.spatial.app_profiles import AppProfile, AppProfileRegistry

    registry = AppProfileRegistry(profile_dir=tmp_path)

    profile = AppProfile(
        app_id="jd",
        display_name="京东",
        package="com.jingdong.app.mall",
        schema_name="shopping",
        aliases=("JD", "jingdong"),
        status="draft",
        coverage_page_types=("home", "search_result", "product_detail"),
        coverage_transitions=(("home", "search_result"), ("search_result", "product_detail")),
        unsafe_tokens=("立即购买", "提交订单"),
        trap_tokens=("直播", "领券"),
        login_wall_on_launch=False,
        default_task="探索京东购物流程",
        onboarding_notes="auto-generated",
    )

    path = registry.save(profile)
    assert path.exists()
    assert path.name == "jd.yaml"

    # Reload
    loaded = registry.load("jd")
    assert loaded is not None
    assert loaded.app_id == "jd"
    assert loaded.display_name == "京东"
    assert loaded.package == "com.jingdong.app.mall"
    assert loaded.schema_name == "shopping"
    assert "JD" in loaded.aliases
    assert loaded.status == "draft"
    assert "home" in loaded.coverage_page_types
    assert ("home", "search_result") in loaded.coverage_transitions
    assert "立即购买" in loaded.unsafe_tokens
    assert "直播" in loaded.trap_tokens
    assert loaded.default_task == "探索京东购物流程"


def test_app_profile_registry_readme_created(tmp_path: Path) -> None:
    from phone_agent.spatial.app_profiles import AppProfile, AppProfileRegistry

    registry = AppProfileRegistry(profile_dir=tmp_path)
    profile = AppProfile(
        app_id="test_app",
        display_name="Test App",
        package="com.test.app",
        schema_name="common_mobile",
    )
    registry.save(profile)
    readme = tmp_path / "README.md"
    assert readme.exists()


# ---------------------------------------------------------------------------
# B. resolve by alias / package / display_name
# ---------------------------------------------------------------------------


def test_resolve_by_display_name(tmp_path: Path) -> None:
    from phone_agent.spatial.app_profiles import AppProfile, AppProfileRegistry

    registry = AppProfileRegistry(profile_dir=tmp_path)
    profile = AppProfile(
        app_id="jd",
        display_name="京东",
        package="com.jingdong.app.mall",
        schema_name="shopping",
        aliases=("JD",),
    )
    registry.save(profile)

    assert registry.resolve("京东") is not None
    assert registry.resolve("京东").app_id == "jd"


def test_resolve_by_alias(tmp_path: Path) -> None:
    from phone_agent.spatial.app_profiles import AppProfile, AppProfileRegistry

    registry = AppProfileRegistry(profile_dir=tmp_path)
    profile = AppProfile(
        app_id="jd",
        display_name="京东",
        package="com.jingdong.app.mall",
        schema_name="shopping",
        aliases=("JD", "jingdong"),
    )
    registry.save(profile)

    assert registry.resolve("jd").app_id == "jd"
    assert registry.resolve("JD").app_id == "jd"
    assert registry.resolve("jingdong").app_id == "jd"


def test_resolve_by_package(tmp_path: Path) -> None:
    from phone_agent.spatial.app_profiles import AppProfile, AppProfileRegistry

    registry = AppProfileRegistry(profile_dir=tmp_path)
    profile = AppProfile(
        app_id="pdd",
        display_name="拼多多",
        package="com.xunmeng.pinduoduo",
        schema_name="shopping",
    )
    registry.save(profile)

    assert registry.resolve("com.xunmeng.pinduoduo").app_id == "pdd"


def test_resolve_missing_returns_none(tmp_path: Path) -> None:
    from phone_agent.spatial.app_profiles import AppProfileRegistry

    registry = AppProfileRegistry(profile_dir=tmp_path)
    assert registry.resolve("nonexistent_app") is None


def test_list_profiles(tmp_path: Path) -> None:
    from phone_agent.spatial.app_profiles import AppProfile, AppProfileRegistry

    registry = AppProfileRegistry(profile_dir=tmp_path)
    for app_id, name in [("aaa", "App A"), ("bbb", "App B")]:
        registry.save(AppProfile(
            app_id=app_id, display_name=name,
            package=f"com.{app_id}", schema_name="common_mobile"
        ))

    profiles = registry.list_profiles()
    assert len(profiles) == 2
    assert profiles[0].app_id == "aaa"
    assert profiles[1].app_id == "bbb"


# ---------------------------------------------------------------------------
# C. AppOnboarder with fake VLM → valid domain
# ---------------------------------------------------------------------------


def _make_valid_vlm_response(domain: str = "shopping") -> str:
    return json.dumps({
        "domain": domain,
        "confidence": 0.9,
        "app_id": "jd",
        "display_name": "京东",
        "aliases": ["JD"],
        "coverage_page_types": ["home", "search_result", "product_detail", "cart"],
        "coverage_transitions": [["home", "search_result"], ["search_result", "product_detail"]],
        "unsafe_tokens": ["立即购买", "提交订单"],
        "trap_tokens": ["直播", "视频"],
        "login_wall_on_launch": False,
        "notes": "Standard JD shopping app",
        "suggested_task": "探索京东购物核心流程",
    })


def _make_fake_vlm_client(response_text: str) -> Any:
    client = MagicMock()
    message = MagicMock()
    message.content = response_text
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    client.chat.completions.create.return_value = response
    return client


def test_onboarder_valid_vlm_creates_draft_profile(tmp_path: Path) -> None:
    from phone_agent.spatial.app_profiles import AppProfileRegistry
    from phone_agent.spatial.app_registry import AppRegistry
    from phone_agent.spatial.schema_registry import SchemaRegistry
    from phone_agent.memory.exploration.onboarding import AppOnboarder

    profile_dir = tmp_path / "profiles"
    registry_path = tmp_path / "app_registry.yaml"
    # Copy the real registry file content
    from phone_agent.spatial.app_registry import REGISTRY_PATH
    registry_path.write_text(REGISTRY_PATH.read_text(encoding="utf-8"), encoding="utf-8")

    device = _make_fake_device_factory()
    profile_registry = AppProfileRegistry(profile_dir=profile_dir)
    app_registry = AppRegistry(registry_path=registry_path)
    schema_registry = SchemaRegistry()

    onboarder = AppOnboarder(
        device_factory=device,
        profile_registry=profile_registry,
        app_registry=app_registry,
        schema_registry=schema_registry,
        evidence_root=str(tmp_path / "onboarding"),
    )
    # Inject fake VLM
    onboarder._client = _make_fake_vlm_client(_make_valid_vlm_response("shopping"))
    onboarder._model = "fake-model"

    result = onboarder.onboard("京东", package="com.jingdong.app.mall", num_screens=2)

    assert result.profile.status == "draft"
    assert result.profile.schema_name == "shopping"
    assert result.profile.package == "com.jingdong.app.mall"
    assert result.domain_confidence == pytest.approx(0.9, abs=0.01)
    assert result.profile_path.exists()
    assert (result.evidence_dir / "vlm_output.txt").exists()
    # Screenshots persisted
    assert any(result.evidence_dir.glob("screen_*.png"))


def test_onboarder_invalid_domain_falls_back_to_common_mobile(tmp_path: Path) -> None:
    from phone_agent.spatial.app_profiles import AppProfileRegistry
    from phone_agent.spatial.app_registry import AppRegistry
    from phone_agent.spatial.schema_registry import SchemaRegistry
    from phone_agent.memory.exploration.onboarding import AppOnboarder

    device = _make_fake_device_factory()
    profile_registry = AppProfileRegistry(profile_dir=tmp_path / "profiles")
    schema_registry = SchemaRegistry()

    # Registry path doesn't need to exist for this test
    from phone_agent.spatial.app_registry import REGISTRY_PATH
    registry_path = tmp_path / "app_registry.yaml"
    registry_path.write_text(REGISTRY_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    app_registry = AppRegistry(registry_path=registry_path)

    onboarder = AppOnboarder(
        device_factory=device,
        profile_registry=profile_registry,
        app_registry=app_registry,
        schema_registry=schema_registry,
        evidence_root=str(tmp_path / "onboarding"),
    )
    # VLM returns unknown domain
    bad_vlm_response = json.dumps({
        "domain": "totally_unknown_domain_xyz",
        "confidence": 0.8,
        "app_id": "some_app",
        "display_name": "SomeApp",
        "aliases": [],
        "coverage_page_types": [],
        "coverage_transitions": [],
        "unsafe_tokens": [],
        "trap_tokens": [],
        "login_wall_on_launch": False,
        "notes": "",
        "suggested_task": "",
    })
    onboarder._client = _make_fake_vlm_client(bad_vlm_response)
    onboarder._model = "fake-model"

    result = onboarder.onboard("SomeApp", package="com.some.app", num_screens=2)

    assert result.profile.schema_name == "common_mobile"
    # confidence should be halved: 0.8 * 0.5 = 0.4
    assert result.domain_confidence == pytest.approx(0.4, abs=0.01)
    assert "unknown domain" in result.profile.onboarding_notes.lower() or "unknown" in result.profile.onboarding_notes.lower()


def test_onboarder_vlm_failure_returns_fallback_profile(tmp_path: Path) -> None:
    from phone_agent.spatial.app_profiles import AppProfileRegistry
    from phone_agent.spatial.app_registry import AppRegistry
    from phone_agent.spatial.schema_registry import SchemaRegistry
    from phone_agent.memory.exploration.onboarding import AppOnboarder

    device = _make_fake_device_factory()
    profile_registry = AppProfileRegistry(profile_dir=tmp_path / "profiles")
    schema_registry = SchemaRegistry()

    from phone_agent.spatial.app_registry import REGISTRY_PATH
    registry_path = tmp_path / "app_registry.yaml"
    registry_path.write_text(REGISTRY_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    app_registry = AppRegistry(registry_path=registry_path)

    onboarder = AppOnboarder(
        device_factory=device,
        profile_registry=profile_registry,
        app_registry=app_registry,
        schema_registry=schema_registry,
        evidence_root=str(tmp_path / "onboarding"),
    )
    # VLM raises exception
    failing_client = MagicMock()
    failing_client.chat.completions.create.side_effect = RuntimeError("API error")
    onboarder._client = failing_client
    onboarder._model = "fake-model"

    result = onboarder.onboard("SomeApp", package="com.some.app", num_screens=2)

    assert result.domain_confidence == pytest.approx(0.0, abs=0.01)
    assert result.profile.status == "draft"
    assert "fallback" in result.profile.onboarding_notes.lower() or result.profile.onboarding_notes != ""


def test_onboarder_no_vlm_returns_fallback(tmp_path: Path) -> None:
    from phone_agent.spatial.app_profiles import AppProfileRegistry
    from phone_agent.spatial.app_registry import AppRegistry
    from phone_agent.spatial.schema_registry import SchemaRegistry
    from phone_agent.memory.exploration.onboarding import AppOnboarder

    device = _make_fake_device_factory()
    profile_registry = AppProfileRegistry(profile_dir=tmp_path / "profiles")
    schema_registry = SchemaRegistry()
    from phone_agent.spatial.app_registry import REGISTRY_PATH
    registry_path = tmp_path / "app_registry.yaml"
    registry_path.write_text(REGISTRY_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    app_registry = AppRegistry(registry_path=registry_path)

    onboarder = AppOnboarder(
        device_factory=device,
        profile_registry=profile_registry,
        app_registry=app_registry,
        schema_registry=schema_registry,
        evidence_root=str(tmp_path / "onboarding"),
    )
    # No VLM
    onboarder._client = None
    onboarder._model = ""

    result = onboarder.onboard("NewApp", package="com.new.app", num_screens=2)

    assert result.domain_confidence == pytest.approx(0.0, abs=0.01)
    assert result.profile.status == "draft"


# ---------------------------------------------------------------------------
# D. Draft gate
# ---------------------------------------------------------------------------


def test_draft_gate_blocks_auto_import_for_draft() -> None:
    from phone_agent.memory.exploration.cli import _apply_draft_gate
    from phone_agent.spatial.app_profiles import AppProfile

    draft_profile = AppProfile(
        app_id="jd",
        display_name="京东",
        package="com.jingdong.app.mall",
        schema_name="shopping",
        status="draft",
    )
    effective, warning = _apply_draft_gate(draft_profile, auto_import_graph=True)
    assert effective is False
    assert warning is not None
    assert "draft" in warning or "草稿" in warning


def test_draft_gate_allows_exploration_for_draft() -> None:
    from phone_agent.memory.exploration.cli import _apply_draft_gate
    from phone_agent.spatial.app_profiles import AppProfile

    draft_profile = AppProfile(
        app_id="jd",
        display_name="京东",
        package="com.jingdong.app.mall",
        schema_name="shopping",
        status="draft",
    )
    # auto_import=False → no gate applied
    effective, warning = _apply_draft_gate(draft_profile, auto_import_graph=False)
    assert effective is False
    assert warning is None


def test_draft_gate_allows_auto_import_for_confirmed() -> None:
    from phone_agent.memory.exploration.cli import _apply_draft_gate
    from phone_agent.spatial.app_profiles import AppProfile

    confirmed_profile = AppProfile(
        app_id="jd",
        display_name="京东",
        package="com.jingdong.app.mall",
        schema_name="shopping",
        status="confirmed",
    )
    effective, warning = _apply_draft_gate(confirmed_profile, auto_import_graph=True)
    assert effective is True
    assert warning is None


def test_draft_gate_no_profile() -> None:
    from phone_agent.memory.exploration.cli import _apply_draft_gate

    effective, warning = _apply_draft_gate(None, auto_import_graph=True)
    assert effective is True
    assert warning is None


# ---------------------------------------------------------------------------
# E. Explorer + profile: coverage overrides and SafetyPolicy token reach
# ---------------------------------------------------------------------------


def test_explorer_profile_overrides_coverage(tmp_path: Path) -> None:
    """Coverage targets come from profile when profile has coverage_page_types."""
    from phone_agent.memory.exploration.explorer import OfflineExplorer
    from phone_agent.spatial.app_profiles import AppProfile
    from unittest.mock import MagicMock

    profile = AppProfile(
        app_id="jd",
        display_name="京东",
        package="com.jingdong.app.mall",
        schema_name="shopping",
        status="confirmed",
        coverage_page_types=("home", "product_detail"),
        coverage_transitions=(("home", "product_detail"),),
        unsafe_tokens=("立即下单",),
        trap_tokens=("直播间",),
    )

    # Build explorer with profile using object.__new__ to skip device setup
    explorer = object.__new__(OfflineExplorer)
    explorer.app_name = "京东"
    explorer.app_profile = profile

    from phone_agent.spatial.schema_registry import get_default_registry
    schema = get_default_registry().merged("shopping")
    explorer.schema = schema

    from phone_agent.memory.exploration.task_builder import coverage_from_schema
    coverage = coverage_from_schema(schema, profile=profile)
    explorer.coverage_targets = coverage

    # Profile coverage_page_types should override schema defaults
    assert "home" in explorer.coverage_targets.page_types
    assert "product_detail" in explorer.coverage_targets.page_types
    # Should NOT contain all 18 shopping page types (only profile's 2)
    assert len(explorer.coverage_targets.page_types) == 2


def test_explorer_profile_unsafe_tokens_reach_safety_policy() -> None:
    """Profile unsafe/trap tokens are appended into SafetyPolicy."""
    from phone_agent.memory.exploration.safety import SafetyPolicy
    from phone_agent.spatial.app_profiles import AppProfile
    from phone_agent.spatial.schema_registry import get_default_registry

    profile = AppProfile(
        app_id="jd",
        display_name="京东",
        package="com.jingdong.app.mall",
        schema_name="shopping",
        status="confirmed",
        unsafe_tokens=("京东特有危险按钮",),
        trap_tokens=("直播间入口",),
    )

    schema = get_default_registry().merged("shopping")
    policy = SafetyPolicy.from_schema(schema, profile=profile)

    assert "京东特有危险按钮" in policy.unsafe_tokens
    assert "直播间入口" in policy.trap_tokens


def test_coverage_from_schema_with_no_profile_uses_schema_defaults() -> None:
    """Without profile, coverage_from_schema returns schema defaults."""
    from phone_agent.memory.exploration.task_builder import coverage_from_schema
    from phone_agent.spatial.schema_registry import get_default_registry

    schema = get_default_registry().merged("shopping")
    coverage = coverage_from_schema(schema, profile=None)

    # Shopping schema has multiple page types
    assert len(coverage.page_types) > 2


def test_app_profile_extra_tokens_properties() -> None:
    """extra_unsafe_tokens / extra_trap_tokens proxy works for SafetyPolicy."""
    from phone_agent.spatial.app_profiles import AppProfile

    profile = AppProfile(
        app_id="test",
        display_name="Test",
        package="com.test",
        schema_name="common_mobile",
        unsafe_tokens=("danger",),
        trap_tokens=("trap",),
    )
    assert profile.extra_unsafe_tokens == ("danger",)
    assert profile.extra_trap_tokens == ("trap",)


def test_onboarder_package_resolution_from_config() -> None:
    """get_package_name() is consulted first for package resolution."""
    from phone_agent.spatial.app_profiles import AppProfileRegistry
    from phone_agent.spatial.app_registry import AppRegistry
    from phone_agent.spatial.schema_registry import SchemaRegistry
    from phone_agent.memory.exploration.onboarding import AppOnboarder

    device = _make_fake_device_factory()
    profile_registry = AppProfileRegistry(profile_dir=MagicMock())
    schema_registry = SchemaRegistry()
    from phone_agent.spatial.app_registry import REGISTRY_PATH
    app_registry = AppRegistry()

    onboarder = AppOnboarder(
        device_factory=device,
        profile_registry=profile_registry,
        app_registry=app_registry,
        schema_registry=schema_registry,
    )
    onboarder._client = None
    onboarder._model = ""

    # 京东 is in APP_PACKAGES, so resolve_package should return its package
    pkg = onboarder._resolve_package("京东", None)
    assert pkg == "com.jingdong.app.mall"


def test_onboarder_package_resolution_raises_on_unknown() -> None:
    """ValueError raised when package cannot be resolved."""
    from phone_agent.spatial.app_profiles import AppProfileRegistry
    from phone_agent.spatial.app_registry import AppRegistry
    from phone_agent.spatial.schema_registry import SchemaRegistry
    from phone_agent.memory.exploration.onboarding import AppOnboarder

    device = _make_fake_device_factory()
    from phone_agent.spatial.app_registry import AppRegistry as AR
    app_registry = AR()

    onboarder = AppOnboarder(
        device_factory=device,
        app_registry=app_registry,
    )
    onboarder._client = None

    with pytest.raises(ValueError, match="Cannot resolve package"):
        onboarder._resolve_package("完全不存在的App", None)
