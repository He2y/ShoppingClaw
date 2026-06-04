"""Human verification / CAPTCHA detection for phone automation.

Detects login, SMS verification, CAPTCHA, and other screens that require
manual user intervention.  Integrates into the agent loop to auto-pause
before VLM inference — preventing the stuck-loop where the VLM keeps
outputting invalid actions on verification pages.

Two detection layers:
  1. detect_verification()           — from PageClassifier output (primary)
  2. detect_verification_from_vlm()  — from VLM thinking/raw (fallback)
"""

from __future__ import annotations

from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class VerificationInfo:
    """Immutable description of a detected verification screen."""

    verification_type: str   # "login" | "sms_code" | "captcha" | "slider"
    message: str             # user-facing description
    confidence: float        # 0.0 – 1.0


# ---------------------------------------------------------------------------
# Keyword banks
# ---------------------------------------------------------------------------

_CAPTCHA_KEYWORDS = (
    "滑块验证", "拼图验证", "图形验证", "人机验证",
    "安全验证", "安全检测", "请完成验证",
    "slider", "puzzle", "captcha",
)

_SMS_KEYWORDS = (
    "验证码", "短信验证", "获取验证码", "获取短信验证码",
    "输入验证码", "请输入验证码", "手机验证",
    "verification code", "sms code", "enter code",
    "get code", "send code",
)

_LOGIN_KEYWORDS = (
    "登录", "登入", "密码登录", "验证码登录",
    "短信登录", "扫码登录", "扫码验证",
    "验证身份", "身份验证",
    "security check", "verify",
)

_USER_MESSAGES = {
    "login": "检测到登录页面，请在手机上完成登录操作",
    "sms_code": "检测到短信验证码页面，请在手机上输入验证码",
    "captcha": "检测到人机验证（滑块/拼图），请在手机上完成验证",
    "slider": "检测到滑块验证，请在手机上完成验证",
}


# ---------------------------------------------------------------------------
# Layer 1 — PageClassifier-based detection
# ---------------------------------------------------------------------------

def detect_verification(
    page_type: str | None,
    summary: str,
    elements: dict | None = None,
) -> VerificationInfo | None:
    """Detect verification from PageClassifier results.

    Called after ``PageClassifier.classify()`` — before VLM inference —
    so we can skip the expensive model call entirely.
    """
    if not page_type and not summary:
        return None

    combined = (summary or "").lower()
    if elements:
        combined += " " + str(elements).lower()

    # --- page_type == "login" → always requires intervention -----------------
    if page_type == "login":
        vtype = _classify_type(combined)
        return VerificationInfo(
            verification_type=vtype,
            message=_USER_MESSAGES.get(vtype, _USER_MESSAGES["login"]),
            confidence=0.92,
        )

    # --- keyword hit in *any* page type (classifier might label it dialog) ---
    for kw in _CAPTCHA_KEYWORDS:
        if kw in combined:
            return VerificationInfo(
                verification_type="captcha",
                message=_USER_MESSAGES["captcha"],
                confidence=0.88,
            )

    for kw in _SMS_KEYWORDS:
        if kw in combined:
            return VerificationInfo(
                verification_type="sms_code",
                message=_USER_MESSAGES["sms_code"],
                confidence=0.85,
            )

    # --- dialog / permission with login-ish keywords -------------------------
    if page_type in ("dialog", "permission"):
        for kw in _LOGIN_KEYWORDS:
            if kw in combined:
                return VerificationInfo(
                    verification_type="login",
                    message=_USER_MESSAGES["login"],
                    confidence=0.80,
                )

    return None


# ---------------------------------------------------------------------------
# Layer 2 — VLM-output-based fallback
# ---------------------------------------------------------------------------

def detect_verification_from_vlm(
    thinking: str,
    raw_content: str,
) -> VerificationInfo | None:
    """Fallback: detect verification from VLM output.

    Used when PageClassifier was skipped (RuntimeDAG hint).  Checks the
    VLM's *thinking* and *raw_content* for verification indicators.
    Only fires when the VLM did NOT already produce a ``Take_over`` action.
    """
    combined = ((thinking or "") + " " + (raw_content or "")).lower()

    # VLM already produced Take_over — let the normal handler run
    if "take_over" in combined:
        return None

    # Captcha indicators
    for kw in ("人机验证", "滑块", "拼图", "验证码", "captcha"):
        if kw in combined:
            return VerificationInfo(
                verification_type="captcha",
                message="VLM检测到验证页面，请在手机上完成验证",
                confidence=0.75,
            )

    # Login indicators (VLM phrases like "检测到您未登录")
    for phrase in ("未登录", "需要登录", "登录账号", "登录页", "log in", "not logged in"):
        if phrase in combined:
            return VerificationInfo(
                verification_type="login",
                message="VLM检测到需要登录，请在手机上完成登录",
                confidence=0.70,
            )

    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _classify_type(text: str) -> str:
    """Determine the specific verification sub-type from context text."""
    for kw in ("滑块", "拼图", "slider", "puzzle"):
        if kw in text:
            return "captcha"
    for kw in ("短信", "sms", "验证码"):
        if kw in text:
            return "sms_code"
    return "login"
