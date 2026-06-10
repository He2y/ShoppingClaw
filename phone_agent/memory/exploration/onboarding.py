"""Unfamiliar-app onboarding: safe screen sampling → strong-VLM profile inference.

Usage (CLI):
    python -m phone_agent.memory.exploration.onboarding --app 京东
    python -m phone_agent.memory.exploration.onboarding --app 京东 --package com.jingdong.app.mall
    python -m phone_agent.memory.exploration.onboarding --app 京东 --device-id emulator-5554
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from phone_agent.config.apps import get_package_name
from phone_agent.spatial.app_profiles import AppProfile, AppProfileRegistry
from phone_agent.spatial.app_registry import AppRecord, get_default_app_registry
from phone_agent.spatial.schema_registry import get_default_registry


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OnboardingResult:
    """Output of a successful onboarding run."""

    profile: AppProfile
    profile_path: Path
    evidence_dir: Path
    domain_confidence: float
    raw_vlm_output: str


# ---------------------------------------------------------------------------
# AppOnboarder
# ---------------------------------------------------------------------------


class AppOnboarder:
    """Launch app → safe screenshot sampling → strong VLM profile inference.

    Provider chain for the strong VLM mirrors PageClassifier:
      AMSG_STRONG_VLM_* → OFFLINE_VLM_* → PHONE_AGENT_* env vars.
    """

    def __init__(
        self,
        device_factory: Any,
        *,
        device_id: str | None = None,
        schema_registry: Any | None = None,
        profile_registry: AppProfileRegistry | None = None,
        app_registry: Any | None = None,
        evidence_root: str = "memory_db/onboarding",
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.device_factory = device_factory
        self.device_id = device_id
        self.schema_registry = schema_registry or get_default_registry()
        self.profile_registry = profile_registry or AppProfileRegistry()
        self.app_registry = app_registry or get_default_app_registry()
        self.evidence_root = Path(evidence_root)

        # Build VLM client using provider-chain pattern (like PageClassifier)
        self._client, self._model = self._build_vlm_client(model, base_url, api_key)

    # ------------------------------------------------------------------
    # Provider chain (mirrors PageClassifier)
    # ------------------------------------------------------------------

    def _build_vlm_client(
        self,
        explicit_model: str | None,
        explicit_base_url: str | None,
        explicit_api_key: str | None,
    ) -> tuple[Any, str]:
        """Return (openai.OpenAI client, model_name).

        Priority: explicit args → AMSG_STRONG_VLM_* → OFFLINE_VLM_* → PHONE_AGENT_*.
        Returns (None, "") when no provider is configured — onboard() will use fallback.
        """
        try:
            from openai import OpenAI
        except ImportError:
            return None, ""

        if explicit_model and explicit_base_url and explicit_api_key:
            client = OpenAI(
                base_url=explicit_base_url,
                api_key=explicit_api_key,
                timeout=60.0,
            )
            return client, explicit_model

        load_dotenv()
        providers = (
            (
                os.environ.get("AMSG_STRONG_VLM_API_KEY"),
                os.environ.get("AMSG_STRONG_VLM_BASE_URL"),
                os.environ.get("AMSG_STRONG_VLM_MODEL"),
            ),
            (
                os.environ.get("OFFLINE_VLM_API_KEY"),
                os.environ.get("OFFLINE_VLM_BASE_URL"),
                os.environ.get("OFFLINE_VLM_MODEL"),
            ),
            (
                os.environ.get("PHONE_AGENT_API_KEY", "EMPTY"),
                os.environ.get("PHONE_AGENT_BASE_URL", "http://localhost:8000/v1"),
                os.environ.get("PHONE_AGENT_MODEL", "autoglm-phone-9b"),
            ),
        )
        for key, url, mdl in providers:
            if key and url and mdl:
                client = OpenAI(base_url=url, api_key=key, timeout=60.0)
                return client, str(mdl)

        return None, ""

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def onboard(
        self,
        app_name: str,
        package: str | None = None,
        num_screens: int = 4,
    ) -> OnboardingResult:
        """Bootstrap an AppProfile for an unfamiliar app.

        Steps:
        1. Resolve the package name.
        2. Safe screen sampling (launch + Back + swipe, no taps).
        3. Strong VLM inference → draft AppProfile.
        4. Persist profile + evidence.
        5. Register in AppRegistry in-memory and persist.
        """
        resolved_package = self._resolve_package(app_name, package)

        slug = _make_slug(app_name)
        evidence_dir = self.evidence_root / slug
        evidence_dir.mkdir(parents=True, exist_ok=True)

        screenshots = self._capture_bootstrap_screens(
            app_name, resolved_package, evidence_dir, num_screens
        )

        profile, raw_vlm, confidence = self._infer_profile(
            app_name, resolved_package, slug, screenshots
        )

        # Save evidence
        (evidence_dir / "vlm_output.txt").write_text(raw_vlm, encoding="utf-8")

        # Persist profile
        profile_path = self.profile_registry.save(profile)

        # Register in AppRegistry in-memory + save
        self._register_in_app_registry(profile)

        return OnboardingResult(
            profile=profile,
            profile_path=profile_path,
            evidence_dir=evidence_dir,
            domain_confidence=confidence,
            raw_vlm_output=raw_vlm,
        )

    # ------------------------------------------------------------------
    # Step 1: package resolution
    # ------------------------------------------------------------------

    def _resolve_package(self, app_name: str, explicit_package: str | None) -> str:
        if explicit_package:
            return explicit_package

        # APP_PACKAGES dict
        from_config = get_package_name(app_name)
        if from_config:
            return from_config

        # AppRegistry
        record = self.app_registry.resolve(app_name)
        if record and record.package_names:
            return record.package_names[0]

        raise ValueError(
            f"Cannot resolve package for app '{app_name}'. "
            "Provide --package <com.example.app> or add the app to APP_PACKAGES."
        )

    # ------------------------------------------------------------------
    # Step 2: safe screen sampling
    # ------------------------------------------------------------------

    def _capture_bootstrap_screens(
        self,
        app_name: str,
        package: str,
        evidence_dir: Path,
        num_screens: int,
    ) -> list[tuple[str, int, int]]:
        """Return list of (base64_png, width, height) without tapping anything.

        Sequence:
        - Launch app, wait 4 s → screen 0
        - Back (dismiss popup), wait 1.5 s → screen 1
        - Swipe down, wait 1.5 s → screen 2
        - (optional) Back + screenshot up to num_screens
        """
        evidence_dir.mkdir(parents=True, exist_ok=True)
        try:
            from phone_agent.actions.handler import ActionHandler
            action_handler: Any = ActionHandler(device_id=self.device_id)
        except Exception as exc:
            print(f"[onboarding] ActionHandler init failed: {exc}")
            action_handler = None
        screenshots: list[tuple[str, int, int]] = []

        # Launch app
        self._launch_by_package(app_name, package)
        time.sleep(4)

        def _snap(index: int) -> None:
            try:
                sc = self.device_factory.get_screenshot(self.device_id)
                b64 = sc.base64_data
                w, h = sc.width, sc.height
                screenshots.append((b64, w, h))
                img_path = evidence_dir / f"screen_{index}.png"
                img_path.write_bytes(base64.b64decode(b64))
            except Exception as exc:
                print(f"[onboarding] screenshot {index} failed: {exc}")

        _snap(0)  # initial launch screen

        if len(screenshots) < num_screens:
            # Press Back once to dismiss possible launch popup
            if action_handler is not None:
                try:
                    sc = self.device_factory.get_screenshot(self.device_id)
                    action_handler.execute(
                        {"_metadata": "do", "action": "Back"},
                        sc.width,
                        sc.height,
                    )
                except Exception:
                    pass
            time.sleep(1.5)
            _snap(1)

        if len(screenshots) < num_screens:
            # Swipe downward (scroll up reveal more content)
            if action_handler is not None:
                try:
                    sc = self.device_factory.get_screenshot(self.device_id)
                    action_handler.execute(
                        {
                            "_metadata": "do",
                            "action": "Swipe",
                            "element": [[500, 700], [500, 300]],
                        },
                        sc.width,
                        sc.height,
                    )
                except Exception:
                    pass
            time.sleep(1.5)
            _snap(2)

        # Extra screens: Back + screenshot
        extra_idx = 3
        while len(screenshots) < num_screens and extra_idx < 8:
            if action_handler is not None:
                try:
                    sc = self.device_factory.get_screenshot(self.device_id)
                    action_handler.execute(
                        {"_metadata": "do", "action": "Back"},
                        sc.width,
                        sc.height,
                    )
                except Exception:
                    pass
            time.sleep(1.5)
            _snap(extra_idx)
            extra_idx += 1

        return screenshots

    def _launch_by_package(self, app_name: str, package: str) -> None:
        """Launch app by name first; fall back to launch_package if name fails."""
        try:
            result = self.device_factory.launch_app(app_name, self.device_id)
            if result:
                return
        except Exception:
            pass
        # Fallback: launch by package directly
        self._launch_package_direct(package)

    def _launch_package_direct(self, package: str) -> None:
        """Launch by package via ADB monkey command (fallback)."""
        try:
            from phone_agent.adb.device import launch_package
            launch_package(package, self.device_id)
        except (ImportError, AttributeError):
            # launch_package not available; attempt via subprocess
            import subprocess
            adb_prefix = ["adb"]
            if self.device_id:
                adb_prefix = ["adb", "-s", self.device_id]
            subprocess.run(
                adb_prefix + [
                    "shell", "monkey", "-p", package,
                    "-c", "android.intent.category.LAUNCHER", "1",
                ],
                capture_output=True,
            )

    # ------------------------------------------------------------------
    # Step 3: strong-VLM profile inference
    # ------------------------------------------------------------------

    def _infer_profile(
        self,
        app_name: str,
        package: str,
        slug: str,
        screenshots: list[tuple[str, int, int]],
    ) -> tuple[AppProfile, str, float]:
        """Call strong VLM with all screenshots; parse JSON → AppProfile.

        Returns (profile, raw_vlm_output, confidence).
        On VLM error → returns minimal fallback draft profile with confidence 0.0.
        """
        schema_names = self.schema_registry.list_schemas()
        schema_menu = self._build_schema_menu(schema_names)

        if not self._client or not screenshots:
            return self._fallback_profile(app_name, package, slug, schema_names, "VLM unavailable or no screenshots"), "", 0.0

        prompt = self._build_inference_prompt(app_name, package, schema_menu)

        # Build messages with all screenshots
        content: list[dict] = [{"type": "text", "text": prompt}]
        for b64, _w, _h in screenshots:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            })

        raw_vlm = ""
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": content}],
                max_tokens=1024,
                temperature=0.0,
            )
            message = response.choices[0].message
            raw_vlm = getattr(message, "content", None) or ""
            if not raw_vlm.strip():
                return (
                    self._fallback_profile(app_name, package, slug, schema_names, "VLM returned empty content"),
                    raw_vlm,
                    0.0,
                )
        except Exception as exc:
            note = f"VLM call failed: {exc}"
            return self._fallback_profile(app_name, package, slug, schema_names, note), str(exc), 0.0

        # Parse JSON
        try:
            data = _parse_json_object(raw_vlm)
        except Exception:
            data = {}

        return self._build_profile_from_vlm(app_name, package, slug, data, schema_names, raw_vlm)

    def _build_schema_menu(self, schema_names: tuple[str, ...]) -> str:
        lines = ["可选域 (domain) 列表："]
        for name in schema_names:
            try:
                schema = self.schema_registry.merged(name)
                page_types_str = ", ".join(sorted(schema.page_types.keys()))
                lines.append(f"  {name}: [{page_types_str}]")
            except Exception:
                lines.append(f"  {name}")
        return "\n".join(lines)

    def _build_inference_prompt(
        self, app_name: str, package: str, schema_menu: str
    ) -> str:
        return (
            f"你是 App 领域分类专家。以下是 App「{app_name}」(包名: {package}) "
            f"的启动截图（按顺序）。\n\n"
            f"{schema_menu}\n\n"
            "请根据截图和包名，输出以下 **严格 JSON**（不要任何其他文字）：\n"
            "{\n"
            '  "domain": "<与上方列表完全一致的 domain 名称>",\n'
            '  "confidence": <0.0-1.0 浮点数>,\n'
            '  "app_id": "<ASCII snake_case，如 jd 或 pinduoduo>",\n'
            '  "display_name": "<中文或英文显示名>",\n'
            '  "aliases": ["<其他常用名称>"],\n'
            '  "coverage_page_types": ["<该域中此 App 会有的页面类型>"],\n'
            '  "coverage_transitions": [["<源页面>", "<目标页面>"]],\n'
            '  "unsafe_tokens": ["<该 App 界面语言下的危险按钮文案，如 立即购买、提交订单>"],\n'
            '  "trap_tokens": ["<直播/视频/广告/领券等陷阱入口文案>"],\n'
            '  "login_wall_on_launch": false,\n'
            '  "notes": "<onboarding 备注，可为空>",\n'
            '  "suggested_task": "<建议的探索任务描述，可为空>"\n'
            "}"
        )

    def _build_profile_from_vlm(
        self,
        app_name: str,
        package: str,
        slug: str,
        data: dict,
        schema_names: tuple[str, ...],
        raw_vlm: str,
    ) -> tuple[AppProfile, str, float]:
        """Convert parsed VLM JSON into a validated AppProfile."""
        domain = str(data.get("domain") or "")
        confidence = float(data.get("confidence") or 0.0)

        # Validate domain; fall back to common_mobile with halved confidence
        if domain not in schema_names:
            notes = (
                f"VLM suggested unknown domain '{domain}'; "
                f"fell back to common_mobile. confidence halved. "
                f"Original notes: {data.get('notes', '')}"
            )
            domain = "common_mobile"
            confidence = confidence * 0.5
        else:
            notes = str(data.get("notes") or "")

        # Determine schema name for the chosen domain
        schema_name = domain  # domains map directly to same-named schema files

        # Filter coverage_page_types to only known types in schema
        try:
            schema = self.schema_registry.merged(schema_name)
            known_types = set(schema.page_types.keys())
            raw_coverage = [str(t) for t in (data.get("coverage_page_types") or [])]
            unknown_types = [t for t in raw_coverage if t not in known_types]
            coverage_page_types = tuple(t for t in raw_coverage if t in known_types)
            if unknown_types:
                notes = f"{notes} [unknown page_types dropped: {unknown_types}]".strip()
        except Exception:
            coverage_page_types = ()

        raw_trans = data.get("coverage_transitions") or []
        coverage_transitions = tuple(
            (str(p[0]), str(p[1]))
            for p in raw_trans
            if isinstance(p, (list, tuple)) and len(p) >= 2
        )

        app_id = _sanitize_app_id(
            str(data.get("app_id") or ""),
            fallback=slug,
        )
        display_name = str(data.get("display_name") or app_name)

        aliases_raw = data.get("aliases") or []
        aliases = tuple(str(a) for a in aliases_raw if str(a) != display_name and str(a) != app_id)

        unsafe_tokens = tuple(str(t) for t in (data.get("unsafe_tokens") or []))
        trap_tokens = tuple(str(t) for t in (data.get("trap_tokens") or []))
        login_wall = bool(data.get("login_wall_on_launch") or False)
        default_task = str(data.get("suggested_task") or "")

        suggested_task = str(data.get("suggested_task") or "")
        notes_full = notes
        if suggested_task:
            notes_full = f"{notes_full} [suggested_task recorded in default_task]".strip()

        profile = AppProfile(
            app_id=app_id,
            display_name=display_name,
            package=package,
            schema_name=schema_name,
            aliases=aliases,
            status="draft",
            coverage_page_types=coverage_page_types,
            coverage_transitions=coverage_transitions,
            unsafe_tokens=unsafe_tokens,
            trap_tokens=trap_tokens,
            login_wall_on_launch=login_wall,
            default_task=default_task,
            onboarding_notes=notes_full,
        )
        return profile, raw_vlm, confidence

    def _fallback_profile(
        self,
        app_name: str,
        package: str,
        slug: str,
        schema_names: tuple[str, ...],
        reason: str,
    ) -> AppProfile:
        """Build a minimal draft profile when VLM is unavailable."""
        # Try to get schema from AppRegistry
        record = self.app_registry.resolve(app_name)
        schema_name = "common_mobile"
        if record:
            schema_name = record.schema

        return AppProfile(
            app_id=slug,
            display_name=app_name,
            package=package,
            schema_name=schema_name,
            aliases=(),
            status="draft",
            coverage_page_types=(),
            coverage_transitions=(),
            unsafe_tokens=(),
            trap_tokens=(),
            login_wall_on_launch=False,
            default_task="",
            onboarding_notes=f"[fallback profile] {reason}",
        )

    # ------------------------------------------------------------------
    # Step 4: register in AppRegistry
    # ------------------------------------------------------------------

    def _register_in_app_registry(self, profile: AppProfile) -> None:
        """Register the profile in AppRegistry (in-memory) and save to YAML."""
        # Map schema_name to a domain in the registry
        domain_map = {
            "shopping": "shopping",
            "utility": "utility",
            "common_mobile": "unknown",
        }
        domain = domain_map.get(profile.schema_name, "unknown")

        # Only register if not already present
        existing = self.app_registry.resolve(profile.app_id)
        if existing is None:
            record = AppRecord(
                app_id=profile.app_id,
                domain=domain,
                schema=profile.schema_name,
                package_names=(profile.package,) if profile.package else (),
                display_names=(profile.display_name,) + profile.aliases,
                legacy_aliases=(),
            )
            self.app_registry.register(record)
        try:
            self.app_registry.save()
        except Exception as exc:
            print(f"[onboarding] AppRegistry save failed: {exc}")


# ---------------------------------------------------------------------------
# JSON parsing helpers (robust, same style as PageClassifier)
# ---------------------------------------------------------------------------


def _parse_json_object(raw: str) -> dict:
    """Extract and parse a JSON object from raw VLM output."""
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
            cleaned = cleaned[start: end + 1]
    result = json.loads(cleaned)
    return result if isinstance(result, dict) else {}


def _make_slug(text: str) -> str:
    """ASCII snake_case slug from any string."""
    import unicodedata
    normalized = unicodedata.normalize("NFKD", text or "")
    ascii_part = re.sub(r"[^a-z0-9]+", "_", normalized.lower()).strip("_")
    if ascii_part:
        return ascii_part
    import hashlib
    digest = hashlib.md5((text or "").encode("utf-8")).hexdigest()[:8]
    return f"app_{digest}"


def _sanitize_app_id(value: str, fallback: str) -> str:
    """Ensure app_id is ASCII snake_case; use fallback if empty/invalid."""
    cleaned = re.sub(r"[^a-z0-9_]", "", value.lower().replace("-", "_"))
    return cleaned if cleaned else fallback


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Onboard an unfamiliar app: sample screens and infer an AppProfile."
    )
    parser.add_argument("--app", required=True, help="App display name (e.g. 京东).")
    parser.add_argument(
        "--package",
        default=None,
        help="Android package name (optional; auto-resolved if omitted).",
    )
    parser.add_argument(
        "--device-id",
        default=os.getenv("PHONE_AGENT_DEVICE_ID"),
        help="ADB device ID.",
    )
    parser.add_argument(
        "--num-screens",
        type=int,
        default=4,
        help="Number of screenshots to capture (default: 4).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Override strong VLM model name.",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Override strong VLM API base URL.",
    )
    parser.add_argument(
        "--apikey",
        default=None,
        help="Override strong VLM API key.",
    )
    args = parser.parse_args()

    from phone_agent.device_factory import DeviceType, get_device_factory, set_device_type

    set_device_type(DeviceType("adb"))

    onboarder = AppOnboarder(
        device_factory=get_device_factory(),
        device_id=args.device_id,
        model=args.model,
        base_url=args.base_url,
        api_key=args.apikey,
    )

    try:
        result = onboarder.onboard(
            args.app,
            package=args.package,
            num_screens=args.num_screens,
        )
    except ValueError as exc:
        print(f"[onboarding] 错误: {exc}")
        return 1

    print(f"\n{'='*60}")
    print(f"  Onboarding 完成: {result.profile.display_name}")
    print(f"{'='*60}")
    print(f"  Profile: {result.profile_path}")
    print(f"  Evidence: {result.evidence_dir}")
    print(f"  Domain 置信度: {result.domain_confidence:.2f}")
    print(f"  Schema: {result.profile.schema_name}")
    print(f"  Status: {result.profile.status}")
    print()
    if result.profile.status == "draft":
        print("  [下一步] 人工确认: 编辑以下文件，检查字段后将 status 改为 confirmed，")
        print("           之后即可使用 --auto-import-graph 进行探索入图。")
        print(f"    {result.profile_path}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
