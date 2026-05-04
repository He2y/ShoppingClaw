#!/usr/bin/env python3
"""
Offline Explorer CLI - Run task-directed VLM exploration of shopping apps.

Each run focuses on ONE functional area (search, cart, category, account,
product). Run multiple times with different --task values to build a
complete app page graph. Combine all JSON outputs afterwards.

Usage:
    python -m phone_agent.memory.run_explorer --app 京东 --task search
    python -m phone_agent.memory.run_explorer --app 京东 --task cart
    python -m phone_agent.memory.run_explorer --app 京东 --task category
    python -m phone_agent.memory.run_explorer --app 京东 --task account
    python -m phone_agent.memory.run_explorer --app 京东 --task product
    python -m phone_agent.memory.run_explorer --app 京东 --task general
    python -m phone_agent.memory.run_explorer --list-tasks
"""

import argparse
import os

from dotenv import load_dotenv
load_dotenv()

from phone_agent.device_factory import DeviceFactory, DeviceType
from phone_agent.model.client import ModelClient, ModelConfig
from phone_agent.memory.offline_explorer import (
    ExplorationTask,
    OfflineExplorer,
    _TASK_DESCRIPTIONS,
)


def main():
    parser = argparse.ArgumentParser(
        description="Task-Directed App Explorer — each run focuses on one functional area",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s --app 京东 --task search    # Explore search flow\n"
            "  %(prog)s --app 京东 --task cart      # Explore cart & checkout\n"
            "  %(prog)s --app 京东 --task category  # Explore category browsing\n"
            "  %(prog)s --app 京东 --task account   # Explore account & settings\n"
            "  %(prog)s --app 京东 --task product   # Explore product detail\n"
            "  %(prog)s --app 京东 --task general   # Broad exploration\n\n"
            "After running all tasks, combine JSONs in --storage dir to build the graph."
        ),
    )
    parser.add_argument("--app", type=str, default="京东", help="App name to explore")
    parser.add_argument(
        "--task", type=str, default="general",
        choices=["search", "cart", "category", "account", "product", "general"],
        help="Exploration direction (default: general)",
    )
    parser.add_argument("--device-type", type=str, default="adb", help="Device type (adb/hdc/ios)")
    parser.add_argument("--max-steps", type=int, default=15, help="Max exploration steps (default: 15)")
    parser.add_argument("--storage", type=str, default="memory_db/exploration", help="Storage directory")
    parser.add_argument("--quiet", action="store_true", help="Suppress verbose output")
    parser.add_argument("--list-tasks", action="store_true", help="List available task types")
    parser.add_argument("--list-apps", action="store_true", help="List supported shopping apps")
    args = parser.parse_args()

    if args.list_tasks:
        print("Available exploration tasks:")
        print("-" * 50)
        for key, desc in _TASK_DESCRIPTIONS.items():
            print(f"  {key:<12} {desc}")
        print("\nUse --task <name> to select one.")
        return

    if args.list_apps:
        print("Supported shopping apps: 京东, 淘宝, 拼多多, 天猫, 美团, 饿了么")
        print("Make sure the app is installed on the connected device.")
        return

    # ── Init ModelClient (reuse existing VLM pipeline) ──
    config = ModelConfig(
        base_url=os.getenv("PHONE_AGENT_BASE_URL", "http://localhost:8000/v1"),
        api_key=os.getenv("PHONE_AGENT_API_KEY", "EMPTY"),
        model_name=os.getenv("PHONE_AGENT_MODEL", "autoglm-phone"),
        max_tokens=3000,
        temperature=0.0,
        top_p=0.85,
        lang=os.getenv("PHONE_AGENT_LANG", "cn"),
    )
    model_client = ModelClient(config)

    # ── Init DeviceFactory ──
    dt_map = {"adb": DeviceType.ADB, "hdc": DeviceType.HDC, "ios": DeviceType.IOS}
    dt = dt_map.get(args.device_type, DeviceType.ADB)
    device_factory = DeviceFactory(dt)

    # ── Check device connectivity ──
    try:
        sc = device_factory.get_screenshot()
        print(f"  Device connected OK ({sc.width}x{sc.height})")
    except Exception as e:
        print(f"  Device connection failed: {e}")
        print("  Make sure a device is connected via ADB/HDC.")
        return

    # ── Run task-directed exploration ──
    task = ExplorationTask(args.task)
    explorer = OfflineExplorer(
        app_name=args.app,
        device_factory=device_factory,
        model_client=model_client,
        storage_dir=args.storage,
        max_steps=args.max_steps,
        exploration_task=task,
        verbose=not args.quiet,
    )

    trajectories = explorer.explore()

    if trajectories:
        t = trajectories[0]
        print(f"\n{'='*60}")
        print(f"  Exploration Summary: {args.app} [{args.task}]")
        print(f"{'='*60}")
        print(f"  Steps taken: {len(t.steps)}")
        print(f"  Unique pages discovered: {len(explorer.discovered_pages)}")
        print(f"\n  Pages by type:")
        by_type: dict[str, int] = {}
        for p in explorer.discovered_pages.values():
            by_type[p.page_type.value] = by_type.get(p.page_type.value, 0) + 1
        for ptype, count in sorted(by_type.items()):
            print(f"    {ptype}: {count}")
        print(f"\n  Files saved to: {args.storage}/")
    else:
        print("  Exploration failed. Check device and app availability.")


if __name__ == "__main__":
    main()
