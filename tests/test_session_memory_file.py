"""Session memory file: persistence, rendering, checkpoint merging."""

from types import SimpleNamespace

from phone_agent.session_memory_file import (
    RevisionEntry,
    SessionMemoryFile,
    SubTask,
    VerifiedFact,
)

_PLAN = {
    "search_query": "蓝牙耳机",
    "product": "蓝牙耳机",
    "specs": {"price_range": "500-1000元", "feature": "降噪"},
    "target_action": "add_to_cart",
    "steps": [
        {"description": "搜索蓝牙耳机", "target_page": "search_result"},
        {"description": "设置价格筛选", "target_page": "filter_panel"},
        {"description": "选择商品加购", "target_page": "spec_selection"},
    ],
}


def _memory(tmp_path) -> SessionMemoryFile:
    return SessionMemoryFile.create(
        "去淘宝买蓝牙耳机，500-1000元", _PLAN, sessions_dir=tmp_path,
    )


def test_create_extracts_plan_fields(tmp_path):
    memory = _memory(tmp_path)
    assert memory.original_task == "去淘宝买蓝牙耳机，500-1000元"
    assert memory.product["name"] == "蓝牙耳机"
    assert memory.constraints["price_range"] == "500-1000元"
    assert len(memory.subtasks) == 3
    assert memory.subtasks[0].status == "current"
    assert memory.revisions[0].trigger == "init"


def test_save_load_roundtrip_atomic(tmp_path):
    memory = _memory(tmp_path)
    memory.verified_facts.append(VerifiedFact(fact="已应用价格筛选", step=5))
    assert memory.save() is True
    assert not list(tmp_path.glob("*.tmp"))  # atomic write leaves no temp

    loaded = SessionMemoryFile.load(memory._path)
    assert loaded is not None
    assert loaded.original_task == memory.original_task
    assert loaded.verified_facts[0].fact == "已应用价格筛选"
    assert loaded.subtasks[1].description == "设置价格筛选"


def test_load_corrupted_returns_none(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert SessionMemoryFile.load(bad) is None


def test_render_injection_content(tmp_path):
    memory = _memory(tmp_path)
    for i in range(7):
        memory.verified_facts.append(VerifiedFact(fact=f"事实{i}", step=i))

    text = memory.render_injection()
    assert "原始任务: 去淘宝买蓝牙耳机" in text
    assert "price_range=500-1000元" in text
    assert "[current]" in text
    assert "✔ 事实6" in text
    assert "✔ 事实0" not in text  # only last 5 facts rendered


def test_apply_checkpoint_merges(tmp_path):
    memory = _memory(tmp_path)
    result = SimpleNamespace(
        completed_step_ids=[1],
        new_facts=[{"fact": "已搜索蓝牙耳机", "kind": "other"},
                   {"fact": "已搜索蓝牙耳机", "kind": "other"}],  # dup ignored
        current_situation="已完成搜索",
        trigger="interval",
        changes="done:+1 revised:0",
    )
    memory.apply_checkpoint(result, step=5)

    assert memory.subtasks[0].status == "done"
    assert memory.subtasks[0].completed_at_step == 5
    assert sum(1 for f in memory.verified_facts if f.fact == "已搜索蓝牙耳机") == 1
    assert memory.revisions[-1].summary == "已完成搜索"
    assert memory.vlm_call_count == 1
    assert memory.brief_line().startswith("已完成1/3个子任务")


def test_finalize_sets_status(tmp_path):
    memory = _memory(tmp_path)
    memory.finalize(success=True)
    assert memory.status == "completed"
    loaded = SessionMemoryFile.load(memory._path)
    assert loaded.status == "completed"


def test_compress_session_history_is_noop(tmp_path):
    """Regression lock: the leak-prone compression must stay dead."""
    from phone_agent.memory.memory_manager import MemoryManager

    manager = MemoryManager(storage_dir=str(tmp_path), user_id="tester")
    manager.state.overall_progress = "untouched"
    assert manager.compress_session_history() is None
    assert manager.state.overall_progress == "untouched"
