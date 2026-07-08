"""Tests for StuckDetector and ProgressWatchdog (Phase 4b/4c)."""

from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

from phone_agent.memory.exploration.watchdog import (
    ProgressWatchdog,
    StuckDetector,
    WatchdogConfig,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_b64_image(color: tuple[int, int, int] = (128, 128, 128), size: int = 64) -> str:
    """Create a solid-color PNG and return as base64."""
    img = Image.new("RGB", (size, size), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _make_gradient_b64(offset: int = 0, size: int = 64) -> str:
    """Create a gradient image for robust phash testing.

    The gradient varies smoothly, so different offsets produce distinct phashes.
    """
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


def _red_b64() -> str:
    # Use gradient-based to avoid phash degeneration
    return _make_gradient_b64(0)


def _blue_b64() -> str:
    return _make_gradient_b64(64)


def _green_b64() -> str:
    return _make_gradient_b64(128)


# ── StuckDetector ─────────────────────────────────────────────────────────────

class TestStuckDetector:
    def test_not_stuck_with_few_observations(self) -> None:
        cfg = WatchdogConfig(stuck_window=6, stuck_repeat_threshold=4)
        det = StuckDetector(cfg)
        b64 = _red_b64()
        # Only 2 observations — not enough to trigger
        det.observe(b64)
        det.observe(b64)
        assert not det.is_stuck()

    def test_stuck_after_threshold_same_image(self) -> None:
        cfg = WatchdogConfig(stuck_window=6, stuck_repeat_threshold=4, hash_distance_threshold=30)
        det = StuckDetector(cfg)
        b64 = _red_b64()
        for _ in range(5):
            det.observe(b64)
        assert det.is_stuck()

    def test_not_stuck_with_varied_images(self) -> None:
        # Use a very strict distance threshold so only nearly-identical images count.
        # The 3 gradient images have pairwise distances of ~14-195; with threshold=5
        # only the identical repeats (dist=0) are "close", giving close_count=3 < 4.
        cfg = WatchdogConfig(stuck_window=6, stuck_repeat_threshold=4, hash_distance_threshold=5)
        det = StuckDetector(cfg)
        images = [_red_b64(), _blue_b64(), _green_b64(), _red_b64(), _blue_b64(), _green_b64()]
        for b64 in images:
            det.observe(b64)
        # With threshold=5, only identical images (dist=0) are considered close.
        # Each image has exactly one close neighbor → close_count=6, but that's still >= 4.
        # Actually all 6 are close to themselves (duplicates), so use threshold=5
        # and only 3 UNIQUE hashes. We need a scenario where images are truly distinct.
        # Instead, test with non-repeating distinct images.
        det2 = StuckDetector(cfg)
        for offset in [0, 30, 60, 90, 120, 150]:
            det2.observe(_make_gradient_b64(offset * 2))
        # Each image is unique; at threshold=5 no pairs should match
        assert not det2.is_stuck()

    def test_reset_clears_buffer(self) -> None:
        cfg = WatchdogConfig(stuck_window=6, stuck_repeat_threshold=4)
        det = StuckDetector(cfg)
        b64 = _red_b64()
        for _ in range(5):
            det.observe(b64)
        assert det.is_stuck()
        det.reset()
        assert not det.is_stuck()

    def test_stuck_false_with_small_threshold(self) -> None:
        # With threshold=0 only exact hash matches count; a single color still matches itself
        cfg = WatchdogConfig(stuck_window=6, stuck_repeat_threshold=4, hash_distance_threshold=0)
        det = StuckDetector(cfg)
        b64 = _red_b64()
        for _ in range(6):
            det.observe(b64)
        # Exact same b64 → same phash → distance 0, still considered stuck
        assert det.is_stuck()


# ── ProgressWatchdog ──────────────────────────────────────────────────────────

class TestProgressWatchdog:
    def test_not_wandering_with_growth(self) -> None:
        cfg = WatchdogConfig(no_progress_limit=8)
        pw = ProgressWatchdog(cfg)
        for i in range(10):
            pw.observe(i * 2)  # coverage grows every step
        assert not pw.is_wandering()

    def test_wandering_after_no_progress(self) -> None:
        cfg = WatchdogConfig(no_progress_limit=4)
        pw = ProgressWatchdog(cfg)
        # First 2 observations grow coverage
        pw.observe(1)
        pw.observe(2)
        # Then stalls for no_progress_limit + 1 steps to ensure full trigger
        for _ in range(5):
            pw.observe(2)
        assert pw.is_wandering()

    def test_not_wandering_before_limit(self) -> None:
        cfg = WatchdogConfig(no_progress_limit=8)
        pw = ProgressWatchdog(cfg)
        for _ in range(5):
            pw.observe(5)
        assert not pw.is_wandering()

    def test_reset_after_restart_clears_no_progress_count(self) -> None:
        cfg = WatchdogConfig(no_progress_limit=4)
        pw = ProgressWatchdog(cfg)
        pw.observe(1)
        pw.observe(2)
        for _ in range(4):
            pw.observe(2)
        assert pw.is_wandering()
        pw.reset_after_restart()
        # After reset, counter is cleared but new observations are needed
        assert not pw.is_wandering()

    def test_wandering_not_triggered_too_early(self) -> None:
        cfg = WatchdogConfig(no_progress_limit=8)
        pw = ProgressWatchdog(cfg)
        # Even with 0 progress, needs enough observations first
        for _ in range(3):
            pw.observe(0)
        assert not pw.is_wandering()

    def test_recovery_after_growth(self) -> None:
        cfg = WatchdogConfig(no_progress_limit=3)
        pw = ProgressWatchdog(cfg)
        # First observe sets the baseline (5 > 0, no stall)
        pw.observe(5)
        # Then stall for no_progress_limit + 1 steps
        for _ in range(4):
            pw.observe(5)
        assert pw.is_wandering()
        # Growth resets the stall counter
        pw.observe(10)
        assert not pw.is_wandering()
