"""Phone agent error classification for smart retry logic."""

from enum import Enum


class ErrorCategory(Enum):
    """Error classification for retry decisions."""
    RECOVERABLE = "recoverable"     # Network errors, timeouts — retry with backoff
    RATE_LIMITED = "rate_limited"   # API rate limits — wait and retry
    FATAL = "fatal"                 # Auth failures, bad config — no retry
    VALIDATION = "validation"       # Response parsing failures


class AgentError(Exception):
    """Agent error with category for retry decisions."""

    def __init__(
        self,
        category: ErrorCategory,
        message: str,
        original_exception: Exception | None = None,
    ):
        self.category = category
        self.message = message
        self.original_exception = original_exception
        super().__init__(message)
