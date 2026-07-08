"""Screen stability detection using perceptual hashing (Phase 4.5a)."""

from __future__ import annotations

import base64
import hashlib
import time
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Callable


@dataclass(frozen=True)
class StabilityResult:
    stable: bool
    screenshots_taken: int
    elapsed_s: float


def screen_similarity(b64_a: str, b64_b: str) -> float:
    """Compute visual similarity between two base64-encoded screenshots.

    Decodes both images, converts to grayscale, resizes to 32x32 thumbnails,
    and returns 1 - (mean absolute pixel diff / 255).

    Returns 0.0 on any decode failure.
    """
    try:
        from PIL import Image

        def _decode(b64: str) -> Any:
            data = base64.b64decode(b64)
            img = Image.open(BytesIO(data)).convert("L")
            img = img.resize((32, 32), Image.Resampling.LANCZOS)
            return img

        img_a = _decode(b64_a)
        img_b = _decode(b64_b)
        pixels_a = list(img_a.getdata())
        pixels_b = list(img_b.getdata())
        total_diff = sum(abs(a - b) for a, b in zip(pixels_a, pixels_b))
        mean_diff = total_diff / len(pixels_a)
        return max(0.0, 1.0 - mean_diff / 255.0)
    except Exception:
        return 0.0


def perceptual_hash(b64: str) -> str:
    """Compute a perceptual hash of a screenshot.

    PIL decode → grayscale → resize 16x16 → average-threshold bits → hex string.
    Falls back to an md5-based hash on any decode failure.
    """
    try:
        from PIL import Image

        data = base64.b64decode(b64)
        img = Image.open(BytesIO(data)).convert("L")
        img = img.resize((16, 16), Image.Resampling.LANCZOS)
        pixels = list(img.getdata())
        avg = sum(pixels) / len(pixels)
        bits = "".join("1" if p >= avg else "0" for p in pixels)
        # Pack bits into integer then format as hex
        value = int(bits, 2)
        hex_str = format(value, "064x")
        return hex_str
    except Exception:
        # Fallback: use md5 of the raw b64 string
        return "md5:" + hashlib.md5(b64.encode()).hexdigest()


def hash_distance(h1: str, h2: str) -> int:
    """Hamming distance between two perceptual hashes.

    If either hash starts with "md5:" (fallback), compares them directly:
    distance 0 if equal, 256 if not.
    """
    if h1.startswith("md5:") or h2.startswith("md5:"):
        return 0 if h1 == h2 else 256
    try:
        v1 = int(h1, 16)
        v2 = int(h2, 16)
        xor = v1 ^ v2
        return bin(xor).count("1")
    except ValueError:
        return 256


def wait_for_stable_screen(
    capture_fn: Callable[[], Any],
    *,
    similarity_threshold: float = 0.95,
    interval_s: float = 0.8,
    timeout_s: float = 5.0,
) -> tuple[Any, StabilityResult]:
    """Wait until the screen stops changing (two consecutive frames are similar).

    Args:
        capture_fn: Callable returning a screenshot object with ``.base64_data``.
        similarity_threshold: Required similarity [0, 1] between consecutive frames.
        interval_s: Sleep between captures in seconds.
        timeout_s: Maximum wait in seconds.

    Returns:
        (last_screenshot, StabilityResult)
        If timeout is reached before stability, StabilityResult.stable == False.
        Caller should mark the landing page as ``transient`` in that case.
    """
    start = time.monotonic()
    prev_screenshot = capture_fn()
    screenshots_taken = 1
    prev_b64 = prev_screenshot.base64_data

    while True:
        elapsed = time.monotonic() - start
        if elapsed >= timeout_s:
            return prev_screenshot, StabilityResult(
                stable=False,
                screenshots_taken=screenshots_taken,
                elapsed_s=elapsed,
            )
        time.sleep(interval_s)
        current_screenshot = capture_fn()
        screenshots_taken += 1
        current_b64 = current_screenshot.base64_data
        sim = screen_similarity(prev_b64, current_b64)
        if sim >= similarity_threshold:
            elapsed = time.monotonic() - start
            return current_screenshot, StabilityResult(
                stable=True,
                screenshots_taken=screenshots_taken,
                elapsed_s=elapsed,
            )
        prev_screenshot = current_screenshot
        prev_b64 = current_b64
