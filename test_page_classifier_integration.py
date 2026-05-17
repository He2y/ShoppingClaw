#!/usr/bin/env python
"""Test script to verify PageClassifier integration in PhoneAgent."""

import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from phone_agent.agent import AgentConfig
from phone_agent.model import ModelConfig
from phone_agent.memory.offline_explorer import ShoppingPageType


def test_page_classifier_initialization():
    """Test that PageClassifier is initialized correctly."""
    print("=" * 60)
    print("Test 1: PageClassifier Initialization")
    print("=" * 60)

    # Check if OFFLINE_VLM_API_KEY is set
    api_key = os.environ.get("OFFLINE_VLM_API_KEY") or os.environ.get("PHONE_AGENT_API_KEY")
    if not api_key:
        print("❌ OFFLINE_VLM_API_KEY not found in environment")
        return False

    print(f"✅ API key found: {api_key[:20]}...")

    # Import PhoneAgent
    try:
        from phone_agent.agent import PhoneAgent
        print("✅ PhoneAgent imported successfully")
    except Exception as e:
        print(f"❌ Failed to import PhoneAgent: {e}")
        return False

    # Create agent with memory enabled
    try:
        agent = PhoneAgent(
            model_config=ModelConfig(),
            agent_config=AgentConfig(enable_memory=True, verbose=True),
        )
        print("✅ PhoneAgent created successfully")
    except Exception as e:
        print(f"❌ Failed to create PhoneAgent: {e}")
        import traceback
        traceback.print_exc()
        return False

    # Check if page_classifier is initialized
    if hasattr(agent, 'page_classifier') and agent.page_classifier is not None:
        print("✅ PageClassifier initialized successfully")
        return True
    else:
        print("❌ PageClassifier not initialized")
        return False


def test_page_classifier_classify():
    """Test PageClassifier.classify() method."""
    print("\n" + "=" * 60)
    print("Test 2: PageClassifier Classification")
    print("=" * 60)

    from phone_agent.agent import PhoneAgent

    agent = PhoneAgent(
        model_config=ModelConfig(),
        agent_config=AgentConfig(enable_memory=True, verbose=False),
    )

    if not agent.page_classifier:
        print("❌ PageClassifier not available")
        return False

    # Test with a sample screenshot (if available)
    test_image_path = Path("tests/fixtures/taobao_home.png")
    if not test_image_path.exists():
        print("⚠️ No test image available, skipping classification test")
        return True

    import base64
    with open(test_image_path, "rb") as f:
        screenshot_b64 = base64.b64encode(f.read()).decode()

    try:
        page_type, summary, elements = agent.page_classifier.classify(
            screenshot_b64, 1080, 2400
        )
        print(f"✅ Classification successful:")
        print(f"   Page type: {page_type.value}")
        print(f"   Summary: {summary[:100]}...")
        print(f"   Elements: {len(elements) if elements else 0}")
        return True
    except Exception as e:
        print(f"❌ Classification failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_memory_manager_interface():
    """Test that memory_manager.locate_and_get_context() accepts screen_dict."""
    print("\n" + "=" * 60)
    print("Test 3: MemoryManager Interface")
    print("=" * 60)

    from phone_agent.memory.memory_manager import MemoryManager
    import inspect

    # Check method signature
    sig = inspect.signature(MemoryManager.locate_and_get_context)
    params = list(sig.parameters.keys())

    if 'screen_dict' in params:
        print(f"✅ screen_dict parameter found in locate_and_get_context()")
        print(f"   Parameters: {params}")
        return True
    else:
        print(f"❌ screen_dict parameter not found")
        print(f"   Parameters: {params}")
        return False


def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("PageClassifier Integration Test Suite")
    print("=" * 60 + "\n")

    results = []

    # Test 1: Initialization
    results.append(("PageClassifier Initialization", test_page_classifier_initialization()))

    # Test 2: Classification
    results.append(("PageClassifier Classification", test_page_classifier_classify()))

    # Test 3: Interface
    results.append(("MemoryManager Interface", test_memory_manager_interface()))

    # Summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)

    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status}: {name}")

    total = len(results)
    passed = sum(1 for _, r in results if r)

    print(f"\nTotal: {passed}/{total} tests passed")

    return all(r for _, r in results)


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
