"""CLI entry point for the offline explorer."""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

from dotenv import load_dotenv

from phone_agent.device_factory import DeviceType, get_device_factory, set_device_type
from phone_agent.model.client import ModelClient, ModelConfig

from .explorer import OfflineExplorer, _build_taobao_task
from .human_gate import ConsoleHumanGate
from .interference import InterferencePolicy
from .task_builder import build_default_task
from .watchdog import WatchdogConfig


def _apply_draft_gate(app_profile: "Any | None", auto_import_graph: bool) -> tuple[bool, str | None]:
    """Check draft gate: returns (effective_auto_import, warning_message | None).

    If profile.status == "draft" and auto_import_graph is True, refuse auto import
    and return a warning message.
    """
    if app_profile is None:
        return auto_import_graph, None
    status = str(getattr(app_profile, "status", "confirmed") or "confirmed")
    if status == "draft" and auto_import_graph:
        msg = (
            f"[draft gate] App '{getattr(app_profile, 'display_name', '')}' 的 profile "
            f"状态为 draft，不允许自动入图 (--auto-import-graph)。\n"
            "请人工确认 profile，将 status 改为 confirmed 后再执行入图。\n"
            f"  Profile 路径提示: 运行 `python -m phone_agent.memory.exploration.onboarding "
            f"--app {getattr(app_profile, 'display_name', '')}` 查看或重新生成。"
        )
        return False, msg
    return auto_import_graph, None


def _resolve_app_profile(args: Any) -> Any:
    """Load AppProfile if --profile is given or auto-resolvable from --app."""
    try:
        from phone_agent.spatial.app_profiles import AppProfileRegistry
        profile_registry = AppProfileRegistry()

        # Explicit --profile flag
        if hasattr(args, "profile") and args.profile:
            profile = profile_registry.load(args.profile)
            if profile is None:
                profile = profile_registry.resolve(args.profile)
            return profile

        # Auto-resolve from --app
        if hasattr(args, "app") and args.app:
            return profile_registry.resolve(args.app)
    except Exception:
        pass
    return None


def _resolve_schema_name(args: Any, app_profile: Any) -> str:
    """--schema flag → profile.schema_name → AppRegistry lookup → shopping."""
    if getattr(args, "schema", None):
        return str(args.schema)
    if app_profile is not None and getattr(app_profile, "schema_name", ""):
        return str(app_profile.schema_name)
    try:
        from phone_agent.spatial.app_registry import get_default_app_registry

        record = get_default_app_registry().resolve(args.app)
        if record:
            return record.schema
    except Exception:
        pass
    return "shopping"


def _resolve_default_task(args: Any, app_profile: Any, schema_name: str) -> str:
    """Profile task → schema {app}-templated task → Taobao legacy fallback."""
    try:
        from phone_agent.spatial.schema_registry import get_default_registry

        schema = get_default_registry().merged(schema_name)
        task = build_default_task(schema, app_profile, None, args.app)
        if task and task != "广度优先探索所有主要页面类型":
            return task
    except Exception:
        pass
    return _build_taobao_task()


