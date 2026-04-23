"""
Review and commit pending trajectories from offline execution.

Reads pending_trajectories.json and displays each for review.
User can commit selected trajectories to Neo4j.

Usage:
  python scripts/review_trajectories.py
  python scripts/review_trajectories.py --commit 0   # commit index 0 immediately
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from phone_agent.memory.graph_store import GraphStore
from phone_agent.memory.memory_manager import MemoryManager


def main():
    parser = argparse.ArgumentParser(description="Review and commit pending trajectories")
    parser.add_argument("--commit", type=int, metavar="INDEX",
                        help="Commit trajectory at given index without prompt")
    parser.add_argument("--list-only", action="store_true",
                        help="Only list trajectories, don't commit")
    parser.add_argument("--storage-dir", type=str,
                        default="memory_db_offline_import",
                        help="Path to memory store (default: memory_db_offline_import)")
    args = parser.parse_args()

    pending_file = Path(args.storage_dir) / "pending_trajectories.json"

    if not pending_file.exists():
        print("❌ No pending_trajectories.json found.")
        print("   Run a successful task first — trajectories are saved automatically.")
        return

    with open(pending_file, "r", encoding="utf-8") as f:
        pending = json.load(f)

    if not pending:
        print("📭 No pending trajectories.")
        return

    print(f"📋 待审核轨迹 ({len(pending)} 条)\n")
    print("=" * 80)

    for i, entry in enumerate(pending):
        success_icon = "✅" if entry.get("success") else "❌"
        print(f"\n[{i}] {success_icon} {entry.get('task', 'Unknown task')[:70]}")
        print(f"    步骤数: {entry.get('steps')} | "
              f"APP: {', '.join(entry.get('apps', []) or ['N/A'])} | "
              f"保存: {entry.get('saved_at', '')[:19]}")
        print(f"    结果: {entry.get('result', 'N/A')[:60]}")

        steps = entry.get("step_details", [])
        for j, step in enumerate(steps[:5]):
            action = step.get("action_type", "?")
            params = step.get("action_params", {})
            target = params.get("element") or params.get("app") or params.get("text") or \
                    params.get("target_element") or str(params)[:30]
            print(f"      {j+1}. {action} → {target}")
        if len(steps) > 5:
            print(f"      ... (共 {len(steps)} 步)")
        print()

    print("=" * 80)

    if args.list_only:
        return

    if args.commit is not None:
        idx = args.commit
    else:
        try:
            idx = int(input("输入要提交的编号（回车取消）: ").strip())
        except (EOFError, ValueError):
            print("取消。")
            return

    if idx < 0 or idx >= len(pending):
        print(f"无效编号: {idx}")
        return

    entry = pending[idx]
    if not entry.get("success"):
        print("❌ 跳过失败轨迹（success=False）")
        return

    # Initialize MemoryManager with the same storage
    mm = MemoryManager(
        storage_dir=".",
        user_id="memory_db_offline_import",
        enable_auto_extract=False,
    )

    app = entry.get("apps", ["UnknownApp"])[0] if entry.get("apps") else "UnknownApp"
    ok = mm.graph_store.commit_task_trajectory(
        task_description=entry["task"],
        task_id=entry["task"][:20],
        app=app,
        start_state_id=entry.get("start_state_id"),
        end_state_id=entry.get("end_state_id"),
        success=True,
    )

    if ok:
        # Remove from pending
        pending.pop(idx)
        with open(pending_file, "w", encoding="utf-8") as f:
            json.dump(pending, f, ensure_ascii=False, indent=2)
        print(f"\n✅ 已提交到 Neo4j: {entry['task'][:60]}")
        print(f"   app={app}, steps={entry.get('steps')}")
    else:
        print("\n❌ 提交失败，请检查 Neo4j 连接。")


if __name__ == "__main__":
    main()
