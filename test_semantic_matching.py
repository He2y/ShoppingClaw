#!/usr/bin/env python
"""Test semantic extraction and graph lookup without real device."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

from phone_agent.memory.graph_store import GraphStore
from phone_agent.memory.spatial_graph_memory import SpatialGraphMemory


def test_semantic_matching():
    """Test if extracted semantics match graph nodes."""
    print("=" * 60)
    print("Semantic Matching Test")
    print("=" * 60)

    # Initialize graph
    graph_store = GraphStore()
    memory = SpatialGraphMemory(graph_store)

    print(f"\n✅ Graph memory initialized")
    print(f"  Database: {graph_store.database}")

    # Simulate extracted semantics from PageClassifier
    test_cases = [
        {
            "app": "淘宝",
            "page_type": "home",
            "summary": "淘宝APP首页，包含搜索栏、推荐商品",
            "landmarks": ["search_bar", "bottom_tabs"],
        },
        {
            "app": "淘宝",
            "page_type": "search_input",
            "summary": "淘宝搜索输入页",
            "landmarks": ["search_bar", "keyboard"],
        },
        {
            "app": "淘宝",
            "page_type": "search_result",
            "summary": "搜索结果列表",
            "landmarks": ["product_cards", "filter_tabs"],
        },
    ]

    print(f"\n📊 Testing semantic matching:")
    for i, case in enumerate(test_cases, 1):
        print(f"\n  Test case {i}:")
        print(f"    App: {case['app']}")
        print(f"    Page type: {case['page_type']}")
        print(f"    Summary: {case['summary'][:50]}...")

        # Build screen dict
        screen_dict = {
            "ui_hash": "test_hash",
            "semantic_layout": f"{case['app']} {case['page_type']} {' '.join(case['landmarks'])}",
            "app": case["app"],
            "page_type": case["page_type"],
            "summary": case["summary"],
            "elements": None,
        }

        # Call locate
        belief = memory.locate(screen_dict, task="测试任务")

        if belief.candidates:
            best = belief.candidates[0]
            state = best.state
            print(f"    ✅ Matched to graph node:")
            print(f"       State ID: {state.state_id[:60]}...")
            print(f"       App: {state.app}")
            print(f"       Page type: {state.page_type}")

            # Check outgoing edges
            edges = memory._load_edges(state.state_id, allowed_app=case["app"])
            print(f"       Outgoing edges: {len(edges)}")
            if edges:
                for edge in edges[:3]:
                    print(f"         → {edge.action_type} on {edge.action_target[:30]} → {edge.postcondition} (conf={edge.confidence:.2f})")
        else:
            print(f"    ❌ No matching nodes found")

    print(f"\n✅ Test complete")


if __name__ == "__main__":
    test_semantic_matching()
