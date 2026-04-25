import pytest
import os
import json
import numpy as np
import tempfile
from pathlib import Path
from phone_agent.memory.task_index import TaskIndex

def test_task_index_save_load(monkeypatch):
    monkeypatch.setenv("EMBEDDING_API_KEY", "")
    with tempfile.TemporaryDirectory() as tmpdir:
        index = TaskIndex(storage_dir=tmpdir)
        # Should return 0 vector
        index.add_task("task1", "hello")
        
        # Load in a new instance
        index2 = TaskIndex(storage_dir=tmpdir)
        success = index2.load()
        
        assert success == True
        assert "task1" in index2.task_ids
        assert index2.index.ntotal == 1

def test_task_index_rebuild(monkeypatch):
    monkeypatch.setenv("EMBEDDING_API_KEY", "")
    with tempfile.TemporaryDirectory() as tmpdir:
        index = TaskIndex(storage_dir=tmpdir)
        descriptions = [("task1", "hello"), ("task2", "world")]
        
        index.rebuild_from_neo4j(descriptions)
        assert index.index.ntotal == 2
        assert index.task_ids == ["task1", "task2"]
