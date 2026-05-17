#!/usr/bin/env python
"""Full integration test of PageClassifier with multiple screenshots."""

import os
import sys
import base64
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

from phone_agent.memory.offline_explorer import PageClassifier, ShoppingPageType


def test_pageclassifier_full():
    """Test PageClassifier with all available screenshots."""
    print("=" * 60)
    print("PageClassifier Full Integration Test")
    print("=" * 60)

    api_key = os.environ.get("OFFLINE_VLM_API_KEY")
    model = os.environ.get("OFFLINE_VLM_MODEL")

    print(f"\nConfiguration:")
    print(f"  Model: {model}")
    print(f"  API Key: {api_key[:20]}...")

    # Initialize classifier
    print(f"\n📡 Initializing PageClassifier...")
    try:
        classifier = PageClassifier(api_key=api_key)
        print(f"✅ PageClassifier initialized")
    except Exception as e:
        print(f"❌ Initialization failed: {e}")
        return False

    # Test images
    test_images = [
        ("tmp/taobao_home.png", "home", "淘宝首页"),
        ("tmp/taobao_search_result.png", "search_result", "淘宝搜索结果"),
        ("tmp/taobao_product_detail.png", "product_detail", "淘宝商品详情"),
    ]

    print(f"\n📊 Testing classification on {len(test_images)} images:")
    print(f"{'-' * 60}")

    success_count = 0
    for image_path, expected_type, desc in test_images:
        if not Path(image_path).exists():
            print(f"\n⚠️ Image not found: {image_path}")
            continue

        print(f"\n📸 Test: {desc}")
        print(f"  Image: {image_path}")
        print(f"  Expected: {expected_type}")

        try:
            with open(image_path, "rb") as f:
                screenshot_b64 = base64.b64encode(f.read()).decode()

            page_type, summary, elements = classifier.classify(
                screenshot_b64, 1080, 2340
            )

            print(f"  Result:")
            print(f"    Page type: {page_type.value}")
            print(f"    Summary: {summary[:50]}{'...' if len(summary) > 50 else ''}")

            if page_type.value == expected_type:
                print(f"  ✅ Correct classification!")
                success_count += 1
            elif page_type == ShoppingPageType.UNKNOWN:
                print(f"  ❌ Classification failed (UNKNOWN)")
            else:
                print(f"  ⚠️ Incorrect (expected {expected_type}, got {page_type.value})")

        except Exception as e:
            print(f"  ❌ Error: {e}")

    print(f"\n{'-' * 60}")
    print(f"\n📊 Summary:")
    print(f"  Total tests: {len(test_images)}")
    print(f"  Successful: {success_count}")
    print(f"  Success rate: {success_count / len(test_images) * 100:.1f}%")

    return success_count == len(test_images)


if __name__ == "__main__":
    success = test_pageclassifier_full()
    sys.exit(0 if success else 1)
