"""Tests for Phase 2 GraphStore enhancements: dynamic trajectory query,
ExplorationSession nodes, and find_similar_pages."""

from __future__ import annotations

from typing import Any

from phone_agent.memory.graph_store import GraphStore


# ── Fake Neo4j stubs ───────────────────────────────────────────


class _FakeRecord:
    """Simulate a Neo4j Record returned by session.run()."""

    def __init__(self, data: dict[str, Any]):
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data.get(key)

    def single(self) -> "_FakeRecord | None":
        return self


class _FakeResult:
    """Simulate a Neo4j Result (iterable of records)."""

    def __init__(self, records: list[dict[str, Any]] | None = None):
        self._records = [_FakeRecord(r) for r in records] if records else []
        self._index = 0

    def single(self) -> _FakeRecord | None:
        return self._records[0] if self._records else None

    def __iter__(self):
        return iter(self._records)

    def consume(self):
        pass


class _FakeSession:
    """Capture queries and return pre-configured results."""

    def __init__(self, results_by_call: list[_FakeResult] | None = None):
        self.queries: list[str] = []
        self.params: list[dict[str, Any]] = []
        self._results = list(results_by_call or [])
        self._call_idx = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def run(self, query: str, **params) -> _FakeResult:
        self.queries.append(query)
        self.params.append(params)
        if self._call_idx < len(self._results):
            result = self._results[self._call_idx]
            self._call_idx += 1
            return result
        return _FakeResult()


class _FakeDriver:
    def __init__(self, session: _FakeSession):
        self._session = session

    def session(self, database=None):
        return self._session


def _store_with_session(session: _FakeSession) -> GraphStore:
    store = object.__new__(GraphStore)
    store.driver = _FakeDriver(session)
    store.database = "test-db"
    store.task_index = None
    store.enable_task_index = False
    return store


# ── Task 2.4: Dynamic trajectory query ────────────────────────


def test_trajectory_empty_when_task_not_found():
    """get_task_trajectory returns {} when task ID doesn't exist."""
    session = _FakeSession([_FakeResult()])  # meta query returns None
    store = _store_with_session(session)
    result = store.get_task_trajectory("nonexistent_task")
    assert result == {}


def test_trajectory_returns_empty_steps_when_no_start_state():
    """If task exists but has no STARTS_AT, return metadata with empty steps."""
    meta = _FakeResult([{
        "description": "Search for phone",
        "app": "taobao",
        "start_state": None,
        "end_state": "state_checkout",
    }])
    session = _FakeSession([meta])
    store = _store_with_session(session)
    result = store.get_task_trajectory("task_1")
    assert result["description"] == "Search for phone"
    assert result["steps"] == []
    assert result["state_ids"] == []
    assert result["end_state"] == "state_checkout"


def test_trajectory_walks_full_chain():
    """Iterative walk follows UIState->Action->UIState chain correctly."""
    meta = _FakeResult([{
        "description": "Buy headphones",
        "app": "taobao",
        "start_state": "state_home",
        "end_state": "state_cart",
    }])
    step1 = _FakeResult([{
        "action_type": "Tap",
        "action_target": "search_bar",
        "reasoning": "open search",
        "next_state": "state_search",
        "page_type": "search_input",
        "summary": "搜索输入页",
    }])
    step2 = _FakeResult([{
        "action_type": "Type",
        "action_target": "headphones",
        "reasoning": "type query",
        "next_state": "state_result",
        "page_type": "search_result",
        "summary": "搜索结果",
    }])
    step3 = _FakeResult([{
        "action_type": "Tap",
        "action_target": "product_card",
        "reasoning": "open product",
        "next_state": "state_detail",
        "page_type": "product_detail",
        "summary": "商品详情",
    }])
    step4 = _FakeResult()  # no more steps
    session = _FakeSession([meta, step1, step2, step3, step4])
    store = _store_with_session(session)
    result = store.get_task_trajectory("task_headphones", max_steps=20)

    assert result["description"] == "Buy headphones"
    assert len(result["steps"]) == 3
    assert result["steps"][0]["action_type"] == "Tap"
    assert result["steps"][0]["page_type"] == "search_input"
    assert result["steps"][1]["action_type"] == "Type"
    assert result["steps"][2]["action_target"] == "product_card"
    assert result["state_ids"] == [
        "state_home", "state_search", "state_result", "state_detail"
    ]


def test_trajectory_respects_max_steps():
    """Walk stops at max_steps even if more transitions exist."""
    meta = _FakeResult([{
        "description": "Long task",
        "app": "taobao",
        "start_state": "state_0",
        "end_state": None,
    }])
    # Generate 10 steps but set max_steps=3
    step_results = []
    for i in range(10):
        step_results.append(_FakeResult([{
            "action_type": "Tap",
            "action_target": f"element_{i}",
            "reasoning": f"step {i}",
            "next_state": f"state_{i+1}",
            "page_type": "unknown",
            "summary": f"page {i+1}",
        }]))
    session = _FakeSession([meta] + step_results)
    store = _store_with_session(session)
    result = store.get_task_trajectory("task_long", max_steps=3)

    assert len(result["steps"]) == 3
    assert len(result["state_ids"]) == 4  # start + 3 next states


