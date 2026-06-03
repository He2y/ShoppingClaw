#!/usr/bin/env python3
"""CLI for model-driven autonomous exploration of mobile apps."""

from __future__ import annotations

import argparse
import os

from dotenv import load_dotenv

from phone_agent.device_factory import DeviceFactory, DeviceType
from phone_agent.memory.autonomous_explorer import AutonomousExplorer, ExplorationPolicy
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
    parser = argparse.ArgumentParser(
        description="Model-driven autonomous explorer for mobile apps",
    )
    parser.add_argument("--app", type=str, default="淘宝", help="App name to explore")
    parser.add_argument("--device-type", type=str, default="adb", help="Device type: adb, hdc, or ios")
    parser.add_argument("--device-id", type=str, default=None, help="Device ID override")
    parser.add_argument("--storage", type=str, default="memory_db/exploration/autonomous", help="Storage directory")
    parser.add_argument("--max-steps", type=int, default=80, help="Max total exploration steps")
    parser.add_argument("--max-job-steps", type=int, default=3, help="Max steps per exploration job")
    parser.add_argument("--max-dry-rounds", type=int, default=5, help="Consecutive dry rounds before convergence")
    parser.add_argument("--use-strong-vlm", action="store_true", help="Use strong VLM for functionality extraction")
    parser.add_argument("--classifier-mode", type=str, default="fast", choices=["fast", "full", "off"])
    parser.add_argument("--quiet", action="store_true", help="Suppress verbose output")
    args = parser.parse_args()

    config = ModelConfig(
        base_url=os.getenv("PHONE_AGENT_BASE_URL"),
        api_key=os.getenv("PHONE_AGENT_API_KEY"),
        model_name=os.getenv("PHONE_AGENT_MODEL"),
        max_tokens=4096,
        temperature=0.0,
        top_p=0.85,
        lang=os.getenv("PHONE_AGENT_LANG", "cn"),
    )
    model_client = ModelClient(config)
    device_factory = DeviceFactory(_device_type(args.device_type))

    try:
        device_factory.get_screenshot(args.device_id)
        print("  Device connected OK")
    except Exception as e:
        print(f"  Device connection failed: {e}")
        print("  Make sure a device is connected via ADB/HDC/iOS backend.")
        return 1

    policy = ExplorationPolicy(
        max_total_steps=args.max_steps,
        max_job_steps=args.max_job_steps,
        max_dry_rounds=args.max_dry_rounds,
        use_strong_vlm_extractor=args.use_strong_vlm,
    )
    explorer = AutonomousExplorer(
        app_name=args.app,
        device_factory=device_factory,
        model_client=model_client,
        policy=policy,
        storage_dir=args.storage,
        classifier_mode=args.classifier_mode,
        device_id=args.device_id,
        verbose=not args.quiet,
    )
    trajectories = explorer.explore()

    print(f"\n{'=' * 60}")
    print("  Autonomous Exploration Summary")
    print(f"{'=' * 60}")
    for traj in trajectories:
        print(f"  [{traj.app}] {len(traj.steps)} steps, success={traj.success}")
    print(f"  Pages discovered: {len(explorer._discovered_pages)}")
    print(f"  Transitions recorded: {len(explorer._transitions)}")
    print(f"  Functionality clusters: {len(explorer._all_clusters)}")
    print(f"  Job rounds: {explorer._convergence.total_rounds}")
    print(f"  Converged: {explorer._convergence.converged}")
    print(f"\n  Files saved to: {args.storage}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
