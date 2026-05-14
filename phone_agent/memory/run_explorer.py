#!/usr/bin/env python3
"""
Offline Explorer CLI - Run systematic exploration of shopping apps.

Usage:
    python -m phone_agent.memory.run_explorer --app 京东
    python -m phone_agent.memory.run_explorer --app 淘宝 --queries "手机,iPhone,耳机"
    python -m phone_agent.memory.run_explorer --list-apps
"""

import argparse
import os

from dotenv import load_dotenv
load_dotenv()

from phone_agent.device_factory import DeviceFactory, DeviceType
from phone_agent.model.client import ModelClient, ModelConfig
from phone_agent.memory.offline_explorer import OfflineExplorer


def main():
    parser = argparse.ArgumentParser(description="Offline Explorer for Shopping Apps")
    parser.add_argument("--app", type=str, default="京东", help="App name to explore")
    parser.add_argument("--device-type", type=str, default="adb", help="Device type (adb/hdc/ios)")
    parser.add_argument("--queries", type=str, default="手机,iPhone", help="Comma-separated search queries")
    parser.add_argument("--storage", type=str, default="memory_db/exploration", help="Storage directory")
    parser.add_argument("--list-apps", action="store_true", help="List supported apps only")
    args = parser.parse_args()

    if args.list_apps:
        print("Supported shopping apps: 京东, 淘宝, 拼多多")
        print("Make sure the app is installed on the connected device.")
        return

    # ── Init ModelClient (reuse existing pipeline) ──
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

    # ── Init DeviceFactory ──
    dt = DeviceType.ADB
    if args.device_type == "hdc":
        dt = DeviceType.HDC
    elif args.device_type == "ios":
        dt = DeviceType.IOS
    device_factory = DeviceFactory(dt)

    # ── Check device connectivity ──
    try:
        device_factory.get_screenshot()
        print("  Device connected OK")
    except Exception as e:
        print(f"  Device connection failed: {e}")
        print("  Make sure a device is connected via ADB.")
        return

    # ── Run exploration ──
    explorer = OfflineExplorer(
        app_name=args.app,
        device_factory=device_factory,
        model_client=model_client,
        storage_dir=args.storage,
    )

    trajectories = explorer.explore_shopping_flows()

    print(f"\n{'='*60}")
    print(f"  Summary")
    print(f"{'='*60}")
    for t in trajectories:
        print(f"  [{t.app}] {t.task}: {len(t.steps)} steps, success={t.success}")
    print(f"\n  Files saved to: {args.storage}/")


if __name__ == "__main__":
    main()
