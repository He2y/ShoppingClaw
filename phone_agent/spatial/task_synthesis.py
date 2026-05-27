"""Task synthesis helpers for functionality-driven AMSG exploration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - python-dotenv is a project dependency.
    load_dotenv = None

from .functionality_cluster import FunctionalityCluster


@dataclass(frozen=True)
class AMSGModelConfig:
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    source: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.model and self.api_key and not self.api_key.startswith("your_"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "model": self.model,
            "api_key": "***" if self.api_key else "",
            "source": self.source,
            "configured": self.configured,
        }


def resolve_strong_vlm_config() -> AMSGModelConfig:
    _load_env()
    return _resolve_config(
        (
            ("AMSG_STRONG_VLM_BASE_URL", "AMSG_STRONG_VLM_MODEL", "AMSG_STRONG_VLM_API_KEY", "amsg_strong_vlm"),
            ("OFFLINE_VLM_BASE_URL", "OFFLINE_VLM_MODEL", "OFFLINE_VLM_API_KEY", "offline_vlm"),
            ("PHONE_AGENT_BASE_URL", "PHONE_AGENT_MODEL", "PHONE_AGENT_API_KEY", "phone_agent"),
        )
    )


def resolve_embedding_config() -> AMSGModelConfig:
    _load_env()
    return _resolve_config(
        (
            ("AMSG_EMBEDDING_BASE_URL", "AMSG_EMBEDDING_MODEL", "AMSG_EMBEDDING_API_KEY", "amsg_embedding"),
            ("EMBEDDING_BASE_URL", "EMBEDDING_MODEL", "EMBEDDING_API_KEY", "embedding"),
        )
    )


class TaskSynthesizer:
    """Build high-level exploration tasks from discovered functionality."""

    def __init__(self, strong_vlm_config: AMSGModelConfig | None = None):
        self.strong_vlm_config = strong_vlm_config or resolve_strong_vlm_config()

    def fallback_task(self, cluster: FunctionalityCluster, *, app: str = "淘宝") -> str:
        region = f" in the {', '.join(cluster.regions)} region" if cluster.regions else ""
        return (
            f"Explore the self-discovered function '{cluster.canonical_name}'{region} in {app}. "
            "Interact only if it is safe, observe the postcondition, and stop before payment, "
            "order submission, login, address confirmation, or irreversible changes."
        )

    def build_prompt(self, cluster: FunctionalityCluster, *, short_term: list[str] | None = None, long_term: list[str] | None = None) -> str:
        short_term = short_term or []
        long_term = long_term or []
        return (
            "You are helping construct an Active Mobile Spatial Graph. Generate one safe, high-level "
            "exploration task for the discovered mobile UI functionality below. Do not assume a manually "
            "predefined function bucket; use only the observed evidence.\n\n"
            f"Functionality cluster: {cluster.canonical_name}\n"
            f"Description: {cluster.canonical_description}\n"
            f"Regions: {', '.join(cluster.regions) or 'unknown'}\n"
            f"Risk: {cluster.risk_level}\n"
            f"Short-term neighboring functions: {short_term[:5]}\n"
            f"Long-term related functions: {long_term[:10]}\n\n"
            "Return JSON with fields reasoning and task. The task must be executable, safe, and must stop "
            "before high-risk payment/order/address actions."
        )

    def to_dict(self) -> dict[str, Any]:
        return {"strong_vlm": self.strong_vlm_config.to_dict()}


def _resolve_config(candidates: tuple[tuple[str, str, str, str], ...]) -> AMSGModelConfig:
    first_partial: AMSGModelConfig | None = None
    for base_key, model_key, api_key, source in candidates:
        base_url = os.environ.get(base_key, "")
        model = os.environ.get(model_key, "")
        key = os.environ.get(api_key, "")
        config = AMSGModelConfig(base_url=base_url, model=model, api_key=key, source=source)
        if config.configured:
            return config
        if (base_url or model or key) and first_partial is None:
            first_partial = config
    return first_partial or AMSGModelConfig()


_ENV_LOADED = False


def _load_env() -> None:
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    if load_dotenv is not None:
        load_dotenv()
    _ENV_LOADED = True