def test_trajectory_detects_cycles():
    """Walk stops when revisiting an already-visited state."""
    meta = _FakeResult([{
        "description": "Cycle task",
        "app": "taobao",
        "start_state": "state_a",
        "end_state": None,
    }])
    step1 = _FakeResult([{
        "action_type": "Tap",
        "action_target": "go_b",
        "reasoning": "",
        "next_state": "state_b",
        "page_type": "home",
        "summary": "",
    }])
    step2 = _FakeResult([{
        "action_type": "Back",
        "action_target": "",
        "reasoning": "",
        "next_state": "state_a",  # cycle back to start
        "page_type": "home",
        "summary": "",
    }])
    session = _FakeSession([meta, step1, step2])
    store = _store_with_session(session)
    result = store.get_task_trajectory("task_cycle")

    # step2 action is recorded but state_a is not duplicated in state_ids
    assert len(result["steps"]) == 2
    assert result["state_ids"] == ["state_a", "state_b"]


def test_trajectory_over_15_steps():
    """Verify dynamic query supports >15 steps (old hardcoded limit)."""
    meta = _FakeResult([{
        "description": "20-step task",
        "app": "taobao",
        "start_state": "state_0",
        "end_state": "state_20",
    }])
    step_results = []
    for i in range(20):
        step_results.append(_FakeResult([{
            "action_type": "Tap",
            "action_target": f"el_{i}",
            "reasoning": "",
            "next_state": f"state_{i+1}",
            "page_type": "unknown",
            "summary": "",
        }]))
    session = _FakeSession([meta] + step_results)
    store = _store_with_session(session)
    result = store.get_task_trajectory("task_20", max_steps=25)

    assert len(result["steps"]) == 20
    assert len(result["state_ids"]) == 21  # state_0..state_20
    assert result["state_ids"][-1] == "state_20"


def test_trajectory_includes_page_type_and_summary():
    """Dynamic query returns page_type and summary fields (Task 2.1 enrichment)."""
    meta = _FakeResult([{
        "description": "Check fields",
        "app": "jd",
        "start_state": "state_home",
        "end_state": None,
    }])
    step = _FakeResult([{
        "action_type": "Tap",
        "action_target": "search",
        "reasoning": "open search",
        "next_state": "state_search",
        "page_type": "search_input",
        "summary": "搜索输入页",
    }])
    no_more = _FakeResult()
    session = _FakeSession([meta, step, no_more])
    store = _store_with_session(session)
    result = store.get_task_trajectory("task_fields")

    assert result["steps"][0]["page_type"] == "search_input"
    assert result["steps"][0]["summary"] == "搜索输入页"


# ── Task 2.5: ExplorationSession nodes ────────────────────────


def test_create_exploration_session_runs_merge_query():
    """create_exploration_session issues a MERGE with ExplorationSession label."""
    session = _FakeSession([_FakeResult()])
    store = _store_with_session(session)
    ok = store.create_exploration_session(
        session_id="explore_taobao_123",
        app="taobao",
        session_type="offline_exploration",
        pages_discovered=8,
        transitions_promoted=5,
        task_description="覆盖购物核心骨架",
    )
    assert ok is True
    assert len(session.queries) == 1
    query = session.queries[0]
    assert "ExplorationSession" in query
    assert "TaskTarget" in query
    assert "session_type" in query


def test_create_exploration_session_without_driver():
    """Returns False when Neo4j driver is unavailable."""
    store = object.__new__(GraphStore)
    store.driver = None
    store.task_index = None
    assert store.create_exploration_session("id", "app") is False


def test_find_exploration_sessions_filters_by_app():
    """find_exploration_sessions issues correct query with app filter."""
    session = _FakeSession([_FakeResult([
        {"es": {"target_id": "explore_taobao_1", "app": "taobao", "session_type": "offline_exploration"}},
    ])])
    store = _store_with_session(session)
    results = store.find_exploration_sessions(app="taobao")
    assert len(results) == 1
    assert results[0]["app"] == "taobao"
    assert "ExplorationSession" in session.queries[0]


# ── Task 2.3: find_similar_pages ──────────────────────────────


def test_find_similar_pages_with_summary():
    """find_similar_pages queries by page_type + summary substring."""
    session = _FakeSession([_FakeResult([
        {"s": {"state_id": "state_home_1", "page_type": "home", "summary": "淘宝首页"}},
    ])])
    store = _store_with_session(session)
    results = store.find_similar_pages(page_type="home", summary="首页", app="taobao")
    assert len(results) == 1
    assert results[0]["page_type"] == "home"
    query = session.queries[0]
    assert "CONTAINS" in query


def test_find_similar_pages_without_summary_falls_back():
    """Empty summary delegates to find_page_state_candidates."""
    session = _FakeSession([_FakeResult()])
    store = _store_with_session(session)
    store.find_similar_pages(page_type="cart", summary="")
    # Should use the simpler query (no CONTAINS clause)
    assert len(session.queries) == 1


def test_find_similar_pages_empty_page_type():
    """Returns empty list for empty page_type."""
    store = object.__new__(GraphStore)
    store.driver = True  # truthy but not real
    result = store.find_similar_pages(page_type="", summary="anything")
    assert result == []


def test_find_similar_pages_no_driver():
    """Returns empty list when driver is None."""
    store = object.__new__(GraphStore)
    store.driver = None
    result = store.find_similar_pages(page_type="home")
    assert result == []
