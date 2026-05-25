#!/usr/bin/env python3
"""CLI for task-directed offline exploration of shopping apps."""

from __future__ import annotations

import argparse
import os

from dotenv import load_dotenv

from phone_agent.device_factory import DeviceFactory, DeviceType
from phone_agent.memory.offline_explorer import OfflineExplorer
from phone_agent.model.client import ModelClient, ModelConfig

load_dotenv()


def _device_type(value: str) -> DeviceType:
    normalized = value.lower()
    if normalized == "hdc":
        return DeviceType.HDC
    if normalized == "ios":
        return DeviceType.IOS
    return DeviceType.ADB


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline Explorer for shopping apps")
    parser.add_argument("--app", type=str, default="淘宝", help="App name to explore")
    parser.add_argument("--device-type", type=str, default="adb", help="Device type: adb, hdc, or ios")
    parser.add_argument("--queries", type=str, default="耳机,iPhone", help="Comma-separated search queries")
    parser.add_argument("--storage", type=str, default="memory_db/exploration", help="Storage directory")
    parser.add_argument("--max-steps", type=int, default=15, help="Max exploration steps")
    parser.add_argument("--no-import-graph", action="store_true", help="Skip SpatialGraphMemory import after saving JSON")
    parser.add_argument("--active-exploration", action="store_true", help="Use AMSG frontier scoring hints during exploration")
    parser.add_argument("--list-apps", action="store_true", help="List common shopping apps only")
    args = parser.parse_args()

    if args.list_apps:
        print("Supported shopping apps: 淘宝, 天猫, 京东, 拼多多")
        print("Make sure the app is installed on the connected device.")
        return 0

    config = ModelConfig(
        base_url=os.getenv("PHONE_AGENT_BASE_URL"),
        api_key=os.getenv("PHONE_AGENT_API_KEY"),
        model_name=os.getenv("PHONE_AGENT_MODEL"),
        max_tokens=9000,
        temperature=0.0,
        top_p=0.85,
        lang=os.getenv("PHONE_AGENT_LANG", "cn"),
    )
    model_client = ModelClient(config)
    device_factory = DeviceFactory(_device_type(args.device_type))

    try:
        device_factory.get_screenshot()
        print("  Device connected OK")
    except Exception as e:
        print(f"  Device connection failed: {e}")
        print("  Make sure a device is connected via ADB/HDC/iOS backend.")
        return 1

    explorer = OfflineExplorer(
        app_name=args.app,
        device_factory=device_factory,
        model_client=model_client,
        storage_dir=args.storage,
        max_steps=args.max_steps,
        task_description=f"Explore {args.app} shopping flows for queries: {args.queries}",
        auto_import_graph=not args.no_import_graph,
        active_exploration=args.active_exploration,
    )
    trajectories = explorer.explore()

    print(f"\n{'=' * 60}")
    print("  Summary")
    print(f"{'=' * 60}")
    for trajectory in trajectories:
        print(f"  [{trajectory.app}] {trajectory.task}: {len(trajectory.steps)} steps, success={trajectory.success}")
    print(f"\n  Files saved to: {args.storage}/")
    if explorer.last_import_result:
        result = explorer.last_import_result
        print(f"  Spatial graph imported: {result.pages_imported} pages, {result.transitions_imported} transitions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
