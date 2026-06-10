"""Tests for page confidence assessment (Phase 4.5b)."""

from __future__ import annotations

import base64
import io
from unittest.mock import MagicMock

import pytest
from PIL import Image

from phone_agent.memory.exploration.confidence import (
    PageConfidence,
    assess_page_confidence,
)
from phone_agent.memory.exploration.stability import (
    StabilityResult,
    perceptual_hash,
    screen_similarity,
    wait_for_stable_screen,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_b64(color: tuple[int, int, int] = (128, 128, 128), size: int = 64) -> str:
    img = Image.new("RGB", (size, size), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _make_gradient_b64(offset: int = 0, size: int = 64) -> str:
    """Create a gradient image that is visually distinct at different offsets."""
    pixels = []
    for y in range(size):
        for x in range(size):
            v = ((x + y + offset) * 7) % 256
            pixels.append((v, (255 - v + offset) % 256, (v // 2 + offset) % 256))
    img = Image.new("RGB", (size, size))
    img.putdata(pixels)  # type: ignore[arg-type]
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _make_page_info(
    page_type: str,
    elements: dict | None = None,
    screenshot_b64: str | None = None,
) -> MagicMock:
    page = MagicMock()
    page.page_type = page_type
    page.semantic_summary = f"summary of {page_type}"
    page.elements = elements or {}
    page.screenshot_base64 = screenshot_b64 or _make_b64()
    return page


def _make_schema(
    page_types: dict | None = None,
    alias_map: dict | None = None,
) -> MagicMock:
    """Create a minimal mock schema for confidence tests."""
    schema = MagicMock()

    # page_spec returns a spec with optional landmarks
    page_types = page_types or {}

    def page_spec(page_type: str) -> MagicMock | None:
        pt = page_types.get(page_type)
        if pt is None:
            return None
        spec = MagicMock()
        spec.landmarks = tuple(pt.get("landmarks", []))
        return spec

    schema.page_spec = page_spec

    # page_type_matches checks aliases
    alias_map = alias_map or {}

    def page_type_matches(expected: str, observed: str) -> bool:
        if expected == observed:
            return True
        aliases = alias_map.get(expected, [])
        return observed in aliases

    schema.page_type_matches = page_type_matches
    return schema


# ── assess_page_confidence ────────────────────────────────────────────────────

class TestAssessPageConfidence:
    def test_confident_when_stable_and_agreeing(self) -> None:
        page = _make_page_info("home", elements={"nav": "tab bar"})
        schema = _make_schema()
        confidence = assess_page_confidence(
            page, schema, reasoning_inferred="home", stable=True
        )
        assert confidence.level == "confident"

    def test_transient_when_not_stable(self) -> None:
        page = _make_page_info("home")
        schema = _make_schema()
        confidence = assess_page_confidence(
            page, schema, reasoning_inferred=None, stable=False
        )
        assert confidence.level == "transient"

    def test_transient_overrides_all_signals(self) -> None:
        """Even if signals agree, transient wins when not stable."""
        page = _make_page_info("cart", elements={"checkout": "go to checkout"})
        schema = _make_schema(
            page_types={"cart": {"landmarks": ["checkout"]}}
        )
        confidence = assess_page_confidence(
            page, schema, reasoning_inferred="cart", stable=False
        )
        assert confidence.level == "transient"

    def test_low_confidence_when_reasoning_conflicts(self) -> None:
        page = _make_page_info("home")
        schema = _make_schema()
        confidence = assess_page_confidence(
            page, schema, reasoning_inferred="search_result", stable=True
        )
        assert confidence.level == "low_confidence"

    def test_confident_when_reasoning_abstains(self) -> None:
        page = _make_page_info("home")
        schema = _make_schema()
        confidence = assess_page_confidence(
            page, schema, reasoning_inferred=None, stable=True
        )
        assert confidence.level == "confident"

    def test_low_confidence_when_landmark_mismatch(self) -> None:
        # Cart page requires "checkout" landmark, but elements has none of it
        page = _make_page_info(
            "cart",
            elements={"banner": "ad banner", "product": "product tile"},
        )
        schema = _make_schema(
            page_types={"cart": {"landmarks": ["checkout_button"]}}
        )
        confidence = assess_page_confidence(
            page, schema, reasoning_inferred=None, stable=True
        )
        assert confidence.level == "low_confidence"
        assert confidence.signals["landmark"] == "mismatch"

    def test_confident_when_landmark_present(self) -> None:
        page = _make_page_info(
            "cart",
            elements={"checkout": "去结算", "items": "商品列表"},
        )
        schema = _make_schema(
            page_types={"cart": {"landmarks": ["checkout"]}}
        )
        confidence = assess_page_confidence(
            page, schema, reasoning_inferred=None, stable=True
        )
        assert confidence.level == "confident"

    def test_landmark_abstains_when_no_elements(self) -> None:
        """Fast mode: no elements extracted → landmark check abstains → not low_confidence."""
        page = _make_page_info("cart", elements={})
        schema = _make_schema(
            page_types={"cart": {"landmarks": ["checkout_button"]}}
        )
        confidence = assess_page_confidence(
            page, schema, reasoning_inferred=None, stable=True
        )
        assert confidence.signals["landmark"] == "abstain"
        # abstain does not cause low_confidence on its own
        assert confidence.level == "confident"

    def test_alias_reasoning_not_conflicting(self) -> None:
        # "shopping_cart" is an alias for "cart"
        page = _make_page_info("cart")
        schema = _make_schema(alias_map={"cart": ["shopping_cart"]})
        confidence = assess_page_confidence(
            page, schema, reasoning_inferred="shopping_cart", stable=True
        )
        assert confidence.level == "confident"

    def test_signals_dict_populated(self) -> None:
        page = _make_page_info("home")
        schema = _make_schema()
        confidence = assess_page_confidence(
            page, schema, reasoning_inferred="search_result", stable=True
        )
        assert "classifier" in confidence.signals
        assert "reasoning" in confidence.signals
        assert "landmark" in confidence.signals
        assert confidence.signals["classifier"] == "home"
        assert confidence.signals["reasoning"] == "search_result"


# ── StabilityResult / wait_for_stable_screen ─────────────────────────────────

class TestScreenSimilarity:
    def test_identical_images_have_high_similarity(self) -> None:
        b64 = _make_b64((100, 150, 200))
        sim = screen_similarity(b64, b64)
        assert sim >= 0.99

    def test_different_gradient_images_have_lower_similarity(self) -> None:
        # Gradients with large offset should differ meaningfully
        b64_a = _make_gradient_b64(0)
        b64_b = _make_gradient_b64(128)
        sim = screen_similarity(b64_a, b64_b)
        assert sim < 0.95  # They should not be considered identical

    def test_invalid_b64_returns_zero(self) -> None:
        sim = screen_similarity("notvalidbase64!!!", _make_b64())
        assert sim == 0.0


class TestPerceptualHash:
    def test_same_image_produces_same_hash(self) -> None:
        b64 = _make_gradient_b64(42)
        h1 = perceptual_hash(b64)
        h2 = perceptual_hash(b64)
        assert h1 == h2

    def test_different_gradient_images_produce_different_hashes(self) -> None:
        # Use gradients with large offsets to produce distinct phashes
        h1 = perceptual_hash(_make_gradient_b64(0))
        h2 = perceptual_hash(_make_gradient_b64(128))
        assert h1 != h2

    def test_invalid_b64_returns_fallback(self) -> None:
        h = perceptual_hash("invalid_b64!!!")
        assert h.startswith("md5:")


class TestWaitForStableScreen:
    def test_identical_screenshots_stable_immediately(self) -> None:
        b64 = _make_b64()

        class _FakeScreenshot:
            base64_data = b64

        calls = [0]
        def capture_fn() -> _FakeScreenshot:
            calls[0] += 1
            return _FakeScreenshot()

        screenshot, result = wait_for_stable_screen(
            capture_fn, similarity_threshold=0.95, interval_s=0.0, timeout_s=5.0
        )
        assert result.stable
        assert result.screenshots_taken >= 2

    def test_alternating_different_screenshots_times_out(self) -> None:
        b64_red = _make_gradient_b64(0)
        b64_blue = _make_gradient_b64(128)
        colors = [b64_red, b64_blue]
        idx = [0]

        class _FakeScreenshot:
            def __init__(self, b: str) -> None:
                self.base64_data = b

        def capture_fn() -> _FakeScreenshot:
            b = colors[idx[0] % 2]
            idx[0] += 1
            return _FakeScreenshot(b)

        screenshot, result = wait_for_stable_screen(
            capture_fn, similarity_threshold=0.95, interval_s=0.0, timeout_s=0.1
        )
        assert not result.stable


# ── Explorer integration smoke test ──────────────────────────────────────────

class TestExplorerEscapeAndRestart:
    """Smoke test for _escape_and_restart respecting restart budget."""

    def _make_explorer(self, max_restarts: int = 2) -> object:
        from phone_agent.memory.exploration.explorer import OfflineExplorer
        from phone_agent.memory.exploration.watchdog import WatchdogConfig, StuckDetector

        explorer = object.__new__(OfflineExplorer)
        # Set minimal attributes needed by _escape_and_restart
        explorer.app_name = "testapp"  # type: ignore
        explorer.device_id = None  # type: ignore
        explorer.verbose = False  # type: ignore
        explorer._restart_count = 0  # type: ignore
        cfg = WatchdogConfig(max_restarts=max_restarts)
        explorer.watchdog = StuckDetector(cfg)  # type: ignore
        from phone_agent.memory.exploration.watchdog import ProgressWatchdog
        explorer.progress_watchdog = ProgressWatchdog(cfg)  # type: ignore

        # Mock device
        device = MagicMock()
        device.launch_app = MagicMock()
        explorer.device = device  # type: ignore

        return explorer

    def test_restart_within_budget(self) -> None:
        from phone_agent.memory.exploration.explorer import OfflineExplorer
        explorer = self._make_explorer(max_restarts=2)
        result = OfflineExplorer._escape_and_restart(explorer, "test reason")  # type: ignore
        assert result is True
        assert explorer._restart_count == 1  # type: ignore

    def test_restart_budget_exhausted(self) -> None:
        from phone_agent.memory.exploration.explorer import OfflineExplorer
        explorer = self._make_explorer(max_restarts=2)
        explorer._restart_count = 2  # type: ignore  # already at limit
        result = OfflineExplorer._escape_and_restart(explorer, "test reason")  # type: ignore
        assert result is False
        assert explorer._restart_count == 2  # type: ignore  # not incremented

    def test_multiple_restarts_exhaust_budget(self) -> None:
        from phone_agent.memory.exploration.explorer import OfflineExplorer
        explorer = self._make_explorer(max_restarts=2)
        r1 = OfflineExplorer._escape_and_restart(explorer, "reason 1")  # type: ignore
        r2 = OfflineExplorer._escape_and_restart(explorer, "reason 2")  # type: ignore
        r3 = OfflineExplorer._escape_and_restart(explorer, "reason 3")  # type: ignore
        assert r1 is True
        assert r2 is True
        assert r3 is False
