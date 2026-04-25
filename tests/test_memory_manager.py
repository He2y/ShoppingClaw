import pytest
from unittest.mock import MagicMock
from phone_agent.memory.memory_manager import MemoryManager

def test_memory_manager_initialization():
    manager = MemoryManager()
    assert manager.graph_store is not None

def test_condense_trajectory_context():
    manager = MemoryManager()
    
    # Mock the graph_store's get_task_trajectory
    manager.graph_store.get_task_trajectory = MagicMock(return_value={
        "steps": [
            {"action_type": "click", "action_target": '淘宝'},
            {"action_type": "type", "action_target": 'iPhone 17'},
            {"action_type": "swipe", "action_target": "向上滑动"}
        ]
    })
    
    tasks = [
        {
            "task_id": "test_id",
            "description": "测试买iPhone",
            "app": "淘宝",
        }
    ]
    condensed = manager._condense_trajectory_context(tasks)
    # the exact output based on implementation: target slices or action type if no target
    assert "淘宝" in condensed
    assert "iPhone" in condensed
    assert "向上滑动" in condensed
    assert "→" in condensed
