"""Stuck detection and progress watchdog for offline exploration (Phase 4b/4c)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque

from .stability import hash_distance, perceptual_hash


@dataclass(frozen=True)
class WatchdogConfig:
    """Tunable parameters for stuck / wandering detection."""

    stuck_window: int = 6              # ring buffer size for phash observations
    stuck_repeat_threshold: int = 4    # >= K similar frames in window => stuck
    hash_distance_threshold: int = 30  # phash hamming distance considered "same page cluster"
    no_progress_limit: int = 8         # steps without new coverage => wandering
    max_restarts: int = 2


class StuckDetector:
    """Detects when the explorer is stuck on the same page cluster.

    Uses a fixed-size ring buffer of perceptual hashes.  If at least
    ``stuck_repeat_threshold`` entries in the buffer are pairwise close
    (hamming distance <= ``hash_distance_threshold``), the explorer is
    considered stuck.
    """

    def __init__(self, config: WatchdogConfig = WatchdogConfig()) -> None:
        self._config = config
        self._buffer: Deque[str] = deque(maxlen=config.stuck_window)

    def observe(self, screenshot_b64: str) -> None:
        """Push the perceptual hash of a screenshot into the ring buffer."""
        phash = perceptual_hash(screenshot_b64)
        self._buffer.append(phash)

    def is_stuck(self) -> bool:
        """Return True if >= stuck_repeat_threshold frames are in the same page cluster.

        Uses pairwise hamming distance.  The buffer must be at least half-full
        before this can fire (prevents false positives at startup).
        """
        buf = list(self._buffer)
        if len(buf) < max(2, self._config.stuck_repeat_threshold):
            return False

        threshold = self._config.hash_distance_threshold

        # Count how many hashes in the buffer are "close" to at least one other hash.
        # A hash is considered "in the cluster" if it has distance <= threshold
        # to at least one OTHER hash in the buffer.
        close_count = 0
        for i, h1 in enumerate(buf):
            for j, h2 in enumerate(buf):
                if i != j and hash_distance(h1, h2) <= threshold:
                    close_count += 1
                    break  # h1 is close to at least one other; count it once

        return close_count >= self._config.stuck_repeat_threshold

    def reset(self) -> None:
        """Clear the ring buffer after a successful escape."""
        self._buffer.clear()


class ProgressWatchdog:
    """Detects wandering: no new coverage for a sustained number of steps.

    Feed ``coverage_key_count`` (pages discovered + transitions accepted)
    after each step via ``observe``.  If the count has not increased for
    ``no_progress_limit`` consecutive observations, ``is_wandering`` returns True.
    """

    def __init__(self, config: WatchdogConfig = WatchdogConfig()) -> None:
        self._config = config
        self._last_count: int = 0
        self._no_progress_steps: int = 0
        self._total_observations: int = 0

    def observe(self, coverage_key_count: int) -> None:
        """Record a coverage observation.

        Args:
            coverage_key_count: Total number of pages + transitions discovered so far.
        """
        self._total_observations += 1
        if coverage_key_count > self._last_count:
            self._last_count = coverage_key_count
            self._no_progress_steps = 0
        else:
            self._no_progress_steps += 1

    def is_wandering(self) -> bool:
        """Return True if no_progress_limit consecutive steps had no coverage gain.

        Requires at least no_progress_limit observations before triggering.
        """
        return (
            self._total_observations >= self._config.no_progress_limit
            and self._no_progress_steps >= self._config.no_progress_limit
        )

    def reset_after_restart(self) -> None:
        """Reset progress tracking after an app restart."""
        self._no_progress_steps = 0
        # Keep _last_count so we don't re-trigger immediately; the restart
        # itself does not produce new coverage until the next observation.
