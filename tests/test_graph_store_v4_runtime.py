from phone_agent.memory.graph_store import GraphStore


class _FakeSession:
    def __init__(self, queries):
        self.queries = queries

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def run(self, query, **params):
        self.queries.append(query)
        return []


class _FakeDriver:
    def __init__(self, queries):
        self.queries = queries

    def session(self, database=None):
        return _FakeSession(self.queries)


def _store_with_captured_queries():
    queries = []
    store = object.__new__(GraphStore)
    store.driver = _FakeDriver(queries)
    store.database = "shopping-spatial-v4-pipeline-test"
    return store, queries


def test_v4_runtime_queries_do_not_touch_legacy_task_schema():
    store, queries = _store_with_captured_queries()

    store.find_v4_page_candidates(
        app="taobao",
        page_type="home",
        semantic_signature="taobao home",
    )
    store.get_v4_outgoing_edges("state_home", app="taobao")
    store.get_v4_functionality_context(
        page_type="home",
        app="taobao",
        state_id="state_home",
    )
    store.load_v4_runtime_subgraph(
        app="taobao",
        start_state_ids=["state_home"],
        target_page_types=["cart"],
    )

    joined = "\n".join(queries)
    assert "TaskTarget" not in joined
    assert "STARTS_AT" not in joined
    assert "UIState" in joined
    assert "Action" in joined
    assert "FunctionalityItem" in joined
    assert "IMPLEMENTS_FUNCTION" in joined
