"""CLI entry point for the offline explorer."""

import argparse
import json
import os

from dotenv import load_dotenv

from phone_agent.device_factory import DeviceType, get_device_factory, set_device_type
from phone_agent.model.client import ModelClient, ModelConfig

from .explorer import OfflineExplorer, _build_taobao_task
from .human_gate import ConsoleHumanGate
from .interference import InterferencePolicy
from .task_builder import build_default_task
from .watchdog import WatchdogConfig


def _resolve_default_task(schema_name: str | None) -> str:
    """Return the default task for the given schema, falling back to Taobao task."""
    if schema_name is not None:
        try:
            from phone_agent.spatial.schema_registry import get_default_registry
            schema = get_default_registry().merged(schema_name)
            task = build_default_task(schema)
            if task and task != "广度优先探索所有主要页面类型":
                return task
        except Exception:
            pass
    return _build_taobao_task()


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Coverage-guided offline explorer for shopping apps.")
    parser.add_argument("--app", default="淘宝", help="Target shopping app name.")
    parser.add_argument("--task", default=None, help="Natural-language exploration task.")
    parser.add_argument("--storage-dir", default="memory_db/exploration/taobao_spatial_v2", help="Output directory.")
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
    args = parser.parse_args()

    set_device_type(DeviceType(args.device_type))
    graph_store = None
    try:
        if args.auto_import_graph:
            from ..graph_store import GraphStore

            graph_store = GraphStore(database=args.database)

        # Phase 4: interference policy + human gate
        login_action = "pause_for_human" if args.pause_on_login else "back_out"
        interference_policy = InterferencePolicy(login_action=login_action)
        human_gate = None
        if args.pause_on_login:
            human_gate = ConsoleHumanGate(
                timeout_s=interference_policy.pause_timeout_s,
                interactive=not args.no_human,
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
            storage_dir=args.storage_dir,
            max_steps=args.max_steps,
            task_description=args.task or _resolve_default_task(args.schema),
            classifier_api_key=args.classifier_apikey,
            classifier_base_url=args.classifier_base_url,
            classifier_model=args.classifier_model,
            classifier_mode=args.classifier_mode,
            classifier_timing=args.classifier_timing,
            classifier_timeout=args.classifier_timeout,
            classifier_max_image_width=args.classifier_max_image_width,
            auto_import_graph=args.auto_import_graph,
            graph_store=graph_store,
            device_id=args.device_id,
            active_exploration=args.active_exploration,
            verbose=not args.quiet,
            schema_name=args.schema,
            transition_policy=args.transition_policy,
            interference_policy=interference_policy,
            watchdog_config=WatchdogConfig(),
            human_gate=human_gate,
            wait_stable=not args.no_wait_stable,
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