def _resolve_storage_dir(args: Any) -> str:
    """Explicit --storage-dir wins; default is one directory per canonical app."""
    if getattr(args, "storage_dir", None):
        return str(args.storage_dir)
    try:
        from phone_agent.spatial.app_registry import get_default_app_registry

        app_id = get_default_app_registry().canonical_id(args.app) or "default"
    except Exception:
        app_id = "default"
    return f"memory_db/exploration/{app_id}"


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Coverage-guided offline explorer for shopping apps.")
    parser.add_argument("--app", default="淘宝", help="Target shopping app name.")
    parser.add_argument("--task", default=None, help="Natural-language exploration task.")
    parser.add_argument(
        "--storage-dir",
        default=None,
        help="Output directory. Default: memory_db/exploration/<canonical_app_id>.",
    )
    parser.add_argument("--max-steps", type=int, default=int(os.getenv("OFFLINE_EXPLORER_MAX_STEPS", "24")))
    parser.add_argument("--device-type", choices=["adb", "hdc"], default=os.getenv("PHONE_AGENT_DEVICE_TYPE", "adb"))
    parser.add_argument("--device-id", default=os.getenv("PHONE_AGENT_DEVICE_ID"))
    parser.add_argument("--base-url", default=os.getenv("PHONE_AGENT_BASE_URL", "http://localhost:8000/v1"))
    parser.add_argument("--model", default=os.getenv("PHONE_AGENT_MODEL", "autoglm-phone-9b"))
    parser.add_argument("--apikey", default=os.getenv("PHONE_AGENT_API_KEY", "EMPTY"))
    parser.add_argument(
        "--classifier-base-url",
        default=None,
        help="Override classifier API base URL. Default: AMSG_STRONG_VLM_* -> OFFLINE_VLM_* -> PHONE_AGENT_*.",
    )
    parser.add_argument(
        "--classifier-model",
        default=None,
        help="Override classifier model. Default: AMSG_STRONG_VLM_* -> OFFLINE_VLM_* -> PHONE_AGENT_*.",
    )
    parser.add_argument(
        "--classifier-apikey",
        default=None,
        help="Override classifier API key. Default: AMSG_STRONG_VLM_* -> OFFLINE_VLM_* -> PHONE_AGENT_*.",
    )
    parser.add_argument("--classifier-mode", choices=["fast", "full", "off"], default=os.getenv("OFFLINE_CLASSIFIER_MODE", "fast"))
    parser.add_argument("--classifier-timing", choices=["after_action", "before_action"], default=os.getenv("OFFLINE_CLASSIFIER_TIMING", "after_action"))
    parser.add_argument("--classifier-timeout", type=float, default=float(os.getenv("OFFLINE_CLASSIFIER_TIMEOUT", "8")))
    parser.add_argument("--classifier-max-image-width", type=int, default=int(os.getenv("OFFLINE_CLASSIFIER_MAX_IMAGE_WIDTH", "720")))
    parser.add_argument("--auto-import-graph", action="store_true", help="Promote collected staging graph into Neo4j.")
    parser.add_argument("--database", default="shopping-spatial-v2", help="Neo4j database for --auto-import-graph.")
    parser.add_argument("--active-exploration", action="store_true", help="Use AMSG frontier scoring hints during exploration.")
    parser.add_argument("--schema", default=None, help="Domain schema name (e.g. shopping). Defaults to app registry lookup.")
    parser.add_argument(
        "--transition-policy",
        choices=["strict", "schema_guided", "permissive"],
        default="strict",
        help="Transition validation policy: strict (default), schema_guided, permissive.",
    )
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument(
        "--pause-on-login",
        action="store_true",
        help="Pause and prompt the user when a login wall is encountered.",
    )
    parser.add_argument(
        "--no-human",
        action="store_true",
        help="Force non-interactive mode (skip all HumanGate prompts).",
    )
    parser.add_argument(
        "--no-wait-stable",
        action="store_true",
        help="Disable stability-based screen wait after actions (use fixed sleep instead).",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="AppProfile app_id to load for coverage/safety overrides (e.g. jd, pinduoduo).",
    )
    parser.add_argument(
        "--focus",
        default=None,
        help=(
            "本轮探索焦点: 页面类型或转移, 逗号分隔 (e.g. 'category,my_account' 或 "
            "'search_result->filter_panel'). 缺省时自动从历史覆盖缺口中选取."
        ),
    )
    args = parser.parse_args()

    set_device_type(DeviceType(args.device_type))
    graph_store = None

    # Phase 3: resolve app profile
    app_profile = _resolve_app_profile(args)
    effective_auto_import, draft_warning = _apply_draft_gate(app_profile, args.auto_import_graph)
    if draft_warning:
        print(draft_warning)
    schema_name = _resolve_schema_name(args, app_profile)

    try:
        if effective_auto_import:
            from ..graph_store import GraphStore

            graph_store = GraphStore(database=args.database)

        # Phase 4: interference policy + human gate.
        # The gate is always created in interactive runs - captchas cannot be
        # dismissed programmatically, so the operator must be able to take
        # over even without --pause-on-login (which only governs login walls).
        login_action = "pause_for_human" if args.pause_on_login else "back_out"
        captcha_action = "back_out" if args.no_human else "pause_for_human"
        interference_policy = InterferencePolicy(
            login_action=login_action,
            captcha_action=captcha_action,
        )
        human_gate = None
        if not args.no_human:
            human_gate = ConsoleHumanGate(
                timeout_s=interference_policy.pause_timeout_s,
                interactive=True,
            )

        explorer = OfflineExplorer(
            app_name=args.app,
            device_factory=get_device_factory(),
            model_client=ModelClient(
                ModelConfig(
                    base_url=args.base_url,
                    api_key=args.apikey,
                    model_name=args.model,
                    lang=os.getenv("PHONE_AGENT_LANG", "cn"),
                )
            ),
            storage_dir=_resolve_storage_dir(args),
            max_steps=args.max_steps,
            task_description=args.task or _resolve_default_task(args, app_profile, schema_name),
            classifier_api_key=args.classifier_apikey,
            classifier_base_url=args.classifier_base_url,
            classifier_model=args.classifier_model,
            classifier_mode=args.classifier_mode,
            classifier_timing=args.classifier_timing,
            classifier_timeout=args.classifier_timeout,
            classifier_max_image_width=args.classifier_max_image_width,
            auto_import_graph=effective_auto_import,
            graph_store=graph_store,
            device_id=args.device_id,
            active_exploration=args.active_exploration,
            verbose=not args.quiet,
            schema_name=schema_name,
            transition_policy=args.transition_policy,
            interference_policy=interference_policy,
            watchdog_config=WatchdogConfig(),
            human_gate=human_gate,
            wait_stable=not args.no_wait_stable,
            app_profile=app_profile,
            focus=args.focus,
        )
        trajectories = explorer.explore()
        report = {
            "app": args.app,
            "storage_dir": str(explorer.storage_dir),
            "trajectories": len(trajectories),
            "coverage": explorer.coverage_report.to_dict(),
            "auto_import": explorer.last_import_result,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    finally:
        if graph_store:
            graph_store.close()


if __name__ == "__main__":
    raise SystemExit(main())
